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
            print(f"[IQFCompleteTableTrayIdListAPIView] Fetching trays for completed lot_id: {lot_id}")

            all_trays = []
            delinked_trays = []

            def resolve_qty(tray_id, *base_values):
                qty_candidates = []
                for v in base_values:
                    try:
                        qty_candidates.append(int(v or 0))
                    except Exception:
                        pass
                for model in [IQFTrayId, BrassTrayId, BrassAuditTrayId, IPTrayId, DPTrayId_History]:
                    try:
                        q = model.objects.filter(lot_id=lot_id, tray_id=tray_id).values_list('tray_quantity', flat=True).first()
                        if q is not None:
                            qty_candidates.append(int(q or 0))
                    except Exception:
                        pass
                return max(qty_candidates) if qty_candidates else 0

            # Accepted trays from saved IQF acceptance store
            accepted_store_trays = IQF_Accepted_TrayID_Store.objects.filter(lot_id=lot_id)
            accepted_ids = {t.tray_id for t in accepted_store_trays if t.tray_id}
            for store_tray in accepted_store_trays:
                all_trays.append({
                    "tray_id": store_tray.tray_id,
                    "tray_quantity": store_tray.tray_qty or 0,
                    "rejected_tray": False,
                    "delink_tray": False,
                    "iqf_reject_verify": False,
                    "new_tray": False,
                    "IP_tray_verified": True,
                    "top_tray": False
                })

            # Rejected qty map from scan rows (fallback only)
            scan_qty_map = {}
            scan_top_map = {}
            for rec in IQF_Rejected_TrayScan.objects.filter(lot_id=lot_id).order_by('-top_tray', 'id'):
                tid = (rec.tray_id or '').strip()
                if not tid:
                    continue
                try:
                    q = int(rec.rejected_tray_quantity or 0)
                except (ValueError, TypeError):
                    q = 0
                scan_qty_map[tid] = scan_qty_map.get(tid, 0) + q
                scan_top_map[tid] = scan_top_map.get(tid, False) or bool(rec.top_tray)

            # Rejected trays from IQFTrayId are source of truth for tray-level state
            rejected_iqf_trays = IQFTrayId.objects.filter(
                lot_id=lot_id,
                rejected_tray=True,
                delink_tray=False
            ).exclude(
                tray_id__in=accepted_ids
            ).order_by('-top_tray', 'id')

            for rej_tray in rejected_iqf_trays:
                master_qty = int(rej_tray.tray_quantity or 0)
                scan_qty = int(scan_qty_map.get(rej_tray.tray_id, 0))
                tray_qty = resolve_qty(rej_tray.tray_id, master_qty, scan_qty)
                top_tray = bool(rej_tray.top_tray) or bool(scan_top_map.get(rej_tray.tray_id, False))

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

            # Delinked trays: persisted flags only (no inferred candidates)
            delinked_ids = set(
                IQFTrayId.objects.filter(lot_id=lot_id, delink_tray=True).values_list('tray_id', flat=True)
            )
            delinked_ids |= set(
                DPTrayId_History.objects.filter(lot_id=lot_id, delink_tray=True).values_list('tray_id', flat=True)
            )
            delinked_ids |= set(
                BrassTrayId.objects.filter(lot_id=lot_id, delink_tray=True).values_list('tray_id', flat=True)
            )
            delinked_ids |= set(
                BrassAuditTrayId.objects.filter(lot_id=lot_id, delink_tray=True).values_list('tray_id', flat=True)
            )
            delinked_ids = {t for t in delinked_ids if t and t not in accepted_ids}

            for tray_id in sorted(delinked_ids):
                delink_info = {
                    "tray_id": tray_id,
                    "tray_quantity": 0,
                    "rejected_tray": False,
                    "delink_tray": True,
                    "iqf_reject_verify": False,
                    "new_tray": False,
                    "IP_tray_verified": False,
                    "top_tray": False
                }
                delinked_trays.append(delink_info)
                all_trays.append(delink_info)

            return Response({
                "success": True,
                "trays": all_trays,
                "delinked_trays": delinked_trays,
                "total_trays": len(all_trays),
                "total_delinked": len(delinked_trays)
            })

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[IQFCompleteTableTrayIdListAPIView] Error: {str(e)}")
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
            
            def resolve_qty(tray_id, *base_values):
                qty_candidates = []
                for v in base_values:
                    try:
                        qty_candidates.append(int(v or 0))
                    except Exception:
                        pass
                if tray_id:
                    for model in [IQFTrayId, BrassTrayId, BrassAuditTrayId, IPTrayId, DPTrayId_History]:
                        try:
                            q = model.objects.filter(lot_id=lot_id, tray_id=tray_id).values_list('tray_quantity', flat=True).first()
                            if q is not None:
                                qty_candidates.append(int(q or 0))
                        except Exception:
                            pass
                return max(qty_candidates) if qty_candidates else 0
            
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
            
            # ============================
            
            # ============================
            # STEP 4: Build rejected tray list using the mappings
            # =============================
            for rej_tray in rejected_iqf_trays:
                if rej_tray.tray_id in explicit_mappings:
                    rejection_info = explicit_mappings[rej_tray.tray_id]
                    mapped_qty = int(rejection_info.get('qty') or 0)
                    tray_qty = resolve_qty(rej_tray.tray_id, mapped_qty, rej_tray.tray_quantity)
                    top_tray = rejection_info['top_tray'] if rejection_info['top_tray'] else rej_tray.top_tray
                    rejection_reason = rejection_info.get('rejection_reason', 'N/A')
                    rejection_reason_id = rejection_info.get('rejection_reason_id', '')
                    print(f"   ✅ Rejected Tray: ID={rej_tray.tray_id}, Qty={tray_qty}, Top={top_tray}")
                else:
                    # Fallback: use tray_quantity from IQFTrayId if no mapping found
                    tray_qty = resolve_qty(rej_tray.tray_id, rej_tray.tray_quantity)
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
            
            # ✅ All rejections should now be assigned to specific trays via explicit_mappings
            # No need to add unassigned entries - all rejections come with tray IDs from IQF_Rejected_TrayScan
            
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
    """
    🔥 FRESH AUTO-ALLOCATION LOGIC FOR IQF REJECTION SYSTEM
    This completely replaces the old complex logic with simple auto-allocation
    that distributes rejection quantities irrespective of tray categories.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            data = request.data if hasattr(request, 'data') else json.loads(request.body.decode('utf-8'))
            lot_id = data.get('lot_id')
            batch_id = data.get('batch_id')
            tray_rejections = data.get('tray_rejections', [])  # [{reason_id, quantity}]
            accepted_trays = data.get('accepted_trays', [])    # [{tray_id, qty}] - from frontend
            frontend_accepted_tray_ids = [tray.get('tray_id') for tray in accepted_trays if tray.get('tray_id')]
            acceptance_remarks = data.get('acceptance_remarks', '').strip()
            delink_confirmed = data.get('delink_confirmed', False)

            print(f"\n{'='*80}")
            print(f"🚀 [FRESH IQF REJECTION] Auto-allocation system starting")
            print(f"{'='*80}")
            print(f"   Lot ID: {lot_id}")
            print(f"   Batch ID: {batch_id}")
            print(f"   Frontend Rejection Data: {len(tray_rejections)} entries")
            for idx, rej in enumerate(tray_rejections, 1):
                print(f"      {idx}. reason_id={rej.get('reason_id')}, qty={rej.get('quantity')}")
            print(f"   Frontend Accepted Trays: {len(accepted_trays)} entries")
            print(f"   Acceptance Remarks: '{acceptance_remarks}'")
            print(f"{'='*80}\n")

            if not lot_id:
                return Response({'success': False, 'error': 'Missing lot_id'}, status=400)

            # Step 1: Calculate total rejection quantity
            total_rejection_qty = sum(int(item['quantity']) for item in tray_rejections if int(item['quantity']) > 0)
            print(f"📊 Total rejection quantity: {total_rejection_qty}")

            # Step 2: Get original available trays BEFORE saving frontend accepted trays
            # Use fallback logic to get all eligible trays
            accepted_tray_ids = []  # not saved yet
            stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
            use_audit = bool(getattr(stock, 'send_brass_audit_to_iqf', False))
            rejected_scan_tray_ids = set(
                IQF_Rejected_TrayScan.objects.filter(lot_id=lot_id).exclude(tray_id='').values_list('tray_id', flat=True)
            )
            if rejected_scan_tray_ids:
                eligible_tray_ids = rejected_scan_tray_ids
            else:
                if use_audit:
                    upstream_ids = set(
                        Brass_Audit_Rejected_TrayScan.objects.filter(lot_id=lot_id).exclude(rejected_tray_id='').values_list('rejected_tray_id', flat=True)
                    )
                else:
                    upstream_ids = set(
                        Brass_QC_Rejected_TrayScan.objects.filter(lot_id=lot_id).exclude(rejected_tray_id='').values_list('rejected_tray_id', flat=True)
                    )
                if upstream_ids:
                    eligible_tray_ids = upstream_ids
                else:
                    eligible_tray_ids = set(
                        IQFTrayId.objects.filter(lot_id=lot_id, IP_tray_verified=True).values_list('tray_id', flat=True)
                    )
                    eligible_tray_ids |= set(
                        DPTrayId_History.objects.filter(lot_id=lot_id, IP_tray_verified=True).values_list('tray_id', flat=True)
                    )
                    if not eligible_tray_ids:
                        eligible_tray_ids = set(
                            TrayId.objects.filter(lot_id=lot_id, IP_tray_verified=True).values_list('tray_id', flat=True)
                        )
            eligible_tray_ids = {t for t in eligible_tray_ids if t}
            eligible_tray_ids = set(
                TrayId.objects.filter(
                    tray_id__in=eligible_tray_ids,
                    new_tray=False,
                    IP_tray_verified=True
                ).values_list('tray_id', flat=True)
            )
            # ✅ IMPORTANT: Save all lot tray IDs before removing accepted trays
            # This is used to classify accepted trays as EXISTING (from lot) vs NEW (newly created)
            all_lot_tray_ids = eligible_tray_ids.copy()
            
            # Remove accepted tray IDs from eligible list for rejection allocation
            eligible_tray_ids = eligible_tray_ids - set(frontend_accepted_tray_ids)
            def _best_qty_for_tray(tid):
                qtys = []
                for model in [IQFTrayId, DPTrayId_History, BrassTrayId, BrassAuditTrayId, IPTrayId]:
                    try:
                        q = model.objects.filter(lot_id=lot_id, tray_id=tid).values_list('tray_quantity', flat=True).first()
                        if q is not None:
                            qtys.append(int(q or 0))
                    except Exception:
                        pass
                try:
                    master_q = TrayId.objects.filter(tray_id=tid).values_list('tray_quantity', flat=True).first()
                    if master_q is not None:
                        qtys.append(int(master_q or 0))
                except Exception:
                    pass
                return max(qtys) if qtys else 0
            original_available_trays = []
            for tray_id in eligible_tray_ids:
                qty = _best_qty_for_tray(tray_id)
                if qty > 0:
                    master_tray = TrayId.objects.filter(tray_id=tray_id).first()
                    if master_tray:
                        tray_data = {
                            'tray_id': tray_id,
                            'tray_quantity': qty,
                            'tray_capacity': int(master_tray.tray_capacity or qty or 12),
                            'tray_type': getattr(master_tray, 'tray_type', ''),
                            'top_tray': False,
                            'rejected_tray': False
                        }
                        original_available_trays.append(tray_data)
            original_available_tray_ids = set(tray['tray_id'] for tray in original_available_trays)
            
            # Step 2a: Classify accepted trays as NEW or EXISTING
            # ✅ FIXED: Use all_lot_tray_ids for classification, not original_available_tray_ids
            # A tray is EXISTING if it's from the original lot, even if being accepted
            new_trays_used = []
            existing_trays_used = []
            
            if accepted_trays:
                for acc_tray in accepted_trays:
                    tray_id = acc_tray.get('tray_id')
                    if tray_id:
                        # Classify as NEW or EXISTING tray based on whether it was in the original lot
                        if tray_id in all_lot_tray_ids:
                            existing_trays_used.append(tray_id)
                            print(f"   📦 Existing tray used: {tray_id}")
                        else:
                            new_trays_used.append({'tray_id': tray_id, 'qty': acc_tray.get('qty', 0)})
                            print(f"   🆕 NEW tray used: {tray_id} (qty: {acc_tray.get('qty', 0)})")
            
            # Step 2b: Check if delink is required for NEW tray usage
            if new_trays_used:
                print(f"🔗 [DELINK CHECK] Processing {len(new_trays_used)} NEW trays for delink requirements...")
                
                # Calculate total quantity displaced by new tray usage
                total_new_qty = sum(tray['qty'] for tray in new_trays_used)
                print(f"   📊 Total quantity from NEW trays: {total_new_qty}")
                
                # Get existing trays that should be delinked (not used by frontend)
                trays_to_delink = []
                remaining_qty_to_displace = total_new_qty
                
                # Sort original available trays by quantity descending (largest first) for optimal delinking
                sorted_original_trays = sorted(original_available_trays, key=lambda x: x['tray_quantity'], reverse=True)
                
                for orig_tray in sorted_original_trays:
                    if orig_tray['tray_id'] not in existing_trays_used and remaining_qty_to_displace > 0:
                        # This existing tray should be delinked
                        qty_to_delink = min(remaining_qty_to_displace, orig_tray['tray_quantity'])
                        trays_to_delink.append({
                            'tray_id': orig_tray['tray_id'],
                            'qty_delinked': qty_to_delink,
                            'original_qty': orig_tray['tray_quantity']
                        })
                        remaining_qty_to_displace -= qty_to_delink
                        print(f"   🔗 Will delink: {orig_tray['tray_id']} (qty: {qty_to_delink})")
                
                if trays_to_delink:
                    print(f"   📋 Delink required for {len(trays_to_delink)} trays")
                    if not delink_confirmed:
                        return Response({
                            'success': False,
                            'delink_required': True,
                            'delink_trays': trays_to_delink,
                            'message': f'Delink required for {len(trays_to_delink)} trays due to new tray usage. Please confirm delink before proceeding.'
                        }, status=200)
                    else:
                        # Perform delink
                        for delink_item in trays_to_delink:
                            tray_id = delink_item['tray_id']
                            qty_delinked = delink_item['qty_delinked']
                            original_qty = delink_item['original_qty']
                            tray_obj = IQFTrayId.objects.filter(tray_id=tray_id, lot_id=lot_id).first()
                            if tray_obj:
                                # Reduce tray qty by delinked amount (do not set entire tray to 0)
                                remaining_qty = max(0, original_qty - qty_delinked)
                                tray_obj.tray_quantity = remaining_qty
                                tray_obj.delink_tray = True
                                tray_obj.save()
                                print(f"   🔗 Delinked tray: {tray_id}, qty_delinked: {qty_delinked}, remaining: {remaining_qty}")
            
            # Step 2c: Update EXISTING accepted trays in IQFTrayId to reflect consumed quantities
            if existing_trays_used and accepted_trays:
                print(f"🔄 [ACCEPTANCE] Updating {len(existing_trays_used)} EXISTING trays with accepted quantities...")
                for acc_tray in accepted_trays:
                    tray_id = acc_tray.get('tray_id')
                    accepted_qty = acc_tray.get('qty', 0)
                    
                    if tray_id in existing_trays_used and accepted_qty > 0:
                        tray_obj = IQFTrayId.objects.filter(tray_id=tray_id, lot_id=lot_id).first()
                        if tray_obj:
                            # Reduce tray quantity by accepted amount
                            original_qty = tray_obj.tray_quantity
                            remaining_qty = max(0, original_qty - accepted_qty)
                            tray_obj.tray_quantity = remaining_qty
                            tray_obj.save()
                            print(f"   📊 Updated EXISTING tray {tray_id}: {original_qty} -> {remaining_qty} (accepted: {accepted_qty})")
            
            # Step 3: Get available trays for auto-allocation (now with accepted trays excluded)
            available_trays = get_iqf_available_trays_for_allocation(lot_id)
            if not available_trays:
                return Response({
                    'success': False, 
                    'error': f'No available trays found for lot {lot_id}. All trays may be accepted.'
                }, status=400)

            total_available_qty = sum(tray['tray_quantity'] for tray in available_trays)
            print(f"📋 Available trays: {len(available_trays)}, Total available qty: {total_available_qty}")

            # Step 4: Check if this is a full lot rejection or partial rejection
            total_stock = TotalStockModel.objects.filter(lot_id=lot_id).first() 
            physical_qty = total_stock.iqf_physical_qty if total_stock and total_stock.iqf_physical_qty else 0
            
            is_full_lot_rejection = (total_rejection_qty == physical_qty)
            print(f"🔍 Physical qty: {physical_qty}, Is full lot rejection: {is_full_lot_rejection}")

            # Step 5: Handle full lot rejection (simplified)
            if is_full_lot_rejection:
                print("🔄 Processing as FULL LOT REJECTION")
                
                # Mark all trays as rejected and delinked
                for tray in available_trays:
                    tray_id = tray['tray_id']
                    tray_qty = tray['tray_quantity']
                    
                    # Create rejection record
                    reason_obj = None
                    if tray_rejections:
                        reason_id = tray_rejections[0].get('reason_id')
                        reason_obj = IQF_Rejection_Table.objects.filter(rejection_reason_id=reason_id).first()
                    
                    IQF_Rejected_TrayScan.objects.create(
                        lot_id=lot_id,
                        tray_id=tray_id,
                        rejected_tray_quantity=str(tray_qty),
                        rejection_reason=reason_obj,
                        user=request.user,
                        top_tray=False
                    )
                    
                    # Update IQFTrayId - mark as rejected and delinked
                    iqf_tray = IQFTrayId.objects.filter(tray_id=tray_id, lot_id=lot_id).first()
                    if iqf_tray:
                        iqf_tray.rejected_tray = True
                        iqf_tray.delink_tray = True
                        iqf_tray.lot_id = None  # Remove from lot
                        iqf_tray.batch_id = None
                        iqf_tray.save()
                    
                    print(f"   ✅ Fully rejected and delinked: {tray_id}")

                # Create rejection reason store
                if total_rejection_qty > 0:
                    reason_ids = [item['reason_id'] for item in tray_rejections if int(item['quantity']) > 0]
                    reasons = IQF_Rejection_Table.objects.filter(rejection_reason_id__in=reason_ids)
                    reason_store = IQF_Rejection_ReasonStore.objects.create(
                        lot_id=lot_id,
                        user=request.user,
                        total_rejection_quantity=total_rejection_qty,
                        batch_rejection=True
                    )
                    reason_store.rejection_reason.set(reasons)

                # Update TotalStockModel for full rejection
                if total_stock:
                    total_stock.iqf_rejection = True
                    total_stock.last_process_module = "IQF"
                    total_stock.next_process_module = "Jig Loading"
                    total_stock.iqf_last_process_date_time = timezone.now()
                    total_stock.save()
                    print("   ✅ Updated TotalStockModel for full rejection")

                return Response({
                    'success': True, 
                    'message': 'Full lot rejection processed with auto-allocation',
                    'allocation_type': 'full_rejection',
                    'total_rejected': total_rejection_qty
                })

            # Step 5: Handle partial rejection with auto-allocation
            if total_rejection_qty > 0 and not acceptance_remarks:
                return Response({
                    'success': False, 
                    'error': 'Acceptance remarks are required for partial rejections'
                }, status=400)

            print("🔄 Processing as PARTIAL REJECTION with auto-allocation")
            
            # Step 6: Run auto-allocation algorithm (now with proper exclusions)
            allocation_results = auto_allocate_iqf_rejection(
                lot_id=lot_id,
                total_rejection_qty=total_rejection_qty, 
                available_trays=available_trays
            )
            print(f"   📋 Auto-allocation excluded {len(frontend_accepted_tray_ids)} accepted trays: {frontend_accepted_tray_ids}")

            if allocation_results['unallocated_qty'] > 0:
                return Response({
                    'success': False,
                    'error': f"Cannot allocate {allocation_results['unallocated_qty']} qty - insufficient available trays"
                }, status=400)

            print(f"📊 Auto-allocation results:")
            print(f"   Rejected: {allocation_results['total_rejected']}")
            print(f"   Accepted: {allocation_results['total_accepted']}")
            print(f"   Delinked trays: {len(allocation_results['delink_trays'])}")
            print(f"   New trays needed: {len(allocation_results['new_trays_needed'])}")

            # Step 6.5: Pre-seed IQF_Accepted_TrayID_Store for trays the user accepted via the UI.
            # apply_iqf_auto_allocation_results Step 3.5 will mark these is_save=True so that
            # BrassPickTableView and create_brass_tray_instances can read the correct accepted qty/trays.
            for _acc in accepted_trays:
                _tid = _acc.get('tray_id')
                _tqty = _acc.get('qty', 0)
                if _tid:
                    IQF_Accepted_TrayID_Store.objects.update_or_create(
                        lot_id=lot_id,
                        tray_id=_tid,
                        defaults={
                            'tray_qty': _tqty,
                            'user': request.user,
                            'accepted_comment': acceptance_remarks,
                            'is_save': False
                        }
                    )
            print(f"   ✅ Step 6.5: Pre-seeded IQF_Accepted_TrayID_Store with {len(accepted_trays)} accepted tray(s)")

            # Step 7: Apply allocation results to database
            apply_result = apply_iqf_auto_allocation_results(
                lot_id=lot_id,
                batch_id=batch_id,
                allocation_results=allocation_results,
                rejection_reasons=tray_rejections,
                user=request.user,
                acceptance_remarks=acceptance_remarks
            )

            if not apply_result['success']:
                return Response({
                    'success': False,
                    'error': f"Failed to apply allocation: {apply_result.get('error')}"
                }, status=500)

            # Step 8: Clear any existing drafts
            IQF_Draft_Store.objects.filter(
                lot_id=lot_id,
                draft_type__in=['lot_rejection', 'tray_rejection']
            ).delete()
            print("   ✅ Cleared existing drafts")

            # Step 9: Success response with allocation details
            response_data = {
                'success': True,
                'message': 'Partial rejection processed with auto-allocation successfully',
                'allocation_type': 'partial_rejection',
                'allocation_summary': {
                    'total_rejected': allocation_results['total_rejected'],
                    'total_accepted': allocation_results['total_accepted'],
                    'delinked_trays_count': len(allocation_results['delink_trays']),
                    'new_trays_created': len(allocation_results['new_trays_needed']),
                    'rejection_distribution': allocation_results['rejection_distribution'],
                    'acceptance_trays': allocation_results['acceptance_trays'],
                    'new_trays': allocation_results['new_trays_needed']
                }
            }

            print(f"✅ [FRESH IQF REJECTION] Auto-allocation completed successfully")
            print(f"   Summary: {allocation_results['total_rejected']} rejected, {allocation_results['total_accepted']} accepted")
            print(f"{'-'*80}\n")

            return Response(response_data)

        except Exception as e:
            print(f"\n❌ [FRESH IQF REJECTION] Error in auto-allocation system:")
            import traceback
            traceback.print_exc()
            return Response({
                'success': False, 
                'error': f'Auto-allocation failed: {str(e)}'
            }, status=500)

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
                print(f"  - Total Rejected Qty: {total_iqf_rejected_qty}")
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
        print(f"\n{'='*80}")
        print(f"📋 [VERIFY POPUP DATA] Getting rejection data for lot_id: {lot_id}")
        print(f"{'='*80}")
        
        rejection_rows = []

        def _resolve_qty_from_lot_sources(tray_id, scan_qty=0):
            qty_candidates = []
            try:
                qty_candidates.append(int(scan_qty or 0))
            except Exception:
                pass
            if not tray_id:
                return max(qty_candidates) if qty_candidates else 0
            for model in [IQFTrayId, BrassTrayId, BrassAuditTrayId, IPTrayId, DPTrayId_History]:
                try:
                    q = model.objects.filter(lot_id=lot_id, tray_id=tray_id).values_list('tray_quantity', flat=True).first()
                    if q is not None:
                        qty_candidates.append(int(q or 0))
                except Exception:
                    pass
            return max(qty_candidates) if qty_candidates else 0
        
        # ✅ FIXED: Aggregate ALL rejections properly without separating by reason
        # Get total rejection quantity from IQF_Rejection_ReasonStore
        reason_store = IQF_Rejection_ReasonStore.objects.filter(lot_id=lot_id).prefetch_related('rejection_reason').order_by('-id').first()
        
        if reason_store:
            # Get all reasons for this store
            all_reasons = list(reason_store.rejection_reason.all())
            total_qty = reason_store.total_rejection_quantity or 0
            
            if all_reasons:
                # Create single aggregated entry with all reasons
                reason_texts = [r.rejection_reason for r in all_reasons]
                reason_ids = [r.rejection_reason_id for r in all_reasons]
                
                rejection_rows.append({
                    'tray_id': '',
                    'qty': total_qty,
                    'reason': ' | '.join(reason_texts),
                    'reason_id': reason_ids[0] if reason_ids else '',
                    'brass_rejection_qty': total_qty
                })
                
                print(f"✅ [REJECTION SUMMARY] Aggregated from IQF_Rejection_ReasonStore:")
                print(f"   - Total Qty: {total_qty}")
                print(f"   - Reasons: {reason_texts}")
        
        # Fallback: If no reason store, get from Brass QC data
        if not rejection_rows:
            brass_rejected_trays = Brass_QC_Rejected_TrayScan.objects.filter(lot_id=lot_id)
            if brass_rejected_trays.exists():
                total_qty = sum(int(t.rejected_tray_quantity or 0) for t in brass_rejected_trays)
                reason_texts = []
                reason_ids = []
                
                for tray in brass_rejected_trays.distinct('rejection_reason'):
                    if tray.rejection_reason:
                        reason_texts.append(tray.rejection_reason.rejection_reason)
                        reason_ids.append(tray.rejection_reason.rejection_reason_id)
                
                rejection_rows.append({
                    'tray_id': '',
                    'qty': total_qty,
                    'reason': ' | '.join(reason_texts),
                    'reason_id': reason_ids[0] if reason_ids else '',
                    'brass_rejection_qty': total_qty
                })
                
                print(f"✅ [REJECTION SUMMARY] Fallback from Brass_QC_Rejected_TrayScan:")
                print(f"   - Total Qty: {total_qty}")
                print(f"   - Reasons: {reason_texts}")

        # Accepted trays (unchanged)
        accepted_trays = []
        for obj in IQF_Accepted_TrayID_Store.objects.filter(lot_id=lot_id).select_related('user'):
            accepted_trays.append({
                'tray_id': obj.tray_id,
                'tray_qty': obj.tray_qty,
                'accepted_comment': obj.accepted_comment,
                'is_draft': obj.is_draft,
                'is_save': obj.is_save,
                'user': obj.user.username if obj.user else None,
            })
        
        print(f"\n📦 [ACCEPTED TRAYS IN POPUP]")
        if accepted_trays:
            for acc_tray in accepted_trays:
                print(f"   ✅ Tray ID: {acc_tray['tray_id']}, Qty: {acc_tray['tray_qty']}")
        else:
            print(f"   ⚠️ No accepted trays")

        # ✅ FIXED: Get rejected tray IDs with top_tray information from BOTH IQF and Brass QC sources
        rejected_tray_ids = []
        
        # Priority 1: Check IQF_Rejected_TrayScan (if IQF has done its own rejection)
        iqf_rejected_trays = IQF_Rejected_TrayScan.objects.filter(lot_id=lot_id).select_related('rejection_reason', 'user').order_by('id')
        
        print(f"\n📊 [REJECTED TRAYS IN POPUP]")
        print(f"   🔍 Checking IQF_Rejected_TrayScan: Found {iqf_rejected_trays.count()} records")
        
        # ✅ ENHANCED DEBUG: Print each record BEFORE aggregation
        for idx, rec in enumerate(iqf_rejected_trays, 1):
            qty_val = int(rec.rejected_tray_quantity) if rec.rejected_tray_quantity else 0
            reason_text = rec.rejection_reason.rejection_reason if rec.rejection_reason else 'N/A'
            print(f"      Record {idx}: tray_id='{rec.tray_id}', qty={qty_val}, reason='{reason_text}', top_tray={rec.top_tray}")
        
        if iqf_rejected_trays.exists():
            # IQF has rejection data - aggregate by tray_id
            tray_aggregation = {}
            
            for tray_obj in iqf_rejected_trays:
                tray_key = tray_obj.tray_id or 'NO_TRAY'  # Use 'NO_TRAY' as key if tray_id is None
                
                # Convert qty to int
                qty_int = int(tray_obj.rejected_tray_quantity) if tray_obj.rejected_tray_quantity else 0
                
                # Check if this tray is marked as top tray in IQFTrayId model as well
                iqf_tray_id_obj = IQFTrayId.objects.filter(lot_id=lot_id, tray_id=tray_obj.tray_id).first()
                is_top_tray = tray_obj.top_tray or (iqf_tray_id_obj and iqf_tray_id_obj.top_tray)
                
                # Aggregate by tray_id
                if tray_key not in tray_aggregation:
                    tray_aggregation[tray_key] = {
                        'tray_id': tray_obj.tray_id,
                        'qty': 0,
                        'reasons': [],
                        'top_tray': is_top_tray,
                        'user': tray_obj.user.username if tray_obj.user else None,
                    }
                
                # Sum quantities
                tray_aggregation[tray_key]['qty'] += qty_int
                
                # Collect unique reasons
                reason_text = tray_obj.rejection_reason.rejection_reason if tray_obj.rejection_reason else 'N/A'
                reason_id = tray_obj.rejection_reason.rejection_reason_id if tray_obj.rejection_reason else ''
                if reason_text not in [r['reason'] for r in tray_aggregation[tray_key]['reasons']]:
                    tray_aggregation[tray_key]['reasons'].append({
                        'reason': reason_text,
                        'reason_id': reason_id
                    })
                
                # Use highest top_tray value
                tray_aggregation[tray_key]['top_tray'] = tray_aggregation[tray_key]['top_tray'] or is_top_tray
            
            # ✅ ENHANCED DEBUG: Print aggregation results
            print(f"   ℹ️ After aggregation: {len(tray_aggregation)} unique tray keys")
            for tray_key, info in tray_aggregation.items():
                print(f"      Aggregated key '{tray_key}': qty={info['qty']}, reasons={[r['reason'] for r in info['reasons']]}, top_tray={info['top_tray']}")
            
            # Convert aggregation dict to list
            for tray_key, tray_info in tray_aggregation.items():
                reason_text = ' | '.join([r['reason'] for r in tray_info['reasons']])
                reason_id = tray_info['reasons'][0]['reason_id'] if tray_info['reasons'] else ''
                final_qty = _resolve_qty_from_lot_sources(tray_info['tray_id'], tray_info['qty'])
                
                rejected_tray_ids.append({
                    'tray_id': tray_info['tray_id'],
                    'qty': str(final_qty),
                    'reason': reason_text,
                    'reason_id': reason_id,
                    'top_tray': tray_info['top_tray'],
                    'user': tray_info['user'],
                })
        else:
            # Priority 2: Check Brass_QC_Rejected_TrayScan (initial rejection from Brass QC)
            brass_rejected_trays = Brass_QC_Rejected_TrayScan.objects.filter(lot_id=lot_id).select_related('rejection_reason', 'user').order_by('id')
            
            print(f"   🔍 No IQF rejections, checking Brass_QC_Rejected_TrayScan: Found {brass_rejected_trays.count()} records")
            
            # Aggregate by tray_id
            tray_aggregation = {}
            
            # ✅ ENHANCED DEBUG: Print each record BEFORE aggregation
            for idx, rec in enumerate(brass_rejected_trays, 1):
                qty_val = int(rec.rejected_tray_quantity) if rec.rejected_tray_quantity else 0
                reason_text = rec.rejection_reason.rejection_reason if rec.rejection_reason else 'N/A'
                print(f"      Record {idx}: tray_id='{rec.rejected_tray_id}', qty={qty_val}, reason='{reason_text}', top_tray={rec.top_tray}")
            
            for tray_obj in brass_rejected_trays:
                tray_key = tray_obj.rejected_tray_id or 'NO_TRAY'  # Use 'NO_TRAY' as key if tray_id is None
                
                # Convert qty to int
                qty_int = int(tray_obj.rejected_tray_quantity) if tray_obj.rejected_tray_quantity else 0
                
                # ✅ FIXED: Also check BrassTrayId and IQFTrayId for top_tray flag
                brass_tray_id_obj = BrassTrayId.objects.filter(lot_id=lot_id, tray_id=tray_obj.rejected_tray_id).first()
                iqf_tray_id_obj = IQFTrayId.objects.filter(lot_id=lot_id, tray_id=tray_obj.rejected_tray_id).first()
                
                # Use top_tray from any of these sources (priority: Brass_QC_Rejected_TrayScan > BrassTrayId > IQFTrayId)
                is_top_tray = (tray_obj.top_tray or 
                              (brass_tray_id_obj and brass_tray_id_obj.top_tray) or 
                              (iqf_tray_id_obj and iqf_tray_id_obj.top_tray))
                
                # Aggregate by tray_id
                if tray_key not in tray_aggregation:
                    tray_aggregation[tray_key] = {
                        'tray_id': tray_obj.rejected_tray_id,
                        'qty': 0,
                        'reasons': [],
                        'top_tray': is_top_tray,
                        'user': tray_obj.user.username if tray_obj.user else None,
                    }
                
                # Sum quantities
                tray_aggregation[tray_key]['qty'] += qty_int
                
                # Collect unique reasons
                reason_text = tray_obj.rejection_reason.rejection_reason if tray_obj.rejection_reason else 'N/A'
                reason_id = tray_obj.rejection_reason.rejection_reason_id if tray_obj.rejection_reason else ''
                if reason_text not in [r['reason'] for r in tray_aggregation[tray_key]['reasons']]:
                    tray_aggregation[tray_key]['reasons'].append({
                        'reason': reason_text,
                        'reason_id': reason_id
                    })
                
                # Use highest top_tray value
                tray_aggregation[tray_key]['top_tray'] = tray_aggregation[tray_key]['top_tray'] or is_top_tray
            
            # ✅ ENHANCED DEBUG: Print aggregation results
            print(f"   ℹ️ After aggregation: {len(tray_aggregation)} unique tray keys")
            for tray_key, info in tray_aggregation.items():
                print(f"      Aggregated key '{tray_key}': qty={info['qty']}, reasons={[r['reason'] for r in info['reasons']]}, top_tray={info['top_tray']}")
            
            # Convert aggregation dict to list
            for tray_key, tray_info in tray_aggregation.items():
                reason_text = ' | '.join([r['reason'] for r in tray_info['reasons']])
                reason_id = tray_info['reasons'][0]['reason_id'] if tray_info['reasons'] else ''
                final_qty = _resolve_qty_from_lot_sources(tray_info['tray_id'], tray_info['qty'])
                
                rejected_tray_ids.append({
                    'tray_id': tray_info['tray_id'],
                    'qty': str(final_qty),
                    'reason': reason_text,
                    'reason_id': reason_id,
                    'top_tray': tray_info['top_tray'],
                    'user': tray_info['user'],
                })

        # Print rejected trays for debugging
        print(f"   📋 Total rejected trays to show in popup: {len(rejected_tray_ids)}")
        for idx, rej_tray in enumerate(rejected_tray_ids, 1):
            top_marker = "🔝" if rej_tray['top_tray'] else "  "
            print(f"      {idx}. {top_marker} Tray ID: {rej_tray['tray_id']}, Qty: {rej_tray['qty']}, Reason: {rej_tray['reason']}")

        # ✅ FIXED: Get rejected tray IDs with top_tray information from BOTH IQF and Brass QC sources
        
        # 🔹 SECONDARY: Get any additional IQF-specific rejections with tray IDs
        iqf_rejected_trays = IQF_Rejected_TrayScan.objects.filter(
            lot_id=lot_id,
            tray_id__isnull=False
        ).exclude(tray_id='').select_related('rejection_reason').order_by('id')
        
        print(f"\n{'='*80}")
        print(f"📬 [POPUP DATA SUMMARY] What will be shown in verification popup:")
        print(f"{'='*80}")
        print(f"   ✅ Rejection Summary Rows: {len(rejection_rows)}")
        for row in rejection_rows:
            print(f"      - Qty: {row['qty']}, Reason: {row['reason']}")
        print(f"   ✅ Accepted Trays: {len(accepted_trays)}")
        for acc in accepted_trays:
            print(f"      - Tray: {acc['tray_id']}, Qty: {acc['tray_qty']}")
        print(f"   ✅ Rejected Tray IDs: {len(rejected_tray_ids)}")
        for rej in rejected_tray_ids:
            print(f"      - Tray: {rej['tray_id']}, Qty: {rej['qty']}, Reason: {rej['reason']}, TopTray: {rej['top_tray']}")
        print(f"{'='*80}\n")

        print(f"[iqf_get_rejected_tray_scan_data] lot_id={lot_id}")
        print(f"  rejection_rows: {rejection_rows}")
        print(f"  accepted_trays: {accepted_trays}")
        print(f"  rejected_tray_ids: {rejected_tray_ids} (with top_tray from Brass QC/IQF)")  # ✅ Enhanced logging

        # ✅ NEW: Include delink candidates information
        try:
            delink_response = iqf_get_delink_candidates(request._request)
            if delink_response.status_code == 200:
                delink_data = delink_response.data
                delink_candidates = delink_data.get('delink_candidates', [])
                new_tray_used = delink_data.get('new_tray_used', False)
            else:
                delink_candidates = []
                new_tray_used = False
        except Exception as e:
            print(f"  Warning: Could not fetch delink candidates: {str(e)}")
            delink_candidates = []
            new_tray_used = False

        return Response({
            'success': True,
            'rejection_rows': rejection_rows,
            'accepted_trays': accepted_trays,
            'rejected_tray_ids': rejected_tray_ids,  # ✅ NEW: Include rejected tray IDs with top_tray flag
            'delink_candidates': delink_candidates,  # ✅ NEW: Include delink candidates
            'new_tray_used': new_tray_used  # ✅ NEW: Include new tray usage flag
        })
    except Exception as e:
        print(f"[iqf_get_rejected_tray_scan_data] ERROR: {str(e)}")
        traceback.print_exc()
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
                'batch_total_quantity': batch.total_batch_quantity,
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

            # Completed Table Rejected Qty = IQF Physical Qty - IQF Accepted Qty
            try:
                total_qty_for_reject = int(data.get('iqf_physical_qty') or 0)
                accepted_qty = int(data.get('iqf_accepted_qty') or 0)
                data['iqf_rejection_qty'] = max(0, total_qty_for_reject - accepted_qty)
            except Exception:
                pass

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

        # IQF-eligible tray scope for reuse/rearrange:
        # 1) IQF rejected scan tray IDs
        # 2) fallback upstream rejected scan IDs (Brass QC / Brass Audit)
        # 3) last fallback IQF/DP verified tray IDs
        stock_obj_scope = TotalStockModel.objects.filter(lot_id=lot_id).first()
        use_audit_scope = bool(getattr(stock_obj_scope, 'send_brass_audit_to_iqf', False))

        eligible_tray_ids = set(
            IQF_Rejected_TrayScan.objects.filter(lot_id=lot_id).exclude(tray_id='').values_list('tray_id', flat=True)
        )
        if not eligible_tray_ids:
            if use_audit_scope:
                eligible_tray_ids = set(
                    Brass_Audit_Rejected_TrayScan.objects.filter(lot_id=lot_id).exclude(rejected_tray_id='').values_list('rejected_tray_id', flat=True)
                )
            else:
                eligible_tray_ids = set(
                    Brass_QC_Rejected_TrayScan.objects.filter(lot_id=lot_id).exclude(rejected_tray_id='').values_list('rejected_tray_id', flat=True)
                )
        if not eligible_tray_ids:
            eligible_tray_ids = set(
                IQFTrayId.objects.filter(lot_id=lot_id, IP_tray_verified=True).values_list('tray_id', flat=True)
            )
            eligible_tray_ids |= set(
                DPTrayId_History.objects.filter(lot_id=lot_id, IP_tray_verified=True).values_list('tray_id', flat=True)
            )
        eligible_tray_ids = {t for t in eligible_tray_ids if t}

        # If tray belongs to this lot lineage but is outside IQF scope, treat as Different Lot for IQF reuse.
        belongs_this_lot_any = bool(
            TrayId.objects.filter(tray_id=tray_id, lot_id=lot_id).exists() or
            IQFTrayId.objects.filter(tray_id=tray_id, lot_id=lot_id).exists() or
            BrassTrayId.objects.filter(tray_id=tray_id, lot_id=lot_id).exists() or
            BrassAuditTrayId.objects.filter(tray_id=tray_id, lot_id=lot_id).exists() or
            IPTrayId.objects.filter(tray_id=tray_id, lot_id=lot_id).exists() or
            DPTrayId_History.objects.filter(tray_id=tray_id, lot_id=lot_id).exists()
        )
        if belongs_this_lot_any and eligible_tray_ids and tray_id not in eligible_tray_ids:
            print(f"[IQF Reject Validation] Tray {tray_id} belongs to lot lineage but not IQF-eligible scope")
            return JsonResponse({
                'exists': True,
                'valid_for_rejection': False,
                'is_valid': False,
                'status_message': 'Different Lot',
                'error': 'Tray not available in IQF reuse scope'
            })
        
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
    Calculate delink candidates: All trays that are NOT in rejection AND NOT in acceptance.
    
    Logic:
    1. Get ALL trays (from IQFTrayId PLUS from IQF_Rejected_TrayScan if they moved there)
    2. Exclude trays in IQF_Rejected_TrayScan (currently in rejection verification)
    3. Exclude trays in IQF_Accepted_TrayID_Store (currently in acceptance)
    4. Remaining trays with qty > 0 are delink candidates (unused trays)
    """
    lot_id = request.GET.get('lot_id')
    if not lot_id:
        return Response({'success': False, 'error': 'Missing lot_id'}, status=400)
    
    try:
        # 1. Get ALL trays from multiple sources (they might be in different tables)
        all_trays_dict = {}
        
        # From TrayId (modelmasterapp - authoritative master tray table, checked FIRST for correct quantities)
        for tray in TrayId.objects.filter(lot_id=lot_id).values('tray_id', 'tray_quantity'):
            if tray['tray_id']:
                all_trays_dict[tray['tray_id']] = tray['tray_quantity'] or 0
        
        # From IQFTrayId (derived/processed trays - only add if not already found in TrayId)
        for tray in IQFTrayId.objects.filter(lot_id=lot_id).values('tray_id', 'tray_quantity'):
            if tray['tray_id'] and tray['tray_id'] not in all_trays_dict:
                all_trays_dict[tray['tray_id']] = tray['tray_quantity'] or 0
        
        # From BrassTrayId (if from Brass QC)
        for tray in BrassTrayId.objects.filter(lot_id=lot_id).values('tray_id', 'tray_quantity'):
            if tray['tray_id'] and tray['tray_id'] not in all_trays_dict:
                all_trays_dict[tray['tray_id']] = tray['tray_quantity']
        
        # From BrassAuditTrayId (if from Brass Audit)
        for tray in BrassAuditTrayId.objects.filter(lot_id=lot_id).values('tray_id', 'tray_quantity'):
            if tray['tray_id'] and tray['tray_id'] not in all_trays_dict:
                all_trays_dict[tray['tray_id']] = tray['tray_quantity']
        
        # Also add trays from IQF_Rejected_TrayScan if they exist (they might have been moved)
        # Note: rejected_tray_quantity is a CharField, so sum manually in Python
        for rejection in IQF_Rejected_TrayScan.objects.filter(lot_id=lot_id).values('tray_id', 'rejected_tray_quantity').distinct():
            tid = rejection['tray_id']
            if tid and tid not in all_trays_dict:
                try:
                    qty = int(rejection['rejected_tray_quantity']) if rejection['rejected_tray_quantity'] else 0
                except (ValueError, TypeError):
                    qty = 0
                all_trays_dict[tid] = qty
        
        print(f"[DELINK CANDIDATES]")
        print(f"  All trays found from all sources: {all_trays_dict}")
        
        # 2. Get trays currently in rejection verification (BOTH finalized AND draft)
        rejected_tray_ids = set()
        
        # From IQF finalized rejection
        iqf_rejected = IQF_Rejected_TrayScan.objects.filter(lot_id=lot_id).values_list('tray_id', flat=True).distinct()
        rejected_tray_ids.update([t for t in iqf_rejected if t])
        
        # From IQF draft rejection (pending in modal - not yet submitted)
        try:
            import json
            draft_rejection = IQF_Draft_Store.objects.filter(
                lot_id=lot_id,
                draft_type__in=['tray_rejection_draft', 'rejection_verification']
            ).order_by('-created_at').first()
            
            if draft_rejection and draft_rejection.draft_data:
                draft_data = json.loads(draft_rejection.draft_data) if isinstance(draft_rejection.draft_data, str) else draft_rejection.draft_data
                
                # Extract tray IDs from draft rejection
                if 'rejection_verification' in draft_data:
                    for tray_entry in draft_data.get('rejection_verification', []):
                        tray_id = tray_entry.get('tray_id')
                        if tray_id:
                            rejected_tray_ids.add(tray_id)
                            print(f"  📋 Found draft rejection for: {tray_id}")
        except Exception as e:
            print(f"  ⚠️ Could not parse draft rejection data: {str(e)}")
        
        # From Brass QC finalized rejection
        brass_rejected = Brass_QC_Rejected_TrayScan.objects.filter(lot_id=lot_id).values_list('rejected_tray_id', flat=True).distinct()
        rejected_tray_ids.update([t for t in brass_rejected if t])
        
        rejected_tray_ids = list(rejected_tray_ids)
        
        print(f"  Trays in rejection (finalized + draft): {rejected_tray_ids}")
        
        # 2b. Compute actual rejected qty per tray (to handle partially-rejected trays)
        #     A tray rejected for only 9 out of 12 still has 3 remaining that need delink.
        rejected_qty_per_tray = {}
        for rec in IQF_Rejected_TrayScan.objects.filter(lot_id=lot_id).values('tray_id', 'rejected_tray_quantity'):
            tid = rec['tray_id']
            if tid:
                try:
                    qty = int(rec['rejected_tray_quantity']) if rec['rejected_tray_quantity'] else 0
                except (ValueError, TypeError):
                    qty = 0
                rejected_qty_per_tray[tid] = rejected_qty_per_tray.get(tid, 0) + qty
        
        # 3. Get trays currently in acceptance (from DB + from frontend form not yet saved)
        accepted_tray_ids = list(
            IQF_Accepted_TrayID_Store.objects.filter(lot_id=lot_id).values_list('tray_id', flat=True).distinct()
        )
        # Also include tray IDs the user has already scanned in the acceptance form (pending save)
        pending_accepted = request.GET.get('accepted_tray_ids', '')
        if pending_accepted:
            for tid in pending_accepted.split(','):
                tid = tid.strip()
                if tid and tid not in accepted_tray_ids:
                    accepted_tray_ids.append(tid)
        
        # 🔴 ADDITIONAL CHECK: Also check acceptance draft data
        try:
            import json
            draft_acceptance = IQF_Draft_Store.objects.filter(
                lot_id=lot_id,
                draft_type__in=['accepted_tray', 'acceptance_verification']
            ).order_by('-created_at').first()
            
            if draft_acceptance and draft_acceptance.draft_data:
                draft_data = json.loads(draft_acceptance.draft_data) if isinstance(draft_acceptance.draft_data, str) else draft_acceptance.draft_data
                
                # Extract tray IDs from draft acceptance
                if 'accepted_trays' in draft_data:
                    for tray_entry in draft_data.get('accepted_trays', []):
                        tray_id = tray_entry.get('tray_id') if isinstance(tray_entry, dict) else tray_entry
                        if tray_id and tray_id not in accepted_tray_ids:
                            accepted_tray_ids.append(tray_id)
                            print(f"  📋 Found draft acceptance for: {tray_id}")
                # Also check top_tray_id
                if draft_data.get('top_tray_id'):
                    top_id = draft_data.get('top_tray_id')
                    if top_id not in accepted_tray_ids:
                        accepted_tray_ids.append(top_id)
                        print(f"  📋 Found draft acceptance (top tray): {top_id}")
        except Exception as e:
            print(f"  ⚠️ Could not parse draft acceptance data: {str(e)}")
        
        print(f"  Trays in acceptance: {accepted_tray_ids}")
        
        # 4. Find delink candidates: trays whose REMAINING qty (after rejection) > 0 AND not in acceptance
        #    Partially-rejected trays (e.g. 9 rejected from a 12-qty tray) have 3 remaining
        #    and must appear as delink candidates if the user used a new tray for acceptance.
        delink_candidates = []
        for tray_id, tray_qty in all_trays_dict.items():
            rejected_for_tray = rejected_qty_per_tray.get(tray_id, 0)
            remaining_qty = tray_qty - rejected_for_tray
            if remaining_qty > 0 and tray_id not in accepted_tray_ids:
                delink_candidates.append({
                    'tray_id': tray_id
                })
                print(f"  ✅ Delink candidate: {tray_id} (total {tray_qty}, rejected {rejected_for_tray}, remaining {remaining_qty})")
            else:
                reason = []
                if remaining_qty <= 0:
                    reason.append(f"fully consumed in rejection ({rejected_for_tray}/{tray_qty})")
                if tray_id in accepted_tray_ids:
                    reason.append("in acceptance")
                if tray_qty <= 0:
                    reason.append("qty=0")
                print(f"  ❌ Not delink: {tray_id} (qty {tray_qty}) - {', '.join(reason)}")
        
        print(f"  Total delink candidates (raw): {len(delink_candidates)}")

        # ✅ DELINK COUNT FIX: Delink count = number of NEW acceptance trays (not from original lot).
        # Each new acceptance tray displaces exactly 1 original lot tray (the delinked tray).
        # Existing lot trays reused for acceptance do NOT require a delink.
        new_acceptance_count = sum(1 for tid in accepted_tray_ids if tid not in all_trays_dict)

        # Sort candidates by qty descending (largest first) to match STEP-2b delink priority
        delink_candidates = sorted(
            delink_candidates,
            key=lambda x: all_trays_dict.get(x['tray_id'], 0),
            reverse=True
        )
        # Limit to only the required number of delinks
        delink_candidates = delink_candidates[:new_acceptance_count]

        print(f"  New acceptance trays (non-lot): {new_acceptance_count}")
        print(f"  Total delink candidates (after limit): {len(delink_candidates)}")
        
        return Response({
            'success': True,
            'delink_candidates': delink_candidates,
            'new_tray_used': new_acceptance_count > 0,
            'debug': {
                'all_trays': all_trays_dict,
                'rejected_tray_ids': rejected_tray_ids,
                'accepted_tray_ids': accepted_tray_ids,
                'delink_candidates': delink_candidates,
                'new_acceptance_count': new_acceptance_count
            }
        })
        
    except Exception as e:
        print(f"[DELINK ERROR] {str(e)}")
        import traceback
        traceback.print_exc()
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
        stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
        if not stock:
            return Response({'success': False, 'error': 'Stock not found'}, status=400)
        use_audit = bool(getattr(stock, 'send_brass_audit_to_iqf', False))

        # 1) Build IQF-eligible tray IDs only (no direct Brass/IP union)
        rejected_scan_tray_ids = set(
            IQF_Rejected_TrayScan.objects.filter(lot_id=lot_id).exclude(tray_id='').values_list('tray_id', flat=True)
        )
        if rejected_scan_tray_ids:
            eligible_tray_ids = rejected_scan_tray_ids
        else:
            if use_audit:
                upstream_ids = set(
                    Brass_Audit_Rejected_TrayScan.objects.filter(lot_id=lot_id).exclude(rejected_tray_id='').values_list('rejected_tray_id', flat=True)
                )
            else:
                upstream_ids = set(
                    Brass_QC_Rejected_TrayScan.objects.filter(lot_id=lot_id).exclude(rejected_tray_id='').values_list('rejected_tray_id', flat=True)
                )
            if upstream_ids:
                eligible_tray_ids = upstream_ids
            else:
                eligible_tray_ids = set(
                    IQFTrayId.objects.filter(lot_id=lot_id, IP_tray_verified=True).values_list('tray_id', flat=True)
                )
                eligible_tray_ids |= set(
                    DPTrayId_History.objects.filter(lot_id=lot_id, IP_tray_verified=True).values_list('tray_id', flat=True)
                )

        eligible_tray_ids = {t for t in eligible_tray_ids if t}
        eligible_tray_ids = set(
            TrayId.objects.filter(
                tray_id__in=eligible_tray_ids,
                new_tray=False,
                IP_tray_verified=True
            ).values_list('tray_id', flat=True)
        )
        print(f"[DEBUG] IQF-eligible tray IDs: {eligible_tray_ids}")

        def _best_qty_for_tray(tid):
            qtys = []
            for model in [IQFTrayId, DPTrayId_History, BrassTrayId, BrassAuditTrayId, IPTrayId]:
                try:
                    q = model.objects.filter(lot_id=lot_id, tray_id=tid).values_list('tray_quantity', flat=True).first()
                    if q is not None:
                        qtys.append(int(q or 0))
                except Exception:
                    pass
            try:
                master_q = TrayId.objects.filter(tray_id=tid).values_list('tray_quantity', flat=True).first()
                if master_q is not None:
                    qtys.append(int(master_q or 0))
            except Exception:
                pass
            return max(qtys) if qtys else 0

        if eligible_tray_ids:
            found_iqf_trays = list(IQFTrayId.objects.filter(tray_id__in=eligible_tray_ids).distinct().order_by('id'))
            found_ids = {t.tray_id for t in found_iqf_trays}
            missing_ids = eligible_tray_ids - found_ids
            if missing_ids:
                print(f"[DEBUG] Missing IQFTrayId records for: {missing_ids}. Reconstructing...")
                for mid in missing_ids:
                    temp_tray = IQFTrayId(
                        tray_id=mid,
                        lot_id=lot_id,
                        tray_quantity=_best_qty_for_tray(mid),
                        IP_tray_verified=True,
                        rejected_tray=False,
                        delink_tray=False
                    )
                    found_iqf_trays.append(temp_tray)
            iqf_trays = found_iqf_trays
        else:
            iqf_trays = list(IQFTrayId.objects.filter(lot_id=lot_id, IP_tray_verified=True).order_by('id'))
        
        print(f"[DEBUG] Total iqf_trays found (recovered + reconstructed): {len(iqf_trays)}")
        print(f"[DEBUG] Trays: {[t.tray_id for t in iqf_trays]}")

        
        # Get total rejection quantity from Brass_QC_Rejection_ReasonStore table
        # Sum total_rejection_quantity from Brass_QC_Rejection_ReasonStore for this lot
        brass_rejection_records = Brass_QC_Rejection_ReasonStore.objects.filter(lot_id=lot_id)
        brass_total_rejected_qty = sum([record.total_rejection_quantity or 0 for record in brass_rejection_records])
        
        tray_capacity = stock.batch_id.tray_capacity if stock.batch_id and hasattr(stock.batch_id, 'tray_capacity') else 12
        
        # Calculate remaining quantities using IQF rejection total if available
        initial_iqf_trays = {}
        iqf_reason_store = IQF_Rejection_ReasonStore.objects.filter(lot_id=lot_id).order_by('-id').first()
        if iqf_reason_store and eligible_tray_ids:
            remaining_reject_qty = int(iqf_reason_store.total_rejection_quantity or 0)
            base_qty_map = {
                tid: (TrayId.objects.filter(tray_id=tid).values_list('tray_quantity', flat=True).first() or 0)
                for tid in eligible_tray_ids
            }
            sorted_trays = sorted(base_qty_map.items(), key=lambda x: (int(x[1] or 0), x[0]))
            for tray_id, base_qty in sorted_trays:
                base_qty = int(base_qty or 0)
                rejected_qty = min(remaining_reject_qty, base_qty)
                initial_iqf_trays[tray_id] = max(0, base_qty - rejected_qty)
                remaining_reject_qty -= rejected_qty
        else:
            # Use actual tray quantities from iqf_trays (not a distribution of rejection qty)
            for tray in iqf_trays:
                initial_iqf_trays[tray.tray_id] = tray.tray_quantity

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
        
        # Subtract total quantity from remaining trays, small/top trays first
        working_trays = sorted(working_trays, key=lambda x: (x[1], x[0]))
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

        # Decide candidates from all fully consumed trays
        fully_consumed = [t for t in post_subtract if t['remaining_qty'] == 0 and t['used_qty'] > 0]
        partially_consumed_or_left = [t for t in post_subtract if t['remaining_qty'] > 0 or t['used_qty'] == 0]

        # Reset any previous flags and select proper delink / rejection candidates
        delink_candidates = []
        for tray_info in fully_consumed:
            tray_info.pop('is_delink_candidate', None)
            tray_info.pop('is_rejection_candidate', None)
            tray_info['is_delink_candidate'] = True
            delink_candidates.append({
                'tray_id': tray_info['tray_id'],
                'original_qty': tray_info['initial_qty'],
                'physical_qty': tray_info['physical_qty'],
                'subtracted_qty': tray_info['used_qty']
            })
            tray_info['is_rejection_candidate'] = True

        # Merge back into ordered list
        post_subtract = fully_consumed + partially_consumed_or_left

        # ✅ Adjust delink candidates based on unused eligible trays
        unused_existing_tray_ids = {tid for tid, qty in working_trays if qty > 0}
        delink_candidates = []
        for tray_info in post_subtract:
            is_delink = tray_info['tray_id'] in unused_existing_tray_ids
            tray_info['is_delink_candidate'] = is_delink
            if is_delink:
                delink_candidates.append({
                    'tray_id': tray_info['tray_id'],
                    'original_qty': tray_info['initial_qty'],
                    'physical_qty': tray_info['physical_qty'],
                    'subtracted_qty': tray_info['used_qty']
                })

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
        print(f"[DEBUG] Delink candidates selected: {delink_candidates}")
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
    Allow delink of trays that are NOT in rejection verification AND NOT in acceptance.
    Unused trays can be delinked from the lot.
    """
    tray_id = request.GET.get('tray_id', '').strip()
    lot_id = request.GET.get('lot_id', '').strip()
    
    if not tray_id or not lot_id:
        return Response({'success': False, 'error': 'Missing tray_id or lot_id'}, status=400)
    
    try:
        # Check if tray exists in this lot
        tray_exists = IQFTrayId.objects.filter(
            lot_id=lot_id,
            tray_id=tray_id
        ).exists()
        
        if not tray_exists:
            return Response({
                'success': False,
                'is_valid': False,
                'error': f'Tray {tray_id} does not exist in this lot'
            }, status=400)

        # Check if tray is already delinked (delink_tray=True)
        already_delinked = IQFTrayId.objects.filter(
            tray_id=tray_id,
            delink_tray=True
        ).exists()

        if already_delinked:
            return Response({
                'success': False,
                'is_valid': False,
                'error': f'Tray {tray_id} is already delinked'
            })

        # NEW LOGIC: Check if tray is used in rejection verification (IQF_Rejected_TrayScan)
        used_in_rejection = IQF_Rejected_TrayScan.objects.filter(
            lot_id=lot_id,
            tray_id=tray_id
        ).exclude(tray_id='').exists()

        if used_in_rejection:
            return Response({
                'success': False,
                'is_valid': False,
                'error': f'Tray {tray_id} is used in rejection verification and cannot be delinked'
            })

        # NEW LOGIC: Check if tray is used in acceptance (IQF_Accepted_TrayID_Store)
        used_in_acceptance = IQF_Accepted_TrayID_Store.objects.filter(
            lot_id=lot_id,
            tray_id=tray_id
        ).exists()

        if used_in_acceptance:
            return Response({
                'success': False,
                'is_valid': False,
                'error': f'Tray {tray_id} is used in acceptance and cannot be delinked'
            })

        # VALID: Tray is not used in rejection or acceptance - can be delinked
        return Response({
            'success': True,
            'is_valid': True,
            'tray_id': tray_id,
            'lot_id': lot_id,
            'message': f'Tray {tray_id} is valid for delink (unused tray)'
        })
        
    except Exception as e:
        print(f"❌ [DELINK-VALIDATE] Error: {str(e)}")
        import traceback
        traceback.print_exc()
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



def remove_tray_from_rejection(tray_id, lot_id):
    """
    🔴 CRITICAL: Remove a tray from rejection when it's accepted for reuse.
    Ensures mutual exclusivity between rejection and acceptance lists.
    """
    try:
        # Remove from IQF_Rejected_TrayScan
        IQF_Rejected_TrayScan.objects.filter(
            lot_id=lot_id,
            tray_id=tray_id
        ).delete()
        
        # Also remove from Brass rejection if applicable
        Brass_QC_Rejected_TrayScan.objects.filter(
            lot_id=lot_id,
            rejected_tray_id=tray_id
        ).delete()
        
        # Update rejection reason store if this tray was the only one with this qty
        rejection_store = IQF_Rejection_ReasonStore.objects.filter(lot_id=lot_id).first()
        if rejection_store:
            remaining_rejected_trays = IQF_Rejected_TrayScan.objects.filter(lot_id=lot_id).count()
            if remaining_rejected_trays == 0:
                # If no more rejected trays, update the rejection store to reflect removal
                rejection_store.total_rejection_quantity = 0
                rejection_store.save(update_fields=['total_rejection_quantity'])
        
        print(f"✅ [CLEANUP] Removed tray {tray_id} from rejection list for lot {lot_id}")
        return True
    except Exception as e:
        print(f"❌ [CLEANUP] Error removing tray {tray_id} from rejection: {str(e)}")
        return False


def remove_tray_from_acceptance(tray_id, lot_id):
    """
    🔴 CRITICAL: Remove a tray from acceptance when it's being rejected.
    Ensures mutual exclusivity between rejection and acceptance lists.
    """
    try:
        # Remove from IQF_Accepted_TrayID_Store
        IQF_Accepted_TrayID_Store.objects.filter(
            lot_id=lot_id,
            tray_id=tray_id
        ).delete()
        
        print(f"✅ [CLEANUP] Removed tray {tray_id} from acceptance list for lot {lot_id}")
        return True
    except Exception as e:
        print(f"❌ [CLEANUP] Error removing tray {tray_id} from acceptance: {str(e)}")
        return False


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def iqf_process_all_tray_data(request):
    """Process all tray data: delink, verification, and rejection top trays"""
    try:
        data = request.data
        lot_id = data.get('lot_id')
        delink_trays = data.get('delink_trays', [])
        verifications = data.get('verifications', [])
        accepted_trays = data.get('accepted_trays', [])  # ✅ NEW: Get accepted trays
        rejection_top_trays = data.get('rejection_top_trays', [])
        is_draft = data.get('is_draft', False)
        
        if not lot_id:
            return Response({
                'success': False, 
                'error': 'Missing lot_id'
            }, status=400)
        
        # 🔴 CRITICAL DEFENSIVE CHECK: Clean up any trays that appear in BOTH acceptance AND rejection
        # This handles cases where frontend didn't properly coordinate between acceptance/rejection forms
        saved_accepted_tray_ids = set(
            IQF_Accepted_TrayID_Store.objects.filter(lot_id=lot_id).values_list('tray_id', flat=True)
        )
        accepted_tray_ids_from_input = {t.get('tray_id') for t in accepted_trays if t.get('tray_id')}
        all_accepted_tray_ids = saved_accepted_tray_ids | accepted_tray_ids_from_input
        
        # Remove any of these trays from rejection records
        if all_accepted_tray_ids:
            for tray_id in all_accepted_tray_ids:
                remove_tray_from_rejection(tray_id, lot_id)
                print(f"🧹 [DEFENSIVE] Cleaned up {tray_id} from rejection (it's in acceptance)")
        
        results = {
            'processed_delinks': 0,
            'processed_verifications': 0,
            'processed_rejections': 0,
            'processed_accepted': 0,
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
                    # ✅ If verified, mark as rejected tray
                    if verified:
                        # 🔴 CRITICAL: Remove from acceptance if it was accepted
                        remove_tray_from_acceptance(tray_id, lot_id)
                        
                        tray_obj.rejected_tray = True
                        tray_obj.iqf_reject_verify = True
                        print(f"✅ Marked tray {tray_id} as rejected (verified) - removed from acceptance if any")
                    else:
                        # If not verified, it should be available for acceptance
                        tray_obj.iqf_reject_verify = False
                        print(f"✅ Tray {tray_id} not verified - available for acceptance")
                    
                    # ✅ SAVE THE REJECTION QUANTITY if provided
                    if qty is not None:
                        try:
                            existing_qty = int(tray_obj.tray_quantity or 0)
                            incoming_qty = int(qty)
                            tray_obj.tray_quantity = max(existing_qty, incoming_qty)
                            print(f"✅ Updated tray {tray_id} quantity safely: existing={existing_qty}, incoming={incoming_qty}, saved={tray_obj.tray_quantity}")
                        except (ValueError, TypeError):
                            print(f"⚠️ Invalid quantity {qty} for tray {tray_id}, keeping original")
                    
                    # ✅ SAVE BOTH FIELDS
                    tray_obj.save(update_fields=['iqf_reject_verify', 'tray_quantity', 'rejected_tray'])
                    
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
        
        # ✅ CRITICAL: Remove accepted trays from rejection list (mutual exclusivity)
        if accepted_trays:
            for accepted_tray in accepted_trays:
                tray_id = accepted_tray['tray_id']
                # Remove from rejection to avoid duplicate processing
                remove_tray_from_rejection(tray_id, lot_id)
                # Store acceptance record
                IQF_Accepted_TrayID_Store.objects.update_or_create(
                    lot_id=lot_id,
                    tray_id=tray_id,
                    defaults={
                        'tray_qty': accepted_tray['qty'],
                        'user': request.user,
                        'accepted_comment': accepted_tray.get('comment', '')
                    }
                )
                print(f"✅ [ACCEPTED] Stored acceptance for {tray_id} (qty={accepted_tray['qty']}) and removed from rejection")
            
            results['processed_accepted'] = len(accepted_trays)
            print(f"✅ Processed {len(accepted_trays)} explicitly accepted trays with rejection cleanup")

        # ✅ Summary response
        success = len(results['errors']) == 0
        message_parts = []
        
        if results.get('processed_delinks', 0) > 0:
            message_parts.append(f"{results['processed_delinks']} trays delinked")
        if results.get('processed_verifications', 0) > 0:
            message_parts.append(f"{results['processed_verifications']} verifications saved")
        if results.get('processed_rejections', 0) > 0:
            message_parts.append(f"{results['processed_rejections']} rejection trays processed")
        if results.get('processed_accepted', 0) > 0:
            message_parts.append(f"{results['processed_accepted']} trays accepted")
        
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
            use_audit = bool(getattr(stock, 'send_brass_audit_to_iqf', False)) if stock else False
            eligible_ids = set(
                IQF_Rejected_TrayScan.objects.filter(lot_id=lot_id).exclude(tray_id='').values_list('tray_id', flat=True)
            )
            if not eligible_ids:
                if use_audit:
                    eligible_ids = set(
                        Brass_Audit_Rejected_TrayScan.objects.filter(lot_id=lot_id).exclude(rejected_tray_id='').values_list('rejected_tray_id', flat=True)
                    )
                else:
                    eligible_ids = set(
                        Brass_QC_Rejected_TrayScan.objects.filter(lot_id=lot_id).exclude(rejected_tray_id='').values_list('rejected_tray_id', flat=True)
                    )
            eligible_ids = {t for t in eligible_ids if t}

            if eligible_ids:
                total_tray_count = len(eligible_ids)
            else:
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

            # Extract accepted trays data
            accepted_trays = data.get('accepted_trays', [])
            if accepted_trays is None:
                accepted_trays = []
            if not isinstance(accepted_trays, list):
                accepted_trays = []

            # Merge with existing accepted_tray draft to avoid losing scanned trays
            existing_draft = IQF_Draft_Store.objects.filter(
                lot_id=lot_id,
                draft_type='accepted_tray'
            ).first()
            existing_data = existing_draft.draft_data if (existing_draft and isinstance(existing_draft.draft_data, dict)) else {}

            if not accepted_trays and isinstance(existing_data.get('accepted_trays'), list):
                accepted_trays = existing_data.get('accepted_trays', [])
            if (not delink_trays) and isinstance(existing_data.get('delink_trays'), list):
                delink_trays = existing_data.get('delink_trays', [])
            if not top_tray_id and existing_data.get('top_tray_id'):
                top_tray_id = existing_data.get('top_tray_id', '')
            if top_tray_qty in (None, '') and existing_data.get('top_tray_qty') is not None:
                top_tray_qty = existing_data.get('top_tray_qty')
            incoming_remarks = data.get('acceptance_remarks', '').strip()
            if not incoming_remarks and existing_data.get('acceptance_remarks'):
                incoming_remarks = existing_data.get('acceptance_remarks', '')
             
            # Create draft data structure
            draft_data = {
                'is_draft': is_draft,
                'is_auto_save': is_auto_save,
                'top_tray_id': top_tray_id,
                'top_tray_qty': top_tray_qty,
                'delink_trays': delink_trays,
                'accepted_trays': accepted_trays,
                'acceptance_remarks': incoming_remarks
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


# ✅ FRESH AUTO-ALLOCATION LOGIC FOR IQF REJECTION/DELINK SYSTEM

def auto_allocate_iqf_rejection(lot_id, total_rejection_qty, available_trays):
    """
    Auto-allocate rejection quantity across available trays with consolidation and delink calculation.
    
    Args:
        lot_id: The lot ID
        total_rejection_qty: Total quantity to reject
        available_trays: List of available tray objects with tray_id, tray_quantity, tray_capacity
        
    Returns:
        {
            'rejection_distribution': [{'tray_id': str, 'rejected_qty': int, 'remaining_qty': int}],
            'delink_trays': [tray_id_list],
            'acceptance_trays': [{'tray_id': str, 'accepted_qty': int}],
            'new_trays_needed': [{'new_tray_id': str, 'qty': int}]
        }
    """
    try:
        print(f"🎯 [AUTO-ALLOCATE] Starting auto-allocation for lot {lot_id}, rejection qty: {total_rejection_qty}")
        
        # Calculate total available quantity
        total_available = sum(int(tray.get('tray_quantity', 0)) for tray in available_trays)
        tray_capacity = max((int(tray.get('tray_capacity', 12)) for tray in available_trays), default=12)
        
        # Calculate total accepted
        total_accepted = total_available - total_rejection_qty
        
        # Sort trays by quantity ascending (smallest first) for allocation
        sorted_trays = sorted(available_trays, key=lambda x: x.get('tray_quantity', 0))
        
        rejection_distribution = []
        acceptance_trays = []  # Initialize before loop (fixes UnboundLocalError)
        remaining_rejection_qty = total_rejection_qty
        
        # Allocate rejection starting from smallest trays
        for tray in sorted_trays:
            if remaining_rejection_qty <= 0:
                break
            
            tray_id = tray.get('tray_id')
            tray_qty = int(tray.get('tray_quantity', 0))
            
            print(f"   Processing tray {tray_id}: qty={tray_qty}")
            
            if remaining_rejection_qty >= tray_qty:
                # Fully reject this tray
                rejection_distribution.append({
                    'tray_id': tray_id,
                    'rejected_qty': tray_qty,
                    'remaining_qty': 0,
                    'status': 'Rejection: Auto allocated',
                    'allocation_type': 'full_rejection'
                })
                remaining_rejection_qty -= tray_qty
                print(f"      → Full Rejection: {tray_qty} qty, remaining: {remaining_rejection_qty}")
                
            else:
                # Partial rejection - split the tray
                rejected_qty = remaining_rejection_qty
                accepted_qty = tray_qty - rejected_qty
                
                rejection_distribution.append({
                    'tray_id': tray_id,
                    'rejected_qty': rejected_qty,
                    'remaining_qty': accepted_qty,
                    'status': 'Rejection: Auto allocated',
                    'allocation_type': 'partial_rejection'
                })
                
                # Create acceptance entry for leftover quantity
                if accepted_qty > 0:
                    acceptance_trays.append({
                        'tray_id': tray_id,
                        'accepted_qty': accepted_qty,
                        'status': 'Accepted',
                        'allocation_type': 'partial_acceptance'
                    })
                
                remaining_rejection_qty = 0
                print(f"      → Partial: reject={rejected_qty}, accept={accepted_qty}")
        
        # If rejection not fully allocated, allocate to remaining trays
        if remaining_rejection_qty > 0:
            for tray in sorted_trays:
                tray_id = tray.get('tray_id')
                if any(r['tray_id'] == tray_id for r in rejection_distribution):
                    continue
                tray_qty = int(tray.get('tray_quantity', 0))
                rejected = min(remaining_rejection_qty, tray_qty)
                remaining = tray_qty - rejected
                rejection_distribution.append({
                    'tray_id': tray_id,
                    'rejected_qty': rejected,
                    'remaining_qty': remaining,
                    'status': 'Rejection: Additional allocation',
                    'allocation_type': 'additional_rejection'
                })
                remaining_rejection_qty -= rejected
                if remaining_rejection_qty <= 0:
                    break
        
        # Collect acceptance trays from remaining quantities
        acceptance_trays = []  # Reset and rebuild from remaining quantities
        for tray in available_trays:
            tray_id = tray.get('tray_id')
            rej_entry = next((r for r in rejection_distribution if r['tray_id'] == tray_id), None)
            if rej_entry:
                remaining_qty = rej_entry['remaining_qty']
            else:
                remaining_qty = int(tray.get('tray_quantity', 0))
            if remaining_qty > 0:
                acceptance_trays.append({
                    'tray_id': tray_id,
                    'accepted_qty': remaining_qty,
                    'status': 'Accepted',
                    'allocation_type': 'remaining_acceptance'
                })
        
        # Consolidate acceptance trays
        if total_accepted > 0:
            consolidated = consolidate_acceptance_trays(acceptance_trays, lot_id, total_accepted, tray_capacity)
            acceptance_trays = consolidated['acceptance_trays']
            new_trays_needed = consolidated['new_trays_needed']
        else:
            new_trays_needed = []
        
        # Compute delink trays: available trays not consumed by rejection or acceptance
        allocated_tray_ids = (
            set(r['tray_id'] for r in rejection_distribution) |
            set(a['tray_id'] for a in acceptance_trays)
        )
        delink_trays = [t['tray_id'] for t in available_trays if t['tray_id'] not in allocated_tray_ids]
        
        result = {
            'rejection_distribution': rejection_distribution,
            'delink_trays': delink_trays,
            'acceptance_trays': acceptance_trays,
            'new_trays_needed': new_trays_needed,
            'total_rejected': total_rejection_qty,
            'total_accepted': total_accepted,
            'unallocated_qty': 0
        }
        
        print(f"✅ [AUTO-ALLOCATE] Completed: rejected={result['total_rejected']}, accepted={result['total_accepted']}, delinked={len(delink_trays)}")
        return result
        
    except Exception as e:
        print(f"❌ [AUTO-ALLOCATE] Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return {
            'rejection_distribution': [],
            'delink_trays': [],
            'acceptance_trays': [],
            'new_trays_needed': [],
            'total_rejected': 0,
            'total_accepted': 0,
            'unallocated_qty': total_rejection_qty
        }

def consolidate_acceptance_trays(acceptance_trays, lot_id, total_accepted, tray_capacity):
    """
    Consolidate acceptance trays and generate new tray IDs if needed.
    """
    try:
        print(f"🔄 [CONSOLIDATE] Processing {len(acceptance_trays)} partial acceptance entries, total_accepted: {total_accepted}, tray_capacity: {tray_capacity}")
        
        new_trays_needed = []
        remaining_qty = total_accepted
        
        # First, account for partial acceptances (reuse existing trays)
        for tray in acceptance_trays:
            remaining_qty -= tray['accepted_qty']
        
        # Then create new trays for remaining quantity
        while remaining_qty > 0:
            qty = min(remaining_qty, tray_capacity)
            # Use the first partial tray's ID as base, or default
            base_tray_id = acceptance_trays[0]['tray_id'] if acceptance_trays else 'JB-A00001'
            new_tray_id = generate_new_acceptance_tray_id(base_tray_id)
            
            new_trays_needed.append({
                'new_tray_id': new_tray_id,
                'qty': qty,
                'original_tray_id': base_tray_id,
                'status': 'New tray',
                'delink_source': 'Auto allocation'
            })
            remaining_qty -= qty
        
        return {
            'acceptance_trays': acceptance_trays,
            'new_trays_needed': new_trays_needed
        }
        
    except Exception as e:
        print(f"❌ [CONSOLIDATE] Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return {
            'acceptance_trays': acceptance_trays,
            'new_trays_needed': []
        }
        
    except Exception as e:
        print(f"❌ [CONSOLIDATE] Error: {str(e)}")
        return {
            'acceptance_trays': acceptance_trays,
            'new_trays_needed': []
        }

def generate_new_acceptance_tray_id(original_tray_id):
    """
    Generate new tray ID for acceptance based on original tray ID.
    Example: JB-A00114 → JB-A00200
    """
    try:
        import re
        
        # Extract prefix and generate new sequence
        match = re.match(r'([A-Z]+)-([A-Z])(\d{5})', original_tray_id)
        if match:
            prefix = match.group(1)  # JB
            type_char = match.group(2)  # A
            
            # Generate new sequence number for acceptance
            # For simplicity, use timestamp-based approach
            from datetime import datetime
            timestamp = datetime.now().strftime("%m%d%H")
            
            # Ensure it's exactly 5 digits
            new_sequence = timestamp[-5:].zfill(5)
            new_sequence = new_sequence.replace(new_sequence[-2:], '00')  # End with 00 for acceptance
            
            new_tray_id = f"{prefix}-{type_char}{new_sequence}"
            # Ensure the new tray ID is unique across master and IQF tables
            if TrayId.objects.filter(tray_id=new_tray_id).exists() or IQFTrayId.objects.filter(tray_id=new_tray_id).exists():
                base_sequence = int(new_sequence)
                for offset in range(1, 100):
                    candidate_sequence = str(base_sequence + offset).zfill(5)
                    candidate_sequence = candidate_sequence.replace(candidate_sequence[-2:], '00')
                    candidate_id = f"{prefix}-{type_char}{candidate_sequence}"
                    if not TrayId.objects.filter(tray_id=candidate_id).exists() and not IQFTrayId.objects.filter(tray_id=candidate_id).exists():
                        new_tray_id = candidate_id
                        break
            print(f"📋 Generated new acceptance tray ID: {original_tray_id} → {new_tray_id}")
            return new_tray_id
        
        # Fallback for non-standard format
        from datetime import datetime
        timestamp = datetime.now().strftime("%m%d%H")
        return f"ACC-{timestamp}00"
        
    except Exception as e:
        print(f"❌ [TRAY-ID] Error generating new tray ID: {str(e)}")
        from datetime import datetime
        timestamp = datetime.now().strftime("%m%d%H%M")
        return f"ACC-{timestamp}"

def get_iqf_available_trays_for_allocation(lot_id):
    """
    Get all available IQF trays for auto-allocation.
    Returns list of tray data with tray_id, tray_quantity, tray_capacity.
    ✅ FIXED: Now excludes both finalized AND draft accepted trays
    """
    try:
        print(f"📊 [AVAILABLE-TRAYS] Fetching available trays for lot {lot_id}")
        
        # Get accepted tray IDs to exclude (finalized)
        accepted_tray_ids = list(IQF_Accepted_TrayID_Store.objects.filter(lot_id=lot_id).values_list('tray_id', flat=True))
        
        # ✅ ADDED: Also exclude draft accepted trays to prevent them from being allocated to rejection
        try:
            import json
            draft_acceptance = IQF_Draft_Store.objects.filter(
                lot_id=lot_id,
                draft_type='accepted_tray'
            ).order_by('-created_at').first()
            
            if draft_acceptance and draft_acceptance.draft_data:
                draft_data = draft_acceptance.draft_data if isinstance(draft_acceptance.draft_data, dict) else json.loads(draft_acceptance.draft_data) if draft_acceptance.draft_data else {}
                if isinstance(draft_data, dict) and 'accepted_trays' in draft_data:
                    for tray_entry in draft_data.get('accepted_trays', []):
                        if isinstance(tray_entry, dict):
                            tray_id = tray_entry.get('tray_id')
                        else:
                            tray_id = tray_entry
                        if tray_id and tray_id not in accepted_tray_ids:
                            accepted_tray_ids.append(tray_id)
                            print(f"   ⚠️ Excluding draft accepted tray: {tray_id}")
        except Exception as e:
            print(f"   ⚠️ Could not parse draft acceptance data: {str(e)}")
        
        # Get all trays for the lot (including delinked ones for allocation)
        trays = IQFTrayId.objects.filter(
            lot_id=lot_id
        ).exclude(
            tray_quantity__lte=0
        ).exclude(
            tray_id__in=accepted_tray_ids
        ).order_by('id')
        
        if trays.exists():
            # Primary path: IQFTrayId records exist
            tray_list = []
            for tray in trays:
                tray_data = {
                    'tray_id': tray.tray_id,
                    'tray_quantity': int(tray.tray_quantity or 0),
                    'tray_capacity': int(tray.tray_capacity or tray.tray_quantity or 12),
                    'tray_type': tray.tray_type,
                    'top_tray': tray.top_tray,
                    'rejected_tray': tray.rejected_tray
                }
                tray_list.append(tray_data)
                print(f"   Available tray: {tray_data['tray_id']} qty={tray_data['tray_quantity']} cap={tray_data['tray_capacity']}")
        else:
            # Fallback path: Build from upstream sources when IQFTrayId doesn't exist yet
            print(f"   [FALLBACK] No IQFTrayId records found, building from upstream sources...")
            
            # Determine source based on stock configuration
            stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
            use_audit = bool(getattr(stock, 'send_brass_audit_to_iqf', False))
            
            # Build IQF-eligible tray IDs using same logic as iqf_get_remaining_trays
            rejected_scan_tray_ids = set(
                IQF_Rejected_TrayScan.objects.filter(lot_id=lot_id).exclude(tray_id='').values_list('tray_id', flat=True)
            )
            if rejected_scan_tray_ids:
                eligible_tray_ids = rejected_scan_tray_ids
                print(f"   [FALLBACK] Using IQF rejected scan tray IDs: {len(eligible_tray_ids)} trays")
            else:
                if use_audit:
                    upstream_ids = set(
                        Brass_Audit_Rejected_TrayScan.objects.filter(lot_id=lot_id).exclude(rejected_tray_id='').values_list('rejected_tray_id', flat=True)
                    )
                    source_name = "Brass Audit"
                else:
                    upstream_ids = set(
                        Brass_QC_Rejected_TrayScan.objects.filter(lot_id=lot_id).exclude(rejected_tray_id='').values_list('rejected_tray_id', flat=True)
                    )
                    source_name = "Brass QC"
                
                if upstream_ids:
                    eligible_tray_ids = upstream_ids
                    print(f"   [FALLBACK] Using {source_name} rejected scan tray IDs: {len(eligible_tray_ids)} trays")
                else:
                    # Final fallback to IP verified trays
                    eligible_tray_ids = set(
                        IQFTrayId.objects.filter(lot_id=lot_id, IP_tray_verified=True).values_list('tray_id', flat=True)
                    )
                    eligible_tray_ids |= set(
                        DPTrayId_History.objects.filter(lot_id=lot_id, IP_tray_verified=True).values_list('tray_id', flat=True)
                    )
                    
                    # If no IQF/DP records either, use all TrayId records for this lot (fresh lot scenario)
                    if not eligible_tray_ids:
                        eligible_tray_ids = set(
                            TrayId.objects.filter(lot_id=lot_id, IP_tray_verified=True).values_list('tray_id', flat=True)
                        )
                        print(f"   [FALLBACK] Using all TrayId records for fresh lot: {len(eligible_tray_ids)} trays")
                    else:
                        print(f"   [FALLBACK] Using IP verified tray IDs: {len(eligible_tray_ids)} trays")
            
            # Filter and validate eligible tray IDs
            eligible_tray_ids = {t for t in eligible_tray_ids if t}
            eligible_tray_ids = set(
                TrayId.objects.filter(
                    tray_id__in=eligible_tray_ids,
                    new_tray=False,
                    IP_tray_verified=True
                ).values_list('tray_id', flat=True)
            )
            
            # Remove accepted tray IDs from eligible list
            eligible_tray_ids = eligible_tray_ids - set(accepted_tray_ids)
            
            print(f"   [FALLBACK] Final eligible tray IDs: {eligible_tray_ids}")
            
            # Helper function to get best quantity for a tray
            def _best_qty_for_tray(tid):
                qtys = []
                for model in [IQFTrayId, DPTrayId_History, BrassTrayId, BrassAuditTrayId, IPTrayId]:
                    try:
                        q = model.objects.filter(lot_id=lot_id, tray_id=tid).values_list('tray_quantity', flat=True).first()
                        if q is not None:
                            qtys.append(int(q or 0))
                    except Exception:
                        pass
                try:
                    master_q = TrayId.objects.filter(tray_id=tid).values_list('tray_quantity', flat=True).first()
                    if master_q is not None:
                        qtys.append(int(master_q or 0))
                except Exception:
                    pass
                return max(qtys) if qtys else 0
            
            # Build tray list from eligible IDs
            tray_list = []
            for tray_id in eligible_tray_ids:
                qty = _best_qty_for_tray(tray_id)
                if qty > 0:  # Only include trays with positive quantity
                    # Get tray details from TrayId table
                    master_tray = TrayId.objects.filter(tray_id=tray_id).first()
                    if master_tray:
                        tray_data = {
                            'tray_id': tray_id,
                            'tray_quantity': qty,
                            'tray_capacity': int(master_tray.tray_capacity or qty or 12),
                            'tray_type': getattr(master_tray, 'tray_type', ''),
                            'top_tray': False,  # Default for fallback
                            'rejected_tray': False  # Available for allocation
                        }
                        tray_list.append(tray_data)
                        print(f"   [FALLBACK] Available tray: {tray_data['tray_id']} qty={tray_data['tray_quantity']} cap={tray_data['tray_capacity']}")
        
        print(f"✅ [AVAILABLE-TRAYS] Found {len(tray_list)} available trays")
        return tray_list
        
    except Exception as e:
        print(f"❌ [AVAILABLE-TRAYS] Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return []

def apply_iqf_auto_allocation_results(lot_id, batch_id, allocation_results, rejection_reasons, user, acceptance_remarks=""):
    """
    Apply the auto-allocation results to the database.
    """
    try:
        print(f"💾 [APPLY-ALLOCATION] Starting database updates for lot {lot_id}")
        
        # Step 1: Save rejection reasons
        total_rejected_qty = allocation_results['total_rejected']
        if total_rejected_qty > 0:
            reason_store = IQF_Rejection_ReasonStore.objects.create(
                lot_id=lot_id,
                user=user,
                total_rejection_quantity=total_rejected_qty,
                batch_rejection=False
            )
            
            # Add rejection reasons
            reason_ids = [r.get('reason_id') for r in rejection_reasons]
            reasons = IQF_Rejection_Table.objects.filter(rejection_reason_id__in=reason_ids)
            reason_store.rejection_reason.set(reasons)
            
            print(f"   ✅ Created rejection reason store: {total_rejected_qty} qty")
        
        # Step 2: Save rejection distribution
        for rejection in allocation_results['rejection_distribution']:
            tray_id = rejection['tray_id']
            rejected_qty = rejection['rejected_qty']
            
            # Find appropriate rejection reason
            reason_obj = None
            if rejection_reasons:
                reason_id = rejection_reasons[0].get('reason_id')  # Use first reason for now
                reason_obj = IQF_Rejection_Table.objects.filter(rejection_reason_id=reason_id).first()
            
            # Create IQF_Rejected_TrayScan entry
            IQF_Rejected_TrayScan.objects.create(
                lot_id=lot_id,
                tray_id=tray_id,
                rejected_tray_quantity=str(rejected_qty),
                rejection_reason=reason_obj,
                user=user,
                top_tray=False
            )
            
            # Update IQFTrayId
            iqf_tray = IQFTrayId.objects.filter(tray_id=tray_id, lot_id=lot_id).first()
            if iqf_tray:
                if rejection['allocation_type'] == 'full_rejection':
                    # Keep lot_id and batch_id intact so the completed table view can
                    # find these trays via lot_id filter. Setting lot_id=None was
                    # making the rejected trays invisible in IQFCompleteTableTrayIdListAPIView.
                    # delink_tray stays False — these are rejected trays, not delinked trays.
                    iqf_tray.rejected_tray = True
                    iqf_tray.tray_quantity = 0
                else:
                    # Partial rejection - update remaining quantity
                    iqf_tray.tray_quantity = rejection['remaining_qty']
                    iqf_tray.rejected_tray = True
                iqf_tray.save()
            
            print(f"   ✅ Saved rejection: {tray_id} rejected_qty={rejected_qty}")
        
        # Step 3: Save acceptance trays from auto-allocation results (additional ones beyond frontend)
        total_accepted_qty = allocation_results['total_accepted']
        if allocation_results['acceptance_trays']:
            # Create accepted tray scan summary
            IQF_Accepted_TrayScan.objects.create(
                lot_id=lot_id,
                accepted_tray_quantity=str(total_accepted_qty),
                user=user
            )
            
            # Save additional accepted tray IDs from auto-allocation (avoid duplicates)
            existing_accepted_tray_ids = set(
                IQF_Accepted_TrayID_Store.objects.filter(lot_id=lot_id).values_list('tray_id', flat=True)
            )
            
            for acceptance in allocation_results['acceptance_trays']:
                tray_id = acceptance['tray_id']
                if tray_id not in existing_accepted_tray_ids:
                    IQF_Accepted_TrayID_Store.objects.create(
                        lot_id=lot_id,
                        tray_id=tray_id,
                        tray_qty=acceptance['accepted_qty'], 
                        user=user,
                        accepted_comment=acceptance_remarks
                    )
                    print(f"   ✅ Saved auto-allocation acceptance: {tray_id} accepted_qty={acceptance['accepted_qty']}")
                else:
                    print(f"   ⏭️  Skipped duplicate acceptance: {tray_id} (already saved from frontend)")
                
                # Update IQFTrayId for partial acceptance
                if acceptance.get('allocation_type') == 'partial_acceptance':
                    iqf_tray = IQFTrayId.objects.filter(tray_id=tray_id, lot_id=lot_id).first()
                    if iqf_tray:
                        iqf_tray.tray_quantity = acceptance['accepted_qty']
                        iqf_tray.save()

        # Step 3.5: Mark frontend draft accepted trays as saved (is_save=True)
        # These are the user-scanned trays that will be picked up by Brass QC
        # via create_brass_tray_instances when brass_save_ip_checkbox is clicked.
        auto_tray_ids = {a['tray_id'] for a in allocation_results.get('acceptance_trays', [])}
        IQF_Accepted_TrayID_Store.objects.filter(
            lot_id=lot_id,
            is_save=False
        ).exclude(tray_id__in=auto_tray_ids).update(is_save=True)
        print(f"   ✅ Marked draft accepted trays as is_save=True (source for Brass QC)")

        # Step 4: Create new trays for acceptance
        batch_obj = ModelMasterCreation.objects.filter(batch_id=batch_id).first()
        for new_tray in allocation_results['new_trays_needed']:
            # Create new IQFTrayId
            IQFTrayId.objects.create(
                tray_id=new_tray['new_tray_id'],
                lot_id=lot_id,
                batch_id=batch_obj,
                tray_quantity=new_tray['qty'],
                tray_capacity=new_tray['qty'],  # Set capacity to current qty
                rejected_tray=False,
                new_tray=True
            )
            
            # Also create in TrayId master table
            TrayId.objects.create(
                tray_id=new_tray['new_tray_id'],
                lot_id=lot_id,
                batch_id=batch_obj,
                tray_quantity=new_tray['qty'],
                tray_capacity=new_tray['qty'],
                rejected_tray=False,
                new_tray=True
            )
            
            print(f"   ✅ Created new tray: {new_tray['new_tray_id']} qty={new_tray['qty']}")
        
        # Step 5: Update TotalStockModel
        total_stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
        if total_stock:
            if total_rejected_qty == total_stock.iqf_physical_qty:
                # Full lot rejection
                total_stock.iqf_rejection = True
                total_stock.next_process_module = "Jig Loading"
            else:
                # Few cases acceptance
                total_stock.iqf_few_cases_acceptance = True
                total_stock.iqf_onhold_picking = True
                # Compute actual accepted qty from IQF_Accepted_TrayID_Store (Step 3.5 already marked them is_save=True)
                # auto-allocator returns 0 when all available trays were rejected and only frontend trays accepted
                _store_qty = IQF_Accepted_TrayID_Store.objects.filter(
                    lot_id=lot_id, is_save=True
                ).aggregate(total=Sum('tray_qty'))['total'] or 0
                effective_accepted_qty = _store_qty if _store_qty > 0 else total_accepted_qty
                total_stock.iqf_accepted_qty = effective_accepted_qty
                total_stock.total_IP_accpeted_quantity = effective_accepted_qty  # Fix Brass QC display qty
                total_stock.send_brass_qc = True  # send accepted pieces to Brass QC pick table
            
            total_stock.last_process_module = "IQF"
            total_stock.iqf_last_process_date_time = timezone.now()
            total_stock.save()
            
            print(f"   ✅ Updated TotalStockModel: rejection={total_stock.iqf_rejection}, few_cases={total_stock.iqf_few_cases_acceptance}")
        
        print(f"✅ [APPLY-ALLOCATION] Database updates completed successfully")
        return {'success': True, 'message': 'Auto-allocation applied successfully'}
        
    except Exception as e:
        print(f"❌ [APPLY-ALLOCATION] Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return {'success': False, 'error': str(e)}
