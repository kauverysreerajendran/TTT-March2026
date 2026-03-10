from django.views.generic import *
from modelmasterapp.models import *
from .models import Jig, JigLoadingMaster, JigLoadTrayId, JigDetails, JigLoadingManualDraft
from rest_framework.decorators import *
from django.http import JsonResponse
import logging
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404
from django.utils import timezone
from math import ceil
from rest_framework.permissions import IsAuthenticated
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.contrib.auth.decorators import login_required
import logging
import json
import json



# Jig Loading Pick Table - Main View (display completed batch from Brass Audit Complete table)
@method_decorator(login_required, name='dispatch') 
class JigView(TemplateView):
    template_name = "JigLoading/Jig_Picktable.html"
    
    # No of Trays Calculation
    def get_tray_capacity(stock):
        # Try batch first
        if stock.batch_id and getattr(stock.batch_id, 'tray_capacity', None):
            return stock.batch_id.tray_capacity
        # Try model_master
        if stock.model_stock_no and getattr(stock.model_stock_no, 'tray_capacity', None):
            return stock.model_stock_no.tray_capacity
        # Try tray_type
        if stock.batch_id and hasattr(stock.batch_id, 'tray_type') and stock.batch_id.tray_type:
            try:
                tray_type_obj = TrayType.objects.get(tray_type=stock.batch_id.tray_type)
                return tray_type_obj.tray_capacity
            except TrayType.DoesNotExist:
                pass
        # Try JigLoadingMaster
        jig_master = JigLoadingMaster.objects.filter(model_stock_no=stock.model_stock_no).first()
        if jig_master and getattr(jig_master, 'tray_capacity', None):
            return jig_master.tray_capacity
        return None
    
    
    

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Only show lots NOT completed (do not change row order)
        total_stock_qs = (
            TotalStockModel.objects.filter(brass_audit_accptance=True, Jig_Load_completed=False)
            | TotalStockModel.objects.filter(brass_audit_few_cases_accptance=True, Jig_Load_completed=False)
            | TotalStockModel.objects.filter(brass_audit_rejection=True, Jig_Load_completed=False)
            | TotalStockModel.objects.filter(jig_draft=True, Jig_Load_completed=False)  # Include partial draft lots
        )

        master_data = []
        for stock in total_stock_qs:
            plating_stk_no = (
                getattr(stock.batch_id, 'plating_stk_no', None)
                or getattr(stock.model_stock_no, 'plating_stk_no', None)
            )
            polishing_stk_no = (
                getattr(stock.batch_id, 'polishing_stk_no', None)
                or getattr(stock.model_stock_no, 'polishing_stk_no', None)
            )

            tray_capacity = JigView.get_tray_capacity(stock)
            jig_type = ''
            jig_capacity = ''
            if plating_stk_no:
                jig_master = JigLoadingMaster.objects.filter(model_stock_no__plating_stk_no=plating_stk_no).first()
                if jig_master:
                    jig_type = f"{jig_master.jig_capacity}-Jig"
                    jig_capacity = jig_master.jig_capacity

            lot_qty = stock.total_stock or 0
            no_of_trays = 0
            if tray_capacity and tray_capacity > 0:
                no_of_trays = (lot_qty // tray_capacity) + (1 if lot_qty % tray_capacity else 0)

            # --- Fix: Use jig_draft for correct lot status ---
            if getattr(stock, 'released_flag', False):
                lot_status = 'Yet to Released'
                lot_status_class = 'lot-status-yet-released'
            elif getattr(stock, 'jig_draft', False):
                lot_status = 'Partial Draft'
                lot_status_class = 'lot-status-draft'
            else:
                lot_status = 'Yet to Start'
                lot_status_class = 'lot-status-yet'

            master_data.append({
                'batch_id': stock.batch_id.batch_id if stock.batch_id else '',
                'stock_lot_id': stock.lot_id,
                'plating_stk_no': plating_stk_no,
                'polishing_stk_no': polishing_stk_no,
                'plating_color': stock.plating_color.plating_color if stock.plating_color else '',
                'polish_finish': stock.polish_finish.polish_finish if stock.polish_finish else '',
                'version__version_internal': stock.version.version_internal if stock.version else '',
                'no_of_trays': no_of_trays,
                'display_qty': lot_qty,
                'jig_capacity': jig_capacity if jig_capacity else '',
                'jig_type': jig_type,
                'model_images': [img.master_image.url for img in stock.model_stock_no.images.all()] if stock.model_stock_no else [],
                'brass_audit_last_process_date_time': stock.brass_audit_last_process_date_time,
                'last_process_module': stock.last_process_module,
                'lot_status': lot_status,
                'lot_status_class': lot_status_class,
            })
        context['master_data'] = master_data
        return context 

# Tray Info API View
class TrayInfoView(APIView):
    def get(self, request, *args, **kwargs):
        lot_id = request.GET.get('lot_id')
        batch_id = request.GET.get('batch_id')
        trays = JigLoadTrayId.objects.filter(lot_id=lot_id, batch_id__batch_id=batch_id).values('tray_id', 'tray_quantity')
        tray_list = [{'tray_id': t['tray_id'], 'tray_quantity': t['tray_quantity']} for t in trays]
        return Response({'trays': tray_list})
       
# Tray Validation API View   
class TrayValidateAPIView(APIView):
    def post(self, request, *args, **kwargs):
        batch_id = request.data.get('batch_id')
        lot_id = request.data.get('lot_id')
        tray_ids = request.data.get('tray_ids', [])
        if not batch_id or not lot_id:
            return Response({'validated': False, 'message': 'batch_id and lot_id required'}, status=status.HTTP_400_BAD_REQUEST)

        allocated_trays = JigLoadTrayId.objects.filter(
            lot_id=lot_id,
            batch_id__batch_id=batch_id
        ).values_list('tray_id', flat=True)
        allocated_tray_set = set(str(t) for t in allocated_trays)
        scanned_tray_set = set(str(t) for t in tray_ids)

        if not allocated_tray_set:
            return Response({'validated': False, 'message': 'No allocated trays found for this batch.'}, status=status.HTTP_400_BAD_REQUEST)

        if not scanned_tray_set.issubset(allocated_tray_set):
            invalid = scanned_tray_set - allocated_tray_set
            return Response({
                'validated': False,
                'message': f'Tray IDs not allocated: {", ".join(invalid)}',
                'allocated_trays': list(allocated_tray_set),
                'scanned_trays': list(scanned_tray_set)
            }, status=status.HTTP_400_BAD_REQUEST)

        return Response({'validated': True, 'message': 'Tray validation successful'}, status=status.HTTP_200_OK)


# Class for "Add Model" button data
class JigAddModalDataView(TemplateView):
    """
    Comprehensive modal data preparation for "Add Jig" functionality.
    Handles all data selection, calculation, and validation logic.
    """
    def get(self, request, *args, **kwargs):
        import logging
        logger = logging.getLogger(__name__)
        
        batch_id = request.GET.get('batch_id')
        lot_id = request.GET.get('lot_id')
        jig_qr_id = request.GET.get('jig_qr_id')
        # --- FIX: Only restore from draft if not supplied by user ---
        broken_hooks_param = request.GET.get('broken_hooks', None)
        broken_hooks = int(broken_hooks_param) if broken_hooks_param not in [None, ''] else 0

        try:
            try:
                draft = JigLoadingManualDraft.objects.get(
                    batch_id=batch_id,
                    lot_id=lot_id,
                    user=request.user
                )
                # Only restore from draft if user did not supply a new value
                if (broken_hooks_param in [None, '']) and draft.draft_data.get('broken_buildup_hooks') is not None:
                    broken_hooks = int(draft.draft_data.get('broken_buildup_hooks', 0))
                    logger.info(f"🔄 Restored broken_hooks from draft: {broken_hooks}")
            except JigLoadingManualDraft.DoesNotExist:
                pass
            
            logger.info(f"🔍 JigAddModal: Processing batch_id={batch_id}, lot_id={lot_id}, jig_qr_id={jig_qr_id}, broken_hooks={broken_hooks}")
            
            # Fetch TotalStockModel for batch/lot
            stock = get_object_or_404(TotalStockModel, lot_id=lot_id)
            batch = stock.batch_id
            model_master = batch.model_stock_no if (batch and batch.model_stock_no) else stock.model_stock_no
            
            # Comprehensive plating_stk_no resolution logic
            plating_stk_no = self._resolve_plating_stock_number(batch, model_master)
            
            # Comprehensive data preparation
            modal_data = self._prepare_modal_data(request, batch, model_master, stock, jig_qr_id, lot_id, broken_hooks)

            # Calculate excess message if lot qty exceeds jig capacity
            # Calculate excess message if lot qty exceeds jig capacity (no splitting)
            lot_qty = stock.total_stock
            jig_capacity = modal_data.get('jig_capacity', 0)
            excess = max(0, lot_qty - jig_capacity)
            excess_message = f"{excess} cases are in excess" if excess > 0 else ""

            # Enhanced logging for debugging
            logger.info(f"📊 Modal data prepared: plating_stk_no={plating_stk_no}, jig_type={modal_data.get('jig_type')}, jig_capacity={modal_data.get('jig_capacity')}, broken_hooks={broken_hooks}")
            
            return JsonResponse({
                'success': True,
                'form_title': f"Jig Loading / Plating Stock No: {plating_stk_no or 'N/A'}",
                'jig_id': jig_qr_id,
                'nickel_bath_type': modal_data.get('nickel_bath_type'),
                'tray_type': modal_data.get('tray_type'),
                'broken_buildup_hooks': modal_data.get('broken_buildup_hooks'),
                'empty_hooks': modal_data.get('empty_hooks'),
                'loaded_cases_qty': modal_data.get('loaded_cases_qty'),
                'effective_loaded_cases': modal_data.get('effective_loaded_cases', modal_data.get('loaded_cases_qty')),
                'lot_qty': lot_qty,
                'jig_capacity': modal_data.get('jig_capacity'),
                'jig_type': modal_data.get('jig_type'),
                'loaded_hooks': modal_data.get('loaded_hooks'),
                'add_model_enabled': modal_data.get('add_model_enabled'),
                'can_save': modal_data.get('can_save'),
                'model_images': modal_data.get('model_images'),
                'delink_table': modal_data.get('delink_table'),
                'logs': modal_data.get('logs'),
                'no_of_cycle': modal_data.get('no_of_cycle'),
                'plating_stk_no': plating_stk_no,
                'modal_validation': modal_data.get('modal_validation'),
                'ui_config': modal_data.get('ui_config'),
                'tray_distribution': modal_data.get('tray_distribution'),
                'half_filled_tray_cases': modal_data.get('half_filled_tray_cases', 0),
                'remaining_cases': modal_data.get('remaining_cases', 0),
                'excess_message': excess_message,
            })

        except Exception as e:
            logger.error(f"💥 Exception in JigAddModalDataView: {str(e)}")
            return JsonResponse({
                'success': False,
                'error': f'Failed to load modal data: {str(e)}'
            }, status=500)
    
    def _resolve_plating_stock_number(self, batch, model_master):
        """
        Centralized plating stock number resolution logic.
        Priority: ModelMasterCreation.plating_stk_no -> ModelMaster.plating_stk_no
        """
        plating_stk_no = ''
        if batch and batch.plating_stk_no:
            plating_stk_no = batch.plating_stk_no
        elif batch and batch.model_stock_no and batch.model_stock_no.plating_stk_no:
            plating_stk_no = batch.model_stock_no.plating_stk_no
        return plating_stk_no
    
    def _prepare_modal_data(self, request, batch, model_master, stock, jig_qr_id, lot_id, broken_hooks=0):
        """
        Comprehensive modal data preparation including all calculations and validations.
        """
        import logging
        import re
        logger = logging.getLogger(__name__)
        
        # Calculate max broken hooks based on jig ID prefix
        max_broken_hooks = 5  # default
        if jig_qr_id:
            match = re.match(r'J(\d+)-', jig_qr_id)
            if match:
                jig_capacity_from_id = int(match.group(1))
                max_broken_hooks = 10 if jig_capacity_from_id >= 144 else 5
                # Restrict broken_hooks to max allowed
                if broken_hooks > max_broken_hooks:
                    broken_hooks = max_broken_hooks
        
        # Initialize all modal data variables
        modal_data = {
            'nickel_bath_type': None,
            'tray_type': 'Normal',
            'broken_buildup_hooks': broken_hooks,
            'empty_hooks': 0,
            'loaded_cases_qty': 0,
            'jig_capacity': 0,
            'jig_type': None,
            'loaded_hooks': 0,
            'add_model_enabled': False,
            'model_images': [],
            'delink_table': [],
            'no_of_cycle': 1,
            'modal_validation': {},
            'ui_config': {},
            'can_save': False,
        }
        
        # Set max broken hooks in validation
        modal_data['modal_validation']['max_broken_hooks'] = max_broken_hooks
        
        # Get jig details if exists
        jig_details = None
        if jig_qr_id:
            jig_details = JigDetails.objects.filter(jig_qr_id=jig_qr_id, lot_id=lot_id).first()
        
        # Set initial loaded_cases_qty to 0 (no trays scanned yet)
        modal_data['loaded_cases_qty'] = 0
        
        # Calculate effective_loaded_cases based on broken hooks
        original_lot_qty = stock.total_stock or 0
        if broken_hooks > 0:
            # With broken hooks, effective quantity is original minus broken hooks
            modal_data['effective_loaded_cases'] = max(0, original_lot_qty - broken_hooks)
            logger.info(f"🔧 Broken hooks adjustment: original={original_lot_qty}, broken_hooks={broken_hooks}, effective={modal_data['effective_loaded_cases']}")
        else:
            # No broken hooks - use full lot quantity
            modal_data['effective_loaded_cases'] = original_lot_qty
        
        # Store original lot qty for reference
        modal_data['original_lot_qty'] = original_lot_qty
        
        # Resolve plating stock number
        plating_stk_no = self._resolve_plating_stock_number(batch, model_master)
        
        # Get tray capacity from TrayType master table (STRICT: Always from database)
        tray_capacity = None
        if batch and batch.tray_type:
            tray_type_obj = TrayType.objects.filter(tray_type=batch.tray_type).first()
            if tray_type_obj:
                tray_capacity = tray_type_obj.tray_capacity
            else:
                logger.error(f"❌ Tray type '{batch.tray_type}' not found in TrayType master table")
                tray_capacity = 16  # Fallback for Normal
        else:
            logger.warning(f"⚠️ No tray type found in batch, using default Normal tray capacity")
            tray_capacity = 16  # Default for Normal tray type
        
        print(f"💾 MASTER TABLE LOOKUP:")
        print(f"  Batch Tray Type: {batch.tray_type if batch else 'None'}")
        print(f"  Resolved Tray Capacity: {tray_capacity}")
        
        # Get jig capacity from JigLoadingMaster table (STRICT: Always from database)

        jig_master = JigLoadingMaster.objects.filter(model_stock_no=model_master).first()
        
        # Jig Capacity - Fetch from master as per the model number
        jig_master = JigLoadingMaster.objects.filter(model_stock_no=model_master).first()
        
        # Fallback: Try by model_no string if not found
        if not jig_master and hasattr(model_master, "model_no"):
            jig_master = JigLoadingMaster.objects.filter(model_stock_no__model_no=model_master.model_no).first()
        
        if jig_master:
            modal_data['jig_type'] = f"{jig_master.jig_capacity:03d}" if jig_master.jig_capacity else None
            modal_data['jig_capacity'] = jig_master.jig_capacity
            print(f"  Jig Master Found: {jig_master.jig_type} - Capacity: {jig_master.jig_capacity}")
        else:
            modal_data['jig_type'] = None
            modal_data['jig_capacity'] = 0
            logger.error(f"❌ No jig master found for model: {getattr(model_master, 'model_no', str(model_master))}. Please configure JigLoadingMaster.")
            print(f"  Jig Master Not Found: No capacity assigned.")
        # End of Jig Capacity - Fetch from master as per the model number

        
        print(f"  Final Jig Capacity: {modal_data['jig_capacity']}")
        print(f"  Final Jig Type: {modal_data['jig_type']}")
        
        # Set tray type from batch
        modal_data['tray_type'] = batch.tray_type if batch else 'Normal'

        # Re-validate broken hooks based on actual jig capacity (fallback for when jig_qr_id is empty)
        if not jig_qr_id and modal_data['jig_capacity'] > 0:
            # Use actual jig capacity to determine max broken hooks
            max_broken_hooks = 10 if modal_data['jig_capacity'] >= 144 else 5
            # Restrict broken_hooks to max allowed
            if broken_hooks > max_broken_hooks:
                broken_hooks = max_broken_hooks
                modal_data['broken_buildup_hooks'] = broken_hooks
            # Update validation data
            modal_data['modal_validation']['max_broken_hooks'] = max_broken_hooks

        # Nickel Bath Type and jig calculations
        if jig_details:
            modal_data['nickel_bath_type'] = jig_details.ep_bath_type
            modal_data['broken_buildup_hooks'] = broken_hooks
            modal_data['loaded_cases_qty'] = jig_details.total_cases_loaded
            modal_data['loaded_hooks'] = jig_details.total_cases_loaded

            # --- FIX: Only allow empty_hooks > 0 if lot qty < jig capacity, else always 0 ---
            if modal_data['loaded_cases_qty'] < modal_data['jig_capacity']:
                modal_data['empty_hooks'] = modal_data['jig_capacity'] - modal_data['loaded_cases_qty']
            else:
                modal_data['empty_hooks'] = 0
            # --- END FIX ---
            modal_data['no_of_cycle'] = jig_details.no_of_cycle
        else:
            # Auto-fill for new entries with comprehensive defaults
            modal_data['nickel_bath_type'] = "Bright"  # Default
            modal_data['loaded_cases_qty'] = stock.total_stock
            modal_data['loaded_hooks'] = stock.total_stock
            # --- FIX: Only allow empty_hooks > 0 if lot qty < jig capacity, else always 0 ---
            if modal_data['loaded_cases_qty'] < modal_data['jig_capacity']:
                modal_data['empty_hooks'] = modal_data['jig_capacity'] - modal_data['loaded_cases_qty']
            else:
                modal_data['empty_hooks'] = 0
            # --- END FIX ---

        # Apply broken hooks adjustment and half-filled tray logic
        if broken_hooks > 0:
            # Effective loaded cases already calculated above (original_qty - broken_hooks)
            modal_data['loaded_hooks'] = modal_data['effective_loaded_cases']
            modal_data['empty_hooks'] = 0  # No empty hooks when broken hooks present
            
            # For broken hooks scenario: broken hooks quantity goes to half-filled section
            # Delink table gets effective quantity, half-filled gets broken hooks
            modal_data['half_filled_tray_cases'] = broken_hooks
            modal_data['remaining_cases'] = 0  # All cases are distributed
            modal_data['no_of_cycle'] = 1
            
            logger.info(f"🔧 BROKEN HOOKS SETUP: effective_cases={modal_data['effective_loaded_cases']}, half_filled_cases={broken_hooks}")
        else:
            # No broken hooks - effective loaded cases is the lot qty
            modal_data['remaining_cases'] = 0
            modal_data['half_filled_tray_cases'] = 0
            modal_data['half_filled_tray_cases'] = 0

        # Delink Table preparation (existing tray data)
        modal_data['delink_table'] = self._prepare_existing_delink_table(lot_id, batch, modal_data['effective_loaded_cases'], tray_capacity, broken_hooks)

        # If lot qty >= jig capacity, force empty_hooks to 0 regardless of broken hooks
        if modal_data['loaded_cases_qty'] >= modal_data['jig_capacity']:
            modal_data['empty_hooks'] = 0

        # Model Images preparation with validation
        modal_data['model_images'] = self._prepare_model_images(model_master)

        # Add Model button logic with validation
        modal_data['add_model_enabled'] = modal_data['empty_hooks'] > 0
        
        
        # Save button logic: Enable only if empty_hooks == 0
        modal_data['can_save'] = (modal_data['empty_hooks'] == 0)

        # Modal validation rules
        modal_data['modal_validation'] = self._prepare_modal_validation(modal_data)

        # Tray Distribution and Half-Filled Tray Calculation
        # Use effective_loaded_cases which is already reduced by broken hooks
        modal_data['tray_distribution'] = self._calculate_tray_distribution(
            modal_data['effective_loaded_cases'], 
            modal_data['jig_capacity'], 
            modal_data['broken_buildup_hooks'],
            batch
        )

        # Adjust loaded_cases_qty to include broken hooks cases
        modal_data['loaded_cases_qty'] = modal_data['tray_distribution']['current_lot']['total_cases'] + modal_data['broken_buildup_hooks']

        # UI Configuration for frontend rendering
        modal_data['ui_config'] = self._prepare_ui_configuration(modal_data)

        # Comprehensive calculation logs
        modal_data['logs'] = {
            'batch_id': batch.batch_id if batch else None,
            'lot_id': lot_id,
            'jig_qr_id': jig_qr_id,
            'jig_type': modal_data['jig_type'],
            'jig_capacity': modal_data['jig_capacity'],
            'loaded_cases_qty': modal_data['loaded_cases_qty'],
            'loaded_hooks': modal_data['loaded_hooks'],
            'empty_hooks': modal_data['empty_hooks'],
            'broken_buildup_hooks': modal_data['broken_buildup_hooks'],
            'nickel_bath_type': modal_data['nickel_bath_type'],
            'delink_table': modal_data['delink_table'],
            'model_images': modal_data['model_images'],
            'add_model_enabled': modal_data['add_model_enabled'],
            'can_save': modal_data['can_save'],
            'user': request.user.username,
            'calculation_timestamp': timezone.now().isoformat(),
            'tray_type': modal_data['tray_type'],
            'tray_distribution': modal_data['tray_distribution']
        }

        logger.info(f"🎯 Modal data prepared with {len(modal_data['model_images'])} images, {len(modal_data['delink_table'])} existing trays")
        
        # --- Overflow Handling: Lot Qty > Jig Capacity ---
        if modal_data['original_lot_qty'] > modal_data['jig_capacity'] and modal_data['broken_buildup_hooks'] == 0:
            # Only apply this logic if there are NO broken hooks
            leftover_cases = modal_data['original_lot_qty'] - modal_data['jig_capacity']
            modal_data['half_filled_tray_cases'] = leftover_cases
            modal_data['remaining_cases'] = leftover_cases

            # For delink: Only full trays (jig_capacity // tray_capacity)
            tray_capacity = modal_data['tray_distribution']['current_lot']['tray_capacity']
            full_trays = modal_data['jig_capacity'] // tray_capacity

            # Prepare delink table for only full trays
            modal_data['delink_table'] = [
                {
                    'tray_id': '',
                    'tray_quantity': tray_capacity,
                    'model_bg': self._get_model_bg(i + 1),
                    'original_quantity': tray_capacity,
                    'excluded_quantity': 0,
                }
                for i in range(full_trays)
            ]

            # Prepare half-filled tray for the leftover cases
            half_filled_distribution = self._distribute_half_filled_trays(leftover_cases, tray_capacity)
            modal_data['tray_distribution']['half_filled_lot'] = {
                'total_cases': leftover_cases,
                'distribution': half_filled_distribution,
                'total_trays': half_filled_distribution['total_trays'] if half_filled_distribution else 0
            }
            modal_data['open_with_half_filled'] = True

            # Set loaded_cases_qty to 0/jig_capacity for display
            modal_data['loaded_cases_qty'] = f"0/{modal_data['jig_capacity']}"
            modal_data['excess_message'] = f"{modal_data['original_lot_qty'] - modal_data['jig_capacity']} cases are in excess"
        else:
            modal_data['open_with_half_filled'] = False
            # For broken hooks or normal case, keep existing logic for delink_table and loaded_cases_qty
            modal_data['loaded_cases_qty'] = f"0/{modal_data['original_lot_qty']}"
            modal_data['excess_message'] = ""

        return modal_data

    def _prepare_model_images(self, model_master):
        """
        Prepare model images data with proper structure for frontend consumption.
        """
        model_image_data = []
        if model_master and model_master.images.exists():
            for image in model_master.images.all():
                model_image_data.append({
                    'url': image.master_image.url,
                    'model_no': model_master.model_no,
                    'image_id': image.id,
                    'alt_text': f"Model {model_master.model_no} Image"
                })
        return model_image_data
    
    def _prepare_existing_delink_table(self, lot_id, batch, effective_loaded_cases, tray_capacity, broken_hooks):
        """
        Prepare delink table data for scanning.
        Logic: 
        - If no broken hooks: distribute all effective cases into trays
        - If broken hooks > 0: distribute effective quantity across trays (reducing last tray)
        - User example: original=98, broken=5, effective=93, trays=(9,12,12,12,12,12,12,12)
        """
        logger = logging.getLogger(__name__)
        delink_table = []
    
        try:
            if broken_hooks > 0:
                # Use existing trays with broken hooks distribution
                existing_trays = JigLoadTrayId.objects.filter(lot_id=lot_id, batch_id=batch).order_by('id')
                effective_tray_data = self._calculate_broken_hooks_tray_distribution(lot_id, effective_loaded_cases, broken_hooks, batch)
                lot_qty = effective_loaded_cases + broken_hooks
                total_trays_needed = ceil(lot_qty / tray_capacity) if tray_capacity else 0
                if len(effective_tray_data) >= total_trays_needed:
                    effective_tray_data = effective_tray_data[:-1]  # Exclude last tray for broken hooks
                for tray_data in effective_tray_data:
                    delink_table.append({
                        'tray_id': tray_data['tray_id'],
                        'tray_quantity': tray_data['effective_qty'],
                        'model_bg': tray_data['model_bg'],
                        'original_quantity': tray_data['original_qty'],
                        'excluded_quantity': tray_data['excluded_qty']
                    })
            else:
                # No broken hooks - use existing trays with their quantities
                existing_trays = JigLoadTrayId.objects.filter(lot_id=lot_id, batch_id=batch).order_by('id')
                for idx, tray in enumerate(existing_trays):
                    model_bg = self._get_model_bg(idx + 1)
                    tray_qty = tray.tray_quantity or tray_capacity
                    delink_table.append({
                        'tray_id': '',
                        'tray_quantity': tray_qty,
                        'model_bg': model_bg,
                        'original_quantity': tray_qty,
                        'excluded_quantity': 0,
                    })
            
            logger.info(f"📊 DELINK TABLE: {len(delink_table)} trays for scanning (effective_cases={effective_loaded_cases}, broken_hooks={broken_hooks})")
            return delink_table
        
        except Exception as e:
            logger.error(f"❌ Error in _prepare_existing_delink_table: {str(e)}")
            logger.error(f"Parameters: lot_id={lot_id}, effective_loaded_cases={effective_loaded_cases}, tray_capacity={tray_capacity}, broken_hooks={broken_hooks}")
            return []
    
    
    def _prepare_modal_validation(self, modal_data):
        """
        Prepare validation rules and constraints for modal data.
        """
        # Fix hooks balance calculation for half-filled tray scenarios
        if modal_data['broken_buildup_hooks'] > 0:
            # When broken hooks present: loaded_hooks should equal effective capacity
            expected_loaded = modal_data['jig_capacity'] - modal_data['broken_buildup_hooks']
            actual_loaded = modal_data['loaded_hooks'] + modal_data['empty_hooks']
            hooks_balance_valid = actual_loaded == expected_loaded
        else:
            # Standard calculation when no broken hooks
            hooks_balance_valid = modal_data['loaded_hooks'] + modal_data['empty_hooks'] == modal_data['jig_capacity']
        
        validation = {
            'jig_capacity_valid': modal_data['jig_capacity'] > 0,
            'loaded_cases_valid': modal_data['loaded_cases_qty'] > 0,
            'hooks_balance_valid': hooks_balance_valid,
            'broken_hooks_valid': modal_data['broken_buildup_hooks'] >= 0,
            'nickel_bath_valid': modal_data['nickel_bath_type'] in ['Bright', 'Satin', 'Matt'],
            'has_model_images': len(modal_data['model_images']) > 0,
            'can_add_model': modal_data['add_model_enabled'],
            'empty_hooks_zero': (modal_data['empty_hooks'] == 0),
            'has_half_filled_cases': modal_data.get('half_filled_tray_cases', 0) > 0,
        }
        
        validation['overall_valid'] = all([
            validation['jig_capacity_valid'],
            validation['loaded_cases_valid'],
            validation['hooks_balance_valid'],
            validation['broken_hooks_valid'],
            validation['nickel_bath_valid'],
            # validation['empty_hooks_zero'],
        ])
        
        if not validation['empty_hooks_zero']:
            validation['empty_hooks_error'] = (
                "Loaded Cases Qty must equal Jig Capacity. Use 'Add Model' to fill empty hooks with relevant tray allocation."
        )
        
        return validation
    
    def _calculate_tray_distribution(self, loaded_cases_qty, jig_capacity, broken_hooks, batch):
        """
        Calculate tray distribution for cases considering broken hooks.
        Logic: Use loaded_cases_qty (which is effective quantity after broken hooks reduction)
        User example: original=98, broken_hooks=5, loaded_cases_qty=93
        Should distribute 93 cases across trays: (9,12,12,12,12,12,12,12)
        """
        # Get tray capacity from batch tray type (STRICT: Always from database)
        tray_capacity = None
        if batch and batch.tray_type:
            tray_type_obj = TrayType.objects.filter(tray_type=batch.tray_type).first()
            if tray_type_obj:
                tray_capacity = tray_type_obj.tray_capacity

        # STRICT: If tray_capacity is not found, raise error (do not fallback to hardcoded value)
        if not tray_capacity:
            raise ValueError(f"Tray capacity not configured for tray type '{getattr(batch, 'tray_type', None)}'. Please configure in admin.")

        print(f"🧮 TRAY DISTRIBUTION CALCULATION:")
        print(f"Original Jig Capacity: {jig_capacity}")
        print(f"Broken Hooks: {broken_hooks}")
        print(f"Effective Cases to Distribute: {loaded_cases_qty}")
        print(f"Tray Capacity: {tray_capacity}")
        
        # Use the _distribute_cases_to_trays method for proper distribution
        delink_distribution = self._distribute_cases_to_trays(loaded_cases_qty, tray_capacity)
        
        print(f"Delink Distribution: {len(delink_distribution.get('trays', []))} trays")
        for idx, tray in enumerate(delink_distribution.get('trays', [])):
            print(f"  Tray {tray['tray_number']}: {tray['cases']} cases")
        
        # Calculate half-filled tray for broken hooks (if any)
        half_filled_distribution = None
        if broken_hooks > 0:
            half_filled_distribution = {
                'total_cases': broken_hooks,
                'full_trays_count': 0,
                'partial_tray_cases': broken_hooks,
                'total_trays': 1,
                'trays': [{
                    'tray_number': 1,
                    'cases': broken_hooks,
                    'is_full': False,
                    'is_top_tray': True,
                    'scan_required': True
                }]
            }
            print(f"Half-Filled Tray: {broken_hooks} cases (1 tray)")
        
        return {
            'current_lot': {
                'total_cases': loaded_cases_qty,
                'effective_capacity': jig_capacity,
                'broken_hooks': broken_hooks,
                'tray_capacity': tray_capacity,
                'distribution': delink_distribution,
                'total_trays': delink_distribution.get('total_trays', 0) if delink_distribution else 0
            },
            'half_filled_lot': {
                'total_cases': broken_hooks,  # Broken hooks cases go to half-filled section
                'distribution': half_filled_distribution,
                'total_trays': 1 if broken_hooks > 0 else 0  # 1 tray if broken hooks present
            },
            'accountability_info': self._generate_accountability_info(
                jig_capacity, loaded_cases_qty, 0, broken_hooks
            )
        }
    
    
    
    def _distribute_cases_to_trays(self, total_cases, tray_capacity):
        """
        Distribute cases into trays based on tray capacity.
        Returns distribution with full trays and partial tray details.
        For leftover lots, put partial tray first for scanning.
        """
        if total_cases <= 0 or not tray_capacity or tray_capacity <= 0:
            return None
            
        full_trays = total_cases // tray_capacity
        partial_cases = total_cases % tray_capacity
        
        trays = []
        
        # For leftover lots (when there are partial cases), put partial tray first
        if partial_cases > 0:
            trays.append({
                'tray_number': 1,
                'cases': partial_cases,
                'is_full': False,
                'is_top_tray': True,  # Mark as top tray for scanning
                'scan_required': True
            })
            # Then add full trays
            for i in range(full_trays):
                trays.append({
                    'tray_number': i + 2,  # Start from 2 since partial is 1
                    'cases': tray_capacity,
                    'is_full': True,
                    'scan_required': False
                })
        else:
            # For full trays only, add them in order
            for i in range(full_trays):
                trays.append({
                    'tray_number': i + 1,
                    'cases': tray_capacity,
                    'is_full': True,
                    'scan_required': False
                })
        
        return {
            'total_cases': total_cases,
            'full_trays_count': full_trays,
            'partial_tray_cases': partial_cases if partial_cases > 0 else 0,
            'total_trays': len(trays),
            'trays': trays
        }

    def _distribute_half_filled_trays(self, half_filled_cases, tray_capacity):
        """
        Distribute half-filled cases into trays with scan requirements.
        Partial trays require scanning, full trays can auto-assign existing tray IDs.
        """
        if half_filled_cases <= 0:
            return None
            
        partial_cases = half_filled_cases
        
        trays = []
        tray_number = 1
        
        # Add partial tray (requires scanning)
        if partial_cases > 0:
            trays.append({
                'tray_number': tray_number,
                'cases': partial_cases,
                'is_full': False,
                'scan_required': True,
                'tray_type': 'partial',
                'placeholder': f'Scan Tray ID ({partial_cases} pcs)'
            })
            tray_number += 1
        
        return {
            'total_cases': half_filled_cases,
            'full_trays_count': 0,
            'partial_tray_cases': partial_cases if partial_cases > 0 else 0,
            'total_trays': len(trays),
            'trays': trays,
            'scan_required_trays': len([t for t in trays if t.get('scan_required', False)])
        }

    def _generate_accountability_info(self, original_lot_qty, effective_loaded, leftover_cases, broken_hooks):
        """
        Generate accountability information text for user understanding.
        """
        info_lines = []
        
        if broken_hooks > 0:
            info_lines.append(f"Original Lot Qty: {original_lot_qty} cases")
            info_lines.append(f"Broken Hooks: {broken_hooks} (positions unavailable)")
            info_lines.append(f"Current Cycle: {effective_loaded} cases loaded")
            
            if leftover_cases > 0:
                info_lines.append(f"Next Cycle: {leftover_cases} cases remaining")
                info_lines.append("All cases accounted for - no quantities missing")
            else:
                info_lines.append("All cases loaded in current cycle")
        else:
            info_lines.append(f"Total cases: {original_lot_qty} - All loaded in current cycle")
            info_lines.append("No broken hooks - full capacity utilized")
        
        return " • ".join(info_lines)

    def _prepare_ui_configuration(self, modal_data):
        """
        Prepare UI configuration for optimal frontend rendering.
        """
        return {
            'show_model_images': len(modal_data['model_images']) > 0,
            'enable_add_model': modal_data['add_model_enabled'],
            'show_cycle_info': modal_data['no_of_cycle'] > 1,
            'highlight_empty_hooks': modal_data['empty_hooks'] > 0,
            'show_broken_hooks_warning': modal_data['broken_buildup_hooks'] > 0,
            'readonly_fields': ['empty_hooks', 'loaded_cases_qty', 'jig_capacity'],
            'required_fields': ['jig_id', 'nickel_bath_type'],
            'validation_enabled': True
        }

    def _calculate_broken_hooks_tray_distribution(self, lot_id, effective_qty, broken_hooks, batch):
        """
        Calculate how to distribute effective quantity across existing trays when broken hooks are present.
        This updates tray records with broken hooks calculation fields.
        
        User's calculation example:
        - Original lot: 98 cases across 9 trays (JB-A00020=2, JB-A00021=12, ..., JB-A00028=12)
        - Broken hooks: 39 cases  
        - Effective qty: 59 cases
        - Expected distribution: JB-A00020=11, JB-A00021=12, JB-A00022=12, JB-A00023=12, JB-A00024=12
        
        Logic: First tray gets remainder, subsequent trays get full capacity up to effective qty
        """
        logger = logging.getLogger(__name__)
        existing_trays = JigLoadTrayId.objects.filter(lot_id=lot_id, batch_id=batch).order_by('tray_id')
        
        if not existing_trays.exists():
            logger.warning(f"⚠️ No existing trays found for lot {lot_id} and batch {batch.batch_id if batch else 'None'}")
            return []
        
        logger.info(f"🔧 BROKEN HOOKS CALCULATION: lot={lot_id}, effective_qty={effective_qty}, broken_hooks={broken_hooks}")
        
        # Get tray capacity to determine proper distribution
        tray_capacity = 12  # Default fallback
        if existing_trays.exists():
            first_tray = existing_trays.first()
            if first_tray.tray_capacity:
                tray_capacity = first_tray.tray_capacity
            elif first_tray.batch_id and first_tray.batch_id.tray_capacity:
                tray_capacity = first_tray.batch_id.tray_capacity
        
        # Calculate how many full trays we need for effective qty
        full_trays_needed = effective_qty // tray_capacity
        remainder_qty = effective_qty % tray_capacity
        
        logger.info(f"📊 Distribution calculation: effective_qty={effective_qty}, tray_capacity={tray_capacity}, full_trays_needed={full_trays_needed}, remainder_qty={remainder_qty}")
        
        # Reset all trays first
        for tray in existing_trays:
            tray.broken_hooks_effective_tray = False
            tray.broken_hooks_excluded_qty = 0
            tray.effective_tray_qty = tray.tray_quantity  # Default to original quantity
            tray.save()
        
        # Distribute effective quantity: remainder tray first (if any), then full trays
        remaining_effective_qty = effective_qty
        effective_trays = []
        tray_index = 0
        
        # Handle remainder first (partial tray) - user's example: JB-A00020 gets 11 cases
        if remainder_qty > 0 and tray_index < existing_trays.count():
            tray = existing_trays[tray_index]
            tray_effective_qty = remainder_qty
            tray_excluded_qty = tray.tray_quantity - tray_effective_qty
            
            # Update tray with broken hooks fields
            tray.broken_hooks_effective_tray = True
            tray.broken_hooks_excluded_qty = tray_excluded_qty
            tray.effective_tray_qty = tray_effective_qty
            tray.save()
            
            effective_trays.append({
                'tray_id': tray.tray_id,
                'effective_qty': tray_effective_qty,
                'original_qty': tray.tray_quantity,
                'excluded_qty': tray_excluded_qty,
                'model_bg': self._get_model_bg(tray_index + 1)
            })
            
            remaining_effective_qty -= tray_effective_qty
            tray_index += 1
            logger.info(f"  Remainder tray {tray.tray_id}: effective={tray_effective_qty}, excluded={tray_excluded_qty}")
        
        # Handle full trays - user's example: JB-A00021, JB-A00022, JB-A00023, JB-A00024 each get 12 cases
        for i in range(full_trays_needed):
            if tray_index >= existing_trays.count():
                break
                
            tray = existing_trays[tray_index]
            tray_effective_qty = tray_capacity
            tray_excluded_qty = tray.tray_quantity - tray_effective_qty
            
            # Update tray with broken hooks fields
            tray.broken_hooks_effective_tray = True
            tray.broken_hooks_excluded_qty = tray_excluded_qty
            tray.effective_tray_qty = tray_effective_qty
            tray.save()
            
            effective_trays.append({
                'tray_id': tray.tray_id,
                'effective_qty': tray_effective_qty,
                'original_qty': tray.tray_quantity,
                'excluded_qty': tray_excluded_qty,
                'model_bg': self._get_model_bg(tray_index + 1)
            })
            
            remaining_effective_qty -= tray_effective_qty
            tray_index += 1
            logger.info(f"  Full tray {tray.tray_id}: effective={tray_effective_qty}, excluded={tray_excluded_qty}")
        
        # Mark remaining trays as excluded (not part of effective distribution)
        for i in range(tray_index, existing_trays.count()):
            tray = existing_trays[i]
            tray.broken_hooks_effective_tray = False
            tray.broken_hooks_excluded_qty = tray.tray_quantity
            tray.effective_tray_qty = 0
            tray.save()
            logger.info(f"  Excluded tray {tray.tray_id}: all {tray.tray_quantity} cases excluded")
        
        logger.info(f"✅ Broken hooks distribution complete: {len(effective_trays)} effective trays, remaining_qty={remaining_effective_qty}")
        return effective_trays
    
    
    
    def _get_model_bg(self, idx):
        return f"model-bg-{(idx - 1) % 5 + 1}"

# Tray ID Validation - Delink Table View
@api_view(['GET'])
def validate_tray_id(request):
    tray_id = request.GET.get('tray_id')
    batch_id = request.GET.get('batch_id')
    lot_id = request.GET.get('lot_id')  # <-- Add this line to get lot_id from request
    if not tray_id or not batch_id or not lot_id:
        return Response({'valid': False, 'message': 'Tray ID, Batch ID, and Lot ID required'}, status=400)
    # Only accept tray_id that belongs to this lot and batch
    tray = JigLoadTrayId.objects.filter(
        tray_id=tray_id,
        batch_id__batch_id=batch_id,
        lot_id=lot_id
    ).first()
    if tray:
        tray_quantity = tray.tray_quantity or tray.tray_capacity or 0
        return Response({'valid': True, 'tray_quantity': tray_quantity})
    else:
        # Do NOT allow new trays for delink table (only for half-filled section, handled elsewhere)
        return Response({'valid': False, 'message': 'Invalid Tray ID.'})

# Add Jig Btn - Delink Table View


class DelinkTableAPIView(APIView):
    """
    Returns tray rows for Delink Table based on tray type, lot qty, and jig capacity.
    Calculates number of trays needed for scanning based on loaded cases qty and tray capacity.
    """
    def get(self, request, *args, **kwargs):
        import logging
        logger = logging.getLogger(__name__)
        
        batch_id = request.GET.get('batch_id')
        lot_id = request.GET.get('lot_id')
        jig_qr_id = request.GET.get('jig_qr_id', None)
        broken_hooks = int(request.GET.get('broken_hooks', 0))

        if not batch_id or not lot_id:
            logger.info("❌ Missing parameters: batch_id or lot_id")
            return Response({'error': 'batch_id and lot_id required'}, status=status.HTTP_400_BAD_REQUEST)

        logger.info(f"🔍 Processing delink table for batch_id: {batch_id}, lot_id: {lot_id}, broken_hooks: {broken_hooks}")

        # Get TotalStockModel for loaded cases qty
        try:
            stock = TotalStockModel.objects.get(lot_id=lot_id)
            loaded_cases_qty = stock.total_stock or 0
            logger.info(f"📊 Loaded cases qty from TotalStockModel: {loaded_cases_qty}")
        except TotalStockModel.DoesNotExist:
            logger.error(f"❌ TotalStockModel not found for lot_id: {lot_id}")
            return Response({'error': 'Stock record not found'}, status=status.HTTP_404_NOT_FOUND)

        # Get batch/model info for tray type and jig capacity
        try:
            batch = ModelMasterCreation.objects.get(batch_id=batch_id)
            model_master = batch.model_stock_no
            logger.info(f"📦 Found batch: {batch_id}, model: {model_master}")
        except ModelMasterCreation.DoesNotExist:
            logger.error(f"❌ ModelMasterCreation not found for batch_id: {batch_id}")
            return Response({'error': 'Batch not found'}, status=status.HTTP_404_NOT_FOUND)

        # Get tray type and capacity
        tray_type_name = batch.tray_type or "Normal"  # Default to Normal if not set
        try:
            tray_type_obj = TrayType.objects.get(tray_type=tray_type_name)
            tray_capacity = tray_type_obj.tray_capacity
            logger.info(f"🗂️ Tray type: {tray_type_name}, capacity: {tray_capacity}")
        except TrayType.DoesNotExist:
            logger.warning(f"⚠️ TrayType '{tray_type_name}' not found, trying fallback options")
            fallback_types = ["Normal", "Jumbo"]
            tray_capacity = None
            for fallback_type in fallback_types:
                try:
                    fallback_tray_obj = TrayType.objects.get(tray_type=fallback_type)
                    tray_capacity = fallback_tray_obj.tray_capacity
                    logger.warning(f"⚠️ Using fallback TrayType '{fallback_type}' with capacity: {tray_capacity}")
                    break
                except TrayType.DoesNotExist:
                    continue
            if tray_capacity is None:
                logger.error(f"❌ No TrayType configurations found in database")
                return Response({'error': 'Tray type configuration missing. Please configure tray types in admin.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # Get jig capacity from JigLoadingMaster
        jig_capacity = 0
        if model_master:
            try:
                jig_master = JigLoadingMaster.objects.get(model_stock_no=model_master)
                jig_capacity = jig_master.jig_capacity
                logger.info(f"🔧 Jig capacity from JigLoadingMaster: {jig_capacity}")
            except JigLoadingMaster.DoesNotExist:
                logger.warning(f"⚠️ JigLoadingMaster not found for model: {model_master}")
                jig_capacity = loaded_cases_qty  # Use loaded cases qty as fallback

        # Calculate effective capacity considering broken hooks
        effective_capacity = max(0, jig_capacity - broken_hooks) if jig_capacity > 0 else loaded_cases_qty
        actual_qty = min(loaded_cases_qty, effective_capacity)
        logger.info(f"🧮 Calculation: loaded_cases_qty={loaded_cases_qty}, jig_capacity={jig_capacity}, broken_hooks={broken_hooks}, effective_capacity={effective_capacity}, actual_qty={actual_qty}")

        # Check for existing tray IDs for this lot
        existing_trays = JigLoadTrayId.objects.filter(
            lot_id=lot_id, 
            batch_id=batch
        ).order_by('date').only('tray_id', 'tray_quantity')  # Optimization

        # --- NEW LOGIC: Conditional tray distribution based on broken_hooks ---
        half_filled_tray_data = None
        rows = []
        if tray_capacity > 0 and actual_qty > 0:
            if broken_hooks == 0:
                # When broken_hooks == 0, show all trays (full and partial) in delink table
                num_full_trays = actual_qty // tray_capacity
                remainder_qty = actual_qty % tray_capacity
                total_trays = num_full_trays + (1 if remainder_qty > 0 else 0)
                
                for i in range(total_trays):
                    s_no = i + 1
                    if i < num_full_trays:
                        tray_qty = tray_capacity
                    else:
                        tray_qty = remainder_qty
                    
                    # All trays are for scanning - empty inputs
                    tray_id = ""
                    tray_quantity = tray_qty
                    placeholder = "Scan Tray Id"
                    readonly = False
                    
                    rows.append({
                        's_no': s_no,
                        'tray_id': tray_id,
                        'tray_quantity': tray_quantity,
                        'placeholder': placeholder,
                        'readonly': readonly
                    })
                
                num_trays = total_trays
            else:
                # When broken_hooks > 0, show all trays (full and partial) in delink table
                num_full_trays = actual_qty // tray_capacity
                remainder_qty = actual_qty % tray_capacity
                total_trays = num_full_trays + (1 if remainder_qty > 0 else 0)
                
                for i in range(total_trays):
                    s_no = i + 1
                    if i < num_full_trays:
                        tray_qty = tray_capacity
                    else:
                        tray_qty = remainder_qty
                    
                    # All trays are for scanning - empty inputs
                    tray_id = ""
                    tray_quantity = tray_qty
                    placeholder = "Scan Tray Id"
                    readonly = False
                    
                    rows.append({
                        's_no': s_no,
                        'tray_id': tray_id,
                        'tray_quantity': tray_quantity,
                        'placeholder': placeholder,
                        'readonly': readonly
                    })
                
                # Half-filled tray for broken hooks
                if broken_hooks > 0:
                    half_filled_cases = broken_hooks
                    half_filled_num_trays = (half_filled_cases + tray_capacity - 1) // tray_capacity  # ceil division
                    half_filled_tray_data = {
                        'tray_count': half_filled_num_trays,
                        'message': f'Scan half filled tray ID with {half_filled_cases} pieces'
                    }
                
                num_trays = total_trays
        else:
            num_trays = 0

        logger.info(f"✅ Generated {len(rows)} delink table rows")
        logger.info(f"📊 Final calculation summary - tray_type: {tray_type_name}, tray_capacity: {tray_capacity}, actual_qty: {actual_qty}, num_full_trays: {num_full_trays}, half_filled_tray: {half_filled_tray_data}")

        return Response({
            'tray_rows': rows,
            'tray_type': tray_type_name,
            'tray_capacity': tray_capacity,
            'actual_qty': actual_qty,
            'loaded_cases_qty': loaded_cases_qty,
            'jig_capacity': jig_capacity,
            'effective_capacity': effective_capacity,
            'broken_hooks': broken_hooks,
            'num_trays': num_trays,
            'half_filled_tray_data': half_filled_tray_data,
            'calculation_details': {
                'formula': f'{actual_qty} pieces = {num_full_trays} full trays + {remainder_qty if remainder_qty > 0 else 0} remainder',
                'constraint': f'effective_capacity = jig_capacity({jig_capacity}) - broken_hooks({broken_hooks}) = {effective_capacity}',
                'tray_distribution': [row['tray_quantity'] for row in rows],
                'half_filled_info': half_filled_tray_data
            }
        }, status=status.HTTP_200_OK)



# Manual Draft - Save/Update View
class JigLoadingManualDraftAPIView(APIView):
    def post(self, request, *args, **kwargs):
        import logging
        logger = logging.getLogger(__name__)
        
        batch_id = request.data.get('batch_id')
        lot_id = request.data.get('lot_id')
        draft_data = request.data.get('draft_data')
        user = request.user
        
        logger.info(f"🔍 Draft request: user={user.username}, batch_id={batch_id}, lot_id={lot_id}")

        if not batch_id or not lot_id or not draft_data:
            logger.error(f"❌ Missing required fields: batch_id={batch_id}, lot_id={lot_id}, draft_data present={bool(draft_data)}")
            return Response({'error': 'Missing required fields'}, status=status.HTTP_400_BAD_REQUEST)

        obj, created = JigLoadingManualDraft.objects.update_or_create(
            batch_id=batch_id,
            lot_id=lot_id,
            user=user,
            defaults={'draft_data': draft_data}
        )

        # --- Fix: Update only the correct TotalStockModel ---
        try:
            stock = TotalStockModel.objects.get(batch_id__batch_id=batch_id, lot_id=lot_id)
            stock.jig_draft = True
            stock.save()
            logger.info(f"💾 Successfully updated lot status to Draft for batch_id={batch_id}, lot_id={lot_id}")
        except TotalStockModel.DoesNotExist:
            logger.error(f"❌ No TotalStockModel for lot_id={lot_id}, batch_id={batch_id}")
            return Response({'error': 'Stock record not found'}, status=status.HTTP_404_NOT_FOUND)
        
        # --- Update Jig table with draft info ---
        jig_id = draft_data.get('jig_id')
        if jig_id:
            jig_obj, _ = Jig.objects.get_or_create(jig_qr_id=jig_id)
            jig_obj.drafted = True
            jig_obj.current_user = user
            jig_obj.locked_at = timezone.now()
            jig_obj.batch_id = batch_id
            jig_obj.lot_id = lot_id
            jig_obj.save()
            logger.info(f"💾 Jig {jig_id} marked as drafted for batch {batch_id} by {user.username}")

        # --- Draft should NOT split lots - only save form data ---
        logger.info(f"💾 Draft saved without lot splitting - form data saved for later submission")

        logger.info(f"✅ Draft saved successfully for batch_id={batch_id}, lot_id={lot_id}")
        return Response({'success': True, 'created': created, 'updated_at': obj.updated_at})

# Manual Draft - Retrieve View
class JigLoadingManualDraftFetchAPIView(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request, *args, **kwargs):
        batch_id = request.GET.get('batch_id')
        lot_id = request.GET.get('lot_id')
        user = request.user
        try:
            draft = JigLoadingManualDraft.objects.get(batch_id=batch_id, lot_id=lot_id, user=user)
            return Response({'success': True, 'draft_data': draft.draft_data})
        except JigLoadingManualDraft.DoesNotExist:
            return Response({'success': False, 'draft_data': None})
        


class JigSubmitAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        import logging
        logger = logging.getLogger(__name__)
        
        data = request.data
        batch_id = data.get('batch_id')
        lot_id = data.get('lot_id')
        jig_qr_id = data.get('jig_qr_id')
        user = request.user
        
        logger.info(f"🚀 SUBMIT REQUEST: batch_id={batch_id}, lot_id={lot_id}, jig_qr_id={jig_qr_id}, user={user.username}")

        # Basic validation
        if not batch_id or not lot_id or not jig_qr_id:
            logger.error(f"❌ Missing required fields: batch_id={batch_id}, lot_id={lot_id}, jig_qr_id={jig_qr_id}")
            return Response({'success': False, 'message': 'Missing required fields'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Get related objects with specific error handling
            try:
                batch = ModelMasterCreation.objects.get(batch_id=batch_id)
            except ModelMasterCreation.DoesNotExist:
                return Response({'success': False, 'message': 'Batch not found'}, status=status.HTTP_400_BAD_REQUEST)

            try:
                stock = TotalStockModel.objects.get(batch_id=batch, lot_id=lot_id)
            except TotalStockModel.DoesNotExist:
                return Response({'success': False, 'message': 'Stock record not found'}, status=status.HTTP_400_BAD_REQUEST)

            try:
                jig = Jig.objects.get(jig_qr_id=jig_qr_id)
            except Jig.DoesNotExist:
                return Response({'success': False, 'message': 'Jig not found'}, status=status.HTTP_400_BAD_REQUEST)

            # Check if jig is locked by user
            if jig.current_user is not None and jig.current_user != user:
                return Response({'success': False, 'message': 'Jig is locked by another user'}, status=status.HTTP_403_FORBIDDEN)

            # Validate data (add more validations as needed)
            # For now, assume data is valid if provided

            # Parse tray data from draft
            tray_data = data.get('tray_info', [])  # Frontend sends as 'tray_info'
            logger.info(f"📊 Received tray data: {len(tray_data)} trays")
            
            if not tray_data:
                logger.error("❌ No tray data provided")
                return Response({'success': False, 'message': 'No tray data provided'}, status=status.HTTP_400_BAD_REQUEST)

            # Separate delinked and half-filled trays based on row_index
            delinked_trays = []
            half_filled_trays = []
            
            for tray in tray_data:
                row_index = tray.get('row_index', '')
                if row_index == 'half-filled' or str(row_index).startswith('half_'):
                    half_filled_trays.append(tray)
                    logger.info(f"🍃 Half-filled tray: {tray}")
                else:
                    delinked_trays.append(tray)
                    logger.info(f"🔗 Delinked tray: {tray}")
            
            # Calculate quantities
            delinked_cases = sum(int(t.get('tray_qty', 0)) for t in delinked_trays)
            half_filled_cases = sum(int(t.get('tray_qty', 0)) for t in half_filled_trays)
            broken_hooks_qty = int(data.get('broken_buildup_hooks', 0))
            
            # total_cases_loaded should be the effective loaded cases (original lot qty - broken hooks)
            # This represents the actual cases that went through the jig loading process
            original_lot_qty = stock.total_stock
            effective_loaded_cases = original_lot_qty - broken_hooks_qty
            total_cases_loaded = effective_loaded_cases  # Use effective cases for Complete Table
            
            logger.info(f"📊 Calculations: original_lot_qty={original_lot_qty}, delinked_cases={delinked_cases}, half_filled_cases={half_filled_cases}, broken_hooks_qty={broken_hooks_qty}, effective_loaded_cases={effective_loaded_cases}, total_cases_loaded={total_cases_loaded}")

            # Calculate correct tray quantities for delinked trays
            # Get tray capacity from batch
            tray_capacity = None
            if batch and batch.tray_type:
                try:
                    from modelmasterapp.models import TrayTypeMaster
                    tray_master = TrayTypeMaster.objects.filter(tray_type=batch.tray_type).first()
                    if tray_master:
                        tray_capacity = tray_master.tray_capacity
                except:
                    pass
            
            if not tray_capacity:
                tray_capacity = 16 if batch.tray_type == 'Normal' else 12  # fallback
            
            # Calculate correct tray distribution for effective_loaded_cases
            corrected_delinked_trays = []
            remaining_cases = effective_loaded_cases
            
            for i, delinked_tray in enumerate(delinked_trays):
                tray_id = delinked_tray.get('tray_id')
                if tray_id:
                    if remaining_cases >= tray_capacity:
                        tray_qty = tray_capacity
                        remaining_cases -= tray_capacity
                    else:
                        tray_qty = remaining_cases
                        remaining_cases = 0
                    
                    corrected_delinked_trays.append({
                        'tray_id': tray_id,
                        'tray_qty': str(tray_qty),
                        'row_index': delinked_tray.get('row_index', i)
                    })
            
            # NEW: If there's remaining_cases > 0 and half-filled trays exist, add the partial tray to delinked trays
            if remaining_cases > 0 and half_filled_trays:
                # Use the first (and typically only) half-filled tray
                partial_tray = half_filled_trays[0]
                partial_tray_id = partial_tray.get('tray_id')
                if partial_tray_id:
                    corrected_delinked_trays.append({
                        'tray_id': partial_tray_id,
                        'tray_qty': str(remaining_cases),  # The remaining cases (e.g., 7)
                        'row_index': partial_tray.get('row_index', len(corrected_delinked_trays))
                    })
                    remaining_cases = 0  # Ensure no further remainder
            
            logger.info(f"📊 Corrected delinked trays: {corrected_delinked_trays}")
            logger.info(f"📊 Total corrected cases: {sum(int(t['tray_qty']) for t in corrected_delinked_trays)}")
            
            
            

            # Create JigLoadTrayId entries for delinked trays (appears in Complete Table)
            for delinked_tray in corrected_delinked_trays:
                tray_id = delinked_tray.get('tray_id')
                tray_qty = int(delinked_tray.get('tray_qty', 0))
                if tray_id:
                    logger.info(f"🔗 Creating delinked tray {tray_id} for original lot {lot_id} with qty {tray_qty}")
                    JigLoadTrayId.objects.create(
                        lot_id=lot_id,  # Original lot_id for Complete Table
                        tray_id=tray_id,
                        tray_quantity=tray_qty,
                        batch_id=batch,
                        user=user,
                        date=timezone.now()
                    )
                    logger.info(f"✅ Created delinked tray entry {tray_id} for lot {lot_id}")
            
            # Create JigDetails for delinked trays (goes to Complete Table)
            jig_details = JigDetails.objects.create(
                jig_qr_id=jig_qr_id,
                lot_id=lot_id,
                total_cases_loaded=total_cases_loaded,  # Only delinked cases
                broken_hooks=data.get('broken_buildup_hooks', 0),
                empty_slots=data.get('empty_hooks', 0),
                jig_type='',  # Will be set from JigLoadingMaster if available
                jig_capacity=data.get('jig_capacity', 0),
                plating_color=batch.plating_color,
                ep_bath_type=data.get('nickel_bath_type', ''),
                forging=data.get('forging_info', ''),
                no_of_cycle=data.get('no_of_cycle', 1),
                tray_info=corrected_delinked_trays,  # Corrected delinked trays
                delink_tray_data=corrected_delinked_trays,
                half_filled_tray_data=half_filled_trays,  # Store half-filled tray data
                created_by=user,
                jig_loaded_date_time=timezone.now(),
                original_lot_qty=stock.total_stock,
                updated_lot_qty=broken_hooks_qty,  # Remaining cases for new lot
            )
            
            # Set jig_type from JigLoadingMaster if available
            try:
                jig_master = JigLoadingMaster.objects.get(model_stock_no=batch.model_stock_no)
                jig_details.jig_type = jig_master.jig_type
                jig_details.save()
            except JigLoadingMaster.DoesNotExist:
                pass  # Keep empty jig_type
            
            # Create new lot for half-filled tray (stays in Pick Table)
            # This should happen when broken_hooks > 0, regardless of half_filled_trays data
            new_lot_ids_list = []
            if broken_hooks_qty > 0:
                new_lot_id = f"LID{timezone.now().strftime('%d%m%Y%H%M%S')}{broken_hooks_qty:04d}"
                logger.info(f"🆕 Creating new lot for broken hooks cases: {new_lot_id} with {broken_hooks_qty} cases")
                
                new_lot = TotalStockModel.objects.create(
                    batch_id=stock.batch_id,
                    model_stock_no=stock.model_stock_no,
                    version=stock.version,
                    total_stock=broken_hooks_qty,  # Use broken hooks quantity
                    polish_finish=stock.polish_finish,
                    plating_color=stock.plating_color,
                    lot_id=new_lot_id,
                    created_at=timezone.now(),
                    Jig_Load_completed=False,
                    jig_draft=True,  # Set to True for partial draft lots
                    brass_audit_accptance=True,  # Set to True to include in Pick Table query
                    brass_audit_last_process_date_time=timezone.now(),  # Set for table display
                    last_process_date_time=timezone.now(),
                    last_process_module="Jig Loading",
                )
                logger.info(f"✅ Created new lot: {new_lot_id}")
                new_lot_ids_list.append(new_lot_id)
                
                # Create JigLoadTrayId entries for half-filled trays with new lot_id
                if half_filled_trays:
                    for half_filled_tray in half_filled_trays:
                        tray_id = half_filled_tray.get('tray_id')
                        if tray_id:
                            logger.info(f"🔄 Creating tray {tray_id} for new lot {new_lot_id}")
                            # Always create new tray entry for new lot_id
                            JigLoadTrayId.objects.create(
                                lot_id=new_lot_id,
                                tray_id=tray_id,
                                tray_quantity=broken_hooks_qty,  # Use broken hooks quantity
                                batch_id=batch,
                                user=user,
                                date=timezone.now()
                            )
                            logger.info(f"✅ Created new tray entry {tray_id} for lot {new_lot_id}")
                
                # Update JigDetails with new lot IDs
                jig_details.new_lot_ids = new_lot_ids_list
                jig_details.save()
                logger.info(f"✅ Updated JigDetails with new_lot_ids: {new_lot_ids_list}")

            # Update Jig
            jig.is_loaded = True
            jig.batch_id = batch_id
            jig.lot_id = lot_id
            jig.current_user = None
            jig.locked_at = None
            jig.drafted = False
            jig.save()
            logger.info(f"✅ Updated Jig {jig_qr_id}: is_loaded=True, drafted=False")

            # Update TotalStockModel (original lot)
            stock.Jig_Load_completed = True
            stock.jig_draft = False
            stock.save()
            logger.info(f"✅ Updated TotalStockModel {lot_id}: Jig_Load_completed=True, jig_draft=False")

            # Clear any manual draft
            try:
                draft = JigLoadingManualDraft.objects.get(batch_id=batch_id, lot_id=lot_id, user=user)
                draft.delete()
                logger.info(f"🗑️ Deleted manual draft for {lot_id}")
            except JigLoadingManualDraft.DoesNotExist:
                logger.info(f"ℹ️ No manual draft found for {lot_id}")

            logger.info(f"🎉 SUBMIT COMPLETED SUCCESSFULLY for batch_id={batch_id}, lot_id={lot_id}")
            return Response({'success': True, 'message': 'Jig submitted successfully'}, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"💥 Error submitting jig: {e}")
            return Response({'success': False, 'message': f'Internal server error: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        

@api_view(['POST'])
def validate_lock_jig_id(request):
    logger = logging.getLogger(__name__)
    try:
        # Check authentication first
        if not request.user.is_authenticated:
            logger.warning("❌ User not authenticated")
            return JsonResponse({'valid': False, 'message': 'Authentication required'}, status=401)
        
        logger.info(f"🚀 API CALLED - validate_lock_jig_id by user: {request.user.username}")
        
        jig_id = request.data.get('jig_id', '').strip()
        batch_id = request.data.get('batch_id', '').strip()
        lot_id = request.data.get('lot_id', '').strip()
        user = request.user
        
        logger.info(f"📊 Request data: jig_id={jig_id}, batch_id={batch_id}, user={user.username}")

        # Basic validation - check if jig_id is provided
        if not jig_id or len(jig_id) > 9:
            logger.info("⚠️ Basic validation failed: length check")
            return JsonResponse({'valid': False, 'message': 'Jig ID must be <= 9 characters.'}, status=200)

        # FIRST: Check for existing drafted jigs before format validation
        drafted_jig_current_batch = Jig.objects.filter(
            jig_qr_id=jig_id, drafted=True, batch_id=batch_id
        ).first()

        logger.info(f"🔍 Drafted jig current batch query result: {drafted_jig_current_batch}")

        if drafted_jig_current_batch:
            # Only allow if same user, same batch, and same lot
            if (
                drafted_jig_current_batch.current_user == user and
                getattr(drafted_jig_current_batch, 'lot_id', None) == lot_id
            ):
                logger.info("✅ Same user, same batch, same lot - allowing")
                return JsonResponse({'valid': True, 'message': 'Jig ID is valid'}, status=200)
            else:
                logger.info(f"❌ Jig ID in use for another row or user: {drafted_jig_current_batch.current_user.username}")
                return JsonResponse({'valid': False, 'message': f'Jig ID is being used by {drafted_jig_current_batch.current_user.username}.'}, status=200)

        # If not drafted for this batch, check if drafted for any other batch
        drafted_jig_other_batch = Jig.objects.filter(
            jig_qr_id=jig_id, drafted=True
        ).exclude(batch_id=batch_id).first()

        logger.info(f"🔍 Drafted jig other batch query result: {drafted_jig_other_batch}")

        if drafted_jig_other_batch:
            # If same user but different batch, restrict with correct message
            if drafted_jig_other_batch.current_user == user:
                logger.info(f"🎯 FOUND: Same user ({user.username}) but different batch - returning 'Already drafted for another batch'")
                return JsonResponse({'valid': False, 'message': 'Already drafted for another batch'}, status=200)
            # If different user, restrict
            else:
                logger.info(f"❌ Different user for different batch: {drafted_jig_other_batch.current_user.username}")
                return JsonResponse({'valid': False, 'message': f'Jig ID is being used by {drafted_jig_other_batch.current_user.username}.'}, status=200)

        # Check if jig_id exists in database
        try:
            jig = Jig.objects.get(jig_qr_id=jig_id)
        except Jig.DoesNotExist:
            return JsonResponse({'valid': False, 'message': 'Jig ID not found in database.'}, status=200)

        # Get expected jig capacity for this batch/lot
        expected_capacity = None
        try:
            stock = TotalStockModel.objects.get(lot_id=lot_id)
            batch = stock.batch_id
            
            # Resolve plating_stk_no same as modal
            plating_stk_no = ''
            if batch and batch.plating_stk_no:
                plating_stk_no = batch.plating_stk_no
            elif batch and batch.model_stock_no and batch.model_stock_no.plating_stk_no:
                plating_stk_no = batch.model_stock_no.plating_stk_no
            
            if plating_stk_no:
                jig_master = JigLoadingMaster.objects.filter(model_stock_no__plating_stk_no=plating_stk_no).first()
                if jig_master:
                    expected_capacity = jig_master.jig_capacity
                else:
                    logger.warning(f"JigLoadingMaster not found for plating_stk_no {plating_stk_no}")
            else:
                logger.warning(f"Plating stock number not found for batch {batch_id}, lot {lot_id}")
        except TotalStockModel.DoesNotExist as e:
            logger.warning(f"TotalStockModel not found for lot {lot_id}: {e}. Skipping capacity validation.")

        # Check if jig ID prefix matches expected capacity (if available)
        if expected_capacity is not None:
            expected_prefix = f"J{expected_capacity:03d}"
            if not jig_id.startswith(expected_prefix):
                return JsonResponse({'valid': False, 'message': f'Invalid jig ID for this capacity. Expected prefix: {expected_prefix}'}, status=200)

        # If not drafted/locked, show available message
        logger.info("✅ Jig ID is available")
        return JsonResponse({'valid': True, 'message': 'Jig ID is available to use'}, status=200)
        
    except Exception as e:
        logger.error(f"💥 Exception in validate_lock_jig_id: {e}")
        return JsonResponse({'valid': False, 'message': 'Internal server error'}, status=200)



@api_view(['GET'])
def jig_tray_id_list(request):
    stock_lot_id = request.GET.get('stock_lot_id')
    if not stock_lot_id:
        return JsonResponse({'success': False, 'error': 'stock_lot_id required'}, status=400)
    
    # First, check for JigDetails with tray_info (for completed jig loading)
    # First, check for JigDetails with tray_info (for completed jig loading)
    jig_detail = JigDetails.objects.filter(lot_id=stock_lot_id, unload_over=False).first()
    if jig_detail and jig_detail.tray_info:
        formatted_trays = []
        broken_hooks = jig_detail.broken_hooks or 0
        for idx, tray in enumerate(jig_detail.tray_info):
            # Exclude half-filled trays (qty == broken_hooks)
            if int(tray.get('tray_qty', 0)) == broken_hooks:
                continue
            formatted_tray = {
                'tray_id': tray.get('tray_id', ''),
                'tray_quantity': tray.get('tray_qty', ''),
                'row_index': str(idx),
                'tray_status': "Delinked",
                'original_quantity': tray.get('tray_qty', ''),
                'excluded_quantity': 0,
            }
            formatted_trays.append(formatted_tray)
        return JsonResponse({'success': True, 'trays': formatted_trays})
    
    # Fallback to JigLoadTrayId
    tray_objects = JigLoadTrayId.objects.filter(lot_id=stock_lot_id).order_by('date')
    
    if tray_objects.exists():
        formatted_trays = []
        for idx, tray_obj in enumerate(tray_objects):
            # Determine tray status based on broken_hooks_effective_tray field
            tray_status = "Delinked" if tray_obj.broken_hooks_effective_tray else "Partial Draft"
            
            formatted_tray = {
                'tray_id': tray_obj.tray_id,
                'tray_quantity': tray_obj.effective_tray_qty if tray_obj.broken_hooks_effective_tray else tray_obj.tray_quantity,  # Use effective quantity for delinked trays
                'row_index': str(idx),
                'tray_status': tray_status,
                'original_quantity': tray_obj.tray_quantity,  # For reference
                'excluded_quantity': max(0, tray_obj.broken_hooks_excluded_qty),  # Ensure non-negative values
            }
            formatted_trays.append(formatted_tray)
        
        return JsonResponse({'success': True, 'trays': formatted_trays})
    else:
        # Fallback to JigDetails.delink_tray_data if no JigLoadTrayId found
        if jig_detail and jig_detail.delink_tray_data:
            formatted_trays = []
            for tray in jig_detail.delink_tray_data:
                formatted_tray = {
                    'tray_id': tray.get('tray_id', ''),
                    'tray_quantity': tray.get('tray_qty', ''),
                    'row_index': tray.get('row_index', ''),
                    'tray_status': "Delinked",  # Default for legacy data
                    'original_quantity': tray.get('tray_qty', ''),
                    'excluded_quantity': 0,
                }
                formatted_trays.append(formatted_tray)
            return JsonResponse({'success': True, 'trays': formatted_trays})
        else:
            return JsonResponse({'success': True, 'trays': []})

                   
# Jig Loading Complete Table - Main View 
class JigCompletedTable(TemplateView):
    template_name = "JigLoading/Jig_CompletedTable.html"
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        completed_qs = TotalStockModel.objects.filter(Jig_Load_completed=True)
        completed_data = []
        for stock in completed_qs:
            plating_stk_no = (
                getattr(stock.batch_id, 'plating_stk_no', None)
                or getattr(stock.model_stock_no, 'plating_stk_no', None)
            )
            polishing_stk_no = (
                getattr(stock.batch_id, 'polishing_stk_no', None)
                or getattr(stock.model_stock_no, 'polishing_stk_no', None)
            )
            tray_capacity = JigView.get_tray_capacity(stock)
            jig_type = ''
            jig_capacity = ''
            if stock.model_stock_no:
                jig_master = JigLoadingMaster.objects.filter(model_stock_no=stock.model_stock_no).first()
                if jig_master:
                    jig_type = jig_master.jig_type
                    jig_capacity = jig_master.jig_capacity

            # Fetch JigDetails for this lot (for Jig ID, loaded date, etc.)
            jig_detail = JigDetails.objects.filter(lot_id=stock.lot_id).order_by('-date_time').first()

            # Use JigDetails.total_cases_loaded as the effective lot quantity, fallback to stock.total_stock
            lot_qty = jig_detail.total_cases_loaded if jig_detail else (stock.total_stock or 0)

            # --- FIX: Always use tray_info from JigDetails if available ---
            tray_info = []
            if jig_detail and getattr(jig_detail, 'tray_info', None):
                tray_info = jig_detail.tray_info
                no_of_trays = len(tray_info)
            else:
                # Calculate number of trays based on actual JigLoadTrayId entries for this lot
                actual_tray_count = JigLoadTrayId.objects.filter(
                    lot_id=stock.lot_id, 
                    batch_id=stock.batch_id
                ).count()
                # Use actual tray count if available, otherwise calculate from lot_qty
                if actual_tray_count > 0:
                    no_of_trays = actual_tray_count
                else:
                    no_of_trays = 0
                    if tray_capacity and tray_capacity > 0 and lot_qty > 0:
                        no_of_trays = (lot_qty // tray_capacity) + (1 if lot_qty % tray_capacity else 0)

            completed_data.append({
                'batch_id': stock.batch_id.batch_id if stock.batch_id else '',
                'jig_loaded_date_time': (
                    jig_detail.jig_loaded_date_time
                    if jig_detail and jig_detail.jig_loaded_date_time
                    else (jig_detail.date_time if jig_detail else '')
                ),
                'lot_id': stock.lot_id,
                'lot_plating_stk_nos': plating_stk_no or 'No Plating Stock No',
                'lot_polishing_stk_nos': polishing_stk_no or 'No Polishing Stock No',
                'plating_color': stock.plating_color.plating_color if stock.plating_color else '',
                'polish_finish': stock.polish_finish.polish_finish if stock.polish_finish else '',
                'lot_version_names': stock.version.version_internal if stock.version else '',
                'tray_type': getattr(stock.batch_id, 'tray_type', ''),
                'tray_capacity': getattr(stock.batch_id, 'tray_capacity', ''),
                'calculated_no_of_trays': no_of_trays,
                'tray_info': tray_info,  # <-- Pass tray_info to context
                'total_cases_loaded': jig_detail.total_cases_loaded if jig_detail else '',
                'jig_type': jig_type,
                'jig_capacity': jig_capacity,
                'jig_qr_id': jig_detail.jig_qr_id if jig_detail else '',
                'jig_loaded_date_time': jig_detail.jig_loaded_date_time if jig_detail else '',
                'model_images': [img.master_image.url for img in stock.model_stock_no.images.all()] if stock.model_stock_no else [],
                'audio_remark': getattr(jig_detail, 'pick_remarks', ''),
                'IP_jig_pick_remarks': getattr(jig_detail, 'IP_jig_pick_remarks', ''),
            })
        context['jig_details'] = completed_data
        return context