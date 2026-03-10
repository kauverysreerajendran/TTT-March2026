from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.renderers import TemplateHTMLRenderer
from django.shortcuts import render
from django.db.models import OuterRef, Subquery, Exists, F
from django.core.paginator import Paginator
from django.templatetags.static import static
import math
from modelmasterapp.models import *
from DayPlanning.models import *
from InputScreening.models import *
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
from math import ceil
from django.db.models import Q
import math  # ✅ ADD: Missing import for math.ceil
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from IQF.models import *
from BrassAudit.models import *
from Brass_QC.models import *
from Jig_Loading.models import *
from django.utils import timezone
from django.contrib.auth.decorators import login_required

# Import the reverse transfer function from Brass QC
from Brass_QC.views import send_brass_audit_back_to_brass_qc

 


@method_decorator(login_required, name='dispatch')
class BrassAuditPickTableView(APIView):
    renderer_classes = [TemplateHTMLRenderer]
    template_name = 'BrassAudit/BrassAudit_PickTable.html'

    def get(self, request):
        user = request.user
        is_admin = user.groups.filter(name='Admin').exists() if user.is_authenticated else False

        # Handle sorting parameters
        sort = request.GET.get('sort')
        order = request.GET.get('order', 'asc')  # Default to ascending
        
        # Field mapping for proper model field references
        sort_field_mapping = {
            'serial_number': 'lot_id',  # Use lot_id for serial number sorting
            'brass_audit_last_process_date_time': 'bq_last_process_date_time',
            'plating_stk_no': 'batch_id__plating_stk_no',
            'polishing_stk_no': 'batch_id__polishing_stk_no',
            'plating_color': 'batch_id__plating_color',
            'category': 'batch_id__category',
            'polish_finish': 'batch_id__polish_finish',
            'tray_capacity': 'batch_id__tray_capacity',
            'vendor_location': 'batch_id__vendor_internal',  # Simplified to vendor field
            'no_of_trays': 'batch_id__tray_capacity',  # Approximate mapping
            'lot_qty': 'brass_qc_accepted_qty',
            'brass_audit_physical_qty': 'brass_audit_physical_qty',
            'brass_audit_accepted_qty': 'brass_audit_accepted_qty',
            'reject_qty': 'brass_rejection_total_qty'
        }

        brass_rejection_reasons = Brass_Audit_Rejection_Table.objects.all()

        # ✅ CHANGED: Query TotalStockModel directly instead of ModelMasterCreation
        # This way we get separate entries for each lot_id
        queryset = TotalStockModel.objects.select_related(
            'batch_id',
            'batch_id__model_stock_no',
            'batch_id__version',
            'batch_id__location'
        ).filter(
            batch_id__total_batch_quantity__gt=0
        )

        # ✅ Add draft status subqueries
        has_draft_subquery = Exists(
            Brass_Audit_Draft_Store.objects.filter(
                lot_id=OuterRef('lot_id')
            )
        )
        
        draft_type_subquery = Brass_Audit_Draft_Store.objects.filter(
            lot_id=OuterRef('lot_id')
        ).values('draft_type')[:1]

        brass_rejection_qty_subquery = Brass_Audit_Rejection_ReasonStore.objects.filter(
            lot_id=OuterRef('lot_id')
        ).values('total_rejection_quantity')[:1]

        # ✅ Annotate with additional fields
        queryset = queryset.annotate(
            wiping_required=F('batch_id__model_stock_no__wiping_required'),
            has_draft=has_draft_subquery,
            draft_type=draft_type_subquery,
            brass_rejection_total_qty=brass_rejection_qty_subquery,
        )

        # ✅ UPDATED: Filter logic for lots coming from Brass QC and existing Brass Audit lots
        queryset = queryset.filter(
            # Show lots coming from Brass QC (accepted) that haven't been completed in Brass Audit
            Q(brass_qc_accptance=True, brass_audit_accptance__isnull=True) |
            Q(brass_qc_accptance=True, brass_audit_accptance=False) |
            # Show lots with few cases acceptance in Brass QC that are not on hold
            Q(brass_qc_few_cases_accptance=True, brass_onhold_picking=False)|
            # Show existing Brass Audit lots that are on hold or partially processed
            Q(brass_audit_few_cases_accptance=True, brass_audit_onhold_picking=True)
        ).exclude(
            # Exclude completed Brass Audit lots
            brass_audit_accptance=True
        ).exclude(
            # Exclude rejected Brass Audit lots  
            brass_audit_rejection=True
        ).exclude(
            # Exclude completed few cases lots that are not on hold
            Q(brass_audit_few_cases_accptance=True, brass_audit_onhold_picking=False)
        )
        
        # Apply sorting if requested
        if sort and sort in sort_field_mapping:
            field = sort_field_mapping[sort]
            if order == 'desc':
                field = '-' + field
            queryset = queryset.order_by(field)
        else:
            queryset = queryset.order_by('-bq_last_process_date_time', '-lot_id')
        
        print("All lot_ids in queryset:", list(queryset.values_list('lot_id', flat=True)))

        # Pagination
        page_number = request.GET.get('page', 1)
        paginator = Paginator(queryset, 10)
        page_obj = paginator.get_page(page_number)

        # ✅ UPDATED: Get values from TotalStockModel instead of ModelMasterCreation
        master_data = []
        for stock_obj in page_obj.object_list:
            batch = stock_obj.batch_id
            brass_qc_accepted_qty = stock_obj.brass_qc_accepted_qty or 0

            data = {
                'batch_id': batch.batch_id,
                'lot_id': stock_obj.lot_id,  # ✅ Now we have the actual lot_id
                'date_time': batch.date_time,
                'model_stock_no__model_no': batch.model_stock_no.model_no,
                'plating_color': batch.plating_color,
                'polish_finish': batch.polish_finish,
                'version__version_name': batch.version.version_name if batch.version else '',
                'vendor_internal': batch.vendor_internal,
                'location__location_name': batch.location.location_name if batch.location else '',
                'tray_type': batch.tray_type,
                'tray_capacity': batch.tray_capacity,
                'wiping_required': stock_obj.wiping_required,
                'brass_onhold_picking':stock_obj.brass_onhold_picking,
                # ✅ Stock-related fields from TotalStockModel
                'stock_lot_id': stock_obj.lot_id,
                'brass_audit_accepted_qty': stock_obj.brass_audit_accepted_qty,
                'brass_audit_accepted_qty_verified': stock_obj.brass_audit_accepted_qty_verified,
                'brass_qc_accepted_qty': stock_obj.brass_qc_accepted_qty,
                'brass_audit_missing_qty': stock_obj.brass_audit_missing_qty,
                'brass_audit_physical_qty': stock_obj.brass_audit_physical_qty,
                'brass_audit_physical_qty_edited': stock_obj.brass_audit_physical_qty_edited,
                'accepted_Ip_stock': stock_obj.accepted_Ip_stock,
                'rejected_ip_stock': stock_obj.rejected_ip_stock,
                'few_cases_accepted_Ip_stock': stock_obj.few_cases_accepted_Ip_stock,
                'accepted_tray_scan_status': stock_obj.accepted_tray_scan_status,
                'BA_pick_remarks': stock_obj.BA_pick_remarks,
                'brass_qc_accptance': stock_obj.brass_qc_accptance,
                'brass_accepted_tray_scan_status': stock_obj.brass_accepted_tray_scan_status,
                'brass_audit_rejection': stock_obj.brass_audit_rejection,
                'brass_qc_few_cases_accptance': stock_obj.brass_qc_few_cases_accptance,
                'brass_audit_onhold_picking': stock_obj.brass_audit_onhold_picking,
                'brass_audit_draft': stock_obj.brass_audit_draft,
                'iqf_acceptance': stock_obj.iqf_acceptance,
                'send_brass_qc': stock_obj.send_brass_qc,  # ✅ This will now show True for new lots
                'bq_last_process_date_time': stock_obj.bq_last_process_date_time,
                'iqf_last_process_date_time': stock_obj.iqf_last_process_date_time,
                'brass_audit_hold_lot': stock_obj.brass_audit_hold_lot,
                'brass_audit_holding_reason': stock_obj.brass_audit_holding_reason,
                'brass_audit_release_lot': stock_obj.brass_audit_release_lot,
                'brass_audit_release_reason': stock_obj.brass_audit_release_reason,
                'has_draft': stock_obj.has_draft,
                'draft_type': stock_obj.draft_type,
                'brass_rejection_total_qty': stock_obj.brass_rejection_total_qty,
                
                # Additional batch fields
                'plating_stk_no': batch.plating_stk_no,
                'polishing_stk_no': batch.polishing_stk_no,
                'category': batch.category,
                'last_process_module': stock_obj.last_process_module
            }
                        # --- AQL Sampling Plan Calculation ---
            # After you get brass_qc_accepted_qty for the lot
            aql_plan = AQLSamplingPlan.objects.filter(
                lot_qty_from__lte=brass_qc_accepted_qty,
                lot_qty_to__gte=brass_qc_accepted_qty
            ).first()
            data['aql_limit'] = float(aql_plan.aql_limit) if aql_plan else None
            data['sample_qty'] = aql_plan.sample_qty if aql_plan else None

            master_data.append(data)

        # ✅ Process the data as before
        for data in master_data:   
            brass_qc_accepted_qty = data.get('brass_qc_accepted_qty', 0)
            tray_capacity = data.get('tray_capacity', 0)
            data['vendor_location'] = f"{data.get('vendor_internal', '')}_{data.get('location__location_name', '')}"
            
            lot_id = data.get('stock_lot_id')
            
            if brass_qc_accepted_qty and brass_qc_accepted_qty > 0:
                data['display_accepted_qty'] = brass_qc_accepted_qty
            else:
                total_rejection_qty = 0
                rejection_store = Brass_QC_Rejection_ReasonStore.objects.filter(lot_id=lot_id).first()
                if rejection_store and rejection_store.total_rejection_quantity:
                    total_rejection_qty = rejection_store.total_rejection_quantity

                total_stock_obj = TotalStockModel.objects.filter(lot_id=lot_id).first()
                
                if total_stock_obj and total_rejection_qty > 0:
                    data['display_accepted_qty'] = max(total_stock_obj.total_stock - total_rejection_qty, 0)
                else:
                        data['display_accepted_qty'] = 0

                # If display_accepted_qty is still zero, prefer TotalStockModel brass_physical_qty
                # or total_stock as a sensible fallback so the UI shows meaningful Lot/Physical values
                try:
                    if data.get('display_accepted_qty', 0) == 0 and total_stock_obj:
                        if getattr(total_stock_obj, 'brass_physical_qty', 0) and total_stock_obj.brass_physical_qty > 0:
                            data['display_accepted_qty'] = total_stock_obj.brass_physical_qty
                        elif getattr(total_stock_obj, 'total_stock', 0) and total_stock_obj.total_stock > 0:
                            data['display_accepted_qty'] = total_stock_obj.total_stock
                except Exception:
                    # Be defensive - don't break the whole view if TotalStockModel has unexpected state
                    pass

            brass_audit_physical_qty = data.get('brass_audit_physical_qty') or 0
            brass_rejection_total_qty = data.get('brass_rejection_total_qty') or 0
            is_delink_only = (brass_audit_physical_qty > 0 and 
                              brass_rejection_total_qty >= brass_audit_physical_qty and 
                              data.get('brass_audit_onhold_picking', False))
            data['is_delink_only'] = is_delink_only

            display_qty = data.get('display_accepted_qty', 0)
            if tray_capacity > 0 and display_qty > 0:
                data['no_of_trays'] = math.ceil(display_qty / tray_capacity)
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
        
            # Add available_qty
            if data.get('brass_audit_physical_qty') and data.get('brass_audit_physical_qty') > 0:
                data['available_qty'] = data.get('brass_audit_physical_qty')
            else:
                data['available_qty'] = data.get('brass_qc_accepted_qty', 0)

            # Fallbacks: if audit-specific fields are not set, prefer QC-level values so the UI
            # still shows meaningful Lot / Missing / Physical quantities to the user
            if not data.get('brass_audit_physical_qty'):
                data['brass_audit_physical_qty'] = data.get('brass_physical_qty', 0)

            if not data.get('brass_audit_missing_qty'):
                data['brass_audit_missing_qty'] = data.get('brass_missing_qty', 0)

        print(f"[DEBUG] Master data loaded with {len(master_data)} entries.")
        print("All lot_ids in processed data:", [data['stock_lot_id'] for data in master_data])
        
        context = {
            'master_data': master_data,
            'page_obj': page_obj,
            'paginator': paginator,
            'user': user,
            'is_admin': is_admin,
            'brass_rejection_reasons': brass_rejection_reasons,
            'pick_table_count': len(master_data),
        }
        return Response(context, template_name=self.template_name)

@method_decorator(csrf_exempt, name='dispatch')
class BrassAudit_SaveHoldUnholdReasonAPIView(APIView):
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
                obj.brass_audit_holding_reason = remark
                obj.brass_audit_hold_lot = True
                obj.brass_audit_release_reason = ''
                obj.brass_audit_release_lot = False
            elif action == 'unhold':
                obj.brass_audit_release_reason = remark
                obj.brass_audit_hold_lot = False
                obj.brass_audit_release_lot = True

            obj.save(update_fields=['brass_audit_holding_reason', 'brass_audit_release_reason', 'brass_audit_hold_lot', 'brass_audit_release_lot'])
            return JsonResponse({'success': True, 'message': 'Reason saved.'})

        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
        
    
@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(login_required, name='dispatch')  
class BrassAudit_SaveIPCheckboxView(APIView):
    def post(self, request, format=None):
        try:
            data = request.data
            lot_id = data.get("lot_id")
            missing_qty = data.get("missing_qty")
            print("DEBUG: Received missing_qty:", missing_qty)

            if not lot_id:
                return Response({"success": False, "error": "Lot ID is required"}, status=status.HTTP_400_BAD_REQUEST)

            total_stock = TotalStockModel.objects.get(lot_id=lot_id)
            total_stock.brass_audit_accepted_qty_verified = True
            total_stock.last_process_module = "Brass Audit"
            total_stock.next_process_module = "Jig Loading"

            # Calculate display_accepted_qty
            display_accepted_qty = 0
            if total_stock.brass_qc_accepted_qty and total_stock.brass_qc_accepted_qty > 0:
                display_accepted_qty = total_stock.brass_qc_accepted_qty
            else:
                total_rejection_qty = 0
                rejection_store = Brass_QC_Rejection_ReasonStore.objects.filter(lot_id=lot_id).first()
                if rejection_store and rejection_store.total_rejection_quantity:
                    total_rejection_qty = rejection_store.total_rejection_quantity

                if total_rejection_qty > 0:
                    display_accepted_qty = max(total_stock.brass_qc_accepted_qty - total_rejection_qty, 0)
                else:
                    display_accepted_qty = 0

            if missing_qty not in [None, ""]:
                try:
                    missing_qty = int(missing_qty)
                except ValueError:
                    return Response({"success": False, "error": "Missing quantity must be an integer"}, status=status.HTTP_400_BAD_REQUEST)
            
                if missing_qty > display_accepted_qty:
                    return Response(
                        {"success": False, "error": f"Missing quantity must be less than or equal to display accepted quantity ({display_accepted_qty})."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            
                total_stock.brass_audit_missing_qty = missing_qty
                total_stock.brass_audit_physical_qty = display_accepted_qty - missing_qty
            
                self.create_brass_tray_instances(lot_id)
            
            total_stock.save()
            return Response({"success": True})

        except TotalStockModel.DoesNotExist:
            return Response({"success": False, "error": "Stock not found."}, status=status.HTTP_404_NOT_FOUND)

        except Exception as e:
            import traceback
            traceback.print_exc()
            return Response({"success": False, "error": "Unexpected error occurred"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def create_brass_tray_instances(self, lot_id):
        """
        Create or update BrassTrayId instances for all verified tray IDs in the given lot (excluding rejected trays).
        If a BrassTrayId exists for tray_id but lot_id is empty, update it.
        """
        try:
            print(f"✅ [create_brass_tray_instances] Starting for lot_id: {lot_id}")
    
            # Check if send_brass_qc is True for this lot
            total_stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
            send_brass_qc = total_stock.send_brass_qc if total_stock else False
    
            if send_brass_qc:
                # Use IQFTrayId for accepted trays
                verified_trays = IQFTrayId.objects.filter(
                    lot_id=lot_id,
                    IP_tray_verified=True
                ).exclude(
                    rejected_tray=True
                )
                print(f"Using IQFTrayId for tray creation (send_brass_qc=True)")
            else:
                # Use BrassAuditTrayId for accepted trays
                verified_trays = BrassTrayId.objects.filter(
                    lot_id=lot_id,
                ).exclude(
                    rejected_tray=True
                )
                print(f"Using BrassTrayId for tray creation (send_brass_qc=False)")

            total_stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
            batch_id = total_stock.batch_id if total_stock else None
    
            if not batch_id:
                print(f"❌ [create_brass_tray_instances] No batch_id found for lot {lot_id}")
                return
    
            created_count = 0
            updated_count = 0
    
            for tray in verified_trays:
                # Only update if tray exists with lot_id IS NULL (placeholder tray)
                brass_tray = BrassAuditTrayId.objects.filter(tray_id=tray.tray_id, lot_id__isnull=True).first()
                if brass_tray:
                    print(f"🔄 [create_brass_tray_instances] Updating BrassAuditTrayId with empty lot_id for tray_id: {tray.tray_id}")
                    brass_tray.lot_id = lot_id
                    brass_tray.batch_id = batch_id
                    brass_tray.date = timezone.now()
                    brass_tray.user = self.request.user
                    brass_tray.tray_quantity = tray.tray_quantity
                    brass_tray.top_tray = tray.top_tray
                    brass_tray.IP_tray_verified = True
                    brass_tray.tray_type = tray.tray_type
                    brass_tray.tray_capacity = tray.tray_capacity
                    brass_tray.new_tray = False
                    brass_tray.delink_tray = False
                    brass_tray.rejected_tray = False
                    brass_tray.save(update_fields=[
                        'lot_id', 'batch_id', 'date', 'user', 'tray_quantity',
                        'top_tray', 'IP_tray_verified', 'tray_type', 'tray_capacity',
                        'new_tray', 'delink_tray', 'rejected_tray'
                    ])
                    updated_count += 1
                    print(f"✅ [create_brass_tray_instances] Updated BrassTrayId for: {tray.tray_id} (top_tray: {tray.top_tray}, rejected: False)")
                else:
                    # Always create a new record for this lot_id and tray_id
                    print(f"➕ [create_brass_tray_instances] Creating new BrassTrayId for tray_id: {tray.tray_id}")
                    brass_tray = BrassAuditTrayId(
                        tray_id=tray.tray_id,
                        lot_id=lot_id,
                        batch_id=batch_id,
                        date=timezone.now(),
                        user=self.request.user,
                        tray_quantity=tray.tray_quantity,
                        top_tray=tray.top_tray,
                        IP_tray_verified=True,
                        tray_type=tray.tray_type,
                        tray_capacity=tray.tray_capacity,
                        new_tray=False,
                        delink_tray=False,
                        rejected_tray=False
                    )
                    brass_tray.save()
                    created_count += 1
                    print(f"✅ [create_brass_tray_instances] Created new BrassTrayId for: {tray.tray_id} (top_tray: {tray.top_tray}, rejected: False)")
    
        except Exception as e:
            print(f"❌ [create_brass_tray_instances] Error creating/updating BrassTrayId instances: {str(e)}")
            import traceback
            traceback.print_exc()
    
    def get(self, request, format=None):
        return Response(
            {"success": False, "error": "Invalid request method."},
            status=status.HTTP_400_BAD_REQUEST
        )
        
@method_decorator(csrf_exempt, name='dispatch')
class BrassSaveIPPickRemarkAPIView(APIView):
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
            batch_obj.BA_pick_remarks = remark
            batch_obj.save(update_fields=['BA_pick_remarks'])
            return JsonResponse({'success': True, 'message': 'Remark saved'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)

@require_GET
def brass_get_tray_capacity_for_lot(request):
    """
    Get ACTUAL tray capacity for a specific lot from the same source as main table
    """
    lot_id = request.GET.get('lot_id')
    if not lot_id:
        return JsonResponse({'success': False, 'error': 'Missing lot_id'})
    
    try:
        print(f"🔍 [brass_get_tray_capacity_for_lot] Getting tray capacity for lot_id: {lot_id}")
        
        # ✅ METHOD 1: Get from TotalStockModel -> batch_id (same as main table)
        total_stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
        if total_stock:
            print(f"✅ Found TotalStockModel for lot_id: {lot_id}")
            
            # Get the batch_id from TotalStockModel
            if hasattr(total_stock, 'batch_id') and total_stock.batch_id:
                batch_obj = total_stock.batch_id  # This is ModelMasterCreation object
                print(f"✅ Found batch_id: {batch_obj.batch_id}")
                
                # Get tray_capacity from ModelMasterCreation (same as main table)
                if hasattr(batch_obj, 'tray_capacity') and batch_obj.tray_capacity:
                    tray_capacity = batch_obj.tray_capacity
                    print(f"✅ Found tray_capacity from ModelMasterCreation: {tray_capacity}")
                    return JsonResponse({
                        'success': True, 
                        'tray_capacity': tray_capacity,
                        'source': 'ModelMasterCreation.tray_capacity'
                    })
        
        # ✅ METHOD 2: Direct lookup in ModelMasterCreation by lot_id
        try:
            model_creation = ModelMasterCreation.objects.filter(lot_id=lot_id).first()
            if model_creation and hasattr(model_creation, 'tray_capacity') and model_creation.tray_capacity:
                tray_capacity = model_creation.tray_capacity
                print(f"✅ Found tray_capacity from direct ModelMasterCreation lookup: {tray_capacity}")
                return JsonResponse({
                    'success': True, 
                    'tray_capacity': tray_capacity,
                    'source': 'Direct ModelMasterCreation lookup'
                })
        except Exception as e:
            print(f"⚠️ Direct ModelMasterCreation lookup failed: {e}")
        
        # ✅ METHOD 3: Get from any existing TrayId for this lot
        tray_objects = TrayId.objects.filter(lot_id=lot_id).exclude(rejected_tray=True)
        if tray_objects.exists():
            for tray in tray_objects:
                if hasattr(tray, 'tray_capacity') and tray.tray_capacity and tray.tray_capacity > 0:
                    print(f"✅ Found tray_capacity from TrayId: {tray.tray_capacity}")
                    return JsonResponse({
                        'success': True, 
                        'tray_capacity': tray.tray_capacity,
                        'source': 'TrayId.tray_capacity'
                    })
        
        # ✅ METHOD 4: Debug - Show all available data
        print(f"❌ Could not find tray capacity. Debug info:")
        if total_stock:
            print(f"   - TotalStockModel exists: batch_id = {getattr(total_stock.batch_id, 'batch_id', 'None') if total_stock.batch_id else 'None'}")
            if total_stock.batch_id:
                print(f"   - ModelMasterCreation tray_capacity = {getattr(total_stock.batch_id, 'tray_capacity', 'None')}")
        
        # Show available ModelMasterCreation records
        all_mmc = ModelMasterCreation.objects.filter(lot_id=lot_id)
        print(f"   - ModelMasterCreation count for lot_id {lot_id}: {all_mmc.count()}")
        for mmc in all_mmc:
            print(f"     - batch_id: {mmc.batch_id}, tray_capacity: {getattr(mmc, 'tray_capacity', 'None')}")
                
        return JsonResponse({
            'success': False, 
            'error': f'No tray capacity found for lot_id: {lot_id}',
            'debug_info': {
                'lot_id': lot_id,
                'total_stock_exists': bool(total_stock),
                'model_creation_count': all_mmc.count()
            }
        })
        
    except Exception as e:
        print(f"❌ [brass_get_tray_capacity_for_lot] Error: {e}")
        import traceback
        traceback.print_exc()
        return JsonResponse({'success': False, 'error': str(e)})
    
@method_decorator(csrf_exempt, name='dispatch')
class BQDeleteBatchAPIView(APIView):
    def post(self, request):
        try:
            data = request.data if hasattr(request, 'data') else json.loads(request.body.decode('utf-8'))
            stock_lot_id = data.get('lot_id')
            print(f"🔍 [BQDeleteBatchAPIView] Deleting stock lot with ID: {stock_lot_id}")
            if not stock_lot_id:
                return JsonResponse({'success': False, 'error': 'Missing stock_lot_id'}, status=400)
            obj = TotalStockModel.objects.filter(lot_id=stock_lot_id).first()
            if not obj:
                return JsonResponse({'success': False, 'error': 'Stock lot not found'}, status=404)
            obj.delete()
            return JsonResponse({'success': True, 'message': 'Stock lot deleted'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)

class BAValidateTrayIdAPIView(APIView):
    def get(self, request):
        tray_id = request.GET.get('tray_id')
        lot_id = request.GET.get('lot_id')
        exists = BrassAuditTrayId.objects.filter(tray_id=tray_id, lot_id=lot_id).exists()
        return Response({
            'exists': exists,
            'valid_for_lot': exists
        })
        
class BATrayDelinkTopTrayCalcAPIView(APIView):
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
            trays = BrassAuditTrayId.objects.filter(
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
            print(f"Error in BATrayDelinkTopTrayCalcAPIView: {str(e)}")

            return Response({
                'success': False,
                'error': 'Internal server error occurred while calculating delink requirements'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(login_required, name='dispatch')
class BATrayDelinkAndTopTrayUpdateAPIView(APIView):
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
                
                # BrassTrayId - Remove from lot completely
                brass_delink_tray_obj = BrassAuditTrayId.objects.filter(tray_id=delink_tray_id, lot_id=lot_id).first()
                if brass_delink_tray_obj:
                    brass_delink_tray_obj.delink_tray = True
                    brass_delink_tray_obj.lot_id = None
                    brass_delink_tray_obj.batch_id = None
                    brass_delink_tray_obj.IP_tray_verified = False
                    brass_delink_tray_obj.top_tray = False
                    brass_delink_tray_obj.save(update_fields=[
                        'delink_tray', 'lot_id', 'batch_id', 'IP_tray_verified', 'top_tray'
                    ])
                    print(f"✅ Delinked BrassTrayId tray: {delink_tray_id}")
    
                # IPTrayId - Mark as delinked
                ip_delink_tray_obj = IPTrayId.objects.filter(tray_id=delink_tray_id, lot_id=lot_id).first()
                if ip_delink_tray_obj:
                    ip_delink_tray_obj.delink_tray = True
                    ip_delink_tray_obj.save(update_fields=['delink_tray'])
                    print(f"✅ Delinked IPTrayId tray: {delink_tray_id} for lot: {lot_id}")
                
                # DPTrayId_History - Mark as delinked
                dp_history_tray_obj = DPTrayId_History.objects.filter(tray_id=delink_tray_id, lot_id=lot_id).first()
                if dp_history_tray_obj:
                    dp_history_tray_obj.delink_tray = True
                    dp_history_tray_obj.save(update_fields=['delink_tray'])
                    print(f"✅ Delinked DPTrayId_History tray: {delink_tray_id} for lot: {lot_id}")
                                # IPTrayId - Mark as delinked
                bq_delink_tray_obj = BrassTrayId.objects.filter(tray_id=delink_tray_id, lot_id=lot_id).first()
                if bq_delink_tray_obj:
                    bq_delink_tray_obj.delink_tray = True
                    bq_delink_tray_obj.save(update_fields=['delink_tray'])
                    print(f"✅ Delinked BrassTrayId tray: {delink_tray_id} for lot: {lot_id}")

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

                # ✅ FIXED: First, reset ALL trays' top_tray flag to False for this lot
                BrassAuditTrayId.objects.filter(lot_id=lot_id, top_tray=True).update(top_tray=False)
                print(f"✅ Reset all previous top_tray flags to False for lot: {lot_id}")

                # Update BrassAuditTrayId for new top tray
                top_tray_obj = BrassAuditTrayId.objects.filter(tray_id=top_tray_id, lot_id=lot_id).first()
                if top_tray_obj:
                    top_tray_obj.top_tray = True
                    top_tray_obj.tray_quantity = int(top_tray_qty)
                    top_tray_obj.delink_tray = False  # Ensure it's not marked as delink
                    top_tray_obj.save(update_fields=['top_tray', 'tray_quantity', 'delink_tray'])
                    print(f"✅ Updated BrassAuditTrayId top tray: {top_tray_id} to qty: {top_tray_qty}")

            # 3. Reset other trays (not delinked or top tray) to full capacity
            other_trays_brass = BrassAuditTrayId.objects.filter(
                lot_id=lot_id
            ).exclude(
                tray_id__in=delink_tray_ids + ([top_tray_id] if top_tray_id else [])
            )
            
            other_trays_count = 0
            for tray in other_trays_brass:
                print(f"[OTHER TRAY] Resetting BrassTrayId {tray.tray_id} to full capacity: {tray.tray_capacity}")
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

        
@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(login_required, name='dispatch')
class Brass_Audit_Accepted_form(APIView):

    def post(self, request, format=None):
        data = request.data
        lot_id = data.get("stock_lot_id")
        try:
            total_stock_data = TotalStockModel.objects.get(lot_id=lot_id)

                
            total_stock_data.brass_audit_accptance = True
    
            # Use brass_audit_physical_qty if set and > 0, else use total_stock
            physical_qty = total_stock_data.brass_audit_physical_qty

            total_stock_data.brass_audit_accepted_qty = physical_qty
            total_stock_data.send_brass_qc = False
            total_stock_data.send_brass_audit_to_iqf = True
            total_stock_data.total_stock = physical_qty

            # Update process modules
            total_stock_data.next_process_module = "Jig Loading"
            total_stock_data.last_process_module = "Brass Audit"
            total_stock_data.brass_audit_last_process_date_time = timezone.now()  # Set the last process date/time
            
            total_stock_data.save()
            
            # Create JigLoadTrayId records for all accepted trays
            accepted_trays = BrassAuditTrayId.objects.filter(
                lot_id=lot_id,
                rejected_tray=False,
                delink_tray=False
            )
            
            for tray in accepted_trays:
                # Check if already created to avoid duplicates
                if not JigLoadTrayId.objects.filter(tray_id=tray.tray_id, lot_id=lot_id).exists():
                    JigLoadTrayId.objects.create(
                        tray_id=tray.tray_id,
                        lot_id=lot_id,
                        batch_id=tray.batch_id,
                        tray_quantity=tray.tray_quantity,
                        tray_capacity=tray.tray_capacity,
                        tray_type=tray.tray_type,
                        top_tray=tray.top_tray,
                        IP_tray_verified=tray.IP_tray_verified,
                        new_tray=tray.new_tray,
                        delink_tray=tray.delink_tray,
                        rejected_tray=tray.rejected_tray,
                        user=request.user,
                        date=timezone.now()
                    )
            return Response({"success": True})
        
        except TotalStockModel.DoesNotExist:
            return Response(
                {"success": False, "error": "Stock not found."},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {"success": False, "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

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


def transfer_brass_audit_rejections_to_brass_qc(lot_id, user, batch_rejection=False, lot_comment=None):
    """
    Transfer Brass Audit rejection records into Brass QC models so QC Tray Scan
    and QC pick logic will pick up rejected trays and reason stores.
    Safe and idempotent: existing QC records are not duplicated.
    """
    try:
        # Get all Brass Audit rejected tray scans for this lot
        audit_rejections = Brass_Audit_Rejected_TrayScan.objects.filter(lot_id=lot_id).order_by('id')
        if not audit_rejections.exists():
            print(f"🔍 [transfer] No audit rejections to transfer for {lot_id}")
            return

        # Map or create QC rejection reasons
        qc_reason_objs = {}
        for a in audit_rejections:
            text = a.rejection_reason.rejection_reason if a.rejection_reason else None
            if not text:
                continue
            qc_reason = Brass_QC_Rejection_Table.objects.filter(rejection_reason=text).first()
            if not qc_reason:
                qc_reason = Brass_QC_Rejection_Table.objects.create(rejection_reason=text)
            qc_reason_objs[text] = qc_reason

        # Create QC rejected tray scan records (avoid duplicates)
        total_qty = 0
        for a in audit_rejections:
            qty = int(a.rejected_tray_quantity or 0)
            total_qty += qty
            tray_id = a.rejected_tray_id
            reason_text = a.rejection_reason.rejection_reason if a.rejection_reason else None
            qc_reason = qc_reason_objs.get(reason_text) if reason_text else None

            exists = Brass_QC_Rejected_TrayScan.objects.filter(
                lot_id=lot_id,
                rejected_tray_id=tray_id,
                rejected_tray_quantity=a.rejected_tray_quantity,
                rejection_reason=qc_reason
            ).exists()
            if not exists:
                Brass_QC_Rejected_TrayScan.objects.create(
                    lot_id=lot_id,
                    rejected_tray_quantity=a.rejected_tray_quantity,
                    rejected_tray_id=tray_id,
                    rejection_reason=qc_reason,
                    user=user
                )
                print(f"🔁 [transfer] Created QC rejected tray for {lot_id}: tray={tray_id}, qty={qty}, reason={reason_text}")

        # Create or update Brass_QC_Rejection_ReasonStore for the lot
        qc_store = Brass_QC_Rejection_ReasonStore.objects.filter(lot_id=lot_id).first()
        if qc_store:
            qc_store.total_rejection_quantity = total_qty
            if lot_comment:
                qc_store.lot_rejected_comment = lot_comment
            qc_store.batch_rejection = qc_store.batch_rejection or bool(batch_rejection)
            qc_store.save()
            if qc_reason_objs:
                qc_store.rejection_reason.add(*[r for r in qc_reason_objs.values()])
            print(f"🔁 [transfer] Updated QC rejection store for {lot_id} (total={total_qty})")
        else:
            qc_store = Brass_QC_Rejection_ReasonStore.objects.create(
                lot_id=lot_id,
                user=user,
                total_rejection_quantity=total_qty,
                batch_rejection=batch_rejection,
                lot_rejected_comment=lot_comment
            )
            if qc_reason_objs:
                qc_store.rejection_reason.set([r for r in qc_reason_objs.values()])
            print(f"🔁 [transfer] Created QC rejection store for {lot_id} (total={total_qty})")

        # Transfer accepted trays from BrassAuditTrayId to BrassTrayId
        # Sort by tray_quantity ascending and mark the smallest as top_tray
        accepted_trays = BrassAuditTrayId.objects.filter(
            lot_id=lot_id,
            rejected_tray=False
        ).order_by('tray_quantity')

        if accepted_trays.exists():
            # ✅ FIXED: Clean up existing Brass QC records for this lot to prevent duplicates
            # Step 1: Delete by lot_id
            deleted_by_lot = BrassTrayId.objects.filter(lot_id=lot_id).delete()
            print(f"   🗑️ Deleted {deleted_by_lot[0]} existing BrassTrayId records for lot_id: {lot_id}")
            
            # Step 2: Delete duplicate tray_ids from other lots
            tray_ids_to_transfer = [t.tray_id for t in accepted_trays]
            deleted_by_tray = BrassTrayId.objects.filter(tray_id__in=tray_ids_to_transfer).delete()
            print(f"   🗑️ Deleted {deleted_by_tray[0]} duplicate tray_id records from BrassTrayId")
            
            # Step 3: Also clean up Brass_Qc_Accepted_TrayID_Store to prevent conflicts
            from Brass_QC.models import Brass_Qc_Accepted_TrayID_Store
            deleted_accepted = Brass_Qc_Accepted_TrayID_Store.objects.filter(
                lot_id=lot_id
            ).delete()
            deleted_accepted_tray = Brass_Qc_Accepted_TrayID_Store.objects.filter(
                tray_id__in=tray_ids_to_transfer
            ).delete()
            print(f"   🗑️ Deleted {deleted_accepted[0] + deleted_accepted_tray[0]} Brass_Qc_Accepted_TrayID_Store records")
            
            # Get the batch_id for the lot
            total_stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
            batch_id = total_stock.batch_id if total_stock else None

            for idx, tray in enumerate(accepted_trays):
                # Set top_tray for the first (smallest quantity) tray
                is_top_tray = (idx == 0)

                # ✅ FIXED: Use update_or_create for atomic operation
                brass_tray, created = BrassTrayId.objects.update_or_create(
                    tray_id=tray.tray_id,  # Unique identifier
                    defaults={
                        'lot_id': lot_id,
                        'batch_id': batch_id,
                        'tray_quantity': tray.tray_quantity,
                        'tray_capacity': tray.tray_capacity,
                        'tray_type': tray.tray_type,
                        'top_tray': is_top_tray,
                        'IP_tray_verified': True,
                        'new_tray': tray.new_tray,
                        'delink_tray': False,
                        'rejected_tray': False,
                        'user': user,
                        'date': timezone.now()
                    }
                )
                action = "Created" if created else "Updated"
                print(f"🔁 [transfer] {action} BrassTrayId for {lot_id}: tray={tray.tray_id}, qty={tray.tray_quantity}, top_tray={is_top_tray}")

    except Exception as e:
        print(f"⚠️ [transfer_brass_audit_rejections_to_brass_qc] Error: {e}")
        traceback.print_exc()

@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(login_required, name='dispatch')
class BAuditBatchRejectionAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            data = request.data if hasattr(request, 'data') else json.loads(request.body.decode('utf-8'))
            batch_id = data.get('batch_id')
            lot_id = data.get('lot_id')  # <-- get lot_id from POST
            total_qty = data.get('total_qty', 0)
            lot_rejected_comment = data.get('lot_rejected_comment', '').strip()  # <-- NEW: Get lot rejection remarks

            # Validate required fields
            if not batch_id or not lot_id:
                return Response({'success': False, 'error': 'Missing batch_id or lot_id'}, status=400)
            
            # ✅ NEW: Validate lot rejection remarks (required for batch rejection)
            if not lot_rejected_comment:
                return Response({'success': False, 'error': 'Lot rejection remarks are required for batch rejection'}, status=400)

            # Get ModelMasterCreation by batch_id string
            mmc = ModelMasterCreation.objects.filter(batch_id=batch_id).first()
            if not mmc:
                return Response({'success': False, 'error': 'Batch not found'}, status=404)

            # Get TotalStockModel using lot_id (not batch_id)
            total_stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
            if not total_stock:
                return Response({'success': False, 'error': 'TotalStockModel not found'}, status=404)

            # Get brass_audit_physical_qty if set and > 0, else use total_stock
            qty = total_stock.brass_audit_physical_qty 
        

            # Set brass_audit_rejection = True
            total_stock.brass_audit_rejection = True
            total_stock.last_process_module = "Brass Audit"
            total_stock.next_process_module = "Jig loading"
            total_stock.send_brass_qc=False
            total_stock.send_brass_audit_to_qc = False
            total_stock.brass_audit_last_process_date_time = timezone.now()  # Set the last process date/time
            total_stock.save(update_fields=['brass_audit_rejection', 'last_process_module', 'next_process_module', 'brass_audit_last_process_date_time', 'send_brass_audit_to_qc','send_brass_qc'])

            updated_trays_count = BrassAuditTrayId.objects.filter(lot_id=lot_id).update(rejected_tray=True)

            # ✅ UPDATED: Create Brass_Audit_Rejection_ReasonStore entry with lot rejection remarks
            Brass_Audit_Rejection_ReasonStore.objects.create(
                lot_id=lot_id,
                user=request.user,
                total_rejection_quantity=qty,
                batch_rejection=True,
                lot_rejected_comment=lot_rejected_comment  # <-- NEW: Save lot rejection remarks
            )

            # Transfer batch-level audit rejection to Brass QC for pick/tray-scan visibility
            try:
                # ✅ IMPROVED: Use the new reverse transfer function that reuses existing trays
                # Call the new reverse transfer function that prevents duplication
                reverse_transfer_success = send_brass_audit_back_to_brass_qc(lot_id, request.user)
                
                if reverse_transfer_success:
                    print(f"✅ [BAuditBatchRejectionAPIView] Successfully sent lot {lot_id} back to Brass QC using reverse transfer")
                    
                    # Also set the flag to ensure it appears in Brass QC pick table
                    total_stock.send_brass_audit_to_qc = True
                    total_stock.save(update_fields=['send_brass_audit_to_qc'])
                else:
                    print(f"⚠️ [BAuditBatchRejectionAPIView] Reverse transfer failed, falling back to original transfer method")
                    # Fallback to the original transfer method if needed
                    transfer_brass_audit_rejections_to_brass_qc(lot_id, request.user, batch_rejection=True, lot_comment=lot_rejected_comment)
                    
            except Exception as e:
                print(f"⚠️ [BAuditBatchRejectionAPIView] Failed to transfer batch rejection to Brass QC: {e}")
                traceback.print_exc()
                # Fallback to original method
                transfer_brass_audit_rejections_to_brass_qc(lot_id, request.user, batch_rejection=True, lot_comment=lot_rejected_comment)
            
            # Set send_brass_qc=True to send accepted trays back to Brass QC
            total_stock.send_brass_qc = True
            total_stock.last_process_date_time = timezone.now()
            total_stock.save(update_fields=['send_brass_qc', 'last_process_date_time'])
            
                        # ✅ NEW: Create new TotalStockModel instance for next process
            new_lot_id = generate_new_lot_id()
            new_total_stock = TotalStockModel.objects.create(
                lot_id=new_lot_id,
                model_stock_no=total_stock.model_stock_no,
                batch_id=total_stock.batch_id,
                version=total_stock.version,
                total_stock=total_stock.total_stock,
                total_IP_accpeted_quantity=total_stock.brass_audit_physical_qty,
                polish_finish=total_stock.polish_finish,
                plating_color=total_stock.plating_color,
                created_at=total_stock.created_at,
                send_brass_audit_to_qc=True,
                iqf_acceptance=False,  # Ensure IQF flag is cleared
                send_brass_qc=False,  # Ensure Brass QC flag is cleared
                remove_lot=True,
                tray_scan_status=True,
                ip_person_qty_verified=True,
                last_process_module="Brass Audit",
                brass_audit_last_process_date_time=timezone.now(),
                last_process_date_time=timezone.now()
            )
            print(f"✅ Created new TotalStockModel for next process: {new_lot_id}")

                        # ✅ FIX BUG 2: Create BrassTrayId records for the new lot (not BrassAuditTrayId)
            # Get all original trays from the rejected lot
            from Brass_QC.models import BrassTrayId
            original_trays = BrassTrayId.objects.filter(lot_id=lot_id)

            # Create BrassTrayId records for the new lot to enable proper tray scanning in Brass QC
            for tray in original_trays:
                BrassTrayId.objects.create(
                    tray_id=tray.tray_id,
                    lot_id=new_lot_id,
                    batch_id=tray.batch_id,
                    tray_quantity=tray.tray_quantity,
                    tray_capacity=tray.tray_capacity,
                    tray_type=tray.tray_type,
                    top_tray=tray.top_tray,
                    rejected_tray=False,  # Reset rejection flag for new lot
                    delink_tray=False,
                    IP_tray_verified=True,
                    new_tray=False,
                    user=request.user,
                    date=timezone.now()
                )

            return Response({'success': True, 'message': 'Batch rejection saved with remarks.'})

        except Exception as e:
            return Response({'success': False, 'error': str(e)}, status=500)


@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(login_required, name='dispatch')
class BrassAudit_TrayRejectionAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            data = request.data if hasattr(request, 'data') else json.loads(request.body.decode('utf-8'))
            lot_id = data.get('lot_id')
            batch_id = data.get('batch_id')
            tray_rejections = data.get('tray_rejections', [])  # List of {reason_id, qty, tray_id}

            print(f"🔍 [BrassAudit_TrayRejectionAPIView] Received tray_rejections: {tray_rejections}")
            print(f"🔍 [BrassAudit_TrayRejectionAPIView] Lot ID: {lot_id}, Batch ID: {batch_id}")

            if not lot_id or not tray_rejections:
                return Response({'success': False, 'error': 'Missing lot_id or tray_rejections'}, status=400)

            # Get the TotalStockModel for this lot_id
            total_stock_obj = TotalStockModel.objects.filter(lot_id=lot_id).first()
            if not total_stock_obj:
                return Response({'success': False, 'error': 'TotalStockModel not found'}, status=404)

            # Use brass_audit_physical_qty if set and > 0, else use brass_audit_accepted_qty
            available_qty = total_stock_obj.brass_audit_physical_qty if total_stock_obj and total_stock_obj.brass_audit_physical_qty else 0
            
            running_total = 0
            for idx, item in enumerate(tray_rejections):
                qty = int(item.get('qty', 0))
                running_total += qty
                if running_total > available_qty:
                    return Response({
                        'success': False,
                        'error': f'Quantity exceeds available ({available_qty}).'
                    }, status=400)

            # Process each tray rejection
            total_qty = 0
            saved_rejections = []
            reason_ids_used = set()
            
            for idx, item in enumerate(tray_rejections):
                tray_id = item.get('tray_id', '').strip()
                reason_id = item.get('reason_id', '').strip()
                qty = int(item.get('qty', 0))
                
                if qty <= 0 or not tray_id or not reason_id:
                    continue
                
                try:
                    reason_obj = Brass_Audit_Rejection_Table.objects.get(rejection_reason_id=reason_id)
                    rejection_record = Brass_Audit_Rejected_TrayScan.objects.create(
                        lot_id=lot_id,
                        rejected_tray_quantity=qty,
                        rejection_reason=reason_obj,
                        user=request.user,
                        rejected_tray_id=tray_id
                    )
                    saved_rejections.append({
                        'record_id': rejection_record.id,
                        'tray_id': tray_id,
                        'qty': qty,
                        'reason': reason_obj.rejection_reason,
                        'reason_id': reason_id
                    })
                    total_qty += qty
                    reason_ids_used.add(reason_id)
                except Brass_Audit_Rejection_Table.DoesNotExist:
                    return Response({
                        'success': False,
                        'error': f'Rejection reason {reason_id} not found'
                    }, status=400)
                except Exception as e:
                    return Response({
                        'success': False,
                        'error': f'Error creating rejection record: {str(e)}'
                    }, status=500)

            if not saved_rejections:
                return Response({
                    'success': False,
                    'error': 'No valid rejections were processed'
                }, status=400)
            
            # Create ONE summary record for the lot (with all unique rejection reasons)
            if reason_ids_used:
                reasons = Brass_Audit_Rejection_Table.objects.filter(rejection_reason_id__in=list(reason_ids_used))
                reason_store = Brass_Audit_Rejection_ReasonStore.objects.create(
                    lot_id=lot_id,
                    user=request.user,
                    total_rejection_quantity=total_qty,
                    batch_rejection=False
                )
                reason_store.rejection_reason.set(reasons)

            # Update TrayId and BrassAuditTrayId records for ALL individual tray IDs
            unique_tray_ids = list(set([item['tray_id'] for item in saved_rejections]))
            for tray_id in unique_tray_ids:
                tray_obj = TrayId.objects.filter(tray_id=tray_id).first()
                if tray_obj:
                    tray_total_qty = sum([item['qty'] for item in saved_rejections if item['tray_id'] == tray_id])
                    is_new_tray = getattr(tray_obj, 'new_tray', False)
                    if is_new_tray:
                        tray_obj.lot_id = lot_id
                        tray_obj.rejected_tray = True
                        mmc = ModelMasterCreation.objects.filter(batch_id=batch_id).first()
                        tray_obj.batch_id = mmc
                        tray_obj.top_tray = False
                        tray_obj.tray_quantity = tray_total_qty
                        tray_obj.save(update_fields=['lot_id', 'rejected_tray','batch_id','top_tray', 'tray_quantity'])
                    else:
                        tray_obj.rejected_tray = True
                        tray_obj.top_tray = False
                        tray_obj.tray_quantity = tray_total_qty
                        tray_obj.save(update_fields=['rejected_tray', 'top_tray', 'tray_quantity'])

                    # Sync BrassAuditTrayId table for this tray_id and lot_id
                    brass_tray_obj = BrassAuditTrayId.objects.filter(tray_id=tray_id, lot_id=lot_id).first()
                    if brass_tray_obj:
                        brass_tray_obj.tray_quantity = tray_total_qty
                        brass_tray_obj.rejected_tray = True
                        brass_tray_obj.top_tray = False
                        brass_tray_obj.save(update_fields=['tray_quantity', 'rejected_tray', 'top_tray'])
                    else:
                        BrassAuditTrayId.objects.create(
                            tray_id=tray_id,
                            lot_id=lot_id,
                            batch_id=tray_obj.batch_id if hasattr(tray_obj, 'batch_id') else None,
                            tray_quantity=tray_total_qty,
                            rejected_tray=True,
                            top_tray=False,
                            tray_type=getattr(tray_obj, 'tray_type', None),
                            tray_capacity=getattr(tray_obj, 'tray_capacity', None),
                            IP_tray_verified=False,
                            new_tray=is_new_tray,
                            delink_tray=False,
                            user=request.user if hasattr(request, 'user') else None,
                            date=timezone.now()
                        )

            # Decide status based on rejection qty vs physical qty
            if total_qty >= available_qty:
                delink_needed = self.check_delink_required(lot_id, available_qty)
                if delink_needed:
                    total_stock_obj.brass_audit_rejection = True
                    total_stock_obj.brass_audit_onhold_picking = True
                    total_stock_obj.brass_audit_few_cases_accptance = False
                    update_fields = ['brass_audit_rejection', 'brass_audit_onhold_picking', 'brass_audit_few_cases_accptance']
                else:
                    total_stock_obj.brass_audit_rejection = True
                    total_stock_obj.brass_audit_onhold_picking = False
                    total_stock_obj.brass_audit_few_cases_accptance = False
                    update_fields = ['brass_audit_rejection', 'brass_audit_onhold_picking', 'brass_audit_few_cases_accptance']
            else:
                total_stock_obj.brass_audit_onhold_picking = True
                total_stock_obj.brass_audit_few_cases_accptance = True
                total_stock_obj.brass_audit_rejection = False
                update_fields = ['brass_audit_few_cases_accptance', 'brass_audit_onhold_picking', 'brass_audit_rejection']
            
            total_stock_obj.brass_audit_accepted_qty = available_qty - total_qty
            total_stock_obj.brass_audit_last_process_date_time = timezone.now()
            total_stock_obj.save(update_fields=update_fields + ['brass_audit_accepted_qty', 'brass_audit_last_process_date_time'])

            # Transfer audit rejections to Brass QC so QC Tray Scan reflects the rejections
            try:
                transfer_brass_audit_rejections_to_brass_qc(lot_id, request.user, batch_rejection=False)
            except Exception as e:
                print(f"⚠️ [BrassAudit_TrayRejectionAPIView] Failed to transfer rejections to Brass QC: {e}")
                traceback.print_exc()
            
            # If lot is rejected, send accepted trays back to Brass QC
            if total_stock_obj.brass_audit_rejection:
                total_stock_obj.send_brass_qc = True
                total_stock_obj.last_process_date_time = timezone.now()
            else:
                total_stock_obj.send_brass_qc = False
            
            total_stock_obj.send_brass_audit_to_qc = False
            total_stock_obj.send_brass_audit_to_iqf=True
            update_fields.extend(['brass_audit_accepted_qty', 'brass_audit_last_process_date_time','send_brass_audit_to_qc','send_brass_audit_to_iqf','send_brass_qc', 'last_process_date_time'])
            total_stock_obj.save(update_fields=update_fields)

            return Response({
                'success': True,
                'message': 'Tray rejections saved and new lot created with rejected trays.'
            })

        except Exception as e:
            import traceback
            traceback.print_exc()
            return Response({'success': False, 'error': str(e)}, status=500)
        
    def check_delink_required(self, lot_id, available_qty):
        """
        ✅ NEW: Check if delink trays are required after all rejections
        """
        try:
            print(f"🔍 [check_delink_required] Checking for lot_id: {lot_id}")
            
            # Get the stock for this lot
            stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
            if not stock:
                print(f"❌ [check_delink_required] No stock found for lot_id: {lot_id}")
                return False
            
            # Get original distribution
            original_distribution = get_brass_actual_tray_distribution_for_delink(lot_id, stock)
            print(f"🔍 [check_delink_required] Original distribution: {original_distribution}")
            
            if not original_distribution:
                print(f"ℹ️ [check_delink_required] No original distribution - no delink needed")
                return False
            
            # Calculate current distribution after rejections
            current_distribution = brass_calculate_distribution_after_rejections_enhanced(lot_id, original_distribution)
            print(f"🔍 [check_delink_required] Current distribution: {current_distribution}")
            
            # Check for empty trays (quantity = 0)
            empty_trays = [qty for qty in current_distribution if qty == 0]
            empty_tray_count = len(empty_trays)
            
            print(f"🔍 [check_delink_required] Empty trays found: {empty_tray_count}")
            
            # Delink is needed if there are empty trays
            delink_needed = empty_tray_count > 0
            print(f"🔍 [check_delink_required] Final result: delink_needed = {delink_needed}")
            
            return delink_needed
        
            
            
        except Exception as e:
            print(f"❌ [check_delink_required] Error: {e}")
            import traceback
            traceback.print_exc()
            return False  # Default to no delink needed on error
        
        
@require_GET
def brass_audit_reject_check_tray_id(request):
    """
    Check if tray_id exists and is valid for brass QC rejection
    Only allow:
    1. Trays with same lot_id that are verified and not rejected
    2. New trays without lot_id assignment
    """
    tray_id = request.GET.get('tray_id', '').strip()
    lot_id = request.GET.get('lot_id', '').strip()  # This is your stock_lot_id
    print(f"DEBUG: Checking tray_id={tray_id}, lot_id={lot_id}")  # Debug log
    if not tray_id:
        return JsonResponse({'exists': False, 'error': 'Tray ID is required'})
    
    try:
        # Get the tray object if it exists
        tray_obj = TrayId.objects.filter(tray_id=tray_id).first()
        
        if not tray_obj:
            return JsonResponse({
                'exists': False,
                'error': 'Tray ID not found',
                'status_message': 'Not Found'
            })

        # ✅ CHECK 1: For new trays without lot_id, show "New Tray Available"
        is_new_tray = getattr(tray_obj, 'new_tray', False) or not tray_obj.lot_id or tray_obj.lot_id == '' or tray_obj.lot_id is None
        
        if is_new_tray:
            return JsonResponse({
                'exists': True,
                'status_message': 'New Tray Available',
                'validation_type': 'new_tray'
            })

        # ✅ CHECK 2: For existing trays, must belong to same lot
        if tray_obj.lot_id:
            if str(tray_obj.lot_id).strip() != str(lot_id).strip():
                return JsonResponse({
                    'exists': False,
                    'error': 'Different lot',
                    'status_message': 'Different Lot'
                })
        else:
            # This case should be caught by CHECK 1, but just in case
            return JsonResponse({
                'exists': True,
                'status_message': 'New Tray Available',
                'validation_type': 'new_tray'
            })

        # ✅ CHECK 3: Must NOT be already rejected
        if hasattr(tray_obj, 'rejected_tray') and tray_obj.rejected_tray:
            return JsonResponse({
                'exists': False,
                'error': 'Already rejected',
                'status_message': 'Already Rejected'
            })

        # ✅ CHECK 4: Must NOT be in Brass_Audit_Rejected_TrayScan for this lot
        already_rejected_in_brass = Brass_Audit_Rejected_TrayScan.objects.filter(
            lot_id=lot_id,
            rejected_tray_id=tray_id
        ).exists()
        
        if already_rejected_in_brass:
            return JsonResponse({
                'exists': False,
                'error': 'Already rejected in Brass Audit',
                'status_message': 'Already Rejected'
            })

        # ✅ SUCCESS: Tray is valid for brass QC rejection
        return JsonResponse({
            'exists': True,
            'status_message': 'Available (can rearrange)',
            'validation_type': 'existing_valid',
            'tray_quantity': getattr(tray_obj, 'tray_quantity', 0) or 0
        })
        
    except Exception as e:
        return JsonResponse({
            'exists': False,
            'error': 'System error',
            'status_message': 'System Error'
        })

# Tray ID Allowance based on condition in rejection

@require_GET
def brass_audit_reject_check_tray_id_simple(request):
    """
    Enhanced tray validation for Brass Audit rejections with BrassAuditTrayId priority
    """
    tray_id = request.GET.get('tray_id', '')
    current_lot_id = request.GET.get('lot_id', '')
    rejection_qty = int(request.GET.get('rejection_qty', 0))
    
    print(f"[Brass Reject Validation] tray_id: {tray_id}, lot_id: {current_lot_id}, qty: {rejection_qty}")

    # Print overall qty from TotalStockModel
    total_stock_obj = TotalStockModel.objects.filter(lot_id=current_lot_id).first()
    overall_qty = total_stock_obj.brass_audit_physical_qty if total_stock_obj and total_stock_obj.brass_audit_physical_qty else 0
    print(f"[Brass Reject Validation] Overall brass_audit_physical_qty for lot {current_lot_id}: {overall_qty}")

    try:
        # ✅ STEP 1: First check BrassAuditTrayId table for this specific lot_id
        brass_tray_obj = BrassAuditTrayId.objects.filter(tray_id=tray_id, lot_id=current_lot_id).first()
        
        if brass_tray_obj:
            print(f"[Brass Reject Validation] Found in BrassAuditTrayId for lot {current_lot_id}")

            # Check if already rejected
            if brass_tray_obj.rejected_tray:
                return JsonResponse({
                    'exists': False,
                    'valid_for_rejection': False,
                    'error': 'Already rejected in Brass Audit',
                    'status_message': 'Already Rejected'
                })
            
            # Validate tray capacity and rearrangement logic for existing tray
            tray_qty = brass_tray_obj.tray_quantity or 0
            tray_capacity = brass_tray_obj.tray_capacity or 0
            remaining_in_tray = tray_qty - rejection_qty

            
            # If some pieces will remain, check if they can fit in other trays
            if remaining_in_tray > 0:
                other_trays = BrassAuditTrayId.objects.filter(
                    lot_id=current_lot_id,
                    tray_quantity__gt=0,
                    rejected_tray=False
                ).exclude(tray_id=tray_id)
                
                available_space_in_other_trays = 0
                for t in other_trays:
                    current_qty = t.tray_quantity or 0
                    max_capacity = t.tray_capacity or tray_capacity
                    available_space_in_other_trays += max(0, max_capacity - current_qty)
                
                if remaining_in_tray > available_space_in_other_trays:
                    return JsonResponse({
                        'exists': False,
                        'valid_for_rejection': False,
                        'error': f'Cannot reject: {remaining_in_tray} pieces will remain but only {available_space_in_other_trays} space available in other trays',
                        'status_message': 'Need New Tray'
                    })
            
            # Validation passed for existing tray
            return JsonResponse({
                'exists': True,
                'valid_for_rejection': True,
                'status_message': 'Available (Can Rearrange)',
                'validation_type': 'existing_tray_in_brass',
                'tray_capacity': tray_capacity,
                'current_quantity': tray_qty,
                'remaining_after_rejection': remaining_in_tray
            })
        
        # ✅ STEP 2: Not found in BrassTrayId, check TrayId for new tray availability
        print(f"[Brass Reject Validation] Not found in BrassTrayId, checking TrayId for new tray")
        
        tray_obj = TrayId.objects.filter(tray_id=tray_id).first()
        
        if not tray_obj:
            return JsonResponse({
                'exists': False,
                'valid_for_rejection': False,
                'error': 'Tray ID not found in system',
                'status_message': 'Tray Not Found'
            })

        # ✅ Check if tray is already rejected in BrassTrayId
        ip_tray_obj = BrassTrayId.objects.filter(tray_id=tray_id, lot_id=current_lot_id).first()
        if ip_tray_obj and getattr(ip_tray_obj, 'rejected_tray', False):
            return JsonResponse({
                'exists': False,
                'valid_for_rejection': False,
                'error': 'Already rejected in Input Screening',
                'status_message': 'Already Rejected in IP'
            })
        
        # ✅ Check if tray belongs to a different lot
        if tray_obj.lot_id and str(tray_obj.lot_id).strip():
            if str(tray_obj.lot_id).strip() != str(current_lot_id).strip():
                return JsonResponse({
                    'exists': False,
                    'valid_for_rejection': False,
                    'error': 'Tray belongs to different lot',
                    'status_message': 'Different Lot',
                    'debug_info': {
                        'tray_lot_id': str(tray_obj.lot_id).strip(),
                        'current_lot_id': str(current_lot_id).strip()
                    }
                })
            
            # Same lot but check if rejected
            if tray_obj.rejected_tray:
                return JsonResponse({
                    'exists': False,
                    'valid_for_rejection': False,
                    'error': 'Already rejected',
                    'status_message': 'Already Rejected'
                })
        
        # ✅ Validate tray capacity compatibility
        tray_capacity_validation = validate_brass_audit_tray_capacity_compatibility(tray_obj, current_lot_id)
        if not tray_capacity_validation['is_compatible']:
            return JsonResponse({
                'exists': False,
                'valid_for_rejection': False,
                'error': tray_capacity_validation['error'],
                'status_message': 'Wrong Tray Type',
                'tray_capacity_mismatch': True,
                'scanned_tray_capacity': tray_capacity_validation['scanned_tray_capacity'],
                'expected_tray_capacity': tray_capacity_validation['expected_tray_capacity']
            })
        
        # ✅ Check if it's a new tray (no lot_id or empty lot_id)
        is_new_tray = (not tray_obj.lot_id or str(tray_obj.lot_id).strip() == '')
        
        print(f"[Brass Reject Validation] TrayId analysis:")
        print(f"  - lot_id: '{tray_obj.lot_id}'")
        print(f"  - is_new_tray (lot_id None or empty): {is_new_tray}")
        
        if is_new_tray:
            return JsonResponse({
                'exists': True,
                'valid_for_rejection': True,
                'status_message': 'New Tray Available',
                'validation_type': 'new_tray_from_master',
                'tray_capacity_compatible': True,
                'tray_capacity': tray_obj.tray_capacity or tray_capacity_validation['expected_tray_capacity']
            })
        
        # ✅ If we reach here, tray exists in TrayId with same lot_id but not in BrassTrayId
        # This could be a valid scenario - treat as available
        return JsonResponse({
            'exists': True,
            'valid_for_rejection': True,
            'status_message': 'Available (from TrayId)',
            'validation_type': 'existing_tray_from_master',
            'tray_capacity_compatible': True,
            'tray_capacity': tray_obj.tray_capacity
        })

    except Exception as e:
        print(f"[Brass Reject Validation] Error: {str(e)}")
        traceback.print_exc()
        return JsonResponse({
            'exists': False,
            'valid_for_rejection': False,
            'error': 'System error',
            'status_message': 'System Error'
        })
        
        
# ✅ NEW: Helper function to validate tray capacity compatibility for Brass Audit
def validate_brass_audit_tray_capacity_compatibility(tray_obj, lot_id):
    """
    Validate if the scanned tray capacity matches the lot's expected tray capacity
    """
    try:
        # Get the scanned tray's capacity
        scanned_tray_capacity = getattr(tray_obj, 'tray_capacity', None)
        
        if not scanned_tray_capacity:
            # If tray doesn't have capacity info, try to get from batch
            if hasattr(tray_obj, 'batch_id') and tray_obj.batch_id:
                batch_capacity = getattr(tray_obj.batch_id, 'tray_capacity', None)
                if batch_capacity:
                    scanned_tray_capacity = batch_capacity
        
        print(f"[Brass Tray Capacity Validation] Scanned tray capacity: {scanned_tray_capacity}")
        
        # Get the expected tray capacity for the lot
        expected_tray_capacity = get_expected_tray_capacity_for_brass_lot(lot_id)
        print(f"[Brass Tray Capacity Validation] Expected tray capacity for lot {lot_id}: {expected_tray_capacity}")
        
        # If we can't determine either capacity, allow it (fallback)
        if not scanned_tray_capacity or not expected_tray_capacity:
            print(f"[Brass Tray Capacity Validation] Missing capacity info - allowing as fallback")
            return {
                'is_compatible': True,
                'scanned_tray_capacity': scanned_tray_capacity or 'Unknown',
                'expected_tray_capacity': expected_tray_capacity or 'Unknown'
            }
        
        # Compare tray capacities
        is_compatible = int(scanned_tray_capacity) == int(expected_tray_capacity)
        
        if is_compatible:
            print(f"✅ [Brass Tray Capacity Validation] Compatible: {scanned_tray_capacity} matches {expected_tray_capacity}")
            return {
                'is_compatible': True,
                'scanned_tray_capacity': scanned_tray_capacity,
                'expected_tray_capacity': expected_tray_capacity
            }
        else:
            print(f"❌ [Brass Tray Capacity Validation] Incompatible: {scanned_tray_capacity} ≠ {expected_tray_capacity}")
            return {
                'is_compatible': False,
                'error': f'Wrong Tray Type: Scanned tray capacity {scanned_tray_capacity}, but lot requires capacity {expected_tray_capacity}',
                'status_message': f'Wrong Tray Type',
                'scanned_tray_capacity': scanned_tray_capacity,
                'expected_tray_capacity': expected_tray_capacity
            }
            
    except Exception as e:
        print(f"[Brass Tray Capacity Validation] Error: {e}")
        traceback.print_exc()
        # On error, allow the tray (fallback behavior)
        return {
            'is_compatible': True,
            'scanned_tray_capacity': 'Unknown',
            'expected_tray_capacity': 'Unknown',
            'error': f'Validation error: {str(e)}'
        }


# ✅ NEW: Helper function to get expected tray capacity for a Brass Audit lot
def get_expected_tray_capacity_for_brass_lot(lot_id):
    """
    Get the expected tray capacity for a specific lot in Brass Audit
    """
    try:
        # Method 1: Get from TotalStockModel via lot_id
        total_stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
        if total_stock:
            # Check if batch_id has tray capacity info
            if hasattr(total_stock, 'batch_id') and total_stock.batch_id:
                batch_obj = total_stock.batch_id
                if hasattr(batch_obj, 'tray_capacity') and batch_obj.tray_capacity:
                    print(f"[Expected Brass Tray Capacity] Found from batch: {batch_obj.tray_capacity}")
                    return batch_obj.tray_capacity
        
        # Method 2: Get from existing BrassTrayId records for this lot
        existing_tray = BrassAuditTrayId.objects.filter(
            lot_id=lot_id, 
            rejected_tray=False,
            tray_capacity__isnull=False
        ).first()
        if existing_tray and existing_tray.tray_capacity:
            print(f"[Expected Brass Tray Capacity] Found from existing tray: {existing_tray.tray_capacity}")
            return existing_tray.tray_capacity
        
        # Method 3: Get from Brass_Audit_Accepted_TrayID_Store (if tray was processed in IP)
        ip_accepted = Brass_Audit_Accepted_TrayID_Store.objects.filter(lot_id=lot_id).first()
        if ip_accepted and ip_accepted.top_tray_id:
            ip_tray = BrassAuditTrayId.objects.filter(tray_id=ip_accepted.top_tray_id).first()
            if ip_tray and ip_tray.tray_capacity:
                print(f"[Expected Brass Tray Capacity] Found from IP accepted tray: {ip_tray.tray_capacity}")
                return ip_tray.tray_capacity
        
        print(f"[Expected Brass Tray Capacity] Could not determine expected tray capacity for lot {lot_id}")
        return None
        
    except Exception as e:
        print(f"[Expected Brass Tray Capacity] Error getting expected tray capacity: {e}")
        return None


def can_rearrange_remaining_pieces(available_quantities, original_capacities, rejection_qty, remaining_qty):
    """
    ENHANCED: Progressive validation for multiple rejection rows
    - Each row validates against the current state after previous rejections
    - Takes into account the cumulative effect of session allocations
    """
    try:
        print(f"[Progressive Rearrangement Check] Input:")
        print(f"  Available quantities: {available_quantities}")
        print(f"  Original capacities: {original_capacities}")
        print(f"  Current rejection qty: {rejection_qty}")
        print(f"  Remaining qty after this rejection: {remaining_qty}")
        
        if remaining_qty == 0:
            return {'success': True, 'message': 'no pieces left', 'plan': []}
        
        # ✅ STEP 1: Check if we have enough quantity for this specific rejection
        total_current_qty = sum(available_quantities)
        if rejection_qty > total_current_qty:
            return {
                'success': False,
                'message': f'insufficient quantity: need {rejection_qty}, have {total_current_qty}',
                'plan': []
            }
        
        # ✅ STEP 2: Simulate this specific rejection
        temp_quantities = available_quantities.copy()
        temp_remaining_to_reject = rejection_qty
        
        # Consume rejection quantity from largest trays first
        sorted_indices = sorted(range(len(temp_quantities)), key=lambda i: temp_quantities[i], reverse=True)
        
        consumed_from_trays = []
        
        for i in sorted_indices:
            if temp_remaining_to_reject <= 0:
                break
            current_qty = temp_quantities[i]
            if current_qty > 0:
                consume_from_this_tray = min(temp_remaining_to_reject, current_qty)
                temp_quantities[i] -= consume_from_this_tray
                temp_remaining_to_reject -= consume_from_this_tray
                consumed_from_trays.append({
                    'tray_index': i,
                    'consumed_qty': consume_from_this_tray,
                    'remaining_in_tray': temp_quantities[i],
                    'tray_capacity': original_capacities[i] if i < len(original_capacities) else 0
                })
                print(f"  Consumed {consume_from_this_tray} from tray {i}, remaining: {temp_quantities[i]}")
        
        print(f"  After this rejection: {temp_quantities}")
        
        # ✅ STEP 3: Check if partial pieces can be accommodated
        for consumption in consumed_from_trays:
            tray_index = consumption['tray_index']
            remaining_in_tray = consumption['remaining_in_tray']
            tray_capacity = consumption['tray_capacity']
            
            # If we partially emptied a tray, check if remaining pieces can be moved
            if remaining_in_tray > 0 and remaining_in_tray < tray_capacity:
                # Check available space in other trays after this rejection
                available_space_in_other_trays = 0
                for j, qty in enumerate(temp_quantities):
                    if j != tray_index and j < len(original_capacities):
                        capacity = original_capacities[j]
                        available_space = capacity - qty
                        available_space_in_other_trays += max(0, available_space)
                # If partial pieces can't fit in other trays, reject this rejection
                if remaining_in_tray > available_space_in_other_trays:
                    return {
                        'success': False,
                        'message': f'partial {remaining_in_tray} pieces from tray {tray_index} cannot fit in other trays (only {available_space_in_other_trays} space)',
                        'plan': []
                    }
        
        # ✅ STEP 4: Calculate optimal final distribution
        total_remaining = sum(temp_quantities)
        final_distribution = [0] * len(temp_quantities)
        remaining_to_distribute = total_remaining
        
        # Fill trays optimally (largest capacity first)
        capacity_priority = []
        for i in range(len(temp_quantities)):
            if i < len(original_capacities):
                capacity = original_capacities[i]
                capacity_priority.append((capacity, i))
        
        capacity_priority.sort(reverse=True)
        
        for capacity, idx in capacity_priority:
            if remaining_to_distribute <= 0:
                break
            
            fill_amount = min(remaining_to_distribute, capacity)
            final_distribution[idx] = fill_amount
            remaining_to_distribute -= fill_amount
            print(f"  Final distribution: Put {fill_amount} in tray {idx} (capacity {capacity})")
        
        print(f"  Final distribution: {final_distribution}")
        print(f"  Remaining undistributed: {remaining_to_distribute}")
        
        if remaining_to_distribute == 0:
            return {
                'success': True,
                'message': f'can rearrange to {final_distribution}',
                'plan': final_distribution
            }
        else:
            return {
                'success': False,
                'message': f'cannot fit {remaining_to_distribute} pieces after rearrangement',
                'plan': []
            }
            
    except Exception as e:
        print(f"[Progressive Rearrangement Check] Error: {e}")
        return {'success': False, 'message': 'rearrangement check failed', 'plan': []}

def get_brass_available_quantities_with_session_allocations(lot_id, current_session_allocations):
    """
    Calculate available tray quantities and ACTUAL free space for Brass Audit
    """
    try:
        # Get original distribution and track free space separately
        original_distribution = get_brass_original_tray_distribution(lot_id)
        original_capacities = get_brass_tray_capacities_for_lot(lot_id)
        
        available_quantities = original_distribution.copy()
        new_tray_usage_count = 0  # Track NEW tray usage for free space calculation
        
        print(f"[Brass Session Validation] Starting with: {available_quantities}")
        
        # First, apply saved rejections
        saved_rejections = Brass_Audit_Rejected_TrayScan.objects.filter(lot_id=lot_id).order_by('id')
        
        for rejection in saved_rejections:
            rejected_qty = rejection.rejected_tray_quantity or 0
            tray_id = rejection.rejected_tray_id
            
            if rejected_qty <= 0:
                continue
                
            if tray_id and is_new_tray_by_id(tray_id):
                # NEW tray creates actual free space
                new_tray_usage_count += 1
                available_quantities = brass_reduce_quantities_optimally(available_quantities, rejected_qty, is_new_tray=True)
                print(f"[Brass Session Validation] NEW tray saved rejection: freed up {rejected_qty} space")
            else:
                # EXISTING tray just consumes available quantities
                available_quantities = brass_reduce_quantities_optimally(available_quantities, rejected_qty, is_new_tray=False)
                print(f"[Brass Session Validation] EXISTING tray saved rejection: removed tray")
        
        # Then, apply current session allocations
        for allocation in current_session_allocations:
            try:
                reason_text = allocation.get('reason_text', '')
                qty = int(allocation.get('qty', 0))
                tray_ids = allocation.get('tray_ids', [])
                
                if qty <= 0:
                    continue
                
                # Check if NEW tray was used by looking at tray_ids
                is_new_tray_used = False
                if tray_ids:
                    for tray_id in tray_ids:
                        if tray_id and is_new_tray_by_id(tray_id):
                            is_new_tray_used = True
                            break
                
                if is_new_tray_used:
                    new_tray_usage_count += 1
                    available_quantities = brass_reduce_quantities_optimally(available_quantities, qty, is_new_tray=True)
                    print(f"[Brass Session Validation] NEW tray session: freed up {qty} space using tray {tray_ids}")
                else:
                    available_quantities = brass_reduce_quantities_optimally(available_quantities, qty, is_new_tray=False)
                    print(f"[Brass Session Validation] EXISTING tray session: removed tray")
            except Exception as e:
                print(f"[Brass Session Validation] Error processing allocation: {e}")
                continue
        
        # Calculate ACTUAL current free space
        actual_free_space = 0
        if len(available_quantities) <= len(original_capacities):
            for i, qty in enumerate(available_quantities):
                if i < len(original_capacities):
                    capacity = original_capacities[i]
                    actual_free_space += max(0, capacity - qty)
        
        # Calculate totals
        total_available = sum(available_quantities)
        total_capacity = sum(original_capacities[:len(available_quantities)])  # Only count current trays
        
        print(f"[Brass Session Validation] FINAL:")
        print(f"  Available quantities: {available_quantities}")
        print(f"  Total available: {total_available}")
        print(f"  Total capacity of current trays: {total_capacity}")
        print(f"  ACTUAL free space in current trays: {actual_free_space}")
        print(f"  NEW tray usage count: {new_tray_usage_count}")
        
        return available_quantities, actual_free_space
        
    except Exception as e:
        print(f"[Brass Session Validation] Error: {e}")
        return get_brass_original_tray_distribution(lot_id), 0

def brass_reduce_quantities_optimally(available_quantities, qty_to_reduce, is_new_tray=True):
    """
    Reduce quantities optimally for Brass Audit with enhanced logic
    """
    quantities = available_quantities.copy()
    remaining = qty_to_reduce

    if is_new_tray:
        # NEW tray usage should FREE UP space from existing trays
        print(f"[brass_reduce_quantities_optimally] NEW tray: freeing up {qty_to_reduce} space")
        
        # Free up space from smallest trays first (to create empty trays)
        sorted_indices = sorted(range(len(quantities)), key=lambda i: quantities[i])
        for i in sorted_indices:
            if remaining <= 0:
                break
            current_qty = quantities[i]
            if current_qty >= remaining:
                quantities[i] = current_qty - remaining
                print(f"  Freed {remaining} from tray {i}, new qty: {quantities[i]}")
                remaining = 0
            elif current_qty > 0:
                remaining -= current_qty
                print(f"  Freed entire tray {i}: {current_qty}")
                quantities[i] = 0
        
        return quantities
    else:
        # ✅ ENHANCED: EXISTING tray should consume rejection quantity precisely
        total_available = sum(quantities)
        if total_available < qty_to_reduce:
            print(f"[brass_reduce_quantities_optimally] EXISTING tray: insufficient quantity ({total_available} < {qty_to_reduce})")
            return quantities  # Not enough quantity available
        
        print(f"[brass_reduce_quantities_optimally] EXISTING tray: consuming {qty_to_reduce} pieces")
        
        # ✅ STRATEGY: Consume from trays optimally to minimize fragmentation
        temp_quantities = quantities.copy()
        remaining_to_consume = qty_to_reduce
        
        # ✅ NEW: Try to consume from larger trays first to minimize fragmentation
        sorted_indices = sorted(range(len(temp_quantities)), key=lambda i: temp_quantities[i], reverse=True)
        
        for i in sorted_indices:
            if remaining_to_consume <= 0:
                break
            current_qty = temp_quantities[i]
            if current_qty > 0:
                consume_from_this_tray = min(remaining_to_consume, current_qty)
                temp_quantities[i] -= consume_from_this_tray
                remaining_to_consume -= consume_from_this_tray
                print(f"  Consumed {consume_from_this_tray} from tray {i}, new qty: {temp_quantities[i]}")
                
                if remaining_to_consume == 0:
                    break
        
        print(f"  Final quantities after consumption: {temp_quantities}")
        return temp_quantities


def get_brass_original_tray_distribution(lot_id):
    """
    Get original tray quantity distribution for the lot in Brass Audit context
    ✅ FIXED: Exclude trays rejected in Input Screening (rejected_tray=True)
    """
    try:
        print(f"[Brass Original Distribution] Getting distribution for lot_id: {lot_id}")
        
        # ✅ CRITICAL FIX: Exclude trays rejected in Input Screening AND Brass Audit
        tray_objects = BrassAuditTrayId.objects.filter(lot_id=lot_id).exclude(
            rejected_tray=True  # ✅ Exclude Input Screening rejected trays
        ).exclude(
            rejected_tray=True  # ✅ Exclude Brass Audit rejected trays
        ).order_by('date')
        
        print(f"[Brass Original Distribution] Found {tray_objects.count()} valid tray objects (excluding rejected trays)")
        
        if tray_objects.exists():
            # Use actual tray quantities from database
            quantities = []
            for tray in tray_objects:
                tray_qty = getattr(tray, 'tray_quantity', None)
                rejected_tray = getattr(tray, 'rejected_tray', False)
                rejected_tray = getattr(tray, 'rejected_tray', False)
                
                print(f"[Brass Original Distribution] Tray {tray.tray_id}: quantity = {tray_qty}, rejected_tray = {rejected_tray}, rejected_tray = {rejected_tray}")
                
                # ✅ Double-check: Only include non-rejected trays
                if not rejected_tray and not rejected_tray and tray_qty and tray_qty > 0:
                    quantities.append(tray_qty)
                else:
                    print(f"[Brass Original Distribution] SKIPPED tray {tray.tray_id} - rejected or zero quantity")
            
            if quantities:
                print(f"[Brass Original Distribution] From valid BrassTrayId objects: {quantities}")
                return quantities
        
        # Fallback: Calculate from brass_audit_physical_qty and standard capacity
        total_stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
        if not total_stock:
            print(f"[Brass Original Distribution] No TotalStockModel found for lot_id: {lot_id}")
            return []
        
        # ✅ UPDATED: Only use brass_audit_physical_qty
        total_qty = 0
        if hasattr(total_stock, 'brass_audit_physical_qty') and total_stock.brass_audit_physical_qty:
            total_qty = total_stock.brass_audit_physical_qty
        else:
            print(f"[Brass Original Distribution] No brass_audit_physical_qty available for lot_id: {lot_id}")
            return []
        
        tray_capacity = get_brass_tray_capacity_for_lot(lot_id)
        
        print(f"[Brass Original Distribution] Fallback calculation - total_qty: {total_qty}, tray_capacity: {tray_capacity}")
        
        if not total_qty or not tray_capacity:
            return []
        
        # Calculate distribution: remainder first, then full trays
        remainder = total_qty % tray_capacity
        full_trays = total_qty // tray_capacity
        
        distribution = []
        if remainder > 0:
            distribution.append(remainder)
        
        for _ in range(full_trays):
            distribution.append(tray_capacity)
        
        print(f"[Brass Original Distribution] Calculated: {distribution} (total: {total_qty}, capacity: {tray_capacity})")
        return distribution
        
    except Exception as e:
        print(f"[Brass Original Distribution] Error: {e}")
        import traceback
        traceback.print_exc()
        return []

def get_brass_tray_capacities_for_lot(lot_id):
    """
    Get all tray capacities for a lot in Brass Audit context
    ✅ FIXED: Exclude rejected trays from capacity calculation
    """
    try:
        print(f"[get_brass_tray_capacities_for_lot] Getting all capacities for lot_id: {lot_id}")
        
        # ✅ CRITICAL FIX: Exclude rejected trays from capacity calculation
        tray_objects = BrassAuditTrayId.objects.filter(lot_id=lot_id).exclude(
            rejected_tray=True  # ✅ Exclude Input Screening rejected trays
        ).exclude(
            rejected_tray=True  # ✅ Exclude Brass Audit rejected trays
        ).order_by('date')
        
        capacities = []
        for tray in tray_objects:
            capacity = getattr(tray, 'tray_capacity', None)
            if capacity and capacity > 0:
                capacities.append(capacity)
            else:
                # Fallback to standard capacity if not set
                standard_capacity = get_brass_tray_capacity_for_lot(lot_id)
                capacities.append(standard_capacity)
                
        print(f"[get_brass_tray_capacities_for_lot] Capacities: {capacities}")
        return capacities
        
    except Exception as e:
        print(f"[get_brass_tray_capacities_for_lot] Error: {e}")
        return []

def get_brass_tray_capacity_for_lot(lot_id):
    """
    Get tray capacity for a lot from BrassTrayId table (DYNAMIC) - Brass Audit version
    """
    try:
        print(f"[get_brass_tray_capacity_for_lot] Getting capacity for lot_id: {lot_id}")
        # Get tray capacity from BrassAuditTrayId table for this specific lot
        tray_objects = BrassAuditTrayId.objects.filter(lot_id=lot_id).exclude(rejected_tray=True)
        
        if tray_objects.exists():
            # Get tray_capacity from first tray (all trays in same lot should have same capacity)
            first_tray = tray_objects.first()
            tray_capacity = getattr(first_tray, 'tray_capacity', None)
            
            if tray_capacity and tray_capacity > 0:
                print(f"[get_brass_tray_capacity_for_lot] Found tray_capacity from BrassTrayId: {tray_capacity}")
                return tray_capacity
                
            # If tray_capacity is not set, check all trays for a valid capacity
            for tray in tray_objects:
                capacity = getattr(tray, 'tray_capacity', None)
                if capacity and capacity > 0:
                    print(f"[get_brass_tray_capacity_for_lot] Found valid tray_capacity: {capacity}")
                    return capacity
        
        # Fallback: Get from TotalStockModel > batch_id
        total_stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
        if total_stock and hasattr(total_stock, 'batch_id') and total_stock.batch_id:
            batch_capacity = getattr(total_stock.batch_id, 'tray_capacity', None)
            if batch_capacity and batch_capacity > 0:
                print(f"[get_brass_tray_capacity_for_lot] Using batch tray_capacity: {batch_capacity}")
                return batch_capacity
                
        print(f"[get_brass_tray_capacity_for_lot] Using default capacity: 12")
        return 12  # Final fallback
        
    except Exception as e:
        print(f"[get_brass_tray_capacity_for_lot] Error: {e}")
        import traceback
        traceback.print_exc()
        return 12


def is_new_tray_by_id(tray_id):
    """
    Check if a tray is marked as new_tray
    """
    try:
        from modelmasterapp.models import TrayId
        tray_obj = TrayId.objects.filter(tray_id=tray_id).first()
        return getattr(tray_obj, 'new_tray', False) if tray_obj else False
    except Exception as e:
        print(f"[is_new_tray_by_id] Error: {e}")
        return False
#=======================================================

def brass_calculate_distribution_after_rejections_enhanced(lot_id, original_distribution):
    """
    Enhanced calculation with detailed logging for debugging delink logic
    """
    current_distribution = original_distribution.copy()
    
    # Get all rejections for this lot ordered by creation
    rejections = Brass_Audit_Rejected_TrayScan.objects.filter(lot_id=lot_id).order_by('id')
    
    print(f"🔧 [Enhanced Distribution Calc] Starting with: {original_distribution}")
    print(f"🔧 [Enhanced Distribution Calc] Processing {rejections.count()} rejections for lot {lot_id}")
    
    for idx, rejection in enumerate(rejections):
        rejected_qty = int(rejection.rejected_tray_quantity) if rejection.rejected_tray_quantity else 0
        tray_id = rejection.rejected_tray_id
        reason = rejection.rejection_reason.rejection_reason if rejection.rejection_reason else 'Unknown'
        
        if rejected_qty <= 0:
            continue
        
        print(f"🔧 [Enhanced Distribution Calc] Rejection {idx + 1}:")
        print(f"   - Reason: {reason}")
        print(f"   - Qty: {rejected_qty}")
        print(f"   - Tray ID: '{tray_id}'")
        print(f"   - Before: {current_distribution}")
        
        # ✅ ENHANCED: Handle SHORTAGE rejections properly
        if not tray_id or tray_id.strip() == '':
            # SHORTAGE rejection - consume from existing trays
            print(f"   - SHORTAGE rejection detected")
            current_distribution = brass_consume_shortage_from_distribution(current_distribution, rejected_qty)
            print(f"   - After SHORTAGE: {current_distribution}")
            continue
        
        # ✅ ENHANCED: Check if NEW tray was used for non-SHORTAGE rejections
        is_new_tray = is_new_tray_by_id(tray_id)
        print(f"   - is_new_tray_by_id('{tray_id}') = {is_new_tray}")
        
        if is_new_tray:
            # NEW tray creates empty trays by freeing up space
            print(f"   - NEW tray used - freeing up {rejected_qty} space in existing trays")
            current_distribution = brass_free_up_space_optimally(current_distribution, rejected_qty)
            print(f"   - After NEW tray free-up: {current_distribution}")
        else:
            # EXISTING tray removes entire tray from distribution
            print(f"   - EXISTING tray used - removing tray from distribution")
            current_distribution = brass_remove_rejected_tray_from_distribution(current_distribution, rejected_qty)
            print(f"   - After EXISTING tray removal: {current_distribution}")
    
    print(f"🔧 [Enhanced Distribution Calc] FINAL distribution: {current_distribution}")
    
    # ✅ ENHANCED: Analyze empty trays
    empty_positions = [i for i, qty in enumerate(current_distribution) if qty == 0]
    print(f"🔧 [Enhanced Distribution Calc] Empty positions: {empty_positions}")
    
    return current_distribution

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def brass_audit_get_delink_tray_data(request):
    try:
        lot_id = request.GET.get('lot_id')
        if not lot_id:
            return Response({'success': False, 'error': 'Missing lot_id'}, status=400)
        
        stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
        if not stock:
            return Response({'success': False, 'error': 'Stock not found'}, status=400)
        
        original_distribution = get_brass_actual_tray_distribution_for_delink(lot_id, stock)
        print(f"🔍 [brass_audit_get_delink_tray_data] Original distribution: {original_distribution}")

        if not original_distribution:
            return Response({'success': True, 'delink_trays': [], 'message': 'No tray distribution found'})

        # Apply rejections first
        current_distribution = brass_calculate_distribution_after_rejections_enhanced(lot_id, original_distribution)
        print(f"🔍 [brass_audit_get_delink_tray_data] Current distribution after rejections: {current_distribution}")

        # ✅ NEW: Apply missing_qty (shortage) after rejections
        missing_qty = stock.brass_audit_missing_qty or 0
        if missing_qty > 0:
            print(f"🔍 [brass_audit_get_delink_tray_data] Applying missing_qty: {missing_qty}")
            current_distribution = brass_consume_shortage_from_distribution(current_distribution, missing_qty)
            print(f"🔍 [brass_audit_get_delink_tray_data] Distribution after missing_qty: {current_distribution}")

        # Find empty trays (qty == 0)
        delink_trays = []
        empty_tray_positions = []
        for i, qty in enumerate(current_distribution):
            if qty == 0:
                original_capacity = original_distribution[i] if i < len(original_distribution) else 0
                if original_capacity > 0:
                    delink_trays.append({
                        'tray_number': i + 1,
                        'original_capacity': original_capacity,
                        'current_qty': 0,
                        'needs_delink': True
                    })
                    empty_tray_positions.append(i + 1)

        print(f"🔍 [brass_audit_get_delink_tray_data] Empty tray positions: {empty_tray_positions}")
        print(f"🔍 [brass_audit_get_delink_tray_data] Total empty trays needing delink: {len(delink_trays)}")

        if len(delink_trays) == 0:
            return Response({
                'success': True,
                'delink_trays': [],
                'message': 'No empty trays found - no delink needed',
                'original_distribution': original_distribution,
                'current_distribution': current_distribution
            })

        return Response({
            'success': True,
            'delink_trays': delink_trays,
            'original_distribution': original_distribution,
            'current_distribution': current_distribution,
            'total_empty_trays': len(delink_trays),
            'empty_positions': empty_tray_positions
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return Response({'success': False, 'error': str(e)}, status=500)
   
   
   
def get_brass_actual_tray_distribution_for_delink(lot_id, stock):
    """
    ✅ FIXED: Always calculate from brass_audit_physical_qty for accurate delink detection
    """
    try:
        print(f"🔍 [get_brass_actual_tray_distribution_for_delink] Getting distribution for lot_id: {lot_id}")
        
        # ✅ ALWAYS use brass_audit_physical_qty for delink calculations
        total_qty = 0
        if hasattr(stock, 'brass_audit_missing_qty') and stock.brass_audit_missing_qty:
            # If brass_audit_missing_qty is present, use IP accepted quantity
            total_qty = getattr(stock, 'total_IP_accpeted_quantity', 0)
        elif hasattr(stock, 'brass_audit_physical_qty') and stock.brass_audit_physical_qty:
            total_qty = stock.brass_audit_physical_qty
        else:
            print(f"❌ No brass_audit_physical_qty available for lot_id: {lot_id}")
            return []
        
        tray_capacity = get_brass_tray_capacity_for_lot(lot_id)
        print(f"🔍 Total qty: {total_qty}, Tray capacity: {tray_capacity}")
        
        if not total_qty or not tray_capacity:
            return []
        
        # ✅ CORRECTED: Calculate distribution: remainder first, then full trays
        remainder = total_qty % tray_capacity
        full_trays = total_qty // tray_capacity
        
        distribution = []
        if remainder > 0:
            distribution.append(remainder)
        
        for _ in range(full_trays):
            distribution.append(tray_capacity)
        
        print(f"✅ Calculated distribution: {distribution}")
        print(f"   Total: {total_qty}, Capacity: {tray_capacity}")
        print(f"   Remainder: {remainder}, Full trays: {full_trays}")
        
        return distribution
        
    except Exception as e:
        print(f"❌ Error calculating distribution: {e}")
        return []

def brass_calculate_distribution_after_rejections(lot_id, original_distribution):
    """
    Calculate the current tray distribution after applying all rejections.
    
    CORRECTED LOGIC:
    - NEW tray usage frees up existing tray space (creates empty trays)
    - Existing tray usage removes that tray entirely from distribution  
    - SHORTAGE rejections consume quantities from existing trays (can create empty trays)
    """
    current_distribution = original_distribution.copy()
    
    # Get all rejections for this lot ordered by creation
    rejections = Brass_Audit_Rejected_TrayScan.objects.filter(lot_id=lot_id).order_by('id')
    
    print(f"DEBUG: Processing {rejections.count()} rejections for lot {lot_id}")
    print(f"DEBUG: Starting distribution: {original_distribution}")
    
    for rejection in rejections:
        rejected_qty = int(rejection.rejected_tray_quantity) if rejection.rejected_tray_quantity else 0
        tray_id = rejection.rejected_tray_id
        reason = rejection.rejection_reason.rejection_reason if rejection.rejection_reason else 'Unknown'
        
        if rejected_qty <= 0:
            continue
        
        print(f"DEBUG: Processing rejection - Reason: {reason}, Qty: {rejected_qty}, Tray ID: '{tray_id}'")
        
        # ✅ FIXED: Handle SHORTAGE rejections properly
        if not tray_id or tray_id.strip() == '':
            # SHORTAGE rejection - consume from existing trays
            current_distribution = brass_consume_shortage_from_distribution(current_distribution, rejected_qty)
            continue
        
        # Check if NEW tray was used for non-SHORTAGE rejections
        is_new_tray = is_new_tray_by_id(tray_id)
        print(f"DEBUG: is_new_tray_by_id('{tray_id}') = {is_new_tray}")
        
        if is_new_tray:
            # NEW tray creates empty trays by freeing up space
            current_distribution = brass_free_up_space_optimally(current_distribution, rejected_qty)
            print(f"DEBUG: NEW tray freed up {rejected_qty} space")
        else:
            # EXISTING tray removes entire tray from distribution
            current_distribution = brass_remove_rejected_tray_from_distribution(current_distribution, rejected_qty)
            print(f"DEBUG: EXISTING tray removed from distribution")
        
        print(f"DEBUG: Distribution after this rejection: {current_distribution}")
    
    print(f"DEBUG: Final distribution: {current_distribution}")
    return current_distribution


def brass_consume_shortage_from_distribution(distribution, shortage_qty):
    """
    ✅ NEW FUNCTION: Handle SHORTAGE rejections by consuming from existing trays
    This will consume from smallest trays first to maximize chance of creating empty trays
    
    Example: [6, 12, 12] with shortage 6 → [0, 12, 12]
    """
    result = distribution.copy()
    remaining_shortage = shortage_qty
    
    print(f"   SHORTAGE: consuming {shortage_qty} from distribution {distribution}")
    
    # Consume from smallest trays first (to create empty trays for delink)
    sorted_indices = sorted(range(len(result)), key=lambda i: result[i])
    
    for i in sorted_indices:
        if remaining_shortage <= 0:
            break
            
        current_qty = result[i]
        if current_qty >= remaining_shortage:
            result[i] -= remaining_shortage
            print(f"   Consumed {remaining_shortage} from tray {i}, remaining: {result[i]}")
            remaining_shortage = 0
        elif current_qty > 0:
            remaining_shortage -= current_qty
            print(f"   Consumed all {current_qty} from tray {i}")
            result[i] = 0
    
    if remaining_shortage > 0:
        print(f"   ⚠️ WARNING: Could not consume all shortage qty, remaining: {remaining_shortage}")
    
    print(f"   SHORTAGE result: {result}")
    return result


def brass_remove_rejected_tray_from_distribution(distribution, rejected_qty):
    """
    EXISTING tray rejection: consume rejection quantity AND remove one tray entirely
    This matches the user's requirement where existing tray usage removes a physical tray
    """
    result = distribution.copy()
    total_available = sum(result)
    
    if total_available < rejected_qty:
        return result  # Not enough quantity, return unchanged
    
    # Step 1: Try to find exact match first
    for i, qty in enumerate(result):
        if qty == rejected_qty:
            del result[i]
            print(f"   Removed tray {i} with exact matching qty {rejected_qty}")
            return result
    
    # Step 2: No exact match - consume rejected_qty and remove one tray
    remaining_to_consume = rejected_qty
    
    # Consume the rejection quantity from available trays
    for i in range(len(result)):
        if remaining_to_consume <= 0:
            break
        current_qty = result[i]
        consume_from_this_tray = min(remaining_to_consume, current_qty)
        result[i] -= consume_from_this_tray
        remaining_to_consume -= consume_from_this_tray
    
    # Step 3: Remove one tray entirely (prefer empty ones first)
    # Remove empty tray first
    for i in range(len(result)):
        if result[i] == 0:
            del result[i]
            print(f"   Removed empty tray at position {i}")
            return result
    
    # If no empty tray, remove the smallest quantity tray
    if result:
        min_qty = min(result)
        for i in range(len(result)):
            if result[i] == min_qty:
                del result[i]
                print(f"   Removed tray {i} with smallest qty {min_qty}")
                return result
    
    return result


def brass_free_up_space_optimally(distribution, qty_to_free):
    """
    Enhanced free up space function with better logging
    Free up space in existing trays when NEW tray is used for rejection.
    Always zero out the smallest trays first, so delink is possible.
    """
    result = distribution.copy()
    remaining = qty_to_free
    
    print(f"   🔧 [Free Up Space] Input: {distribution}, qty_to_free: {qty_to_free}")
    
    # Free from smallest trays first (to maximize empty trays for delink)
    sorted_indices = sorted(range(len(result)), key=lambda i: result[i])
    print(f"   🔧 [Free Up Space] Processing order (smallest first): {sorted_indices}")
    
    for i in sorted_indices:
        if remaining <= 0:
            break
        current_qty = result[i]
        if current_qty >= remaining:
            result[i] = current_qty - remaining
            print(f"   🔧 [Free Up Space] Freed {remaining} from tray {i+1}, new qty: {result[i]}")
            remaining = 0
        elif current_qty > 0:
            remaining -= current_qty
            print(f"   🔧 [Free Up Space] Freed entire tray {i+1}: {current_qty} -> 0")
            result[i] = 0
    
    empty_trays_created = [i+1 for i, qty in enumerate(result) if qty == 0]
    print(f"   🔧 [Free Up Space] Result: {result}")
    print(f"   🔧 [Free Up Space] Empty trays created: {empty_trays_created}")
    
    return result

@require_GET
def brass_audit_delink_check_tray_id(request):
    """
    Validate tray ID for delink process in Brass Audit
    Check if tray exists in same lot and is not already rejected
    ✅ UPDATED: Do NOT allow new trays (without lot_id)
    """
    tray_id = request.GET.get('tray_id', '')
    current_lot_id = request.GET.get('lot_id', '')
    
    try:
        if not tray_id:
            return JsonResponse({
                'exists': False,
                'valid_for_rejection': False,
                'error': 'Tray ID is required',
                'status_message': 'Required'
            })
        
        # Get the tray object if it exists
        tray_obj = BrassAuditTrayId.objects.filter(tray_id=tray_id, lot_id=current_lot_id).first()

        if not tray_obj:
            return JsonResponse({
                'exists': False,
                'valid_for_rejection': False,
                'error': 'Tray ID not found',
                'status_message': 'Not Found'
            })

        # ✅ NEW: Check if tray is already rejected in BrassTrayId
        ip_tray_obj = BrassTrayId.objects.filter(tray_id=tray_id, lot_id=current_lot_id).first()
        if ip_tray_obj and getattr(ip_tray_obj, 'rejected_tray', False):
            return JsonResponse({
                'exists': False,
                'valid_for_rejection': False,
                'error': 'Already rejected in Input Screening',
                'status_message': 'Already Rejected in IP'
            })

        # ✅ UPDATED: Check 1 - Do NOT allow new trays (without lot_id)
        if not tray_obj.lot_id or tray_obj.lot_id == '' or tray_obj.lot_id is None:
            return JsonResponse({
                'exists': False,
                'valid_for_rejection': False,
                'error': 'New trays not allowed for delink',
                'status_message': 'New Tray Not Allowed'
            })

        # ✅ CHECK 2: Must belong to same lot
        if str(tray_obj.lot_id).strip() != str(current_lot_id).strip():
            return JsonResponse({
                'exists': False,
                'valid_for_rejection': False,
                'error': 'Different lot',
                'status_message': 'Different Lot'
            })

        # ✅ CHECK 3: Must NOT be already rejected
        if hasattr(tray_obj, 'rejected_tray') and tray_obj.rejected_tray:
            return JsonResponse({
                'exists': False,
                'valid_for_rejection': False,
                'error': 'Already rejected',
                'status_message': 'Already Rejected'
            })

        # ✅ CHECK 4: Must NOT be in Brass_Audit_Rejected_TrayScan for this lot
        already_rejected_in_brass = Brass_Audit_Rejected_TrayScan.objects.filter(
            lot_id=current_lot_id,
            rejected_tray_id=tray_id
        ).exists()
        
        if already_rejected_in_brass:
            return JsonResponse({
                'exists': False,
                'valid_for_rejection': False,
                'error': 'Already rejected in Brass Audit',
                'status_message': 'Already Rejected'
            })

        # ✅ CHECK 5: Must NOT be already delinked
        if hasattr(tray_obj, 'delink_tray') and tray_obj.delink_tray:
            return JsonResponse({
                'exists': False,
                'valid_for_rejection': False,
                'error': 'Already delinked',
                'status_message': 'Already Delinked'
            })

        # ✅ CHECK 6: Must be verified (additional validation for delink)
        if not getattr(tray_obj, 'IP_tray_verified', False):
            return JsonResponse({
                'exists': False,
                'valid_for_rejection': False,
                'error': 'Tray not verified',
                'status_message': 'Not Verified'
            })

        # ✅ SUCCESS: Tray is valid for delink
        return JsonResponse({
            'exists': True,
            'valid_for_rejection': True,
            'status_message': 'Available for Delink',
            'validation_type': 'existing_valid',
            'tray_quantity': getattr(tray_obj, 'tray_quantity', 0) or 0
        })
        
    except Exception as e:
        print(f"❌ [brass_audit_delink_check_tray_id] Error: {e}")
        return JsonResponse({
            'exists': False,
            'valid_for_rejection': False,
            'error': 'System error',
            'status_message': 'System Error'
        })
#=========================================================

# This endpoint retrieves top tray scan data for a given lot_id
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def brass_audit_get_accepted_tray_scan_data(request):
    lot_id = request.GET.get('lot_id')
    if not lot_id:
        return Response({'success': False, 'error': 'Missing lot_id'}, status=400)
    
    try:
        stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
        if not stock:
            return Response({'success': False, 'error': 'Stock not found'}, status=404)
        
        model_no = stock.model_stock_no.model_no if stock.model_stock_no else ""
        tray_capacity = stock.batch_id.tray_capacity if stock.batch_id and hasattr(stock.batch_id, 'tray_capacity') else 10

        # ✅ UPDATED: Get rejection qty for calculation
        reason_store = Brass_Audit_Rejection_ReasonStore.objects.filter(lot_id=lot_id).first()
        total_rejection_qty = reason_store.total_rejection_quantity if reason_store else 0

        # ✅ UPDATED: Only use brass_audit_physical_qty
        if stock.brass_audit_physical_qty and stock.brass_audit_physical_qty > 0:
            brass_audit_physical_qty = stock.brass_audit_physical_qty
        else:
            return Response({'success': False, 'error': 'No brass physical quantity available'}, status=400)

        # ✅ CORRECTED: Calculate available_qty after subtracting rejections
        available_qty = brass_audit_physical_qty - total_rejection_qty
        
        print(f"📐 [brass_audit_get_accepted_tray_scan_data] brass_audit_physical_qty = {brass_audit_physical_qty}")
        print(f"📐 [brass_audit_get_accepted_tray_scan_data] total_rejection_qty = {total_rejection_qty}")
        print(f"📐 [brass_audit_get_accepted_tray_scan_data] available_qty = {available_qty}")

        # ✅ NEW: Check if this is for delink-only mode (when available_qty = 0 but have rejections with NEW trays)
        is_delink_only_case = (available_qty <= 0 and total_rejection_qty > 0)
        
        if is_delink_only_case:
            print(f"🚨 [brass_audit_get_accepted_tray_scan_data] Delink-only case detected: all pieces rejected")
            # ✅ NEW: For delink-only case, set minimal values but still allow the process to continue
            return Response({
                'success': True,
                'model_no': model_no,
                'tray_capacity': tray_capacity,
                'brass_audit_physical_qty': brass_audit_physical_qty,
                'total_rejection_qty': total_rejection_qty,
                'available_qty': 0,  # ✅ No available quantity
                'top_tray_qty': 0,   # ✅ No top tray quantity
                'has_draft': False,
                'draft_tray_id': "",
                'is_delink_only': True,  # ✅ NEW: Flag to indicate delink-only mode
                'delink_only_reason': 'All pieces rejected - only delink scanning needed'
            })

        # ✅ EXISTING: Normal case when there's available quantity
        if available_qty <= 0:
            return Response({'success': False, 'error': 'No available quantity for acceptance after rejections'}, status=400)

        # ✅ CORRECTED: Calculate top tray quantity using available_qty after rejections
        full_trays = available_qty // tray_capacity
        top_tray_qty = available_qty % tray_capacity

        # ✅ CORRECTED: If remainder is 0 and we have quantity, the last tray should be full capacity
        if top_tray_qty == 0 and available_qty > 0:
            top_tray_qty = tray_capacity

        print(f"📊 [brass_audit_get_accepted_tray_scan_data] Tray calculation: {available_qty} qty = {full_trays} full trays + {top_tray_qty} top tray")

        # Check for existing draft data
        has_draft = Brass_Audit_Accepted_TrayID_Store.objects.filter(lot_id=lot_id, is_draft=True).exists()
        draft_tray_id = ""
        
        if has_draft:
            draft_record = Brass_Audit_Accepted_TrayID_Store.objects.filter(lot_id=lot_id, is_draft=True).first()
            if (draft_record):
                draft_tray_id = draft_record.tray_id
        
        return Response({
            'success': True,
            'model_no': model_no,
            'tray_capacity': tray_capacity,
            'brass_audit_physical_qty': brass_audit_physical_qty,
            'total_rejection_qty': total_rejection_qty,
            'available_qty': available_qty,
            'top_tray_qty': top_tray_qty,
            'has_draft': has_draft,
            'draft_tray_id': draft_tray_id,
            'is_delink_only': False  # ✅ Normal mode
        })
    except Exception as e:
        traceback.print_exc()
        return Response({'success': False, 'error': str(e)}, status=500)
    
    
@csrf_exempt
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def brass_audit_save_single_top_tray_scan(request):
    try:
        data = request.data
        lot_id = data.get('lot_id')
        tray_id = data.get('tray_id', '').strip()  # ✅ Allow empty
        tray_qty = data.get('tray_qty', 0)         # ✅ Allow 0
        draft_save = data.get('draft_save', False)
        delink_trays = data.get('delink_trays', [])
        user = request.user

        # ✅ UPDATED: Check if this is a "delink-only" case
        is_delink_only = (not tray_id or tray_qty == 0) and delink_trays
        print(f"  is_delink_only: {is_delink_only}")

        # ✅ UPDATED: Validation - require lot_id always, but tray_id/tray_qty only if not delink-only
        if not lot_id:
            return Response({
                'success': False, 
                'error': 'Missing lot_id'
            }, status=400)

        # ✅ NEW: For non-delink-only cases, require tray_id and tray_qty
        if not is_delink_only and (not tray_id or not tray_qty):
            return Response({
                'success': False, 
                'error': 'Missing tray_id or tray_qty for top tray scanning'
            }, status=400)

        # ✅ NEW: For delink-only cases, require delink_trays
        if is_delink_only and not delink_trays:
            return Response({
                'success': False, 
                'error': 'Missing delink_trays for delink-only operation'
            }, status=400)

        # ✅ UPDATED: Validation - Prevent same tray ID for delink and top tray (only if top tray exists)
        if tray_id:
            delink_tray_ids = [delink['tray_id'] for delink in delink_trays if delink.get('tray_id')]
            if tray_id in delink_tray_ids:
                return Response({
                    'success': False,
                    'error': 'Top tray and delink tray cannot be the same'
                }, status=400)

        # ✅ UPDATED: Validate top tray_id only if provided
        if tray_id:
            top_tray_obj = BrassAuditTrayId.objects.filter(tray_id=tray_id, lot_id=lot_id).first()
            if not top_tray_obj:
                return Response({
                    'success': False,
                    'error': f'Top tray ID "{tray_id}" does not exist.'
                }, status=400)
            
            # Validate top tray belongs to same lot
            if str(top_tray_obj.lot_id) != str(lot_id):
                return Response({
                    'success': False,
                    'error': f'Top tray ID "{tray_id}" does not belong to this lot.'
                }, status=400)
            
            # Validate top tray is not rejected
            if top_tray_obj.rejected_tray:
                return Response({
                    'success': False,
                    'error': f'Top tray ID "{tray_id}" is already rejected.'
                }, status=400)

        # ✅ UPDATED: Validate all delink trays (only if not draft and delink_trays exist)
        if not draft_save and delink_trays:
            # Check if any delink tray is missing
            missing_delink = any(not tray.get('tray_id') for tray in delink_trays)
            if missing_delink:
                return Response({
                    'success': False,
                    'error': 'Please fill all Delink Tray IDs before submitting.'
                }, status=400)
                
            for delink in delink_trays:
                delink_tray_id = delink.get('tray_id', '').strip()
                if delink_tray_id:
                    delink_tray_obj = BrassAuditTrayId.objects.filter(tray_id=delink_tray_id, lot_id=lot_id).first()
                    if not delink_tray_obj:
                        return Response({
                            'success': False,
                            'error': f'Delink tray ID "{delink_tray_id}" does not exist.'
                        }, status=400)
                    
                    if str(delink_tray_obj.lot_id) != str(lot_id):
                        return Response({
                            'success': False,
                            'error': f'Delink tray ID "{delink_tray_id}" does not belong to this lot.'
                        }, status=400)
                    
                    if delink_tray_obj.rejected_tray:
                        return Response({
                            'success': False,
                            'error': f'Delink tray ID "{delink_tray_id}" is already rejected.'
                        }, status=400)

        # ✅ UPDATED: Handle BrassTrayId table updates only for final submit (not draft)
        delink_count = 0
        if not draft_save:
            # ✅ UPDATED: Update top tray only if provided
            if tray_id:
                top_tray_obj = BrassAuditTrayId.objects.filter(tray_id=tray_id, lot_id=lot_id).first()
                if top_tray_obj:
                    top_tray_obj.top_tray = True
                    top_tray_obj.tray_quantity = tray_qty
                    top_tray_obj.save(update_fields=['top_tray', 'tray_quantity'])
                    print(f"✅ [brass_audit_save_single_top_tray_scan] Updated top tray: {tray_id}")
        
                # Update all other trays (except rejected and top tray) to have tray_quantity = tray_capacity
                all_trays_in_lot = BrassAuditTrayId.objects.filter(lot_id=lot_id, rejected_tray=False)
                for tray in all_trays_in_lot:
                    if tray.tray_id == tray_id or tray.delink_tray:
                        continue
                    old_qty = tray.tray_quantity
                    tray.tray_quantity = tray.tray_capacity
                    tray.top_tray = False
                    tray.save(update_fields=['tray_quantity', 'top_tray'])
                    print(f"   Updated BrassAuditTrayId tray {tray.tray_id}: qty {old_qty}→{tray.tray_capacity}, top_tray=False")

            # ✅ UPDATED: Process delink trays (works for both normal and delink-only modes)
            for delink in delink_trays:
                delink_tray_id = delink.get('tray_id', '').strip()
                if delink_tray_id:
                    delink_count += 1
                    
                    # BrassAuditTrayId
                    brass_delink_tray_obj = BrassAuditTrayId.objects.filter(tray_id=delink_tray_id, lot_id=lot_id).first()
                    if brass_delink_tray_obj:
                        brass_delink_tray_obj.delink_tray = True
                        brass_delink_tray_obj.lot_id = None
                        brass_delink_tray_obj.batch_id = None
                        brass_delink_tray_obj.IP_tray_verified = False
                        brass_delink_tray_obj.top_tray = False
                        brass_delink_tray_obj.save(update_fields=[
                            'delink_tray', 'lot_id', 'batch_id', 'IP_tray_verified', 'top_tray'
                        ])
                        print(f"✅ Delinked BrassAuditTrayId tray: {delink_tray_id}")
        
                    # IPTrayId
                    ip_delink_tray_obj = IPTrayId.objects.filter(tray_id=delink_tray_id, lot_id=lot_id).first()
                    if ip_delink_tray_obj:
                        ip_delink_tray_obj.delink_tray = True
                        ip_delink_tray_obj.save(update_fields=['delink_tray'])
                        print(f"✅ Delinked IPTrayId tray: {delink_tray_id} for lot: {lot_id}")
                    
                    # IQFTrayId
                    iqf_delink_tray_obj = IQFTrayId.objects.filter(tray_id=delink_tray_id, lot_id=lot_id).first()
                    if iqf_delink_tray_obj:
                        iqf_delink_tray_obj.delink_tray = True
                        iqf_delink_tray_obj.save(update_fields=['delink_tray'])
                        print(f"✅ Delinked BrassTrayId tray: {delink_tray_id} for lot: {lot_id}")
                    
                            
                    # BrassTrayId
                    brass_delink_tray_obj = BrassTrayId.objects.filter(tray_id=delink_tray_id, lot_id=lot_id).first()
                    if brass_delink_tray_obj:
                        brass_delink_tray_obj.delink_tray = True
                        brass_delink_tray_obj.save(update_fields=['delink_tray'])
                        print(f"✅ Delinked BrassTrayId tray: {delink_tray_id} for lot: {lot_id}")
                    
                    # DPTrayId_History
                    dp_history_tray_obj = DPTrayId_History.objects.filter(tray_id=delink_tray_id, lot_id=lot_id).first()
                    if dp_history_tray_obj:
                        dp_history_tray_obj.delink_tray = True
                        dp_history_tray_obj.save(update_fields=['delink_tray'])
                        print(f"✅ Delinked DPTrayId_History tray: {delink_tray_id} for lot: {lot_id}")
                    
                    # TrayId
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

            # ✅ UPDATED: Update TotalStockModel flags (works for both modes)
            stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
            if stock:
                if is_delink_only:
                    # ✅ NEW: For delink-only, set appropriate flags
                    stock.brass_audit_accepted_tray_scan_status = True  # Mark as completed
                    stock.next_process_module = "Jig Loading"     # Or appropriate next module
                    stock.last_process_module = "Brass Audit"
                    stock.brass_audit_onhold_picking = False
                    stock.send_brass_qc=False
                    print(f"✅ Updated stock for DELINK-ONLY mode")
                else:
                    # Normal mode
                    stock.brass_audit_accepted_tray_scan_status = True
                    stock.next_process_module = "Jig Loading"
                    stock.last_process_module = "Brass Audit"
                    stock.brass_audit_onhold_picking = False
                    print(f"✅ Updated stock for NORMAL mode")
                
                stock.save(update_fields=[
                    'brass_audit_accepted_tray_scan_status', 
                    'next_process_module', 
                    'last_process_module', 
                    'brass_audit_onhold_picking'
                ])

        # ✅ UPDATED: Handle draft save
        if draft_save:
            if not lot_id or (not tray_id and not delink_trays):
                return Response({
                    'success': False, 
                    'error': 'Missing lot_id, and no tray_id or delink trays provided'
                }, status=400)
            
            stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
            batch_id = stock.batch_id.batch_id if stock and stock.batch_id else ""
            draft_obj, created = Brass_Audit_TopTray_Draft_Store.objects.update_or_create(
                lot_id=lot_id,
                defaults={
                    'batch_id': batch_id,
                    'user': user,
                    'tray_id': tray_id or '',
                    'tray_qty': tray_qty or 0,
                    'delink_tray_ids': [d['tray_id'] for d in delink_trays if d.get('tray_id')],  # Keep for backward compatibility
                    'delink_trays_data': {
                        "positions": [
                            {
                                "position": idx,
                                "tray_id": d.get('tray_id', ''),  # may be empty string
                                "original_capacity": d.get('original_capacity', 0)
                            }
                            for idx, d in enumerate(delink_trays)
                        ]
                    }
                }
            )
            message = 'Draft saved successfully.'
            return Response({
                'success': True,
                'message': message,
                'draft_id': draft_obj.id,
                'top_tray_id': tray_id or '',
                'is_draft': True,
                'is_delink_only': is_delink_only
            })

        
        # ✅ Create JigLoadTrayId records for all accepted trays (rejected_tray=False)
        accepted_trays = BrassAuditTrayId.objects.filter(
            lot_id=lot_id,
            rejected_tray=False,
            delink_tray=False
        )
        for tray in accepted_trays:
            # Avoid duplicates
            if not JigLoadTrayId.objects.filter(tray_id=tray.tray_id, lot_id=lot_id).exists():
                JigLoadTrayId.objects.create(
                    tray_id=tray.tray_id,
                    lot_id=lot_id,
                    batch_id=tray.batch_id,
                    tray_quantity=tray.tray_quantity,
                    tray_capacity=tray.tray_capacity,
                    tray_type=tray.tray_type,
                    top_tray=tray.top_tray,
                    IP_tray_verified=tray.IP_tray_verified,
                    new_tray=tray.new_tray,
                    delink_tray=tray.delink_tray,
                    rejected_tray=tray.rejected_tray,
                    user=request.user,
                    date=timezone.now()
                )
        
        # ✅ UPDATED: Success response
        if is_delink_only:
            message = f'Delink operation completed successfully. {delink_count} tray(s) delinked.'
        else:
            message = f'Top tray scan completed successfully.'
            if delink_count > 0:
                message += f' {delink_count} tray(s) delinked.'

        return Response({
            'success': True, 
            'message': message,
            'delink_count': delink_count,
            'top_tray_id': tray_id or '',
            'is_draft': draft_save,
            'is_delink_only': is_delink_only
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return Response({'success': False, 'error': str(e)}, status=500)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def brass_audit_get_top_tray_scan_draft(request):
    lot_id = request.GET.get('lot_id')
    if not lot_id:
        return Response({'success': False, 'error': 'Missing lot_id'}, status=400)
    try:
        draft_obj = Brass_Audit_TopTray_Draft_Store.objects.filter(lot_id=lot_id).first()
        if draft_obj:
            return Response({
                'success': True,
                'has_draft': True,
                'draft_data': {
                    'tray_id': draft_obj.tray_id,
                    'tray_qty': draft_obj.tray_qty,
                    'delink_tray_ids': draft_obj.delink_tray_ids,
                    'delink_trays': draft_obj.delink_trays_data.get('positions', []) if draft_obj.delink_trays_data else [],
                }
            })
        else:
            return Response({'success': True, 'has_draft': False})
    except Exception as e:
        return Response({'success': False, 'error': str(e)}, status=500)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def brass_audit_view_tray_list(request):
    """
    Returns tray list for a given lot_id based on different conditions:
    1. If brass_audit_accptance is True: get from BrassTrayId table
    2. If batch_rejection is True: split total_rejection_quantity by tray_capacity and get tray_ids from TrayId
    3. If batch_rejection is False: return all trays from IQF_Accepted_TrayID_Store
    """
    lot_id = request.GET.get('lot_id')
    if not lot_id:
        return Response({'success': False, 'error': 'Missing lot_id'}, status=400)

    try:
        # Check if this lot has brass_audit_accptance = True
        stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
        brass_audit_accptance = False
        tray_capacity = 0
        
        if stock:
            brass_audit_accptance = stock.brass_audit_accptance or False
            if stock.batch_id and hasattr(stock.batch_id, 'tray_capacity'):
                tray_capacity = stock.batch_id.tray_capacity or 0

        tray_list = []

        # ✅ PRIORITY 1: Always check BrassAuditTrayId first (transferred from Brass QC)
        # This table is populated when Brass QC transfers accepted data to Brass Audit
        brass_audit_trays = BrassAuditTrayId.objects.filter(lot_id=lot_id).order_by('id')
        if brass_audit_trays.exists():
            for idx, tray_obj in enumerate(brass_audit_trays):
                tray_list.append({
                    'sno': idx + 1,
                    'tray_id': tray_obj.tray_id,
                    'tray_qty': tray_obj.tray_quantity,
                    'top_tray': getattr(tray_obj, 'top_tray', False),
                })
            
            print(f"✅ [brass_audit_view_tray_list] Found {len(tray_list)} trays from BrassAuditTrayId for lot: {lot_id}")
            
            return Response({
                'success': True,
                'brass_audit_accptance': brass_audit_accptance,
                'batch_rejection': False,
                'total_rejection_qty': 0,
                'tray_capacity': tray_capacity,
                'trays': tray_list,
            })

        # Condition 1: If brass_audit_accptance is True, no additional check needed (handled above)
        # This section is now redundant but kept for backward compatibility
        if brass_audit_accptance:
            # Already handled above by BrassAuditTrayId check
            return Response({
                'success': True,
                'brass_audit_accptance': True,
                'batch_rejection': False,
                'total_rejection_qty': 0,
                'tray_capacity': tray_capacity,
                'trays': tray_list,
            })

        # Condition 2 & 3: Check rejection reason store (existing logic)
        reason_store = Brass_Audit_Rejection_ReasonStore.objects.filter(lot_id=lot_id).order_by('-id').first()
        batch_rejection = False
        total_rejection_qty = 0
        
        if reason_store:
            batch_rejection = reason_store.batch_rejection
            total_rejection_qty = reason_store.total_rejection_quantity

        if batch_rejection and total_rejection_qty > 0:
            # Batch rejection: get actual rejected quantities and tray IDs from Brass_Audit_Rejected_TrayScan AND modelmasterapp.TrayId
            rejected_scans = Brass_Audit_Rejected_TrayScan.objects.filter(lot_id=lot_id).order_by('id')
            
            if rejected_scans.exists():
                for idx, scan in enumerate(rejected_scans):
                    tray_id = scan.rejected_tray_id
                    # ✅ If no tray_id in scan record, get from main TrayId table
                    if not tray_id:
                        main_tray = TrayId.objects.filter(lot_id=lot_id, brass_rejected_tray=True).first()
                        if main_tray:
                            tray_id = main_tray.tray_id
                    
                    tray_list.append({
                        'sno': idx + 1,
                        'tray_id': tray_id or '',
                        'tray_qty': int(scan.rejected_tray_quantity) if scan.rejected_tray_quantity else 0,
                    })
            else:
                # Fallback: get from main TrayId table for lot rejections
                main_trays = TrayId.objects.filter(lot_id=lot_id, brass_rejected_tray=True)
                if main_trays.exists():
                    for idx, tray in enumerate(main_trays):
                        tray_list.append({
                            'sno': idx + 1,
                            'tray_id': tray.tray_id,
                            'tray_qty': tray.tray_quantity or 0,
                        })
                else:
                    # Final fallback: split total_rejection_qty by tray_capacity if no records found
                    tray_ids = list(BrassAuditTrayId.objects.filter(lot_id=lot_id).values_list('tray_id', flat=True))
                    if tray_capacity > 0:
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
            # Not batch rejection: get from Brass_Audit_Accepted_TrayID_Store (transferred from Brass QC)
            trays = Brass_Audit_Accepted_TrayID_Store.objects.filter(lot_id=lot_id).order_by('id')
            
            if trays.exists():
                # Use transferred data from Brass QC
                for idx, obj in enumerate(trays):
                    tray_list.append({
                        'sno': idx + 1,
                        'tray_id': obj.tray_id,
                        'tray_qty': obj.tray_qty,
                    })
                print(f"✅ [brass_audit_view_tray_list] Found {len(tray_list)} transferred trays from Brass QC")
            else:
                # ✅ FALLBACK: No transferred data, get from main TrayId table  
                from modelmasterapp.models import TrayId
                main_trays = TrayId.objects.filter(
                    lot_id=lot_id,
                    brass_rejected_tray=False,  # Only Brass QC accepted trays
                    rejected_tray=False,        # Exclude Input Screening rejected
                    tray_quantity__gt=0
                ).exclude(delink_tray=True)
                
                if main_trays.exists():
                    for idx, tray in enumerate(main_trays):
                        tray_list.append({
                            'sno': idx + 1,
                            'tray_id': tray.tray_id,
                            'tray_qty': tray.tray_quantity,
                        })
                    print(f"✅ [brass_audit_view_tray_list] Found {len(tray_list)} trays from TrayId table")
                else:
                    print(f"⚠️ [brass_audit_view_tray_list] No tray data found for lot: {lot_id}")

        return Response({
            'success': True,
            'brass_audit_accptance': brass_audit_accptance,
            'batch_rejection': batch_rejection,
            'total_rejection_qty': total_rejection_qty,
            'tray_capacity': tray_capacity,
            'trays': tray_list,
        })
        
    except Exception as e:
        return Response({'success': False, 'error': str(e)}, status=500)


@method_decorator(csrf_exempt, name='dispatch')
class BrassAudit_TrayValidateAPIView(APIView):
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
                # Continue anyway since we're checking BrassAuditTrayId which uses lot_id directly

            # Step 2: Check if the tray exists in BrassAuditTrayId for this lot_id
            print(f"[DEBUG] Checking if tray '{tray_id}' exists in BrassAuditTrayId for lot_id: '{lot_id_input}'")
            
            tray_exists = BrassAuditTrayId.objects.filter(
                lot_id=lot_id_input,  # Use lot_id directly
                tray_id=tray_id
            ).exists()
            
            print(f"[DEBUG] Tray exists in BrassAuditTrayId: {tray_exists}")
            
            # Additional debugging: show all trays for this lot_id in BrassAuditTrayId
            all_trays = BrassAuditTrayId.objects.filter(
                lot_id=lot_id_input
            ).values_list('tray_id', flat=True)
            print(f"[DEBUG] All trays in BrassAuditTrayId for lot_id '{lot_id_input}': {list(all_trays)}")
            
            # Also check if tray exists anywhere in BrassAuditTrayId (for debugging)
            tray_anywhere = BrassAuditTrayId.objects.filter(tray_id=tray_id, lot_id=lot_id_input)
            if tray_anywhere.exists():
                tray_lot_ids = list(tray_anywhere.values_list('lot_id', flat=True))
                print(f"[DEBUG] Tray '{tray_id}' found in BrassAuditTrayId for lot_ids: {tray_lot_ids}")
            
            print(f"[DEBUG] Final result - exists: {tray_exists}")
            print("="*50)
            
            return JsonResponse({
                'success': True, 
                'exists': tray_exists,
                'debug_info': {
                    'lot_id_received': lot_id_input,
                    'tray_id_received': tray_id,
                    'all_trays_in_brass_qc_store': list(all_trays),
                    'tray_exists_in_brass_qc_store': tray_exists
                }
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
def brass_audit_check_accepted_tray_draft(request):
    """Check if draft data exists for accepted tray scan"""
    lot_id = request.GET.get('lot_id')
    if not lot_id:
        return Response({'success': False, 'error': 'Missing lot_id'}, status=400)
    
    try:
        has_draft = Brass_Audit_Accepted_TrayID_Store.objects.filter(
            lot_id=lot_id, 
            is_draft=True
        ).exists()
        
        return Response({
            'success': True,
            'has_draft': has_draft
        })
    except Exception as e:
        return Response({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def brass_audit_save_accepted_tray_scan(request):
    try:
        data = request.data
        lot_id = data.get('lot_id')
        rows = data.get('rows', [])
        draft_save = data.get('draft_save', False)  # Get draft_save parameter
        user = request.user

        if not lot_id or not rows:
            return Response({'success': False, 'error': 'Missing lot_id or rows'}, status=400)

        # Validate all tray_ids exist in BrassTrayId table
        for idx, row in enumerate(rows):
            tray_id = row.get('tray_id')
            if not tray_id or not BrassAuditTrayId.objects.filter(tray_id=tray_id, lot_id=lot_id).exists():
                return Response({
                    'success': False,
                    'error': f'Tray ID "{tray_id}" is not existing (Row {idx+1}).'
                }, status=400)

        # Remove existing tray IDs for this lot (to avoid duplicates)
        Brass_Audit_Accepted_TrayID_Store.objects.filter(lot_id=lot_id).delete()

        total_qty = 0
        for row in rows:
            tray_id = row.get('tray_id')
            tray_qty = row.get('tray_qty')
            if not tray_id or tray_qty is None:
                continue
            total_qty += int(tray_qty)
            
            # Create with appropriate boolean flags based on draft_save parameter
            Brass_Audit_Accepted_TrayID_Store.objects.create(
                lot_id=lot_id,
                tray_id=tray_id,
                tray_qty=tray_qty,
                user=user,
                is_draft=draft_save,      # True if Draft button clicked
                is_save=not draft_save    # True if Submit button clicked
            )

        # Save/Update Brass_Audit_Accepted_TrayScan for this lot
        accepted_scan, created = Brass_Audit_Accepted_TrayScan.objects.get_or_create(
            lot_id=lot_id,
            user=user,
            defaults={'accepted_tray_quantity': total_qty}
        )
        if not created:
            accepted_scan.accepted_tray_quantity = total_qty
            accepted_scan.save(update_fields=['accepted_tray_quantity'])

        # Update TotalStockModel flags only if it's a final save (not draft)
        if not draft_save:
            stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
            if stock:
                stock.brass_audit_accepted_tray_scan_status = True
                stock.next_process_module = "Jig Loading"
                stock.last_process_module = "Brass Audit"
                stock.brass_audit_onhold_picking = False  # Reset onhold picking status
                stock.save(update_fields=['brass_audit_accepted_tray_scan_status', 'next_process_module', 'last_process_module', 'brass_audit_onhold_picking'])

        return Response({'success': True, 'message': 'Accepted tray scan saved.'})

    except Exception as e:
        return Response({'success': False, 'error': str(e)}, status=500)




@require_GET
def brass_audit_check_tray_id(request):
    tray_id = request.GET.get('tray_id', '')
    lot_id = request.GET.get('lot_id', '')  # This is your stock_lot_id

    # 1. Must exist in BrassAuditTrayId table and lot_id must match
    tray_obj = BrassAuditTrayId.objects.filter(tray_id=tray_id, lot_id=lot_id).first()
    exists = bool(tray_obj)
    same_lot = exists and str(tray_obj.lot_id) == str(lot_id)

    # 2. Must NOT be rejected in any module (Input Screening OR Brass Audit)
    already_rejected = False
    if exists and same_lot and lot_id:
        # ✅ CHECK 1: Check if rejected in Input Screening (rejected_tray=True)
        input_screening_rejected = getattr(tray_obj, 'rejected_tray', False)
        
        # ✅ CHECK 2: Check if rejected in Brass Audit (rejected_tray=True)
        brass_qc_rejected = getattr(tray_obj, 'rejected_tray', False)
        
        # ✅ CHECK 3: Check if rejected in Brass_Audit_Rejected_TrayScan for this lot
        brass_qc_scan_rejected = Brass_Audit_Rejected_TrayScan.objects.filter(
            lot_id=lot_id,
            rejected_tray_id=tray_id
        ).exists()
        
        # Mark as already rejected if any of the above is true
        already_rejected = input_screening_rejected or brass_qc_rejected or brass_qc_scan_rejected

    # Only valid if exists, same lot, and not already rejected
    is_valid = exists and same_lot and not already_rejected

    return JsonResponse({
        'exists': is_valid,
        'already_rejected': already_rejected,
        'not_in_same_lot': exists and not same_lot,
        'rejected_in_input_screening': exists and getattr(tray_obj, 'rejected_tray', False),
        'rejected_in_brass_qc': exists and getattr(tray_obj, 'rejected_tray', False)
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def brass_audit_get_rejected_tray_scan_data(request):
    lot_id = request.GET.get('lot_id')
    if not lot_id:
        return Response({'success': False, 'error': 'Missing lot_id'}, status=400)
    try:
        rows = []
        
        # ✅ NEW: Check if this is a lot rejection to get tray IDs from modelmasterapp.TrayId
        reason_store = Brass_Audit_Rejection_ReasonStore.objects.filter(lot_id=lot_id).order_by('-id').first()
        is_lot_rejection = reason_store and reason_store.batch_rejection
        
        # Get actual rejection records
        rejected_scans = Brass_Audit_Rejected_TrayScan.objects.filter(lot_id=lot_id)
        
        if rejected_scans.exists():
            for obj in rejected_scans:
                tray_id = obj.rejected_tray_id or ''
                
                # ✅ For lot rejections, if tray_id is empty, get from modelmasterapp.TrayId
                if is_lot_rejection and not tray_id:
                    main_tray = TrayId.objects.filter(lot_id=lot_id, brass_rejected_tray=True).first()
                    if main_tray:
                        tray_id = main_tray.tray_id
                
                rows.append({
                    'tray_id': tray_id,
                    'qty': int(obj.rejected_tray_quantity) if obj.rejected_tray_quantity else 0,
                    'reason': obj.rejection_reason.rejection_reason,
                    'reason_id': obj.rejection_reason.rejection_reason_id,
                })
        else:
            # ✅ Fallback: If no scan records but there's a rejection reason store
            if reason_store:
                # Get tray IDs from modelmasterapp.TrayId for lot rejections
                if is_lot_rejection:
                    main_trays = TrayId.objects.filter(lot_id=lot_id, brass_rejected_tray=True)
                    if main_trays.exists():
                        for idx, tray in enumerate(main_trays):
                            for reason in reason_store.rejection_reason.all():
                                rows.append({
                                    'tray_id': tray.tray_id,
                                    'qty': tray.tray_quantity or 0,
                                    'reason': reason.rejection_reason,
                                    'reason_id': reason.rejection_reason_id,
                                })
                                break  # Only show one reason per tray to avoid duplication
                        
        return Response({'success': True, 'rows': rows})
    except Exception as e:
        return Response({'success': False, 'error': str(e)}, status=500)


class BrassAuditCompletedView(APIView):
    renderer_classes = [TemplateHTMLRenderer]
    template_name = 'BrassAudit/BrassAudit_Completed.html'

    def get(self, request):
        from django.utils import timezone
        from datetime import datetime, timedelta
        import pytz

        user = request.user
        
        # Handle sorting parameters
        sort = request.GET.get('sort')
        order = request.GET.get('order', 'asc')  # Default to ascending
        
        # Field mapping for proper model field references
        sort_field_mapping = {
            'serial_number': 'lot_id',  # Use lot_id for serial number sorting
            'brass_audit_last_process_date_time': 'brass_audit_last_process_date_time',
            'plating_stk_no': 'batch_id__plating_stk_no',
            'polishing_stk_no': 'batch_id__polishing_stk_no',
            'plating_color': 'batch_id__plating_color',
            'category': 'batch_id__category',
            'polish_finish': 'batch_id__polish_finish',
            'tray_capacity': 'batch_id__tray_capacity',
            'vendor_location': 'batch_id__vendor_internal',  # Simplified to vendor field
            'no_of_trays': 'batch_id__tray_capacity',  # Approximate mapping
            'lot_qty': 'brass_qc_accepted_qty',
            'brass_audit_physical_qty': 'brass_audit_physical_qty',
            'brass_audit_accepted_qty': 'brass_audit_accepted_qty',
            'reject_qty': 'brass_rejection_qty'
        }
        
        # ✅ Date filtering logic
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
        brass_rejection_qty_subquery = Brass_Audit_Rejection_ReasonStore.objects.filter(
            lot_id=OuterRef('lot_id')
        ).values('total_rejection_quantity')[:1]

        queryset = TotalStockModel.objects.select_related(
            'batch_id',
            'batch_id__model_stock_no',
            'batch_id__version',
            'batch_id__location'
        ).filter(
            batch_id__total_batch_quantity__gt=0,
            brass_audit_last_process_date_time__range=(from_datetime, to_datetime)  # ✅ Direct date filtering
        ).annotate(
            brass_rejection_qty=brass_rejection_qty_subquery,
        ).filter(
            # ✅ Direct filtering on TotalStockModel fields
            Q(brass_audit_accptance=True) |
            Q(brass_audit_rejection=True) |
            Q(brass_audit_few_cases_accptance=True, brass_audit_onhold_picking=False) |
            Q(send_brass_audit_to_iqf=True, brass_audit_onhold_picking=False)
        )
        
        # Apply sorting if requested
        if sort and sort in sort_field_mapping:
            field = sort_field_mapping[sort]
            if order == 'desc':
                field = '-' + field
            queryset = queryset.order_by(field)
        else:
            queryset = queryset.order_by('-brass_audit_last_process_date_time', '-lot_id')

        print(f"📊 Found {queryset.count()} brass records in date range {from_date} to {to_date}")
        print("All lot_ids in completed queryset:", list(queryset.values_list('lot_id', flat=True)))

        # Pagination
        page_number = request.GET.get('page', 1)
        paginator = Paginator(queryset, 10)
        page_obj = paginator.get_page(page_number)

        # ✅ UPDATED: Build master_data from TotalStockModel records
        master_data = []
        for stock_obj in page_obj.object_list:
            batch = stock_obj.batch_id
            
            data = {
                'batch_id': batch.batch_id,
                'lot_id': stock_obj.lot_id,  # ✅ Include the actual lot_id
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
                
                # ✅ Stock-related fields from TotalStockModel
                'stock_lot_id': stock_obj.lot_id,
                'last_process_module': stock_obj.last_process_module,
                'next_process_module': stock_obj.next_process_module,
                'brass_audit_accepted_qty_verified': stock_obj.brass_audit_accepted_qty_verified,
                'brass_audit_accepted_qty': stock_obj.brass_audit_accepted_qty,
                'brass_rejection_qty': stock_obj.brass_rejection_qty,
                'brass_audit_missing_qty': stock_obj.brass_audit_missing_qty,
                'brass_audit_physical_qty': stock_obj.brass_audit_physical_qty,
                'brass_audit_physical_qty_edited': stock_obj.brass_audit_physical_qty_edited,
                'accepted_Ip_stock': stock_obj.accepted_Ip_stock,
                'rejected_ip_stock': stock_obj.rejected_ip_stock,
                'few_cases_accepted_Ip_stock': stock_obj.few_cases_accepted_Ip_stock,
                'accepted_tray_scan_status': stock_obj.accepted_tray_scan_status,
                'BA_pick_remarks': stock_obj.BA_pick_remarks,
                'brass_audit_accptance': stock_obj.brass_audit_accptance,  # ✅ This will now show True correctly
                'brass_accepted_tray_scan_status': stock_obj.brass_accepted_tray_scan_status,
                'brass_audit_rejection': stock_obj.brass_audit_rejection,
                'brass_audit_few_cases_accptance': stock_obj.brass_audit_few_cases_accptance,
                'brass_audit_onhold_picking': stock_obj.brass_audit_onhold_picking,
                'iqf_acceptance': stock_obj.iqf_acceptance,
                'send_brass_qc': stock_obj.send_brass_qc,
                'brass_qc_accepted_qty': stock_obj.brass_qc_accepted_qty,
                'brass_audit_last_process_date_time': stock_obj.brass_audit_last_process_date_time,
                'brass_audit_hold_lot': stock_obj.brass_audit_hold_lot,
                'brass_qc_accepted_qty_verified': stock_obj.brass_qc_accepted_qty_verified,
                # Additional batch fields
                'plating_stk_no': batch.plating_stk_no,
                'polishing_stk_no': batch.polishing_stk_no,
                'category': batch.category,
            }
            master_data.append(data)

        print(f"[BrassAuditCompletedView] Total master_data records: {len(master_data)}")
        
        # ✅ Process the data as before
        for data in master_data:
            brass_qc_accepted_qty = data.get('brass_qc_accepted_qty', 0)
            tray_capacity = data.get('tray_capacity', 0)
            data['vendor_location'] = f"{data.get('vendor_internal', '')}_{data.get('location__location_name', '')}"
            
            lot_id = data.get('stock_lot_id')
            
            if brass_qc_accepted_qty and brass_qc_accepted_qty > 0:
                data['display_accepted_qty'] = brass_qc_accepted_qty
            else:
                total_rejection_qty = 0
                rejection_store = Brass_Audit_Rejection_ReasonStore.objects.filter(lot_id=lot_id).first()
                if rejection_store and rejection_store.total_rejection_quantity:
                    total_rejection_qty = rejection_store.total_rejection_quantity

                total_stock_obj = TotalStockModel.objects.filter(lot_id=lot_id).first()
                
                if total_stock_obj and total_rejection_qty > 0:
                    data['display_accepted_qty'] = max(total_stock_obj.total_stock - total_rejection_qty, 0)
                    print(f"Calculated accepted qty for {lot_id}: {total_stock_obj.total_stock} - {total_rejection_qty} = {data['display_accepted_qty']}")
                else:
                    data['display_accepted_qty'] = 0

            display_qty = data.get('display_accepted_qty', 0)
            if tray_capacity > 0 and display_qty > 0:
                data['no_of_trays'] = math.ceil(display_qty / tray_capacity)
            else:
                data['no_of_trays'] = 0
                
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

        print("Processed lot_ids:", [data['stock_lot_id'] for data in master_data])
            
        context = {
            'master_data': master_data,
            'page_obj': page_obj,
            'paginator': paginator,
            'user': user,
            'from_date': from_date.strftime('%Y-%m-%d'),
            'to_date': to_date.strftime('%Y-%m-%d'),
            'date_filter_applied': bool(from_date_str and to_date_str),
        }
        return Response(context, template_name=self.template_name)
  
  
@method_decorator(csrf_exempt, name='dispatch')
class BrassTrayIdList_Complete_APIView(APIView):
    def get(self, request):
        batch_id = request.GET.get('batch_id')
        stock_lot_id = request.GET.get('stock_lot_id')
        lot_id = request.GET.get('lot_id') or stock_lot_id
        brass_audit_accptance = request.GET.get('brass_audit_accptance', 'false').lower() == 'true'
        brass_audit_rejection = request.GET.get('brass_audit_rejection', 'false').lower() == 'true'
        brass_qc_few_cases_accptance = request.GET.get('brass_qc_few_cases_accptance', 'false').lower() == 'true'
        
        if not batch_id:
            return JsonResponse({'success': False, 'error': 'Missing batch_id'}, status=400)
        
        if not lot_id:
            return JsonResponse({'success': False, 'error': 'Missing lot_id or stock_lot_id'}, status=400)
        
        # ✅ UPDATED: Base queryset - exclude trays rejected in Input Screening
        base_queryset = BrassTrayId.objects.filter(
            tray_quantity__gt=0,
            lot_id=lot_id
        ).exclude(
            rejected_tray=True  # ✅ EXCLUDE trays rejected in Input Screening
        )
        
        # Get rejected and accepted trays directly from BrassTrayId table
        rejected_trays = base_queryset.filter(rejected_tray=True)
        accepted_trays = base_queryset.filter(rejected_tray=False)
        
        print(f"Total trays in lot (excluding Input Screening rejected): {base_queryset.count()}")
        print(f"Rejected trays (Brass Audit): {rejected_trays.count()}")
        print(f"Accepted trays: {accepted_trays.count()}")
        
        # Apply filtering based on stock status
        if brass_audit_accptance and not brass_qc_few_cases_accptance:
            # Show only accepted trays
            queryset = accepted_trays
            print("Filtering for accepted trays only")
        elif brass_audit_rejection and not brass_qc_few_cases_accptance:
            # Show only rejected trays
            queryset = rejected_trays
            print("Filtering for rejected trays only")
        elif brass_qc_few_cases_accptance:
            # Show both accepted and rejected trays
            queryset = base_queryset
            print("Showing both accepted and rejected trays")
        else:
            # Default - show all trays
            queryset = base_queryset
            print("Using default filter - showing all trays")
        
        # Determine top tray based on status
        top_tray = None
        if brass_audit_accptance and not brass_qc_few_cases_accptance:
            # For accepted trays, prioritize top_tray, then top_tray
            top_tray = accepted_trays.filter(top_tray=True).first()
            if not top_tray:
                top_tray = accepted_trays.filter(top_tray=True).first()
        else:
            # For all other cases, prioritize ip_top_tray
            top_tray = queryset.filter(ip_top_tray=True).first()
            if not top_tray:
                top_tray = queryset.filter(top_tray=True).first()
        
        # Get other trays (excluding top tray)
        other_trays = queryset.exclude(pk=top_tray.pk if top_tray else None).order_by('id')
        
        data = []
        row_counter = 1

        # Helper function to create tray data
        def create_tray_data(tray_obj, is_top=False):
            nonlocal row_counter
            
            # Get rejection details if tray is rejected
            rejection_details = []
            if tray_obj.rejected_tray:
                # Get rejection details from Brass_Audit_Rejected_TrayScan if needed
                rejected_scans = Brass_Audit_Rejected_TrayScan.objects.filter(
                    lot_id=lot_id,
                    rejected_tray_id=tray_obj.tray_id
                )
                for scan in rejected_scans:
                    rejection_details.append({
                        'rejected_quantity': scan.rejected_tray_quantity,
                        'rejection_reason': scan.rejection_reason.rejection_reason if scan.rejection_reason else 'Unknown',
                        'rejection_reason_id': scan.rejection_reason.rejection_reason_id if scan.rejection_reason else None,
                        'user': scan.user.username if scan.user else None
                    })
            
            return {
                's_no': row_counter,
                'tray_id': tray_obj.tray_id,
                'tray_quantity': tray_obj.tray_quantity,
                'position': row_counter - 1,
                'is_top_tray': is_top,
                'rejected_tray': tray_obj.rejected_tray,
                'delink_tray': getattr(tray_obj, 'delink_tray', False),
                'rejection_details': rejection_details,
                'ip_top_tray': getattr(tray_obj, 'ip_top_tray', False),
                'ip_top_tray_qty': getattr(tray_obj, 'ip_top_tray_qty', None),
                'top_tray': getattr(tray_obj, 'top_tray', False),
                'rejected_tray': getattr(tray_obj, 'rejected_tray', False)  # ✅ NEW: Include Input Screening rejection status
            }

        # Add top tray first if it exists
        if top_tray:
            tray_data = create_tray_data(top_tray, is_top=True)
            data.append(tray_data)
            row_counter += 1

        # Add other trays
        for tray in other_trays:
            tray_data = create_tray_data(tray, is_top=False)
            data.append(tray_data)
            row_counter += 1
        
        print(f"Total trays returned: {len(data)}")
        
        # ✅ UPDATED: Get shortage rejections count (trays without tray_id) - use correct model
        shortage_count = Brass_Audit_Rejected_TrayScan.objects.filter(
            lot_id=lot_id
        ).filter(
            models.Q(rejected_tray_id__isnull=True) | models.Q(rejected_tray_id='')
        ).count()
        
        # ✅ UPDATED: Get count of Input Screening rejected trays for summary
        input_screening_rejected_count = BrassTrayId.objects.filter(
            lot_id=lot_id,
            tray_quantity__gt=0,
            rejected_tray=True
        ).count()
        
        # Rejection summary
        rejection_summary = {
            'total_rejected_trays': rejected_trays.count(),
            'rejected_tray_ids': list(rejected_trays.values_list('tray_id', flat=True)),
            'shortage_rejections': shortage_count,
            'total_accepted_trays': accepted_trays.count(),
            'accepted_tray_ids': list(accepted_trays.values_list('tray_id', flat=True)),
            'input_screening_rejected_count': input_screening_rejected_count  # ✅ NEW: Count of excluded trays
        }
        
        return JsonResponse({
            'success': True, 
            'trays': data,
            'rejection_summary': rejection_summary
        })

        
@method_decorator(csrf_exempt, name='dispatch')
class BrassTrayValidate_Complete_APIView(APIView):
    def post(self, request):
        try:
            data = request.data if hasattr(request, 'data') else json.loads(request.body.decode('utf-8'))
            batch_id_input = str(data.get('batch_id')).strip()
            tray_id = str(data.get('tray_id')).strip()
            
            # Get stock status parameters (optional, for enhanced validation)
            brass_audit_accptance = data.get('brass_audit_accptance', False)
            brass_audit_rejection = data.get('brass_audit_rejection', False)
            brass_qc_few_cases_accptance = data.get('brass_qc_few_cases_accptance', False)

            print(f"[BrassTrayValidate_Complete_APIView] User entered: batch_id={batch_id_input}, tray_id={tray_id}")
            print(f"Stock status: accepted={brass_audit_accptance}, rejected={brass_audit_rejection}, few_cases={brass_qc_few_cases_accptance}")

            # Base queryset for trays
            base_queryset = BrassTrayId.objects.filter(
                batch_id__batch_id__icontains=batch_id_input,
                tray_quantity__gt=0
            )
            
            # Apply the same filtering logic as the list API
            if brass_audit_accptance and not brass_qc_few_cases_accptance:
                # Only validate against accepted trays
                trays = base_queryset.filter(rejected_tray=False)
                print(f"Validating against accepted trays only")
            elif brass_audit_rejection and not brass_qc_few_cases_accptance:
                # Only validate against rejected trays
                trays = base_queryset.filter(rejected_tray=True)
                print(f"Validating against rejected trays only")
            else:
                # Validate against all trays (few_cases or default)
                trays = base_queryset
                print(f"Validating against all trays")
            
            print(f"Available tray_ids for validation: {[t.tray_id for t in trays]}")

            exists = trays.filter(tray_id=tray_id).exists()
            print(f"Tray ID '{tray_id}' exists in filtered results? {exists}")

            # Get additional info about the tray if it exists
            tray_info = {}
            if exists:
                tray = trays.filter(tray_id=tray_id).first()
                if tray:
                    tray_info = {
                        'rejected_tray': tray.rejected_tray,
                        'tray_quantity': tray.tray_quantity,
                        'ip_top_tray': tray.ip_top_tray,  # ✅ UPDATED: Use ip_top_tray instead of top_tray
                        'ip_top_tray_qty': tray.ip_top_tray_qty  # ✅ UPDATED: Include ip_top_tray_qty
                    }

            return JsonResponse({
                'success': True, 
                'exists': exists,
                'tray_info': tray_info
            })
            
        except Exception as e:
            print(f"[TrayValidate_Complete_APIView] Error: {str(e)}")
            return JsonResponse({'success': False, 'error': str(e)}, status=500)    
           
        
@method_decorator(csrf_exempt, name='dispatch')
class BrassGetShortageRejectionsView(APIView):
    def get(self, request):
        lot_id = request.GET.get('lot_id')
        
        if not lot_id:
            return JsonResponse({'success': False, 'error': 'Missing lot_id'}, status=400)
        
        # Get SHORTAGE rejections (where rejected_tray_id is empty or null)
        shortage_rejections = IP_Rejected_TrayScan.objects.filter(
            lot_id=lot_id,
            rejected_tray_id__isnull=True
        ).union(
            IP_Rejected_TrayScan.objects.filter(
                lot_id=lot_id,
                rejected_tray_id=''
            )
        )
        
        shortage_data = []
        for shortage in shortage_rejections:
            shortage_data.append({
                'quantity': shortage.rejected_tray_quantity,
                'reason': shortage.rejection_reason.rejection_reason,
                'user': shortage.user.username if shortage.user else None
            })
        
        return JsonResponse({
            'success': True,
            'shortage_rejections': shortage_data
        })


@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(login_required, name='dispatch')
class BrassAuditBatchRejectionDraftAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            data = request.data if hasattr(request, 'data') else json.loads(request.body.decode('utf-8'))
            batch_id = data.get('batch_id')
            lot_id = data.get('lot_id')
            total_qty = data.get('total_qty', 0)
            lot_rejected_comment = data.get('lot_rejected_comment', '').strip()
            is_draft = data.get('is_draft', True)

            if not batch_id or not lot_id or not lot_rejected_comment:
                return Response({'success': False, 'error': 'Missing required fields'}, status=400)

            # Save as draft
            draft_data = {
                'total_qty': total_qty,
                'lot_rejected_comment': lot_rejected_comment,
                'batch_rejection': True,
                'is_draft': is_draft
            }

            # Update or create draft record
            draft_obj, created = Brass_Audit_Draft_Store.objects.update_or_create(
                lot_id=lot_id,
                draft_type='batch_rejection',
                defaults={
                    'batch_id': batch_id,
                    'user': request.user,
                    'draft_data': draft_data
                }
            )

            return Response({
                'success': True, 
                'message': 'Batch rejection draft saved successfully',
                'draft_id': draft_obj.id
            })

        except Exception as e:
            import traceback
            traceback.print_exc()
            return Response({'success': False, 'error': str(e)}, status=500)


@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(login_required, name='dispatch')
class BrassAuditAutoSaveRejectionAPIView(APIView):
    """
    Auto-save endpoint for Brass Audit rejection data.
    Works alongside existing manual draft functionality.
    Saves drafts automatically as user types without page refresh.
    """
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        try:
            data = request.data if hasattr(request, 'data') else json.loads(request.body.decode('utf-8'))
            lot_id = data.get('lot_id')
            batch_id = data.get('batch_id')
            rejection_type = data.get('rejection_type')  # 'batch' or 'tray'
            auto_save = data.get('auto_save', True)  # Flag to indicate this is auto-save
            
            if not lot_id or not batch_id or not rejection_type:
                return Response({
                    'success': False, 
                    'error': 'Missing required fields: lot_id, batch_id, rejection_type'
                }, status=400)

            if rejection_type == 'batch':
                # Auto-save for batch rejection
                total_qty = data.get('total_qty', 0)
                lot_rejected_comment = data.get('lot_rejected_comment', '').strip()
                
                if not lot_rejected_comment or len(lot_rejected_comment) < 3:
                    return Response({
                        'success': False, 
                        'error': 'Lot rejection comment too short for auto-save'
                    }, status=400)
                
                # Save batch rejection draft
                draft_data = {
                    'total_qty': total_qty,
                    'lot_rejected_comment': lot_rejected_comment,
                    'batch_rejection': True,
                    'is_draft': True,
                    'auto_save': auto_save
                }
                
                draft_obj, created = Brass_Audit_Draft_Store.objects.update_or_create(
                    lot_id=lot_id,
                    draft_type='batch_rejection',
                    defaults={
                        'batch_id': batch_id,
                        'user': request.user,
                        'draft_data': draft_data
                    }
                )
                
                # ✅ FIXED: Auto-save should NOT update draft status in TotalStockModel
                # Only manual draft should change lot status to "Draft"
                # TotalStockModel.objects.filter(lot_id=lot_id).update(brass_audit_draft=True)
                
                return Response({
                    'success': True, 
                    'message': 'Batch rejection auto-saved successfully',
                    'draft_id': draft_obj.id,
                    'type': 'batch',
                    'auto_save': True
                })
                
            elif rejection_type == 'tray':
                # Auto-save for tray-wise rejection  
                tray_rejections = data.get('tray_rejections', [])
                
                if not tray_rejections:
                    return Response({
                        'success': False, 
                        'error': 'No tray rejection data provided'
                    }, status=400)
                
                # Validate tray rejection data has meaningful content
                valid_rejections = [r for r in tray_rejections if r.get('qty', 0) > 0]
                if not valid_rejections:
                    return Response({
                        'success': False, 
                        'error': 'No valid rejection quantities provided'
                    }, status=400)
                
                # Save tray rejection draft
                draft_data = {
                    'tray_rejections': tray_rejections,
                    'batch_rejection': False,
                    'is_draft': True,
                    'auto_save': auto_save
                }
                
                draft_obj, created = Brass_Audit_Draft_Store.objects.update_or_create(
                    lot_id=lot_id,
                    draft_type='tray_rejection',
                    defaults={
                        'batch_id': batch_id,
                        'user': request.user,
                        'draft_data': draft_data
                    }
                )
                
                # ✅ FIXED: Auto-save should NOT update draft status in TotalStockModel
                # Only manual draft should change lot status to "Draft"
                # TotalStockModel.objects.filter(lot_id=lot_id).update(brass_audit_draft=True)
                
                return Response({
                    'success': True, 
                    'message': f'Tray rejection auto-saved successfully ({len(valid_rejections)} rejections)',
                    'draft_id': draft_obj.id,
                    'type': 'tray',
                    'rejection_count': len(valid_rejections),
                    'auto_save': True
                })
            
            else:
                return Response({
                    'success': False, 
                    'error': 'Invalid rejection_type. Must be "batch" or "tray"'
                }, status=400)

        except Exception as e:
            import traceback
            traceback.print_exc()
            return Response({
                'success': False, 
                'error': f'Auto-save error: {str(e)}',
                'auto_save': True
            }, status=500)


@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(login_required, name='dispatch')
class BrassTrayRejectionDraftAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            data = request.data if hasattr(request, 'data') else json.loads(request.body.decode('utf-8'))
            lot_id = data.get('lot_id')
            batch_id = data.get('batch_id')
            tray_rejections = data.get('tray_rejections', [])
            is_draft = data.get('is_draft', True)

            if not lot_id or not tray_rejections:
                return Response({'success': False, 'error': 'Missing lot_id or tray_rejections'}, status=400)

            # Save as draft
            draft_data = {
                'tray_rejections': tray_rejections,
                'batch_rejection': False,
                'is_draft': is_draft
            }

            # Update or create draft record
            draft_obj, created = Brass_Audit_Draft_Store.objects.update_or_create(
                lot_id=lot_id,
                draft_type='tray_rejection',
                defaults={
                    'batch_id': batch_id,
                    'user': request.user,
                    'draft_data': draft_data
                }
            )

            # ✅ NEW: Update brass_audit_draft in TotalStockModel
            TotalStockModel.objects.filter(lot_id=lot_id).update(brass_audit_draft=True)

            return Response({
                'success': True, 
                'message': 'Tray rejection draft saved successfully',
                'draft_id': draft_obj.id,
                'total_rejections': len(tray_rejections)
            })

        except Exception as e:
            import traceback
            traceback.print_exc()
            return Response({'success': False, 'error': str(e)}, status=500)
        
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def brass_get_draft_data(request):
    """Get draft data for a lot_id"""
    lot_id = request.GET.get('lot_id')
    draft_type = request.GET.get('draft_type', 'tray_rejection')
    
    if not lot_id:
        return Response({'success': False, 'error': 'Missing lot_id'}, status=400)
    
    try:
        draft_obj = Brass_Audit_Draft_Store.objects.filter(
            lot_id=lot_id,
            draft_type=draft_type
        ).first()
        
        if draft_obj:
            return Response({
                'success': True,
                'has_draft': True,
                'draft_data': draft_obj.draft_data,
                'created_at': draft_obj.created_at,
                'updated_at': draft_obj.updated_at
            })
        else:
            return Response({
                'success': True,
                'has_draft': False,
                'draft_data': None
            })
            
    except Exception as e:
        return Response({'success': False, 'error': str(e)}, status=500)


# Add this new API endpoint to your views.py

@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(login_required, name='dispatch')
class BrassClearDraftAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            data = request.data if hasattr(request, 'data') else json.loads(request.body.decode('utf-8'))
            lot_id = data.get('lot_id')
            draft_type = data.get('draft_type')  # 'batch_rejection' or 'tray_rejection'

            if not lot_id or not draft_type:
                return Response({'success': False, 'error': 'Missing lot_id or draft_type'}, status=400)

            # Delete the specific draft type
            deleted_count, _ = Brass_Audit_Draft_Store.objects.filter(
                lot_id=lot_id,
                draft_type=draft_type
            ).delete()

            return Response({
                'success': True, 
                'message': f'Cleared {draft_type} draft',
                'deleted_count': deleted_count
            })

        except Exception as e:
            import traceback
            traceback.print_exc()
            return Response({'success': False, 'error': str(e)}, status=500)


# Add this new API endpoint to your views.py

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def brass_get_all_drafts(request):
    """Get all draft data for a lot_id"""
    lot_id = request.GET.get('lot_id')
    
    if not lot_id:
        return Response({'success': False, 'error': 'Missing lot_id'}, status=400)
    
    try:
        result = {
            'success': True,
            'batch_rejection_draft': None,
            'tray_rejection_draft': None
        }
        
        # Get batch rejection draft
        batch_draft = Brass_Audit_Draft_Store.objects.filter(
            lot_id=lot_id,
            draft_type='batch_rejection'
        ).first()
        
        if batch_draft:
            result['batch_rejection_draft'] = {
                'draft_data': batch_draft.draft_data,
                'created_at': batch_draft.created_at,
                'updated_at': batch_draft.updated_at,
                'user': batch_draft.user.username if batch_draft.user else None
            }
        
        # Get tray rejection draft
        tray_draft = Brass_Audit_Draft_Store.objects.filter(
            lot_id=lot_id,
            draft_type='tray_rejection'
        ).first()
        
        if tray_draft:
            result['tray_rejection_draft'] = {
                'draft_data': tray_draft.draft_data,
                'created_at': tray_draft.created_at,
                'updated_at': tray_draft.updated_at,
                'user': tray_draft.user.username if tray_draft.user else None
            }
        
        return Response(result)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return Response({'success': False, 'error': str(e)}, status=500)



#Pick table Validation and List
@method_decorator(csrf_exempt, name='dispatch')
class PickTrayValidate_Complete_APIView(APIView):
    def post(self, request):
        try:
            data = request.data if hasattr(request, 'data') else json.loads(request.body.decode('utf-8'))
            batch_id_input = str(data.get('batch_id')).strip()
            tray_id = str(data.get('tray_id')).strip()
            
            # Get stock status parameters (optional, for enhanced validation)
            accepted_ip_stock = data.get('accepted_ip_stock', False)
            rejected_ip_stock = data.get('rejected_ip_stock', False)
            few_cases_accepted_ip_stock = data.get('few_cases_accepted_ip_stock', False)
            
            print(f"[PickTrayValidate_Complete_APIView] User entered: batch_id={batch_id_input}, tray_id={tray_id}")
            print(f"Stock status: accepted={accepted_ip_stock}, rejected={rejected_ip_stock}, few_cases={few_cases_accepted_ip_stock}")

            # ✅ FIXED: Use main TrayId table for validation
            from modelmasterapp.models import TrayId
            
            # Get all trays for this batch from main TrayId table
            all_trays = TrayId.objects.filter(
                batch_id__batch_id__icontains=batch_id_input,
                tray_quantity__gt=0
            ).exclude(
                delink_tray=True  # Exclude delinked trays
            )
            
            # ✅ CORRECTED: Apply Brass Audit workflow filtering:
            # Only Brass QC ACCEPTED trays should be validated for Brass Audit
            # Brass QC REJECTED trays should go to Recovery (NOT Brass Audit)
            filtered_trays = all_trays.filter(
                brass_rejected_tray=False,  # Only Brass QC ACCEPTED trays
                rejected_tray=False         # Also exclude Input Screening rejected trays
            )
            
            print(f"Available tray_ids for Brass Audit validation: {[t.tray_id for t in filtered_trays]}")

            exists = filtered_trays.filter(tray_id=tray_id).exists()
            print(f"Tray ID '{tray_id}' exists in Brass Audit workflow? {exists}")

            # Get additional info about the tray if it exists
            tray_info = {}
            if exists:
                tray = filtered_trays.filter(tray_id=tray_id).first()
                if tray:
                    tray_info = {
                        'rejected_tray': tray.rejected_tray,
                        'brass_rejected_tray': tray.brass_rejected_tray,
                        'tray_quantity': tray.tray_quantity,
                        'brass_top_tray': tray.brass_top_tray,
                        'tray_quantity': tray.tray_quantity,
                        'workflow_source': 'brass_qc_rejected' if tray.brass_rejected_tray else 'iqf_accepted'
                    }

            return JsonResponse({
                'success': True, 
                'exists': exists,
                'tray_info': tray_info
            })
            
        except Exception as e:
            print(f"[PickTrayValidate_Complete_APIView] Error: {str(e)}")
            return JsonResponse({'success': False, 'error': str(e)}, status=500)    
           
           
@method_decorator(csrf_exempt, name='dispatch')
class PickTrayIdList_Complete_APIView(APIView):
    def get(self, request):
        batch_id = request.GET.get('batch_id')
        stock_lot_id = request.GET.get('stock_lot_id')
        lot_id = request.GET.get('lot_id') or stock_lot_id

        if not batch_id:
            return JsonResponse({'success': False, 'error': 'Missing batch_id'}, status=400)
        if not lot_id:
            return JsonResponse({'success': False, 'error': 'Missing lot_id or stock_lot_id'}, status=400)

        print(f"✅ [PickTrayIdList_Complete_APIView] Getting tray classification for lot_id={lot_id}, batch_id={batch_id}")

        # ✅ INVESTIGATE: Check lot quantity vs tray quantities to find data inconsistency
        from modelmasterapp.models import TrayId, TotalStockModel
        
        # Get the lot record to see expected quantities
        try:
            stock_record = TotalStockModel.objects.get(lot_id=lot_id)
            print(f"🔍 [DATA DEBUG] TotalStockModel for {lot_id}:")
            
            # Safe field access with fallbacks
            try:
                print(f"   - brass_audit_physical_qty: {getattr(stock_record, 'brass_audit_physical_qty', 'N/A')}")
                print(f"   - brass_audit_accepted_qty: {getattr(stock_record, 'brass_audit_accepted_qty', 'N/A')}")
                print(f"   - brass_qc_accepted_qty: {getattr(stock_record, 'brass_qc_accepted_qty', 'N/A')}")
                print(f"   - total_stock: {getattr(stock_record, 'total_stock', 'N/A')}")
                print(f"   - brass_audit_accptance: {getattr(stock_record, 'brass_audit_accptance', 'N/A')}")
            except Exception as field_error:
                print(f"   ⚠️ Error accessing fields: {field_error}")
                
        except TotalStockModel.DoesNotExist:
            print(f"⚠️ [DATA DEBUG] No TotalStockModel found for lot_id={lot_id}")
        except Exception as e:
            print(f"⚠️ [DATA DEBUG] Error accessing TotalStockModel: {e}")

        # ✅ PRIORITY 1: Check for transferred data from Brass Audit store
        brass_audit_trays = Brass_Audit_Accepted_TrayID_Store.objects.filter(lot_id=lot_id)
        
        if brass_audit_trays.exists():
            # Use transferred data from Brass QC (persisted)
            print(f"🔄 [PickTrayIdList_Complete_APIView] Found {brass_audit_trays.count()} transferred trays from Brass QC (persisted)")
            base_transferred = brass_audit_trays
            base_source = 'brass_audit_transferred'
        else:
            # No persisted transferred data, check if lot came from IQF (rejection-recovery path)
            stock_obj = TotalStockModel.objects.filter(lot_id=lot_id).first()
            send_to_iqf = getattr(stock_obj, 'send_brass_audit_to_iqf', False) if stock_obj else False
            
            if send_to_iqf:
                # ✅ REJECTION-RECOVERY PATH: Check IQF accepted data
                from IQF.models import IQF_Accepted_TrayID_Store, IQFTrayId
                
                # Priority: Check IQF_Accepted_TrayID_Store first (persisted accepted data)
                iqf_accepted_store = IQF_Accepted_TrayID_Store.objects.filter(lot_id=lot_id)
                if iqf_accepted_store.exists():
                    print(f"🔄 [PickTrayIdList_Complete_APIView] Found {iqf_accepted_store.count()} IQF accepted trays (rejection-recovery path)")
                    base_transferred = iqf_accepted_store
                    base_source = 'iqf_accepted_store'
                else:
                    # Fallback: Check IQFTrayId table for accepted trays
                    iqf_trays = IQFTrayId.objects.filter(lot_id=lot_id, rejected_tray=False)
                    if iqf_trays.exists():
                        print(f"🔄 [PickTrayIdList_Complete_APIView] Found {iqf_trays.count()} IQF accepted trays from IQFTrayId")
                        base_transferred = iqf_trays
                        base_source = 'iqf_tray_id'
                    else:
                        base_transferred = None
                        base_source = None
            else:
                # Normal path: Check Brass QC Accepted store for final saves
                from Brass_QC.models import Brass_Qc_Accepted_TrayID_Store, Brass_Qc_Accepted_TrayScan
                qc_saved = Brass_Qc_Accepted_TrayID_Store.objects.filter(lot_id=lot_id, is_save=True)
                if qc_saved.exists():
                    print(f"🔁 [PickTrayIdList_Complete_APIView] Found {qc_saved.count()} QC accepted trays (final saved) for lot {lot_id}")
                    base_transferred = qc_saved
                    base_source = 'brass_qc_saved'
                else:
                    # If no final saved data, check for draft accepted trays (preview mode)
                    qc_draft = Brass_Qc_Accepted_TrayID_Store.objects.filter(lot_id=lot_id, is_draft=True)
                    if qc_draft.exists():
                        print(f"🔁 [PickTrayIdList_Complete_APIView] Found {qc_draft.count()} QC accepted draft trays for lot {lot_id} - using for display only")
                        base_transferred = qc_draft
                        base_source = 'brass_qc_draft'
                    else:
                        base_transferred = None
                        base_source = None

        # Ensure base_queryset is always defined to avoid UnboundLocalError in downstream logging
        base_queryset = TrayId.objects.none()

        if base_transferred:
            # Use transferred/derived QC data
            print(f"✅ [PRIORITY] Using transferred/derived Brass QC data from {base_source}: {base_transferred.count()} trays")
            accepted_total = sum(getattr(tray, 'tray_qty', getattr(tray, 'tray_quantity', 0)) for tray in base_transferred)
            print(f"✅ Total qty from source {base_source}: {accepted_total}")
        else:
            # ✅ FALLBACK: Use main TrayId table if no transferred data exists
            print(f"⚠️ [FALLBACK] No transferred data found, checking main TrayId table")
            
            # Get all trays for this lot from main TrayId table
            all_trays = TrayId.objects.filter(
                batch_id__batch_id=batch_id,
                lot_id=lot_id,
                tray_quantity__gt=0
            ).exclude(
                delink_tray=True  # Exclude delinked trays
            )
            
            print(f"[PickTrayIdList_Complete_APIView] Total trays found in TrayId table: {all_trays.count()}")
            
            # ✅ DEBUG: Show all tray statuses and calculate actual total
            actual_tray_total = 0
            # ✅ NEW: Check IPTrayId first as it is the source for Brass QC
            # We prioritize IPTrayId because TrayId table seems to have stale/incorrect data for potential top-trays
            from InputScreening.models import IPTrayId
            ip_trays = IPTrayId.objects.filter(lot_id=lot_id)
            
            if ip_trays.exists():
                print(f"✅ [DEBUG] Found {ip_trays.count()} records in IPTrayId (Input Screening), using these as primary source.")
                all_trays = ip_trays
            elif all_trays.exists():
                print(f"🔍 [DEBUG] IPTrayId empty, falling back to TrayId table (found {all_trays.count()} records)")
            else:
                print(f"⚠️ [DEBUG] No trays found in IPTrayId OR TrayId for lot {lot_id}")
            
            actual_tray_total = 0
            print(f"🔍 [DEBUG] All trays for lot {lot_id} (Model: {all_trays.model.__name__}):")
            for tray in all_trays:
                actual_tray_total += tray.tray_quantity
                # Handle different field names if needed
                is_rej = getattr(tray, 'rejected_tray', False)
                is_brass_rej = getattr(tray, 'brass_rejected_tray', False)
                print(f"   Tray: {tray.tray_id}, Qty: {tray.tray_quantity}, brass_rejected_tray: {is_brass_rej}, rejected_tray: {is_rej}")
            
            print(f"🔍 [DATA VERIFICATION] Total quantity from all trays: {actual_tray_total}")
            
            # ✅ CORRECTED: Use Brass QC rejection data to determine accepted/rejected/delinked trays
            from Brass_QC.models import Brass_QC_Rejected_TrayScan, Brass_QC_Rejection_ReasonStore

            reason_store = Brass_QC_Rejection_ReasonStore.objects.filter(lot_id=lot_id).first()
            is_lot_rejection = reason_store.batch_rejection if reason_store else False
            total_rejection_qty = reason_store.total_rejection_quantity if reason_store else 0

            print(f"🔍 [PickTrayIdList_Complete_APIView] Lot rejection check: is_lot_rejection={is_lot_rejection}, total_rejection_qty={total_rejection_qty}")

            if is_lot_rejection:
                # All trays are rejected
                base_queryset = all_trays
                accepted_total = 0
                print(f"🚨 [PickTrayIdList_Complete_APIView] LOT REJECTION: all {all_trays.count()} trays marked as REJECTED")
            else:
                # Determine which trays have explicit rejections
                rejected_scans = Brass_QC_Rejected_TrayScan.objects.filter(lot_id=lot_id)
                rejected_tray_ids = set(scan.rejected_tray_id for scan in rejected_scans)

                # Partition trays into accepted candidates and rejected trays
                accepted_candidates = [t for t in all_trays if t.tray_id not in rejected_tray_ids]
                rejected_trays = [t for t in all_trays if t.tray_id in rejected_tray_ids]

                # Compute accepted quantity target (physical - rejection qty)
                # ✅ FIX: Prioritize brass_audit_physical_qty or brass_qc_accepted_qty (Net) over brass_physical_qty calculation
                brass_audit_qty = getattr(stock_record, 'brass_audit_physical_qty', 0)
                brass_qc_qty = getattr(stock_record, 'brass_qc_accepted_qty', 0)
                
                if brass_audit_qty and brass_audit_qty > 0:
                    accepted_qty_target = brass_audit_qty
                    print(f"🔍 [PickTrayIdList_Complete_APIView] Using brass_audit_physical_qty (Net): {accepted_qty_target}")
                elif brass_qc_qty and brass_qc_qty > 0:
                    accepted_qty_target = brass_qc_qty
                    print(f"🔍 [PickTrayIdList_Complete_APIView] Using brass_qc_accepted_qty (Net): {accepted_qty_target}")
                else:
                    # Fallback to calculation
                    brass_physical_qty = getattr(stock_record, 'brass_physical_qty', 0) or 0
                    accepted_qty_target = brass_physical_qty - (total_rejection_qty or 0)
                    print(f"🔍 [PickTrayIdList_Complete_APIView] Calculated target from brass_physical_qty: {accepted_qty_target}")

                print(f"🔍 [PickTrayIdList_Complete_APIView] Quantity analysis: target={accepted_qty_target} (Rejections: {total_rejection_qty})")

                # Try to detect an explicitly chosen QC top-tray (draft or saved) and prefer it for partial acceptance
                qc_top_tray_id = None

                # ✅ NEW: Prefer any explicit Brass Audit admin top-tray selection (BrassAuditTrayId.top_tray)
                try:
                    ba_top = BrassAuditTrayId.objects.filter(lot_id=lot_id, top_tray=True).first()
                    if ba_top and getattr(ba_top, 'tray_id', None):
                        qc_top_tray_id = ba_top.tray_id
                        print(f"🔍 [PREFERRED TOP TRAY] Found Brass Audit admin top-tray: {qc_top_tray_id}")
                except Exception:
                    pass

                try:
                    from Brass_QC.models import Brass_TopTray_Draft_Store
                    top_draft = Brass_TopTray_Draft_Store.objects.filter(lot_id=lot_id).first()
                    if top_draft and getattr(top_draft, 'tray_id', None):
                        # Only override if we don't already have a Brass Audit admin top tray
                        if not qc_top_tray_id:
                            qc_top_tray_id = top_draft.tray_id
                            print(f"🔍 [PREFERRED TOP TRAY] Found QC top-tray draft: {qc_top_tray_id}")
                except Exception:
                    qc_top_tray_id = qc_top_tray_id

                # Select largest trays first to meet target, allow one partial top-tray if needed
                accepted_candidates_sorted = sorted(accepted_candidates, key=lambda x: x.tray_quantity or 0, reverse=True)
                # Precompute id-sorted order for deterministic tie-breaker (choose smallest tray_id when needed)
                accepted_candidates_by_id = sorted(accepted_candidates, key=lambda x: x.tray_id or '')
                # If QC explicitly selected a top tray, move it to the end so we can treat it as the partial top-tray if needed
                if qc_top_tray_id:
                    preferred_tray_obj = next((t for t in accepted_candidates_sorted if t.tray_id == qc_top_tray_id), None)
                    if preferred_tray_obj:
                        accepted_candidates_sorted = [t for t in accepted_candidates_sorted if t.tray_id != qc_top_tray_id] + [preferred_tray_obj]

                final_accepted = []  # list of TrayId objects used for acceptance (may include a partial one)
                partial_qty_map = {}  # tray_id -> accepted_qty for partial acceptance
                running = 0

                # Use a designated-top strategy: prefer QC-chosen top tray (if any), else pick the smallest tray_id as designated top.
                # NEW STRATEGY: Start with the designated top tray to ensure it's included.
                # Then fill the rest with other trays (largest first).
                
                final_accepted = []  # list of TrayId objects used for acceptance
                partial_qty_map = {}  # tray_id -> accepted_qty for partial acceptance
                running = 0

                # Determine designated top tray id (prefer QC draft/persistent choice)
                designated_top_id = None
                if qc_top_tray_id and any(t.tray_id == qc_top_tray_id for t in accepted_candidates):
                    designated_top_id = qc_top_tray_id
                else:
                    # Default: use smallest tray_id among candidates
                    if accepted_candidates_by_id:
                        designated_top_id = accepted_candidates_by_id[0].tray_id

                designated_top_obj = next((t for t in accepted_candidates_sorted if t.tray_id == designated_top_id), None) if designated_top_id else None

                # 1. ALWAYS add the designated top tray first (if it exists)
                if designated_top_obj:
                    q = int(designated_top_obj.tray_quantity or 0)
                    
                    if q >= accepted_qty_target:
                        # Top tray alone is enough (or more than enough)
                        final_accepted.append(designated_top_obj)
                        partial_qty_map[designated_top_obj.tray_id] = int(accepted_qty_target)
                        running += accepted_qty_target
                        print(f"🔍 [Categorization] {designated_top_obj.tray_id} (TOP): FULL/PARTIAL ACCEPTED = {accepted_qty_target} (Target met)")
                    else:
                        # Top tray is used fully, but we need more
                        final_accepted.append(designated_top_obj)
                        running += q
                        print(f"🔍 [Categorization] {designated_top_obj.tray_id} (TOP): FULL ACCEPTED = {q} (running total: {running})")


                # 2. Fill with other trays if we still need more quantity
                remaining_needed = accepted_qty_target - running
                
                if remaining_needed > 0:
                    # Iterate through others (largest first)
                    for tray in accepted_candidates_sorted:
                        # Skip the top tray we already added
                        if designated_top_obj and tray.tray_id == designated_top_obj.tray_id:
                            continue
                        
                        q = int(tray.tray_quantity or 0)
                        
                        if running >= accepted_qty_target:
                            break

                        if q <= remaining_needed:
                            # Take full tray
                            final_accepted.append(tray)
                            running += q
                            remaining_needed -= q
                            print(f"🔍 [Categorization] {tray.tray_id}: ACCEPTED = {q} (running total: {running})")
                        else:
                            # Take partial tray to finish
                            final_accepted.append(tray)
                            partial_qty_map[tray.tray_id] = int(remaining_needed)
                            running += remaining_needed
                            remaining_needed = 0
                            print(f"🔍 [Categorization] {tray.tray_id}: PARTIAL ACCEPTED = {partial_qty_map[tray.tray_id]} (running total: {running})")
                            break

                # Determine delinked trays (accepted_candidates not in final_accepted)
                delinked = [t for t in accepted_candidates_sorted if t.tray_id not in [fa.tray_id for fa in final_accepted]]

                accepted_total = sum(partial_qty_map.get(t.tray_id, t.tray_quantity) for t in final_accepted)
                # base_queryset for counts and tray id listing should reference a QuerySet
                base_queryset = TrayId.objects.filter(tray_id__in=[t.tray_id for t in final_accepted])

                print(f"✅ [PickTrayIdList_Complete_APIView] Brass QC ACCEPTED trays for Brass Audit: {len(final_accepted)}")
                print(f"✅ [QUANTITY CHECK] Total quantity from accepted trays: {accepted_total}")
                print(f"Brass QC ACCEPTED trays: {len(final_accepted)}")
                print(f"Brass QC REJECTED trays (go to Recovery): {len(rejected_trays)}")

                expected_qty = (getattr(stock_record, 'brass_audit_physical_qty', 0) or getattr(stock_record, 'brass_qc_accepted_qty', 0) or getattr(stock_record, 'total_stock', 0))
                if expected_qty and expected_qty != accepted_total:
                    print(f"⚠️ [INCONSISTENCY DETECTED] Expected lot qty: {expected_qty}, Actual tray total: {accepted_total}")
                    print(f"   This explains why frontend shows {expected_qty} but backend returns {accepted_total}")
        print(f"Tray IDs shown: {list(base_queryset.values_list('tray_id', flat=True))}")

        data = []
        row_counter = 1

        if base_transferred is not None:
            # ✅ Handle transferred/derived data from Brass Audit or Brass QC stores
            print(f"✅ Processing transferred/derived Brass QC data for display from source={base_source}")
            
            # ✅ UPDATED: Respect the top_tray flag from BrassAuditTrayId instead of using heuristics
            trays_list = list(base_transferred.order_by('tray_id'))
            top_tray = None
            other_trays = []

            # ✅ PRIORITY: Check for top_tray flag from BrassAuditTrayId database records
            audit_top_tray_obj = None  # Store the BrassAuditTrayId object with updated qty
            try:
                for tray in trays_list:
                    tray_id = getattr(tray, 'tray_id', None)
                    if tray_id:
                        # Query BrassAuditTrayId to check if this tray has top_tray=True
                        audit_tray = BrassAuditTrayId.objects.filter(lot_id=lot_id, tray_id=tray_id, top_tray=True).first()
                        if audit_tray:
                            top_tray = tray
                            audit_top_tray_obj = audit_tray  # Store for updated qty retrieval
                            print(f"🔍 [DATABASE TOP TRAY] Found top_tray=True in BrassAuditTrayId for {tray_id} (updated qty: {audit_tray.tray_quantity})")
                            break
            except Exception as e:
                print(f"⚠️ [DATABASE TOP TRAY] Error checking BrassAuditTrayId: {e}")

            # If no top_tray found from database, use the tray with smallest quantity
            if not top_tray:
                print(f"⚠️ [TOP TRAY FALLBACK] No top_tray flag found, selecting tray with smallest quantity")
                smallest_tray = None
                for tray in trays_list:
                    tray_qty = int(getattr(tray, 'tray_qty', getattr(tray, 'tray_quantity', 0)) or 0)
                    if smallest_tray is None or tray_qty < int(getattr(smallest_tray, 'tray_qty', getattr(smallest_tray, 'tray_quantity', 0)) or 0):
                        smallest_tray = tray
                top_tray = smallest_tray

            # Build other_trays list (all trays except top_tray)
            if top_tray:
                other_trays = [t for t in trays_list if getattr(t, 'tray_id', None) != getattr(top_tray, 'tray_id', None)]
            else:
                # Fallback: if still no top_tray, use alphabetical highest ID
                for tray in trays_list:
                    if not top_tray or getattr(tray, 'tray_id', '') > getattr(top_tray, 'tray_id', ''):
                        if top_tray:
                            other_trays.append(top_tray)
                        top_tray = tray
                    else:
                        other_trays.append(tray)

            # Add top tray first
            if top_tray:
                # ✅ FIXED: Use updated quantity from BrassAuditTrayId if available
                if audit_top_tray_obj:
                    top_tray_qty = int(audit_top_tray_obj.tray_quantity or 0)
                    print(f"   ✅ Using UPDATED qty from BrassAuditTrayId: {top_tray_qty}")
                else:
                    top_tray_qty = int(getattr(top_tray, 'tray_qty', getattr(top_tray, 'tray_quantity', 0)) or 0)
                    print(f"   ⚠️ Using original qty from base_transferred: {top_tray_qty}")
                
                data.append({
                    's_no': row_counter,
                    'tray_id': getattr(top_tray, 'tray_id', None),
                    'tray_quantity': top_tray_qty,
                    'position': row_counter - 1,
                    'is_top_tray': True,
                    'rejected_tray': False,
                    'brass_rejected_tray': False,
                    'delink_tray': False,
                    'rejection_details': [],
                    'top_tray': True
                })
                row_counter += 1
                print(f"   ✅ Top Tray (transferred): {getattr(top_tray, 'tray_id', None)} = {top_tray_qty}")

            # Add the other trays
            for tray in sorted(other_trays, key=lambda x: getattr(x, 'tray_id', None)):
                tray_id = getattr(tray, 'tray_id', None)
                
                # ✅ FIXED: Check if this tray still exists in BrassAuditTrayId (skip if delinked)
                try:
                    audit_tray = BrassAuditTrayId.objects.filter(lot_id=lot_id, tray_id=tray_id).first()
                    if audit_tray:
                        other_tray_qty = int(audit_tray.tray_quantity or 0)
                    else:
                        # ✅ CRITICAL: If tray doesn't exist in BrassAuditTrayId, it was delinked - skip it!
                        print(f"   ⚠️ SKIPPING delinked tray: {tray_id} (not found in BrassAuditTrayId)")
                        continue
                except Exception as e:
                    print(f"   ⚠️ Error checking BrassAuditTrayId for {tray_id}: {e}")
                    other_tray_qty = int(getattr(tray, 'tray_qty', getattr(tray, 'tray_quantity', 0)) or 0)
                
                data.append({
                    's_no': row_counter,
                    'tray_id': tray_id,
                    'tray_quantity': other_tray_qty,
                    'position': row_counter - 1,
                    'is_top_tray': False,
                    'rejected_tray': False,
                    'brass_rejected_tray': False,
                    'delink_tray': False,
                    'rejection_details': [],
                    'top_tray': False
                })
                row_counter += 1
                print(f"   ✅ Other Tray (transferred): {tray_id} = {other_tray_qty}")
                
        else:
            # ✅ FALLBACK: Handle main TrayId table data using Brass QC categorization
            print(f"⚠️ Processing main TrayId table data for display")

            # Helper to create tray payload with optional overrides for rejection/delink flags
            def create_tray_data(tray_obj, is_top=False, is_rejected=None, is_delink=None, rejection_qty=None):
                nonlocal row_counter
                # Determine flags (allow override when we derived categories)
                rejected_flag = is_rejected if is_rejected is not None else getattr(tray_obj, 'rejected_tray', False)
                brass_rejected_flag = getattr(tray_obj, 'brass_rejected_tray', False)
                delink_flag = is_delink if is_delink is not None else getattr(tray_obj, 'delink_tray', False)

                # Attach rejection details if rejected based on Audit scans
                rejection_details = []
                if rejected_flag:
                    rejected_scans = Brass_Audit_Rejected_TrayScan.objects.filter(lot_id=lot_id, rejected_tray_id=tray_obj.tray_id)
                    for scan in rejected_scans:
                        rejection_details.append({
                            'rejected_quantity': scan.rejected_tray_quantity,
                            'rejection_reason': scan.rejection_reason.rejection_reason if scan.rejection_reason else 'Unknown',
                            'rejection_reason_id': scan.rejection_reason.rejection_reason_id if scan.rejection_reason else None,
                            'user': scan.user.username if scan.user else None
                        })

                # Compute display quantity: for rejected trays, use provided rejection_qty if supplied (used for partial rejections)
                display_quantity = rejection_qty if (rejection_qty is not None) else getattr(tray_obj, 'tray_quantity', 0)

                return {
                    's_no': row_counter,
                    'tray_id': tray_obj.tray_id,
                    'tray_quantity': display_quantity,
                    'position': row_counter - 1,
                    'is_top_tray': is_top,
                    'rejected_tray': rejected_flag,
                    'brass_rejected_tray': brass_rejected_flag,
                    'delink_tray': delink_flag,
                    'rejection_details': rejection_details,
                    'top_tray': getattr(tray_obj, 'brass_top_tray', False),
                }

            # If we derived final_accepted/final_rejected/delinked (from earlier categorization), use them
            if 'final_accepted' in locals():
                # final_accepted is a list of TrayId objects (accepted), partial_qty_map may override qtys
                # Prefer partial accepted tray as top if present
                partial_top_id = None
                if 'partial_qty_map' in locals() and partial_qty_map:
                    # pick any partial (there will be at most one)
                    partial_top_id = next(iter(partial_qty_map.keys()))

                top_tray = None
                if partial_top_id:
                    top_tray = next((t for t in final_accepted if t.tray_id == partial_top_id), None)
                if not top_tray:
                    top_tray = next((t for t in final_accepted if getattr(t, 'brass_top_tray', False)), None)
                if not top_tray and final_accepted:
                    top_tray = final_accepted[0]

                # Add top tray (use partial qty if present)
                if top_tray:
                    qty_override = partial_qty_map.get(top_tray.tray_id) if 'partial_qty_map' in locals() else None
                    data.append(create_tray_data(top_tray, is_top=True, is_rejected=False, is_delink=False, rejection_qty=qty_override))
                    row_counter += 1

                # Add other accepted trays
                for tray in final_accepted:
                    if top_tray and tray.tray_id == top_tray.tray_id:
                        continue
                    qty_override = partial_qty_map.get(tray.tray_id) if 'partial_qty_map' in locals() else None
                    data.append(create_tray_data(tray, is_top=False, is_rejected=False, is_delink=False, rejection_qty=qty_override))
                    row_counter += 1

                # Add explicitly rejected trays
                for tray in rejected_trays:
                    data.append(create_tray_data(tray, is_top=False, is_rejected=True))
                    row_counter += 1

                # Add delinked trays
                for tray in delinked:
                    data.append(create_tray_data(tray, is_top=False, is_rejected=False, is_delink=True))
                    row_counter += 1

            else:
                # No derived categorization available; fall back to existing behaviour
                top_tray = base_queryset.filter(brass_top_tray=True).first()
                other_trays = base_queryset.exclude(pk=top_tray.pk if top_tray else None).order_by('id')

                if top_tray:
                    data.append(create_tray_data(top_tray, is_top=True))
                    row_counter += 1

                for tray in other_trays:
                    data.append(create_tray_data(tray, is_top=False))
                    row_counter += 1

        print(f"✅ [PickTrayIdList_Complete_APIView] Total trays returned for Brass Audit workflow: {len(data)}")

        # Provide detailed summary based on data source
        if base_transferred is not None:
            # Summary for transferred data
            print(f"📊 [SUMMARY] Showing {len(data)} transferred trays from {base_source}")
            brass_qc_accepted = len(data)
            brass_qc_rejected = 0  # Transferred/draft data is considered accepted for display
        else:
            # Summary for main TrayId data (fallback)
            # Summary for main TrayId data (fallback)
            try:
                # Try filtering with brass_rejected_tray (TrayId model)
                brass_qc_accepted = all_trays.filter(brass_rejected_tray=False, rejected_tray=False).count() if 'all_trays' in locals() else 0
                brass_qc_rejected = all_trays.filter(brass_rejected_tray=True).count() if 'all_trays' in locals() else 0
            except Exception:
                # If field doesn't exist (IPTrayId model), use rejected_tray only
                brass_qc_accepted = all_trays.filter(rejected_tray=False).count() if 'all_trays' in locals() else 0
                brass_qc_rejected = all_trays.filter(rejected_tray=True).count() if 'all_trays' in locals() else 0
        
        summary = {
            'total_trays_for_brass_audit': base_queryset.count(),
            'brass_qc_accepted_trays': brass_qc_accepted,
            'brass_qc_rejected_trays_to_recovery': brass_qc_rejected,
            'tray_ids_shown': list(base_queryset.values_list('tray_id', flat=True)),
            'filter_applied': 'brass_qc_accepted_only'
        }

        return JsonResponse({
            'success': True, 
            'trays': data,
            'rejection_summary': summary
        })
        
#After SaveIPCHeckbox tray validation and list
# ✅ CORRECTED: AfterCheckTrayValidate_Complete_APIView - Use BrassTrayId and remove False filtering
@method_decorator(csrf_exempt, name='dispatch')
class AfterCheckTrayValidate_Complete_APIView(APIView):
    def post(self, request):
        try:
            data = request.data if hasattr(request, 'data') else json.loads(request.body.decode('utf-8'))
            batch_id_input = str(data.get('batch_id')).strip()
            tray_id = str(data.get('tray_id')).strip()
            
            # Get Brass Audit status parameters
            brassQcAccptance = data.get('brass_audit_accptance', False)
            brassQcRejection = data.get('brass_audit_rejection', False)
            brassQcFewCases = data.get('brass_qc_few_cases_accptance', False)

            print(f"✅ [AfterCheckTrayValidate] User entered: batch_id={batch_id_input}, tray_id={tray_id}")
            print(f"Brass Audit status: accptance={brassQcAccptance}, rejection={brassQcRejection}, few_cases={brassQcFewCases}")

            # ✅ FIXED: Use main TrayId table for validation (same as other endpoints)
            from modelmasterapp.models import TrayId
            
            # Get all trays for this batch from main TrayId table
            all_trays = TrayId.objects.filter(
                batch_id__batch_id__icontains=batch_id_input,
                tray_quantity__gt=0
            ).exclude(
                delink_tray=True  # Exclude delinked trays
            )
            
            # ✅ CORRECTED: Apply Brass Audit workflow filtering:
            # Only Brass QC ACCEPTED trays should be validated for Brass Audit
            # Brass QC REJECTED trays should go to Recovery (NOT Brass Audit)
            filtered_trays = all_trays.filter(
                brass_rejected_tray=False,  # Only Brass QC ACCEPTED trays
                rejected_tray=False         # Also exclude Input Screening rejected trays
            )
            
            print(f"✅ [AfterCheckTrayValidate] Available tray_ids for Brass Audit validation: {[t.tray_id for t in filtered_trays[:10]]}...")

            exists = filtered_trays.filter(tray_id=tray_id).exists()
            print(f"🔍 [AfterCheckTrayValidate] Tray ID '{tray_id}' exists in Brass Audit workflow? {exists}")

            # Get additional info about the tray if it exists
            tray_info = {}
            if exists:
                tray = filtered_trays.filter(tray_id=tray_id).first()
                if tray:
                    tray_info = {
                        'rejected_tray': tray.rejected_tray,
                        'brass_rejected_tray': tray.brass_rejected_tray,
                        'tray_quantity': tray.tray_quantity,
                        'brass_top_tray': tray.brass_top_tray,
                        'workflow_source': 'brass_qc_rejected' if tray.brass_rejected_tray else 'iqf_accepted',
                        'data_source': 'TrayId_MainTable'
                    }

            return JsonResponse({
                'success': True, 
                'exists': exists,
                'tray_info': tray_info,
                'data_source': 'TrayId_MainTable',
                'workflow_filtering_applied': True
            })
            
        except Exception as e:
            print(f"❌ [AfterCheckTrayValidate_Complete_APIView] Error: {str(e)}")
            import traceback
            traceback.print_exc()
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
         
         
           
# ✅ CORRECTED: AfterCheckPickTrayIdList_Complete_APIView - Use BrassTrayId and remove False filtering
@method_decorator(csrf_exempt, name='dispatch')
class AfterCheckPickTrayIdList_Complete_APIView(APIView):
    def get(self, request):
        batch_id = request.GET.get('batch_id')
        stock_lot_id = request.GET.get('stock_lot_id')
        lot_id = request.GET.get('lot_id') or stock_lot_id
        brass_audit_accptance = request.GET.get('brass_audit_accptance', 'false').lower() == 'true'
        brass_audit_rejection = request.GET.get('brass_audit_rejection', 'false').lower() == 'true'
        brass_audit_few_cases_accptance = request.GET.get('brass_audit_few_cases_accptance', 'false').lower() == 'true'

        if not batch_id:
            return JsonResponse({'success': False, 'error': 'Missing batch_id'}, status=400)
        if not lot_id:
            return JsonResponse({'success': False, 'error': 'Missing lot_id or stock_lot_id'}, status=400)

        print(f"✅ [AfterCheckPickTrayIdList_Complete_APIView] Getting tray classification for lot_id={lot_id}, batch_id={batch_id}")
        print(f"Brass Audit status: accptance={brass_audit_accptance}, rejection={brass_audit_rejection}, few_cases={brass_audit_few_cases_accptance}")

        from modelmasterapp.models import TrayId, TotalStockModel
        
        # Get the lot record to see expected quantities
        try:
            stock_record = TotalStockModel.objects.get(lot_id=lot_id)
            print(f"🔍 [AfterCheck DATA DEBUG] TotalStockModel for {lot_id}:")
            print(f"   - brass_audit_physical_qty: {getattr(stock_record, 'brass_audit_physical_qty', 'N/A')}")
            print(f"   - brass_audit_accepted_qty: {getattr(stock_record, 'brass_audit_accepted_qty', 'N/A')}")
            print(f"   - brass_qc_accepted_qty: {getattr(stock_record, 'brass_qc_accepted_qty', 'N/A')}")
            print(f"   - total_stock: {getattr(stock_record, 'total_stock', 'N/A')}")
        except TotalStockModel.DoesNotExist:
            print(f"⚠️ [AfterCheck DATA DEBUG] No TotalStockModel found for lot_id={lot_id}")
            stock_record = None
        except Exception as e:
            print(f"⚠️ [AfterCheck DATA DEBUG] Error accessing TotalStockModel: {e}")
            stock_record = None

        # ✅ FIXED: Handle rejected lots - fetch from Brass_Audit_Rejected_TrayScan
        if brass_audit_rejection:
            print(f"🔴 [AfterCheck REJECTION] Fetching rejected trays for lot_id={lot_id}")
            
            # Get rejected trays from Brass_Audit_Rejected_TrayScan
            rejected_scans = Brass_Audit_Rejected_TrayScan.objects.filter(lot_id=lot_id).order_by('id')
            print(f"   Found {rejected_scans.count()} rejected tray scans")
            
            # Build data from rejected scans
            data = []
            row_counter = 1
            
            # Group by tray_id to get total rejected quantity per tray
            from collections import defaultdict
            tray_reject_data = defaultdict(lambda: {'qty': 0, 'reasons': []})
            
            for scan in rejected_scans:
                tray_id = scan.rejected_tray_id
                tray_reject_data[tray_id]['qty'] += scan.rejected_tray_quantity or 0
                tray_reject_data[tray_id]['reasons'].append({
                    'rejection_reason': scan.rejection_reason.rejection_reason if scan.rejection_reason else 'Unknown',
                    'rejection_quantity': scan.rejected_tray_quantity,
                    'user': scan.user.username if scan.user else 'Unknown'
                })
            
            # Create response data
            for tray_id, info in sorted(tray_reject_data.items()):
                data.append({
                    's_no': row_counter,
                    'tray_id': tray_id,
                    'tray_quantity': info['qty'],
                    'position': row_counter - 1,
                    'is_top_tray': False,
                    'rejected_tray': True,
                    'brass_rejected_tray': True,
                    'delink_tray': False,
                    'rejection_details': info['reasons'],
                    'top_tray': False
                })
                row_counter += 1
                print(f"   ❌ Rejected Tray: {tray_id} = {info['qty']} pcs")
            
            print(f"✅ [AfterCheck REJECTION] Returning {len(data)} rejected trays")
            
            return JsonResponse({
                'success': True,
                'data': data,
                'summary': {
                    'total_accepted_trays': 0,
                    'accepted_tray_ids': [],
                    'total_rejected_trays': len(data),
                    'rejected_tray_ids': list(tray_reject_data.keys()),
                    'shortage_rejections': 0,
                    'filter_applied': 'rejected_only'
                }
            })

        # For accepted/few_cases, delegate to PickTrayIdList_Complete_APIView
        try:
            pick_view = PickTrayIdList_Complete_APIView()
            return pick_view.get(request)
        except Exception as e:
            print(f"❌ [AfterCheckPickTrayIdList_Complete_APIView] Error delegating to Pick view: {e}")
            import traceback; traceback.print_exc()
            return JsonResponse({'success': False, 'error': str(e)}, status=500)


        # ✅ INVESTIGATE: Check if expected lot quantity matches actual tray quantities
        try:
            expected_qty = stock_record.brass_audit_physical_qty or stock_record.brass_qc_accepted_qty or stock_record.total_stock
            if expected_qty and expected_qty != accepted_total:
                print(f"⚠️ [AfterCheck INCONSISTENCY DETECTED] Expected lot qty: {expected_qty}, Actual tray total: {accepted_total}")
            else:
                print(f"✅ [AfterCheck QUANTITY MATCH] Expected qty: {expected_qty}, Actual tray total: {accepted_total}")
        except:
            pass

        # Apply Brass Audit status filtering if any status is provided
        has_brass_qc_status = brass_audit_accptance or brass_audit_rejection or brass_audit_few_cases_accptance
        
        if has_brass_qc_status:
            # Apply additional filtering based on Brass Audit status
            if brass_audit_accptance and not brass_audit_few_cases_accptance:
                # Show only accepted trays (for completed Brass Audit)
                queryset = base_queryset  # All valid workflow trays are considered "accepted" for view
            elif brass_audit_rejection and not brass_audit_few_cases_accptance:
                # Show only rejected trays (would be empty since these are workflow trays)
                queryset = base_queryset.none()  # No workflow trays should be "rejected" from the start
            elif brass_audit_few_cases_accptance:
                # Show all workflow trays
                queryset = base_queryset
            else:
                queryset = base_queryset
        else:
            # No Brass Audit status filtering, show all workflow-valid trays
            queryset = base_queryset

        # Find top tray based on brass_top_tray field from TrayId table
        top_tray = queryset.filter(brass_top_tray=True).first()
        other_trays = queryset.exclude(pk=top_tray.pk if top_tray else None).order_by('id')

        data = []
        row_counter = 1

        def create_tray_data(tray_obj, is_top=False):
            nonlocal row_counter
            
            # Get rejection details if tray was rejected in Brass Audit
            rejection_details = []
            if hasattr(tray_obj, 'brass_rejected_tray') and getattr(tray_obj, 'brass_rejected_tray', False):
                # This would be trays rejected in Brass QC that came to Brass Audit
                rejected_scans = Brass_Audit_Rejected_TrayScan.objects.filter(
                    lot_id=lot_id,
                    rejected_tray_id=tray_obj.tray_id
                )
                for scan in rejected_scans:
                    rejection_details.append({
                        'rejected_quantity': scan.rejected_tray_quantity,
                        'rejection_reason': scan.rejection_reason.rejection_reason if scan.rejection_reason else 'Unknown',
                        'rejection_reason_id': scan.rejection_reason.rejection_reason_id if scan.rejection_reason else None,
                        'user': scan.user.username if scan.user else None
                    })
                    
            return {
                's_no': row_counter,
                'tray_id': tray_obj.tray_id,
                'tray_quantity': tray_obj.tray_quantity,
                'position': row_counter - 1,
                'is_top_tray': is_top,
                'rejected_tray': getattr(tray_obj, 'rejected_tray', False),
                'brass_rejected_tray': getattr(tray_obj, 'brass_rejected_tray', False),
                'delink_tray': getattr(tray_obj, 'delink_tray', False),
                'rejection_details': rejection_details,
                'top_tray': getattr(tray_obj, 'brass_top_tray', False),
                'workflow_source': 'brass_qc_rejected' if getattr(tray_obj, 'brass_rejected_tray', False) else 'iqf_accepted'
            }

        # Add top tray first if exists
        if top_tray:
            data.append(create_tray_data(top_tray, is_top=True))
            row_counter += 1
            
        # Add other trays
        for tray in other_trays:
            data.append(create_tray_data(tray, is_top=False))
            row_counter += 1

        print(f"✅ [AfterCheckPickTrayIdList_Complete_APIView] Total trays returned for Brass Audit workflow: {len(data)}")

        # Provide detailed summary based on TrayId table
        brass_qc_accepted = all_trays.filter(brass_rejected_tray=False, rejected_tray=False).count()
        brass_qc_rejected = all_trays.filter(brass_rejected_tray=True).count()
        
        rejection_summary = {
            'total_trays_for_brass_audit': base_queryset.count(),
            'brass_qc_accepted_trays': brass_qc_accepted,
            'brass_qc_rejected_trays_to_recovery': brass_qc_rejected,
            'tray_ids_shown': list(queryset.values_list('tray_id', flat=True)),
            'filter_applied': f'brass_qc_accepted_only_with_status_{"enabled" if has_brass_qc_status else "disabled"}',
            'data_source': 'TrayId_MainTable'  # ✅ Updated: Indicate proper data source
        }

        return JsonResponse({
            'success': True,
            'trays': data,
            'rejection_summary': rejection_summary
        })
        

class BrassAuditRejectTableView(APIView):
    renderer_classes = [TemplateHTMLRenderer]
    template_name = 'BrassAudit/BrassAudit_RejectTable.html'

    def get(self, request):
        user = request.user

        # Handle sorting parameters
        sort = request.GET.get('sort')
        order = request.GET.get('order', 'asc')  # Default to ascending
        
        # Field mapping for proper model field references
        sort_field_mapping = {
            'serial_number': 'lot_id',  # Use lot_id for serial number sorting
            'brass_audit_rejected_last_process_date_time': 'brass_audit_last_process_date_time',
            'plating_stk_no': 'batch_id__plating_stk_no',
            'polishing_stk_no': 'batch_id__polishing_stk_no',
            'plating_color': 'batch_id__plating_color',
            'polish_finish': 'batch_id__polish_finish',
            'vendor_location': 'batch_id__vendor_internal',  # Simplified to vendor field
            'tray_capacity': 'batch_id__tray_capacity',
            'no_of_trays': 'batch_id__tray_capacity',  # Approximate mapping
            'total_rejection_quantity': 'brass_audit_rejection_total_qty'
        }

        # Subquery for total rejection quantity
        brass_audit_rejection_total_qty_subquery = Brass_Audit_Rejection_ReasonStore.objects.filter(
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
            brass_audit_rejection_total_qty=brass_audit_rejection_total_qty_subquery,
        ).filter(
            Q(brass_audit_rejection=True) | Q(brass_audit_few_cases_accptance=True)
        
        )
        
        # Apply sorting if requested
        if sort and sort in sort_field_mapping:
            field = sort_field_mapping[sort]
            if order == 'desc':
                field = '-' + field
            queryset = queryset.order_by(field)
        else:
            queryset = queryset.order_by('-brass_audit_last_process_date_time', '-lot_id')

        print(f"📊 Found {queryset.count()} Brass Audit rejected records")
        print("All lot_ids in Brass Audit reject queryset:", list(queryset.values_list('lot_id', flat=True)))

        # Pagination
        page_number = request.GET.get('page', 1)
        paginator = Paginator(queryset, 10)
        page_obj = paginator.get_page(page_number)

        master_data = []
        for stock_obj in page_obj.object_list:
            batch = stock_obj.batch_id
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
                'Moved_to_D_Picker': getattr(batch, 'Moved_to_D_Picker', None),
                'Draft_Saved': getattr(batch, 'Draft_Saved', None),
                'plating_stk_no': getattr(batch, 'plating_stk_no', None),
                'polishing_stk_no': getattr(batch, 'polishing_stk_no', None),
                'category': getattr(batch, 'category', None),
                'lot_id': stock_obj.lot_id,
                'stock_lot_id': stock_obj.lot_id,
                'last_process_module': stock_obj.last_process_module,
                'next_process_module': stock_obj.next_process_module,
                'brass_audit_rejection': stock_obj.brass_audit_rejection,
                'brass_audit_few_cases_accptance': stock_obj.brass_audit_few_cases_accptance,
                'brass_audit_rejection_total_qty': stock_obj.brass_audit_rejection_total_qty,
                'brass_audit_last_process_date_time': stock_obj.brass_audit_last_process_date_time,
                'brass_audit_missing_qty': stock_obj.brass_audit_missing_qty,
                'brass_audit_physical_qty': stock_obj.brass_audit_physical_qty,
            }
            master_data.append(data)

        print(f"[BrassAuditRejectTableView] Total master_data records: {len(master_data)}")

        # Enhanced data processing for rejection
        for data in master_data:
            stock_lot_id = data.get('stock_lot_id')
            # Check if tray exists in BrassAuditTrayId
            tray_exists = BrassAuditTrayId.objects.filter(lot_id=stock_lot_id).exists()
            data['tray_id_in_trayid'] = tray_exists

            # Add lot rejection remarks
            lot_rejected_comment = ""
            if stock_lot_id:
                reason_store = Brass_Audit_Rejection_ReasonStore.objects.filter(lot_id=stock_lot_id).first()
                if reason_store:
                    lot_rejected_comment = reason_store.lot_rejected_comment or ""
            data['lot_rejected_comment'] = lot_rejected_comment
            
            # Get batch rejection and reason letters
            first_letters = []
            data['batch_rejection'] = False

            if stock_lot_id:
                try:
                    rejection_record = Brass_Audit_Rejection_ReasonStore.objects.filter(
                        lot_id=stock_lot_id
                    ).first()
                    if rejection_record:
                        data['batch_rejection'] = rejection_record.batch_rejection
                        data['brass_audit_rejection_total_qty'] = rejection_record.total_rejection_quantity
                        reasons = rejection_record.rejection_reason.all()
                        first_letters = [r.rejection_reason.strip()[0].upper() for r in reasons if r.rejection_reason]
                        print(f"✅ Found rejection for {stock_lot_id}: {rejection_record.total_rejection_quantity}")
                    else:
                        if 'brass_audit_rejection_total_qty' not in data or not data['brass_audit_rejection_total_qty']:
                            data['brass_audit_rejection_total_qty'] = 0
                        print(f"⚠️ No rejection record found for {stock_lot_id}")
                except Exception as e:
                    print(f"❌ Error getting rejection for {stock_lot_id}: {str(e)}")
                    data['brass_audit_rejection_total_qty'] = data.get('brass_audit_rejection_total_qty', 0)
            else:
                data['brass_audit_rejection_total_qty'] = 0
                print(f"❌ No stock_lot_id for batch {data.get('batch_id')}")

            data['rejection_reason_letters'] = first_letters

            # Calculate number of trays
            total_stock = data.get('brass_audit_rejection_total_qty', 0)
            tray_capacity = data.get('tray_capacity', 0)
            data['vendor_location'] = f"{data.get('vendor_internal', '')}_{data.get('location__location_name', '')}"

            if tray_capacity > 0 and total_stock > 0:
                data['no_of_trays'] = math.ceil(total_stock / tray_capacity)
            else:
                data['no_of_trays'] = 0

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

        print("✅ Brass Audit Reject data processing completed")
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
def brass_get_rejection_details(request):
    lot_id = request.GET.get('lot_id')
    if not lot_id:
        return Response({'success': False, 'error': 'Missing lot_id'}, status=400)
    try:
        reason_store = Brass_Audit_Rejection_ReasonStore.objects.filter(lot_id=lot_id).order_by('-id').first()
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
    
@method_decorator(csrf_exempt, name='dispatch')
class RejectTableTrayIdListAPIView(APIView):
    def get(self, request):
        lot_id = request.GET.get("lot_id")
        if not lot_id:
            return Response({"success": False, "error": "Lot ID is required"}, status=400)

        try:
            # ✅ FIXED: Get rejected trays from modelmasterapp.TrayId (main table) instead of BrassAuditTrayId
            # This ensures data consistency from Pick table to Reject table
            main_trays = TrayId.objects.filter(lot_id=lot_id, brass_rejected_tray=True)
            brass_audit_trays = BrassAuditTrayId.objects.filter(lot_id=lot_id, rejected_tray=True)
            
            all_trays = []
            
            # Priority 1: Use main TrayId table data
            for tray in main_trays:
                tray_data = {
                    "tray_id": tray.tray_id,
                    "tray_quantity": tray.tray_quantity,
                    "rejected_tray": True,  # ✅ FIXED: Always True for rejected trays
                    "brass_rejected_tray": True,  # Include original flag for reference
                    "delink_tray": getattr(tray, 'delink_tray', False),
                    "iqf_reject_verify": True,  # Coming from main table means it's verified
                    "new_tray": getattr(tray, 'new_tray', False),
                    "IP_tray_verified": getattr(tray, 'IP_tray_verified', False),
                    "source": "main_table"  # Track source for debugging
                }
                all_trays.append(tray_data)
            
            # Priority 2: Add any additional Brass Audit-specific rejected trays not in main table
            for tray in brass_audit_trays:
                # Check if this tray already exists in main table results
                exists_in_main = any(t['tray_id'] == tray.tray_id for t in all_trays)
                if not exists_in_main:
                    tray_data = {
                        "tray_id": tray.tray_id,
                        "tray_quantity": tray.tray_quantity,
                        "rejected_tray": tray.rejected_tray,
                        "delink_tray": getattr(tray, 'delink_tray', False),
                        "iqf_reject_verify": getattr(tray, 'iqf_reject_verify', False),
                        "new_tray": getattr(tray, 'new_tray', False),
                        "IP_tray_verified": getattr(tray, 'IP_tray_verified', False),
                        "source": "brass_audit_table"  # Track source for debugging
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

@method_decorator(csrf_exempt, name='dispatch')
class RejectCheckTrayValidate_Complete_APIView(APIView):
    def post(self, request):
        try:
            data = request.data if hasattr(request, 'data') else json.loads(request.body.decode('utf-8'))
            batch_id_input = str(data.get('batch_id')).strip()
            tray_id = str(data.get('tray_id')).strip()

            exists = BrassAuditTrayId.objects.filter(
                batch_id__batch_id__icontains=batch_id_input,
                tray_id=tray_id
            ).exists()

            return JsonResponse({
                'success': True,
                'exists': exists
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ==========================================
# BARCODE SCANNER API - Brass Audit
# ==========================================

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_lot_id_for_tray(request):
    """
    Get lot_id for a given tray_id to support barcode scanner functionality in Brass Audit
    
    This endpoint searches across multiple tables to find the lot_id associated with a tray_id:
    1. BrassAuditTrayId table (primary)
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
        # Strategy 1: Check BrassAuditTrayId table first (most specific to Brass Audit)
        try:
            brass_audit_tray = BrassAuditTrayId.objects.get(tray_id=tray_id)
            return JsonResponse({
                'success': True,
                'lot_id': brass_audit_tray.lot_id,
                'source': 'BrassAuditTrayId',
                'message': f'Tray {tray_id} found in Brass Audit system'
            })
        except BrassAuditTrayId.DoesNotExist:
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
        
        
        
        


def get_reusable_trays_after_rejection(tray_quantities, rejection_quantities):
    """
    Given a list of tray quantities and a list of progressive rejection quantities,
    returns the indices of trays that become zero and can be reused for rejection.

    Args:
        tray_quantities: List[int] - initial tray quantities (e.g. [6, 16, 16, 16])
        rejection_quantities: List[int] - progressive rejection quantities (e.g. [5, 6])

    Returns:
        reusable_tray_indices: List[int] - indices of trays that become zero after rejections
        final_tray_quantities: List[int] - tray quantities after all rejections
    """
    trays = tray_quantities.copy()
    reusable_tray_indices = []

    for reject_qty in rejection_quantities:
        # Always consume from the smallest non-zero tray first (ascending order)
        sorted_indices = sorted([i for i, qty in enumerate(trays) if qty > 0], key=lambda i: trays[i])
        remaining = reject_qty
        for idx in sorted_indices:
            if remaining <= 0:
                break
            consume = min(trays[idx], remaining)
            trays[idx] -= consume
            remaining -= consume
            # If this tray just became zero, mark it as reusable (if not already marked)
            if trays[idx] == 0 and idx not in reusable_tray_indices:
                reusable_tray_indices.append(idx)
        # If rejection qty not fully consumed, break (should not happen if logic is correct)

    return reusable_tray_indices, trays

# Example usage:
# trays = [6, 16, 16, 16]
# rejections = [5, 6]
# reusable, final = get_reusable_trays_after_rejection(trays, rejections)
# print("Reusable tray indices:", reusable)  # e.g. [0]
# print("Final tray quantities:", final)     # e.g. [0, 11, 16, 16]

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_brass_audit_tray_details_for_modal(request):
    lot_id = request.GET.get('lot_id')
    if not lot_id:
        return Response({'success': False, 'error': 'Missing lot_id'})

    try:
        stock_obj = TotalStockModel.objects.filter(lot_id=lot_id).first()
        if not stock_obj:
            return Response({'success': False, 'error': 'Lot not found'})

        accepted_trays = []
        rejected_trays = []
        total_accepted_qty = 0

        if stock_obj.current_stage == 'Brass Audit':
            if BrassAuditTrayId.objects.filter(lot_id=lot_id).exists():
                # ✅ FIX: Order by top_tray first, then tray_quantity ascending (smallest qty = top tray)
                trays = BrassAuditTrayId.objects.filter(lot_id=lot_id).order_by('-top_tray', 'tray_quantity')
                for tray in trays:
                    if tray.rejected_tray:
                        rejected_trays.append({
                            'tray_id': tray.tray_id,
                            'tray_quantity': tray.tray_quantity or 0,
                            'rejection_reason': 'Rejected'
                        })
                    else:
                        accepted_trays.append({
                            'tray_id': tray.tray_id,
                            'tray_quantity': tray.tray_quantity or 0,
                            'top_tray': tray.top_tray
                        })
                        total_accepted_qty += tray.tray_quantity or 0
            else:
                # Fetch from TrayId, update qtys and top_tray from BrassTrayId
                tray_objs = TrayId.objects.filter(lot_id=lot_id)
                brass_trays = {t.tray_id: t for t in BrassTrayId.objects.filter(lot_id=lot_id)}
                for tray in tray_objs:
                    brass_tray = brass_trays.get(tray.tray_id)
                    qty = brass_tray.tray_quantity if brass_tray else tray.tray_capacity or 12
                    top_tray = brass_tray.top_tray if brass_tray else False
                    accepted_trays.append({
                        'tray_id': tray.tray_id,
                        'tray_quantity': qty,
                        'top_tray': top_tray
                    })
                    total_accepted_qty += qty
        else:
            # ✅ FIX: Order by top_tray first, then tray_quantity ascending (smallest qty = top tray)
            trays = BrassAuditTrayId.objects.filter(lot_id=lot_id).order_by('-top_tray', 'tray_quantity')
            for tray in trays:
                if tray.rejected_tray:
                    rejected_trays.append({
                        'tray_id': tray.tray_id,
                        'tray_quantity': tray.tray_quantity or 0,
                        'rejection_reason': 'Rejected'
                    })
                else:
                    accepted_trays.append({
                        'tray_id': tray.tray_id,
                        'tray_quantity': tray.tray_quantity or 0,
                        'top_tray': tray.top_tray
                    })
                    total_accepted_qty += tray.tray_quantity or 0

        # ✅ FIX: Sort by top_tray (descending) THEN by tray_quantity (ascending)
        # This ensures top tray appears first, followed by other trays in qty order
        accepted_trays.sort(key=lambda x: (not x.get('top_tray', False), x.get('tray_quantity', 0)))

        for idx, tray in enumerate(accepted_trays, 1):
            tray['s_no'] = idx
            if tray.get('top_tray'):
                tray['s_no_display'] = f"{idx} (Top Tray)"
            else:
                tray['s_no_display'] = str(idx)

        return Response({
            'success': True,
            'lot_id': lot_id,
            'model_no': stock_obj.batch_id.model_no if stock_obj.batch_id else '',
            'lot_qty': total_accepted_qty,
            'accepted_trays': accepted_trays,
            'rejected_trays': rejected_trays,
            'total_accepted_qty': total_accepted_qty
        })

    except Exception as e:
        return Response({'success': False, 'error': str(e)})