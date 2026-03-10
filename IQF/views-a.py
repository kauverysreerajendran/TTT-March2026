from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.renderers import TemplateHTMLRenderer
from django.shortcuts import render
from django.db.models import OuterRef, Subquery, Exists, F, Sum
from django.core.paginator import Paginator
from django.templatetags.static import static
import math
from Brass_QC.models import *
from BrassAudit.models import *
from InputScreening.models import *
from DayPlanning.models import *
from .models import *
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.contrib.auth.decorators import login_required
import traceback
from rest_framework import status
from django.http import JsonResponse
import json
from rest_framework.permissions import IsAuthenticated
from django.views.decorators.http import require_GET
from django.db.models import Q
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from math import ceil
from django.utils import timezone
from datetime import datetime, timedelta
import pytz
from django.db.models import Sum
from django.views.decorators.http import require_http_methods
from django.views import View
from django.db.models import Sum, F, Func, IntegerField
# Create your views here.

def generate_new_lot_id():
        from datetime import datetime
        timestamp = datetime.now().strftime("%d%m%Y%H%M%S")
        last_lot = TotalStockModel.objects.order_by('-id').first()
        if last_lot and last_lot.lot_id and last_lot.lot_id.startswith("LID"):
            last_seq_no = int(last_lot.lot_id[-4:])
            next_seq_no = last_seq_no + 1
        else:
            next_seq_no = 1
        seq_no = f"{next_seq_no:04d}"
        return f"LID{timestamp}{seq_no}"
    
@method_decorator(login_required, name='dispatch')    
class IQFPickTableView(APIView):
    renderer_classes = [TemplateHTMLRenderer]
    template_name = 'IQF/Iqf_PickTable.html'

    def get(self, request):
        user = request.user
        is_admin = user.groups.filter(name='Admin').exists() if user.is_authenticated else False

        lot_id = request.GET.get('lot_id')
        iqf_rejection_reasons = IQF_Rejection_Table.objects.all()

        # ✅ CHANGED: Query TotalStockModel directly instead of ModelMasterCreation
        brass_rejection_qty_subquery = Brass_QC_Rejection_ReasonStore.objects.filter(
            lot_id=OuterRef('lot_id')
        ).values('total_rejection_quantity')[:1]
        
        brass_audit_rejection_qty_subquery = Brass_Audit_Rejection_ReasonStore.objects.filter(
            lot_id=OuterRef('lot_id')
        ).values('total_rejection_quantity')[:1]

        iqf_rejection_qty_subquery = IQF_Rejection_ReasonStore.objects.filter(
            lot_id=OuterRef('lot_id')
        ).values('total_rejection_quantity')[:1]

        queryset = TotalStockModel.objects.select_related(
            'batch_id',
            'batch_id__model_stock_no',
            'batch_id__version',
            'batch_id__location'
        ).filter(
            batch_id__total_batch_quantity__gt=0
        ).annotate(
            wiping_required=F('batch_id__model_stock_no__wiping_required'),
            brass_rejection_total_qty=brass_rejection_qty_subquery,
            brass_audit_rejection_qty=brass_audit_rejection_qty_subquery,
            iqf_rejection_qty=iqf_rejection_qty_subquery,
        ).filter(
            # ✅ Direct filtering on TotalStockModel fields (no more subquery filtering)
            (
                Q(brass_qc_rejection=True) |
                Q(send_brass_audit_to_iqf=True) |
                Q(brass_qc_few_cases_accptance=True, brass_onhold_picking=False)
            )
        ).exclude(
            Q(iqf_acceptance=True) | 
            Q(iqf_rejection=True) | 
            Q(send_brass_audit_to_iqf=True, brass_audit_onhold_picking=True)|
            Q(iqf_few_cases_acceptance=True, iqf_onhold_picking=False)
        ).order_by('-bq_last_process_date_time', '-lot_id')

        print(f"📊 Found {queryset.count()} IQF pick records")
        print("All lot_ids in IQF pick queryset:", list(queryset.values_list('lot_id', flat=True)))

        # Pagination
        page_number = request.GET.get('page', 1)
        paginator = Paginator(queryset, 10)
        page_obj = paginator.get_page(page_number)

        # ✅ UPDATED: Build master_data from TotalStockModel records
        master_data = []
        for stock_obj in page_obj.object_list:
            batch = stock_obj.batch_id
            
            # ✅ CHECK FOR IQF-SPECIFIC DRAFTS ONLY WHERE is_draft = True
            iqf_has_drafts = (
                IQF_Draft_Store.objects.filter(lot_id=stock_obj.lot_id, draft_data__is_draft=True).exists() or
                IQF_Accepted_TrayID_Store.objects.filter(lot_id=stock_obj.lot_id, is_draft=True).exists()
            )
            
            data = {
                # ✅ Batch fields from foreign key
                'batch_id': batch.batch_id,
                'bq_last_process_date_time': stock_obj.bq_last_process_date_time,
                'model_stock_no__model_no': batch.model_stock_no.model_no,
                'plating_color': batch.plating_color,
                'polish_finish': batch.polish_finish,
                'version__version_name': batch.version.version_name if batch.version else '',
                'vendor_internal': batch.vendor_internal,
                'location__location_name': batch.location.location_name if batch.location else '',
                'tray_type': batch.tray_type,
                'tray_capacity': batch.tray_capacity,
                'Moved_to_D_Picker': batch.Moved_to_D_Picker,
                'Draft_Saved': iqf_has_drafts,  # ✅ USE IQF-SPECIFIC DRAFTS INSTEAD OF GLOBAL Draft_Saved
                'plating_stk_no': batch.plating_stk_no,
                'polishing_stk_no': batch.polishing_stk_no,
                'category': batch.category,
                
                # ✅ Stock-related fields from TotalStockModel
                'lot_id': stock_obj.lot_id,
                'stock_lot_id': stock_obj.lot_id,
                'last_process_module': stock_obj.last_process_module,
                'next_process_module': stock_obj.next_process_module,
                'wiping_required': stock_obj.wiping_required,
                'iqf_missing_qty': stock_obj.iqf_missing_qty,
                'iqf_physical_qty': stock_obj.iqf_physical_qty,
                'iqf_physical_qty_edited': stock_obj.iqf_physical_qty_edited,
                'accepted_tray_scan_status': stock_obj.accepted_tray_scan_status,
                'iqf_rejection_qty': stock_obj.iqf_rejection_qty,
                'iqf_accepted_qty': stock_obj.iqf_accepted_qty,
                'IQF_pick_remarks': stock_obj.IQF_pick_remarks,
                'brass_accepted_tray_scan_status': stock_obj.brass_accepted_tray_scan_status,
                'brass_qc_rejection': stock_obj.brass_qc_rejection,  # ✅ Direct access
                'brass_rejection_total_qty': stock_obj.brass_rejection_total_qty,
                'brass_audit_rejection_qty': stock_obj.brass_audit_rejection_qty,
                'brass_qc_few_cases_accptance': stock_obj.brass_qc_few_cases_accptance,
                'iqf_accepted_qty_verified': stock_obj.iqf_accepted_qty_verified,
                'iqf_acceptance': stock_obj.iqf_acceptance,
                'iqf_rejection': stock_obj.iqf_rejection,
                'brass_audit_few_cases_accptance': stock_obj.brass_audit_few_cases_accptance,
                'iqf_few_cases_acceptance': stock_obj.iqf_few_cases_acceptance,
                'iqf_onhold_picking': stock_obj.iqf_onhold_picking,
                'brass_onhold_picking': stock_obj.brass_onhold_picking,
                'iqf_hold_lot': stock_obj.iqf_hold_lot,
                'iqf_holding_reason': stock_obj.iqf_holding_reason,
                'iqf_release_lot': stock_obj.iqf_release_lot,
                'iqf_release_reason': stock_obj.iqf_release_reason,
                'brass_audit_onhold_picking': stock_obj.brass_audit_onhold_picking,
                'send_brass_audit_to_iqf': stock_obj.send_brass_audit_to_iqf,  # ✅ Direct access
                'total_IP_accpeted_quantity': stock_obj.total_IP_accpeted_quantity,
            }
            master_data.append(data)

        print(f"[IQFPickTableView] Total master_data records: {len(master_data)}")
        
        # ✅ Process the data (same logic as before)
        for data in master_data:
            print(data['batch_id'], data['brass_rejection_total_qty'])

        for data in master_data:
            brass_rejection_total_qty = data.get('brass_rejection_total_qty') or 0
            tray_capacity = data.get('tray_capacity') or 0
            brass_audit_rejection_qty = data.get('brass_audit_rejection_qty') or 0

            data['vendor_location'] = f"{data.get('vendor_internal', '')}_{data.get('location__location_name', '')}"
            
            # Use total_IP_accpeted_quantity if brass_rejection_total_qty is zero
            qty_for_trays = brass_rejection_total_qty if brass_rejection_total_qty > 0 else brass_audit_rejection_qty
            
            if tray_capacity and tray_capacity > 0:
                data['no_of_trays'] = math.ceil(qty_for_trays / tray_capacity)
            else:
                data['no_of_trays'] = 0

            # Get model images
            batch_obj = ModelMasterCreation.objects.filter(batch_id=data['batch_id']).first()
            images = []
            if batch_obj:
                model_master = batch_obj.model_stock_no
                for img in model_master.images.all():
                    if img.master_image:
                        images.append(img.master_image.url)
            if not images:
                images = [static('assets/images/imagePlaceholder.png')]
            data['model_images'] = images

            # Add available_qty for each row
            lot_id = data.get('stock_lot_id')
            total_stock_obj = TotalStockModel.objects.filter(lot_id=lot_id).first()
            if total_stock_obj:
                # ✅ HEALING LOGIC: If iqf_physical_qty is 0/None and no missing qty recorded, try to recover from Brass QC/Audit
                # This fixes the bug where "assigned quantity" is lost after draft save
                current_physical_qty = total_stock_obj.iqf_physical_qty or 0
                current_missing_qty = total_stock_obj.iqf_missing_qty or 0
                
                # Strict conditions: 
                # 1. Physical qty is 0 or None
                # 2. Missing qty is 0 or None (if user set missing, 0 physical is valid)
                # 3. Not yet finalized (not fully accepted or rejected)
                needs_healing = (
                    current_physical_qty <= 0 and 
                    current_missing_qty <= 0 and
                    not total_stock_obj.iqf_acceptance and 
                    not total_stock_obj.iqf_rejection
                )

                if needs_healing:
                    try:
                        # Determine source based on flag
                        use_audit = getattr(total_stock_obj, 'send_brass_audit_to_iqf', False)
                        
                        if use_audit:
                            reason_store = Brass_Audit_Rejection_ReasonStore.objects.filter(lot_id=lot_id).order_by('-id').first()
                        else:
                            reason_store = Brass_QC_Rejection_ReasonStore.objects.filter(lot_id=lot_id).order_by('-id').first()
                        
                        if reason_store and reason_store.total_rejection_quantity > 0:
                            qty_to_heal = reason_store.total_rejection_quantity
                            print(f"🚑 HEALING: Recovering iqf_physical_qty for {lot_id} -> {qty_to_heal}")
                            
                            # Persist the recovered quantity
                            total_stock_obj.iqf_physical_qty = qty_to_heal
                            total_stock_obj.iqf_missing_qty = 0
                            total_stock_obj.save(update_fields=['iqf_physical_qty', 'iqf_missing_qty'])
                            
                            # Update local object for immediate use in this view
                            total_stock_obj.iqf_physical_qty = qty_to_heal
                    except Exception as e:
                        print(f"⚠️ HEALING FAILED for {lot_id}: {str(e)}")

                # Standard assignment using (possibly healed) value
                if total_stock_obj.iqf_physical_qty and total_stock_obj.iqf_physical_qty > 0:
                    data['available_qty'] = total_stock_obj.iqf_physical_qty
                else:
                    data['available_qty'] = data.get('brass_rejection_total_qty', 0)
            else:
                data['available_qty'] = 0
            
            # Add display_physical_qty for frontend
            iqf_physical_qty = data.get('iqf_physical_qty', 0)
            if iqf_physical_qty and iqf_physical_qty > 0:
                data['display_physical_qty'] = iqf_physical_qty
            else:
                # Use same logic as display_lot_qty for fallback
                if data.get('send_brass_audit_to_iqf'):
                    data['display_physical_qty'] = data.get('brass_audit_rejection_qty') or 0
                else:
                    data['display_physical_qty'] = data.get('brass_rejection_total_qty') or 0
        print("Processed lot_ids:", [data['stock_lot_id'] for data in master_data])

        context = {
            'master_data': master_data,
            'page_obj': page_obj,
            'paginator': paginator,
            'user': user,
            'is_admin': is_admin,
            'iqf_rejection_reasons': iqf_rejection_reasons,
        }
        return Response(context, template_name=self.template_name)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def iqf_get_brass_rejection_quantities(request):
    lot_id = request.GET.get('lot_id')
    if not lot_id:
        return Response({'success': False, 'error': 'Missing lot_id'}, status=400)
    
    try:
        # Fetch rejection reasons from database
        rejection_reasons = []
        for reason in IQF_Rejection_Table.objects.all().order_by('id'):
            rejection_reasons.append({
                'id': reason.id,
                'rejection_reason_id': reason.rejection_reason_id,
                'rejection_reason': reason.rejection_reason
            })
        
        # Check if lot is send_brass_audit_to_iqf
        stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
        use_audit = getattr(stock, 'send_brass_audit_to_iqf', False)

        if use_audit:
            # Use Brass_Audit tables
            rejected_trays = Brass_Audit_Rejected_TrayScan.objects.filter(lot_id=lot_id)
            reason_store_model = Brass_Audit_Rejection_ReasonStore
        else:
            # Use Brass_QC tables
            rejected_trays = Brass_QC_Rejected_TrayScan.objects.filter(lot_id=lot_id)
            reason_store_model = Brass_QC_Rejection_ReasonStore

        rejection_qty_map = {}
        for tray in rejected_trays:
            reason = tray.rejection_reason.rejection_reason.strip()
            qty = int(tray.rejected_tray_quantity) if tray.rejected_tray_quantity else 0
            if reason in rejection_qty_map:
                rejection_qty_map[reason] += qty
            else:
                rejection_qty_map[reason] = qty

        # If no tray-wise reasons, show lot_rejected_comment from reason_store_model
        lot_rejected_comment = ""
        total_rejection_quantity = 0
        reason_store = reason_store_model.objects.filter(lot_id=lot_id).order_by('-id').first()
        if reason_store:
            total_rejection_quantity = reason_store.total_rejection_quantity or 0
            if not rejection_qty_map:
                lot_rejected_comment = reason_store.lot_rejected_comment or ""

        return Response({
            'success': True,
            'rejection_reasons': rejection_reasons,
            'brass_rejection_qty_map': rejection_qty_map,
            'lot_rejected_comment': lot_rejected_comment,
            'total_rejection_quantity': total_rejection_quantity
        })
    except Exception as e:
        print(f"❌ ERROR in iqf_get_brass_rejection_quantities: {str(e)}")
        return Response({'success': False, 'error': str(e)}, status=500)
    

@method_decorator(login_required, name='dispatch')
@method_decorator(csrf_exempt, name='dispatch')
class IQFSaveHoldUnholdReasonAPIView(APIView):
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
            print("DEBUG: Received lot_id:", lot_id)  # <-- Add this line

            remark = data.get('remark', '').strip()
            action = data.get('action', '').strip().lower()

            if not lot_id or not remark or action not in ['hold', 'unhold']:
                return JsonResponse({'success': False, 'error': 'Missing or invalid parameters.'}, status=400)

            obj = TotalStockModel.objects.filter(lot_id=lot_id).first()
            if not obj:
                return JsonResponse({'success': False, 'error': 'LOT not found.'}, status=404)

            if action == 'hold':
                obj.iqf_holding_reason = remark
                obj.iqf_hold_lot = True
                obj.iqf_release_reason = ''
                obj.iqf_release_lot = False
            elif action == 'unhold':
                obj.iqf_release_reason = remark
                obj.iqf_hold_lot = False
                obj.iqf_release_lot = True

            obj.save(update_fields=['iqf_holding_reason', 'iqf_release_reason', 'iqf_hold_lot', 'iqf_release_lot'])
            return JsonResponse({'success': True, 'message': 'Reason saved.'})

        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
        
@method_decorator(csrf_exempt, name='dispatch')  
@method_decorator(login_required, name='dispatch')  
class IQFSaveIPCheckboxView(APIView):
    def post(self, request, format=None):
        try:
            data = request.data
            lot_id = data.get("lot_id")
            missing_qty = data.get("missing_qty")

            if not lot_id:
                return Response({"success": False, "error": "Lot ID is required"}, status=status.HTTP_400_BAD_REQUEST)

            total_stock = TotalStockModel.objects.get(lot_id=lot_id)
            total_stock.iqf_accepted_qty_verified = True
            total_stock.last_process_module = "IQF"
            total_stock.next_process_module = "Brass QC"
            
            
            total_stock.save()

            # Use Brass_Audit or Brass_QC based on send_brass_audit_to_iqf
            if getattr(total_stock, 'send_brass_audit_to_iqf', False):
                brass_rejection_obj = Brass_Audit_Rejection_ReasonStore.objects.filter(lot_id=lot_id).first()
                brass_rejection_total_qty = brass_rejection_obj.total_rejection_quantity if brass_rejection_obj else 0
            else:
                brass_rejection_obj = Brass_QC_Rejection_ReasonStore.objects.filter(lot_id=lot_id).first()
                brass_rejection_total_qty = brass_rejection_obj.total_rejection_quantity if brass_rejection_obj else 0

            use_total_qty = brass_rejection_total_qty

            if missing_qty not in [None, ""]:
                try:
                    missing_qty = int(missing_qty)
                except ValueError:
                    return Response({"success": False, "error": "Missing quantity must be an integer"}, status=status.HTTP_400_BAD_REQUEST)

                if missing_qty == use_total_qty:
                    return Response(
                        {"success": False, "error": "Missing quantity cannot be equal to assigned quantity."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                if missing_qty > use_total_qty:
                    return Response(
                        {"success": False, "error": "Missing quantity must be less than assigned quantity."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                total_stock.iqf_missing_qty = missing_qty
                total_stock.iqf_physical_qty = use_total_qty - missing_qty
            total_stock.save()

            # ✅ If send_brass_audit_to_iqf is True, create IQFTrayId from BrassAuditTrayId (rejected_tray=True)
            if getattr(total_stock, 'send_brass_audit_to_iqf', False):
                rejected_trays = BrassAuditTrayId.objects.filter(lot_id=lot_id, rejected_tray=True)
            else:
                rejected_trays = BrassTrayId.objects.filter(lot_id=lot_id, rejected_tray=True)

            created_count = 0
            updated_count = 0

            for tray in rejected_trays:
                # ✅ FIXED: Preserve top_tray flag from source tray (Brass QC/Brass Audit)
                source_top_tray = getattr(tray, 'top_tray', False)
                
                iqf_tray, created = IQFTrayId.objects.get_or_create(
                    tray_id=tray.tray_id,
                    lot_id=lot_id,
                    defaults={
                        'lot_id': lot_id,
                        'batch_id': tray.batch_id,
                        'tray_quantity': tray.tray_quantity,
                        'tray_capacity': tray.tray_capacity,
                        'tray_type': tray.tray_type,
                        'rejected_tray': True,
                        'IP_tray_verified': True,
                        'new_tray': False,
                        'top_tray': source_top_tray,  # ✅ FIXED: Use source tray's top_tray flag
                    }
                )
                if not created:
                    iqf_tray.lot_id = lot_id
                    iqf_tray.batch_id = tray.batch_id
                    iqf_tray.tray_quantity = tray.tray_quantity
                    iqf_tray.tray_capacity = tray.tray_capacity
                    iqf_tray.tray_type = tray.tray_type
                    iqf_tray.rejected_tray = True
                    iqf_tray.IP_tray_verified = True
                    iqf_tray.new_tray = False
                    iqf_tray.top_tray = source_top_tray  # ✅ FIXED: Preserve source top_tray flag
                    iqf_tray.save(update_fields=[
                        'lot_id', 'batch_id', 'tray_quantity', 'tray_capacity',
                        'tray_type', 'rejected_tray', 'IP_tray_verified', 'new_tray', 'top_tray'
                    ])
                    updated_count += 1
                else:
                    created_count += 1

            print(f"✅ Synced rejected trays to IQFTrayId: created={created_count}, updated={updated_count}")

            return Response({"success": True})

        except TotalStockModel.DoesNotExist:
            return Response({"success": False, "error": "Stock not found."}, status=status.HTTP_404_NOT_FOUND)

        except Exception as e:
            import traceback
            traceback.print_exc()
            return Response({"success": False, "error": "Unexpected error occurred"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@method_decorator(login_required, name='dispatch')
@method_decorator(csrf_exempt, name='dispatch')
class IQFSaveIPPickRemarkAPIView(APIView):
    def post(self, request):
        try:
            data = request.data if hasattr(request, 'data') else json.loads(request.body.decode('utf-8'))
            batch_id = data.get('batch_id')
            remark = data.get('remark', '').strip()
            if not batch_id:
                return JsonResponse({'success': False, 'error': 'Missing batch_id'}, status=400)
            mmc = ModelMasterCreation.objects.filter(batch_id=batch_id).first()
            if not mmc:
                return JsonResponse({'success': False, 'error': 'Batch not found'}, status=404)
            batch_obj = TotalStockModel.objects.filter(batch_id=mmc).first()  
            if not batch_obj:
                return JsonResponse({'success': False, 'error': 'TotalStockModel not found'}, status=404)
            batch_obj.IQF_pick_remarks = remark
            batch_obj.save(update_fields=['IQF_pick_remarks'])
            return JsonResponse({'success': True, 'message': 'Remark saved'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)


@method_decorator(login_required, name='dispatch')
@method_decorator(csrf_exempt, name='dispatch')
class IQFTrayValidate_Complete_APIView(APIView):
    def post(self, request):
        try:
            data = request.data if hasattr(request, 'data') else json.loads(request.body.decode('utf-8'))
            tray_id = str(data.get('tray_id', '')).strip()
            batch_id = str(data.get('batch_id', '')).strip()

            print(f"[IQF Validation] Validating tray {tray_id} for batch {batch_id}")
            
            # 1. Basic Existence Check
            # Only check if tray_id exists in TrayId table
            tray_obj = TrayId.objects.filter(tray_id=tray_id).first()
            exists = bool(tray_obj)

            if not exists:
                return JsonResponse({
                    'success': True,
                    'exists': False,
                    'error': f'Tray {tray_id} not found in system'
                })

            # 2. Tray Type Validation
            if batch_id:
                try:
                    # Get expected tray type from batch
                    # Note: batch_id in request might be lot_id or batch_id, handling both
                    batch_obj = ModelMasterCreation.objects.filter(batch_id=batch_id).first()
                    if not batch_obj:
                         # Try as lot_id
                         batch_obj = ModelMasterCreation.objects.filter(lot_id=batch_id).first()
                    
                    if batch_obj:
                        expected_tray_type = batch_obj.tray_type
                        
                        # Get actual tray type
                        actual_tray_type = tray_obj.tray_type
                        
                        print(f"   - Expected Type: {expected_tray_type}")
                        print(f"   - Actual Type: {actual_tray_type}")
                        
                        # Normalize for comparison
                        expected_norm = str(expected_tray_type).strip().lower() if expected_tray_type else ''
                        actual_norm = str(actual_tray_type).strip().lower() if actual_tray_type else ''
                        
                        # Strict Validation: If batch has a type, tray must match
                        if expected_norm:
                            if expected_norm != actual_norm:
                                return JsonResponse({
                                    'success': False,
                                    'exists': True,
                                    'error': f'Tray Type Mismatch! Expected: {expected_tray_type}, Scanned: {actual_tray_type or "None"}'
                                })
                            
                except Exception as val_e:
                    print(f"   - Validation Error: {val_e}")
                    # Don't block flow on error, but log it

            return JsonResponse({
                'success': True,
                'exists': exists
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
# 1. ADD THIS API VIEW TO YOUR views.py


@method_decorator(login_required, name='dispatch')
@method_decorator(csrf_exempt, name='dispatch')
class IQFCompleteTableTrayIdListAPIView(APIView):
    def get(self, request):
        lot_id = request.GET.get("lot_id")
        if not lot_id:
            return Response({"success": False, "error": "Lot ID is required"}, status=400)

        try:
            print(f"🔍 [IQFCompleteTableTrayIdListAPIView] Fetching trays for completed lot_id: {lot_id}")
            
            all_trays = []
            
            # ✅ FIXED: Distribute rejection quantities from IQF_Rejected_TrayScan to rejected trays in IQFTrayId
            # 1. Get accepted trays from IQF_Accepted_TrayID_Store
            accepted_store_trays = IQF_Accepted_TrayID_Store.objects.filter(lot_id=lot_id)
            print(f"🔍 Found {accepted_store_trays.count()} accepted trays in IQF_Accepted_TrayID_Store")
            
            for store_tray in accepted_store_trays:
                all_trays.append({
                    "tray_id": store_tray.tray_id,
                    "tray_quantity": store_tray.tray_qty,
                    "rejected_tray": False,
                    "delink_tray": False,
                    "iqf_reject_verify": False,
                    "new_tray": False,
                    "IP_tray_verified": True,
                    "top_tray": False
                })
                print(f"   ✅ Accepted Tray: ID={store_tray.tray_id}, Qty={store_tray.tray_qty}")
            
            # 2. Get all rejection quantities from IQF_Rejected_TrayScan (ordered by top_tray first)
            rejected_scan_records = IQF_Rejected_TrayScan.objects.filter(lot_id=lot_id).order_by('-top_tray', 'id')
            print(f"🔍 Found {rejected_scan_records.count()} rejection records in IQF_Rejected_TrayScan")
            
            # Collect rejection quantities (whether they have tray_ids or not)
            rejection_quantities = []
            for scan_record in rejected_scan_records:
                try:
                    qty = int(scan_record.rejected_tray_quantity) if scan_record.rejected_tray_quantity else 0
                    rejection_quantities.append({
                        'qty': qty,
                        'tray_id': scan_record.tray_id if scan_record.tray_id else None,
                        'top_tray': scan_record.top_tray,
                        'reason': scan_record.rejection_reason.rejection_reason if scan_record.rejection_reason else "N/A",
                        'reason_id': scan_record.rejection_reason.rejection_reason_id if scan_record.rejection_reason else ""
                    })
                    print(f"   📝 Rejection record: TrayID={scan_record.tray_id or 'None'}, Qty={qty}, Top={scan_record.top_tray}")
                except (ValueError, TypeError):
                    pass
            
            # 3. Get rejected trays from IQFTrayId (ordered by top_tray first)
            rejected_iqf_trays = IQFTrayId.objects.filter(
                lot_id=lot_id, 
                rejected_tray=True,
                delink_tray=False  # Exclude delinked trays
            ).exclude(
                tray_id__in=[t.tray_id for t in accepted_store_trays]  # Exclude accepted trays
            ).order_by('-top_tray', 'id')
            print(f"🔍 Found {rejected_iqf_trays.count()} rejected trays in IQFTrayId (excluding delinked)")
            
            # 4. ✅ SMART MATCHING: Match rejection quantities with tray IDs
            # Strategy: First match by tray_id if present, then distribute remaining by order
            
            # Create a map of tray_id -> quantity from rejection records that HAVE tray_ids
            explicit_mappings = {}
            unassigned_quantities = []
            
            for rej_qty_info in rejection_quantities:
                if rej_qty_info['tray_id']:
                    explicit_mappings[rej_qty_info['tray_id']] = rej_qty_info
                else:
                    unassigned_quantities.append(rej_qty_info)
            
            print(f"   📊 Explicit mappings: {len(explicit_mappings)}, Unassigned quantities: {len(unassigned_quantities)}")
            
            # Get list of available tray IDs that don't have explicit mappings
            available_tray_ids = [tray.tray_id for tray in rejected_iqf_trays if tray.tray_id not in explicit_mappings]
            
            # Distribute unassigned quantities to available trays in order
            unassigned_idx = 0
            for tray_id in available_tray_ids:
                if unassigned_idx < len(unassigned_quantities):
                    explicit_mappings[tray_id] = unassigned_quantities[unassigned_idx]
                    print(f"   🔗 Auto-assigned: {tray_id} -> Qty: {unassigned_quantities[unassigned_idx]['qty']}")
                    unassigned_idx += 1
            
            # 5. Build rejected tray list using the mappings
            for rej_tray in rejected_iqf_trays:
                if rej_tray.tray_id in explicit_mappings:
                    rejection_info = explicit_mappings[rej_tray.tray_id]
                    tray_qty = rejection_info['qty']
                    top_tray = rejection_info['top_tray'] if rejection_info['top_tray'] else rej_tray.top_tray
                    print(f"   ✅ Rejected Tray: ID={rej_tray.tray_id}, Qty={tray_qty}, Top={top_tray}")
                else:
                    # This shouldn't happen if logic is correct, but fallback just in case
                    tray_qty = 0
                    top_tray = rej_tray.top_tray
                    print(f"   ⚠️ Rejected Tray (no quantity found): ID={rej_tray.tray_id}, Qty={tray_qty}, Top={top_tray}")
                
                all_trays.append({
                    "tray_id": rej_tray.tray_id,
                    "tray_quantity": tray_qty,
                    "rejected_tray": True,
                    "delink_tray": False,
                    "iqf_reject_verify": rej_tray.iqf_reject_verify,
                    "new_tray": False,
                    "IP_tray_verified": False,
                    "top_tray": top_tray
                })
            
            print(f"🔍 [IQFCompleteTableTrayIdListAPIView] Returning {len(all_trays)} total trays")
            
            return Response({
                "success": True,
                "trays": all_trays,
                "total_trays": len(all_trays)
            })
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"❌ [IQFCompleteTableTrayIdListAPIView] Error: {str(e)}")
            return Response({"success": False, "error": str(e)}, status=500)


@method_decorator(login_required, name='dispatch')
@method_decorator(csrf_exempt, name='dispatch')
class IQFPickCompleteTableTrayIdListAPIView(APIView):
    def get(self, request):
        lot_id = request.GET.get("lot_id")
        if not lot_id:
            return Response({"success": False, "error": "Lot ID is required"}, status=400)

        try:
            print(f"🔍 [IQFPickCompleteTableTrayIdListAPIView] Fetching rejected trays for lot_id: {lot_id}")
            
            # Get TotalStockModel for this lot
            total_stock = TotalStockModel.objects.filter(lot_id=lot_id).first()

            # ✅ ENHANCED: Fetch from both Brass QC rejection data AND IQF rejection data
            from Brass_QC.models import Brass_QC_Rejected_TrayScan, BrassTrayId, Brass_QC_Rejection_ReasonStore
            from BrassAudit.models import BrassAuditTrayId, Brass_Audit_Rejected_TrayScan, Brass_Audit_Rejection_ReasonStore
            
            all_trays = []
            
            # 🔹 PRIMARY: Get tray-wise rejections from Brass QC (with actual tray IDs)
            if total_stock and getattr(total_stock, 'send_brass_audit_to_iqf', False):
                # Use Brass Audit data
                brass_rejected_trays = Brass_Audit_Rejected_TrayScan.objects.filter(
                    lot_id=lot_id,
                    rejected_tray_id__isnull=False
                ).exclude(rejected_tray_id='').select_related('rejection_reason').order_by('id')
                
                # Also get batch rejection trays from BrassAuditTrayId
                batch_rejected_trays = BrassAuditTrayId.objects.filter(
                    lot_id=lot_id, 
                    rejected_tray=True
                ).order_by('id')
            else:
                # Use Brass QC data
                brass_rejected_trays = Brass_QC_Rejected_TrayScan.objects.filter(
                    lot_id=lot_id,
                    rejected_tray_id__isnull=False
                ).exclude(rejected_tray_id='').select_related('rejection_reason').order_by('id')
                
                # Also get batch rejection trays from BrassTrayId
                batch_rejected_trays = BrassTrayId.objects.filter(
                    lot_id=lot_id, 
                    rejected_tray=True
                ).order_by('id')
            
            print(f"🔍 [IQFPickCompleteTableTrayIdListAPIView] Found {brass_rejected_trays.count()} tray-wise rejected trays")
            print(f"🔍 [IQFPickCompleteTableTrayIdListAPIView] Found {batch_rejected_trays.count()} batch rejected trays")
            
            # 🔹 Build tray list from Brass QC tray-wise rejection data (PARTIAL REJECTIONS)
            for idx, tray in enumerate(brass_rejected_trays):
                # ✅ FIXED: Get actual top_tray flag from Brass_QC_Rejected_TrayScan record
                is_top_tray = getattr(tray, 'top_tray', False)
                
                # ✅ ENHANCED DEBUG: Log the top_tray value being read
                print(f"   🔍 DEBUG Tray {idx + 1}: tray_id={tray.rejected_tray_id}, top_tray field value={is_top_tray}")
                
                tray_data = {
                    "tray_id": tray.rejected_tray_id,  # Use rejected_tray_id field
                    "tray_quantity": int(tray.rejected_tray_quantity) if tray.rejected_tray_quantity else 0,
                    "rejected_tray": True,  # Always true in this context
                    "rejection_reason": tray.rejection_reason.rejection_reason if tray.rejection_reason else "N/A",
                    "rejection_reason_id": tray.rejection_reason.rejection_reason_id if tray.rejection_reason else "",
                    "is_top_tray": is_top_tray,  # ✅ FIXED: Use actual top_tray field from database
                    "top_tray": is_top_tray,  # ✅ ADDED: Also include as 'top_tray' for compatibility
                    "source": "brass_qc_partial",  # Identify source
                    "rejection_type": "Partial Rejection",
                    # These fields may not be available in Brass_QC_Rejected_TrayScan, set defaults
                    "delink_tray": False,
                    "iqf_reject_verify": False,
                    "new_tray": False,
                    "IP_tray_verified": False
                }
                all_trays.append(tray_data)
                print(f"   ✅ Partial Rejection Tray {idx + 1}: ID={tray.rejected_tray_id}, Qty={tray.rejected_tray_quantity}, Top={is_top_tray}, Reason={tray.rejection_reason.rejection_reason if tray.rejection_reason else 'N/A'}")
            
            # 🔹 Get tray IDs already added from partial rejections to avoid duplicates
            partial_tray_ids = {tray_data['tray_id'] for tray_data in all_trays}
            
            # 🔹 Build tray list from Brass QC batch rejection data (BATCH REJECTIONS)
            for idx, tray in enumerate(batch_rejected_trays):
                # Skip if this tray was already added from partial rejections
                if tray.tray_id not in partial_tray_ids:
                    # For batch rejections, we need to get the rejection reason from Brass_QC_Rejection_ReasonStore
                    if total_stock and getattr(total_stock, 'send_brass_audit_to_iqf', False):
                        reason_store = Brass_Audit_Rejection_ReasonStore.objects.filter(lot_id=lot_id, batch_rejection=True).first()
                    else:
                        reason_store = Brass_QC_Rejection_ReasonStore.objects.filter(lot_id=lot_id, batch_rejection=True).first()
                    
                    batch_reason = "Batch Rejection"
                    batch_comment = ""
                    if reason_store:
                        # Get first rejection reason
                        first_reason = reason_store.rejection_reason.first()
                        if first_reason:
                            batch_reason = first_reason.rejection_reason
                        if reason_store.lot_rejected_comment:
                            batch_comment = f" - {reason_store.lot_rejected_comment}"
                    
                    # ✅ FIXED: Get actual top_tray flag from BrassTrayId/BrassAuditTrayId
                    is_top_tray = getattr(tray, 'top_tray', False)
                    
                    tray_data = {
                        "tray_id": tray.tray_id,  # Use tray_id field from BrassTrayId
                        "tray_quantity": tray.tray_quantity or 0,
                        "rejected_tray": True,
                        "rejection_reason": f"{batch_reason}{batch_comment}",
                        "rejection_reason_id": first_reason.rejection_reason_id if reason_store and first_reason else "",
                        "is_top_tray": is_top_tray,  # ✅ FIXED: Use actual top_tray field from database
                        "source": "brass_qc_batch",  # Identify source
                        "rejection_type": "Batch Rejection",
                        "delink_tray": getattr(tray, 'delink_tray', False),
                        "iqf_reject_verify": getattr(tray, 'iqf_reject_verify', False),
                        "new_tray": getattr(tray, 'new_tray', False),
                        "IP_tray_verified": getattr(tray, 'IP_tray_verified', False)
                    }
                    all_trays.append(tray_data)
                    print(f"   ✅ Batch Rejection Tray {idx + 1}: ID={tray.tray_id}, Qty={tray.tray_quantity}, Top={is_top_tray}, Reason={batch_reason}")
            
            # 🔹 SECONDARY: Get any additional IQF-specific rejections with tray IDs
            iqf_rejected_trays = IQF_Rejected_TrayScan.objects.filter(
                lot_id=lot_id,
                tray_id__isnull=False
            ).exclude(tray_id='').select_related('rejection_reason').order_by('id')
            
            print(f"🔍 [IQFPickCompleteTableTrayIdListAPIView] Found {iqf_rejected_trays.count()} IQF-specific rejected trays")
            
            # 🔹 Add IQF-specific rejections (avoiding duplicates)
            existing_tray_ids = {tray_data['tray_id'] for tray_data in all_trays}
            
            for idx, tray in enumerate(iqf_rejected_trays):
                if tray.tray_id not in existing_tray_ids:  # Avoid duplicates
                    tray_data = {
                        "tray_id": tray.tray_id,
                        "tray_quantity": int(tray.rejected_tray_quantity) if tray.rejected_tray_quantity else 0,
                        "rejected_tray": True,
                        "rejection_reason": tray.rejection_reason.rejection_reason if tray.rejection_reason else "N/A",
                        "rejection_reason_id": tray.rejection_reason.rejection_reason_id if tray.rejection_reason else "",
                        "is_top_tray": tray.top_tray,
                        "source": "iqf",  # Identify source
                        "rejection_type": "IQF Rejection",
                        "delink_tray": False,
                        "iqf_reject_verify": False,
                        "new_tray": False,
                        "IP_tray_verified": False
                    }
                    all_trays.append(tray_data)
                    print(f"   ✅ IQF Rejection Tray {idx + 1}: ID={tray.tray_id}, Qty={tray.rejected_tray_quantity}, Reason={tray.rejection_reason.rejection_reason if tray.rejection_reason else 'N/A'}")

            # ✅ FIXED: Aggregate duplicate tray IDs by summing quantities
            print(f"🔍 [IQFPickCompleteTableTrayIdListAPIView] Before aggregation: {len(all_trays)} tray records")
            
            aggregated_trays = {}
            for tray in all_trays:
                tray_id = tray['tray_id']
                
                if tray_id in aggregated_trays:
                    # Tray already exists - aggregate quantities
                    existing = aggregated_trays[tray_id]
                    existing['tray_quantity'] += tray['tray_quantity']
                    
                    # Preserve top_tray flag if any record has it
                    if tray.get('is_top_tray', False):
                        existing['is_top_tray'] = True
                        existing['top_tray'] = True
                    
                    # Combine rejection reasons if different
                    if tray.get('rejection_reason') and tray['rejection_reason'] not in existing['rejection_reason']:
                        existing['rejection_reason'] += f" | {tray['rejection_reason']}"
                    
                    print(f"   🔄 Aggregated duplicate {tray_id}: {tray['tray_quantity']} added to existing {existing['tray_quantity'] - tray['tray_quantity']} = {existing['tray_quantity']}")
                else:
                    # First occurrence of this tray ID
                    aggregated_trays[tray_id] = tray.copy()
                    print(f"   ✅ New tray {tray_id}: Qty={tray['tray_quantity']}")
            
            # Convert back to list and maintain order
            final_trays = list(aggregated_trays.values())
            
            print(f"🔍 [IQFPickCompleteTableTrayIdListAPIView] After aggregation: {len(final_trays)} unique trays")
            for idx, tray in enumerate(final_trays):
                print(f"   Final Tray {idx + 1}: ID={tray['tray_id']}, Qty={tray['tray_quantity']}, Top={tray.get('is_top_tray', False)}")
            
            return Response({
                "success": True,
                "trays": final_trays,
                "total_trays": len(final_trays)
            })

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"❌ [IQFPickCompleteTableTrayIdListAPIView] Error: {str(e)}")
            return Response({"success": False, "error": str(e)}, status=500)


@method_decorator(login_required, name='dispatch')
@method_decorator(csrf_exempt, name='dispatch')
class IQFAcceptCompleteTableTrayIdListAPIView(APIView):
    def get(self, request):
        lot_id = request.GET.get("lot_id")
        if not lot_id:
            return Response({"success": False, "error": "Lot ID is required"}, status=400)

        try:
            # Get TotalStockModel for this lot
            total_stock = TotalStockModel.objects.filter(lot_id=lot_id).first()

            # Decide tray source
            if total_stock and getattr(total_stock, 'send_brass_audit_to_iqf', False):
                trays = IQFTrayId.objects.filter(lot_id=lot_id, rejected_tray=False)
            else:
                trays = IQFTrayId.objects.filter(lot_id=lot_id, rejected_tray=False)

            # Build tray list
            all_trays = []
            for tray in trays:
                tray_data = {
                    "tray_id": tray.tray_id,
                    "tray_quantity": tray.tray_quantity,
                    "rejected_tray": tray.rejected_tray,
                    "is_top_tray": getattr(tray, 'top_tray', False),  # ✅ FIXED: Include top_tray field
                    "delink_tray": getattr(tray, 'delink_tray', False),
                    "iqf_reject_verify": getattr(tray, 'iqf_reject_verify', False),
                    "new_tray": getattr(tray, 'new_tray', False),
                    "IP_tray_verified": getattr(tray, 'IP_tray_verified', False)
                }
                all_trays.append(tray_data)

            return Response({
                "success": True,
                "trays": all_trays,
                "total_trays": len(all_trays)
            })

        except Exception as e:
            import traceback
            traceback.print_exc()
            return Response({"success": False, "error": str(e)}, status=500)

@method_decorator(login_required, name='dispatch')
@method_decorator(csrf_exempt, name='dispatch')
class IQFRejectTableTrayIdListAPIView(APIView):
    def get(self, request):
        lot_id = request.GET.get("lot_id")
        if not lot_id:
            return Response({"success": False, "error": "Lot ID is required"}, status=400)

        try:
            print(f"🔍 [IQFRejectTableTrayIdListAPIView] Fetching rejected trays for lot_id: {lot_id}")
            
            rejected_trays = []
            delinked_trays = []
            
            # ============================
            # STEP 1: Get all IQF rejection quantities from IQF_Rejected_TrayScan
            # ============================
            iqf_rejection_records = IQF_Rejected_TrayScan.objects.filter(
                lot_id=lot_id
            ).select_related('rejection_reason').order_by('-top_tray', 'id')
            
            print(f"   📦 Found {iqf_rejection_records.count()} IQF rejection records")
            
            # Collect rejection quantity information (some may have tray_id, some may not)
            rejection_quantities = []
            for rec in iqf_rejection_records:
                rejection_quantities.append({
                    'tray_id': rec.tray_id if rec.tray_id else None,
                    'qty': int(rec.rejected_tray_quantity) if rec.rejected_tray_quantity else 0,
                    'top_tray': rec.top_tray,
                    'rejection_reason': rec.rejection_reason.rejection_reason if rec.rejection_reason else "N/A",
                    'rejection_reason_id': rec.rejection_reason.rejection_reason_id if rec.rejection_reason else ""
                })
            
            print(f"   📊 Collected {len(rejection_quantities)} rejection quantities")
            for rq in rejection_quantities:
                print(f"      - Tray ID: {rq['tray_id']}, Qty: {rq['qty']}, Top: {rq['top_tray']}")
            
            # ============================
            # STEP 2: Get all rejected tray IDs from IQFTrayId (excluding delinked)
            # ============================
            rejected_iqf_trays = IQFTrayId.objects.filter(
                lot_id=lot_id,
                rejected_tray=True,
                delink_tray=False
            ).order_by('-top_tray', 'id')
            
            print(f"   🆔 Found {rejected_iqf_trays.count()} rejected tray IDs (excluding delinked)")
            for tray in rejected_iqf_trays:
                print(f"      - Tray ID: {tray.tray_id}, Qty in IQFTrayId: {tray.tray_quantity}, Top: {tray.top_tray}")
            
            # ============================
            # STEP 3: Smart matching - map rejection quantities to tray IDs
            # ============================
            # Create explicit mappings for records that already have tray_ids
            explicit_mappings = {}
            unassigned_quantities = []
            
            for rej_qty_info in rejection_quantities:
                if rej_qty_info['tray_id']:
                    explicit_mappings[rej_qty_info['tray_id']] = rej_qty_info
                else:
                    unassigned_quantities.append(rej_qty_info)
            
            # ============================
            # STEP 3: Smart matching - map rejection quantities to tray IDs
            # ============================
            # Create explicit mappings for records that already have tray_ids
            explicit_mappings = {}
            unassigned_quantities = []
            
            for rej_qty_info in rejection_quantities:
                if rej_qty_info['tray_id']:
                    explicit_mappings[rej_qty_info['tray_id']] = rej_qty_info
                else:
                    unassigned_quantities.append(rej_qty_info)
            
            print(f"   📊 Explicit mappings: {len(explicit_mappings)}, Unassigned quantities: {len(unassigned_quantities)}")
            
            # Get list of available tray IDs that don't have explicit mappings
            available_tray_ids = [tray.tray_id for tray in rejected_iqf_trays if tray.tray_id not in explicit_mappings]
            
            # Distribute unassigned quantities to available trays in order (top tray first)
            unassigned_idx = 0
            for tray_id in available_tray_ids:
                if unassigned_idx < len(unassigned_quantities):
                    explicit_mappings[tray_id] = unassigned_quantities[unassigned_idx]
                    print(f"   🔗 Auto-assigned: {tray_id} -> Qty: {unassigned_quantities[unassigned_idx]['qty']}")
                    unassigned_idx += 1
            
            # ============================
            # STEP 3.5: Assign remaining unassigned to lot trays
            # ============================
            all_lot_trays = list(IQFTrayId.objects.filter(lot_id=lot_id).values_list('tray_id', flat=True))
            print(f"   🗂️ Found {len(all_lot_trays)} lot trays: {all_lot_trays}")
            
            for idx, unassigned in enumerate(unassigned_quantities[unassigned_idx:], start=1):
                # Assign to lot trays if available, otherwise use virtual
                if idx-1 < len(all_lot_trays):
                    assigned_tray_id = all_lot_trays[idx-1]
                else:
                    assigned_tray_id = f"Rejection {idx}"
                explicit_mappings[assigned_tray_id] = unassigned
                print(f"   🔗 Assigned to lot/virtual: {assigned_tray_id} -> Qty: {unassigned['qty']}")
            
            # ============================
            # STEP 4: Build rejected tray list using the mappings
            # ============================
            for rej_tray in rejected_iqf_trays:
                if rej_tray.tray_id in explicit_mappings:
                    rejection_info = explicit_mappings[rej_tray.tray_id]
                    tray_qty = rejection_info['qty']
                    top_tray = rejection_info['top_tray'] if rejection_info['top_tray'] else rej_tray.top_tray
                    rejection_reason = rejection_info.get('rejection_reason', 'N/A')
                    rejection_reason_id = rejection_info.get('rejection_reason_id', '')
                    print(f"   ✅ Rejected Tray: ID={rej_tray.tray_id}, Qty={tray_qty}, Top={top_tray}")
                else:
                    # Fallback: use tray_quantity from IQFTrayId if no mapping found
                    tray_qty = rej_tray.tray_quantity if rej_tray.tray_quantity else 0
                    top_tray = rej_tray.top_tray
                    rejection_reason = "N/A"
                    rejection_reason_id = ""
                    print(f"   ⚠️ Rejected Tray (no explicit quantity found, using IQFTrayId qty): ID={rej_tray.tray_id}, Qty={tray_qty}, Top={top_tray}")
                
                rejected_trays.append({
                    "tray_id": rej_tray.tray_id,
                    "tray_quantity": tray_qty,
                    "rejected_tray": True,
                    "delink_tray": False,
                    "iqf_reject_verify": rej_tray.iqf_reject_verify,
                    "new_tray": rej_tray.new_tray,
                    "IP_tray_verified": rej_tray.IP_tray_verified,
                    "top_tray": top_tray,
                    "is_top_tray": top_tray,
                    "rejection_reason": rejection_reason,
                    "rejection_reason_id": rejection_reason_id
                })
            
            # Add assigned unassigned rejection quantities as rejected trays
            for assigned_tray_id, unassigned in explicit_mappings.items():
                if assigned_tray_id not in [t['tray_id'] for t in rejected_trays]:
                    rejected_trays.append({
                        "tray_id": assigned_tray_id,
                        "tray_quantity": unassigned['qty'],
                        "rejected_tray": True,
                        "delink_tray": False,
                        "iqf_reject_verify": False,
                        "new_tray": False,
                        "IP_tray_verified": True,
                        "top_tray": unassigned['top_tray'],
                        "is_top_tray": unassigned['top_tray'],
                        "rejection_reason": unassigned.get('rejection_reason', 'N/A'),
                        "rejection_reason_id": unassigned.get('rejection_reason_id', '')
                    })
                    print(f"   ➕ Added assigned rejected tray: ID={assigned_tray_id}, Qty={unassigned['qty']}, Top={unassigned['top_tray']}")
            
            # ============================
            # STEP 5: Get delinked trays
            # ============================
            delinked_iqf_trays = IQFTrayId.objects.filter(
                lot_id=lot_id,
                delink_tray=True
            ).order_by('id')
            
            print(f"   🔄 Found {delinked_iqf_trays.count()} delinked trays")
            
            for delink_tray in delinked_iqf_trays:
                delinked_trays.append({
                    "tray_id": delink_tray.tray_id,
                    "tray_quantity": 0,  # Delinked trays have 0 quantity
                    "rejected_tray": False,
                    "delink_tray": True,
                    "iqf_reject_verify": delink_tray.iqf_reject_verify,
                    "new_tray": delink_tray.new_tray,
                    "IP_tray_verified": delink_tray.IP_tray_verified,
                    "top_tray": False,
                    "is_top_tray": False
                })
            
            print(f"🔍 [IQFRejectTableTrayIdListAPIView] Returning {len(rejected_trays)} rejected trays and {len(delinked_trays)} delinked trays")
            
            return Response({
                "success": True,
                "rejected_trays": rejected_trays,
                "delinked_trays": delinked_trays,
                "total_rejected": len(rejected_trays),
                "total_delinked": len(delinked_trays)
            })
        except Exception as e:
            import traceback
            traceback.print_exc()
            return Response({"success": False, "error": str(e)}, status=500)



@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(login_required, name='dispatch')
class IQFLotRejectionDraftAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            data = request.data if hasattr(request, 'data') else json.loads(request.body.decode('utf-8'))
            batch_id = data.get('batch_id')
            lot_id = data.get('lot_id')
            total_qty = data.get('total_qty', 0)
            lot_rejected_comment = data.get('lot_rejected_comment', '').strip()
            is_auto_save = data.get('is_auto_save', False)
            # Enforce: auto-save always is_draft = False, only manual Draft is True
            is_draft = data.get('is_draft', False)
            if is_auto_save:
                is_draft = False

            if not batch_id or not lot_id or not lot_rejected_comment:
                return Response({'success': False, 'error': 'Missing required fields'}, status=400)

            draft_obj, created = IQF_Draft_Store.objects.get_or_create(
                lot_id=lot_id,
                draft_type='combined_rejection',
                defaults={
                    'batch_id': batch_id,
                    'user': request.user,
                    'draft_data': {}
                }
            )

            # Only keep lot rejection data, remove tray rejection
            updated_draft_data = {
                'lot_rejection': {
                    'total_qty': total_qty,
                    'lot_rejected_comment': lot_rejected_comment,
                    'is_draft': is_draft
                },
                'has_lot_rejection': True,
                'last_updated_type': 'lot_rejection',
                'batch_id': batch_id
            }

            draft_obj.draft_data = updated_draft_data
            draft_obj.batch_id = batch_id
            draft_obj.user = request.user
            draft_obj.save()

            # Only update lot status to Draft if this is an explicit Draft action (not auto-save)
            if is_draft and not is_auto_save:
                from modelmasterapp.models import TotalStockModel
                total_stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
                if total_stock:
                    total_stock.iqf_draft = True  # If you have a specific draft field, else set status as needed
                    total_stock.save(update_fields=["iqf_draft"])

            return Response({
                'success': True,
                'message': 'Lot rejection draft saved successfully',
                'draft_id': draft_obj.id,
                'combined_draft': True
            })

        except Exception as e:
            import traceback
            traceback.print_exc()
            return Response({'success': False, 'error': str(e)}, status=500)

@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(login_required, name='dispatch')
class IQFTrayRejectionDraftAPIView(APIView):
    permission_classes = [IsAuthenticated]


    def post(self, request):
        try:
            data = request.data if hasattr(request, 'data') else json.loads(request.body.decode('utf-8'))
            lot_id = data.get('lot_id')
            batch_id = data.get('batch_id')
            tray_rejections = data.get('tray_rejections', [])
            accepted_trays = data.get('accepted_trays', [])
            acceptance_remarks = data.get('acceptance_remarks', '')
            is_auto_save = data.get('is_auto_save', False)
            # Enforce: auto-save always is_draft = False, only manual Draft is True
            is_draft = data.get('is_draft', False)
            if is_auto_save:
                is_draft = False

            if not lot_id or not tray_rejections:
                return Response({'success': False, 'error': 'Missing lot_id or tray_rejections'}, status=400)

            draft_obj, created = IQF_Draft_Store.objects.get_or_create(
                lot_id=lot_id,
                draft_type='combined_rejection',
                defaults={
                    'batch_id': batch_id,
                    'user': request.user,
                    'draft_data': {}
                }
            )

            updated_draft_data = {
                'tray_rejection': {
                    'tray_rejections': tray_rejections,
                    'accepted_trays': accepted_trays,
                    'acceptance_remarks': acceptance_remarks,
                    'is_draft': is_draft
                },
                'has_tray_rejection': True,
                'last_updated_type': 'tray_rejection',
                'batch_id': batch_id
            }
            draft_obj.draft_data = updated_draft_data
            draft_obj.batch_id = batch_id
            draft_obj.user = request.user
            draft_obj.save()

            # Only update lot status to Draft if this is an explicit Draft action (not auto-save)
            if is_draft and not is_auto_save:
                from modelmasterapp.models import TotalStockModel
                total_stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
                if total_stock:
                    total_stock.iqf_draft = True  # If you have a specific draft field, else set status as needed
                    total_stock.save(update_fields=["iqf_draft"])

            return Response({
                'success': True,
                'message': 'Tray rejection draft saved successfully',
                'draft_id': draft_obj.id,
                'total_rejections': len(tray_rejections),
                'combined_draft': True
            })

        except Exception as e:
            import traceback
            traceback.print_exc()
            return Response({'success': False, 'error': str(e)}, status=500)

@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(login_required, name='dispatch')
class IQFClearDraftAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            data = request.data if hasattr(request, 'data') else json.loads(request.body.decode('utf-8'))
            lot_id = data.get('lot_id')
            clear_type = data.get('clear_type', 'all')  # 'all', 'lot_rejection', 'tray_rejection'

            if not lot_id:
                return Response({'success': False, 'error': 'Missing lot_id'}, status=400)

            # Get the combined draft
            try:
                draft_obj = IQF_Draft_Store.objects.get(
                    lot_id=lot_id,
                    draft_type='combined_rejection'
                )
            except IQF_Draft_Store.DoesNotExist:
                return Response({'success': True, 'message': 'No draft found to clear'})

            if clear_type == 'all':
                # Delete the entire draft record
                draft_obj.delete()
                return Response({'success': True, 'message': 'All drafts cleared successfully'})
            
            elif clear_type == 'lot_rejection':
                # Remove only lot rejection data
                draft_data = draft_obj.draft_data or {}
                draft_data.pop('lot_rejection', None)
                draft_data.pop('has_lot_rejection', None)
                
                # If no tray rejection data exists, delete the record
                if not draft_data.get('has_tray_rejection'):
                    draft_obj.delete()
                else:
                    draft_obj.draft_data = draft_data
                    draft_obj.save()
                
                return Response({'success': True, 'message': 'Lot rejection draft cleared successfully'})
            
            elif clear_type == 'tray_rejection':
                # Remove only tray rejection data
                draft_data = draft_obj.draft_data or {}
                draft_data.pop('tray_rejection', None)
                draft_data.pop('has_tray_rejection', None)
                
                # If no lot rejection data exists, delete the record
                if not draft_data.get('has_lot_rejection'):
                    draft_obj.delete()
                else:
                    draft_obj.draft_data = draft_data
                    draft_obj.save()
                
                return Response({'success': True, 'message': 'Tray rejection draft cleared successfully'})

        except Exception as e:
            import traceback
            traceback.print_exc()
            return Response({'success': False, 'error': str(e)}, status=500)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def iqf_get_draft_data(request):
    """Get combined draft data for a lot_id"""
    lot_id = request.GET.get('lot_id')
    
    if not lot_id:
        return Response({'success': False, 'error': 'Missing lot_id'}, status=400)
    
    try:
        draft_obj = IQF_Draft_Store.objects.filter(
            lot_id=lot_id,
            draft_type='combined_rejection'
        ).first()
        
        if draft_obj and draft_obj.draft_data:
            draft_data = draft_obj.draft_data
            return Response({
                'success': True,
                'has_draft': True,
                'has_lot_rejection': draft_data.get('has_lot_rejection', False),
                'has_tray_rejection': draft_data.get('has_tray_rejection', False),
                'lot_rejection_data': draft_data.get('lot_rejection', {}),
                'tray_rejection_data': draft_data.get('tray_rejection', {}),
                'last_updated_type': draft_data.get('last_updated_type', ''),
                'created_at': draft_obj.created_at,
                'updated_at': draft_obj.updated_at
            })
        else:
            return Response({
                'success': True,
                'has_draft': False,
                'has_lot_rejection': False,
                'has_tray_rejection': False,
                'lot_rejection_data': {},
                'tray_rejection_data': {}
            })
            
    except Exception as e:
        return Response({'success': False, 'error': str(e)}, status=500)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def iqf_get_all_drafts(request):
    """Get combined draft data for a lot_id (backwards compatibility)"""
    lot_id = request.GET.get('lot_id')
    
    if not lot_id:
        return Response({'success': False, 'error': 'Missing lot_id'}, status=400)
    
    try:
        # ✅ FIXED: Look for separate draft types (following new API pattern)
        batch_draft = IQF_Draft_Store.objects.filter(
            lot_id=lot_id,
            draft_type='batch_rejection'
        ).first()
        
        tray_draft = IQF_Draft_Store.objects.filter(
            lot_id=lot_id,
            draft_type='tray_rejection'  
        ).first()
        
        accepted_tray_draft = IQF_Draft_Store.objects.filter(
            lot_id=lot_id,
            draft_type='accepted_tray'
        ).first()
        
        response_data = {
            'success': True,
            'has_lot_rejection': False,
            'has_tray_rejection': False,
            'has_accepted_tray': False,
            'lot_rejection_data': {},
            'tray_rejection_data': {},
            'accepted_tray_data': {}
        }
        
        # ✅ Process batch rejection draft
        if batch_draft and batch_draft.draft_data:
            response_data['has_lot_rejection'] = True
            response_data['lot_rejection_data'] = batch_draft.draft_data
            
        # ✅ Process tray rejection draft  
        if tray_draft and tray_draft.draft_data:
            response_data['has_tray_rejection'] = True
            response_data['tray_rejection_data'] = tray_draft.draft_data
            print(f"🔍 [IQF Get Drafts] Tray rejection draft data: {tray_draft.draft_data}")
            
        # ✅ Process accepted tray draft
        if accepted_tray_draft and accepted_tray_draft.draft_data:
            response_data['has_accepted_tray'] = True
            response_data['accepted_tray_data'] = accepted_tray_draft.draft_data
            print(f"🔍 [IQF Get Drafts] Accepted tray draft data: {accepted_tray_draft.draft_data}")
        
        print(f"✅ [IQF Get Drafts] Found drafts for lot {lot_id}: batch={response_data['has_lot_rejection']}, tray={response_data['has_tray_rejection']}, accepted={response_data['has_accepted_tray']}")
        print(f"🔍 [IQF Get Drafts] Full response data: {response_data}")
        
        return Response(response_data)
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        return Response({'success': False, 'error': str(e)}, status=500) 
    
@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(login_required, name='dispatch')
class IQFLotRejectionAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            data = request.data if hasattr(request, 'data') else json.loads(request.body.decode('utf-8'))
            batch_id = data.get('batch_id')
            lot_id = data.get('lot_id')
            total_qty = data.get('total_qty', 0)
            lot_rejected_comment = data.get('lot_rejected_comment', '').strip()
            
            # NEW: Delink parameters
            missing_qty = data.get('missing_qty', 0)
            delink_tray_ids = data.get('delink_tray_ids', [])
            top_tray_id = data.get('top_tray_id')
            top_tray_qty = data.get('top_tray_qty')

            # Validate required fields
            if not batch_id or not lot_id:
                return Response({'success': False, 'error': 'Missing batch_id or lot_id'}, status=400)
            
            if not lot_rejected_comment:
                return Response({'success': False, 'error': 'Lot rejection remarks are required for batch rejection'}, status=400)

            # Get ModelMasterCreation by batch_id string
            mmc = ModelMasterCreation.objects.filter(batch_id=batch_id).first()
            if not mmc:
                return Response({'success': False, 'error': 'Batch not found'}, status=404)

            # Get TotalStockModel using lot_id
            total_stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
            if not total_stock:
                return Response({'success': False, 'error': 'TotalStockModel not found'}, status=404)

            # NEW: Process delink operations if missing quantity exists
            delink_operations_summary = {'delinked_trays': 0, 'top_tray_updated': False}
            
            if missing_qty > 0 and delink_tray_ids:
                print(f"[BATCH REJECTION DELINK] Processing {len(delink_tray_ids)} delink trays for missing qty: {missing_qty}")
                
                # 1. Process delink trays across all tables
                delinked_count = 0
                for delink_tray_id in delink_tray_ids:
                    if not delink_tray_id.strip():  # Skip empty tray IDs
                        continue
                        
                    print(f"[BATCH REJECTION DELINK] Processing tray: {delink_tray_id}")
                    
                    # IQFTrayId - Remove from lot completely
                    brass_delink_tray_obj = IQFTrayId.objects.filter(tray_id=delink_tray_id, lot_id=lot_id).first()
                    if brass_delink_tray_obj:
                        brass_delink_tray_obj.delink_tray = True
                        brass_delink_tray_obj.lot_id = None
                        brass_delink_tray_obj.batch_id = None
                        brass_delink_tray_obj.IP_tray_verified = False
                        brass_delink_tray_obj.top_tray = False
                        brass_delink_tray_obj.save(update_fields=[
                            'delink_tray', 'lot_id', 'batch_id', 'IP_tray_verified', 'top_tray'
                        ])
                        print(f"✅ Delinked IQFTrayId tray: {delink_tray_id}")
        
                    # IPTrayId - Mark as delinked
                    ip_delink_tray_obj = IPTrayId.objects.filter(tray_id=delink_tray_id, lot_id=lot_id).first()
                    if ip_delink_tray_obj:
                        ip_delink_tray_obj.delink_tray = True
                        ip_delink_tray_obj.save(update_fields=['delink_tray'])
                        print(f"✅ Delinked IPTrayId tray: {delink_tray_id}")
                    
                    bq_delink_tray_obj = BrassTrayId.objects.filter(tray_id=delink_tray_id, lot_id=lot_id).first()
                    if bq_delink_tray_obj:
                        bq_delink_tray_obj.delink_tray = True
                        bq_delink_tray_obj.save(update_fields=['delink_tray'])
                        print(f"✅ Delinked IPTrayId tray: {delink_tray_id}")
                    
                    # DPTrayId_History - Mark as delinked
                    dp_history_tray_obj = DPTrayId_History.objects.filter(tray_id=delink_tray_id, lot_id=lot_id).first()
                    if dp_history_tray_obj:
                        dp_history_tray_obj.delink_tray = True
                        dp_history_tray_obj.save(update_fields=['delink_tray'])
                        print(f"✅ Delinked DPTrayId_History tray: {delink_tray_id}")
                    
                    # TrayId - Remove from lot completely
                    trayid_delink_tray_obj = TrayId.objects.filter(tray_id=delink_tray_id, lot_id=lot_id).first()
                    if trayid_delink_tray_obj:
                        trayid_delink_tray_obj.delink_tray = True
                        trayid_delink_tray_obj.lot_id = None
                        trayid_delink_tray_obj.batch_id = None
                        trayid_delink_tray_obj.IP_tray_verified = False
                        trayid_delink_tray_obj.top_tray = False
                        trayid_delink_tray_obj.save(update_fields=[
                            'delink_tray', 'lot_id', 'batch_id', 'IP_tray_verified', 'top_tray'
                        ])
                        print(f"✅ Delinked TrayId tray: {delink_tray_id}")
                    
                    delinked_count += 1

                # 2. Update top tray (if provided)
                if top_tray_id and top_tray_qty is not None:
                    print(f"[BATCH REJECTION TOP TRAY] Updating tray: {top_tray_id} with qty: {top_tray_qty}")
                    
                    # Update IQFTrayId for top tray
                    top_tray_obj = IQFTrayId.objects.filter(tray_id=top_tray_id, lot_id=lot_id).first()
                    if top_tray_obj:
                        top_tray_obj.top_tray = True
                        top_tray_obj.tray_quantity = int(top_tray_qty)
                        top_tray_obj.delink_tray = False
                        top_tray_obj.save(update_fields=['top_tray', 'tray_quantity', 'delink_tray'])
                        print(f"✅ Updated IQFTrayId top tray: {top_tray_id} to qty: {top_tray_qty}")
                        delink_operations_summary['top_tray_updated'] = True

                # 3. Reset other trays (not delinked or top tray) to full capacity
                other_trays_brass = IQFTrayId.objects.filter(
                    lot_id=lot_id
                ).exclude(
                    tray_id__in=delink_tray_ids + ([top_tray_id] if top_tray_id else [])
                )
                
                other_trays_count = 0
                for tray in other_trays_brass:
                    print(f"[BATCH REJECTION OTHER TRAY] Resetting IQFTrayId {tray.tray_id} to full capacity: {tray.tray_capacity}")
                    tray.tray_quantity = tray.tray_capacity
                    tray.top_tray = False
                    tray.delink_tray = False
                    tray.save(update_fields=['tray_quantity', 'top_tray', 'delink_tray'])
                    other_trays_count += 1

                delink_operations_summary['delinked_trays'] = delinked_count
                print(f"[BATCH REJECTION DELINK SUMMARY] Delinked {delinked_count} trays, reset {other_trays_count} other trays")

            # Get iqf_physical_qty if set and > 0, else use total_stock
            qty = total_stock.iqf_physical_qty

            # Set iqf_rejection = True (original batch rejection logic)
            total_stock.iqf_rejection = True
            total_stock.last_process_module = "IQF QC"
            total_stock.next_process_module = "IQF Audit"
            total_stock.iqf_last_process_date_time = timezone.now()
            total_stock.save(update_fields=[
                'iqf_rejection', 'last_process_module', 'next_process_module',
                'iqf_last_process_date_time'
            ])

            # Update IQFTrayId records (only for non-delinked trays)
            if delink_tray_ids:
                # Only update trays that weren't delinked
                updated_trays_count = IQFTrayId.objects.filter(
                    lot_id=lot_id
                ).exclude(
                    tray_id__in=delink_tray_ids
                ).update(rejected_tray=True)
            else:
                # No delink operations, update all trays
                updated_trays_count = IQFTrayId.objects.filter(lot_id=lot_id).update(rejected_tray=True)

            # Create IQF_QC_Rejection_ReasonStore entry with lot rejection remarks
            IQF_Rejection_ReasonStore.objects.create(
                lot_id=lot_id,
                user=request.user,
                total_rejection_quantity=qty,
                batch_rejection=True,
                lot_rejected_comment=lot_rejected_comment
            )
            # Clear any existing drafts for this lot
            IQF_Draft_Store.objects.filter(
                lot_id=lot_id,
                draft_type__in=['lot_rejection', 'tray_rejection']
            ).delete()

            # Prepare response message
            success_message = 'Batch rejection saved with remarks.'
            if missing_qty > 0 and delink_operations_summary['delinked_trays'] > 0:
                success_message += f' {delink_operations_summary["delinked_trays"]} tray(s) delinked for missing quantity.'

            return Response({
                'success': True, 
                'message': success_message,
                'delink_operations': delink_operations_summary,
                'updated_trays': updated_trays_count,
                'missing_qty_processed': missing_qty
            })

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[BATCH REJECTION ERROR] Failed to process batch rejection with delink: {str(e)}")
            return Response({'success': False, 'error': str(e)}, status=500)



class IQFTrayDelinkTopTrayCalcAPIView(APIView):
    """
    Calculate delink trays and top tray based on missing quantity.

    GET Parameters:
    - lot_id: The lot ID to calculate for
    - missing_qty: The quantity that needs to be delinked

    Returns:
    {
        "success": true,
        "delink_count": int,
        "delink_trays": [tray_id, ...],
        "top_tray": {"tray_id": ..., "qty": ...} or None,
        "total_missing": int,
        "calculation_details": {...}
    }
    """

    def get(self, request):
        try:
            # Get parameters
            lot_id = request.GET.get('lot_id')
            missing_qty = request.GET.get('missing_qty', 0)

            # Validation
            if not lot_id:
                return Response({
                    'success': False,
                    'error': 'Missing lot_id parameter'
                }, status=status.HTTP_400_BAD_REQUEST)

            try:
                missing_qty = int(missing_qty)
                if missing_qty < 0:
                    raise ValueError("Missing quantity cannot be negative")
            except (ValueError, TypeError):
                return Response({
                    'success': False,
                    'error': 'Invalid missing_qty parameter. Must be a non-negative integer.'
                }, status=status.HTTP_400_BAD_REQUEST)

            # If missing quantity is 0, return empty result
            if missing_qty == 0:
                return Response({
                    'success': True,
                    'delink_count': 0,
                    'delink_trays': [],
                    'top_tray': None,
                    'total_missing': 0,
                    'message': 'No delink required'
                })

            # Get trays for the lot, ordered by creation/ID to maintain consistency
            trays = IQFTrayId.objects.filter(
                lot_id=lot_id,
                tray_quantity__gt=0  # Only trays with quantity > 0
            ).order_by('id').values('tray_id', 'tray_quantity')

            if not trays.exists():
                return Response({
                    'success': False,
                    'error': f'No trays found for lot {lot_id}'
                }, status=status.HTTP_404_NOT_FOUND)

            # Convert to list for easier processing
            tray_list = list(trays)

            # Calculate total available quantity
            total_available = sum(tray['tray_quantity'] for tray in tray_list)

            if missing_qty > total_available:
                return Response({
                    'success': False,
                    'error': f'Missing quantity ({missing_qty}) exceeds total available quantity ({total_available})'
                }, status=status.HTTP_400_BAD_REQUEST)

            # Sort tray_list by tray_quantity ascending (smallest first)
            tray_list_sorted = sorted(tray_list, key=lambda x: x['tray_quantity'])
            
            delink_trays = []
            top_tray = None
            remaining_missing = missing_qty
            calculation_steps = []
            
            for i, tray in enumerate(tray_list_sorted):
                tray_id = tray['tray_id']
                tray_qty = tray['tray_quantity']
            
                print(f"[DELINK DEBUG] Step {i+1}: tray_id={tray_id}, tray_qty={tray_qty}, remaining_missing={remaining_missing}")
            
                if remaining_missing <= 0:
                    break
            
                if remaining_missing >= tray_qty:
                    print(f"[DELINK DEBUG] Delinking full tray {tray_id} (qty {tray_qty})")
                    delink_trays.append(tray_id)
                    remaining_missing -= tray_qty
                    calculation_steps.append({
                        'step': i + 1,
                        'tray_id': tray_id,
                        'tray_qty': tray_qty,
                        'action': 'delink_complete',
                        'remaining_missing': remaining_missing
                    })
                else:
                    remaining_qty_in_tray = tray_qty - remaining_missing
                    print(f"[DELINK DEBUG] Top tray is {tray_id}: original_qty={tray_qty}, delinked_qty={remaining_missing}, remaining_qty_in_tray={remaining_qty_in_tray}")
                    top_tray = {
                        'tray_id': tray_id,
                        'qty': remaining_qty_in_tray,
                        'original_qty': tray_qty,
                        'delinked_qty': remaining_missing
                    }
                    calculation_steps.append({
                        'step': i + 1,
                        'tray_id': tray_id,
                        'tray_qty': tray_qty,
                        'action': 'partial_delink',
                        'delinked_from_tray': remaining_missing,
                        'remaining_in_tray': remaining_qty_in_tray,
                        'remaining_missing': 0
                    })
                    remaining_missing = 0
                    break
            
            print(f"[DELINK DEBUG] Final delink_count: {len(delink_trays)}")
            # ✅ PATCH: If missing_qty is exactly consumed by full trays, show next tray as top tray
            if remaining_missing == 0 and len(delink_trays) > 0 and len(tray_list) > len(delink_trays) and top_tray is None:
                next_tray = tray_list[len(delink_trays)]
                top_tray = {
                    'tray_id': next_tray['tray_id'],
                    'qty': next_tray['tray_quantity'],
                    'original_qty': next_tray['tray_quantity'],
                    'delinked_qty': 0,
                    'top_tray': True  # <-- Add this line

                }

            # Prepare response
            result = {
                'success': True,
                'delink_count': len(delink_trays),
                'delink_trays': delink_trays,
                'top_tray': top_tray,
                'total_missing': missing_qty,
                'total_available': total_available,
                'calculation_details': {
                    'steps': calculation_steps,
                    'trays_processed': len([step for step in calculation_steps]),
                    'total_trays_in_lot': len(tray_list)
                }
            }

            return Response(result, status=status.HTTP_200_OK)

        except Exception as e:
            # Log the error in production
            print(f"Error in IQFTrayDelinkTopTrayCalcAPIView: {str(e)}")

            return Response({
                'success': False,
                'error': 'Internal server error occurred while calculating delink requirements'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(login_required, name='dispatch')
class IQFTrayDelinkAndTopTrayUpdateAPIView(APIView):
    def post(self, request):
        try:
            data = request.data if hasattr(request, 'data') else json.loads(request.body.decode('utf-8'))
            lot_id = data.get('lot_id')
            delink_tray_ids = data.get('delink_tray_ids', [])
            top_tray_id = data.get('top_tray_id')
            top_tray_qty = data.get('top_tray_qty')
            
            print(f"[DEBUG] Incoming data: {data}")
            print(f"[DEBUG] Delink tray IDs: {delink_tray_ids}")
            print(f"[DEBUG] Top tray: {top_tray_id} with qty: {top_tray_qty}")

            # 1. Process delink trays across all tables
            delinked_count = 0
            for delink_tray_id in delink_tray_ids:
                print(f"[DELINK] Processing tray: {delink_tray_id}")
                
                # IQFTrayId - Remove from lot completely
                iqf_delink_tray_obj = IQFTrayId.objects.filter(tray_id=delink_tray_id, lot_id=lot_id).first()
                if iqf_delink_tray_obj:
                    iqf_delink_tray_obj.delink_tray = True
                    iqf_delink_tray_obj.lot_id = None
                    iqf_delink_tray_obj.batch_id = None
                    iqf_delink_tray_obj.IP_tray_verified = False
                    iqf_delink_tray_obj.top_tray = False
                    iqf_delink_tray_obj.save(update_fields=[
                        'delink_tray', 'lot_id', 'batch_id', 'IP_tray_verified', 'top_tray'
                    ])
                    print(f"✅ Delinked IQFTrayId tray: {delink_tray_id}")
    
                # IPTrayId - Mark as delinked
                ip_delink_tray_obj = IPTrayId.objects.filter(tray_id=delink_tray_id, lot_id=lot_id).first()
                if ip_delink_tray_obj:
                    ip_delink_tray_obj.delink_tray = True
                    ip_delink_tray_obj.save(update_fields=['delink_tray'])
                    print(f"✅ Delinked IPTrayId tray: {delink_tray_id} for lot: {lot_id}")
                
                # BQTrayId - Mark as delinked
                bq_delink_tray_obj = BrassTrayId.objects.filter(tray_id=delink_tray_id, lot_id=lot_id).first()
                if bq_delink_tray_obj:
                    bq_delink_tray_obj.delink_tray = True
                    bq_delink_tray_obj.save(update_fields=['delink_tray'])
                    print(f"✅ Delinked BrassTrayId tray: {delink_tray_id} for lot: {lot_id}")

                # DPTrayId_History - Mark as delinked
                dp_history_tray_obj = DPTrayId_History.objects.filter(tray_id=delink_tray_id, lot_id=lot_id).first()
                if dp_history_tray_obj:
                    dp_history_tray_obj.delink_tray = True
                    dp_history_tray_obj.save(update_fields=['delink_tray'])
                    print(f"✅ Delinked DPTrayId_History tray: {delink_tray_id} for lot: {lot_id}")
                
                # TrayId - Remove from lot completely
                trayid_delink_tray_obj = TrayId.objects.filter(tray_id=delink_tray_id, lot_id=lot_id).first()
                if trayid_delink_tray_obj:
                    trayid_delink_tray_obj.delink_tray = True
                    trayid_delink_tray_obj.lot_id = None
                    trayid_delink_tray_obj.batch_id = None
                    trayid_delink_tray_obj.IP_tray_verified = False
                    trayid_delink_tray_obj.top_tray = False
                    trayid_delink_tray_obj.save(update_fields=[
                        'delink_tray', 'lot_id', 'batch_id', 'IP_tray_verified', 'top_tray'
                    ])
                    print(f"✅ Delinked TrayId tray: {delink_tray_id}")
                
                delinked_count += 1

            # 2. Update top tray (if provided)
            if top_tray_id and top_tray_qty is not None:
                print(f"[TOP TRAY] Updating tray: {top_tray_id} with qty: {top_tray_qty}")
                
                # Update IQFTrayId for top tray
                top_tray_obj = IQFTrayId.objects.filter(tray_id=top_tray_id, lot_id=lot_id).first()
                if top_tray_obj:
                    top_tray_obj.top_tray = True
                    top_tray_obj.tray_quantity = int(top_tray_qty)
                    top_tray_obj.delink_tray = False  # Ensure it's not marked as delink
                    top_tray_obj.save(update_fields=['top_tray', 'tray_quantity', 'delink_tray'])
                    print(f"✅ Updated IQFTrayId top tray: {top_tray_id} to qty: {top_tray_qty}")

            # 3. Reset other trays (not delinked or top tray) to full capacity
            other_trays_iqf = IQFTrayId.objects.filter(
                lot_id=lot_id
            ).exclude(
                tray_id__in=delink_tray_ids + ([top_tray_id] if top_tray_id else [])
            )
            
            other_trays_count = 0
            for tray in other_trays_iqf:
                print(f"[OTHER TRAY] Resetting IQFTrayId {tray.tray_id} to full capacity: {tray.tray_capacity}")
                tray.tray_quantity = tray.tray_capacity  # Reset to full capacity
                tray.top_tray = False
                tray.delink_tray = False
                tray.save(update_fields=['tray_quantity', 'top_tray', 'delink_tray'])
                other_trays_count += 1

            # 4. Summary logging
            print(f"[SUMMARY] Processing completed:")
            print(f"  - Delinked {delinked_count} trays across all tables")
            if top_tray_id:
                print(f"  - Updated top tray {top_tray_id} to qty={top_tray_qty}")
            print(f"  - Reset {other_trays_count} other trays to full capacity")

            return Response({
                'success': True, 
                'message': f'Delink and top tray update completed successfully.',
                'details': {
                    'delinked_trays': delinked_count,
                    'top_tray_updated': bool(top_tray_id),
                    'other_trays_reset': other_trays_count,
                    'top_tray_id': top_tray_id,
                    'top_tray_qty': top_tray_qty
                }
            })

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[ERROR] Failed to update trays: {str(e)}")
            return Response({
                'success': False, 
                'error': f'Failed to update trays: {str(e)}'
            }, status=500)
            
class IQFValidateTrayIdAPIView(APIView):
    def get(self, request):
        tray_id = request.GET.get('tray_id')
        lot_id = request.GET.get('lot_id')
        exists = IQFTrayId.objects.filter(tray_id=tray_id, lot_id=lot_id).exists()
        return Response({
            'exists': exists,
            'valid_for_lot': exists
        })



@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(login_required, name='dispatch')
class IQFTrayRejectionAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            data = request.data if hasattr(request, 'data') else json.loads(request.body.decode('utf-8'))
            lot_id = data.get('lot_id')
            batch_id = data.get('batch_id')
            tray_rejections = data.get('tray_rejections', [])  # [{reason_id, quantity}]
            accepted_trays = data.get('accepted_trays', [])    # [{tray_id, qty, sequence}]
            acceptance_remarks = data.get('acceptance_remarks', '').strip()

            if not lot_id:
                return Response({'success': False, 'error': 'Missing required fields'}, status=400)

            # Get physical_qty from TotalStockModel
            total_stock_obj = TotalStockModel.objects.filter(lot_id=lot_id).first()
            physical_qty = total_stock_obj.iqf_physical_qty if total_stock_obj and total_stock_obj.iqf_physical_qty else 0

            # Calculate total entered rejection qty
            total_entered_qty = sum(int(item['quantity']) for item in tray_rejections if int(item['quantity']) > 0)

            is_brass_rejection = False
            if total_entered_qty == 0:
                use_audit = getattr(total_stock_obj, 'send_brass_audit_to_iqf', False)
                if use_audit:
                    brass_rejection_store = Brass_Audit_Rejection_ReasonStore.objects.filter(lot_id=lot_id).first()
                    brass_rejected_trays = Brass_Audit_Rejected_TrayScan.objects.filter(lot_id=lot_id)
                else:
                    brass_rejection_store = Brass_QC_Rejection_ReasonStore.objects.filter(lot_id=lot_id).first()
                    brass_rejected_trays = Brass_QC_Rejected_TrayScan.objects.filter(lot_id=lot_id)
                if brass_rejection_store and brass_rejection_store.total_rejection_quantity > 0:
                    total_entered_qty = brass_rejection_store.total_rejection_quantity
                    is_brass_rejection = True
                    reason_qty = {}
                    for tray in brass_rejected_trays:
                        reason_id = tray.rejection_reason.rejection_reason_id
                        qty = int(tray.rejected_tray_quantity or 0)
                        reason_qty[reason_id] = reason_qty.get(reason_id, 0) + qty
                    tray_rejections = [{'reason_id': rid, 'quantity': str(qty)} for rid, qty in reason_qty.items()]

            # If all qty is rejected, skip acceptance remarks and save as full rejection
            if physical_qty == total_entered_qty or is_brass_rejection:
                # Save as full lot rejection
                total_stock_obj.iqf_rejection = True
                total_stock_obj.last_process_module = "IQF"
                total_stock_obj.next_process_module = "Jig Loading"
                total_stock_obj.iqf_last_process_date_time = timezone.now()
                total_stock_obj.save(update_fields=['iqf_rejection', 'iqf_last_process_date_time','last_process_module', 'next_process_module'])

                # Save IQF_Rejection_ReasonStore entry
                reason_ids = [item['reason_id'] for item in tray_rejections if int(item['quantity']) > 0]
                reasons = IQF_Rejection_Table.objects.filter(rejection_reason_id__in=reason_ids)
                reason_store = IQF_Rejection_ReasonStore.objects.create(
                    lot_id=lot_id,
                    user=request.user,
                    total_rejection_quantity=total_entered_qty,
                    batch_rejection=True
                )
                reason_store.rejection_reason.set(reasons)

                # ✅ ENHANCED: Save tray-wise rejection records inheriting tray IDs from Brass QC
                use_audit = getattr(total_stock_obj, 'send_brass_audit_to_iqf', False)
                
                if use_audit:
                    brass_rejected_trays = Brass_Audit_Rejected_TrayScan.objects.filter(lot_id=lot_id).order_by('id')
                else:
                    brass_rejected_trays = Brass_QC_Rejected_TrayScan.objects.filter(
                        lot_id=lot_id,
                        rejected_tray_id__isnull=False
                    ).exclude(rejected_tray_id='').order_by('id')
                
                print(f"🔍 [IQF Full Lot Rejection] Found {brass_rejected_trays.count()} Brass QC rejected trays")
                
                # ✅ ENHANCED: Inherit tray IDs for full lot rejection records
                brass_tray_iter = iter(brass_rejected_trays)
            
                for idx, item in enumerate(tray_rejections):
                    # ensure numeric qty
                    try:
                        qty = int(item.get('quantity', 0))
                    except Exception:
                        qty = 0

                    # find the rejection reason object (accept either id or rejection_reason_id)
                    reason_obj = None
                    reason_id = item.get('reason_id') or item.get('rejection_reason_id') or item.get('rejection_reason')
                    if reason_id:
                        reason_obj = IQF_Rejection_Table.objects.filter(rejection_reason_id=reason_id).first() or IQF_Rejection_Table.objects.filter(id=reason_id).first()

                    # tray_id may be supplied; otherwise inherit from brass rejected trays
                    tray_id = item.get('tray_id') or None
                    top_tray_flag = bool(item.get('top_tray', False))
                    if not tray_id:
                        try:
                            brass_tray = next(brass_tray_iter)
                            tray_id = getattr(brass_tray, 'tray_id', None)
                            # inherit top_tray flag if present from brass_tray
                            top_tray_flag = bool(getattr(brass_tray, 'top_tray', top_tray_flag))
                        except StopIteration:
                            tray_id = None

                    # ensure top_tray is a boolean and never None (DB has NOT NULL constraint)
                    if top_tray_flag is None:
                        top_tray_flag = False

                    IQF_Rejected_TrayScan.objects.create(
                        lot_id=lot_id,
                        tray_id=tray_id,
                        rejected_tray_quantity=str(qty),
                        rejection_reason=reason_obj,
                        user=request.user,
                        top_tray=top_tray_flag
                    )
                    return Response({'success': True, 'message': 'Full lot rejection saved.'})

            # Else, require acceptance remarks and save as few cases acceptance
            if total_entered_qty > 0 and not acceptance_remarks:
                return Response({'success': False, 'error': 'Acceptance remarks are required.'}, status=400)

            # Save rejection reasons and qty (no tray_id) in IQF_Rejection_ReasonStore
            reason_ids = [item['reason_id'] for item in tray_rejections if int(item['quantity']) > 0]
            reasons = IQF_Rejection_Table.objects.filter(rejection_reason_id__in=reason_ids)
            total_qty = sum(int(item['quantity']) for item in tray_rejections if int(item['quantity']) > 0)
            reason_store = IQF_Rejection_ReasonStore.objects.create(
                lot_id=lot_id,
                user=request.user,
                total_rejection_quantity=total_qty,
                batch_rejection=False
            )
            reason_store.rejection_reason.set(reasons)

            # ✅ ENHANCED: Get existing Brass QC rejection data to inherit tray IDs
            total_stock_obj = TotalStockModel.objects.filter(lot_id=lot_id).first()
            use_audit = getattr(total_stock_obj, 'send_brass_audit_to_iqf', False)
            
            if use_audit:
                brass_rejected_trays = Brass_Audit_Rejected_TrayScan.objects.filter(lot_id=lot_id).order_by('id')
            else:
                brass_rejected_trays = Brass_QC_Rejected_TrayScan.objects.filter(
                    lot_id=lot_id,
                    rejected_tray_id__isnull=False
                ).exclude(rejected_tray_id='').order_by('id')
            
            print(f"🔍 [IQF Tray Rejection] Found {brass_rejected_trays.count()} Brass QC rejected trays with tray IDs")
            
            # ✅ ENHANCED: Save individual IQF_Rejected_TrayScan records inheriting tray IDs from Brass QC
            brass_tray_iter = iter(brass_rejected_trays)
            
            for idx, item in enumerate(tray_rejections):
                # ensure numeric qty
                try:
                    qty = int(item.get('quantity', 0))
                except Exception:
                    qty = 0

                # find the rejection reason object (accept either id or rejection_reason_id)
                reason_obj = None
                reason_id = item.get('reason_id') or item.get('rejection_reason_id') or item.get('rejection_reason')
                if reason_id:
                    reason_obj = IQF_Rejection_Table.objects.filter(rejection_reason_id=reason_id).first() or IQF_Rejection_Table.objects.filter(id=reason_id).first()

                # tray_id may be supplied; otherwise inherit from brass rejected trays
                tray_id = item.get('tray_id') or None
                top_tray_flag = bool(item.get('top_tray', False))
                if not tray_id:
                    try:
                        brass_tray = next(brass_tray_iter)
                        tray_id = getattr(brass_tray, 'tray_id', None)
                        # inherit top_tray flag if present from brass_tray
                        top_tray_flag = bool(getattr(brass_tray, 'top_tray', top_tray_flag))
                    except StopIteration:
                        tray_id = None

                # ensure top_tray is a boolean and never None (DB has NOT NULL constraint)
                if top_tray_flag is None:
                    top_tray_flag = False

                IQF_Rejected_TrayScan.objects.create(
                    lot_id=lot_id,
                    tray_id=tray_id,
                    rejected_tray_quantity=str(qty),
                    rejection_reason=reason_obj,
                    user=request.user,
                    top_tray=top_tray_flag
                )            # ✅ Top tray marking is now handled during creation above

            # Save accepted tray IDs and qty and acceptance remarks in IQF_Accepted_TrayID_Store
            for tray in accepted_trays:
                IQF_Accepted_TrayID_Store.objects.create(
                    lot_id=lot_id,
                    tray_id=tray['tray_id'],
                    tray_qty=tray['qty'],
                    user=request.user,
                    accepted_comment=acceptance_remarks
                )
                batch_obj = ModelMasterCreation.objects.filter(batch_id=batch_id).first()
                tray_obj = TrayId.objects.filter(tray_id=tray['tray_id']).first()
                tray_type = tray_obj.tray_type if tray_obj else None
                tray_capacity = tray_obj.tray_capacity if tray_obj else None
                iqf_tray, created = IQFTrayId.objects.get_or_create(
                    tray_id=tray['tray_id'],
                    lot_id=lot_id,
                    defaults={
                        'batch_id': batch_obj,
                        'tray_quantity': tray['qty'],
                        'tray_capacity': tray_capacity,
                        'tray_type': tray_type,
                        'rejected_tray': False,
                    }
                )
                if not created:
                    iqf_tray.lot_id = lot_id
                    iqf_tray.batch_id = batch_obj
                    iqf_tray.tray_quantity = tray['qty']
                    iqf_tray.tray_capacity = tray_capacity
                    iqf_tray.tray_type = tray_type
                    iqf_tray.rejected_tray = False
                    iqf_tray.save(update_fields=['lot_id', 'batch_id', 'tray_quantity', 'tray_capacity', 'tray_type', 'rejected_tray'])
                if tray_obj:
                    tray_obj.lot_id = lot_id
                    tray_obj.batch_id = batch_obj
                    tray_obj.tray_quantity = tray['qty']
                    tray_obj.tray_capacity = tray_capacity
                    tray_obj.tray_type = tray_type
                    tray_obj.rejected_tray = False
                    tray_obj.save(update_fields=['lot_id', 'batch_id', 'tray_quantity', 'tray_capacity', 'tray_type', 'rejected_tray'])

            # Update TotalStockModel fields for few cases acceptance
            if total_stock_obj:
                total_stock_obj.iqf_few_cases_acceptance = True
                total_stock_obj.iqf_onhold_picking = True
                total_stock_obj.iqf_last_process_date_time = timezone.now()
                total_stock_obj.iqf_accepted_qty = total_stock_obj.iqf_physical_qty - total_qty
                total_stock_obj.save(update_fields=[
                    'iqf_few_cases_acceptance', 'iqf_onhold_picking', 'iqf_accepted_qty', 'iqf_last_process_date_time'
                ])


            return Response({'success': True, 'message': 'Tray rejection and acceptance saved.'})

        except Exception as e:
            import traceback
            traceback.print_exc()
            return Response({'success': False, 'error': str(e)}, status=500)

@require_GET
def iqf_reject_check_tray_id(request):
    tray_id = request.GET.get('tray_id', '')
    exists = TrayId.objects.filter(tray_id=tray_id).exists()
    return JsonResponse({'exists': exists})

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def iqf_get_accepted_tray_scan_data(request):
    lot_id = request.GET.get('lot_id')
    
    
    if not lot_id:
        return Response({'success': False, 'error': 'Missing lot_id'}, status=400)
    try:
        # Get rejection details
        reason_store = IQF_Rejection_ReasonStore.objects.filter(lot_id=lot_id).order_by('-id').first()
        rejection_details = []
        batch_rejection = False
        lot_rejected_comment = ""
        total_rejection_qty = 0
        if reason_store:
            batch_rejection = reason_store.batch_rejection
            lot_rejected_comment = reason_store.lot_rejected_comment or ""
            total_rejection_qty = reason_store.total_rejection_quantity
            for reason in reason_store.rejection_reason.all():
                rejection_details.append({
                    'reason': reason.rejection_reason,
                    'reason_id': reason.rejection_reason_id,
                    'qty': total_rejection_qty
                })



        # Get model_no and tray_capacity from TotalStockModel
        stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
        model_no = ""
        tray_capacity = 0
        total_stock = 0
        if stock:
            model_no = stock.model_stock_no.model_no if stock.model_stock_no else ""
            tray_capacity = stock.batch_id.tray_capacity if stock.batch_id and hasattr(stock.batch_id, 'tray_capacity') else 0
            total_stock = stock.iqf_physical_qty if stock.iqf_physical_qty and stock.iqf_physical_qty > 0 else stock.brass_qc_accepted_qty or 0



        return Response({
            'success': True,
            'rejection_details': rejection_details,
            'batch_rejection': batch_rejection,
            'lot_rejected_comment': lot_rejected_comment,
            'total_rejection_qty': total_rejection_qty,
            'model_no': model_no,
            'tray_capacity': tray_capacity,
        })
    except Exception as e:
        return Response({'success': False, 'error': str(e)}, status=500)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def iqf_view_tray_list(request):
    """
    Returns tray list for a given lot_id based on different conditions:
    1. If iqf_acceptance is True: get from TrayId table
    2. If batch_rejection is True: split total_rejection_quantity by tray_capacity and get tray_ids from TrayId
    3. If batch_rejection is False: return all trays from IQF_Accepted_TrayID_Store
    """
    lot_id = request.GET.get('lot_id')
    if not lot_id:
        return Response({'success': False, 'error': 'Missing lot_id'}, status=400)

    try:
        # Check if this lot has iqf_acceptance = True
        stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
        iqf_acceptance = False
        tray_capacity = 0
        
        if stock:
            iqf_acceptance = stock.iqf_acceptance or False
            if stock.batch_id and hasattr(stock.batch_id, 'tray_capacity'):
                tray_capacity = stock.batch_id.tray_capacity or 0

        tray_list = []

        # Condition 1: If iqf_acceptance is True, get from TrayId table
        if iqf_acceptance:
            trays = TrayId.objects.filter(lot_id=lot_id).order_by('id')
            for idx, tray_obj in enumerate(trays):
                tray_list.append({
                    'sno': idx + 1,
                    'tray_id': tray_obj.tray_id,
                    'tray_qty': tray_obj.tray_quantity,  # Assuming this field exists in TrayId model
                })
            
            return Response({
                'success': True,
                'iqf_acceptance': True,
                'batch_rejection': False,
                'total_rejection_qty': 0,
                'tray_capacity': tray_capacity,
                'trays': tray_list,
            })

        # Condition 2 & 3: Check rejection reason store (existing logic)
        reason_store = IQF_Rejection_ReasonStore.objects.filter(lot_id=lot_id).order_by('-id').first()
        batch_rejection = False
        total_rejection_qty = 0
        
        if reason_store:
            batch_rejection = reason_store.batch_rejection
            total_rejection_qty = reason_store.total_rejection_quantity

        if batch_rejection and total_rejection_qty > 0 and tray_capacity > 0:
            # Batch rejection: split total_rejection_qty by tray_capacity, get tray_ids from TrayId
            tray_ids = list(TrayId.objects.filter(lot_id=lot_id).values_list('tray_id', flat=True))
            num_trays = ceil(total_rejection_qty / tray_capacity)
            qty_left = total_rejection_qty
            
            for i in range(num_trays):
                qty = tray_capacity if qty_left > tray_capacity else qty_left
                tray_id = tray_ids[i] if i < len(tray_ids) else ""
                tray_list.append({
                    'sno': i + 1,
                    'tray_id': tray_id,
                    'tray_qty': qty,
                })
                qty_left -= qty
        else:
            # Not batch rejection: get from IQF_Accepted_TrayID_Store
            trays = IQF_Accepted_TrayID_Store.objects.filter(lot_id=lot_id).order_by('id')
            for idx, obj in enumerate(trays):
                tray_list.append({
                    'sno': idx + 1,
                    'tray_id': obj.tray_id,
                    'tray_qty': obj.tray_qty,
                })

        return Response({
            'success': True,
            'iqf_acceptance': iqf_acceptance,
            'batch_rejection': batch_rejection,
            'total_rejection_qty': total_rejection_qty,
            'tray_capacity': tray_capacity,
            'trays': tray_list,
        })
        
    except Exception as e:
        return Response({'success': False, 'error': str(e)}, status=500)


@method_decorator(login_required, name='dispatch')
@method_decorator(csrf_exempt, name='dispatch')
class IQFTrayValidateAPIView(APIView):
    def post(self, request):
        try:
            # Parse request data
            data = request.data if hasattr(request, 'data') else json.loads(request.body.decode('utf-8'))
            
            # Get parameters
            lot_id_input = str(data.get('batch_id', '') or data.get('lot_id', '')).strip()
            tray_id = str(data.get('tray_id', '')).strip()
            
            print("="*50)
            print(f"[DEBUG] Raw request data: {data}")
            print(f"[DEBUG] Extracted lot_id: '{lot_id_input}' (length: {len(lot_id_input)})")
            print(f"[DEBUG] Extracted tray_id: '{tray_id}' (length: {len(tray_id)})")
            
            if not lot_id_input or not tray_id:
                return JsonResponse({
                    'success': False,
                    'error': 'Both lot_id and tray_id are required'
                }, status=400)

            # Step 1: Check if lot_id exists in ModelMasterCreation (optional validation)
            print(f"[DEBUG] Checking if lot_id exists in ModelMasterCreation: '{lot_id_input}'")
            try:
                model_master_creation = ModelMasterCreation.objects.get(lot_id=lot_id_input)
                print(f"[DEBUG] Found ModelMasterCreation: batch_id='{model_master_creation.batch_id}', lot_id='{model_master_creation.lot_id}'")
            except ModelMasterCreation.DoesNotExist:
                print(f"[DEBUG] No ModelMasterCreation found with lot_id: '{lot_id_input}'")
                # Continue anyway since we're checking TrayId which uses lot_id directly

            # Step 2: Check if the tray exists in TrayId for this lot_id
            print(f"[DEBUG] Checking if tray '{tray_id}' exists in TrayId for lot_id: '{lot_id_input}'")
            
            tray_exists = TrayId.objects.filter(
                lot_id=lot_id_input,  # Use lot_id directly
                tray_id=tray_id
            ).exists()
            
            print(f"[DEBUG] Tray exists in TrayId: {tray_exists}")
            
            # Additional debugging: show all trays for this lot_id in TrayId
            all_trays = TrayId.objects.filter(
                lot_id=lot_id_input
            ).values_list('tray_id', flat=True)
            print(f"[DEBUG] All trays in TrayId for lot_id '{lot_id_input}': {list(all_trays)}")
            
            # Also check if tray exists anywhere in TrayId (for debugging)
            tray_anywhere = TrayId.objects.filter(tray_id=tray_id)
            if tray_anywhere.exists():
                tray_lot_ids = list(tray_anywhere.values_list('lot_id', flat=True))
                print(f"[DEBUG] Tray '{tray_id}' found in TrayId for lot_ids: {tray_lot_ids}")
            
            print(f"[DEBUG] Final result - exists: {tray_exists}")
            
            # ----------------------------
            # CLASSIFY TRAY FIRST (minimal, correct fix)
            # ----------------------------
            # ----------------------------
            # 1. CLASSIFY TRAY
            # ----------------------------
            # Get TrayId (Global Master)
            tray_id_obj = TrayId.objects.filter(tray_id=tray_id).first()
            # Get IQFTrayId (Lot Specific)
            ip_tray_obj = IQFTrayId.objects.filter(lot_id=lot_id_input, tray_id=tray_id).first()
            
            print(f"\n[IQF VALIDATION DEBUG] Start Validation --------------------------------")
            print(f"[IQFTrayValidate] Request Tray: {tray_id}, Lot: {lot_id_input}")
            
            # Definition: New Tray = Exists in Master AND (No lot assigned OR Marked as new_tray)
            is_free_tray = bool(tray_id_obj and not tray_id_obj.lot_id)
            is_marked_new = bool(tray_id_obj and getattr(tray_id_obj, 'new_tray', False))
            is_new_tray = (ip_tray_obj is None) and (is_free_tray or is_marked_new)

            # Definition: Existing Tray = Already in IQFTrayId for this lot
            is_existing_tray = bool(ip_tray_obj)
            
            # Check status of existing tray
            is_empty_existing_tray = False
            is_occupied_existing_tray = False
            tray_qty_db = 0
            
            if is_existing_tray:
                tray_qty_db = getattr(ip_tray_obj, 'tray_quantity', 0)
                if tray_qty_db > 0:
                    is_occupied_existing_tray = True
                else:
                    is_empty_existing_tray = True
            
            print(f"[IQFTrayValidate] TRAY STATUS:")
            print(f"  - In IQFTrayId (Existing)? {is_existing_tray}")
            print(f"  - In TrayId (Master)? {bool(tray_id_obj)}")
            if tray_id_obj:
                 print(f"    -> Master Lot: '{tray_id_obj.lot_id}'")
            print(f"  - Qty in DB: {tray_qty_db}")
            print(f"  - Flags: New={is_new_tray}, Empt={is_empty_existing_tray}, Occ={is_occupied_existing_tray}")

            # ----------------------------
            # 2. CALCULATE REUSE LIMITS (Always calculate for visibility)
            # ----------------------------
            remaining_reuse_slots = 0
            used_reusable_trays = set()
            try:
                # Parse optional session allocations
                current_session_allocations = []
                try:
                    alloc_raw = data.get('current_session_allocations', [])
                    if isinstance(alloc_raw, str):
                        current_session_allocations = json.loads(alloc_raw)
                    elif isinstance(alloc_raw, list):
                        current_session_allocations = alloc_raw
                except Exception:
                    current_session_allocations = []

                # Get Tray Capacity
                tray_capacity = 0
                if ip_tray_obj:
                     tray_capacity = getattr(ip_tray_obj, 'tray_capacity', 0)
                
                if not tray_capacity:
                    ts = TotalStockModel.objects.filter(lot_id=lot_id_input).first()
                    if ts and ts.batch_id:
                        tray_capacity = getattr(ts.batch_id, 'tray_capacity', 12) or 12
                    else:
                        tray_capacity = 12

                # Get Total Rejected Quantity (Not used in new formula, but kept for reference if needed, commented out)
                # ts = TotalStockModel.objects.filter(lot_id=lot_id_input).first()
                # use_audit = getattr(ts, 'send_brass_audit_to_iqf', False) if ts else False
                # total_rejected_qty = 0
                
                # --- NEW PHYSICAL CAPACITY FORMULA ---

                # 1. Total Trays (All physical trays for the lot)
                non_rejected_trays_qs = IQFTrayId.objects.filter(lot_id=lot_id_input)
                total_tray_count = non_rejected_trays_qs.count()

                # 2. Total Quantity currently present (Sum of tray_quantity)
                total_qty_agg = non_rejected_trays_qs.aggregate(total_sum=Sum('tray_quantity'))
                total_qty = total_qty_agg.get('total_sum', 0) or 0

                # ✅ FALLBACK: When no IQFTrayId records exist yet (initial rejection entry)
                frontend_total_iqf_qty = int(request.GET.get('total_iqf_qty', 0))
                
                if total_tray_count == 0 and frontend_total_iqf_qty > 0:
                    ts_fb = TotalStockModel.objects.filter(lot_id=lot_id_input).first()
                    if ts_fb:
                        physical_qty = getattr(ts_fb, 'iqf_physical_qty', 0) or 0
                        if not physical_qty:
                            physical_qty = getattr(ts_fb, 'quantity', 0) or 0
                        
                        if physical_qty > 0 and tray_capacity > 0:
                            total_tray_count = math.ceil(physical_qty / tray_capacity)
                            total_qty = physical_qty
                            print(f"  [IQF FALLBACK] No IQFTrayId records. physical_qty={physical_qty}, estimated_trays={total_tray_count}")

                # 3. Get IQF Rejections (Store + Draft)
                iqf_rejected_qty = 0
                
                # A. Committed Rejections
                iqf_rejection_record = IQF_Rejection_ReasonStore.objects.filter(lot_id=lot_id_input).order_by('-id').first()
                if iqf_rejection_record:
                    iqf_rejected_qty += iqf_rejection_record.total_rejection_quantity or 0

                # B. Draft Rejections
                draft_rejected_qty = 0
                draft_record = IQF_Draft_Store.objects.filter(lot_id=lot_id_input, draft_type='tray_rejection').first()
                if draft_record and draft_record.draft_data:
                    try:
                        draft_data = draft_record.draft_data
                        if isinstance(draft_data, str):
                            import json
                            draft_data = json.loads(draft_data)
                        
                        # Sum up rejections in draft
                        tray_rejections = draft_data.get('tray_rejections', [])
                        for rej in tray_rejections:
                            draft_rejected_qty += int(rej.get('qty', 0) or 0)
                    except Exception as e:
                        print(f"Error parsing draft rejection data: {e}")

                total_iqf_rejected_qty = iqf_rejected_qty + draft_rejected_qty
                
                # If no committed/draft rejections but frontend has rejection qty, use it
                if total_iqf_rejected_qty == 0 and frontend_total_iqf_qty > 0:
                    total_iqf_rejected_qty = frontend_total_iqf_qty

                # 4. Remaining Qty = Total Qty (Physical) - Total IQF Rejected (Committed + Draft)
                remaining_qty = max(0, total_qty - total_iqf_rejected_qty)

                # 5. Trays Needed = ceil(Remaining Qty / Tray Capacity)
                trays_needed = 0
                if tray_capacity > 0:
                     trays_needed = math.ceil(remaining_qty / tray_capacity)

                # 6. Max Reusable Trays
                max_reusable_trays = max(0, total_tray_count - trays_needed)

                print(f"  REUSE CALCULATION (Physical Formula - Updated with IQF_Draft_Store):")
                print(f"  - Total Tray Count: {total_tray_count}")
                print(f"  - Total Quantity (Physical Sum): {total_qty}")
                print(f"  - IQF Rejected Qty (Store): {iqf_rejected_qty}")
                print(f"  - IQF Rejected Qty (Draft): {draft_rejected_qty}")
                print(f"  - Remaining Qty: {remaining_qty}")
                print(f"  - Trays Needed: {trays_needed} (ceil({remaining_qty}/{tray_capacity}))")
                print(f"  = Max Reusable Trays: {max_reusable_trays} ({total_tray_count} - {trays_needed})")
                max_reusable_trays = max(0, total_tray_count - trays_needed)
                
                # Count ALREADY used reusable trays in session
                for alloc in current_session_allocations:
                    t_ids = alloc.get('tray_ids', [])
                    if isinstance(t_ids, str): t_ids = [t_ids]
                    
                    for t_id in t_ids:
                        if not t_id: continue
                        # Check if this tray is an EXISTING tray for this lot
                        t_ip_obj = IQFTrayId.objects.filter(tray_id=t_id, lot_id=lot_id_input).first()
                        if t_ip_obj:
                            # If it's the specific top-most working tray (optional check, but logic says ANY empty tray is reusable)
                            # Actually, user logic implies we just count reuses.
                            # We should check if t_id was originally empty? No, session allocation implies we just filled it.
                            # Let's count it if it's being used as a reuse tray.
                            used_reusable_trays.add(t_id)

                used_reusable_count = len(used_reusable_trays)
                remaining_reuse_slots = max(0, max_reusable_trays - used_reusable_count)
                    
                print(f"  REUSE CALCULATION (Physical Formula):")
                print(f"  - Total Tray Count: {total_tray_count}")
                print(f"  - Total Quantity (Sum): {total_qty}")
                print(f"  - Total Rejected Qty: {total_rejected_qty}")
                print(f"  - Remaining Qty: {remaining_qty}")
                print(f"  - Tray Capacity: {tray_capacity}")
                print(f"  - Trays Needed: {trays_needed} (ceil({remaining_qty}/{tray_capacity}))")
                print(f"  = Max Reusable Trays: {max_reusable_trays} ({total_tray_count} - {trays_needed})")
                print(f"  - Used Reusable Count: {used_reusable_count}")
                print(f"  = Remaining Slots: {remaining_reuse_slots}")

            except Exception as e:
                print(f"  Error Calculating Reuse Limit: {e}")
                import traceback
                traceback.print_exc()

            # ----------------------------
            # 3. HARD RULES
            # ----------------------------
            
            # RULE A: Occupied existing trays are NEVER allowed for reuse/rejection
            if is_occupied_existing_tray:
                # Check if it's "Occupied"
                if tray_qty_db > 0:
                    if remaining_reuse_slots > 0:
                        # ALLOW REUSE: Physical capacity logic says we have room to empty a tray.
                        print(f"  [IQF Reuse Validation] ALLOWING Occupied Tray {tray_id} (Reuse Slots Available: {remaining_reuse_slots})")
                        # Proceed. This tray counts as one reuse instance.
                        return JsonResponse({
                            'exists': True,
                            'success': True,
                            'is_valid': True,
                            'status_message': 'Available (can rearrange)',
                            'validation_type': 'reuse_occupied', 
                            'tray_capacity': tray_capacity,
                            'current_quantity': tray_qty_db
                        })
                    else:
                        # BLOCK: No reuse slots left, and tray is occupied.
                        print(f"  [IQF Reuse Validation] BLOCKING: Tray is Occupied (Qty: {tray_qty_db}) and No Reuse Slots ({remaining_reuse_slots})")
                        return JsonResponse({
                            'exists': True,
                            'success': False,
                            'valid_for_rejection': False,
                            'error': 'Tray still contains material. Reuse limit reached.',
                            'status_message': 'Tray Not Empty. Reuse Exhausted.'
                        })
            
            # RULE B: New trays are ALWAYS allowed (Logic: Reuse limit does not apply to new trays)
            if is_new_tray:
                print(f"[IQFTrayValidate] ALLOWING: Valid New Tray")
                if tray_id_obj and tray_id_obj.lot_id and str(tray_id_obj.lot_id) != str(lot_id_input):
                     print(f"[IQFTrayValidate] WARNING: Tray assigned to other lot '{tray_id_obj.lot_id}'")
                     return JsonResponse({
                        'exists': False,
                        'success': False,
                        'valid_for_rejection': False,
                        'status_message': 'Tray assigned to other lot'
                    })
                    
                return JsonResponse({
                    'exists': True,
                    'success': True,
                    'valid_for_rejection': True,
                    'status_message': 'New tray allowed',
                    'validation_type': 'new_tray'
                })

            # ----------------------------
            # 4. REUSE LOGIC (Only for Existing Empty Trays)
            # ----------------------------
            if is_empty_existing_tray:
                print(f"[IQFTrayValidate] CHECKING REUSE for Empty Existing Tray...")
                
                # CHECK: Is this specific tray (tray_id) already one of the used ones?
                # If so, it's allowed (already counted). 
                if tray_id in used_reusable_trays:
                        print(f"  RESULT: Allowed (Already in use session)")
                        pass # Allowed, already accounted for
                elif remaining_reuse_slots <= 0:
                    # FAIL: Limit reached and this is a NEW addition to the reuse set
                    print(f"  RESULT: BLOCKED (Reuse limit reached)")
                    return JsonResponse({
                        'exists': True,
                        'valid_for_rejection': False,
                        'error': 'Reuse limit reached for existing trays',
                        'status_message': 'Tray Not Empty. Reuse Exhausted.'
                    })
                else:
                    print(f"  RESULT: Allowed (Slots available)")

                # If logic passes:
                return JsonResponse({
                    'exists': True,
                    'success': True,
                    'valid_for_rejection': True,
                    'status_message': 'Tray accepted (Reusable)',
                    'validation_type': 'reuse_existing'
                })
            
        except Exception as e:
            print(f"[DEBUG] ERROR: {str(e)}")
            import traceback
            traceback.print_exc()
            return JsonResponse({
                'success': False, 
                'error': str(e)
            }, status=500)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def IQF_check_accepted_tray_draft(request):
    """Check if draft data exists for accepted tray scan"""
    lot_id = request.GET.get('lot_id')
    if not lot_id:
        return Response({'success': False, 'error': 'Missing lot_id'}, status=400)
    
    try:
        has_draft = IQF_Accepted_TrayID_Store.objects.filter(
            lot_id=lot_id, 
            is_draft=True
        ).exists()
        
        return Response({
            'success': True,
            'has_draft': has_draft
        })
    except Exception as e:
        return Response({'success': False, 'error': str(e)}, status=500)



@require_GET
def iqf_check_tray_id(request):
    tray_id = request.GET.get('tray_id', '')
    lot_id = request.GET.get('lot_id', '')  # This is your stock_lot_id

    # 1. Must exist in TrayId table and lot_id must match
    tray_obj = TrayId.objects.filter(tray_id=tray_id).first()
    exists = bool(tray_obj)
    same_lot = exists and str(tray_obj.lot_id) == str(lot_id)

    # 2. Must NOT be in IQF_Rejected_TrayScan for this lot
    already_rejected = False
    if exists and same_lot and lot_id:
        already_rejected = IQF_Rejected_TrayScan.objects.filter(
            lot_id=lot_id,
        ).exists()

    # Only valid if exists, same lot, and not already rejected
    is_valid = exists and same_lot and not already_rejected

    return JsonResponse({
        'exists': is_valid,
        'already_rejected': already_rejected,
        'not_in_same_lot': exists and not same_lot
    })




class Cast(Func):
    function = 'CAST'
    template = '%(expressions)s::integer'
    output_field = IntegerField()

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def iqf_get_rejected_tray_scan_data(request):
    lot_id = request.GET.get('lot_id')
    if not lot_id:
        return Response({'success': False, 'error': 'Missing lot_id'}, status=400)
    try:
        rejection_rows = []
        
        # STEP 1: Get ALL unique brass rejection reasons for this lot_id first
        all_brass_reasons = Brass_QC_Rejected_TrayScan.objects.filter(
            lot_id=lot_id
        ).values(
            'rejection_reason__rejection_reason_id',
            'rejection_reason__rejection_reason'
        ).distinct()

        # STEP 2: For each brass reason, get brass qty and find corresponding IQF data
        for brass_reason in all_brass_reasons:
            reason_id = brass_reason['rejection_reason__rejection_reason_id']
            reason_text = brass_reason['rejection_reason__rejection_reason']
            
            # Get brass rejection quantity for this reason
            brass_qty = Brass_QC_Rejected_TrayScan.objects.filter(
                lot_id=lot_id,
                rejection_reason__rejection_reason_id=reason_id
            ).aggregate(
                total=Sum(Cast('rejected_tray_quantity', output_field=IntegerField()))
            )['total'] or 0
            
            # Try to find corresponding IQF data - first check tray-wise rejections
            iqf_qty = 0
            tray_id = ''
            iqf_qty = IQF_Rejected_TrayScan.objects.filter(
                lot_id=lot_id,
                rejection_reason__rejection_reason_id=reason_id
            ).aggregate(
                total=Sum(Cast('rejected_tray_quantity', output_field=IntegerField()))
            )['total']
            
            if iqf_qty is None or iqf_qty == 0:
                reason_store = IQF_Rejection_ReasonStore.objects.filter(
                    lot_id=lot_id,
                    rejection_reason__rejection_reason_id=reason_id
                ).first()
                if reason_store:
                    iqf_qty = reason_store.total_rejection_quantity or 0
                else:
                    iqf_qty = 0
                    
                    
            # Add to rejection_rows
            rejection_rows.append({
                'tray_id': tray_id,
                'qty': iqf_qty,
                'reason': reason_text,
                'reason_id': reason_id,
                'brass_rejection_qty': brass_qty
            })

        # STEP 3: Handle case where there are no brass rejections but IQF rejections exist
        if not all_brass_reasons.exists():
            # Fallback to original IQF-first logic if no brass data
            tray_rejections = IQF_Rejected_TrayScan.objects.filter(lot_id=lot_id)
            if tray_rejections.exists():
                for obj in tray_rejections:
                    rejection_rows.append({
                        'tray_id': '',
                        'qty': obj.rejected_tray_quantity,
                        'reason': obj.rejection_reason.rejection_reason,
                        'reason_id': obj.rejection_reason.rejection_reason_id,
                        'brass_rejection_qty': 0  # No brass data
                    })
            else:
                # Check IQF_Rejection_ReasonStore
                reason_store = IQF_Rejection_ReasonStore.objects.filter(lot_id=lot_id).order_by('-id').first()
                if reason_store:
                    total_qty = reason_store.total_rejection_quantity
                    for reason in reason_store.rejection_reason.all():
                        rejection_rows.append({
                            'tray_id': '',
                            'qty': total_qty,
                            'reason': reason.rejection_reason,
                            'reason_id': reason.rejection_reason_id,
                            'brass_rejection_qty': 0  # No brass data
                        })

        # Accepted trays (unchanged)
        accepted_trays = []
        for obj in IQF_Accepted_TrayID_Store.objects.filter(lot_id=lot_id):
            accepted_trays.append({
                'tray_id': obj.tray_id,
                'tray_qty': obj.tray_qty,
                'accepted_comment': obj.accepted_comment,
                'is_draft': obj.is_draft,
                'is_save': obj.is_save,
                'user': obj.user.username if obj.user else None,
            })

        # ✅ FIXED: Get rejected tray IDs with top_tray information from BOTH IQF and Brass QC sources
        rejected_tray_ids = []
        
        # Priority 1: Check IQF_Rejected_TrayScan (if IQF has done its own rejection)
        iqf_rejected_trays = IQF_Rejected_TrayScan.objects.filter(lot_id=lot_id).order_by('-top_tray', 'id')
        
        if iqf_rejected_trays.exists():
            # IQF has rejection data
            for tray_obj in iqf_rejected_trays:
                # Check if this tray is marked as top tray in IQFTrayId model as well
                iqf_tray_id_obj = IQFTrayId.objects.filter(lot_id=lot_id, tray_id=tray_obj.tray_id).first()
                is_top_tray = tray_obj.top_tray or (iqf_tray_id_obj and iqf_tray_id_obj.top_tray)
                
                rejected_tray_ids.append({
                    'tray_id': tray_obj.tray_id,
                    'qty': tray_obj.rejected_tray_quantity,
                    'reason': tray_obj.rejection_reason.rejection_reason,
                    'reason_id': tray_obj.rejection_reason.rejection_reason_id,
                    'top_tray': is_top_tray,
                    'user': tray_obj.user.username if tray_obj.user else None,
                })
        else:
            # Priority 2: Check Brass_QC_Rejected_TrayScan (initial rejection from Brass QC)
            brass_rejected_trays = Brass_QC_Rejected_TrayScan.objects.filter(lot_id=lot_id).order_by('-top_tray', 'id')
            
            print(f"   🔍 [iqf_get_rejected_tray_scan_data] Found {brass_rejected_trays.count()} Brass QC rejected trays")
            
            for tray_obj in brass_rejected_trays:
                # ✅ FIXED: Also check BrassTrayId and IQFTrayId for top_tray flag
                brass_tray_id_obj = BrassTrayId.objects.filter(lot_id=lot_id, tray_id=tray_obj.rejected_tray_id).first()
                iqf_tray_id_obj = IQFTrayId.objects.filter(lot_id=lot_id, tray_id=tray_obj.rejected_tray_id).first()
                
                # Use top_tray from any of these sources (priority: Brass_QC_Rejected_TrayScan > BrassTrayId > IQFTrayId)
                is_top_tray = (tray_obj.top_tray or 
                              (brass_tray_id_obj and brass_tray_id_obj.top_tray) or 
                              (iqf_tray_id_obj and iqf_tray_id_obj.top_tray))
                
                # ✅ DEBUG: Log the top_tray value from each source
                print(f"      🔍 Tray: {tray_obj.rejected_tray_id}")
                print(f"         - Brass_QC_Rejected_TrayScan.top_tray = {tray_obj.top_tray}")
                print(f"         - BrassTrayId.top_tray = {brass_tray_id_obj.top_tray if brass_tray_id_obj else 'N/A'}")
                print(f"         - IQFTrayId.top_tray = {iqf_tray_id_obj.top_tray if iqf_tray_id_obj else 'N/A'}")
                print(f"         - Final is_top_tray = {is_top_tray}")
                
                rejected_tray_ids.append({
                    'tray_id': tray_obj.rejected_tray_id,  # Note: Brass QC uses 'rejected_tray_id' field
                    'qty': tray_obj.rejected_tray_quantity,
                    'reason': tray_obj.rejection_reason.rejection_reason if tray_obj.rejection_reason else 'N/A',
                    'reason_id': tray_obj.rejection_reason.rejection_reason_id if tray_obj.rejection_reason else '',
                    'top_tray': is_top_tray,  # ✅ FIXED: Now reads from Brass QC rejection record
                    'user': tray_obj.user.username if tray_obj.user else None,
                })

        print(f"[iqf_get_rejected_tray_scan_data] lot_id={lot_id}")
        print(f"  rejection_rows: {rejection_rows}")
        print(f"  accepted_trays: {accepted_trays}")
        print(f"  rejected_tray_ids: {rejected_tray_ids} (with top_tray from Brass QC/IQF)")  # ✅ Enhanced logging

        return Response({
            'success': True,
            'rejection_rows': rejection_rows,
            'accepted_trays': accepted_trays,
            'rejected_tray_ids': rejected_tray_ids  # ✅ NEW: Include rejected tray IDs with top_tray flag
        })
    except Exception as e:
        print(f"[iqf_get_rejected_tray_scan_data] ERROR: {str(e)}")
        return Response({'success': False, 'error': str(e)}, status=500)


@method_decorator(login_required, name='dispatch')
@method_decorator(csrf_exempt, name='dispatch')
class IQFDeleteBatchAPIView(APIView):
    def post(self, request):
        try:
            data = request.data if hasattr(request, 'data') else json.loads(request.body.decode('utf-8'))
            stock_lot_id = data.get('stock_lot_id')
            if not stock_lot_id:
                return JsonResponse({'success': False, 'error': 'Missing stock_lot_id'}, status=400)
            obj = TotalStockModel.objects.filter(lot_id=stock_lot_id).first()
            if not obj:
                return JsonResponse({'success': False, 'error': 'Stock lot not found'}, status=404)
            obj.delete()
            return JsonResponse({'success': True, 'message': 'Stock lot deleted'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)

@method_decorator(login_required, name='dispatch')
class IQFCompletedTableView(APIView):
    renderer_classes = [TemplateHTMLRenderer]
    template_name = 'IQF/Iqf_Completed.html'

    def get(self, request):
        from django.utils import timezone
        from datetime import datetime, timedelta
        import pytz
        import math

        user = request.user
        
        # ✅ Date filtering logic (unchanged)
        tz = pytz.timezone("Asia/Kolkata")
        now_local = timezone.now().astimezone(tz)
        today = now_local.date()
        yesterday = today - timedelta(days=1)

        from_date_str = request.GET.get('from_date')
        to_date_str = request.GET.get('to_date')

        if from_date_str and to_date_str:
            try:
                from_date = datetime.strptime(from_date_str, '%Y-%m-%d').date()
                to_date = datetime.strptime(to_date_str, '%Y-%m-%d').date()
            except ValueError:
                from_date = yesterday
                to_date = today
        else:
            from_date = yesterday
            to_date = today

        from_datetime = timezone.make_aware(datetime.combine(from_date, datetime.min.time()))
        to_datetime = timezone.make_aware(datetime.combine(to_date, datetime.max.time()))

        # ✅ CHANGED: Query TotalStockModel directly instead of ModelMasterCreation
        iqf_rejection_reasons = IQF_Rejection_Table.objects.all()
        
        # ✅ External data subqueries (only for data from other tables)
        brass_rejection_qty_subquery = Brass_QC_Rejection_ReasonStore.objects.filter(
            lot_id=OuterRef('lot_id')
        ).values('total_rejection_quantity')[:1]

        iqf_rejection_qty_subquery = IQF_Rejection_ReasonStore.objects.filter(
            lot_id=OuterRef('lot_id')
        ).values('total_rejection_quantity')[:1]

        queryset = TotalStockModel.objects.select_related(
            'batch_id',
            'batch_id__model_stock_no',
            'batch_id__version',
            'batch_id__location'
        ).filter(
            batch_id__total_batch_quantity__gt=0,
            iqf_last_process_date_time__range=(from_datetime, to_datetime)  # ✅ Direct date filtering
        ).annotate(
            wiping_required=F('batch_id__model_stock_no__wiping_required'),
            brass_rejection_total_qty=brass_rejection_qty_subquery,
            iqf_rejection_qty=iqf_rejection_qty_subquery,
        ).filter(
            # ✅ Direct filtering on TotalStockModel fields (no more subquery filtering)
            Q(iqf_acceptance=True) |
            Q(iqf_rejection=True) |
            (Q(iqf_few_cases_acceptance=True) & Q(iqf_onhold_picking=False))
        ).order_by('-iqf_last_process_date_time', '-lot_id')

        print(f"📊 Found {queryset.count()} IQF completed records in date range {from_date} to {to_date}")
        print("All lot_ids in IQF completed queryset:", list(queryset.values_list('lot_id', flat=True)))

        # Pagination
        page_number = request.GET.get('page', 1)
        paginator = Paginator(queryset, 10)
        page_obj = paginator.get_page(page_number)

        # ✅ UPDATED: Build master_data from TotalStockModel records
        master_data = []
        for stock_obj in page_obj.object_list:
            batch = stock_obj.batch_id
            
            data = {
                # ✅ Batch fields from foreign key
                'batch_id': batch.batch_id,
                'date_time': batch.date_time,
                'model_stock_no__model_no': batch.model_stock_no.model_no,
                'plating_color': batch.plating_color,
                'polish_finish': batch.polish_finish,
                'version__version_name': batch.version.version_name if batch.version else '',
                'vendor_internal': batch.vendor_internal,
                'location__location_name': batch.location.location_name if batch.location else '',
                'tray_type': batch.tray_type,
                'tray_capacity': batch.tray_capacity,
                'Moved_to_D_Picker': batch.Moved_to_D_Picker,
                'Draft_Saved': batch.Draft_Saved,
                'plating_stk_no': batch.plating_stk_no,
                'polishing_stk_no': batch.polishing_stk_no,
                
                # ✅ Stock-related fields from TotalStockModel
                'lot_id': stock_obj.lot_id,
                'stock_lot_id': stock_obj.lot_id,
                'last_process_module': stock_obj.last_process_module,
                'next_process_module': stock_obj.next_process_module,
                'wiping_required': stock_obj.wiping_required,
                'iqf_missing_qty': stock_obj.iqf_missing_qty,
                'iqf_physical_qty': stock_obj.iqf_physical_qty,
                'iqf_physical_qty_edited': stock_obj.iqf_physical_qty_edited,
                'accepted_tray_scan_status': stock_obj.accepted_tray_scan_status,
                'iqf_rejection_qty': stock_obj.iqf_rejection_qty,
                'iqf_accepted_qty': stock_obj.iqf_accepted_qty,
                'IQF_pick_remarks': stock_obj.IQF_pick_remarks,
                'brass_accepted_tray_scan_status': stock_obj.brass_accepted_tray_scan_status,
                'brass_qc_rejection': stock_obj.brass_qc_rejection,
                'brass_rejection_total_qty': stock_obj.brass_rejection_total_qty,
                'brass_qc_few_cases_accptance': stock_obj.brass_qc_few_cases_accptance,
                'iqf_accepted_qty_verified': stock_obj.iqf_accepted_qty_verified,
                'iqf_acceptance': stock_obj.iqf_acceptance,  # ✅ Direct access - will show True correctly
                'iqf_rejection': stock_obj.iqf_rejection,
                'iqf_few_cases_acceptance': stock_obj.iqf_few_cases_acceptance,
                'iqf_onhold_picking': stock_obj.iqf_onhold_picking,
                'iqf_accepted_tray_scan_status': stock_obj.iqf_accepted_tray_scan_status,
                'iqf_last_process_date_time': stock_obj.iqf_last_process_date_time,
                'total_IP_accpeted_quantity': stock_obj.total_IP_accpeted_quantity,
            }
            master_data.append(data)

        print(f"[IQFCompletedTableView] Total master_data records: {len(master_data)}")
        
        # ✅ Process the data with fallback to total_IP_accpeted_quantity
        for data in master_data:
            brass_rejection_total_qty = data.get('brass_rejection_total_qty')
            total_ip_accepted_qty = data.get('total_IP_accpeted_quantity', 0)
            tray_capacity = data.get('tray_capacity')
            
            # ✅ UPDATED: Use total_IP_accpeted_quantity as fallback when brass_rejection_total_qty is None
            if brass_rejection_total_qty is None:
                effective_qty = total_ip_accepted_qty
                print(f"Lot {data['lot_id']}: Using total_IP_accpeted_quantity ({total_ip_accepted_qty}) as fallback")
            else:
                effective_qty = brass_rejection_total_qty
                print(f"Lot {data['lot_id']}: Using brass_rejection_total_qty ({brass_rejection_total_qty})")
            
            data['vendor_location'] = f"{data.get('vendor_internal', '')}_{data.get('location__location_name', '')}"
            
            # Calculate no_of_trays using effective_qty
            if effective_qty is None or tray_capacity is None or tray_capacity == 0:
                data['no_of_trays'] = 0
            else:
                data['no_of_trays'] = math.ceil(effective_qty / tray_capacity)
            
            # ✅ Store the effective quantity for display purposes
            data['display_quantity'] = effective_qty
            data['quantity_source'] = 'total_IP_accpeted_quantity' if brass_rejection_total_qty is None else 'brass_rejection_total_qty'

            # Get model images
            batch_obj = ModelMasterCreation.objects.filter(batch_id=data['batch_id']).first()
            images = []
            if batch_obj:
                model_master = batch_obj.model_stock_no
                for img in model_master.images.all():
                    if img.master_image:
                        images.append(img.master_image.url)
            if not images:
                images = [static('assets/images/imagePlaceholder.png')]
            data['model_images'] = images

            # ✅ UPDATED: Add available_qty logic with fallback
            lot_id = data.get('stock_lot_id')
            total_stock_obj = TotalStockModel.objects.filter(lot_id=lot_id).first()
            if total_stock_obj:
                if total_stock_obj.iqf_physical_qty and total_stock_obj.iqf_physical_qty > 0:
                    data['available_qty'] = total_stock_obj.iqf_physical_qty
                else:
                    # Use effective_qty (either brass_rejection_total_qty or total_IP_accpeted_quantity)
                    data['available_qty'] = effective_qty or 0
            else:
                data['available_qty'] = 0

        print("Processed lot_ids:", [data['stock_lot_id'] for data in master_data])

        context = {
            'master_data': master_data,
            'page_obj': page_obj,
            'paginator': paginator,
            'user': user,
            'iqf_rejection_reasons': iqf_rejection_reasons,
            'from_date': from_date.strftime('%Y-%m-%d'),
            'to_date': to_date.strftime('%Y-%m-%d'),
            'date_filter_applied': bool(from_date_str and to_date_str),
        }
        return Response(context, template_name=self.template_name)

@method_decorator(login_required, name='dispatch')
class IQFRejectTableView(APIView):
    renderer_classes = [TemplateHTMLRenderer]
    template_name = 'IQF/Iqf_RejectTable.html'

    def get(self, request):
        user = request.user
        
        iqf_rejection_total_qty_subquery = IQF_Rejection_ReasonStore.objects.filter(
            lot_id=OuterRef('lot_id')
        ).values('total_rejection_quantity')[:1]
        
        brass_rejection_total_qty_subquery = Brass_QC_Rejection_ReasonStore.objects.filter(
            lot_id=OuterRef('lot_id')
        ).values('total_rejection_quantity')[:1]

        queryset = TotalStockModel.objects.select_related(
            'batch_id',
            'batch_id__model_stock_no',
            'batch_id__version',
            'batch_id__location'
        ).filter(
            batch_id__total_batch_quantity__gt=0
        ).annotate(
            iqf_rejection_total_qty=iqf_rejection_total_qty_subquery,
            brass_rejection_total_qty=brass_rejection_total_qty_subquery
        ).filter(
            Q(iqf_rejection=True,iqf_onhold_picking=False) |
            Q(iqf_few_cases_acceptance=True,iqf_onhold_picking=False)
        ).order_by('-iqf_last_process_date_time', '-lot_id')

        print(f"📊 Found {queryset.count()} IQF rejected records")
        print("All lot_ids in IQF reject queryset:", list(queryset.values_list('lot_id', flat=True)))

        # Pagination
        page_number = request.GET.get('page', 1)
        paginator = Paginator(queryset, 10)
        page_obj = paginator.get_page(page_number)

        master_data = []
        for stock_obj in page_obj.object_list:
            batch = stock_obj.batch_id
            
            data = {
                'batch_id': batch.batch_id,
                'date_time': batch.date_time,
                'model_stock_no__model_no': batch.model_stock_no.model_no,
                'plating_color': batch.plating_color,
                'polish_finish': batch.polish_finish,
                'version__version_name': batch.version.version_name if batch.version else '',
                'vendor_internal': batch.vendor_internal,
                'location__location_name': batch.location.location_name if batch.location else '',
                'tray_type': batch.tray_type,
                'tray_capacity': batch.tray_capacity,
                'Moved_to_D_Picker': batch.Moved_to_D_Picker,
                'Draft_Saved': batch.Draft_Saved,
                'plating_stk_no': batch.plating_stk_no,
                'polishing_stk_no': batch.polishing_stk_no,
                'lot_id': stock_obj.lot_id,
                'stock_lot_id': stock_obj.lot_id,
                'last_process_module': stock_obj.last_process_module,
                'next_process_module': stock_obj.next_process_module,
                'iqf_accepted_qty_verified': stock_obj.iqf_accepted_qty_verified,
                'iqf_rejection': stock_obj.iqf_rejection,
                'iqf_few_cases_acceptance': stock_obj.iqf_few_cases_acceptance,
                'iqf_rejection_total_qty': stock_obj.iqf_rejection_total_qty,
                'iqf_last_process_date_time': stock_obj.iqf_last_process_date_time,
                'brass_rejection_total_qty': stock_obj.brass_rejection_total_qty,
                'iqf_physical_qty': stock_obj.iqf_physical_qty,
                'iqf_missing_qty': stock_obj.iqf_missing_qty,
                'lot_qty': batch.total_batch_quantity if hasattr(batch, 'total_batch_quantity') else 0
            }

            # --- Add lot rejection remarks ---
            stock_lot_id = data.get('stock_lot_id')
            lot_rejected_comment = ""
            if stock_lot_id:
                reason_store = IQF_Rejection_ReasonStore.objects.filter(lot_id=stock_lot_id).first()
                if reason_store:
                    lot_rejected_comment = reason_store.lot_rejected_comment or ""
            data['lot_rejected_comment'] = lot_rejected_comment
            # --- End lot rejection remarks ---

            # Existing logic for tray_id_in_trayid, rejection_reason_letters, etc.
            tray_exists = IQFTrayId.objects.filter(lot_id=stock_lot_id, delink_tray=False).exists()
            data['tray_id_in_trayid'] = tray_exists

            first_letters = []
            data['batch_rejection'] = False

            if stock_lot_id:
                try:
                    rejection_record = IQF_Rejection_ReasonStore.objects.filter(
                        lot_id=stock_lot_id
                    ).first()
                    if rejection_record:
                        data['batch_rejection'] = rejection_record.batch_rejection
                        data['iqf_rejection_total_qty'] = rejection_record.total_rejection_quantity
                        reasons = rejection_record.rejection_reason.all()
                        first_letters = [r.rejection_reason.strip()[0].upper() for r in reasons if r.rejection_reason]
                        print(f"✅ Found rejection for {stock_lot_id}: {rejection_record.total_rejection_quantity}")
                    else:
                        if 'iqf_rejection_total_qty' not in data or not data['iqf_rejection_total_qty']:
                            data['iqf_rejection_total_qty'] = 0
                        print(f"⚠️ No rejection record found for {stock_lot_id}")
                except Exception as e:
                    print(f"❌ Error getting rejection for {stock_lot_id}: {str(e)}")
                    data['iqf_rejection_total_qty'] = data.get('iqf_rejection_total_qty', 0)
            else:
                data['iqf_rejection_total_qty'] = 0
                print(f"❌ No stock_lot_id for batch {data.get('batch_id')}")

            data['rejection_reason_letters'] = first_letters

            # Calculate number of trays
            total_stock = data.get('iqf_rejection_total_qty', 0)
            tray_capacity = data.get('tray_capacity', 0)
            data['vendor_location'] = f"{data.get('vendor_internal', '')}_{data.get('location__location_name', '')}"

            if tray_capacity > 0 and total_stock > 0:
                data['no_of_trays'] = math.ceil(total_stock / tray_capacity)
            else:
                data['no_of_trays'] = 0

            # Get model images
            batch_obj = ModelMasterCreation.objects.filter(batch_id=data['batch_id']).first()
            images = []
            if batch_obj:
                model_master = batch_obj.model_stock_no
                for img in model_master.images.all():
                    if img.master_image:
                        images.append(img.master_image.url)
            if not images:
                images = [static('assets/images/imagePlaceholder.png')]
            data['model_images'] = images

            master_data.append(data)

        print("✅ IQF Reject data processing completed")
        print("Processed lot_ids:", [data['stock_lot_id'] for data in master_data])
            
        context = {
            'master_data': master_data,
            'page_obj': page_obj,
            'paginator': paginator,
            'user': user,
        }
        return Response(context, template_name=self.template_name)



@api_view(['GET'])
@permission_classes([IsAuthenticated])
def iqf_get_rejection_details(request):
    lot_id = request.GET.get('lot_id')
    if not lot_id:
        return Response({'success': False, 'error': 'Missing lot_id'}, status=400)
    try:
        reason_store = IQF_Rejection_ReasonStore.objects.filter(lot_id=lot_id).order_by('-id').first()
        if not reason_store:
            return Response({'success': True, 'reasons': []})

        reasons = reason_store.rejection_reason.all()
        total_qty = reason_store.total_rejection_quantity

        if reason_store.batch_rejection:
            if reasons.exists():
                data = [{
                    'reason': r.rejection_reason,
                    'qty': total_qty
                } for r in reasons]
            else:
                # No reasons recorded for batch rejection
                data = [{
                    'reason': 'Batch rejection: No individual reasons recorded',
                    'qty': total_qty
                }]
        else:
            data = [{
                'reason': r.rejection_reason,
                'qty': total_qty
            } for r in reasons]

        return Response({'success': True, 'reasons': data})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return Response({'success': False, 'error': str(e)}, status=500)
    
@require_GET
def iqf_reject_check_tray_id_simple(request):
    """
    Enhanced tray validation for IQF rejections with session allocation awareness
    """
    try:
        tray_id = request.GET.get('tray_id', '').strip()
        lot_id = request.GET.get('lot_id', '').strip()
        rejection_qty = int(request.GET.get('rejection_qty', 0))
        
         # Parse current session allocations
        from urllib.parse import unquote
        import json as _json  # Local import to avoid shadowing
        print(f"[IQF DEBUG] Request Keys: {list(request.GET.keys())}")
        raw_allocations = request.GET.get('current_session_allocations', None)
        print(f"[IQF DEBUG] Raw allocations (from param): {raw_allocations}")
        
        if raw_allocations is None:
             print("[IQF DEBUG] 'current_session_allocations' param is MISSING.")
             current_session_allocations = []
        else:
             try:
                # URL-decode first, then parse JSON
                decoded = unquote(raw_allocations)
                print(f"[IQF DEBUG] After URL decode: {decoded}")
                current_session_allocations = _json.loads(decoded)
                print(f"[IQF DEBUG] Parsed allocations: {current_session_allocations}, Type: {type(current_session_allocations)}")
             except Exception as e:
                print(f"[IQF DEBUG] Failed to parse allocations: {e}")
                current_session_allocations = []

        print(f"[IQF Reject Validation] tray_id: {tray_id}, lot_id: {lot_id}, qty: {rejection_qty}")

        # ----------------------------
        # 1. CLASSIFY TRAY
        # ----------------------------
        # Get TrayId (Global Master)
        tray_id_obj = TrayId.objects.filter(tray_id=tray_id).first()
        # Get IQFTrayId (Lot Specific)
        ip_tray_obj = IQFTrayId.objects.filter(lot_id=lot_id, tray_id=tray_id).first()
        
        # Definition: New Tray = Not in IQFTrayId for this lot, and available for use
        # A tray is "new" if it's not already tracked in IQF for this lot
        # It could be: (a) free/unassigned, (b) marked new, (c) assigned to THIS lot but not yet in IQF
        is_existing_tray = bool(ip_tray_obj)
        
        # If tray is assigned to a DIFFERENT lot, reject early
        if not is_existing_tray and tray_id_obj and tray_id_obj.lot_id and str(tray_id_obj.lot_id) != str(lot_id):
            print(f"[IQF Reject Validation] Tray {tray_id} belongs to different lot: {tray_id_obj.lot_id}")
            return JsonResponse({
                'exists': True,
                'valid_for_rejection': False,
                'is_valid': False,
                'status_message': 'Different Lot',
                'error': f'Tray assigned to lot {tray_id_obj.lot_id}'
            })
        
        is_new_tray = not is_existing_tray  # If not in IQFTrayId, it's new (we already handled different-lot above)
        
        # Check status of existing tray
        is_empty_existing_tray = False
        is_occupied_existing_tray = False
        tray_qty_db = 0
        
        if is_existing_tray:
            tray_qty_db = getattr(ip_tray_obj, 'tray_quantity', 0)
            if tray_qty_db > 0:
                is_occupied_existing_tray = True
            else:
                is_empty_existing_tray = True
        
        print(f"[IQF Reject Validation] Classification: New={is_new_tray}, Existing={is_existing_tray} (Empty={is_empty_existing_tray}, Occupied={is_occupied_existing_tray})")

        # ----------------------------
        # 2. CALCULATE REUSE LIMITS (Always calculate for visibility)
        # ----------------------------
        remaining_reuse_slots = 0
        used_reusable_trays = set()
        try:
             # Get Tray Capacity
            tray_capacity = 0
            if ip_tray_obj:
                    tray_capacity = getattr(ip_tray_obj, 'tray_capacity', 0)
            
            if not tray_capacity:
                ts = TotalStockModel.objects.filter(lot_id=lot_id).first()
                if ts and ts.batch_id:
                    tray_capacity = getattr(ts.batch_id, 'tray_capacity', 12) or 12
                else:
                    tray_capacity = 12

            # Get Total Rejected Quantity (Not used in new formula)
            # ts = TotalStockModel.objects.filter(lot_id=lot_id).first()
            # use_audit = getattr(ts, 'send_brass_audit_to_iqf', False) if ts else False
            # total_rejected_qty = 0
            
            # --- NEW PHYSICAL CAPACITY FORMULA ---
            
            # 1. Total Trays (All physical trays associated to this lot)
            non_rejected_trays_qs = IQFTrayId.objects.filter(lot_id=lot_id)
            total_tray_count = non_rejected_trays_qs.count()

            # 2. Total Quantity currently present (Sum of tray_quantity)
            total_qty_agg = non_rejected_trays_qs.aggregate(total_sum=Sum('tray_quantity'))
            total_qty = total_qty_agg.get('total_sum', 0) or 0

            # ✅ FALLBACK: When no IQFTrayId records exist yet (initial rejection entry)
            # Use TotalStockModel + Brass data to estimate tray count and remaining qty
            frontend_total_iqf_qty = int(request.GET.get('total_iqf_qty', 0))
            
            if total_tray_count == 0 and frontend_total_iqf_qty > 0:
                ts = TotalStockModel.objects.filter(lot_id=lot_id).first()
                if ts:
                    # Get the physical quantity from Brass QC/Audit
                    physical_qty = getattr(ts, 'iqf_physical_qty', 0) or 0
                    if not physical_qty:
                        physical_qty = getattr(ts, 'quantity', 0) or 0
                    
                    if physical_qty > 0 and tray_capacity > 0:
                        # Calculate total trays from physical qty
                        total_tray_count = math.ceil(physical_qty / tray_capacity)
                        total_qty = physical_qty
                        print(f"  [IQF FALLBACK] No IQFTrayId records. Using TotalStockModel:")
                        print(f"  [IQF FALLBACK] physical_qty={physical_qty}, tray_capacity={tray_capacity}, estimated_trays={total_tray_count}")

            # 3. Get IQF Rejections (Store + Draft)
            iqf_rejected_qty = 0
            
            # A. Committed Rejections
            iqf_rejection_record = IQF_Rejection_ReasonStore.objects.filter(lot_id=lot_id).order_by('-id').first()
            if iqf_rejection_record:
                iqf_rejected_qty += iqf_rejection_record.total_rejection_quantity or 0

            # B. Draft Rejections (Using IQF_Draft_Store)
            draft_rejected_qty = 0
            draft_record = IQF_Draft_Store.objects.filter(lot_id=lot_id, draft_type='tray_rejection').first()
            if draft_record and draft_record.draft_data:
                try:
                    draft_data = draft_record.draft_data
                    if isinstance(draft_data, str):
                        import json
                        draft_data = json.loads(draft_data)
                    
                    # Sum up rejections in draft
                    tray_rejections = draft_data.get('tray_rejections', [])
                    for rej in tray_rejections:
                        draft_rejected_qty += int(rej.get('qty', 0) or 0)
                except Exception as e:
                    print(f"Error parsing draft rejection data: {e}")

            total_iqf_rejected_qty = iqf_rejected_qty + draft_rejected_qty
            
            # If no committed/draft rejections exist but frontend has rejection qty, use it
            if total_iqf_rejected_qty == 0 and frontend_total_iqf_qty > 0:
                total_iqf_rejected_qty = frontend_total_iqf_qty
                print(f"  [IQF FALLBACK] Using frontend total_iqf_qty={frontend_total_iqf_qty} as rejection qty")

            # 4. Remaining Qty = Total Qty (Physical) - Total IQF Rejected (Committed + Draft)
            remaining_qty = max(0, total_qty - total_iqf_rejected_qty)

            # 5. Trays needed for rejected qty (reserved for rejection, cannot be reused for acceptance)
            trays_for_rejection = 0
            if tray_capacity > 0 and total_iqf_rejected_qty > 0:
                trays_for_rejection = math.ceil(total_iqf_rejected_qty / tray_capacity)

            # 6. Max Reusable Trays = Total original trays - Trays reserved for rejection
            max_reusable_trays = max(0, total_tray_count - trays_for_rejection)

            # Count ALREADY used reusable trays in session
            # ✅ IMPORTANT: Exclude the CURRENT tray being validated to avoid self-blocking
            for item in current_session_allocations:
                # Handle both list of strings (new frontend) and list of objects (legacy/other)
                t_ids = []
                if isinstance(item, str):
                    t_ids = [item]
                elif isinstance(item, dict):
                     val = item.get('tray_ids', [])
                     if isinstance(val, list): t_ids = val
                     elif isinstance(val, str): t_ids = [val]
                
                for t_id in t_ids:
                    if not t_id: continue
                    # Skip the current tray being validated
                    if t_id == tray_id:
                        print(f"[IQF DEBUG] Skipping current tray {t_id} from reuse count")
                        continue
                    # Check if this tray is an EXISTING tray for this lot
                    t_ip_obj = IQFTrayId.objects.filter(tray_id=t_id, lot_id=lot_id).first()
                    
                    # If it exists in DB, it's a reusable tray
                    if t_ip_obj:
                         used_reusable_trays.add(t_id)

            used_reusable_count = len(used_reusable_trays)
            
            # FALLBACK: If current_session_allocations was missing (cached frontend),
            # use already_allocated as proxy for how many trays are already used
            if raw_allocations is None and used_reusable_count == 0:
                already_allocated_count = int(request.GET.get('already_allocated', 0))
                if already_allocated_count > 0:
                    # Assume worst case: all already-allocated trays could be reusable
                    used_reusable_count = min(already_allocated_count, max_reusable_trays)
                    print(f"[IQF DEBUG] FALLBACK: Using already_allocated={already_allocated_count} as proxy, used_reusable_count={used_reusable_count}")
            
            remaining_reuse_slots = max(0, max_reusable_trays - used_reusable_count)
            
            print(f"  [IQF Reject Validation] REUSE CALCULATION (Rejection-Based Formula):")
            print(f"  - Total Tray Count: {total_tray_count}")
            print(f"  - Total Quantity (Sum): {total_qty}")
            print(f"  - Total IQF Rejected Qty: {total_iqf_rejected_qty}")
            print(f"  - Remaining Qty (Accepted): {remaining_qty}")
            print(f"  - Tray Capacity: {tray_capacity}")
            print(f"  - Trays For Rejection: {trays_for_rejection} (ceil({total_iqf_rejected_qty}/{tray_capacity}))")
            print(f"  = Max Reusable Trays: {max_reusable_trays} ({total_tray_count} - {trays_for_rejection})")
            print(f"  - Used Reusable Count: {used_reusable_count}")
            print(f"  = Remaining Slots: {remaining_reuse_slots}")

        except Exception as e:
            print(f"  Error Calculating Reuse Limit: {e}")
            import traceback
            traceback.print_exc()


        # ----------------------------
        # 3. HARD RULES
        # ----------------------------
        
        # RULE A: Occupied existing trays are allowed for reuse IF we have reuse slots available (Physical Capacity Logic)
        if is_occupied_existing_tray:
            if remaining_reuse_slots > 0:
                 # ALLOW REUSE
                 print(f"[IQF Reject Validation] ALLOWING Occupied Tray {tray_id} (Reuse Slots Available: {remaining_reuse_slots})")
                 # We treat this as a valid reuse. The tray will be overwritten/used for rejection.
                 return JsonResponse({
                    'exists': True,
                    'valid_for_rejection': True,
                    'is_valid': True,
                    'status_message': 'Available (can rearrange)',
                    'tray_capacity': tray_capacity,
                    'current_quantity': tray_qty_db # Frontend might warn, but here we explicitly allow
                 })
            else:
                 print(f"[IQF Reject Validation] BLOCKING: Tray is Occupied (Qty: {tray_qty_db}) and No Reuse Slots ({remaining_reuse_slots})")
                 return JsonResponse({
                    'exists': True,
                    'valid_for_rejection': False,
                    'is_valid': False,
                    'error': 'Tray still contains material and reuse limit reached',
                    'status_message': 'Tray Not Empty. Reuse Exhausted.'
                })
        
        # RULE B: New trays - check if physically occupied for THIS lot
        if is_new_tray:
            # Check: does this tray belong to THIS lot and have stock?
            # (Different-lot trays were already rejected above)
            tray_master_qty = 0
            if tray_id_obj:
                tray_master_qty = getattr(tray_id_obj, 'tray_quantity', 0) or 0
                tray_master_lot = str(getattr(tray_id_obj, 'lot_id', '') or '')
                print(f"[IQF Reject Validation] New tray {tray_id}: master_qty={tray_master_qty}, master_lot={tray_master_lot}, current_lot={lot_id}")
            else:
                print(f"[IQF Reject Validation] New tray {tray_id}: NO TrayId record (truly new)")
            
            # If tray belongs to THIS lot and has stock → physically occupied → apply reuse limits
            if tray_id_obj and tray_master_qty > 0 and str(getattr(tray_id_obj, 'lot_id', '') or '') == str(lot_id):
                if remaining_reuse_slots > 0:
                    print(f"[IQF Reject Validation] ALLOWING occupied tray (Reuse Slots: {remaining_reuse_slots})")
                    return JsonResponse({
                        'exists': True,
                        'valid_for_rejection': True,
                        'is_valid': True,
                        'status_message': 'Available (can rearrange)',
                        'tray_capacity': tray_capacity,
                        'current_quantity': tray_master_qty
                    })
                else:
                    print(f"[IQF Reject Validation] BLOCKING occupied tray {tray_id} (qty={tray_master_qty}, No Reuse Slots)")
                    return JsonResponse({
                        'exists': True,
                        'valid_for_rejection': False,
                        'is_valid': False,
                        'error': 'Tray still contains material and reuse limit reached',
                        'status_message': 'Tray Not Empty. Reuse Exhausted.'
                    })
            
            # Strict Tray Type Validation for truly empty/free New Tray
            try:
                mm_obj = None
                mm_obj = ModelMasterCreation.objects.filter(lot_id=lot_id).first()
                if not mm_obj:
                        mm_obj = ModelMasterCreation.objects.filter(batch_id=lot_id).first()
                if not mm_obj:
                    ts_obj = TotalStockModel.objects.filter(lot_id=lot_id).first()
                    if ts_obj:
                        mm_obj = ts_obj.batch_id

                if mm_obj:
                        expected_type = mm_obj.tray_type
                        actual_type = tray_id_obj.tray_type if tray_id_obj else None
                        
                        norm_expected = str(expected_type).strip().lower() if expected_type else ''
                        norm_actual = str(actual_type).strip().lower() if actual_type else ''

                        if norm_expected and norm_expected != norm_actual:
                                return JsonResponse({
                                'exists': True, 
                                'valid_for_rejection': False,
                                'is_valid': False,
                                'status_message': 'Wrong Tray Type',
                                'error': f'Tray Type Mismatch! Expected: {expected_type}, Scanned: {actual_type or "None"}'
                            })
            except Exception as e:
                print(f"Error checking tray type: {e}")

            return JsonResponse({
                'exists': True,
                'valid_for_rejection': True,
                'is_valid': True,
                'status_message': 'New Tray Available',
                'validation_type': 'new_tray'
            })

        # ----------------------------
        # 4. REUSE LOGIC (Only for Existing Empty Trays)
        # ----------------------------
        if is_empty_existing_tray:
            try:
                # CHECK: Is this specific tray (tray_id) already one of the used ones?
                if tray_id in used_reusable_trays:
                        pass # Allowed
                elif remaining_reuse_slots <= 0:
                    # FAIL: Limit reached
                    return JsonResponse({
                        'exists': True,
                        'valid_for_rejection': False,
                        'is_valid': False,
                        'error': 'Tray still contains material and reuse limit reached',
                        'status_message': 'Tray Not Empty. Reuse Exhausted.'
                    })

                return JsonResponse({
                    'exists': True,
                    'valid_for_rejection': True,
                    'is_valid': True,
                    'status_message': 'Available (can rearrange)',
                    'validation_type': 'reuse_existing',
                    'tray_capacity': tray_capacity,
                    'current_quantity': 0
                })

            except Exception as e:
                print(f"Error in reuse logic: {e}")
                return JsonResponse({'exists': False, 'error': f'Validation Error: {str(e)}'})

        # Fallback (Should not catch anything if logic is sound, but strictly safe)
        return JsonResponse({
            'exists': False, 
            'valid_for_rejection': False, 
            'status_message': 'Invalid Tray State',
            'error': 'Tray could not be classified'
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({'exists': False, 'error': str(e)}, status=500)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def iqf_get_delink_candidates(request):
    """
    Calculate delink candidates based on new vs existing tray usage logic.
    
    Logic:
    1. Get all initial IQF trays (existing trays: new_tray=False, IP_tray_verified=True)
    2. Get accepted trays used (both new and existing)
    3. Remove existing tray usage completely
    4. Subtract new tray usage quantities from remaining trays
    5. Trays that become qty=0 are delink candidates
    """
    lot_id = request.GET.get('lot_id')
    if not lot_id:
        return Response({'success': False, 'error': 'Missing lot_id'}, status=400)
    
    try:
        # 1. Get all initial IQF trays (existing trays after IP verification)
        initial_trays = list(
            IQFTrayId.objects.filter(
                lot_id=lot_id,
                new_tray=False,
                IP_tray_verified=True
            ).order_by('id').values('tray_id', 'tray_quantity', 'id')
        )
        
        # 2. Get all accepted trays (rejected_tray=False)
        accepted_trays = list(
            IQFTrayId.objects.filter(
                lot_id=lot_id,
                rejected_tray=False
            ).values('tray_id', 'tray_quantity', 'new_tray', 'IP_tray_verified')
        )
        
        # 3. Separate new trays vs existing trays used in acceptance
        new_trays_used = []
        existing_trays_used = []
        
        for tray in accepted_trays:
            if tray['new_tray'] and not tray['IP_tray_verified']:
                # New tray
                new_trays_used.append({
                    'tray_id': tray['tray_id'],
                    'quantity': tray['tray_quantity']
                })
            elif not tray['new_tray'] and tray['IP_tray_verified']:
                # Existing tray
                existing_trays_used.append({
                    'tray_id': tray['tray_id'],
                    'quantity': tray['tray_quantity']
                })
        
        # 4. Process delink logic
        delink_candidates = []
        remaining_trays = initial_trays.copy()
        
        # Step 1: Remove existing tray usage completely
        for existing_usage in existing_trays_used:
            for i, tray in enumerate(remaining_trays):
                if tray['tray_id'] == existing_usage['tray_id']:
                    remaining_trays.pop(i)
                    break
        
        # Step 2: Subtract new tray usage quantities
        new_usage_quantities = [usage['quantity'] for usage in new_trays_used]
        
        for i, new_qty in enumerate(new_usage_quantities):
            if i < len(remaining_trays):
                original_qty = remaining_trays[i]['tray_quantity']
                new_remaining_qty = max(0, original_qty - new_qty)
                
                # If tray becomes 0, it's a delink candidate
                if new_remaining_qty == 0 and original_qty > 0:
                    delink_candidates.append({
                        'tray_id': remaining_trays[i]['tray_id'],
                        'original_quantity': original_qty,
                        'subtracted_quantity': new_qty
                    })
                
                # Update the remaining quantity
                remaining_trays[i]['tray_quantity'] = new_remaining_qty
        
        # 5. Check if any new trays were actually used
        new_tray_used = len(new_trays_used) > 0
        
        return Response({
            'success': True,
            'delink_candidates': delink_candidates if new_tray_used else [],
            'new_tray_used': new_tray_used,
            'debug': {
                'initial_trays': initial_trays,
                'new_trays_used': new_trays_used,
                'existing_trays_used': existing_trays_used,
                'remaining_after_processing': remaining_trays,
                'delink_candidates': delink_candidates
            }
        })
        
    except Exception as e:
        return Response({'success': False, 'error': str(e)}, status=500)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def iqf_get_rejected_trays(request):
    """
    Get remaining rejected trays after processing delink logic.
    Returns trays with remaining quantities > 0 after new tray usage subtraction.
    """
    lot_id = request.GET.get('lot_id')
    if not lot_id:
        return Response({'success': False, 'error': 'Missing lot_id'}, status=400)
    
    try:
        # 1. Get all initial IQF trays (existing trays after IP verification)
        initial_trays = list(
            IQFTrayId.objects.filter(
                lot_id=lot_id,
                new_tray=False,
                IP_tray_verified=True
            ).order_by('id').values('tray_id', 'tray_quantity')
        )
        
        # 2. Get accepted trays to calculate usage
        accepted_trays = list(
            IQFTrayId.objects.filter(
                lot_id=lot_id,
                rejected_tray=False
            ).values('tray_id', 'tray_quantity', 'new_tray', 'IP_tray_verified')
        )
        
        # 3. Separate usage types
        new_trays_used = []
        existing_trays_used = []
        
        for tray in accepted_trays:
            if tray['new_tray'] and not tray['IP_tray_verified']:
                new_trays_used.append(tray['tray_quantity'])
            elif not tray['new_tray'] and tray['IP_tray_verified']:
                existing_trays_used.append(tray['tray_id'])
        
        # 4. Process remaining trays
        remaining_trays = initial_trays.copy()
        
        # Remove existing tray usage
        for existing_tray_id in existing_trays_used:
            remaining_trays = [t for t in remaining_trays if t['tray_id'] != existing_tray_id]
        
        # Subtract new tray usage quantities
        rejected_trays = []
        for i, new_qty in enumerate(new_trays_used):
            if i < len(remaining_trays):
                original_qty = remaining_trays[i]['tray_quantity']
                final_qty = max(0, original_qty - new_qty)
                
                if final_qty > 0:
                    rejected_trays.append({
                        'tray_id': remaining_trays[i]['tray_id'],
                        'tray_quantity': final_qty
                    })
        
        # Add any remaining trays that weren't affected
        for i in range(len(new_trays_used), len(remaining_trays)):
            rejected_trays.append({
                'tray_id': remaining_trays[i]['tray_id'],
                'tray_quantity': remaining_trays[i]['tray_quantity']
            })
        
        return Response({
            'success': True,
            'rejected_trays': rejected_trays,
            'count': len(rejected_trays),
            'debug': {
                'initial_trays_count': len(initial_trays),
                'new_trays_used': new_trays_used,
                'existing_trays_removed': len(existing_trays_used),
                'final_rejected_count': len(rejected_trays)
            }
        })
        
    except Exception as e:
        return Response({'success': False, 'error': str(e)}, status=500)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def iqf_get_remaining_trays(request):
    """
    Get remaining trays and determine delink needs with correct new/existing identification
    """
    lot_id = request.GET.get('lot_id')
    if not lot_id:
        return Response({'success': False, 'error': 'Missing lot_id'}, status=400)
    
    try:
        # 1. Get all initial IQF tray IDs for this lot (original trays after IP verification)
        # ✅ FIXED: Recover tray IDs from multiple sources since active IQFTrayId/DPTrayId_History might be incomplete
        
        # Get history from various sources to ensure we have ALL trays
        dp_history_ids = set(DPTrayId_History.objects.filter(lot_id=lot_id, IP_tray_verified=True).values_list('tray_id', flat=True))
        brass_ids = set(BrassTrayId.objects.filter(lot_id=lot_id).values_list('tray_id', flat=True))
        ip_ids = set(IPTrayId.objects.filter(lot_id=lot_id).values_list('tray_id', flat=True))
        
        # Get list of tray IDs that have rejections for this lot
        rejected_scan_tray_ids = set(IQF_Rejected_TrayScan.objects.filter(lot_id=lot_id).exclude(tray_id='').values_list('tray_id', flat=True))
        
        # Combine all known tray IDs and filter out None/Empty
        all_known_tray_ids = (dp_history_ids | brass_ids | ip_ids | rejected_scan_tray_ids)
        all_known_tray_ids = {t for t in all_known_tray_ids if t} # Remove None and empty strings
        
        print(f"[DEBUG] Found trays - DP: {len(dp_history_ids)}, Brass: {len(brass_ids)}, IP: {len(ip_ids)}, Scan: {len(rejected_scan_tray_ids)}")
        print(f"[DEBUG] Valid combined tray IDs: {all_known_tray_ids}")
        
        if all_known_tray_ids:
            # Filter IQFTrayId for these specific trays, regardless of their current lot_id status
            found_iqf_trays = list(IQFTrayId.objects.filter(
                tray_id__in=all_known_tray_ids
            ).distinct().order_by('id'))
            
            # Identify missing tray IDs
            found_ids = {t.tray_id for t in found_iqf_trays}
            missing_ids = all_known_tray_ids - found_ids
            
            if missing_ids:
                print(f"[DEBUG] Missing IQFTrayId records for: {missing_ids}. Reconstructing...")
                # Create temporary objects for missing trays
                for mid in missing_ids:
                    # Provide defaults as if they were new/reset trays
                    # We assume capacity will be filled later or treated as empty initially
                    temp_tray = IQFTrayId(
                        tray_id=mid,
                        lot_id=lot_id, # Assume they belong to this lot
                        tray_quantity=0, # Default to 0, let logic distribute
                        IP_tray_verified=True,
                        rejected_tray=False,
                        delink_tray=False 
                    )
                    found_iqf_trays.append(temp_tray)
            
            iqf_trays = found_iqf_trays
        else:
            # Fallback to standard query if no history found (unlikely for valid stock)
            iqf_trays = list(IQFTrayId.objects.filter(
                lot_id=lot_id,
                IP_tray_verified=True
            ).order_by('id'))
        
        print(f"[DEBUG] Total iqf_trays found (recovered + reconstructed): {len(iqf_trays)}")
        print(f"[DEBUG] Trays: {[t.tray_id for t in iqf_trays]}")

        
        # Get total rejection quantity from Brass_QC_Rejection_ReasonStore table
        stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
        if not stock:
            return Response({'success': False, 'error': 'Stock not found'}, status=400)
            
        # Sum total_rejection_quantity from Brass_QC_Rejection_ReasonStore for this lot
        brass_rejection_records = Brass_QC_Rejection_ReasonStore.objects.filter(lot_id=lot_id)
        brass_total_rejected_qty = sum([record.total_rejection_quantity or 0 for record in brass_rejection_records])
        
        tray_capacity = stock.batch_id.tray_capacity if stock.batch_id and hasattr(stock.batch_id, 'tray_capacity') else 12
        
        # Calculate initial quantities based on brass_total_rejected_qty distributed across trays
        initial_iqf_trays = {}
        remaining_qty = brass_total_rejected_qty
        
        for tray in iqf_trays:
            if remaining_qty > 0:
                tray_qty = min(remaining_qty, tray_capacity)
                initial_iqf_trays[tray.tray_id] = tray_qty
                remaining_qty -= tray_qty
            else:
                initial_iqf_trays[tray.tray_id] = 0

        print(f"[DEBUG] Brass rejection records count: {len(brass_rejection_records)}")
        print(f"[DEBUG] Brass total rejected qty (from Brass_QC_Rejection_ReasonStore): {brass_total_rejected_qty}")
        print(f"[DEBUG] Tray capacity: {tray_capacity}")
        print(f"[DEBUG] Initial IQF trays (calculated): {initial_iqf_trays}")

        # 2. Apply missing quantities to get current physical quantities
        missing_qty = stock.iqf_missing_qty if stock.iqf_missing_qty else 0
        
        # Subtract missing quantity from initial trays (in order)
        current_physical_trays = {}
        remaining_missing = missing_qty
        for tray_id, initial_qty in initial_iqf_trays.items():
            if remaining_missing > 0:
                deduction = min(remaining_missing, initial_qty)
                current_physical_trays[tray_id] = initial_qty - deduction
                remaining_missing -= deduction
            else:
                current_physical_trays[tray_id] = initial_qty

        print(f"[DEBUG] After applying missing qty ({missing_qty}): {current_physical_trays}")

        # 3. Get accepted trays (rejected_tray=False) and separate by type
        accepted_trays = IQFTrayId.objects.filter(
            lot_id=lot_id, 
            rejected_tray=False
        )
        
        new_trays_used = []
        existing_trays_used = []
        
        for tray in accepted_trays:
            tray_info = {
                'tray_id': tray.tray_id,
                'qty': tray.tray_quantity
            }
            
            # Check if this tray was in the initial trays (existing) or is new
            if tray.tray_id in initial_iqf_trays:
                existing_trays_used.append(tray_info)
            else:
                new_trays_used.append(tray_info)

        print(f"[DEBUG] New trays used: {new_trays_used}")
        print(f"[DEBUG] Existing trays used: {existing_trays_used}")

        # 4. Apply delink logic using current physical quantities
        remaining_trays = []
        delink_candidates = []
        
        # Start with current physical trays
        working_trays = list(current_physical_trays.items())  # [(tray_id, physical_qty), ...]
        
        # Calculate shortage from existing tray usage
        total_shortage = 0
        for existing_usage in existing_trays_used:
            tray_id = existing_usage['tray_id']
            required_qty = existing_usage['qty']
            available_qty = current_physical_trays.get(tray_id, 0)
            
            if required_qty > available_qty:
                shortage = required_qty - available_qty
                total_shortage += shortage
                print(f"[DEBUG] Shortage for {tray_id}: required {required_qty}, available {available_qty}, shortage {shortage}")
            
            # Remove this tray from working_trays (it's been used)
            working_trays = [(tid, qty) for tid, qty in working_trays if tid != tray_id]
        
        print(f"[DEBUG] Total shortage from existing usage: {total_shortage}")
        print(f"[DEBUG] After removing existing usage: {working_trays}")
        
        # Calculate total new tray quantities used
        new_tray_total = sum(tray['qty'] for tray in new_trays_used)
        
        # Total quantity to subtract = shortage + new tray usage
        total_to_subtract = total_shortage + new_tray_total
        
        print(f"[DEBUG] New tray total: {new_tray_total}, Total to subtract: {total_to_subtract}")
        
        # Subtract total quantity from remaining trays in order
        post_subtract = []
        remaining_to_subtract = total_to_subtract

        for tray_id, physical_qty in working_trays:
            # how much we take from this tray
            used_qty = min(physical_qty, remaining_to_subtract)
            remaining_qty = physical_qty - used_qty

            post_subtract.append({
                'tray_id': tray_id,
                'initial_qty': initial_iqf_trays.get(tray_id, physical_qty),
                'physical_qty': physical_qty,
                'used_qty': used_qty,
                'remaining_qty': remaining_qty,
                'used_new_tray': used_qty > 0
            })

            remaining_to_subtract -= used_qty
            if remaining_to_subtract <= 0:
                # still include remaining trays that were not touched
                continue

        # Combine leftovers unchanged if any trays after working_trays
        # (No change needed here - working_trays already covers current physical trays)

        # ===== NEW: Select delink candidates conservatively =====
        # Prefer to delink at most (number_of_new_trays_used - 1) trays so one tray
        # can act as the rejection/top-tray candidate when multiple new trays used.
        num_new_trays_used = len(new_trays_used) if isinstance(new_trays_used, list) else 0
        max_delink_allowed = max(0, num_new_trays_used - 1)

# Decide candidates only from fully consumed trays (deterministic order preserved)
        fully_consumed = [t for t in post_subtract if t['remaining_qty'] == 0 and t['used_qty'] > 0]
        partially_consumed_or_left = [t for t in post_subtract if t['remaining_qty'] > 0 or t['used_qty'] == 0]

        # Reset any previous flags and select proper delink / rejection candidates
        delink_candidates = []
        for idx, tray_info in enumerate(fully_consumed):
            tray_info.pop('is_delink_candidate', None)
            tray_info.pop('is_rejection_candidate', None)

            if len(delink_candidates) < max_delink_allowed:
                tray_info['is_delink_candidate'] = True
                delink_candidates.append({
                    'tray_id': tray_info['tray_id'],
                    'original_qty': tray_info['initial_qty'],
                    'physical_qty': tray_info['physical_qty'],
                    'subtracted_qty': tray_info['used_qty']
                })
            else:
                # keep as potential rejection/top-tray candidate (not delink)
                tray_info['is_delink_candidate'] = False
            tray_info['is_rejection_candidate'] = True

        # Merge back into ordered list
        post_subtract = fully_consumed + partially_consumed_or_left

        # Build remaining_trays and delink list for response
        remaining_trays = []
        for t in post_subtract:
            remaining_trays.append({
                'tray_id': t['tray_id'],
                'initial_qty': t.get('initial_qty', t['physical_qty']),
                'physical_qty': t['physical_qty'],
                'used_qty': t['used_qty'],
                'remaining_qty': t['remaining_qty'],
                'is_delink_candidate': bool(t.get('is_delink_candidate', False)),
                'is_rejection_candidate': bool(t.get('is_rejection_candidate', False)),
                'used_new_tray': bool(t.get('used_new_tray', False))
            })

        # Flag whether delink is needed (for frontend)
        needs_delink = (len(delink_candidates) > 0)
        print(f"[DEBUG] Delink candidates selected: {delink_candidates} (max_allowed={max_delink_allowed})")
        return Response({
            'success': True,
            'remaining_trays': remaining_trays,
            'delink_candidates': delink_candidates,
            'new_trays_used': new_trays_used,
            'existing_trays_used': existing_trays_used,
            'needs_delink': needs_delink,
            'debug_info': {
                'brass_total_rejected_qty_source': 'Brass_QC_Rejection_ReasonStore',
                'brass_rejection_records_count': len(brass_rejection_records),
                'brass_total_rejected_qty': brass_total_rejected_qty,
                'tray_capacity': tray_capacity,
                'initial_trays': initial_iqf_trays,
                'physical_trays': current_physical_trays,
                'missing_qty': missing_qty,
                'total_shortage': total_shortage,
                'new_trays_count': len(new_trays_used),
                'existing_trays_count': len(existing_trays_used),
                'delink_candidates_count': len(delink_candidates)
            }
        })
        
    except Exception as e:
        print(f"[DEBUG] Error in iqf_get_remaining_trays: {str(e)}")
        return Response({'success': False, 'error': str(e)}, status=500)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def iqf_validate_delink_tray(request):
    """
    Allow ANY rejected tray from same lot to be delinked,
    but do NOT allow duplicate tray_id in delink or rejection lists.
    """
    tray_id = request.GET.get('tray_id', '').strip()
    lot_id = request.GET.get('lot_id', '').strip()
    
    if not tray_id or not lot_id:
        return Response({'success': False, 'error': 'Missing tray_id or lot_id'}, status=400)
    
    try:
        # Check if tray is a rejected tray for this lot
        rejected_tray_exists = IQFTrayId.objects.filter(
            lot_id=lot_id,
            tray_id=tray_id,
            rejected_tray=True
        ).exists()

        # Check if tray is already delinked (delink_tray=True)
        already_delinked = IQFTrayId.objects.filter(
            tray_id=tray_id,
            delink_tray=True
        ).exists()

        # Check if tray is already used in any other delink or rejection row for this lot
        # (You may want to check other tables if needed)
        duplicate_in_lot = IQFTrayId.objects.filter(
            lot_id=lot_id,
            tray_id=tray_id
        ).exclude(rejected_tray=True).exists()

        if already_delinked:
            return Response({
                'success': False,
                'is_valid': False,
                'error': f'Tray {tray_id} is already delinked'
            })

        if duplicate_in_lot:
            return Response({
                'success': False,
                'is_valid': False,
                'error': f'Tray {tray_id} is already used in this lot'
            })

        if rejected_tray_exists:
            return Response({
                'success': True,
                'is_valid': True,
                'tray_id': tray_id,
                'lot_id': lot_id,
                'message': 'Valid rejected tray for delink'
            })
        else:
            return Response({
                'success': True,
                'is_valid': False,
                'error': f'Tray {tray_id} is not a rejected tray for this lot'
            })
        
    except Exception as e:
        return Response({'success': False, 'error': str(e), 'is_valid': False}, status=500)     



@csrf_exempt
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def iqf_save_draft_tray_ids(request):
    """
    Save draft tray IDs and quantities in IQF_Draft_Store as draft_type='tray_id_draft'.
    """
    try:
        data = request.data
        lot_id = data.get('lot_id')
        trays = data.get('trays', [])
        user = request.user

        if not lot_id or not trays:
            return Response({'success': False, 'error': 'Missing lot_id or trays'}, status=400)

        # Use batch_id if provided, else empty string
        batch_id = data.get('batch_id', '')

        # Prepare draft_data as JSON
        draft_data = {
            'trays': trays
        }

        # Save or update the draft (unique on lot_id, draft_type, user)
        draft_type = 'tray_id_draft'
        obj, created = IQF_Draft_Store.objects.update_or_create(
            lot_id=lot_id,
            draft_type=draft_type,
            user=user,
            defaults={
                'batch_id': batch_id,
                'draft_data': draft_data
            }
        )

        return Response({'success': True, 'message': 'Draft trays saved.'})
    except Exception as e:
        return Response({'success': False, 'error': str(e)}, status=500)



@api_view(['POST'])
@permission_classes([IsAuthenticated])
def iqf_process_all_tray_data(request):
    """Process all tray data: delink, verification, and rejection top trays"""
    try:
        data = request.data
        lot_id = data.get('lot_id')
        delink_trays = data.get('delink_trays', [])
        verifications = data.get('verifications', [])
        rejection_top_trays = data.get('rejection_top_trays', [])
        is_draft = data.get('is_draft', False)
        
        if not lot_id:
            return Response({
                'success': False, 
                'error': 'Missing lot_id'
            }, status=400)
        
        results = {
            'processed_delinks': 0,
            'processed_verifications': 0,
            'processed_rejections': 0,
            'errors': []
        }
        
        # ✅ Process delink trays
        for tray_id in delink_trays:
            try:
                tray_obj = IQFTrayId.objects.filter(lot_id=lot_id, tray_id=tray_id).first()
                if tray_obj:
                    # Update all delink fields
                    tray_obj.lot_id = None
                    tray_obj.delink_tray = True
                    tray_obj.new_tray = False
                    tray_obj.batch_id = None
                    tray_obj.tray_quantity = 0
                    tray_obj.IP_tray_verified = False
                    tray_obj.rejected_tray = False
                    tray_obj.iqf_reject_verify = False
                    tray_obj.top_tray = False
                    
                    tray_obj.save(update_fields=[
                        'lot_id', 'delink_tray', 'new_tray', 'batch_id', 'tray_quantity',
                        'IP_tray_verified', 'rejected_tray', 'iqf_reject_verify', 'top_tray'
                    ])
                    
                    IPTrayId.objects.filter(tray_id=tray_id, lot_id=lot_id).update(delink_tray=True)
                    results['processed_delinks'] += 1
                    print(f"✅ Delinked tray: {tray_id}")
                    
                    # 🟢 Update only delink_tray in BrassTrayId for this tray_id and lot_id
                    BrassTrayId.objects.filter(tray_id=tray_id, lot_id=lot_id).update(delink_tray=True)
                    results['processed_delinks'] += 1
                    print(f"✅ Delinked tray: {tray_id}")
                    
                    # 🟢 Update only delink_tray in BrassAuditTrayId for this tray_id and lot_id
                    BrassAuditTrayId.objects.filter(tray_id=tray_id, lot_id=lot_id).update(delink_tray=True)
                    results['processed_delinks'] += 1
                    print(f"✅ Delinked tray: {tray_id}")
                    
                    
                    DPTrayId_History.objects.filter(tray_id=tray_id, lot_id=lot_id).update(delink_tray=True)
                    results['processed_delinks'] += 1
                    print(f"✅ Delinked tray: {tray_id}")
                    
                    tray_obj = TrayId.objects.filter(tray_id=tray_id, lot_id=lot_id).first()
                    if tray_obj:
                        tray_obj.delink_tray = True
                        tray_obj.lot_id = None
                        tray_obj.batch_id = None
                        tray_obj.scanned = False
                        tray_obj.tray_quantity = 0
                        tray_obj.IP_tray_verified = False
                        tray_obj.rejected_tray = False
                        tray_obj.top_tray = False
                        tray_obj.new_tray = True
                        tray_obj.save(update_fields=[
                            'delink_tray', 'lot_id', 'batch_id', 'scanned', 'tray_quantity',
                            'IP_tray_verified', 'rejected_tray', 'top_tray', 'new_tray'
                        ])
                    
                    
                    results['processed_delinks'] += 1
                    print(f"✅ Delinked tray: {tray_id}")
                else:
                    results['errors'].append(f"Delink tray {tray_id} not found")
                    
            except Exception as e:
                results['errors'].append(f"Error processing delink tray {tray_id}: {str(e)}")
        
        # ✅ UPDATED: Process verification checkboxes AND save rejection quantity
        for verification in verifications:
            try:
                tray_id = verification['tray_id']
                verified = verification['verified']
                qty = verification.get('qty')  # ✅ Get the rejection quantity
                
                tray_obj = IQFTrayId.objects.filter(lot_id=lot_id, tray_id=tray_id).first()
                if tray_obj:
                    # ✅ UPDATE BOTH VERIFICATION FLAG AND QUANTITY
                    tray_obj.iqf_reject_verify = verified
                    
                    # ✅ SAVE THE REJECTION QUANTITY if provided
                    if qty is not None:
                        try:
                            tray_obj.tray_quantity = int(qty)
                            print(f"✅ Updated tray {tray_id} quantity to {qty}")
                        except (ValueError, TypeError):
                            print(f"⚠️ Invalid quantity {qty} for tray {tray_id}, keeping original")
                    
                    # ✅ SAVE BOTH FIELDS
                    tray_obj.save(update_fields=['iqf_reject_verify', 'tray_quantity'])
                    
                    results['processed_verifications'] += 1
                    print(f"✅ Verified tray: {tray_id} = {verified}, qty = {qty}")
                else:
                    results['errors'].append(f"Verification tray {tray_id} not found")
                    
            except Exception as e:
                results['errors'].append(f"Error processing verification for {verification.get('tray_id', 'unknown')}: {str(e)}")
        
        # ✅ Process rejection top trays (if any)
        for rejection in rejection_top_trays:
            try:
                tray_id = rejection['tray_id']
                qty = rejection['qty']
                
                # Update tray quantity if it exists
                tray_obj = IQFTrayId.objects.filter(lot_id=lot_id, tray_id=tray_id).first()
                if tray_obj:
                    tray_obj.tray_quantity = int(qty)
                    tray_obj.save(update_fields=['tray_quantity'])
                    print(f"✅ Updated rejection top tray: {tray_id} = {qty}")
                
                results['processed_rejections'] += 1
                
            except Exception as e:
                results['errors'].append(f"Error processing rejection tray {rejection.get('tray_id', 'unknown')}: {str(e)}")
        
        # ✅ Update stock status
        stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
        if stock:
            stock.iqf_onhold_picking = False
            stock.save(update_fields=['iqf_onhold_picking'])
        
        # ✅ CREATE NEW LOT FOR ACCEPTED TRAYS (if needed)
        accepted_trays_objs = IQFTrayId.objects.filter(lot_id=lot_id, rejected_tray=False)
        if accepted_trays_objs.exists():
            total_stock_obj = stock  # Already fetched above
            new_lot_id = generate_new_lot_id()
            new_total_stock = TotalStockModel.objects.create(
                lot_id=new_lot_id,
                model_stock_no=total_stock_obj.model_stock_no,
                batch_id=total_stock_obj.batch_id,
                version=total_stock_obj.version,
                total_stock=total_stock_obj.total_stock,
                total_IP_accpeted_quantity=total_stock_obj.iqf_accepted_qty,
                polish_finish=total_stock_obj.polish_finish,
                plating_color=total_stock_obj.plating_color,
                created_at=total_stock_obj.created_at,
                last_process_date_time=total_stock_obj.last_process_date_time,
                send_brass_qc=True,
                tray_scan_status=True,
                ip_person_qty_verified=True,
                last_process_module="IQF",
                remove_lot=True,
                iqf_last_process_date_time=timezone.now()
            )
            for tray in accepted_trays_objs:
                IQFTrayId.objects.create(
                    tray_id=tray.tray_id,
                    lot_id=new_lot_id,
                    batch_id=tray.batch_id,
                    tray_quantity=tray.tray_quantity,
                    tray_capacity=tray.tray_capacity,
                    tray_type=tray.tray_type,
                    rejected_tray=False,
                    IP_tray_verified=True,
                    new_tray=False,
                    top_tray=getattr(tray, 'top_tray', False)  # ✅ FIXED: Preserve top_tray flag
                )

        # ✅ Summary response
        success = len(results['errors']) == 0
        message_parts = []
        
        if results['processed_delinks'] > 0:
            message_parts.append(f"{results['processed_delinks']} trays delinked")
        if results['processed_verifications'] > 0:
            message_parts.append(f"{results['processed_verifications']} verifications saved")
        if results['processed_rejections'] > 0:
            message_parts.append(f"{results['processed_rejections']} rejection trays processed")
        
        message = ", ".join(message_parts) if message_parts else "No data to process"
        
        if results['errors']:
            message += f" (with {len(results['errors'])} errors)"
        
        return Response({
            'success': success,
            'message': message,
            'results': results,
            'is_draft': is_draft
        })
        
    except Exception as e:
        print(f"[DEBUG] Error in iqf_process_all_tray_data: {str(e)}")
        import traceback
        traceback.print_exc()
        return Response({'success': False, 'error': str(e)}, status=500)
     
     
     
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_tray_capacity(request):
    lot_id = request.GET.get('lot_id')
    rejection_qty = int(request.GET.get('rejection_qty', 0))
    print(f"Received tray capacity request for lot_id: {lot_id}, rejection_qty: {rejection_qty}")

    if not lot_id:
        return Response({'success': False, 'error': 'Missing lot_id'}, status=400)
    try:
        stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
        tray_capacity = 0
        if stock and stock.batch_id and hasattr(stock.batch_id, 'tray_capacity'):
            tray_capacity = stock.batch_id.tray_capacity
        print(f"Tray capacity for lot_id {lot_id}: {tray_capacity}")

        response_data = {'success': True, 'tray_capacity': tray_capacity}

        # Calculate max reusable trays when rejection_qty is provided
        if tray_capacity and tray_capacity > 0:
            total_tray_count = IQFTrayId.objects.filter(lot_id=lot_id).count()

            # Fallback: estimate tray count from physical qty if no IQFTrayId records
            if total_tray_count == 0 and stock:
                physical_qty = getattr(stock, 'iqf_physical_qty', 0) or 0
                if physical_qty > 0:
                    total_tray_count = math.ceil(physical_qty / tray_capacity)

            trays_for_rejection = math.ceil(rejection_qty / tray_capacity) if rejection_qty > 0 else 0
            max_reusable_trays = max(0, total_tray_count - trays_for_rejection)
            response_data['max_reusable_trays'] = max_reusable_trays
            response_data['total_tray_count'] = total_tray_count
            print(f"  Reuse calc: total_trays={total_tray_count}, trays_for_rej={trays_for_rejection}, max_reusable={max_reusable_trays}")

        return Response(response_data)
    except Exception as e:
        return Response({'success': False, 'error': str(e)}, status=500)
    

@csrf_exempt
def iqf_delink_selected_trays(request):
    if request.method == "POST":
        import json
        try:
            data = json.loads(request.body.decode('utf-8'))
            
            stock_lot_ids = data.get('stock_lot_ids', [])
            tray_ids = data.get('tray_ids', [])
            
            updated_ip_trays = 0
            updated_original_trays = 0
            updated_brass_trays = 0
            updated_brass_audit_trays = 0
            updated_iptrayid_trays = 0  # Track IPTrayId updates
            lots_processed = 0
            not_found = []
            
            if stock_lot_ids:
                for stock_lot_id in stock_lot_ids:
                    rejected_tray_ids = list(IQFTrayId.objects.filter(
                        lot_id=stock_lot_id, 
                        rejected_tray=True
                    ).values_list('tray_id', flat=True))
                    
                    if rejected_tray_ids:
                        # IQFTrayId
                        updated_count_ip = IQFTrayId.objects.filter(
                            lot_id=stock_lot_id,
                            rejected_tray=True
                        ).update(
                            delink_tray=True,
                        )
                        updated_ip_trays += updated_count_ip
                        
                        # TrayId
                        updated_count_original = TrayId.objects.filter(
                            tray_id__in=rejected_tray_ids
                        ).update(
                            delink_tray=True,
                            lot_id=None,
                            batch_id=None,
                            scanned=False,
                            IP_tray_verified=False,
                            rejected_tray=False,
                            top_tray=False,
                            new_tray=True
                        )
                        updated_original_trays += updated_count_original

                        # BrassTrayId
                        updated_count_brass = BrassTrayId.objects.filter(
                            tray_id__in=rejected_tray_ids
                        ).update(
                            delink_tray=True,
                        )
                        updated_brass_trays += updated_count_brass

                        # BrassAuditTrayId
                        updated_count_brass_audit = BrassAuditTrayId.objects.filter(
                            tray_id__in=rejected_tray_ids
                        ).update(
                            delink_tray=True,
                        )
                        updated_brass_audit_trays += updated_count_brass_audit

                        # IPTrayId
                        updated_count_iptrayid = IPTrayId.objects.filter(
                            tray_id__in=rejected_tray_ids
                        ).update(
                            delink_tray=True,
                        )
                        updated_iptrayid_trays += updated_count_iptrayid
                        
                        lots_processed += 1
                    else:
                        not_found.append(stock_lot_id)
                
                return JsonResponse({
                    'success': True, 
                    'updated_ip_trays': updated_ip_trays,
                    'updated_original_trays': updated_original_trays,
                    'updated_brass_trays': updated_brass_trays,
                    'updated_brass_audit_trays': updated_brass_audit_trays,
                    'updated_iptrayid_trays': updated_iptrayid_trays,
                    'total_updated': updated_ip_trays + updated_original_trays + updated_brass_trays + updated_brass_audit_trays + updated_iptrayid_trays,
                    'lots_processed': lots_processed,
                    'not_found': not_found
                })
            
            elif tray_ids:
                for tray_id in tray_ids:
                    # IQFTrayId
                    delink_tray_obj = IQFTrayId.objects.filter(tray_id=tray_id).first()
                    if delink_tray_obj:
                        delink_tray_obj.delink_tray = True
                        delink_tray_obj.save(update_fields=[
                            'delink_tray'
                        ])
                        updated_ip_trays += 1
                    
                    # TrayId
                    original_tray_obj = TrayId.objects.filter(tray_id=tray_id).first()
                    if original_tray_obj:
                        original_tray_obj.delink_tray = True
                        original_tray_obj.lot_id = None
                        original_tray_obj.batch_id = None
                        original_tray_obj.scanned = False
                        original_tray_obj.IP_tray_verified = False
                        original_tray_obj.rejected_tray = False
                        original_tray_obj.top_tray = False
                        original_tray_obj.new_tray = True
                        original_tray_obj.save(update_fields=[
                            'delink_tray', 'lot_id', 'batch_id', 'scanned','new_tray','IP_tray_verified', 'top_tray', 'rejected_tray'
                        ])
                        updated_original_trays += 1
                        
                    history_tray_obj = DPTrayId_History.objects.filter(tray_id=tray_id).first()
                    if history_tray_obj:
                        history_tray_obj.delink_tray = True
                        history_tray_obj.save(update_fields=[
                            'delink_tray'
                        ])
                        updated_ip_trays += 1
                        print(f"✅ Updated IPTrayId for tray_id: {tray_id}")
                    else:
                        print(f"⚠️ history_tray_obj not found for tray_id: {tray_id}")
                    
                    # BrassTrayId
                    brass_tray_obj = BrassTrayId.objects.filter(tray_id=tray_id).first()
                    if brass_tray_obj:
                        brass_tray_obj.delink_tray = True
                        brass_tray_obj.save(update_fields=[
                            'delink_tray'
                        ])
                        updated_brass_trays += 1

                    # BrassAuditTrayId
                    brass_audit_tray_obj = BrassAuditTrayId.objects.filter(tray_id=tray_id).first()
                    if brass_audit_tray_obj:
                        brass_audit_tray_obj.delink_tray = True
                        brass_audit_tray_obj.save(update_fields=[
                            'delink_tray'
                        ])
                        updated_brass_audit_trays += 1

                    # IPTrayId
                    iptrayid_tray_obj = IPTrayId.objects.filter(tray_id=tray_id).first()
                    if iptrayid_tray_obj:
                        iptrayid_tray_obj.delink_tray = True
                        iptrayid_tray_obj.save(update_fields=[
                            'delink_tray'
                        ])
                        updated_iptrayid_trays += 1

                    else:
                        not_found.append(tray_id)
                
                return JsonResponse({
                    'success': True, 
                    'updated_ip_trays': updated_ip_trays,
                    'updated_original_trays': updated_original_trays,
                    'updated_brass_trays': updated_brass_trays,
                    'updated_brass_audit_trays': updated_brass_audit_trays,
                    'updated_iptrayid_trays': updated_iptrayid_trays,
                    'total_updated': updated_ip_trays + updated_original_trays + updated_brass_trays + updated_brass_audit_trays + updated_iptrayid_trays,
                    'not_found': not_found
                })
            
            else:
                return JsonResponse({'success': False, 'error': 'No stock_lot_ids or tray_ids provided'})
                
        except Exception as e:
            import traceback
            traceback.print_exc()
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Invalid request'}, status=400)
# These views is for draft ( Delink and Rejection Verification )



@method_decorator([login_required, csrf_exempt], name='dispatch')
class IQFOptimalDistributionDraftView(View):
    """
    Handle optimal distribution draft save/load operations
    """
    
    def get(self, request):
        """
        Get draft data for a specific lot_id
        """
        try:
            lot_id = request.GET.get('lot_id')
            
            if not lot_id:
                return JsonResponse({
                    'success': False,
                    'error': 'lot_id parameter is required'
                }, status=400)
            
            try:
                draft = IQF_OptimalDistribution_Draft.objects.get(
                    lot_id=lot_id,
                    user=request.user
                )
                
                return JsonResponse({
                    'success': True,
                    'has_draft': True,
                    'draft_data': {
                        'lot_id': draft.lot_id,
                        'delink_trays': draft.delink_trays,
                        'rejection_verifications': draft.rejection_verifications,
                        'created_at': draft.created_at.isoformat(),
                        'updated_at': draft.updated_at.isoformat()
                    }
                })
                
            except IQF_OptimalDistribution_Draft.DoesNotExist:
                return JsonResponse({
                    'success': True,
                    'has_draft': False,
                    'draft_data': None
                })
                
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': f'Error retrieving draft: {str(e)}'
            }, status=500)
    
    def post(self, request):
        """
        Save draft data for optimal distribution
        """
        try:
            data = json.loads(request.body)
            lot_id = data.get('lot_id')
            delink_trays = data.get('delink_trays', [])
            rejection_verifications = data.get('rejection_verifications', [])
            
            if not lot_id:
                return JsonResponse({
                    'success': False,
                    'error': 'lot_id is required'
                }, status=400)
            
            # Validate delink_trays structure
            if not isinstance(delink_trays, list):
                return JsonResponse({
                    'success': False,
                    'error': 'delink_trays must be an array'
                }, status=400)
            
            # Validate rejection_verifications structure
            if not isinstance(rejection_verifications, list):
                return JsonResponse({
                    'success': False,
                    'error': 'rejection_verifications must be an array'
                }, status=400)
            
            # Create or update draft
            draft, created = IQF_OptimalDistribution_Draft.objects.update_or_create(
                lot_id=lot_id,
                user=request.user,
                defaults={
                    'delink_trays': delink_trays,
                    'rejection_verifications': rejection_verifications
                }
            )
            
            action = 'created' if created else 'updated'
            
            return JsonResponse({
                'success': True,
                'message': f'Draft {action} successfully',
                'draft_id': draft.id,
                'action': action,
                'data': {
                    'lot_id': draft.lot_id,
                    'delink_trays_count': len(draft.delink_trays),
                    'rejection_verifications_count': len(draft.rejection_verifications),
                    'updated_at': draft.updated_at.isoformat()
                }
            })
            
        except json.JSONDecodeError:
            return JsonResponse({
                'success': False,
                'error': 'Invalid JSON data'
            }, status=400)
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': f'Error saving draft: {str(e)}'
            }, status=500)
    
    def delete(self, request):
        """
        Delete draft data for a specific lot_id
        """
        try:
            data = json.loads(request.body)
            lot_id = data.get('lot_id')
            
            if not lot_id:
                return JsonResponse({
                    'success': False,
                    'error': 'lot_id is required'
                }, status=400)
            
            try:
                draft = IQF_OptimalDistribution_Draft.objects.get(
                    lot_id=lot_id,
                    user=request.user
                )
                draft.delete()
                
                return JsonResponse({
                    'success': True,
                    'message': 'Draft deleted successfully'
                })
                
            except IQF_OptimalDistribution_Draft.DoesNotExist:
                return JsonResponse({
                    'success': False,
                    'error': 'Draft not found'
                }, status=404)
                
        except json.JSONDecodeError:
            return JsonResponse({
                'success': False,
                'error': 'Invalid JSON data'
            }, status=400)
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': f'Error deleting draft: {str(e)}'
            }, status=500)


# Alternative function-based views (if you prefer)
@csrf_exempt
@login_required
@require_http_methods(["GET"])
def iqf_check_optimal_distribution_draft(request):
    """
    Check if draft exists for a lot_id
    """
    try:
        lot_id = request.GET.get('lot_id')
        
        if not lot_id:
            return JsonResponse({
                'success': False,
                'error': 'lot_id parameter is required'
            }, status=400)
        
        has_draft = IQF_OptimalDistribution_Draft.objects.filter(
            lot_id=lot_id,
            user=request.user
        ).exists()
        
        return JsonResponse({
            'success': True,
            'has_draft': has_draft
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error checking draft: {str(e)}'
        }, status=500)


@csrf_exempt
@login_required
@require_http_methods(["POST"])
def iqf_save_optimal_distribution_draft(request):
    """
    Save optimal distribution draft data
    """
    try:
        data = json.loads(request.body)
        lot_id = data.get('lot_id')
        delink_trays = data.get('delink_trays', [])
        rejection_verifications = data.get('rejection_verifications', [])
        
        if not lot_id:
            return JsonResponse({
                'success': False,
                'error': 'lot_id is required'
            }, status=400)
        
        # Save or update the draft
        draft, created = IQF_OptimalDistribution_Draft.objects.update_or_create(
            lot_id=lot_id,
            user=request.user,
            defaults={
                'delink_trays': delink_trays,
                'rejection_verifications': rejection_verifications
            }
        )
        
        return JsonResponse({
            'success': True,
            'message': f'Draft {"created" if created else "updated"} successfully',
            'draft_id': draft.id
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON data'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error saving draft: {str(e)}'
        }, status=500)


@csrf_exempt
@login_required
@require_http_methods(["GET"])
def iqf_load_optimal_distribution_draft(request):
    """
    Load optimal distribution draft data
    """
    try:
        lot_id = request.GET.get('lot_id')
        
        if not lot_id:
            return JsonResponse({
                'success': False,
                'error': 'lot_id parameter is required'
            }, status=400)
        
        try:
            draft = IQF_OptimalDistribution_Draft.objects.get(
                lot_id=lot_id,
                user=request.user
            )
            
            return JsonResponse({
                'success': True,
                'draft_data': {
                    'lot_id': draft.lot_id,
                    'delink_trays': draft.delink_trays,
                    'rejection_verifications': draft.rejection_verifications,
                    'created_at': draft.created_at.isoformat(),
                    'updated_at': draft.updated_at.isoformat()
                }
            })
            
        except IQF_OptimalDistribution_Draft.DoesNotExist:
            return JsonResponse({
                'success': False,
                'error': 'Draft not found'
            }, status=404)
            
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error loading draft: {str(e)}'
        }, status=500)
        


@method_decorator(login_required, name='dispatch')
class IQFAcceptTableView(APIView):
    renderer_classes = [TemplateHTMLRenderer]
    template_name = 'IQF/Iqf_AcceptTable.html'
    permission_classes = [IsAuthenticated]

    def get(self, request):
        import math

        user = request.user

        # Query only accepted IQF lots (no date filter)
        queryset = TotalStockModel.objects.select_related(
            'batch_id',
            'batch_id__model_stock_no',
            'batch_id__version',
            'batch_id__location'
        ).filter(
            Q(iqf_acceptance=True) |
            (Q(iqf_few_cases_acceptance=True) & Q(iqf_onhold_picking=False)),
            batch_id__total_batch_quantity__gt=0
        ).order_by('-iqf_last_process_date_time')

        # Subqueries for rejection quantities
        brass_rejection_qty_subquery = Brass_QC_Rejection_ReasonStore.objects.filter(
            lot_id=OuterRef('lot_id')
        ).values('total_rejection_quantity')[:1]

        iqf_rejection_qty_subquery = IQF_Rejection_ReasonStore.objects.filter(
            lot_id=OuterRef('lot_id')
        ).values('total_rejection_quantity')[:1]

        queryset = queryset.annotate(
            wiping_required=F('batch_id__model_stock_no__wiping_required'),
            brass_rejection_total_qty=brass_rejection_qty_subquery,
            iqf_rejection_qty=iqf_rejection_qty_subquery,
        )

        # Pagination
        page_number = request.GET.get('page', 1)
        paginator = Paginator(queryset, 10)
        page_obj = paginator.get_page(page_number)

        master_data = []
        for stock_obj in page_obj.object_list:
            batch = stock_obj.batch_id
            lot_id = stock_obj.lot_id
            # Fetch first accepted_comment for this lot_id
            accepted_comment = ""
            accepted_tray_obj = IQF_Accepted_TrayID_Store.objects.filter(lot_id=lot_id).order_by('id').first()
            if accepted_tray_obj and accepted_tray_obj.accepted_comment:
                accepted_comment = accepted_tray_obj.accepted_comment

            data = {
                'batch_id': batch.batch_id,
                'date_time': getattr(batch, 'date_time', None),
                'model_stock_no__model_no': batch.model_stock_no.model_no if batch.model_stock_no else '',
                'plating_color': batch.plating_color,
                'polish_finish': batch.polish_finish,
                'version__version_name': batch.version.version_name if batch.version else '',
                'vendor_internal': batch.vendor_internal,
                'location__location_name': batch.location.location_name if batch.location else '',
                'tray_type': batch.tray_type,
                'tray_capacity': batch.tray_capacity,
                'Moved_to_D_Picker': batch.Moved_to_D_Picker,
                'Draft_Saved': batch.Draft_Saved,
                'plating_stk_no': batch.plating_stk_no,
                'polishing_stk_no': batch.polishing_stk_no,
                'category': batch.category,
                'lot_id': stock_obj.lot_id,
                'stock_lot_id': stock_obj.lot_id,
                'last_process_module': stock_obj.last_process_module,
                'next_process_module': stock_obj.next_process_module,
                'wiping_required': getattr(stock_obj, 'wiping_required', None),
                'iqf_missing_qty': stock_obj.iqf_missing_qty,
                'iqf_physical_qty': stock_obj.iqf_physical_qty,
                'iqf_physical_qty_edited': getattr(stock_obj, 'iqf_physical_qty_edited', None),
                'accepted_tray_scan_status': getattr(stock_obj, 'accepted_tray_scan_status', None),
                'iqf_rejection_qty': getattr(stock_obj, 'iqf_rejection_qty', None),
                'iqf_accepted_qty': stock_obj.iqf_accepted_qty,
                'IQF_pick_remarks': getattr(stock_obj, 'IQF_pick_remarks', None),
                'brass_accepted_tray_scan_status': getattr(stock_obj, 'brass_accepted_tray_scan_status', None),
                'brass_qc_rejection': getattr(stock_obj, 'brass_qc_rejection', None),
                'brass_rejection_total_qty': getattr(stock_obj, 'brass_rejection_total_qty', None),
                'brass_qc_few_cases_accptance': getattr(stock_obj, 'brass_qc_few_cases_accptance', None),
                'iqf_accepted_qty_verified': stock_obj.iqf_accepted_qty_verified,
                'iqf_acceptance': stock_obj.iqf_acceptance,
                'iqf_rejection': getattr(stock_obj, 'iqf_rejection', None),
                'iqf_few_cases_acceptance': getattr(stock_obj, 'iqf_few_cases_acceptance', None),
                'iqf_onhold_picking': getattr(stock_obj, 'iqf_onhold_picking', None),
                'iqf_accepted_tray_scan_status': getattr(stock_obj, 'iqf_accepted_tray_scan_status', None),
                'iqf_last_process_date_time': stock_obj.iqf_last_process_date_time,
                'total_IP_accpeted_quantity': stock_obj.total_IP_accpeted_quantity,
                'accepted_comment': accepted_comment,
            }
            master_data.append(data)

        # Process the data with fallback to total_IP_accpeted_quantity
        for data in master_data:
            brass_rejection_total_qty = data.get('brass_rejection_total_qty')
            total_ip_accepted_qty = data.get('total_IP_accpeted_quantity', 0)
            tray_capacity = data.get('tray_capacity')

            # Use total_IP_accpeted_quantity as fallback when brass_rejection_total_qty is None
            if brass_rejection_total_qty is None:
                effective_qty = total_ip_accepted_qty
            else:
                effective_qty = brass_rejection_total_qty

            data['vendor_location'] = f"{data.get('vendor_internal', '')}_{data.get('location__location_name', '')}"

            # Calculate no_of_trays using effective_qty
            if effective_qty is None or tray_capacity is None or tray_capacity == 0:
                data['no_of_trays'] = 0
            else:
                data['no_of_trays'] = math.ceil(effective_qty / tray_capacity)

            # Store the effective quantity for display purposes
            data['display_quantity'] = effective_qty
            data['quantity_source'] = 'total_IP_accpeted_quantity' if brass_rejection_total_qty is None else 'brass_rejection_total_qty'

            # Get model images
            batch_obj = ModelMasterCreation.objects.filter(batch_id=data['batch_id']).first()
            images = []
            if batch_obj and batch_obj.model_stock_no:
                for img in batch_obj.model_stock_no.images.all():
                    if img.master_image:
                        images.append(img.master_image.url)
            if not images:
                images = [static('assets/images/imagePlaceholder.png')]
            data['model_images'] = images

            # Add available_qty logic with fallback
            lot_id = data.get('stock_lot_id')
            total_stock_obj = TotalStockModel.objects.filter(lot_id=lot_id).first()
            if total_stock_obj:
                if total_stock_obj.iqf_physical_qty and total_stock_obj.iqf_physical_qty > 0:
                    data['available_qty'] = total_stock_obj.iqf_physical_qty
                else:
                    data['available_qty'] = effective_qty or 0
            else:
                data['available_qty'] = 0

        context = {
            'master_data': master_data,
            'page_obj': page_obj,
            'paginator': paginator,
            'user': user,
        }
        return Response(context, template_name=self.template_name)


# ==========================================
# BARCODE SCANNER API - IQF
# ==========================================

# ==========================================
# MANUAL DRAFT AND AUTO-SAVE API ENDPOINTS (Following Brass QC Pattern)
# ==========================================

@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(login_required, name='dispatch')
class IQFSetManualDraftAPIView(APIView):
    """
    API endpoint to save manual draft data when user clicks draft button
    Following the successful Brass QC pattern
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            data = request.data
            lot_id = data.get('lot_id')
            batch_id = data.get('batch_id', '')
            draft_type = data.get('draft_type')
            draft_data = data.get('draft_data')
            
            if not lot_id:
                return Response({
                    'success': False,
                    'error': 'Missing lot_id parameter'
                }, status=400)

            if not draft_type:
                return Response({
                    'success': False,
                    'error': 'Missing draft_type parameter'
                }, status=400)

            if not draft_data:
                return Response({
                    'success': False,
                    'error': 'Missing draft_data parameter'
                }, status=400)

            # Validate draft_type
            if draft_type not in ['batch_rejection', 'tray_rejection']:
                return Response({
                    'success': False,
                    'error': 'Invalid draft_type. Must be batch_rejection or tray_rejection'
                }, status=400)

            # Verify the lot exists
            try:
                TotalStockModel.objects.get(lot_id=lot_id)
            except TotalStockModel.DoesNotExist:
                return Response({
                    'success': False,
                    'error': f'Lot {lot_id} not found'
                }, status=404)

            # Save the manual draft data
            draft_obj, created = IQF_Draft_Store.objects.update_or_create(
                lot_id=lot_id,
                draft_type=draft_type,
                defaults={
                    'batch_id': batch_id,
                    'user': request.user,
                    'draft_data': draft_data
                }
            )

            # ✅ CRITICAL: Update lot status to "Draft" for manual draft
            try:
                lot_obj = TotalStockModel.objects.get(lot_id=lot_id)
                lot_obj.iqf_current_status = "Draft"
                lot_obj.save()
                print(f"✅ [IQF Manual Draft] Updated lot {lot_id} status to 'Draft'")
            except Exception as status_error:
                print(f"⚠️ [IQF Manual Draft] Failed to update lot status: {status_error}")

            action = "created" if created else "updated"
            
            return Response({
                'success': True,
                'message': f'Manual draft {action} successfully for lot {lot_id}',
                'lot_id': lot_id,
                'draft_type': draft_type,
                'action': action
            })

        except Exception as e:
            import traceback
            traceback.print_exc()
            return Response({
                'success': False,
                'error': f'Failed to save manual draft: {str(e)}'
            }, status=500)


@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(login_required, name='dispatch')
class IQFSaveRejectionDraftAPIView(APIView):
    """
    Auto-save API for rejection data (like Brass QC auto-save)
    Does NOT change lot status - keeps as "Yet to start"
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            data = request.data
            lot_id = data.get('lot_id') or data.get('stock_lot_id')
            batch_id = data.get('batch_id', '')
            user = request.user

            if not lot_id:
                return Response({'success': False, 'error': 'Missing lot_id'}, status=400)

            # Enforce: auto-save always is_draft = False, only manual Draft is True
            is_auto_save = data.get('is_auto_save', True)
            is_draft = data.get('is_draft', False)
            if is_auto_save:
                is_draft = False

            # Accept multiple possible shapes from frontend
            incoming = data.get('tray_rejections') or data.get('tray_rejection') or data.get('rejections') or []
            tray_id_mappings = data.get('tray_id_mappings') or []
            
            if isinstance(incoming, dict):
                incoming = [incoming]
            if not isinstance(incoming, list):
                incoming = []

            # Normalize incoming entries
            cleaned = []
            for it in incoming:
                try:
                    qty = int(it.get('qty') or it.get('quantity') or it.get('rejected_qty') or 0)
                except Exception:
                    qty = 0
                
                associated_trays = it.get('associated_trays', [])
                
                cleaned.append({
                    'reason_id': str(it.get('reason_id') or it.get('reason') or '').strip(),
                    'qty': qty,
                    'tray_id': str(it.get('tray_id') or it.get('rejected_tray_id') or '').strip(),
                    'associated_trays': associated_trays
                })

            # Create or update draft (auto-save - no status change)
            draft_obj, created = IQF_Draft_Store.objects.get_or_create(
                lot_id=lot_id,
                draft_type='tray_rejection',
                defaults={
                    'batch_id': batch_id or '',
                    'user': user,
                    'draft_data': {
                        'is_draft': is_draft,
                        'is_auto_save': is_auto_save,
                        'batch_rejection': False,
                        'tray_rejections': cleaned,
                        'tray_id_mappings': tray_id_mappings
                    }
                }
            )

            if not created:
                existing = draft_obj.draft_data or {}
                existing['tray_rejections'] = cleaned
                existing['tray_id_mappings'] = tray_id_mappings
                existing['is_draft'] = is_draft
                existing['is_auto_save'] = is_auto_save
                existing['batch_rejection'] = False

                draft_obj.batch_id = batch_id or draft_obj.batch_id
                draft_obj.user = user
                draft_obj.draft_data = existing
                draft_obj.save()

            # ✅ For auto-save: Do NOT change lot status
            print(f"✅ [IQF Auto-Save] Saved rejection data for lot {lot_id}, keeping status unchanged")

            return Response({
                'success': True, 
                'message': 'Auto-save completed',
                'draft': draft_obj.draft_data
            })
        except Exception as e:
            import traceback
            traceback.print_exc()
            return Response({'success': False, 'error': str(e)}, status=500)


@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(login_required, name='dispatch')
class IQFSaveAcceptedTrayDraftAPIView(APIView):
    """
    Auto-save API for accepted tray data (top tray and delink trays)
    Does NOT change lot status - keeps as "Yet to start"
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            data = request.data
            lot_id = data.get('lot_id')
            batch_id = data.get('batch_id', '')
            
            if not lot_id:
                return Response({'success': False, 'error': 'Missing lot_id'}, status=400)

            # Enforce: auto-save always is_draft = False, only manual Draft is True
            is_auto_save = data.get('is_auto_save', False)
            is_draft = data.get('is_draft', False)
            if is_auto_save:
                is_draft = False

            # Extract top tray data
            top_tray_id = data.get('top_tray_id', '').strip()
            top_tray_qty = data.get('top_tray_qty')
            
            # Extract delink trays data
            delink_trays = data.get('delink_trays', [])
            if isinstance(delink_trays, str):
                delink_trays = [delink_trays] if delink_trays else []
            
            # Create draft data structure
            draft_data = {
                'is_draft': is_draft,
                'is_auto_save': is_auto_save,
                'top_tray_id': top_tray_id,
                'top_tray_qty': top_tray_qty,
                'delink_trays': delink_trays,
                'acceptance_remarks': data.get('acceptance_remarks', '').strip()
            }

            # Save to draft store
            draft_obj, created = IQF_Draft_Store.objects.update_or_create(
                lot_id=lot_id,
                draft_type='accepted_tray',
                defaults={
                    'batch_id': batch_id,
                    'user': request.user,
                    'draft_data': draft_data
                }
            )

            # ✅ For auto-save: Do NOT change lot status
            # Only change status to "Draft" for manual draft saves
            if is_draft and not is_auto_save:
                try:
                    lot_obj = TotalStockModel.objects.get(lot_id=lot_id)
                    lot_obj.iqf_current_status = "Draft"
                    lot_obj.save()
                    print(f"✅ [IQF Manual Draft] Updated lot {lot_id} status to 'Draft'")
                except Exception as status_error:
                    print(f"⚠️ [IQF Manual Draft] Failed to update lot status: {status_error}")
            else:
                print(f"✅ [IQF Auto-Save] Saved accepted tray data for lot {lot_id}, keeping status unchanged")

            return Response({
                'success': True,
                'message': 'Draft saved successfully' if is_draft else 'Auto-save completed',
                'draft_data': draft_data
            })

        except Exception as e:
            import traceback
            traceback.print_exc()
            return Response({
                'success': False,
                'error': f'Failed to save accepted tray draft: {str(e)}'
            }, status=500)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_lot_id_for_tray(request):
    """
    Get lot_id for a given tray_id to support barcode scanner functionality in IQF
    
    This endpoint searches across multiple tables to find the lot_id associated with a tray_id:
    1. IQFTrayId table (primary)
    2. TotalStockModel table (secondary)
    3. TrayId table (fallback)
    
    Returns JSON response with lot_id if found, or error message if not found.
    """
    tray_id = request.GET.get('tray_id', '').strip()
    
    if not tray_id:
        return JsonResponse({
            'success': False,
            'error': 'tray_id parameter is required'
        })
    
    try:
        # Strategy 1: Check IQFTrayId table first (most specific to IQF)
        try:
            iqf_tray = IQFTrayId.objects.get(tray_id=tray_id)
            return JsonResponse({
                'success': True,
                'lot_id': iqf_tray.lot_id,
                'source': 'IQFTrayId',
                'message': f'Tray {tray_id} found in IQF system'
            })
        except IQFTrayId.DoesNotExist:
            pass
            
        # Strategy 2: Check TotalStockModel table
        try:
            stock_model = TotalStockModel.objects.get(lot_id=tray_id)
            return JsonResponse({
                'success': True,
                'lot_id': stock_model.lot_id,
                'source': 'TotalStockModel',
                'message': f'Tray {tray_id} found as lot_id in system'
            })
        except TotalStockModel.DoesNotExist:
            pass
            
        # Strategy 3: Check main TrayId table (fallback)
        try:
            tray_obj = TrayId.objects.get(tray_id=tray_id)
            return JsonResponse({
                'success': True,
                'lot_id': tray_obj.lot_id,
                'source': 'TrayId',
                'message': f'Tray {tray_id} found in main tray system'
            })
        except TrayId.DoesNotExist:
            pass
            
        # Tray not found in any table
        return JsonResponse({
            'success': False,
            'error': f'Tray {tray_id} not found in system',
            'message': 'Tray will need to be entered manually'
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Database error: {str(e)}'
        })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def iqf_get_available_new_tray(request):
    """
    Get an available new tray for the given lot_id and tray_type.
    Returns a tray that has no lot_id assigned and matches the expected tray_type.
    """
    lot_id = request.GET.get('lot_id')
    if not lot_id:
        return JsonResponse({
            'success': False,
            'error': 'Missing lot_id'
        })
    
    try:
        # Get expected tray type from the batch
        selected_lot_obj = TotalStockModel.objects.filter(lot_id=lot_id).first()
        if not selected_lot_obj or not hasattr(selected_lot_obj, 'batch_id'):
            return JsonResponse({
                'success': False,
                'error': 'Invalid lot_id or batch not found'
            })
        
        expected_tray_type = getattr(selected_lot_obj.batch_id, 'tray_type', None)
        
        # Find available new trays (lot_id is None or empty, and tray_type matches if expected_tray_type is set)
        query = TrayId.objects.filter(
            Q(lot_id__isnull=True) | Q(lot_id='') | Q(lot_id='None')
        )
        
        if expected_tray_type:
            query = query.filter(tray_type=expected_tray_type)
        
        # Get the first available tray
        available_tray = query.first()
        
        if available_tray:
            return JsonResponse({
                'success': True,
                'tray_id': available_tray.tray_id,
                'tray_type': available_tray.tray_type
            })
        else:
            return JsonResponse({
                'success': False,
                'error': 'No available new trays found'
            })
            
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Database error: {str(e)}'
        })


# ✅ NEW: Helper functions for IQF (adapted from Brass QC)

def get_iqf_available_quantities_with_session_allocations(lot_id, current_session_allocations):
    """
    Calculate available tray quantities and ACTUAL free space for IQF
    """
    try:
        # Get original distribution and track free space separately
        original_distribution = get_iqf_original_tray_distribution(lot_id)
        original_capacities = get_iqf_tray_capacities_for_lot(lot_id)
        
        available_quantities = original_distribution.copy()
        
        # First, apply saved rejections
        saved_rejections = IQF_Rejected_TrayScan.objects.filter(lot_id=lot_id).order_by('id')
        
        for rejection in saved_rejections:
            # Assuming rejection quantity field is 'rejected_quantity' or similar?
            # Need to check model. Assuming similar to BrassQC: rejected_tray_quantity?
            # Or is it implicity full tray?
            # Re-read IQF models... Assuming 'rejected_tray_quantity' based on pattern.
            rejected_qty = getattr(rejection, 'rejected_tray_quantity', 0) or 0
            # If rejected_qty is 0, maybe it means full tray? No, 0 usually means nothing.
            
            # If rejected_qty is missing, fallback to 0.
            
            tray_id = getattr(rejection, 'rejected_tray_id', None)
            
            if rejected_qty <= 0:
                continue

            # Standard reduction logic
            # Simplify: treat all as consumption
            available_quantities = iqf_reduce_quantities_optimally(available_quantities, rejected_qty)
        
        # Then, apply current session allocations
        for allocation in current_session_allocations:
            qty = int(allocation.get('qty', 0))
            if qty > 0:
                available_quantities = iqf_reduce_quantities_optimally(available_quantities, qty)
        
        # Calculate ACTUAL current free space
        actual_free_space = 0
        if len(available_quantities) <= len(original_capacities):
            for i, qty in enumerate(available_quantities):
                if i < len(original_capacities):
                    capacity = original_capacities[i]
                    actual_free_space += max(0, capacity - qty)
        
        return available_quantities, actual_free_space

    except Exception as e:
        print(f"[IQF Session Validation] Error: {e}")
        return [], 0

def get_iqf_original_tray_distribution(lot_id):
    """
    Get original tray quantity distribution for IQF
    """
    try:
        trays = IQFTrayId.objects.filter(lot_id=lot_id).exclude(
            rejected_tray=True
        ).order_by('id') # Or date?
        
        quantities = []
        for t in trays:
            q = getattr(t, 'tray_quantity', 0)
            if q > 0:
                quantities.append(q)
        return quantities
    except:
        return []

def get_iqf_tray_capacities_for_lot(lot_id):
    """
    Get capacities for IQF trays
    """
    try:
        trays = IQFTrayId.objects.filter(lot_id=lot_id).exclude(
            rejected_tray=True
        ).order_by('id')
        capacities = []
        for t in trays:
            c = getattr(t, 'tray_capacity', 12) or 12
            capacities.append(c)
        return capacities
    except:
        return []

def iqf_reduce_quantities_optimally(available_quantities, qty_to_reduce):
    """
    Reduce quantities optimally for IQF
    """
    quantities = available_quantities.copy()
    remaining = qty_to_reduce
    
    # Consume from larger trays first? Or smaller?
    # Brass QC strategy: Existing trays consume optimally.
    # Let's consume from LARGEST first to mimic Brass QC "Existing" logic.
    sorted_indices = sorted(range(len(quantities)), key=lambda i: quantities[i], reverse=True)
    
    for i in sorted_indices:
        if remaining <= 0: break
        current_qty = quantities[i]
        if current_qty > 0:
            consume = min(remaining, current_qty)
            quantities[i] -= consume
            remaining -= consume
    return quantities
