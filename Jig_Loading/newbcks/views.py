from django.views.generic import *
from modelmasterapp.models import *
from .models import Jig, JigLoadingMaster, JigLoadTrayId, JigDetails, JigCompleted
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


def calculate_jig_qty_distribution(original_lot_qty, jig_capacity, trays):
    """
    Calculate tray distribution for jig loading with excess handling.
    
    Args:
        original_lot_qty (int): Original lot quantity
        jig_capacity (int): Jig capacity
        trays (list): List of tray dicts with 'tray_id' and 'tray_qty'
    
    Returns:
        dict: Distribution with complete_trays, pick_trays, half_filled_tray
    """
    logger = logging.getLogger(__name__)
    
    logger.info(f"🔢 Starting distribution calculation:")
    logger.info(f"   Original Lot Qty: {original_lot_qty}")
    logger.info(f"   Jig Capacity: {jig_capacity}")
    logger.info(f"   Total Trays: {len(trays)}")
    
    # Determine if there's excess
    if original_lot_qty <= jig_capacity:
        # No excess - all goes to complete
        logger.info("✅ No excess - all trays go to complete table")
        complete_qty = original_lot_qty
        pick_qty = 0
        complete_trays = []
        pick_trays = []
        half_filled_tray = None
        
        # Allocate all trays to complete
        remaining_qty = complete_qty
        for tray in trays:
            if remaining_qty <= 0:
                break
            allocated_qty = min(remaining_qty, tray['original_qty'])
            complete_trays.append({
                'tray_id': tray['tray_id'],
                'allocated_qty': allocated_qty,
                'original_qty': tray['original_qty']
            })
            remaining_qty -= allocated_qty
            logger.info(f"   Complete Tray {tray['tray_id']}: {allocated_qty}/{tray['original_qty']}")
        
    else:
        # Excess exists - split between complete and pick
        excess_qty = original_lot_qty - jig_capacity
        complete_qty = jig_capacity
        pick_qty = excess_qty
        
        logger.info(f"⚖️ Excess detected: {excess_qty}")
        logger.info(f"   Complete Qty: {complete_qty}, Pick Qty: {pick_qty}")
        
        complete_trays = []
        pick_trays = []
        half_filled_tray = None
        
        # First, allocate to complete table up to jig_capacity
        remaining_complete = complete_qty
        for tray in trays:
            if remaining_complete <= 0:
                break
            allocated_qty = min(remaining_complete, tray['original_qty'])
            complete_trays.append({
                'tray_id': tray['tray_id'],
                'allocated_qty': allocated_qty,
                'original_qty': tray['original_qty']
            })
            remaining_complete -= allocated_qty
            logger.info(f"   Complete Tray {tray['tray_id']}: {allocated_qty}/{tray['original_qty']}")
        
        # Remaining trays go to pick table
        remaining_trays = trays[len(complete_trays):]
        if complete_trays and complete_trays[-1]['allocated_qty'] < complete_trays[-1]['original_qty']:
            # The last complete tray has remaining, include it in pick
            last_tray = complete_trays[-1].copy()
            last_tray['tray_qty'] = last_tray['original_qty'] - last_tray['allocated_qty']  # Remaining qty
            remaining_trays = [last_tray] + remaining_trays
        
        remaining_pick = pick_qty
        
        for tray in remaining_trays:
            if remaining_pick <= 0:
                break
            tray_qty_for_alloc = tray.get('tray_qty', tray['original_qty'])
            allocated_qty = min(remaining_pick, tray_qty_for_alloc)
            if allocated_qty < tray['original_qty']:
                # Half-filled tray
                half_filled_tray = {
                    'tray_id': tray['tray_id'],
                    'allocated_qty': allocated_qty,
                    'original_qty': tray['original_qty']
                }
                logger.info(f"   Half-filled Tray {tray['tray_id']}: {allocated_qty}/{tray['original_qty']}")
            else:
                # Full tray for pick
                pick_trays.append({
                    'tray_id': tray['tray_id'],
                    'allocated_qty': allocated_qty,
                    'original_qty': tray['original_qty']
                })
                logger.info(f"   Pick Tray {tray['tray_id']}: {allocated_qty}/{tray['original_qty']}")
            remaining_pick -= allocated_qty
    
    result = {
        'complete_qty': complete_qty,
        'pick_qty': pick_qty,
        'complete_trays': complete_trays,
        'pick_trays': pick_trays,
        'half_filled_tray': half_filled_tray,
        'has_excess': pick_qty > 0
    }
    
    logger.info(f"📊 Final Distribution:")
    logger.info(f"   Complete: {complete_qty} qty, {len(complete_trays)} trays")
    logger.info(f"   Pick: {pick_qty} qty, {len(pick_trays)} trays + {'1 half-filled' if half_filled_tray else '0 half-filled'}")
    
    return result


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
# JIG ADD MODAL DATA VIEW - Provides modal data for "Add Jig" modal
# =====================================================================

class JigAddModalDataView(TemplateView):
    """
    Provide comprehensive modal data preparation for "Add Jig" functionality.
    Returns JSON with all necessary data for modal initialization and validation.
    """
    def get(self, request, *args, **kwargs):
        logger = logging.getLogger(__name__)
        
        try:
            batch_id = request.GET.get('batch_id')
            lot_id = request.GET.get('lot_id')
            jig_qr_id = request.GET.get('jig_qr_id', '')
            
            logger.info(f"📋 JigAddModal: batch_id={batch_id}, lot_id={lot_id}, jig_qr_id={jig_qr_id}")
            
            # Fetch TotalStockModel for batch/lot
            stock = get_object_or_404(TotalStockModel, lot_id=lot_id)
            batch = stock.batch_id
            model_master = batch.model_stock_no if batch else None
            
            # Resolve plating stock number
            plating_stk_no = ''
            if batch and batch.plating_stk_no:
                plating_stk_no = batch.plating_stk_no
            elif batch and batch.model_stock_no and batch.model_stock_no.plating_stk_no:
                plating_stk_no = batch.model_stock_no.plating_stk_no
            
            # Get jig details if exists
            jig_details = JigDetails.objects.filter(jig_qr_id=jig_qr_id, lot_id=lot_id).first()
            
            # Fetch Jig Type and Capacity from JigLoadingMaster
            jig_type = None
            jig_capacity = 0
            if model_master:
                jig_master = JigLoadingMaster.objects.filter(model_stock_no=model_master).first()
                if jig_master:
                    jig_type = jig_master.jig_type
                    jig_capacity = jig_master.jig_capacity
            
            # Get tray type from batch
            tray_type = batch.tray_type if batch else 'Normal'
            
            # Calculate modal state
            if jig_details:
                nickel_bath_type = jig_details.ep_bath_type
                broken_buildup_hooks = jig_details.faulty_slots
                loaded_cases_qty = jig_details.total_cases_loaded
            else:
                nickel_bath_type = "Bright"
                broken_buildup_hooks = 0
                loaded_cases_qty = stock.total_stock if stock else 0
            
            empty_hooks = max(0, jig_capacity - loaded_cases_qty)
            
            # Get original lot quantity
            original_lot_qty = stock.total_stock or 0
            
            # Prepare trays list for distribution
            trays = []
            tray_ids = JigLoadTrayId.objects.filter(lot_id=lot_id, batch_id=batch).order_by('date')
            for tray in tray_ids:
                trays.append({
                    'tray_id': tray.tray_id,
                    'tray_qty': tray.tray_quantity or 0
                })
            
            # Calculate distribution
            distribution = calculate_jig_qty_distribution(original_lot_qty, jig_capacity, trays)
            
            # Prepare delink table from complete trays only
            delink_table = []
            for idx, tray in enumerate(distribution['complete_trays']):
                delink_table.append({
                    's_no': idx + 1,
                    'tray_id': tray['tray_id'],
                    'tray_qty': tray['allocated_qty'],
                    'is_existing': True
                })
            
            # Set lot_qty to original
            lot_qty = original_lot_qty
            
            # Prepare modal validation
            modal_validation = {
                'jig_capacity_valid': jig_capacity > 0,
                'loaded_cases_valid': loaded_cases_qty > 0,
                'broken_hooks_valid': broken_buildup_hooks >= 0,
                'nickel_bath_valid': nickel_bath_type in ['Bright', 'Satin', 'Matt'],
                'empty_hooks_zero': empty_hooks == 0,
                'max_broken_hooks': 10 if jig_capacity >= 144 else 5,
            }
            
            return JsonResponse({
                'form_title': f"Jig Loading / Plating Stock No: {plating_stk_no or 'N/A'}",
                'jig_id': jig_qr_id,
                'lot_qty': lot_qty,
                'nickel_bath_type': nickel_bath_type,
                'tray_type': tray_type,
                'broken_buildup_hooks': broken_buildup_hooks,
                'empty_hooks': empty_hooks,
                'loaded_cases_qty': loaded_cases_qty,
                'jig_capacity': jig_capacity,
                'jig_type': jig_type,
                'loaded_hooks': loaded_cases_qty,
                'add_model_enabled': empty_hooks > 0,
                'can_save': empty_hooks == 0,
                'model_images': [],
                'delink_table': delink_table,
                'no_of_cycle': 1,
                'plating_stk_no': plating_stk_no,
                'modal_validation': modal_validation,
                'has_excess': distribution['has_excess'],
                'half_filled_tray': distribution['half_filled_tray'],
                'pick_qty': distribution['pick_qty'],
                'complete_qty': distribution['complete_qty'],
            }, status=200)
            
        except Exception as e:
            logger.error(f"❌ Error in JigAddModalDataView: {str(e)}", exc_info=True)
            return JsonResponse({
                'error': f'Failed to load modal data: {str(e)}'
            }, status=500)


@api_view(['POST'])
def submit_jig_with_qty_split(request):
    """
    Submit jig loading with quantity split between complete and pick tables.
    Handles excess quantity by storing partial draft in JigCompleted.
    """
    logger = logging.getLogger(__name__)
    
    try:
        data = request.data
        batch_id = data.get('batch_id')
        lot_id = data.get('lot_id')
        jig_qr_id = data.get('jig_qr_id')
        delink_tray_ids = data.get('delink_tray_ids', [])
        half_filled_qty = data.get('half_filled_qty', 0)
        
        logger.info(f"🚀 Submit Jig with Split: batch_id={batch_id}, lot_id={lot_id}, jig_qr_id={jig_qr_id}")
        logger.info(f"   Delink Trays: {delink_tray_ids}")
        logger.info(f"   Half-filled Qty: {half_filled_qty}")
        
        if not all([batch_id, lot_id, jig_qr_id]):
            return Response({'error': 'Missing required fields'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Get stock and calculate distribution
        stock = get_object_or_404(TotalStockModel, lot_id=lot_id)
        original_lot_qty = stock.total_stock or 0
        
        # Get jig capacity
        jig_capacity = 0
        if stock.model_stock_no:
            jig_master = JigLoadingMaster.objects.filter(model_stock_no=stock.model_stock_no).first()
            if jig_master:
                jig_capacity = jig_master.jig_capacity
        
        # Get all trays
        trays = []
        tray_objs = JigLoadTrayId.objects.filter(lot_id=lot_id, batch_id=stock.batch_id).order_by('date')
        for tray in tray_objs:
            trays.append({
                'tray_id': tray.tray_id,
                'original_qty': tray.tray_quantity or 0
            })
        
        # Calculate distribution
        distribution = calculate_jig_qty_distribution(original_lot_qty, jig_capacity, trays)
        
        # Validate delink trays match complete trays
        expected_delink = [t['tray_id'] for t in distribution['complete_trays']]
        if set(delink_tray_ids) != set(expected_delink):
            logger.error(f"❌ Delink tray mismatch. Expected: {expected_delink}, Got: {delink_tray_ids}")
            return Response({'error': 'Delink trays do not match expected complete trays'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Validate half-filled qty if excess
        if distribution['has_excess'] and distribution['half_filled_tray']:
            expected_half_qty = distribution['half_filled_tray']['allocated_qty']
            if half_filled_qty != expected_half_qty:
                logger.error(f"❌ Half-filled qty mismatch. Expected: {expected_half_qty}, Got: {half_filled_qty}")
                return Response({'error': f'Half-filled quantity must be {expected_half_qty}'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Create JigCompleted for complete table
        jig_completed, created = JigCompleted.objects.get_or_create(
            lot_id=lot_id,
            batch_id=batch_id,
            defaults={
                'jig_qr_id': jig_qr_id,
                'updated_lot_qty': distribution['complete_qty'],
                'original_lot_qty': original_lot_qty,
                'delink_tray_info': distribution['complete_trays'],
                'partial_tray_info': distribution['pick_trays'] + ([distribution['half_filled_tray']] if distribution['half_filled_tray'] else []),
                'jig_load_status': 'completed' if not distribution['has_excess'] else 'partial_draft',
                'created_by': request.user if request.user.is_authenticated else None,
            }
        )
        
        if not created:
            jig_completed.updated_lot_qty = distribution['complete_qty']
            jig_completed.delink_tray_info = distribution['complete_trays']
            jig_completed.partial_tray_info = distribution['pick_trays'] + ([distribution['half_filled_tray']] if distribution['half_filled_tray'] else [])
            jig_completed.jig_load_status = 'completed' if not distribution['has_excess'] else 'partial_draft'
            jig_completed.save()
        
        # Update TotalStockModel
        stock.Jig_Load_completed = not distribution['has_excess']
        stock.jig_draft = distribution['has_excess']
        stock.save()
        
        logger.info(f"✅ Jig submitted successfully. Complete: {distribution['complete_qty']}, Pick: {distribution['pick_qty']}")
        
        return Response({
            'success': True,
            'message': 'Jig submitted with quantity split',
            'complete_qty': distribution['complete_qty'],
            'pick_qty': distribution['pick_qty'],
            'has_excess': distribution['has_excess']
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"❌ Error in submit_jig_with_qty_split: {str(e)}", exc_info=True)
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)