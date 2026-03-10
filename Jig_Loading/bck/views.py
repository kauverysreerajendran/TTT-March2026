from django.views.generic import *
from modelmasterapp.models import *
from .models import Jig, JigLoadingMaster, JigLoadTrayId, JigDetails, JigCompleted, JigDraft
# NOTE: JigLoadingManualDraft is deprecated - use JigDraft for perfect accountability
from rest_framework.decorators import *
from django.http import JsonResponse
import logging
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.renderers import TemplateHTMLRenderer
from rest_framework import status
from django.shortcuts import get_object_or_404
from django.utils import timezone
from math import ceil
from rest_framework.permissions import IsAuthenticated
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
import logging
import json
import re



# Jig Loading Pick Table - Main View (display completed batch from Brass Audit Complete table)
@method_decorator(login_required, name='dispatch') 
class JigView(APIView):
    renderer_classes = [TemplateHTMLRenderer]
    template_name = "JigLoading/Jig_Picktable.html"
    permission_classes = [IsAuthenticated]
    
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
    
    
    

    def get(self, request):
        user = request.user
        
        # Only show lots NOT completed (do not change row order), OR lots with half-filled trays from JigCompleted
        total_stock_qs = (
            TotalStockModel.objects.filter(brass_audit_accptance=True, Jig_Load_completed=False)
            | TotalStockModel.objects.filter(brass_audit_few_cases_accptance=True, Jig_Load_completed=False)
            | TotalStockModel.objects.filter(brass_audit_rejection=True, Jig_Load_completed=False)
            # | TotalStockModel.objects.filter(jig_draft=True, Jig_Load_completed=False)  # Include partial draft lots
        )
        
        # Also include completed lots that have half-filled trays (broken hooks remaining)
        completed_with_half_filled = JigCompleted.objects.filter(
            half_filled_tray_info__isnull=False
        ).exclude(
            half_filled_tray_info=[]
        ).values_list('lot_id', flat=True)
        
        if completed_with_half_filled:
            total_stock_qs |= TotalStockModel.objects.filter(
                lot_id__in=completed_with_half_filled,
                Jig_Load_completed=True
            )
        
        # Order by newest first (most recent brass audit date)
        total_stock_qs = total_stock_qs.order_by('-brass_audit_last_process_date_time')
        
        # Pagination
        page_number = request.GET.get('page', 1)
        paginator = Paginator(total_stock_qs, 10)
        page_obj = paginator.get_page(page_number)

        master_data = []
        for stock in page_obj.object_list:
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
            if getattr(stock, 'brass_audit_release_lot', False):
                lot_status = 'Yet to Released'
                lot_status_class = 'lot-status-yet-released'
            elif getattr(stock, 'jig_draft', False):
                lot_status = 'Draft'
                lot_status_class = 'lot-status-draft'
            else:
                # Check if this is a completed lot with half-filled trays (broken hooks remaining)
                jig_completed = JigCompleted.objects.filter(lot_id=stock.lot_id).first()
                if jig_completed and jig_completed.half_filled_tray_info:
                    lot_status = 'Partial Draft'
                    lot_status_class = 'lot-status-draft'
                    # Update display_qty to show remaining broken hooks quantity
                    lot_qty = jig_completed.half_filled_tray_qty or sum(t.get('cases', 0) for t in jig_completed.half_filled_tray_info)
                else:
                    lot_status = 'Yet to Start'
                    lot_status_class = 'lot-status-yet'

            # Check hold status from JigCompleted
            jig_completed = JigCompleted.objects.filter(lot_id=stock.lot_id, batch_id=stock.batch_id, user=user).first()
            hold_status = jig_completed.hold_status if jig_completed else False
            hold_reason = jig_completed.hold_reason if jig_completed else ''
            unhold_reason = jig_completed.unhold_reason if jig_completed else ''

            plating_color = ''
            if stock.plating_color:
                plating_color = stock.plating_color.plating_color
            elif stock.plating_stk_no:
                try:
                    plating_obj = Plating_Color.objects.get(plating_color_internal=stock.plating_stk_no)
                    plating_color = plating_obj.plating_color
                except Plating_Color.DoesNotExist:
                    plating_color = ''

            master_data.append({
                'batch_id': stock.batch_id.batch_id if stock.batch_id else '',
                'stock_lot_id': stock.lot_id,
                'plating_stk_no': plating_stk_no,
                'polishing_stk_no': polishing_stk_no,
                'plating_color': plating_color,
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
                'hold_status': hold_status,
                'hold_reason': hold_reason,
                'unhold_reason': unhold_reason,
            })
        
        context = {
            'master_data': master_data,
            'page_obj': page_obj,
            'paginator': paginator,
            'user': user,
            'csp_nonce': getattr(request, 'csp_nonce', ''),
        }
        return Response(context, template_name=self.template_name)

# Tray Info API View
class TrayInfoView(APIView):
    def get(self, request, *args, **kwargs):
        lot_id = request.GET.get('lot_id')
        batch_id = request.GET.get('batch_id')
        
        # Check if this lot is completed
        jig_completed = JigCompleted.objects.filter(lot_id=lot_id, batch_id=batch_id).first()
        
        if jig_completed:
            # For completed lots, only show half-filled trays (remaining to process)
            tray_list = []
            if jig_completed.half_filled_tray_info:
                for tray in jig_completed.half_filled_tray_info:
                    tray_list.append({
                        'tray_id': tray.get('tray_id'),
                        'tray_quantity': tray.get('cases')
                    })
                    
            # Sort by tray_quantity ascending to prevent shuffling
            tray_list.sort(key=lambda x: x['tray_quantity'])
        else:
            # Check if this is a leftover lot from a completed jig
            jig_completed_for_batch = JigCompleted.objects.filter(batch_id=batch_id, half_filled_lot_qty__gt=0).first()
            if jig_completed_for_batch and jig_completed_for_batch.half_filled_tray_info:
                tray_list = [{'tray_id': t['tray_id'], 'tray_quantity': t['cases']} for t in jig_completed_for_batch.half_filled_tray_info]
            else:
                # For incomplete lots, show allocated trays from JigLoadTrayId
                trays = JigLoadTrayId.objects.filter(lot_id=lot_id, batch_id__batch_id=batch_id).order_by('tray_id').values('tray_id', 'tray_quantity')
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
        """Enhanced modal data with optional JigDraft integration"""
        import logging
        logger = logging.getLogger(__name__)
        
        batch_id = request.GET.get('batch_id')
        lot_id = request.GET.get('lot_id')
        jig_qr_id = request.GET.get('jig_qr_id')
        use_new_draft_system = request.GET.get('use_draft_system', 'false').lower() == 'true'
        # --- FIX: Only restore from draft if not supplied by user ---
        broken_hooks_param = request.GET.get('broken_hooks', None)
        broken_hooks = int(broken_hooks_param) if broken_hooks_param not in [None, ''] else 0

        # Check if we should use the new JigDraft system
        if use_new_draft_system:
            return self._handle_new_draft_system(request, batch_id, lot_id, jig_qr_id, broken_hooks)
        
        # DEFAULT: Use new JigDraft system (no more legacy manual draft)
        return self._handle_new_draft_system(request, batch_id, lot_id, jig_qr_id, broken_hooks)

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
            try:
                modal_data = self._prepare_modal_data(request, batch, model_master, stock, jig_qr_id, lot_id, broken_hooks)
            except Exception as e:
                logger.error(f"❌ Error preparing modal data: {e}")
                return JsonResponse({
                    'success': False,
                    'error': f'Failed to prepare modal data: {str(e)}'
                }, status=500)

            # Load draft data if exists and override modal_data
            try:
                draft = JigLoadingManualDraft.objects.get(batch_id=batch_id, lot_id=lot_id, user=request.user)
                draft_data = draft.draft_data
                # Override with draft values
                modal_data['original_lot_qty'] = draft.original_lot_qty or modal_data.get('original_lot_qty')
                modal_data['updated_lot_qty'] = draft.updated_lot_qty or modal_data.get('updated_lot_qty')
                modal_data['delink_tray_info'] = draft.delink_tray_info or []
                modal_data['partial_tray_info'] = draft_data.get('partial_tray_info', [])
                modal_data['half_filled_tray_info'] = draft.half_filled_tray_info or []
                # --- PATCH: Restore half_filled_tray_cases from draft if present ---
                if 'half_filled_tray_cases' in draft_data:
                    modal_data['half_filled_tray_cases'] = draft_data.get('half_filled_tray_cases', 0)
                elif draft.half_filled_tray_info:
                    modal_data['half_filled_tray_cases'] = sum(t.get('cases', 0) for t in (draft.half_filled_tray_info or []))
                # --- PATCH: Transform draft's tray_distribution to expected format ---
                draft_tray_dist = draft_data.get('tray_distribution')
                if draft_tray_dist:
                    delink_trays = draft_tray_dist.get('delink', [])
                    half_filled_trays = draft_tray_dist.get('half_filled', [])
                    modal_data['tray_distribution'] = {
                        'current_lot': {
                            'total_cases': sum(t.get('cases', 0) for t in delink_trays),
                            'distribution': {
                                'total_trays': len(delink_trays),
                                'trays': [{'tray_number': i+1, 'cases': t.get('cases', 0), 'is_full': True, 'scan_required': False} for i, t in enumerate(delink_trays)]
                            }
                        },
                        'half_filled_lot': {
                            'total_cases': sum(t.get('cases', 0) for t in half_filled_trays),
                            'distribution': {
                                'total_trays': len(half_filled_trays),
                                'trays': [{'tray_number': i+1, 'cases': t.get('cases', 0), 'is_full': False, 'scan_required': True, 'tray_id': t.get('tray_id')} for i, t in enumerate(half_filled_trays)]
                            }
                        }
                    }
                else:
                    modal_data['tray_distribution'] = modal_data.get('tray_distribution')
                # Only restore broken hooks from draft if user didn't provide a new value
                if broken_hooks_param in [None, '']:
                    modal_data['broken_buildup_hooks'] = draft.broken_hooks or modal_data.get('broken_buildup_hooks')
                modal_data['jig_capacity'] = draft.jig_capacity or modal_data.get('jig_capacity')
                modal_data['loaded_cases_qty'] = draft.loaded_cases_qty or modal_data.get('loaded_cases_qty')
                logger.info(f"🔄 Restored draft data for batch_id={batch_id}, lot_id={lot_id}")
            except JigLoadingManualDraft.DoesNotExist:
                pass

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
                'updated_lot_qty': modal_data.get('updated_lot_qty'),
                'jig_capacity': modal_data.get('jig_capacity'),
                'effective_jig_capacity': modal_data.get('effective_jig_capacity'),
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
                'half_filled_tray_info': modal_data.get('half_filled_tray_info', []),
                'remaining_cases': modal_data.get('remaining_cases', 0),
                'excess_message': excess_message,
            })

        except Exception as e:
            logger.error(f"💥 Exception in JigAddModalDataView: {str(e)}")
            return JsonResponse({
                'success': False,
                'error': f'Failed to load modal data: {str(e)}'
            }, status=500)
    
    def _handle_new_draft_system(self, request, batch_id, lot_id, jig_qr_id, broken_hooks):
        """
        Handle modal data using the new JigDraft system with perfect accountability
        """
        try:
            # If no jig scanned yet, return master data without draft
            if not jig_qr_id or jig_qr_id.strip() == '':
                # Get master data for display
                try:
                    jig_capacity, tray_type, tray_capacity, original_qty = JigDraft.get_master_data(
                        batch_id, lot_id, jig_qr_id
                    )
                    
                    # Adjust for broken hooks
                    effective_jig_capacity = max(0, jig_capacity - broken_hooks)
                    jig_delinked_qty = min(original_qty, effective_jig_capacity)
                    jig_excess_qty = original_qty - jig_delinked_qty
                    
                    # Get original tray distribution
                    original_tray_info = self._get_original_tray_distribution(lot_id, batch_id)
                    
                    # Generate delink table for UI
                    delink_table, half_filled_tray_info = self._generate_delink_table_from_master_data(
                        jig_delinked_qty, tray_capacity, original_tray_info
                    )
                    
                    # Get batch for form_title and model images
                    try:
                        from modelmasterapp.models import ModelMasterCreation, TotalStockModel
                        batch = ModelMasterCreation.objects.get(batch_id=batch_id)
                        stock = TotalStockModel.objects.filter(lot_id=lot_id, batch_id__batch_id=batch_id).first()
                        model_master = batch.model_stock_no if batch else None
                        
                        # Get model images
                        model_images = []
                        if model_master and model_master.images.exists():
                            for img in model_master.images.all():
                                model_images.append({
                                    'url': img.image.url if img.image else None,
                                    'model_no': model_master.model_no if hasattr(model_master, 'model_no') else '',
                                    'plating_stk_no': batch.plating_stk_no if batch else ''
                                })
                        
                        # Resolve plating stock number
                        plating_stk_no = self._resolve_plating_stock_number(batch, model_master)
                        
                    except Exception as e:
                        logger.error(f"Error getting batch/model data: {e}")
                        batch = None
                        model_images = []
                        plating_stk_no = ''

                    return JsonResponse({
                        'success': True,
                        'source': 'master_data_no_jig',
                        'form_title': f"Add Jig: {batch_id}",
                        'jig_id': jig_qr_id or '',
                        'jig_qr_id': jig_qr_id or '',
                        'lot_id': lot_id,
                        'batch_id': batch_id,
                        'jig_capacity': jig_capacity,
                        'tray_type': tray_type,
                        'tray_capacity': tray_capacity,
                        'jig_original_qty': original_qty,
                        'original_lot_qty': original_qty,
                        'jig_delinked_qty': jig_delinked_qty,
                        'jig_excess_qty': jig_excess_qty,
                        'effective_jig_capacity': effective_jig_capacity,
                        'broken_buildup_hooks': broken_hooks,
                        'broken_hooks': broken_hooks,
                        'loaded_cases_qty': 0,  # No trays scanned yet
                        'empty_hooks': effective_jig_capacity - jig_delinked_qty,  # Correct calculation: capacity - what can be loaded
                        'nickel_bath_type': '',  # No jig scanned yet
                        'no_of_cycle': 1,
                        'model_images': model_images,
                        'plating_stk_no': plating_stk_no,
                        'original_tray_info': original_tray_info,
                        'delink_table': delink_table,
                        'half_filled_tray_info': half_filled_tray_info,
                        'can_save': False,  # Cannot save until jig is scanned
                        'add_model_enabled': False,  # Cannot add model until jig is scanned
                        'accountability_summary': {
                            'formula': f'{jig_delinked_qty} (delink) + {jig_excess_qty} (excess) = {original_qty} (total)',
                            'is_valid': (jig_delinked_qty + jig_excess_qty) == original_qty
                        },
                        'modal_validation': {
                            'can_save': False,  # Cannot save until jig is scanned
                            'max_broken_hooks': self._get_max_broken_hooks(jig_qr_id),
                            'overall_valid': False,
                        },
                        'ui_config': {
                            'show_tray_scanning': False,  # Don't show tray scanning until jig is scanned
                            'show_half_filled': jig_excess_qty > 0,
                            'master_data_source': {
                                'jig_capacity_from': 'JigLoadingMaster',
                                'tray_type_from': 'ModelMasterCreation',
                                'tray_capacity_from': 'TrayType',
                                'original_qty_from': 'TotalStockModel'
                            }
                        }
                    })
                    
                except ValueError as ve:
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.error(f"❌ Master data error (no jig) for batch_id={batch_id}, lot_id={lot_id}: {str(ve)}")
                    
                    return JsonResponse({
                        'success': False,
                        'error': f'Master data error: {str(ve)}',
                        'suggestion': 'Please ensure all master data is properly configured',
                        'debug_info': {
                            'batch_id': batch_id,
                            'lot_id': lot_id,
                            'jig_qr_id': jig_qr_id,
                            'error_details': str(ve)
                        }
                    }, status=400)
            
            # Check for existing active draft (only if jig_qr_id is provided)
            existing_draft = JigDraft.objects.filter(
                jig_qr_id=jig_qr_id,
                lot_id=lot_id,
                is_active=True
            ).first()
            
            if existing_draft:
                # Return existing draft data
                return JsonResponse({
                    'success': True,
                    'source': 'existing_draft',
                    'data': {
                        'draft_id': existing_draft.id,
                        'jig_qr_id': existing_draft.jig_qr_id,
                        'lot_id': existing_draft.lot_id,
                        'batch_id': existing_draft.batch_id,
                        'jig_capacity': existing_draft.jig_capacity,
                        'tray_type': existing_draft.tray_type,
                        'tray_capacity': existing_draft.tray_capacity,
                        'jig_original_qty': existing_draft.jig_original_qty,
                        'jig_delinked_qty': existing_draft.jig_delinked_qty,
                        'jig_excess_qty': existing_draft.jig_excess_qty,
                        'effective_jig_capacity': max(0, existing_draft.jig_capacity - broken_hooks),
                        'broken_hooks': broken_hooks,
                        'original_tray_info': existing_draft.original_tray_info,
                        'delink_tray_info': existing_draft.delink_tray_info,
                        'half_filled_tray_info': existing_draft.half_filled_tray_info,
                        'reconciliation_ok': existing_draft.reconciliation_ok,
                        'remarks': existing_draft.remarks,
                        'modal_validation': {
                            'can_save': existing_draft.reconciliation_ok,
                            'max_broken_hooks': self._get_max_broken_hooks(jig_qr_id),
                        },
                        'ui_config': {
                            'show_tray_scanning': True,
                            'show_half_filled': existing_draft.jig_excess_qty > 0,
                            'accountability_summary': {
                                'total_original': existing_draft.jig_original_qty,
                                'total_delinked': sum(t.get('qty', 0) for t in existing_draft.delink_tray_info),
                                'total_half_filled': sum(t.get('qty', 0) for t in existing_draft.half_filled_tray_info)
                            }
                        }
                    }
                })
            
            else:
                # Create new draft using master data
                try:
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.info(f"🔍 Fetching master data for batch_id={batch_id}, lot_id={lot_id}")
                    
                    jig_capacity, tray_type, tray_capacity, original_qty = JigDraft.get_master_data(
                        batch_id, lot_id, jig_qr_id
                    )
                    
                    logger.info(f"✅ Master data fetched: jig_capacity={jig_capacity}, tray_type={tray_type}, tray_capacity={tray_capacity}, original_qty={original_qty}")
                    
                    # Adjust for broken hooks
                    effective_jig_capacity = max(0, jig_capacity - broken_hooks)
                    jig_delinked_qty = min(original_qty, effective_jig_capacity)
                    jig_excess_qty = original_qty - jig_delinked_qty
                    
                    # Get original tray distribution
                    original_tray_info = self._get_original_tray_distribution(lot_id, batch_id)
                    
                    # Generate delink table for UI
                    delink_table = self._generate_delink_table_from_master_data(
                        jig_delinked_qty, tray_capacity, original_tray_info
                    )
                    
                    return JsonResponse({
                        'success': True,
                        'source': 'master_data',
                        'data': {
                            'jig_qr_id': jig_qr_id,
                            'lot_id': lot_id,
                            'batch_id': batch_id,
                            'jig_capacity': jig_capacity,
                            'tray_type': tray_type,
                            'tray_capacity': tray_capacity,
                            'jig_original_qty': original_qty,
                            'jig_delinked_qty': jig_delinked_qty,
                            'jig_excess_qty': jig_excess_qty,
                            'effective_jig_capacity': effective_jig_capacity,
                            'broken_hooks': broken_hooks,
                            'original_tray_info': original_tray_info,
                            'delink_table': delink_table,
                            'accountability_summary': {
                                'formula': f'{jig_delinked_qty} (delink) + {jig_excess_qty} (excess) = {original_qty} (total)',
                                'is_valid': (jig_delinked_qty + jig_excess_qty) == original_qty
                            },
                            'modal_validation': {
                                'can_save': False,  # Cannot save until trays are scanned
                                'max_broken_hooks': self._get_max_broken_hooks(jig_qr_id),
                            },
                            'ui_config': {
                                'show_tray_scanning': True,
                                'show_half_filled': jig_excess_qty > 0,
                                'master_data_source': {
                                    'jig_capacity_from': 'JigLoadingMaster',
                                    'tray_type_from': 'ModelMasterCreation',
                                    'tray_capacity_from': 'TrayType',
                                    'original_qty_from': 'TotalStockModel'
                                }
                            }
                        }
                    })
                    
                except ValueError as ve:
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.error(f"❌ Master data error (with jig) for batch_id={batch_id}, lot_id={lot_id}: {str(ve)}")
                    
                    return JsonResponse({
                        'success': False,
                        'error': f'Master data error: {str(ve)}',
                        'suggestion': 'Please ensure all master data is properly configured',
                        'debug_info': {
                            'batch_id': batch_id,
                            'lot_id': lot_id,
                            'jig_qr_id': jig_qr_id,
                            'error_details': str(ve)
                        }
                    }, status=400)
        
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': f'Draft system error: {str(e)}'
            }, status=500)
    
    def _get_max_broken_hooks(self, jig_qr_id):
        """Get max broken hooks based on jig ID prefix"""
        max_broken_hooks = 5  # default
        if jig_qr_id:
            if jig_qr_id.startswith(('B-', 'b-')):
                max_broken_hooks = 18
            elif jig_qr_id.startswith(('N-', 'n-')):
                max_broken_hooks = 25
        return max_broken_hooks
    
    def _get_original_tray_distribution(self, lot_id, batch_id):
        """Get original tray distribution from JigLoadTrayId"""
        tray_objects = JigLoadTrayId.objects.filter(
            lot_id=lot_id,
            batch_id__batch_id=batch_id
        ).order_by('id')
        
        original_tray_info = []
        for tray in tray_objects:
            original_tray_info.append({
                "tray_id": tray.tray_id,
                "qty": tray.tray_quantity or 0,
                "is_top_tray": tray.top_tray
            })
        
        return original_tray_info
    
    def _generate_delink_table_from_master_data(self, delinked_qty, tray_capacity, original_tray_info):
        """
        Generate delink table and half-filled tray info for user scanning.
        Create empty tray slots based on calculated tray counts, no pre-assigned tray IDs.
        """
        if tray_capacity <= 0:
            return [], []
        
        delink_tray_info = []
        half_filled_tray_info = []
        
        # Calculate trays needed for delink (full + partial)
        full_trays_delink = delinked_qty // tray_capacity
        partial_qty_delink = delinked_qty % tray_capacity
        
        # Add full trays for delink
        for i in range(full_trays_delink):
            delink_tray_info.append({
                'tray_id': '',  # Empty for user scanning
                'qty': tray_capacity,
                'is_top_tray': False  # Not applicable
            })
        
        # Add partial tray for delink if needed
        if partial_qty_delink > 0:
            delink_tray_info.append({
                'tray_id': '',  # Empty for user scanning
                'qty': partial_qty_delink,
                'is_top_tray': False
            })
        
        # Calculate trays needed for half-filled (excess)
        excess_qty = self.jig_excess_qty  # Assuming available in context
        full_trays_half = excess_qty // tray_capacity
        partial_qty_half = excess_qty % tray_capacity
        
        # Add full trays for half-filled
        for i in range(full_trays_half):
            half_filled_tray_info.append({
                'tray_id': '',  # Empty for user scanning
                'qty': tray_capacity,
                'original_qty': tray_capacity
            })
        
        # Add partial tray for half-filled if needed
        if partial_qty_half > 0:
            half_filled_tray_info.append({
                'tray_id': '',  # Empty for user scanning
                'qty': partial_qty_half,
                'original_qty': partial_qty_half
            })
        
        return delink_tray_info, half_filled_tray_info
      
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
        
        # Set form title
        modal_data['form_title'] = f"Add Jig: {batch.batch_id}"
        
        # Set jig ID
        modal_data['jig_id'] = jig_qr_id
        
        # Set max broken hooks in validation
        modal_data['modal_validation']['max_broken_hooks'] = max_broken_hooks
        
        # Get jig details if exists
        jig_details = None
        if jig_qr_id:
            jig_details = JigDetails.objects.filter(jig_qr_id=jig_qr_id, lot_id=lot_id).first()
        
        # Set initial loaded_cases_qty to 0 (no trays scanned yet)
        modal_data['loaded_cases_qty'] = 0
        
        # Get jig capacity from JigLoadingMaster table first
        jig_master = JigLoadingMaster.objects.filter(model_stock_no=model_master).first()
        if not jig_master and hasattr(model_master, "model_no"):
            jig_master = JigLoadingMaster.objects.filter(model_stock_no__model_no=model_master.model_no).first()
        
        if jig_master:
            modal_data['jig_type'] = f"{jig_master.jig_capacity:03d}" if jig_master.jig_capacity else None
            modal_data['jig_capacity'] = jig_master.jig_capacity
            print(f"  Jig Master Found: {jig_master.jig_type} - Capacity: {jig_master.jig_capacity}")
        else:
            # Try to derive jig capacity from jig_qr_id
            if jig_qr_id:
                match = re.match(r'J(\d+)-', jig_qr_id)
                if match:
                    jig_capacity_from_id = int(match.group(1))
                    modal_data['jig_capacity'] = jig_capacity_from_id
                    modal_data['jig_type'] = f"{jig_capacity_from_id:03d}"
                    print(f"  Jig Capacity derived from ID: {jig_capacity_from_id}")
                else:
                    modal_data['jig_capacity'] = 144  # Default fallback
                    modal_data['jig_type'] = "144"
                    print(f"  Using default jig capacity: 144")
            else:
                modal_data['jig_capacity'] = 144  # Default fallback
                modal_data['jig_type'] = "144"
                print(f"  Using default jig capacity: 144")
        
        # Calculate effective jig capacity (jig_capacity - broken_hooks)
        effective_jig_capacity = max(0, modal_data['jig_capacity'] - broken_hooks)
        
        # Calculate delink cases (cases that go into delink trays)
        original_lot_qty = stock.total_stock or 0
        delink_cases = min(original_lot_qty, effective_jig_capacity)
        
        # Calculate excess cases
        excess_cases = original_lot_qty - delink_cases
        
        # Half-filled cases = excess cases (broken hooks already accounted for in effective_jig_capacity)
        half_filled_cases = excess_cases
        
        # Store values
        modal_data['effective_loaded_cases'] = delink_cases
        modal_data['half_filled_cases'] = half_filled_cases
        modal_data['effective_jig_capacity'] = effective_jig_capacity
        modal_data['original_lot_qty'] = original_lot_qty
        
        # Resolve plating stock number
        plating_stk_no = self._resolve_plating_stock_number(batch, model_master)
        
        # Get tray capacity from TrayType master table (STRICT: Always from database)
        tray_capacity = None
        if batch and batch.tray_type:
            tray_type_obj = TrayType.objects.filter(tray_type=batch.tray_type).first()
            if tray_type_obj and tray_type_obj.tray_capacity and tray_type_obj.tray_capacity > 0:
                tray_capacity = tray_type_obj.tray_capacity
            else:
                logger.warning(f"⚠️ Tray type '{batch.tray_type}' not properly configured, using default capacity")
                tray_capacity = 16  # Fallback for Normal tray type
        else:
            logger.warning(f"⚠️ No tray type found in batch, using default Normal tray capacity")
            tray_capacity = 16  # Default for Normal tray type
        
        print(f"💾 MASTER TABLE LOOKUP:")
        print(f"  Batch Tray Type: {batch.tray_type if batch else 'None'}")
        print(f"  Resolved Tray Capacity: {tray_capacity}")
        
        # Jig Capacity already fetched above
        
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
            modal_data['loaded_hooks'] = min(jig_details.total_cases_loaded or 0, modal_data['effective_jig_capacity'])

            # Calculate empty hooks based on effective jig capacity
            if modal_data['loaded_cases_qty'] < modal_data['effective_jig_capacity']:
                modal_data['empty_hooks'] = modal_data['effective_jig_capacity'] - modal_data['loaded_cases_qty']
            else:
                modal_data['empty_hooks'] = 0
            modal_data['no_of_cycle'] = jig_details.no_of_cycle
        else:
            # Auto-fill for new entries with comprehensive defaults
            modal_data['nickel_bath_type'] = "Bright"  # Default
            modal_data['loaded_cases_qty'] = delink_cases
            modal_data['loaded_hooks'] = delink_cases
            # Calculate empty hooks based on effective jig capacity
            if delink_cases < effective_jig_capacity:
                modal_data['empty_hooks'] = effective_jig_capacity - delink_cases
            else:
                modal_data['empty_hooks'] = 0

        # Delink Table preparation (existing tray data)
        modal_data['delink_table'] = self._prepare_existing_delink_table(lot_id, batch, modal_data['effective_loaded_cases'], tray_capacity, broken_hooks)

        # Model Images preparation with validation
        modal_data['model_images'] = self._prepare_model_images(model_master)

        # Check for multi-model scenario
        selected_models = request.GET.get('selected_models')
        if selected_models:
            # Parse selected models and recalculate
            import json
            try:
                selected_models_data = json.loads(selected_models)
                modal_data = self._handle_multi_model_selection(
                    modal_data, selected_models_data, batch, model_master, stock, lot_id
                )
            except (json.JSONDecodeError, Exception) as e:
                logger.error(f"Error parsing selected models: {e}")
        
        # Add Model button logic with validation
        modal_data['add_model_enabled'] = modal_data['empty_hooks'] > 0
        
        
        # Save button logic: Enable only if empty_hooks == 0
        modal_data['can_save'] = (modal_data['empty_hooks'] == 0)

        # Modal validation rules
        modal_data['modal_validation'] = self._prepare_modal_validation(modal_data)

        # Tray Distribution and Half-Filled Tray Calculation
        modal_data['tray_distribution'] = self._calculate_tray_distribution(
            modal_data['effective_loaded_cases'], 
            modal_data['effective_jig_capacity'], 
            modal_data['half_filled_cases'],
            tray_capacity,
            batch,
            modal_data['original_lot_qty'],
            broken_hooks
        )

        # Update delink_table with calculated trays if no existing trays
        if not modal_data['delink_table']:
            delink_distribution = modal_data['tray_distribution']['current_lot']['distribution']
            modal_data['delink_table'] = delink_distribution['trays'] if delink_distribution else []

        # Adjust loaded_cases_qty to include broken hooks cases if any
        modal_data['loaded_cases_qty'] = modal_data['tray_distribution']['current_lot']['total_cases']

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

            # Calculate full trays and partial for jig_capacity (to fill delink up to capacity)
            tray_capacity = modal_data['tray_distribution']['current_lot']['tray_capacity']
            full_trays = modal_data['jig_capacity'] // tray_capacity
            partial_cases = modal_data['jig_capacity'] % tray_capacity

            # Build delink_table with full trays + partial tray (if any) to sum to jig_capacity
            delink_table = []
            for i in range(full_trays):
                delink_table.append({
                    'tray_id': '',
                    'tray_quantity': tray_capacity,
                    'model_bg': self._get_model_bg(i + 1),
                    'original_quantity': tray_capacity,
                    'excluded_quantity': 0,
                })
            if partial_cases > 0:
                delink_table.append({
                    'tray_id': '',
                    'tray_quantity': partial_cases,
                    'model_bg': self._get_model_bg(full_trays + 1),
                    'original_quantity': partial_cases,
                    'excluded_quantity': 0,
                })
            modal_data['delink_table'] = delink_table

            # Prepare half-filled tray for the leftover cases (excess)
            half_filled_distribution = self._distribute_half_filled_trays(leftover_cases, tray_capacity)
            modal_data['tray_distribution']['half_filled_lot'] = {
                'total_cases': leftover_cases,
                'distribution': half_filled_distribution,
                'total_trays': half_filled_distribution['total_trays'] if half_filled_distribution else 0
            }
            
            # Update Current Lot distribution to match jig capacity
            modal_data['tray_distribution']['current_lot'] = {
                'total_cases': modal_data['jig_capacity'],
                'effective_capacity': modal_data['jig_capacity'],
                'broken_hooks': 0,
                'tray_capacity': tray_capacity,
                'distribution': self._distribute_cases_to_trays(modal_data['jig_capacity'], tray_capacity),
                'total_trays': len(delink_table)
            }
            
            modal_data['open_with_half_filled'] = True

            # Set loaded_cases_qty to 0/jig_capacity for display (will update on scan)
            modal_data['loaded_cases_qty'] = f"0/{modal_data['jig_capacity']}"
            modal_data['excess_message'] = f"{leftover_cases} cases are in excess"
        else:
            modal_data['open_with_half_filled'] = False
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
                existing_trays = JigLoadTrayId.objects.filter(lot_id=lot_id, batch_id=batch).order_by('tray_id')
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
                existing_trays = JigLoadTrayId.objects.filter(lot_id=lot_id, batch_id=batch).order_by('tray_id')
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
            'empty_hooks_zero': (modal_data['empty_hooks'] == 0) or (modal_data['loaded_cases_qty'] >= modal_data['jig_capacity']),  # Allow excess cases
            'has_half_filled_cases': modal_data.get('half_filled_tray_cases', 0) > 0,
        }
        
        validation['overall_valid'] = all([
            validation['jig_capacity_valid'],
            validation['loaded_cases_valid'],
            validation['hooks_balance_valid'],
            validation['broken_hooks_valid'],
            validation['nickel_bath_valid'],
            validation['empty_hooks_zero'],
        ])
        
        if not validation['empty_hooks_zero']:
            validation['empty_hooks_error'] = (
                "Loaded Cases Qty must equal Jig Capacity. Use 'Add Model' to fill empty hooks with relevant tray allocation."
        )
        
        return validation
    
    def _calculate_tray_distribution(self, delink_cases, effective_jig_capacity, half_filled_cases, tray_capacity, batch, original_lot_qty, broken_hooks):
        """
        Calculate tray distribution for delink and half-filled cases.
        """
        print(f"🧮 TRAY DISTRIBUTION CALCULATION:")
        print(f"Effective Jig Capacity: {effective_jig_capacity}")
        print(f"Delink Cases: {delink_cases}")
        print(f"Half-Filled Cases: {half_filled_cases}")
        print(f"Tray Capacity: {tray_capacity}")
        
        # Distribute delink cases
        delink_distribution = self._distribute_cases_to_trays(delink_cases, tray_capacity)
        
        # Calculate delink scanning cases (only full trays)
        delink_scanning_cases = 0
        if delink_distribution:
            delink_scanning_cases = delink_distribution.get('full_trays_count', 0) * tray_capacity
        
        if delink_distribution:
            print(f"Delink Distribution: {len(delink_distribution.get('trays', []))} trays")
            print(f"Delink Scanning Cases (Full Trays Only): {delink_scanning_cases}")
            for idx, tray in enumerate(delink_distribution.get('trays', [])):
                print(f"  Tray {tray['tray_number']}: {tray['cases']} cases")
        else:
            print("Delink Distribution: 0 trays")
        
        # Distribute half-filled cases
        half_filled_distribution = None
        if half_filled_cases > 0:
            half_filled_distribution = self._distribute_half_filled_trays(half_filled_cases, tray_capacity)
            if half_filled_distribution:
                print(f"Half-Filled Distribution: {len(half_filled_distribution.get('trays', []))} trays")
                for idx, tray in enumerate(half_filled_distribution.get('trays', [])):
                    print(f"  Half-Filled Tray {tray['tray_number']}: {tray['cases']} cases ({'Scan' if tray.get('scan_required') else 'Auto'})")
            else:
                print("Half-Filled Distribution: 0 trays")
        
        return {
            'current_lot': {
                'total_cases': delink_cases,
                'delink_scanning_cases': delink_scanning_cases,  # Cases for delink scanning (full trays only)
                'effective_capacity': effective_jig_capacity,
                'tray_capacity': tray_capacity,
                'distribution': delink_distribution,
                'total_trays': delink_distribution.get('total_trays', 0) if delink_distribution else 0
            },
            'half_filled_lot': {
                'total_cases': half_filled_cases,
                'distribution': half_filled_distribution,
                'total_trays': half_filled_distribution.get('total_trays', 0) if half_filled_distribution else 0
            },
            'accountability_info': self._generate_accountability_info(
                original_lot_qty, delink_cases, half_filled_cases, broken_hooks
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
        For excess lots: put partial tray first (Scan Required), then full trays (Auto Assigned).
        Example for 22 cases (capacity 12): Tray 1 (10 cases, Scan), Tray 2 (12 cases, Auto).
        """
        if half_filled_cases <= 0 or not tray_capacity:
            return None
            
        full_trays = half_filled_cases // tray_capacity
        remainder_cases = half_filled_cases % tray_capacity
        
        trays = []
        tray_number = 1
        
        # Add partial tray FIRST (requires scanning, top tray for half-filled section)
        if remainder_cases > 0:
            trays.append({
                'tray_number': tray_number,
                'cases': remainder_cases,
                'is_full': False,
                'scan_required': True,
                'tray_type': 'partial',
                'placeholder': f'Scan Tray ID ({remainder_cases} pcs)'
            })
            tray_number += 1
            
        # Add full trays (auto-assignment from existing trays)
        for i in range(full_trays):
            trays.append({
                'tray_number': tray_number,
                'cases': tray_capacity,
                'is_full': True,
                'scan_required': False,
                'tray_type': 'full',
                'info': 'Auto Assigned'
            })
            tray_number += 1
        
        return {
            'total_cases': half_filled_cases,
            'full_trays_count': full_trays,
            'partial_tray_cases': remainder_cases,
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
    
    def _handle_multi_model_selection(self, modal_data, selected_models_data, primary_batch, primary_model_master, primary_stock, primary_lot_id):
        """Handle multi-model selection and recalculate modal data"""
        import json
        
        # Initialize multi-model structure
        modal_data['is_multi_model'] = True
        modal_data['model_tabs'] = []
        modal_data['delink_table'] = []
        
        # Add primary model tab
        primary_images = self._prepare_model_images(primary_model_master)
        modal_data['model_tabs'].append({
            'batch_id': primary_batch.batch_id if primary_batch else '',
            'lot_id': primary_lot_id,
            'quantity': modal_data['effective_loaded_cases'],
            'is_primary': True,
            'model_images': primary_images,
            'bg_class': 'bg-1',
        })
        
        # Add additional models
        total_loaded_cases = modal_data['effective_loaded_cases']
        remaining_capacity = modal_data['effective_jig_capacity'] - total_loaded_cases
        
        for idx, selected_model in enumerate(selected_models_data, start=1):
            if remaining_capacity <= 0:
                break
                
            try:
                add_stock = TotalStockModel.objects.filter(
                    lot_id=selected_model['lot_id']
                ).first()
                
                if add_stock:
                    add_batch = add_stock.batch_id
                    add_model_master = add_batch.model_stock_no if add_batch else add_stock.model_stock_no
                    
                    # Cap quantity to remaining capacity
                    original_qty = add_stock.total_stock or 0
                    capped_qty = min(
                        original_qty, 
                        remaining_capacity, 
                        selected_model.get('selected_qty', original_qty)
                    )
                    
                    if capped_qty > 0:
                        # Add model tab
                        add_images = self._prepare_model_images(add_model_master)
                        modal_data['model_tabs'].append({
                            'batch_id': selected_model['batch_id'],
                            'lot_id': selected_model['lot_id'],
                            'quantity': capped_qty,
                            'is_primary': False,
                            'model_images': add_images,
                            'bg_class': f'bg-{idx + 1}',
                        })
                        
                        # Update loaded cases
                        total_loaded_cases += capped_qty
                        remaining_capacity -= capped_qty
                        
                        # Generate delink table for additional model
                        if primary_batch and primary_batch.tray_type:
                            tray_capacity = None
                            from modelmasterapp.models import TrayType
                            tray_type_obj = TrayType.objects.filter(
                                tray_type=primary_batch.tray_type
                            ).first()
                            if tray_type_obj:
                                tray_capacity = tray_type_obj.tray_capacity
                            
                            if tray_capacity:
                                add_delink_table = self._generate_delink_table_for_model_helper(
                                    selected_model, capped_qty, tray_capacity, idx
                                )
                                modal_data['delink_table'].extend(add_delink_table)
                        
            except Exception as e:
                # Skip invalid models
                continue
        
        # Update modal calculations
        modal_data['loaded_cases_qty'] = total_loaded_cases
        modal_data['empty_hooks'] = max(0, modal_data['effective_jig_capacity'] - total_loaded_cases)
        modal_data['add_model_enabled'] = modal_data['empty_hooks'] > 0
        modal_data['can_save'] = (modal_data['empty_hooks'] == 0)
        
        return modal_data
    
    def _generate_delink_table_for_model_helper(self, model_data, quantity, tray_capacity, model_index):
        """Generate delink table entries for additional models"""
        if not tray_capacity or tray_capacity <= 0:
            return []
        
        delink_table = []
        
        # Calculate number of full and partial trays
        full_trays = quantity // tray_capacity
        partial_qty = quantity % tray_capacity
        
        tray_counter = 1
        
        # Add full trays
        for i in range(full_trays):
            delink_table.append({
                'tray_label': f'Tray {tray_counter}',
                'tray_qty': tray_capacity,
                'lot_id': model_data['lot_id'],
                'batch_id': model_data['batch_id'],
                'model_index': model_index,
                'bg_class': f'model-bg-{model_index + 1}',
                'is_delink': True,
            })
            tray_counter += 1
        
        # Add partial tray if exists
        if partial_qty > 0:
            delink_table.append({
                'tray_label': f'Tray {tray_counter}',
                'tray_qty': partial_qty,
                'lot_id': model_data['lot_id'],
                'batch_id': model_data['batch_id'],
                'model_index': model_index,
                'bg_class': f'model-bg-{model_index + 1}',
                'is_delink': True,
            })
        
        return delink_table
    
    
    
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

        # Get stock to calculate original_lot_qty
        try:
            stock = TotalStockModel.objects.get(batch_id__batch_id=batch_id, lot_id=lot_id)
            original_lot_qty = stock.total_stock
        except TotalStockModel.DoesNotExist:
            logger.error(f"❌ No TotalStockModel for lot_id={lot_id}, batch_id={batch_id}")
            return Response({'error': 'Stock record not found'}, status=status.HTTP_404_NOT_FOUND)

        # Get jig capacity
        jig_capacity = 0
        jig_id = draft_data.get('jig_id')
        if jig_id:
            try:
                jig = Jig.objects.get(jig_qr_id=jig_id)
                # Get jig capacity from JigLoadingMaster via batch
                batch = ModelMasterCreation.objects.get(batch_id=batch_id)
                jig_master = JigLoadingMaster.objects.filter(model_stock_no=batch.model_stock_no).first()
                if jig_master:
                    jig_capacity = jig_master.jig_capacity
            except (Jig.DoesNotExist, ModelMasterCreation.DoesNotExist):
                pass

        # Calculate updated_lot_qty
        broken_hooks = int(draft_data.get('broken_buildup_hooks', 0))
        updated_lot_qty = original_lot_qty - broken_hooks if broken_hooks > 0 else original_lot_qty

        # Separate trays into delink, partial, half_filled
        trays = draft_data.get('trays', [])
        delink_tray_info = []
        partial_tray_info = []
        half_filled_tray_info = []

        for tray in trays:
            row_index = tray.get('row_index', '')
            tray_id = tray.get('tray_id', '')
            tray_qty = int(tray.get('tray_qty', 0))
            if row_index == 'half-filled' or str(row_index).startswith('half_'):
                half_filled_tray_info.append({'tray_id': tray_id, 'cases': tray_qty})
            else:
                delink_tray_info.append({'tray_id': tray_id, 'cases': tray_qty})

        # Update draft_data with calculated fields
        draft_data.update({
            'original_lot_qty': original_lot_qty,
            'updated_lot_qty': updated_lot_qty,
            'delink_tray_info': delink_tray_info,
            'partial_tray_info': partial_tray_info,
            'half_filled_tray_info': half_filled_tray_info,
            'tray_distribution': {'delink': delink_tray_info, 'partial': partial_tray_info, 'half_filled': half_filled_tray_info},
            'broken_hooks': broken_hooks,
            'jig_capacity': jig_capacity,
        })

        # Calculate totals
        delink_tray_qty = updated_lot_qty
        delink_tray_count = len(delink_tray_info)
        half_filled_tray_qty = sum(t['cases'] for t in half_filled_tray_info)
        loaded_cases_qty = 0  # No trays scanned yet

        obj, created = JigLoadingManualDraft.objects.update_or_create(
            batch_id=batch_id,
            lot_id=lot_id,
            user=user,
            defaults={
                'draft_data': draft_data,
                'original_lot_qty': original_lot_qty,
                'updated_lot_qty': updated_lot_qty,
                'jig_id': jig_id,
                'delink_tray_info': delink_tray_info,
                'delink_tray_qty': delink_tray_qty,
                'delink_tray_count': delink_tray_count,
                'half_filled_tray_info': half_filled_tray_info,
                'half_filled_tray_qty': half_filled_tray_qty,
                'jig_capacity': jig_capacity,
                'broken_hooks': broken_hooks,
                'loaded_cases_qty': loaded_cases_qty,
            }
        )
        
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
        import logging
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
            # Convert any potential string numbers to integers early
            try:
                # Handle broken_buildup_hooks from request data - ensure it's an integer
                raw_broken_hooks = data.get('broken_buildup_hooks', 0)
                if isinstance(raw_broken_hooks, str):
                    raw_broken_hooks = int(raw_broken_hooks) if raw_broken_hooks.strip() else 0
                logger.info(f"📊 Raw broken hooks from request: {raw_broken_hooks} (type: {type(raw_broken_hooks)})")
                
                # Handle jig_capacity from request data
                raw_jig_capacity = data.get('jig_capacity', 0)
                if isinstance(raw_jig_capacity, str):
                    raw_jig_capacity = int(raw_jig_capacity) if raw_jig_capacity.strip() else 0
                logger.info(f"📊 Raw jig capacity from request: {raw_jig_capacity} (type: {type(raw_jig_capacity)})")
                
            except (ValueError, TypeError) as e:
                logger.error(f"❌ Type conversion error: {e}")
                return Response({'success': False, 'message': 'Invalid numeric data in request'}, status=status.HTTP_400_BAD_REQUEST)

            # Get related objects with specific error handling FIRST
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

            # Get draft data after we have stock object
            try:
                draft = JigLoadingManualDraft.objects.get(batch_id=batch_id, lot_id=lot_id, user=user)
                draft_data = draft.draft_data
                original_lot_qty = int(draft_data.get('original_lot_qty', stock.total_stock))
                updated_lot_qty = int(draft_data.get('updated_lot_qty', stock.total_stock))
                jig_capacity = int(draft_data.get('jig_capacity', 0))
                broken_hooks = int(draft_data.get('broken_hooks', 0))
                delink_tray_info = data.get('delink_tray_info', [])
                partial_tray_info = draft_data.get('partial_tray_info', [])
                half_filled_tray_info = data.get('half_filled_tray_info', [])
            except JigLoadingManualDraft.DoesNotExist:
                # Fallback to old logic using pre-converted values
                original_lot_qty = stock.total_stock
                updated_lot_qty = stock.total_stock
                jig_capacity = raw_jig_capacity
                broken_hooks = raw_broken_hooks
                delink_tray_info = data.get('delink_tray_info', [])
                partial_tray_info = []
                half_filled_tray_info = data.get('half_filled_tray_info', [])
                draft = None
            
            # Final type safety check
            original_lot_qty = int(original_lot_qty)
            updated_lot_qty = int(updated_lot_qty) 
            jig_capacity = int(jig_capacity)
            broken_hooks = int(broken_hooks)
            
            logger.info(f"📊 Final values: original_lot_qty={original_lot_qty}, jig_capacity={jig_capacity}, broken_hooks={broken_hooks}")
            logger.info(f"📊 Types: original_lot_qty={type(original_lot_qty)}, jig_capacity={type(jig_capacity)}, broken_hooks={type(broken_hooks)}")

            # Get tray capacity from batch tray type
            tray_capacity = None
            if batch and batch.tray_type:
                tray_type_obj = TrayType.objects.filter(tray_type=batch.tray_type).first()
                if tray_type_obj:
                    tray_capacity = int(tray_type_obj.tray_capacity)

            # STRICT: If tray_capacity is not found, raise error
            if not tray_capacity:
                return Response({'success': False, 'message': f"Tray capacity not configured for tray type '{getattr(batch, 'tray_type', None)}'. Please configure in admin."}, status=status.HTTP_400_BAD_REQUEST)
            
            logger.info(f"📊 Tray capacity: {tray_capacity} (type: {type(tray_capacity)})")

            # Implement new logic: Compare original_lot_qty and jig_capacity
            if original_lot_qty == jig_capacity:
                # No splitting, submit normally
                logger.info(f"✅ No splitting: original_lot_qty ({original_lot_qty}) == jig_capacity ({jig_capacity})")
                
                # Calculate delink_tray_info and half_filled_tray_info from existing scanned trays
                if broken_hooks > 0:
                    existing_trays = list(JigLoadTrayId.objects.filter(lot_id=lot_id, batch_id=batch).order_by('id'))
                    delink_tray_info = []
                    half_filled_tray_info = []
                    if existing_trays:
                        effective_qty = original_lot_qty - broken_hooks
                        total_effective = 0
                        for tray in existing_trays:
                            if total_effective >= effective_qty:
                                # Remaining cases in this tray go to half-filled
                                half_filled_cases = tray.tray_quantity
                                half_filled_tray_info.append({'tray_id': tray.tray_id, 'cases': half_filled_cases})
                                tray.broken_hooks_excluded_qty = half_filled_cases
                                tray.effective_tray_qty = 0
                                tray.broken_hooks_effective_tray = False
                                tray.save()
                            else:
                                remaining = effective_qty - total_effective
                                effective_for_this = min(remaining, tray.tray_quantity)
                                delink_tray_info.append({'tray_id': tray.tray_id, 'cases': effective_for_this})
                                excluded_for_this = tray.tray_quantity - effective_for_this
                                if excluded_for_this > 0:
                                    half_filled_tray_info.append({'tray_id': tray.tray_id, 'cases': excluded_for_this})
                                tray.effective_tray_qty = effective_for_this
                                tray.broken_hooks_excluded_qty = excluded_for_this
                                tray.broken_hooks_effective_tray = True
                                tray.save()
                                total_effective += effective_for_this
                    logger.info(f"🔍 Tray distribution: Complete={len(delink_tray_info)} trays ({sum(t['cases'] for t in delink_tray_info)} cases), Pick={len(half_filled_tray_info)} trays ({sum(t['cases'] for t in half_filled_tray_info)} cases)")
                else:
                    # No broken hooks, all existing trays are delink
                    existing_trays = JigLoadTrayId.objects.filter(lot_id=lot_id, batch_id=batch)
                    delink_tray_info = [{'tray_id': t.tray_id, 'cases': t.tray_quantity} for t in existing_trays]
                    half_filled_tray_info = []
                
                # Trays are already created during scanning, no need to create again
                
            else:
                # Splitting required - keep existing logic but remove JigDetails
                logger.info(f"🔀 Splitting: original_lot_qty ({original_lot_qty}) > jig_capacity ({jig_capacity})")
                
                # Apply broken hooks logic to the complete table portion
                complete_table_cases = jig_capacity
                complete_delink_tray_info = []
                complete_half_filled_tray_info = []
                
                if broken_hooks > 0:
                    # Calculate distribution for complete table cases using existing trays
                    effective_trays = self._calculate_broken_hooks_tray_distribution(lot_id, jig_capacity - broken_hooks, broken_hooks, batch)
                    complete_delink_tray_info = [{'tray_id': t['tray_id'], 'cases': t['effective_qty']} for t in effective_trays if t['effective_qty'] > 0]
                    complete_half_filled_tray_info = [{'tray_id': t['tray_id'], 'cases': t['excluded_qty']} for t in effective_trays if t['excluded_qty'] > 0]
                else:
                    # No broken hooks, distribute complete table cases normally
                    existing_trays = JigLoadTrayId.objects.filter(lot_id=lot_id, batch_id=batch).order_by('id')
                    # Take enough trays for jig_capacity
                    total_cases = 0
                    for tray in existing_trays:
                        if total_cases >= jig_capacity:
                            break
                        cases_to_take = min(tray.tray_quantity, jig_capacity - total_cases)
                        complete_delink_tray_info.append({'tray_id': tray.tray_id, 'cases': cases_to_take})
                        total_cases += cases_to_take
                    complete_half_filled_tray_info = []
                
                # Create new lot for partial (remaining cases after jig_capacity)
                leftover_qty = original_lot_qty - jig_capacity
                new_lot_id = f"LID{timezone.now().strftime('%d%m%Y%H%M%S')}{leftover_qty:04d}"
                new_stock = TotalStockModel.objects.create(
                    batch_id=stock.batch_id,
                    model_stock_no=stock.model_stock_no,
                    version=stock.version,
                    total_stock=leftover_qty,  # Set to actual leftover qty
                    polish_finish=stock.polish_finish,
                    plating_color=stock.plating_color,
                    lot_id=new_lot_id,
                    parent_lot_id=lot_id,
                    created_at=timezone.now(),
                    Jig_Load_completed=False,
                    jig_draft=True,  # Ensures it stays in pick table as Partial Draft
                    brass_audit_accptance=True,
                    brass_audit_last_process_date_time=timezone.now(),
                    last_process_date_time=timezone.now(),
                    last_process_module="Jig Loading",
                )
                # Create JigLoadTrayId for remaining trays in new lot
                remaining_trays = JigLoadTrayId.objects.filter(lot_id=lot_id, batch_id=batch).order_by('id')
                total_assigned = sum(t['cases'] for t in complete_delink_tray_info)
                for tray in remaining_trays:
                    if total_assigned >= jig_capacity:
                        # Move remaining to new lot
                        JigLoadTrayId.objects.create(
                            lot_id=new_lot_id,
                            tray_id=tray.tray_id,
                            tray_quantity=tray.tray_quantity,
                            batch_id=batch,
                            user=user,
                            date=timezone.now()
                        )
                
                # Create JigLoadTrayId for delink_tray_info
                for tray in complete_delink_tray_info:
                    jig_tray, created = JigLoadTrayId.objects.get_or_create(
                        lot_id=lot_id,
                        tray_id=tray['tray_id'],
                        batch_id=batch,
                        defaults={
                            'tray_quantity': tray['cases'],
                            'user': user,
                            'date': timezone.now()
                        }
                    )
                    if not created:
                        jig_tray.tray_quantity = tray['cases']
                        jig_tray.save()
                
                # Create JigLoadTrayId for half_filled_tray_info (pick table)
                for tray in complete_half_filled_tray_info:
                    jig_tray, created = JigLoadTrayId.objects.get_or_create(
                        lot_id=lot_id,
                        tray_id=tray['tray_id'],
                        batch_id=batch,
                        defaults={
                            'tray_quantity': tray['cases'],
                            'user': user,
                            'broken_hooks_effective_tray': True,
                            'date': timezone.now()
                        }
                    )
                    if not created:
                        jig_tray.tray_quantity = tray['cases']
                        jig_tray.broken_hooks_effective_tray = True
                        jig_tray.save()

            # Update Jig
            jig.is_loaded = True
            jig.batch_id = batch_id
            jig.lot_id = lot_id
            jig.current_user = None
            jig.locked_at = None
            jig.drafted = False
            jig.save()

            # Update original stock
            stock.Jig_Load_completed = True
            stock.jig_draft = False
            stock.save()

            # Mark draft as submitted
            if draft:
                draft.draft_status = 'submitted'
                draft.save()

            # Create JigCompleted record - This is what the user expects to see in "Complete table"
            try:
                # Prepare the complete data for JigCompleted
                complete_draft_data = draft_data if draft else {
                    'batch_id': batch_id,
                    'lot_id': lot_id,
                    'jig_id': jig_qr_id,
                    'broken_buildup_hooks': broken_hooks,
                    'original_lot_qty': original_lot_qty,
                    'updated_lot_qty': updated_lot_qty if original_lot_qty == jig_capacity else jig_capacity,
                    'jig_capacity': jig_capacity,
                }
                
                # For equal capacity scenario (98==98), use delink_tray_info and half_filled_tray_info
                if original_lot_qty == jig_capacity:
                    effective_lot_qty = original_lot_qty - broken_hooks
                    complete_delink_tray_info = delink_tray_info
                    complete_half_filled_tray_info = half_filled_tray_info
                else:
                    # For splitting scenario, use the complete table portion 
                    effective_lot_qty = jig_capacity - broken_hooks
                    leftover_qty = original_lot_qty - jig_capacity
                    
                    # Calculate delink tray info for jig capacity portion
                    complete_delink_tray_info = []
                    existing_trays = JigLoadTrayId.objects.filter(lot_id=lot_id, batch_id=batch).order_by('id')
                    
                    remaining_for_jig = jig_capacity
                    for tray in existing_trays:
                        if remaining_for_jig <= 0:
                            break
                        cases_for_jig = min(remaining_for_jig, tray.tray_quantity)
                        complete_delink_tray_info.append({
                            'tray_id': tray.tray_id,
                            'cases': cases_for_jig
                        })
                        remaining_for_jig -= cases_for_jig
                    
                    # Calculate half-filled tray info for leftover cases
                    if leftover_qty > 0:
                        complete_half_filled_tray_info = []
                        remaining_cases = leftover_qty
                        
                        # Find trays that have leftover cases after jig loading
                        for tray in existing_trays:
                            if remaining_cases <= 0:
                                break
                            
                            # Check how many cases from this tray were used in delink
                            used_in_delink = next((t['cases'] for t in complete_delink_tray_info if t['tray_id'] == tray.tray_id), 0)
                            remaining_in_tray = tray.tray_quantity - used_in_delink
                            
                            if remaining_in_tray > 0:
                                cases_for_half_filled = min(remaining_cases, remaining_in_tray)
                                complete_half_filled_tray_info.append({
                                    'tray_id': tray.tray_id, 
                                    'cases': cases_for_half_filled
                                })
                                remaining_cases -= cases_for_half_filled

                # Create JigCompleted entry
                # Preserve hold status from existing record
                existing_hold = JigCompleted.objects.filter(lot_id=lot_id, batch_id=batch_id, user=user).first()
                hold_status = existing_hold.hold_status if existing_hold else False
                hold_reason = existing_hold.hold_reason if existing_hold else ''
                unhold_reason = existing_hold.unhold_reason if existing_hold else ''
                
                JigCompleted.objects.create(
                    batch_id=batch_id,
                    lot_id=lot_id,
                    user=user,
                    draft_data=complete_draft_data,
                    original_lot_qty=original_lot_qty,
                    updated_lot_qty=jig_capacity if original_lot_qty > jig_capacity else effective_lot_qty,
                    jig_id=jig_qr_id,
                    delink_tray_info=complete_delink_tray_info,
                    delink_tray_qty=sum(t['cases'] for t in complete_delink_tray_info),
                    delink_tray_count=len(complete_delink_tray_info),
                    half_filled_tray_info=complete_half_filled_tray_info,
                    half_filled_tray_qty=original_lot_qty - jig_capacity if original_lot_qty > jig_capacity else 0,
                    half_filled_lot_qty=original_lot_qty - jig_capacity if original_lot_qty > jig_capacity else 0,
                    jig_capacity=jig_capacity,
                    broken_hooks=broken_hooks,
                    loaded_cases_qty=jig_capacity if original_lot_qty > jig_capacity else effective_lot_qty,
                    draft_status='submitted',
                    hold_status=hold_status,
                    hold_reason=hold_reason,
                    unhold_reason=unhold_reason
                )
                logger.info(f"✅ JigCompleted record created for lot_id={lot_id} with effective_qty={effective_lot_qty}")
            except Exception as e:
                logger.error(f"❌ Failed to create JigCompleted record: {e}")

            logger.info(f"🎉 SUBMIT COMPLETED SUCCESSFULLY for batch_id={batch_id}, lot_id={lot_id}")
            return Response({'success': True, 'message': 'Jig submitted successfully'}, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"💥 Error submitting jig: {e}")
            return Response({'success': False, 'message': f'Internal server error: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        



# In validate_lock_jig_id, move capacity check before is_loaded check to show capacity mismatch for all jigs
@api_view(['POST'])
def validate_lock_jig_id(request):
    logger = logging.getLogger(__name__)
    try:
        # Check authentication first
        if not request.user.is_authenticated:
            return JsonResponse({'valid': False, 'message': 'User not authenticated'}, status=401)
        
        logger.info(f"🚀 API CALLED - validate_lock_jig_id by user: {request.user.username}")
        
        jig_id = request.data.get('jig_id', '').strip()
        batch_id = request.data.get('batch_id', '').strip()
        lot_id = request.data.get('lot_id', '').strip()
        user = request.user
        
        logger.info(f"📊 Request data: jig_id={jig_id}, batch_id={batch_id}, user={user.username}")

        # Basic validation - check if jig_id is provided
        if not jig_id or len(jig_id) > 9:
            return JsonResponse({'valid': False, 'message': 'Invalid Jig ID format'}, status=200)

        # Check if jig_id exists in database
        try:
            jig = Jig.objects.get(jig_qr_id=jig_id)
        except Jig.DoesNotExist:
            return JsonResponse({'valid': False, 'message': 'Invalid Jig ID format'}, status=200)

        # Get expected jig capacity for this batch/lot
        expected_capacity = None
        try:
            stock = TotalStockModel.objects.get(batch_id__batch_id=batch_id, lot_id=lot_id)
            batch = stock.batch_id
            model_master = batch.model_stock_no if batch else stock.model_stock_no
            if model_master:
                jig_master = JigLoadingMaster.objects.filter(model_stock_no=model_master).first()
                if jig_master:
                    expected_capacity = jig_master.jig_capacity
        except (TotalStockModel.DoesNotExist, AttributeError) as e:
            logger.warning(f"⚠️ Could not determine expected capacity: {e}")

        # Check if jig ID prefix matches expected capacity (if available) - do this for all existing jigs
        if expected_capacity is not None:
            match = re.match(r'J(\d+)-', jig_id)
            if match:
                jig_prefix_capacity = int(match.group(1))
                if jig_prefix_capacity != expected_capacity:
                    return JsonResponse({'valid': False, 'message': f'Jig ID capacity ({jig_prefix_capacity}) does not match expected ({expected_capacity})'}, status=200)

        # Check if jig is already submitted (loaded)
        if jig.is_loaded:
            return JsonResponse({'valid': False, 'message': 'Jig ID has been submitted and cannot be reused'}, status=200)

        # FIRST: Check for existing drafted jigs for current batch
        drafted_jig_current_batch = Jig.objects.filter(
            jig_qr_id=jig_id, drafted=True, batch_id=batch_id
        ).first()

        logger.info(f"🔍 Drafted jig current batch query result: {drafted_jig_current_batch}")

        if drafted_jig_current_batch:
            if drafted_jig_current_batch.current_user == user:
                return JsonResponse({'valid': True, 'message': 'Jig ID is drafted by you for this batch.'}, status=200)
            else:
                return JsonResponse({'valid': False, 'message': 'Jig ID is drafted by another user for this batch.'}, status=200)

        # If not drafted for this batch, check if drafted for any other batch
        drafted_jig_other_batch = Jig.objects.filter(
            jig_qr_id=jig_id, drafted=True
        ).exclude(batch_id=batch_id).first()

        logger.info(f"🔍 Drafted jig other batch query result: {drafted_jig_other_batch}")

        if drafted_jig_other_batch:
            return JsonResponse({'valid': False, 'message': 'Jig ID is drafted for another batch.'}, status=200)

        # If not drafted/locked and capacity matches, show available message
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
    
    # Check JigCompleted for completed jig loading
    jig_completed = JigCompleted.objects.filter(lot_id=stock_lot_id).first()
    if jig_completed and jig_completed.delink_tray_info:
        formatted_trays = []
        for tray in jig_completed.delink_tray_info:
            formatted_tray = {
                'tray_id': tray.get('tray_id', ''),
                'tray_quantity': tray.get('cases', ''),
                'row_index': '',
                'tray_status': "Delinked",
                'original_quantity': tray.get('cases', ''),
                'excluded_quantity': 0,
            }
            formatted_trays.append(formatted_tray)
        # Sort trays by tray_id
        formatted_trays.sort(key=lambda x: x['tray_id'])
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
        return JsonResponse({'success': True, 'trays': []})

@api_view(['POST'])
def update_jig_draft_status(request, batch_id, lot_id):
    try:
        from modelmasterapp.models import TotalStockModel
        stock = TotalStockModel.objects.get(batch_id__batch_id=batch_id, lot_id=lot_id)
        stock.jig_draft = True
        stock.save()
        return JsonResponse({'success': True})
    except TotalStockModel.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Stock not found'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# Add Model Pick Table View - Filters compatible lots for multi-model selection
class AddModelPickTableView(APIView):
    """Filter compatible lots for Add Model functionality"""
    renderer_classes = [TemplateHTMLRenderer]
    template_name = "JigLoading/Jig_Picktable.html"
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        user = request.user
        import re
        from django.db.models import Q
        
        # Get filtering parameters from current jig
        current_lot_id = request.GET.get('current_lot_id')
        current_batch_id = request.GET.get('current_batch_id')
        filter_tray_type = request.GET.get('tray_type')
        filter_jig_type = request.GET.get('jig_type')
        filter_plating_color = request.GET.get('plating_color')
        filter_polish_finish = request.GET.get('polish_finish')
        remaining_capacity = int(request.GET.get('remaining_capacity', 0))
        
        # Base query for compatible lots - exclude completed and incompatible statuses
        total_stock_qs = TotalStockModel.objects.filter(
            brass_audit_accptance=True, 
            Jig_Load_completed=False
        ).exclude(
            lot_id=current_lot_id  # Exclude current lot
        ).exclude(
            jig_draft=True  # Exclude "Partial Draft" lots
        )
        
        # Apply compatibility filters - REMOVED for now to show all lots
        
        # Order by newest first
        total_stock_qs = total_stock_qs.order_by('-brass_audit_last_process_date_time')
        
        # Pagination
        page_number = request.GET.get('page', 1)
        paginator = Paginator(total_stock_qs, 10)
        page_obj = paginator.get_page(page_number)

        master_data = []
        for stock in page_obj.object_list:
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

            # Determine lot status based on draft/completion status
            if JigLoadingManualDraft.objects.filter(batch_id=stock.batch_id, lot_id=stock.lot_id).exists():
                lot_status = 'Draft'
            elif stock.Jig_Load_completed:
                completed = JigCompleted.objects.filter(batch_id=stock.batch_id, lot_id=stock.lot_id).first()
                if completed and completed.draft_status == 'partial':
                    lot_status = 'Partial Draft'
                else:
                    lot_status = 'Submitted'
            else:
                lot_status = 'Yet to Start'
            lot_status_class = 'lot-status-yet'
            
            # Cap display_qty to remaining capacity
            capped_qty = min(lot_qty, remaining_capacity)
            
            # Determine if checkbox should be enabled
            checkbox_enabled = capped_qty > 0 and remaining_capacity > 0

            master_data.append({
                'batch_id': stock.batch_id.batch_id if stock.batch_id else '',
                'stock_lot_id': stock.lot_id,
                'model_stock_no__model_no': stock.model_stock_no.model_no if stock.model_stock_no else '',
                'plating_stk_no': plating_stk_no,
                'polishing_stk_no': polishing_stk_no,
                'plating_color': stock.plating_color.plating_color if stock.plating_color else '',
                'polish_finish': stock.polish_finish.polish_finish if stock.polish_finish else '',
                'version__version_internal': stock.version.version_internal if stock.version else '',
                'no_of_trays': no_of_trays,
                'display_qty': capped_qty,  # Capped quantity
                'original_qty': lot_qty,    # Original quantity
                'jig_capacity': jig_capacity if jig_capacity else '',
                'jig_type': jig_type,
                'model_images': [img.master_image.url for img in stock.model_stock_no.images.all()] if stock.model_stock_no else [],
                'brass_audit_last_process_date_time': stock.brass_audit_last_process_date_time,
                'last_process_module': stock.last_process_module,
                'lot_status': lot_status,
                'lot_status_class': lot_status_class,
                'checkbox_enabled': checkbox_enabled,
                'hold_status': False,
                'hold_reason': '',
                'unhold_reason': '',
            })
        
        # Add filter information for UI display
        filter_info = {
            'is_filtered': True,
            'current_lot_id': current_lot_id,
            'current_batch_id': current_batch_id,
            'tray_type': filter_tray_type,
            'jig_type': filter_jig_type,
            'plating_color': filter_plating_color,
            'polish_finish': filter_polish_finish,
            'remaining_capacity': remaining_capacity,
            'compatible_count': len(master_data)
        }
        
        context = {
            'master_data': master_data,
            'page_obj': page_obj,
            'paginator': paginator,
            'user': user,
            'filter_info': filter_info,
            'add_model_mode': True,
            'csp_nonce': getattr(request, 'csp_nonce', ''),
        }
        return Response(context, template_name=self.template_name)


# Add Selected Models API View - Processes multi-model selection
class AddSelectedModelsAPIView(APIView):
    """Process selected models and return updated modal data"""
    permission_classes = [IsAuthenticated]
    
    def post(self, request, *args, **kwargs):
        try:
            # Get current jig data
            current_batch_id = request.data.get('current_batch_id')
            current_lot_id = request.data.get('current_lot_id')
            selected_lots = request.data.get('selected_lots', [])
            jig_qr_id = request.data.get('jig_qr_id')
            broken_hooks = int(request.data.get('broken_hooks', 0))
            
            if not selected_lots:
                return Response({
                    'success': False,
                    'error': 'No models selected'
                }, status=400)
            
            # Prepare multi-model data structure
            multi_model_data = {
                'primary_model': {
                    'batch_id': current_batch_id,
                    'lot_id': current_lot_id,
                },
                'additional_models': selected_lots,
                'jig_qr_id': jig_qr_id,
                'broken_hooks': broken_hooks
            }
            
            # Get updated modal data with multi-model calculations
            modal_data = self._calculate_multi_model_data(multi_model_data)
            
            return Response({
                'success': True,
                'modal_data': modal_data
            })
            
        except Exception as e:
            return Response({
                'success': False,
                'error': str(e)
            }, status=500)
    
    def _calculate_multi_model_data(self, multi_model_data):
        """Calculate comprehensive modal data for multi-model jig loading"""
        from modelmasterapp.models import TotalStockModel
        
        primary_batch_id = multi_model_data['primary_model']['batch_id']
        primary_lot_id = multi_model_data['primary_model']['lot_id']
        additional_models = multi_model_data['additional_models']
        jig_qr_id = multi_model_data['jig_qr_id']
        broken_hooks = multi_model_data['broken_hooks']
        
        # Get primary model data
        primary_stock = get_object_or_404(TotalStockModel, lot_id=primary_lot_id)
        primary_batch = primary_stock.batch_id
        primary_model_master = primary_batch.model_stock_no if primary_batch else primary_stock.model_stock_no
        
        # Get jig capacity and other details from primary model
        jig_capacity = 0
        jig_type = None
        tray_capacity = 0
        
        plating_stk_no = (
            getattr(primary_batch, 'plating_stk_no', None)
            or getattr(primary_model_master, 'plating_stk_no', None)
        )
        
        if plating_stk_no:
            jig_master = JigLoadingMaster.objects.filter(
                model_stock_no__plating_stk_no=plating_stk_no
            ).first()
            if jig_master:
                jig_capacity = jig_master.jig_capacity
                jig_type = f"{jig_capacity}-Jig"
        
        # Get tray capacity
        if primary_batch and primary_batch.tray_type:
            from modelmasterapp.models import TrayType
            tray_type_obj = TrayType.objects.filter(tray_type=primary_batch.tray_type).first()
            if tray_type_obj:
                tray_capacity = tray_type_obj.tray_capacity
        
        # Calculate effective jig capacity (after broken hooks)
        effective_jig_capacity = max(0, jig_capacity - broken_hooks)
        
        # Collect all models with their quantities
        all_models = []
        total_loaded_cases = 0
        
        # Add primary model
        primary_qty = min(primary_stock.total_stock or 0, effective_jig_capacity)
        all_models.append({
            'batch_id': primary_batch_id,
            'lot_id': primary_lot_id,
            'stock': primary_stock,
            'batch': primary_batch,
            'model_master': primary_model_master,
            'quantity': primary_qty,
            'is_primary': True,
        })
        total_loaded_cases += primary_qty
        
        # Add additional models (cap quantities to remaining capacity)
        remaining_capacity = effective_jig_capacity - total_loaded_cases
        
        for additional in additional_models:
            if remaining_capacity <= 0:
                break
                
            add_stock = TotalStockModel.objects.filter(
                lot_id=additional['lot_id']
            ).first()
            
            if add_stock:
                add_batch = add_stock.batch_id
                add_model_master = add_batch.model_stock_no if add_batch else add_stock.model_stock_no
                
                # Cap quantity to remaining capacity
                original_qty = add_stock.total_stock or 0
                capped_qty = min(original_qty, remaining_capacity, additional.get('selected_qty', original_qty))
                
                all_models.append({
                    'batch_id': additional['batch_id'],
                    'lot_id': additional['lot_id'],
                    'stock': add_stock,
                    'batch': add_batch,
                    'model_master': add_model_master,
                    'quantity': capped_qty,
                    'is_primary': False,
                })
                
                total_loaded_cases += capped_qty
                remaining_capacity -= capped_qty
        
        # Calculate delink tables for all models
        delink_tables = []
        model_tabs = []
        
        for idx, model in enumerate(all_models):
            # Generate delink table for this model
            model_delink_table = self._generate_delink_table_for_model(
                model, tray_capacity, idx
            )
            
            delink_tables.extend(model_delink_table)
            
            # Create model tab data
            model_images = []
            if model['model_master']:
                model_images = [
                    img.master_image.url for img in model['model_master'].images.all()
                ]
            
            model_tabs.append({
                'batch_id': model['batch_id'],
                'lot_id': model['lot_id'],
                'quantity': model['quantity'],
                'is_primary': model['is_primary'],
                'model_images': model_images,
                'bg_class': f'bg-{idx + 1}',
            })
        
        # Calculate empty hooks
        empty_hooks = max(0, effective_jig_capacity - total_loaded_cases)
        
        # Prepare comprehensive modal data
        modal_data = {
            'jig_capacity': jig_capacity,
            'jig_type': jig_type,
            'effective_jig_capacity': effective_jig_capacity,
            'broken_buildup_hooks': broken_hooks,
            'loaded_cases_qty': total_loaded_cases,
            'empty_hooks': empty_hooks,
            'nickel_bath_type': 'Bright',  # Default
            'tray_type': primary_batch.tray_type if primary_batch else 'Normal',
            'no_of_cycle': 1,
            'add_model_enabled': empty_hooks > 0,
            'can_save': empty_hooks == 0,
            'delink_table': delink_tables,
            'model_tabs': model_tabs,
            'is_multi_model': len(all_models) > 1,
            'modal_validation': {
                'max_broken_hooks': 10 if jig_capacity >= 144 else 5,
                'empty_hooks_zero': empty_hooks == 0,
            },
            'tray_distribution': {
                'current_lot': {
                    'total_cases': total_loaded_cases,
                    'delink_trays': len([t for t in delink_tables if t.get('is_delink', False)]),
                },
                'half_filled': {
                    'total_cases': 0,
                    'trays': [],
                }
            }
        }
        
        return modal_data
    
    def _generate_delink_table_for_model(self, model, tray_capacity, model_index):
        """Generate delink table entries for a specific model"""
        if not tray_capacity or tray_capacity <= 0:
            return []
        
        quantity = model['quantity']
        delink_table = []
        
        # Calculate number of full and partial trays
        full_trays = quantity // tray_capacity
        partial_qty = quantity % tray_capacity
        
        tray_counter = 1
        
        # Add full trays
        for i in range(full_trays):
            delink_table.append({
                'tray_label': f'Tray {tray_counter}',
                'tray_qty': tray_capacity,
                'lot_id': model['lot_id'],
                'batch_id': model['batch_id'],
                'model_index': model_index,
                'bg_class': f'model-bg-{model_index + 1}',
                'is_delink': True,
            })
            tray_counter += 1
        
        # Add partial tray if exists
        if partial_qty > 0:
            delink_table.append({
                'tray_label': f'Tray {tray_counter}',
                'tray_qty': partial_qty,
                'lot_id': model['lot_id'],
                'batch_id': model['batch_id'],
                'model_index': model_index,
                'bg_class': f'model-bg-{model_index + 1}',
                'is_delink': True,
            })
        
        return delink_table


                   
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

            # Fetch JigCompleted for this lot
            jig_completed = JigCompleted.objects.filter(lot_id=stock.lot_id).order_by('-updated_at').first()

            # Use JigCompleted.updated_lot_qty as the effective lot quantity
            lot_qty = jig_completed.updated_lot_qty if jig_completed else (stock.total_stock or 0)

            # --- FIX: Use delink_tray_info from JigCompleted ---
            tray_info = []
            if jig_completed and getattr(jig_completed, 'delink_tray_info', None):
                tray_info = sorted(jig_completed.delink_tray_info, key=lambda x: x.get('tray_id', ''))
                no_of_trays = len(tray_info)
            else:
                # Fallback to calculation
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
                    jig_completed.updated_at
                    if jig_completed
                    else ''
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
                'total_cases_loaded': jig_completed.loaded_cases_qty if jig_completed else '',
                'jig_type': jig_type,
                'jig_capacity': jig_capacity,
                'jig_qr_id': jig_completed.jig_id if jig_completed else '',
                'jig_loaded_date_time': jig_completed.updated_at if jig_completed else '',
                'model_images': [img.master_image.url for img in stock.model_stock_no.images.all()] if stock.model_stock_no else [],
                'audio_remark': '',
                'IP_jig_pick_remarks': '',
            })
        context['jig_details'] = completed_data
        return context


class JigSaveHoldUnholdReasonAPIView(APIView):
    permission_classes = [IsAuthenticated]
    """
    POST with:
    {
        "remark": "Reason text",
        "action": "hold"  # or "unhold"
    }
    """
    def post(self, request):
        try:
            data = request.data if hasattr(request, 'data') else json.loads(request.body.decode('utf-8'))
            lot_id = data.get('lot_id')
            batch_id = data.get('batch_id')
            user = request.user

            remark = data.get('remark', '').strip()
            action = data.get('action', '').strip().lower()

            if not lot_id or not batch_id or not remark or action not in ['hold', 'unhold']:
                return JsonResponse({'success': False, 'error': 'Missing or invalid parameters.'}, status=400)

            obj = JigCompleted.objects.filter(lot_id=lot_id, batch_id=batch_id, user=user).first()
            if not obj:
                # Create a new JigCompleted record for draft hold/unhold
                obj = JigCompleted.objects.create(
                    lot_id=lot_id,
                    batch_id=batch_id,
                    user=user,
                    draft_status='active'
                )

            if action == 'hold':
                obj.hold_reason = remark
                obj.hold_status = True
                obj.unhold_reason = ''
            elif action == 'unhold':
                obj.unhold_reason = remark
                obj.hold_status = False

            obj.save(update_fields=['hold_reason', 'unhold_reason', 'hold_status'])
            return JsonResponse({'success': True, 'message': 'Reason saved.'})

        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)


# =====================================================================
# NEW JIG DRAFT API ENDPOINTS - NO HARDCODING, PERFECT ACCOUNTABILITY
# =====================================================================

class JigDraftCreateAPIView(APIView):
    """Create new JigDraft with perfect accountability"""
    permission_classes = [IsAuthenticated]
    
    def post(self, request, *args, **kwargs):
        try:
            # Get required parameters
            jig_qr_id = request.data.get('jig_qr_id')
            lot_id = request.data.get('lot_id')
            batch_id = request.data.get('batch_id')
            broken_hooks = int(request.data.get('broken_hooks', 0))
            
            if not all([jig_qr_id, lot_id, batch_id]):
                return Response({
                    'success': False, 
                    'error': 'Missing required parameters: jig_qr_id, lot_id, batch_id'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Deactivate any existing active drafts for this jig+lot
            JigDraft.objects.filter(
                jig_qr_id=jig_qr_id,
                lot_id=lot_id,
                is_active=True
            ).update(is_active=False)
            
            # Get all master data dynamically
            jig_capacity, tray_type, tray_capacity, original_qty = JigDraft.get_master_data(
                batch_id, lot_id, jig_qr_id
            )
            
            # Adjust jig capacity for broken hooks
            effective_jig_capacity = max(0, jig_capacity - broken_hooks)
            
            # Calculate accountable quantities
            jig_delinked_qty = min(original_qty, effective_jig_capacity)
            jig_excess_qty = original_qty - jig_delinked_qty
            
            # Get original tray info from JigLoadTrayId
            tray_objects = JigLoadTrayId.objects.filter(
                lot_id=lot_id,
                batch_id__batch_id=batch_id
            ).order_by('id')
            
            if not tray_objects.exists():
                return Response({
                    'success': False,
                    'error': f'No trays found for lot: {lot_id}, batch: {batch_id}'
                }, status=status.HTTP_404_NOT_FOUND)
            
            # Build original tray info (immutable source)
            original_tray_info = []
            for tray in tray_objects:
                original_tray_info.append({
                    "tray_id": tray.tray_id,
                    "qty": tray.tray_quantity or 0,
                    "is_top_tray": tray.top_tray
                })
            
            # Initialize empty delink and half-filled arrays (will be populated during scanning)
            delink_tray_info = []
            half_filled_tray_info = []
            
            # Create the draft
            draft = JigDraft.objects.create(
                jig_qr_id=jig_qr_id,
                lot_id=lot_id,
                batch_id=batch_id,
                created_by=request.user,
                jig_capacity=jig_capacity,
                tray_type=tray_type,
                tray_capacity=tray_capacity,
                jig_original_qty=original_qty,
                jig_delinked_qty=jig_delinked_qty,
                jig_excess_qty=jig_excess_qty,
                original_tray_info=original_tray_info,
                delink_tray_info=delink_tray_info,
                half_filled_tray_info=half_filled_tray_info
            )
            
            return Response({
                'success': True,
                'message': 'JigDraft created successfully',
                'data': {
                    'draft_id': draft.id,
                    'jig_capacity': jig_capacity,
                    'effective_jig_capacity': effective_jig_capacity,
                    'tray_type': tray_type,
                    'tray_capacity': tray_capacity,
                    'jig_original_qty': original_qty,
                    'jig_delinked_qty': jig_delinked_qty,
                    'jig_excess_qty': jig_excess_qty,
                    'original_tray_count': len(original_tray_info),
                    'reconciliation_ok': draft.reconciliation_ok
                }
            }, status=status.HTTP_201_CREATED)
            
        except ValueError as e:
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({
                'success': False,
                'error': f'Failed to create draft: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class JigDraftUpdateAPIView(APIView):
    """Update JigDraft with scanned tray data"""
    permission_classes = [IsAuthenticated]
    
    def post(self, request, *args, **kwargs):
        try:
            draft_id = request.data.get('draft_id')
            scanned_trays = request.data.get('scanned_trays', [])
            
            if not draft_id:
                return Response({
                    'success': False,
                    'error': 'draft_id is required'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Get the active draft
            draft = JigDraft.objects.get(id=draft_id, is_active=True)
            
            # Process scanned trays and distribute to delink/half-filled
            delink_tray_info, half_filled_tray_info = self._distribute_scanned_trays(
                draft, scanned_trays
            )
            
            # Update draft with new tray distributions
            draft.delink_tray_info = delink_tray_info
            draft.half_filled_tray_info = half_filled_tray_info
            draft.save()  # This will trigger reconciliation in save method
            
            return Response({
                'success': True,
                'message': 'Draft updated successfully',
                'data': {
                    'delink_tray_count': len(delink_tray_info),
                    'half_filled_tray_count': len(half_filled_tray_info),
                    'delink_qty': sum(t.get('qty', 0) for t in delink_tray_info),
                    'half_filled_qty': sum(t.get('qty', 0) for t in half_filled_tray_info),
                    'reconciliation_ok': draft.reconciliation_ok,
                    'remarks': draft.remarks
                }
            }, status=status.HTTP_200_OK)
            
        except JigDraft.DoesNotExist:
            return Response({
                'success': False,
                'error': 'Draft not found or inactive'
            }, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({
                'success': False,
                'error': f'Failed to update draft: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def _distribute_scanned_trays(self, draft, scanned_trays):
        """
        Distribute scanned trays into delink and half-filled buckets
        Based on jig capacity and original tray order
        """
        delink_tray_info = []
        half_filled_tray_info = []
        
        remaining_jig_capacity = draft.jig_delinked_qty
        
        # Sort original trays by top_tray first, then by order
        original_trays = sorted(draft.original_tray_info, 
                               key=lambda x: (not x.get('is_top_tray', False), draft.original_tray_info.index(x)))
        
        # Get set of scanned tray IDs
        scanned_tray_ids = {t['tray_id'] for t in scanned_trays}
        
        # Process scanned trays in original order
        for tray in original_trays:
            tray_id = tray['tray_id']
            original_qty = tray['qty']
            
            if tray_id in scanned_tray_ids:
                # Tray was scanned - consume in order
                if remaining_jig_capacity > 0:
                    consumed_qty = min(original_qty, remaining_jig_capacity)
                    
                    delink_tray_info.append({
                        'tray_id': tray_id,
                        'qty': consumed_qty,
                        'is_partial': consumed_qty < original_qty
                    })
                    remaining_jig_capacity -= consumed_qty
                    
                    # Remaining qty from this tray goes to half-filled
                    remaining_qty = original_qty - consumed_qty
                    if remaining_qty > 0:
                        half_filled_tray_info.append({
                            'tray_id': tray_id,
                            'qty': remaining_qty,
                            'is_partial': True
                        })
                else:
                    # Jig capacity reached, all goes to half-filled
                    half_filled_tray_info.append({
                        'tray_id': tray_id,
                        'qty': original_qty,
                        'is_partial': False
                    })
            else:
                # Tray not scanned, all goes to half-filled
                half_filled_tray_info.append({
                    'tray_id': tray_id,
                    'qty': original_qty,
                    'is_unscanned': True
                })
        
        return delink_tray_info, half_filled_tray_info


class JigDraftRetrieveAPIView(APIView):
    """Retrieve JigDraft data"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request, *args, **kwargs):
        try:
            draft_id = request.GET.get('draft_id')
            jig_qr_id = request.GET.get('jig_qr_id')
            lot_id = request.GET.get('lot_id')
            
            # Find draft by ID or jig+lot combination
            if draft_id:
                draft = JigDraft.objects.get(id=draft_id, is_active=True)
            elif jig_qr_id and lot_id:
                draft = JigDraft.objects.filter(
                    jig_qr_id=jig_qr_id,
                    lot_id=lot_id,
                    is_active=True
                ).first()
                if not draft:
                    return Response({
                        'success': False,
                        'error': 'No active draft found for this jig and lot'
                    }, status=status.HTTP_404_NOT_FOUND)
            else:
                return Response({
                    'success': False,
                    'error': 'Either draft_id or (jig_qr_id + lot_id) is required'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            return Response({
                'success': True,
                'data': {
                    'draft_id': draft.id,
                    'jig_qr_id': draft.jig_qr_id,
                    'lot_id': draft.lot_id,
                    'batch_id': draft.batch_id,
                    'jig_capacity': draft.jig_capacity,
                    'tray_type': draft.tray_type,
                    'tray_capacity': draft.tray_capacity,
                    'jig_original_qty': draft.jig_original_qty,
                    'jig_delinked_qty': draft.jig_delinked_qty,
                    'jig_excess_qty': draft.jig_excess_qty,
                    'original_tray_info': draft.original_tray_info,
                    'delink_tray_info': draft.delink_tray_info,
                    'half_filled_tray_info': draft.half_filled_tray_info,
                    'reconciliation_ok': draft.reconciliation_ok,
                    'remarks': draft.remarks,
                    'created_at': draft.created_at,
                    'updated_at': draft.updated_at
                }
            }, status=status.HTTP_200_OK)
            
        except JigDraft.DoesNotExist:
            return Response({
                'success': False,
                'error': 'Draft not found'
            }, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({
                'success': False,
                'error': f'Failed to retrieve draft: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class JigDraftValidateAPIView(APIView):
    """Validate JigDraft reconciliation"""
    permission_classes = [IsAuthenticated]
    
    def post(self, request, *args, **kwargs):
        try:
            draft_id = request.data.get('draft_id')
            
            if not draft_id:
                return Response({
                    'success': False,
                    'error': 'draft_id is required'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            draft = JigDraft.objects.get(id=draft_id, is_active=True)
            
            # Force reconciliation
            is_valid, error_msg = draft.reconcile_draft()
            
            # Update reconciliation status
            draft.reconciliation_ok = is_valid
            draft.remarks = error_msg if not is_valid else ""
            draft.save()
            
            return Response({
                'success': True,
                'data': {
                    'is_valid': is_valid,
                    'error_message': error_msg,
                    'reconciliation_details': {
                        'original_qty': draft.jig_original_qty,
                        'delinked_qty': sum(t.get('qty', 0) for t in draft.delink_tray_info),
                        'half_filled_qty': sum(t.get('qty', 0) for t in draft.half_filled_tray_info),
                        'total_accounted': sum(t.get('qty', 0) for t in draft.delink_tray_info) + 
                                         sum(t.get('qty', 0) for t in draft.half_filled_tray_info)
                    }
                }
            }, status=status.HTTP_200_OK)
            
        except JigDraft.DoesNotExist:
            return Response({
                'success': False,
                'error': 'Draft not found'
            }, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({
                'success': False,
                'error': f'Failed to validate draft: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)