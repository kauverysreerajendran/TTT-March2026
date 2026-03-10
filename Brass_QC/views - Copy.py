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
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from IQF.models import *
from BrassAudit.models import *
from django.utils import timezone
from datetime import timedelta
import datetime
import pytz
from django.contrib.auth.decorators import login_required

# ✅ NEW: Helper function to automatically calculate and set top tray (smallest quantity)
def auto_calculate_top_tray(lot_id):
    """
    Automatically set the top tray as the tray with smallest quantity for a given lot.
    This ensures the top tray is always correct without requiring manual checkbox clicks.
    Works for BrassTrayId table.
    """
    try:
        print(f"🔄 [auto_calculate_top_tray] Calculating top tray for lot_id: {lot_id}")
        
        # Get all non-rejected BrassTrayId records for this lot, ordered by quantity (smallest first)
        all_brass_trays = BrassTrayId.objects.filter(
            lot_id=lot_id, 
            rejected_tray=False
        ).order_by('tray_quantity')
        
        if all_brass_trays.exists():
            # Reset all top_tray flags to False first
            all_brass_trays.update(top_tray=False)
            
            # Set the first tray (smallest quantity) as top tray
            top_tray = all_brass_trays.first()
            top_tray.top_tray = True
            top_tray.save(update_fields=['top_tray'])
            
            print(f"✅ [auto_calculate_top_tray] Set top_tray=True for {top_tray.tray_id} (qty: {top_tray.tray_quantity})")
            return True
        else:
            print(f"⚠️ [auto_calculate_top_tray] No non-rejected trays found for lot_id: {lot_id}")
            return False
            
    except Exception as e:
        print(f"❌ [auto_calculate_top_tray] Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

# ✅ NEW: Helper function to automatically calculate and set top tray for Brass Audit
def auto_calculate_top_tray_brass_audit(lot_id):
    """
    Automatically set the top tray as the tray with smallest quantity for Brass Audit.
    Works for BrassAuditTrayId table.
    """
    try:
        print(f"🔄 [auto_calculate_top_tray_brass_audit] Calculating top tray for lot_id: {lot_id}")
        
        # Import BrassAudit models
        from BrassAudit.models import BrassAuditTrayId
        
        # Get all non-rejected BrassAuditTrayId records for this lot, ordered by quantity (smallest first)
        all_audit_trays = BrassAuditTrayId.objects.filter(
            lot_id=lot_id, 
            rejected_tray=False
        ).order_by('tray_quantity')
        
        if all_audit_trays.exists():
            # Reset all top_tray flags to False first
            all_audit_trays.update(top_tray=False)
            
            # Set the first tray (smallest quantity) as top tray
            top_tray = all_audit_trays.first()
            top_tray.top_tray = True
            top_tray.save(update_fields=['top_tray'])
            
            print(f"✅ [auto_calculate_top_tray_brass_audit] Set top_tray=True for {top_tray.tray_id} (qty: {top_tray.tray_quantity})")
            return True
        else:
            print(f"⚠️ [auto_calculate_top_tray_brass_audit] No non-rejected trays found for lot_id: {lot_id}")
            return False
            
    except Exception as e:
        print(f"❌ [auto_calculate_top_tray_brass_audit] Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

# ✅ NEW: Helper function to transfer Brass QC accepted data to Brass Audit tables
def transfer_brass_qc_data_to_brass_audit(lot_id, user):
    """
    Transfer accepted tray data from Brass QC tables to Brass Audit tables
    when lots are marked as accepted or partially accepted.
    
    Handles two cases:
    1. With tray scanning: Uses Brass_Qc_Accepted_TrayID_Store
    2. Without tray scanning: Uses BrassTrayId directly
    """
    try:
        print(f"🔄 [TRANSFER] Starting Brass QC → Brass Audit data transfer for lot: {lot_id}")
        
        # Import Brass Audit models
        from BrassAudit.models import Brass_Audit_Accepted_TrayID_Store, Brass_Audit_Accepted_TrayScan
        
        # ✅ FIXED: Always use BrassTrayId as the source for transfer
        # Get all accepted BrassTrayId records (not rejected, not delinked)
        brass_trays_source = BrassTrayId.objects.filter(
            lot_id=lot_id,
            rejected_tray=False
        ).exclude(delink_tray=True)
        
        # ✅ ENHANCED: Debug logging to show all trays before transfer
        all_trays_debug = BrassTrayId.objects.filter(lot_id=lot_id)
        print(f"🔍 [TRANSFER DEBUG] Total BrassTrayId records for lot {lot_id}: {all_trays_debug.count()}")
        for tray in all_trays_debug:
            print(f"   📦 {tray.tray_id}: qty={tray.tray_quantity}, top_tray={tray.top_tray}, rejected={tray.rejected_tray}, delinked={tray.delink_tray}")
        
        # Check if we have any accepted trays to transfer
        if brass_trays_source.exists():
            print(f"✅ [TRANSFER] Found {brass_trays_source.count()} accepted trays in BrassTrayId for lot: {lot_id}")
            
            # ✅ FIXED: Clear any existing Brass Audit data for this lot AND any duplicate tray_ids
            # Step 1: Delete records by lot_id
            deleted_by_lot = Brass_Audit_Accepted_TrayID_Store.objects.filter(lot_id=lot_id).delete()
            print(f"   🗑️ Deleted {deleted_by_lot[0]} existing Brass Audit records for lot_id: {lot_id}")
            
            # Step 2: Delete any remaining records with duplicate tray_ids to prevent constraint violation
            tray_ids_to_transfer = [t.tray_id for t in brass_trays_source]
            deleted_by_tray_id = Brass_Audit_Accepted_TrayID_Store.objects.filter(
                tray_id__in=tray_ids_to_transfer
            ).delete()
            print(f"   🗑️ Deleted {deleted_by_tray_id[0]} duplicate tray_id records from Brass Audit")
            
            # Transfer each tray record from BrassTrayId using update_or_create for safety
            total_qty = 0
            for brass_tray in brass_trays_source:
                # ✅ Use update_or_create to handle duplicates gracefully
                audit_tray, created = Brass_Audit_Accepted_TrayID_Store.objects.update_or_create(
                    tray_id=brass_tray.tray_id,  # Unique field
                    defaults={
                        'lot_id': lot_id,
                        'tray_qty': brass_tray.tray_quantity,
                        'user': user,
                        'is_draft': False,
                        'is_save': True
                    }
                )
                total_qty += brass_tray.tray_quantity
                action = "Created" if created else "Updated"
                print(f"   ✅ {action} tray in Brass Audit: {brass_tray.tray_id} (qty: {brass_tray.tray_quantity})")
            
            # 2. Transfer accepted tray scan data using update_or_create
            qc_scan = Brass_Qc_Accepted_TrayScan.objects.filter(lot_id=lot_id).first()
            if qc_scan:
                # ✅ FIXED: Use update_or_create to prevent duplicates
                scan_record, created = Brass_Audit_Accepted_TrayScan.objects.update_or_create(
                    lot_id=lot_id,
                    defaults={
                        'accepted_tray_quantity': str(total_qty),
                        'user': user
                    }
                )
                action = "Created" if created else "Updated"
                print(f"   ✅ {action} scan data: total_qty={total_qty}")
            
            # 3. Update TrayId table to mark trays as ready for Brass Audit
            from modelmasterapp.models import TrayId
            
            # Mark transferred trays as brass_rejected_tray=False (accepted in Brass QC)
            transferred_tray_ids = [t.tray_id for t in brass_trays_source]
            updated_trays = TrayId.objects.filter(
                lot_id=lot_id,
                tray_id__in=transferred_tray_ids
            ).update(
                brass_rejected_tray=False,  # Mark as accepted in Brass QC
                rejected_tray=False  # Ensure not marked as rejected
            )
            
            # Mark non-transferred trays as rejected (trays not in the accepted list)
            all_lot_trays = TrayId.objects.filter(lot_id=lot_id).exclude(tray_id__in=transferred_tray_ids)
            rejected_count = all_lot_trays.update(brass_rejected_tray=True)  # Mark as rejected in Brass QC
            
            print(f"   ✅ Updated {updated_trays} accepted trays in TrayId table for Brass Audit")
            print(f"   ✅ Marked {rejected_count} trays as rejected in Brass QC")
            
            # 4. ✅ FIXED: Create BrassAuditTrayId records from brass_trays_source to preserve top_tray flag
            from BrassAudit.models import BrassAuditTrayId
            
            # Delete any existing BrassAuditTrayId records for this lot AND duplicate tray_ids
            deleted_by_lot = BrassAuditTrayId.objects.filter(lot_id=lot_id).delete()
            print(f"   🗑️ Deleted {deleted_by_lot[0]} BrassAuditTrayId records for lot: {lot_id}")
            
            # Delete any records with duplicate tray_ids from other lots
            tray_ids_to_transfer = [t.tray_id for t in brass_trays_source]
            deleted_by_tray = BrassAuditTrayId.objects.filter(tray_id__in=tray_ids_to_transfer).delete()
            print(f"   🗑️ Deleted {deleted_by_tray[0]} duplicate tray_id records from BrassAuditTrayId")
            
            # Get TotalStockModel for batch_id
            stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
            batch_id = stock.batch_id if stock else None
            
            # Use brass_trays_source directly (already filtered for accepted trays)
            audit_tray_count = 0
            for brass_tray in brass_trays_source:
                # ✅ FIXED: Use update_or_create to handle duplicates gracefully
                audit_tray, created = BrassAuditTrayId.objects.update_or_create(
                    tray_id=brass_tray.tray_id,  # Unique identifier
                    defaults={
                        'lot_id': lot_id,
                        'batch_id': batch_id,
                        'tray_quantity': brass_tray.tray_quantity,
                        'tray_capacity': brass_tray.tray_capacity,
                        'tray_type': brass_tray.tray_type,
                        'top_tray': brass_tray.top_tray,  # ✅ Preserve top_tray flag
                        'IP_tray_verified': True,
                        'new_tray': False,
                        'delink_tray': False,
                        'rejected_tray': False,
                        'user': user,
                        'date': timezone.now()
                    }
                )
                audit_tray_count += 1
                action = "Created" if created else "Updated"
                print(f"   ✅ {action} BrassAuditTrayId: {brass_tray.tray_id} (qty={brass_tray.tray_quantity}, top_tray={brass_tray.top_tray})")
            
            print(f"   ✅ Processed {audit_tray_count} BrassAuditTrayId records for Brass Audit")
            
            # ✅ NEW: Automatically calculate top tray for Brass Audit after transfer
            auto_calculate_top_tray_brass_audit(lot_id)
            print(f"✅ [TRANSFER] Automatically calculated top tray for Brass Audit lot_id: {lot_id}")
            
            print(f"✅ [TRANSFER] Successfully transferred {brass_trays_source.count()} trays from BrassTrayId to Brass Audit")
            return True
        
        # ✅ FALLBACK: No accepted trays found in BrassTrayId (all rejected or delinked)
        else:
            print(f"❌ [TRANSFER] No accepted trays found in BrassTrayId for lot: {lot_id}")
            print(f"   All trays may be rejected or delinked. Cannot transfer to Brass Audit.")
            return False
            
    except Exception as e:
        print(f"❌ [TRANSFER] Error transferring Brass QC data to Brass Audit: {str(e)}")
        traceback.print_exc()
        return False


# ✅ NEW: Helper function to send Brass Audit rejected data back to Brass QC (reuse existing trays)
def send_brass_audit_back_to_brass_qc(lot_id, user):
    """
    When a lot is rejected in Brass Audit and sent back to Brass QC:
    - Do NOT create new BrassTrayId records (reuse existing ones).
    - Update flags to enable the lot in Brass QC.
    - Clear Brass Audit data to prevent duplication.
    - Automatically recalculate top tray for Brass QC.
    """
    try:
        print(f"🔄 [REVERSE TRANSFER] Sending Brass Audit lot {lot_id} back to Brass QC (reuse existing trays)")
        
        # Get the TotalStockModel entry
        stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
        if not stock:
            print(f"❌ [REVERSE TRANSFER] Lot {lot_id} not found in TotalStockModel")
            return False
        
        # ✅ STEP 1: Clear Brass Audit data (to prevent duplication)
        # Delete Brass Audit tray records for this lot
        from BrassAudit.models import BrassAuditTrayId, Brass_Audit_Accepted_TrayID_Store, Brass_Audit_Rejected_TrayScan
        deleted_audit_trays = BrassAuditTrayId.objects.filter(lot_id=lot_id).delete()
        deleted_accepted_store = Brass_Audit_Accepted_TrayID_Store.objects.filter(lot_id=lot_id).delete()
        deleted_rejected_scans = Brass_Audit_Rejected_TrayScan.objects.filter(lot_id=lot_id).delete()
        print(f"🗑️ [REVERSE TRANSFER] Cleared Brass Audit data for lot {lot_id}: {deleted_audit_trays[0]} trays, {deleted_accepted_store[0]} accepted, {deleted_rejected_scans[0]} rejected scans")
        
        # ✅ STEP 2: Update flags to enable in Brass QC (reuse existing state)
        stock.send_brass_audit_to_qc = True   # ✅ FIX BUG 1: Set this to True so lot appears in Brass QC pick table
        stock.brass_audit_rejection = False   # Reset rejection flag so lot can appear in Brass QC
        stock.brass_audit_accptance = False   # Reset acceptance (prevents appearance in Brass Audit)
        stock.brass_qc_accptance = False      # ✅ FIX BUG 1: Reset this to prevent Brass Audit inclusion
        stock.brass_audit_few_cases_accptance = False
        stock.brass_audit_rejection_tray_scan_status = False
        stock.brass_audit_accepted_tray_scan_status = False
        stock.brass_audit_onhold_picking = False
        stock.brass_audit_draft = False
        stock.save(update_fields=[
            'send_brass_audit_to_qc', 'brass_audit_rejection', 'brass_audit_accptance', 'brass_qc_accptance',
            'brass_audit_few_cases_accptance', 'brass_audit_rejection_tray_scan_status',
            'brass_audit_accepted_tray_scan_status', 'brass_audit_onhold_picking', 'brass_audit_draft'
        ])
        print(f"✅ [REVERSE TRANSFER] Updated flags for lot {lot_id} to enable in Brass QC")
        
        # ✅ STEP 3: Ensure existing BrassTrayId records are available (no new creation)
        # Reset any flags on existing trays to make them available for Brass QC
        brass_trays = BrassTrayId.objects.filter(lot_id=lot_id)
        if brass_trays.exists():
            # Reset flags to make trays available for processing again
            brass_trays.update(
                rejected_tray=False,  # Reset rejection flag
                delink_tray=False     # Ensure not marked as delinked
            )
            print(f"🔄 [REVERSE TRANSFER] Reused {brass_trays.count()} existing BrassTrayId records for lot {lot_id}")
            
            # Debug: Show tray details
            for tray in brass_trays:
                print(f"   📦 Reused tray: {tray.tray_id} (qty={tray.tray_quantity}, top_tray={tray.top_tray})")
        else:
            print(f"⚠️ [REVERSE TRANSFER] No existing BrassTrayId records found for lot {lot_id} (this might be an issue)")
        
        # ✅ STEP 4: Reset TrayId table flags to enable processing
        from modelmasterapp.models import TrayId
        tray_ids_in_lot = brass_trays.values_list('tray_id', flat=True)
        if tray_ids_in_lot:
            updated_tray_count = TrayId.objects.filter(
                lot_id=lot_id,
                tray_id__in=tray_ids_in_lot
            ).update(
                brass_rejected_tray=False,  # Reset rejection flag
                rejected_tray=False         # Ensure not marked as rejected
            )
            print(f"✅ [REVERSE TRANSFER] Reset {updated_tray_count} TrayId records for Brass QC processing")
        
        # ✅ STEP 5: Clear any Brass QC accepted data that might interfere
        # Clear any lingering accepted data to start fresh
        Brass_Qc_Accepted_TrayID_Store.objects.filter(lot_id=lot_id).delete()
        Brass_Qc_Accepted_TrayScan.objects.filter(lot_id=lot_id).delete()
        print(f"🗑️ [REVERSE TRANSFER] Cleared any lingering Brass QC accepted data for lot {lot_id}")
        
        # ✅ STEP 6: Automatically recalculate top tray for Brass QC
        auto_calculate_top_tray(lot_id)
        print(f"✅ [REVERSE TRANSFER] Automatically recalculated top tray for Brass QC lot {lot_id}")
        
        print(f"✅ [REVERSE TRANSFER] Successfully sent lot {lot_id} back to Brass QC (reused existing trays)")
        return True
        
    except Exception as e:
        print(f"❌ [REVERSE TRANSFER] Error sending back to Brass QC: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


@method_decorator(login_required, name='dispatch')
class BrassPickTableView(APIView):
    renderer_classes = [TemplateHTMLRenderer]
    template_name = 'Brass_Qc/Brass_PickTable.html'

    def get(self, request):
        user = request.user
        is_admin = user.groups.filter(name='Admin').exists() if user.is_authenticated else False

        # Handle sorting parameters
        sort = request.GET.get('sort')
        order = request.GET.get('order', 'asc')  # Default to ascending
        
        # Field mapping for proper model field references
        sort_field_mapping = {
            'serial_number': 'lot_id',  # Use lot_id for serial number sorting
            'date_time': 'last_process_date_time',
            'plating_stk_no': 'batch_id__plating_stk_no',
            'polishing_stk_no': 'batch_id__polishing_stk_no',
            'plating_color': 'batch_id__plating_color',
            'category': 'batch_id__category',
            'polish_finish': 'batch_id__polish_finish',
            'tray_capacity': 'batch_id__tray_capacity',
            'vendor_location': 'batch_id__vendor_internal',
            'no_of_trays': 'batch_id__no_of_trays',
            'total_ip_accepted_qty': 'total_IP_accpeted_quantity',
            'process_status': 'last_process_module',
            'lot_status': 'last_process_module',
            'current_stage': 'next_process_module',
            'remarks': 'Bq_pick_remarks'
        }

        brass_rejection_reasons = Brass_QC_Rejection_Table.objects.all()

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
            Brass_QC_Draft_Store.objects.filter(
                lot_id=OuterRef('lot_id')
            )
        )
        
        draft_type_subquery = Brass_QC_Draft_Store.objects.filter(
            lot_id=OuterRef('lot_id')
        ).values('draft_type')[:1]

        brass_rejection_qty_subquery = Brass_QC_Rejection_ReasonStore.objects.filter(
            lot_id=OuterRef('lot_id')
        ).values('total_rejection_quantity')[:1]

        # ✅ Annotate with additional fields
        queryset = queryset.annotate(
            wiping_required=F('batch_id__model_stock_no__wiping_required'),
            has_draft=has_draft_subquery,
            draft_type=draft_type_subquery,
            brass_rejection_total_qty=brass_rejection_qty_subquery,
        )

        # ✅ UPDATED: Filter logic now works on TotalStockModel directly
        # ✅ FIX: Exclude lots that have been rejected at Brass Audit level (brass_audit_rejection=True)
        # These lots have been replaced with new lots (send_brass_audit_to_qc=True)
        queryset = queryset.filter(
            (
                (
                    Q(brass_qc_accptance__isnull=True) | Q(brass_qc_accptance=False)
                ) &
                (
                    Q(brass_qc_rejection__isnull=True) | Q(brass_qc_rejection=False)
                ) &
                ~Q(brass_qc_few_cases_accptance=True, brass_onhold_picking=False)
                &
                (
                    Q(accepted_Ip_stock=True) | 
                    Q(few_cases_accepted_Ip_stock=True, ip_onhold_picking=False)
                )
            )
            |
            Q(send_brass_qc=True)  # ✅ This will now work correctly
            |
            Q(brass_qc_rejection=True, brass_onhold_picking=True)
            |
            Q(send_brass_audit_to_qc=True)
        ).exclude(
            Q(brass_audit_rejection=True)  # ✅ Exclude old lots that were rejected at Brass Audit
        )

        # Apply sorting if requested
        if sort and sort in sort_field_mapping:
            field = sort_field_mapping[sort]
            if order == 'desc':
                field = '-' + field
            queryset = queryset.order_by(field)
        else:
            queryset = queryset.order_by('-last_process_date_time', '-lot_id')  # Default sorting

        print("All lot_ids in queryset:", list(queryset.values_list('lot_id', flat=True)))

        # Pagination
        page_number = request.GET.get('page', 1)
        paginator = Paginator(queryset, 10)
        page_obj = paginator.get_page(page_number)

        # ✅ UPDATED: Get values from TotalStockModel instead of ModelMasterCreation
        master_data = []
        for stock_obj in page_obj.object_list:
            batch = stock_obj.batch_id
            
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
                'brass_audit_rejection': stock_obj.brass_audit_rejection,
                # ✅ Stock-related fields from TotalStockModel
                'stock_lot_id': stock_obj.lot_id,
                'total_IP_accpeted_quantity': stock_obj.total_IP_accpeted_quantity,
                'brass_qc_accepted_qty_verified': stock_obj.brass_qc_accepted_qty_verified,
                'brass_qc_accepted_qty': stock_obj.brass_qc_accepted_qty,
                'brass_missing_qty': stock_obj.brass_missing_qty,
                'brass_physical_qty': stock_obj.brass_physical_qty,
                'brass_physical_qty_edited': stock_obj.brass_physical_qty_edited,
                'accepted_Ip_stock': stock_obj.accepted_Ip_stock,
                'rejected_ip_stock': stock_obj.rejected_ip_stock,
                'few_cases_accepted_Ip_stock': stock_obj.few_cases_accepted_Ip_stock,
                'accepted_tray_scan_status': stock_obj.accepted_tray_scan_status,
                'Bq_pick_remarks': stock_obj.Bq_pick_remarks,
                'brass_qc_accptance': stock_obj.brass_qc_accptance,
                'brass_accepted_tray_scan_status': stock_obj.brass_accepted_tray_scan_status,
                'brass_qc_rejection': stock_obj.brass_qc_rejection,
                'brass_qc_few_cases_accptance': stock_obj.brass_qc_few_cases_accptance,
                'brass_onhold_picking': stock_obj.brass_onhold_picking,
                'brass_draft': stock_obj.brass_draft,
                'iqf_acceptance': stock_obj.iqf_acceptance,
                'send_brass_qc': stock_obj.send_brass_qc,  # ✅ This will now show True for new lots
                'send_brass_audit_to_qc': stock_obj.send_brass_audit_to_qc,
                'last_process_date_time': stock_obj.last_process_date_time,
                'iqf_last_process_date_time': stock_obj.iqf_last_process_date_time,
                'brass_hold_lot': stock_obj.brass_hold_lot,
                'brass_holding_reason': stock_obj.brass_holding_reason,
                'brass_release_lot': stock_obj.brass_release_lot,
                'brass_release_reason': stock_obj.brass_release_reason,
                'has_draft': stock_obj.has_draft,
                'draft_type': stock_obj.draft_type,
                'brass_rejection_total_qty': stock_obj.brass_rejection_total_qty,
                # Additional batch fields
                'plating_stk_no': batch.plating_stk_no,
                'polishing_stk_no': batch.polishing_stk_no,
                'category': batch.category,
                'last_process_module': stock_obj.last_process_module,
            }
            master_data.append(data)

        # ✅ Process the data as before
        for data in master_data:   
            total_IP_accpeted_quantity = data.get('total_IP_accpeted_quantity', 0)
            tray_capacity = data.get('tray_capacity', 0)
            data['vendor_location'] = f"{data.get('vendor_internal', '')}_{data.get('location__location_name', '')}"
            
            lot_id = data.get('stock_lot_id')
            
            if total_IP_accpeted_quantity and total_IP_accpeted_quantity > 0:
                data['display_accepted_qty'] = total_IP_accpeted_quantity
            else:
                total_rejection_qty = 0
                rejection_store = IP_Rejection_ReasonStore.objects.filter(lot_id=lot_id).first()
                if rejection_store and rejection_store.total_rejection_quantity:
                    total_rejection_qty = rejection_store.total_rejection_quantity

                total_stock_obj = TotalStockModel.objects.filter(lot_id=lot_id).first()
                
                if total_stock_obj and total_rejection_qty > 0:
                    data['display_accepted_qty'] = max(total_stock_obj.total_stock - total_rejection_qty, 0)
                else:
                    data['display_accepted_qty'] = 0

            brass_physical_qty = data.get('brass_physical_qty') or 0
            brass_rejection_total_qty = data.get('brass_rejection_total_qty') or 0
            is_delink_only = (brass_physical_qty > 0 and 
                              brass_rejection_total_qty >= brass_physical_qty and 
                              data.get('brass_onhold_picking', False))
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
            data['available_qty'] = data.get('brass_qc_accepted_qty') if data.get('brass_qc_accepted_qty') and data.get('brass_qc_accepted_qty') > 0 else (data.get('brass_physical_qty') if data.get('brass_physical_qty') and data.get('brass_physical_qty') > 0 else data.get('total_IP_accpeted_quantity', 0))

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


@method_decorator(login_required, name='dispatch')
@method_decorator(csrf_exempt, name='dispatch')
class BrassSaveHoldUnholdReasonAPIView(APIView):
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
                obj.brass_holding_reason = remark
                obj.brass_hold_lot = True
                obj.brass_release_reason = ''
                obj.brass_release_lot = False
            elif action == 'unhold':
                obj.brass_release_reason = remark
                obj.brass_hold_lot = False
                obj.brass_release_lot = True

            obj.save(update_fields=['brass_holding_reason', 'brass_release_reason', 'brass_hold_lot', 'brass_release_lot'])
            return JsonResponse({'success': True, 'message': 'Reason saved.'})

        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
        
    
@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(login_required, name='dispatch')  
class BrassSaveIPCheckboxView(APIView):
    def post(self, request, format=None):
        try:
            data = request.data
            lot_id = data.get("lot_id")
            missing_qty = data.get("missing_qty")
            print("DEBUG: Received missing_qty:", missing_qty)

            if not lot_id:
                return Response({"success": False, "error": "Lot ID is required"}, status=status.HTTP_400_BAD_REQUEST)

            total_stock = TotalStockModel.objects.get(lot_id=lot_id)
            total_stock.brass_qc_accepted_qty_verified = True
            total_stock.last_process_module = "Brass_QC"
            total_stock.next_process_module = "Brass Audit"

            # Calculate display_accepted_qty
            display_accepted_qty = 0
            if total_stock.total_IP_accpeted_quantity and total_stock.total_IP_accpeted_quantity > 0:
                display_accepted_qty = total_stock.total_IP_accpeted_quantity
            else:
                total_rejection_qty = 0
                rejection_store = IP_Rejection_ReasonStore.objects.filter(lot_id=lot_id).first()
                if rejection_store and rejection_store.total_rejection_quantity:
                    total_rejection_qty = rejection_store.total_rejection_quantity

                if total_rejection_qty > 0:
                    display_accepted_qty = max(total_stock.total_stock - total_rejection_qty, 0)
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
            
                total_stock.brass_missing_qty = missing_qty
                total_stock.brass_physical_qty = display_accepted_qty - missing_qty
            
                self.create_brass_tray_instances(lot_id)
                IQFTrayId.objects.filter(lot_id=lot_id).delete()
                
                # ✅ NEW: Automatically calculate top tray without needing checkbox click
                auto_calculate_top_tray(lot_id)
                print(f"✅ [BrassSaveIPCheckboxView] Automatically calculated top tray for lot_id: {lot_id}")

            
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

            # Check flags for different tray models
            total_stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
            send_brass_qc = total_stock.send_brass_qc if total_stock else False
            send_brass_audit_to_qc = total_stock.send_brass_audit_to_qc if total_stock else False

            print(f"Flags: send_brass_qc={send_brass_qc}, send_brass_audit_to_qc={send_brass_audit_to_qc}")

            # Determine source model based on flags (priority order)
            if send_brass_audit_to_qc:
                # Use BrassAuditTrayId for audit trays
                verified_trays = BrassAuditTrayId.objects.filter(
                    lot_id=lot_id,
                    IP_tray_verified=True,
                    rejected_tray=True  # <-- Only include trays where rejected_tray is True
                )
                print(f"Using BrassAuditTrayId for tray creation (send_brass_audit_to_qc=True)")
            elif send_brass_qc:
                # Use IQFTrayId for accepted trays
                verified_trays = IQFTrayId.objects.filter(
                    lot_id=lot_id,
                    IP_tray_verified=True
                ).exclude(
                    rejected_tray=True
                )
                print(f"Using IQFTrayId for tray creation (send_brass_qc=True)")
            else:
                # Use IPTrayId for accepted trays
                verified_trays = IPTrayId.objects.filter(
                    lot_id=lot_id,
                    IP_tray_verified=True
                ).exclude(
                    rejected_tray=True
                )
                print(f"Using IPTrayId for tray creation (default)")


            total_stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
            batch_id = total_stock.batch_id if total_stock else None

            if not batch_id:
                print(f"❌ [create_brass_tray_instances] No batch_id found for lot {lot_id}")
                return

            created_count = 0
            updated_count = 0

            for tray in verified_trays:
                # ✅ FIXED: Check if record already exists for this lot_id AND tray_id
                brass_tray = BrassTrayId.objects.filter(tray_id=tray.tray_id, lot_id=lot_id).first()
                
                if brass_tray:
                    # Update existing record
                    print(f"🔄 [create_brass_tray_instances] Updating existing BrassTrayId for lot_id={lot_id}, tray_id={tray.tray_id}")
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
                        'batch_id', 'date', 'user', 'tray_quantity',
                        'top_tray', 'IP_tray_verified', 'tray_type', 'tray_capacity',
                        'new_tray', 'delink_tray', 'rejected_tray'
                    ])
                    updated_count += 1
                else:
                    # Check if there's a placeholder tray (lot_id IS NULL) that we can reuse
                    placeholder_tray = BrassTrayId.objects.filter(tray_id=tray.tray_id, lot_id__isnull=True).first()
                    if placeholder_tray:
                        print(f"🔄 [create_brass_tray_instances] Reusing placeholder tray for tray_id={tray.tray_id}")
                        placeholder_tray.lot_id = lot_id
                        placeholder_tray.batch_id = batch_id
                        placeholder_tray.date = timezone.now()
                        placeholder_tray.user = self.request.user
                        placeholder_tray.tray_quantity = tray.tray_quantity
                        placeholder_tray.top_tray = tray.top_tray
                        placeholder_tray.IP_tray_verified = True
                        placeholder_tray.tray_type = tray.tray_type
                        placeholder_tray.tray_capacity = tray.tray_capacity
                        placeholder_tray.new_tray = False
                        placeholder_tray.delink_tray = False
                        placeholder_tray.rejected_tray = False
                        placeholder_tray.save(update_fields=[
                            'lot_id', 'batch_id', 'date', 'user', 'tray_quantity',
                            'top_tray', 'IP_tray_verified', 'tray_type', 'tray_capacity',
                            'new_tray', 'delink_tray', 'rejected_tray'
                        ])
                        updated_count += 1
                    else:
                        # Create a new record
                        print(f"➕ [create_brass_tray_instances] Creating new BrassTrayId for lot_id={lot_id}, tray_id={tray.tray_id}")
                        brass_tray = BrassTrayId(
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
                            rejected_tray=False,
                        )
                        brass_tray.save()
                        created_count += 1

            # ✅ NOTE: Top tray calculation moved to auto_calculate_top_tray() function
            # Called separately after this method completes

            print(f"📊 [create_brass_tray_instances] Summary for lot {lot_id}:")
            print(f"   Created: {created_count} BrassTrayId records")
            print(f"   Updated: {updated_count} BrassTrayId records")
            print(f"   Total Processed: {created_count + updated_count}")
            
            deleted_count, _ = BrassAuditTrayId.objects.filter(lot_id=lot_id).delete()
            print(f"✅ Deleted {deleted_count} BrassAuditTrayId records for lot_id={lot_id}")  

        except Exception as e:
            print(f"❌ [create_brass_tray_instances] Error creating/updating BrassTrayId instances: {str(e)}")
            import traceback
            traceback.print_exc()
    
    def get(self, request, format=None):
        return Response(
            {"success": False, "error": "Invalid request method."},
            status=status.HTTP_400_BAD_REQUEST
        )


    
@method_decorator(login_required, name='dispatch')
class BrassTrayDelinkTopTrayCalcAPIView(APIView):
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
            trays = BrassTrayId.objects.filter(
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
            print(f"Error in BrassTrayDelinkTopTrayCalcAPIView: {str(e)}")

            return Response({
                'success': False,
                'error': 'Internal server error occurred while calculating delink requirements'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(login_required, name='dispatch')
class BrassTrayDelinkAndTopTrayUpdateAPIView(APIView):
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
                brass_delink_tray_obj = BrassTrayId.objects.filter(tray_id=delink_tray_id, lot_id=lot_id).first()
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
                
                # Update BrassTrayId for top tray
                top_tray_obj = BrassTrayId.objects.filter(tray_id=top_tray_id, lot_id=lot_id).first()
                if top_tray_obj:
                    top_tray_obj.top_tray = True
                    top_tray_obj.tray_quantity = int(top_tray_qty)
                    top_tray_obj.delink_tray = False  # Ensure it's not marked as delink
                    top_tray_obj.save(update_fields=['top_tray', 'tray_quantity', 'delink_tray'])
                    print(f"✅ Updated BrassTrayId top tray: {top_tray_id} to qty: {top_tray_qty}")

            # 3. Reset other trays (not delinked or top tray) to full capacity
            other_trays_brass = BrassTrayId.objects.filter(
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


@method_decorator(login_required, name='dispatch')
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
            batch_obj.Bq_pick_remarks = remark
            batch_obj.save(update_fields=['Bq_pick_remarks'])
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


@method_decorator(login_required, name='dispatch')   
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


@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(login_required, name='dispatch')
class BQ_Accepted_form(APIView):

    def post(self, request, format=None):
        data = request.data
        lot_id = data.get("stock_lot_id")
        try:
            total_stock_data = TotalStockModel.objects.get(lot_id=lot_id)

                
            total_stock_data.brass_qc_accptance = True
    
            # Use brass_physical_qty if set and > 0, else use total_stock
            physical_qty = total_stock_data.brass_physical_qty

            total_stock_data.brass_qc_accepted_qty = physical_qty
            total_stock_data.send_brass_qc = False
            # Update process modules
            total_stock_data.next_process_module = "Brass Audit"  # ✅ CORRECTED: Send to Brass Audit instead of Jig Loading
            total_stock_data.last_process_module = "Brass QC"
            total_stock_data.bq_last_process_date_time = timezone.now()  # Set the last process date/time
            total_stock_data.send_brass_audit_to_qc = False
            total_stock_data.save()
            
            # ✅ NEW: Transfer Brass QC accepted data to Brass Audit tables
            transfer_success = transfer_brass_qc_data_to_brass_audit(lot_id, request.user)
            if transfer_success:
                print(f"✅ [BQ_Accepted_form] Data transferred to Brass Audit for lot: {lot_id}")
            else:
                print(f"⚠️ [BQ_Accepted_form] Failed to transfer data to Brass Audit for lot: {lot_id}")
            
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


@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(login_required, name='dispatch')
class BQBatchRejectionAPIView(APIView):
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
                    
                    # BrassTrayId - Remove from lot completely
                    brass_delink_tray_obj = BrassTrayId.objects.filter(tray_id=delink_tray_id, lot_id=lot_id).first()
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
                    
                    # Update BrassTrayId for top tray
                    top_tray_obj = BrassTrayId.objects.filter(tray_id=top_tray_id, lot_id=lot_id).first()
                    if top_tray_obj:
                        top_tray_obj.top_tray = True
                        top_tray_obj.tray_quantity = int(top_tray_qty)
                        top_tray_obj.delink_tray = False
                        top_tray_obj.save(update_fields=['top_tray', 'tray_quantity', 'delink_tray'])
                        print(f"✅ Updated BrassTrayId top tray: {top_tray_id} to qty: {top_tray_qty}")
                        delink_operations_summary['top_tray_updated'] = True

                # 3. Reset other trays (not delinked or top tray) to full capacity
                other_trays_brass = BrassTrayId.objects.filter(
                    lot_id=lot_id
                ).exclude(
                    tray_id__in=delink_tray_ids + ([top_tray_id] if top_tray_id else [])
                )
                
                other_trays_count = 0
                for tray in other_trays_brass:
                    print(f"[BATCH REJECTION OTHER TRAY] Resetting BrassTrayId {tray.tray_id} to full capacity: {tray.tray_capacity}")
                    tray.tray_quantity = tray.tray_capacity
                    tray.top_tray = False
                    tray.delink_tray = False
                    tray.save(update_fields=['tray_quantity', 'top_tray', 'delink_tray'])
                    other_trays_count += 1

                delink_operations_summary['delinked_trays'] = delinked_count
                print(f"[BATCH REJECTION DELINK SUMMARY] Delinked {delinked_count} trays, reset {other_trays_count} other trays")

            # Get brass_physical_qty if set and > 0, else use total_stock
            qty = total_stock.brass_physical_qty

            # Set brass_qc_rejection = True (original batch rejection logic)
            total_stock.brass_qc_rejection = True
            total_stock.last_process_module = "Brass QC"
            total_stock.next_process_module = "Brass Audit"
            total_stock.send_brass_audit_to_qc = False
            total_stock.send_brass_qc = False
            total_stock.bq_last_process_date_time = timezone.now()
            total_stock.save(update_fields=[
                'brass_qc_rejection', 'last_process_module', 'next_process_module', 
                'bq_last_process_date_time', 'send_brass_audit_to_qc', 'send_brass_qc'
            ])

            # Update BrassTrayId records (only for non-delinked trays)
            if delink_tray_ids:
                # Only update trays that weren't delinked
                updated_trays_count = BrassTrayId.objects.filter(
                    lot_id=lot_id
                ).exclude(
                    tray_id__in=delink_tray_ids
                ).update(rejected_tray=True)
            else:
                # No delink operations, update all trays
                updated_trays_count = BrassTrayId.objects.filter(lot_id=lot_id).update(rejected_tray=True)
            
            # ✅ NEW: Mark all trays in TrayId model as brass_rejected_tray=True for lot rejection
            if delink_tray_ids:
                # Only update trays that weren't delinked
                trayid_updated_count = TrayId.objects.filter(
                    lot_id=lot_id
                ).exclude(
                    tray_id__in=delink_tray_ids
                ).update(brass_rejected_tray=True)
            else:
                # No delink operations, update all trays
                trayid_updated_count = TrayId.objects.filter(lot_id=lot_id).update(brass_rejected_tray=True)
            
            print(f"✅ [LOT REJECTION] Updated {updated_trays_count} BrassTrayId records and {trayid_updated_count} TrayId records with brass_rejected_tray=True")

            # Create Brass_QC_Rejection_ReasonStore entry with lot rejection remarks
            Brass_QC_Rejection_ReasonStore.objects.create(
                lot_id=lot_id,
                user=request.user,
                total_rejection_quantity=qty,
                batch_rejection=True,
                lot_rejected_comment=lot_rejected_comment
            )

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


@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(login_required, name='dispatch')
class BQTrayRejectionAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            data = request.data if hasattr(request, 'data') else json.loads(request.body.decode('utf-8'))
            lot_id = data.get('lot_id')
            batch_id = data.get('batch_id')
            tray_rejections = data.get('tray_rejections', [])  # List of {reason_id, qty, tray_id}

            print(f"🔍 [BQTrayRejectionAPIView] Received tray_rejections: {tray_rejections}")
            print(f"🔍 [BQTrayRejectionAPIView] Lot ID: {lot_id}, Batch ID: {batch_id}")

            if not lot_id or not tray_rejections:
                return Response({'success': False, 'error': 'Missing lot_id or tray_rejections'}, status=400)

            # Get the TotalStockModel for this lot_id
            total_stock_obj = TotalStockModel.objects.filter(lot_id=lot_id).first()
            if not total_stock_obj:
                return Response({'success': False, 'error': 'TotalStockModel not found'}, status=404)

            # Use brass_physical_qty if set and > 0, else use total_IP_accpeted_quantity
            available_qty = total_stock_obj.brass_physical_qty if total_stock_obj and total_stock_obj.brass_physical_qty else 0
            
            running_total = 0
            for idx, item in enumerate(tray_rejections):
                qty = int(item.get('qty', 0))
                running_total += qty
                if running_total > available_qty:
                    return Response({
                        'success': False,
                        'error': f'Quantity exceeds available ({available_qty}).'
                    }, status=400)

            # ✅ ENHANCED: Process each tray rejection INDIVIDUALLY with detailed logging
            total_qty = 0
            saved_rejections = []
            reason_ids_used = set()  # Track unique reason IDs for summary
            
            print(f"🔍 [BQTrayRejectionAPIView] Processing {len(tray_rejections)} individual tray rejections...")
            
            # ✅ CRITICAL: Process each rejection individually (no grouping)
            for idx, item in enumerate(tray_rejections):
                tray_id = item.get('tray_id', '').strip()
                reason_id = item.get('reason_id', '').strip()
                qty = int(item.get('qty', 0))
                top_tray = item.get('top_tray', False)  # ✅ NEW: Extract top_tray flag
                
                print(f"🔍 [BQTrayRejectionAPIView] Processing rejection {idx + 1}:")
                print(f"   - Tray ID: '{tray_id}'")
                print(f"   - Reason ID: '{reason_id}'")
                print(f"   - Quantity: {qty}")
                print(f"   - Top Tray: {top_tray}")  # ✅ NEW: Log top tray flag
                
                if qty <= 0:
                    print(f"   ⚠️ Skipping - zero or negative quantity")
                    continue
                    
                if not tray_id or not reason_id:
                    print(f"   ⚠️ Skipping - missing tray_id or reason_id")
                    continue
                
                try:
                    reason_obj = Brass_QC_Rejection_Table.objects.get(rejection_reason_id=reason_id)
                    print(f"   ✅ Found rejection reason: {reason_obj.rejection_reason}")
                    
                    # ✅ CREATE INDIVIDUAL RECORD FOR EACH TRAY + REASON COMBINATION
                    rejection_record = Brass_QC_Rejected_TrayScan.objects.create(
                        lot_id=lot_id,
                        rejected_tray_quantity=qty,  # Individual tray quantity
                        rejection_reason=reason_obj,
                        user=request.user,
                        rejected_tray_id=tray_id,  # Individual tray ID
                       # top_tray=top_tray  # ✅ NEW: Store top tray flag
                    )
                    
                    saved_rejections.append({
                        'record_id': rejection_record.id,
                        'tray_id': tray_id,
                        'qty': qty,
                        'reason': reason_obj.rejection_reason,
                        'reason_id': reason_id,
                        'top_tray': top_tray  # ✅ NEW: Track top tray flag
                    })
                    
                    total_qty += qty
                    reason_ids_used.add(reason_id)
                    
                    print(f"   ✅ SAVED rejection record ID {rejection_record.id}: tray_id={tray_id}, qty={qty}, reason={reason_obj.rejection_reason}")
                    
                except Brass_QC_Rejection_Table.DoesNotExist:
                    print(f"   ❌ Rejection reason {reason_id} not found")
                    return Response({
                        'success': False,
                        'error': f'Rejection reason {reason_id} not found'
                    }, status=400)
                except Exception as e:
                    print(f"   ❌ Error creating rejection record: {e}")
                    return Response({
                        'success': False,
                        'error': f'Error creating rejection record: {str(e)}'
                    }, status=500)

            if not saved_rejections:
                return Response({
                    'success': False,
                    'error': 'No valid rejections were processed'
                }, status=400)

            # ✅ Create ONE summary record for the lot (with all unique rejection reasons)
            if reason_ids_used:
                reasons = Brass_QC_Rejection_Table.objects.filter(rejection_reason_id__in=list(reason_ids_used))
                
                reason_store = Brass_QC_Rejection_ReasonStore.objects.create(
                    lot_id=lot_id,
                    user=request.user,
                    total_rejection_quantity=total_qty,
                    batch_rejection=False
                )
                reason_store.rejection_reason.set(reasons)
                
                print(f"✅ [BQTrayRejectionAPIView] Created summary record: total_qty={total_qty}, reasons={len(reasons)}")

            # ✅ Update TrayId records for ALL individual tray IDs
            unique_tray_ids = list(set([item['tray_id'] for item in saved_rejections]))
            updated_tray_count = 0
            
            # ✅ NEW: Track which tray is marked as top tray
            top_tray_id = None
            for rejection in saved_rejections:
                if rejection.get('top_tray', False):
                    top_tray_id = rejection['tray_id']
                    break  # Only one top tray allowed
            
            print(f"🔍 [BQTrayRejectionAPIView] Updating TrayId records for {len(unique_tray_ids)} unique trays: {unique_tray_ids}")
            if top_tray_id:
                print(f"👑 [BQTrayRejectionAPIView] Top tray identified: {top_tray_id}")
            else:
                print(f"📝 [BQTrayRejectionAPIView] No top tray marked")
            
            for tray_id in unique_tray_ids:
                tray_obj = TrayId.objects.filter(tray_id=tray_id).first()
                if tray_obj:
                    tray_total_qty = sum([item['qty'] for item in saved_rejections if item['tray_id'] == tray_id])
                    is_new_tray = getattr(tray_obj, 'new_tray', False)
                    print(f"🔍 [BQTrayRejectionAPIView] Updating tray {tray_id}: new_tray={is_new_tray}, total_qty={tray_total_qty}")
            
                    # Update TrayId fields
                # ✅ CRITICAL FIX: Only mark as rejected_tray=True if tray is actually being rejected 
                # (not if it's being reused for rejection)
                
                # Check if this tray will have remaining quantity after rejection
                # If tray_total_qty > 0, it means the tray still has pieces (reused)
                # If tray_total_qty = 0, it means the tray is fully emptied (rejected)
                should_mark_rejected = (tray_total_qty == 0)
                
                if is_new_tray:
                    tray_obj.lot_id = lot_id
                    tray_obj.rejected_tray = should_mark_rejected  # ✅ FIXED: Only mark rejected if fully consumed
                    mmc = ModelMasterCreation.objects.filter(batch_id=batch_id).first()
                    tray_obj.batch_id = mmc
                    tray_obj.top_tray = (tray_id == top_tray_id)  # ✅ UPDATED: Set top_tray if this is the selected top tray
                    tray_obj.tray_quantity = tray_total_qty
                    tray_obj.save(update_fields=['lot_id', 'rejected_tray','batch_id','top_tray', 'tray_quantity'])
                    print(f"✅ [BQTrayRejectionAPIView] Updated NEW tray {tray_id}: lot_id={lot_id}, rejected_tray={should_mark_rejected}, tray_quantity={tray_total_qty}, top_tray={tray_id == top_tray_id}")
                else:
                    tray_obj.rejected_tray = should_mark_rejected  # ✅ FIXED: Only mark rejected if fully consumed
                    tray_obj.top_tray = (tray_id == top_tray_id)  # ✅ UPDATED: Set top_tray if this is the selected top tray
                    tray_obj.tray_quantity = tray_total_qty
                    tray_obj.save(update_fields=['rejected_tray', 'top_tray', 'tray_quantity'])
                    print(f"✅ [BQTrayRejectionAPIView] Updated EXISTING tray {tray_id}: rejected_tray={should_mark_rejected}, tray_quantity={tray_total_qty}, top_tray={tray_id == top_tray_id}")

                # ✅ FIXED: Sync BrassTrayId table for this tray_id and lot_id
                brass_tray_obj = BrassTrayId.objects.filter(tray_id=tray_id, lot_id=lot_id).first()
                if brass_tray_obj:
                    # ✅ ALWAYS use tray_total_qty (sum of all rejections for this tray)
                    brass_tray_obj.tray_quantity = tray_total_qty
                    brass_tray_obj.rejected_tray = should_mark_rejected  # ✅ FIXED: Only mark rejected if fully consumed
                    brass_tray_obj.top_tray = (tray_id == top_tray_id)  # ✅ UPDATED: Set top_tray if this is the selected top tray
                    brass_tray_obj.save(update_fields=['tray_quantity', 'rejected_tray', 'top_tray'])
                    print(f"✅ [BQTrayRejectionAPIView] Updated BrassTrayId for tray {tray_id}: tray_quantity={tray_total_qty}, rejected_tray={should_mark_rejected}, top_tray={tray_id == top_tray_id}")
                else:
                    # ✅ If not found, create a new BrassTrayId record
                    BrassTrayId.objects.create(
                        tray_id=tray_id,
                        lot_id=lot_id,
                        batch_id=tray_obj.batch_id if hasattr(tray_obj, 'batch_id') else None,
                        tray_quantity=tray_total_qty,  # ✅ ALWAYS use total quantity
                        rejected_tray=should_mark_rejected,  # ✅ FIXED: Only mark rejected if fully consumed
                        top_tray=(tray_id == top_tray_id),  # ✅ UPDATED: Set top_tray if this is the selected top tray
                        tray_type=getattr(tray_obj, 'tray_type', None),
                        tray_capacity=getattr(tray_obj, 'tray_capacity', None),
                        IP_tray_verified=False,
                        new_tray=is_new_tray,
                        delink_tray=False,
                        user=request.user if hasattr(request, 'user') else None,
                        date=timezone.now()
                    )
                    print(f"➕ [BQTrayRejectionAPIView] Created new BrassTrayId for tray_id={tray_id}, tray_quantity={tray_total_qty}, rejected_tray={should_mark_rejected}")
                    updated_tray_count += 1
                
            print(f"✅ [BQTrayRejectionAPIView] Updated {updated_tray_count} tray IDs as rejected")

            # ✅ NEW: Sync top tray selection with IQF system
            if top_tray_id:
                print(f"🔄 [BQTrayRejectionAPIView] Syncing top tray {top_tray_id} with IQF system...")
                
                # Update IQFTrayId record to mark this tray as top tray
                from IQF.models import IQFTrayId
                iqf_tray_obj = IQFTrayId.objects.filter(tray_id=top_tray_id, lot_id=lot_id).first()
                if iqf_tray_obj:
                    iqf_tray_obj.top_tray = True
                    iqf_tray_obj.rejected_tray = True  # Since this is a rejection tray
                    iqf_tray_obj.save(update_fields=['top_tray', 'rejected_tray'])
                    print(f"✅ [BQTrayRejectionAPIView] Updated IQFTrayId for top tray {top_tray_id}")
                else:
                    # Create IQFTrayId record if it doesn't exist
                    tray_obj = TrayId.objects.filter(tray_id=top_tray_id).first()
                    if tray_obj:
                        IQFTrayId.objects.create(
                            lot_id=lot_id,
                            tray_id=top_tray_id,
                            tray_quantity=sum([item['qty'] for item in saved_rejections if item['tray_id'] == top_tray_id]),
                            batch_id=tray_obj.batch_id if hasattr(tray_obj, 'batch_id') else None,
                            top_tray=True,
                            rejected_tray=True,
                            user=request.user,
                            tray_type=getattr(tray_obj, 'tray_type', None),
                            tray_capacity=getattr(tray_obj, 'tray_capacity', None)
                        )
                        print(f"➕ [BQTrayRejectionAPIView] Created IQFTrayId for top tray {top_tray_id}")
                
                # Also update any IQF_Rejected_TrayScan records for this tray
                from IQF.models import IQF_Rejected_TrayScan
                iqf_rejection_records = IQF_Rejected_TrayScan.objects.filter(tray_id=top_tray_id, lot_id=lot_id)
                for iqf_record in iqf_rejection_records:
                    iqf_record.top_tray = True
                    iqf_record.save(update_fields=['top_tray'])
                print(f"🔄 [BQTrayRejectionAPIView] Updated {iqf_rejection_records.count()} IQF rejection records for top tray {top_tray_id}")

            # ✅ NEW: Ensure ALL rejection tray IDs also have BrassTrayId records
            # This handles the case where a new tray (JB-A00150) is used to reject from an original tray (JB-A00050)
            print(f"🔍 [BQTrayRejectionAPIView] Ensuring BrassTrayId records for ALL rejection records...")
            
            for rejection_item in saved_rejections:
                # Each rejection record contains the tray_id that had pieces rejected from it
                rejected_from_tray_id = rejection_item.get('tray_id')
                rejected_qty = rejection_item.get('qty', 0)
                
                if rejected_from_tray_id and rejected_from_tray_id not in unique_tray_ids:
                    # This tray wasn't scanned but had pieces rejected from it - ensure it has a BrassTrayId record
                    print(f"🔍 [BQTrayRejectionAPIView] Creating BrassTrayId for original tray {rejected_from_tray_id} (not scanned)")
                    
                    # Check if BrassTrayId already exists for this tray
                    brass_tray_obj = BrassTrayId.objects.filter(tray_id=rejected_from_tray_id, lot_id=lot_id).first()
                    if not brass_tray_obj:
                        # Get the original TrayId record to get capacity info
                        original_tray_obj = TrayId.objects.filter(tray_id=rejected_from_tray_id).first()
                        if original_tray_obj:
                            # Calculate remaining quantity for this tray (original - total rejected from it)
                            total_rejected_from_tray = sum([item['qty'] for item in saved_rejections if item['tray_id'] == rejected_from_tray_id])
                            original_qty = getattr(original_tray_obj, 'tray_quantity', 0) or getattr(original_tray_obj, 'tray_capacity', 0)
                            remaining_qty = max(0, original_qty - total_rejected_from_tray)
                            
                            # Create BrassTrayId record for the original tray
                            mmc = ModelMasterCreation.objects.filter(batch_id=batch_id).first()
                            BrassTrayId.objects.create(
                                tray_id=rejected_from_tray_id,
                                lot_id=lot_id,
                                batch_id=mmc,
                                tray_quantity=remaining_qty,  # Remaining quantity after rejection
                                rejected_tray=True,  # Mark as rejected since pieces were taken from it
                                top_tray=False,
                                tray_type=getattr(original_tray_obj, 'tray_type', None),
                                tray_capacity=getattr(original_tray_obj, 'tray_capacity', None),
                                IP_tray_verified=True,  # Assume verified since it was in the lot
                                new_tray=False,  # Original tray from the lot
                                delink_tray=False,
                                user=request.user if hasattr(request, 'user') else None,
                                date=timezone.now()
                            )
                            print(f"➕ [BQTrayRejectionAPIView] Created BrassTrayId for original tray {rejected_from_tray_id}: remaining_qty={remaining_qty}, rejected=True")
                            updated_tray_count += 1

            # Decide status based on rejection qty vs physical qty
            if total_qty >= available_qty:
                # All pieces rejected: Check if delink is needed
                print("🔍 All pieces rejected - checking for delink requirements...")
                
                # ✅ NEW: Check if delink trays are needed
                delink_needed = self.check_delink_required(lot_id, available_qty)
                print(f"🔍 Delink needed: {delink_needed}")
                
                if delink_needed:
                    # ✅ NEW: All rejected + delink needed = Keep on hold for delink scanning
                    total_stock_obj.brass_qc_rejection = True
                    total_stock_obj.brass_onhold_picking = True  # ✅ Keep on hold
                    total_stock_obj.brass_qc_few_cases_accptance = False
                    total_stock_obj.send_brass_audit_to_qc = False
                    print("✅ All pieces rejected + delink needed: brass_qc_rejection=True, brass_onhold_picking=True")
                    update_fields = ['brass_qc_rejection', 'brass_onhold_picking', 'brass_qc_few_cases_accptance', 'bq_last_process_date_time','send_brass_audit_to_qc']
                else:
                    # ✅ EXISTING: All rejected + no delink = Complete rejection (remove from pick table)
                    total_stock_obj.brass_qc_rejection = True
                    total_stock_obj.brass_onhold_picking = False  # ✅ Remove from pick table
                    total_stock_obj.brass_qc_few_cases_accptance = False
                    total_stock_obj.send_brass_audit_to_qc = False
                    print("✅ All pieces rejected + no delink: brass_qc_rejection=True, brass_onhold_picking=False")
                    update_fields = ['brass_qc_rejection', 'brass_onhold_picking', 'brass_qc_few_cases_accptance', 'bq_last_process_date_time','send_brass_audit_to_qc']
            else:
                # ✅ EXISTING: Partial rejection logic remains unchanged
                total_stock_obj.brass_onhold_picking = True
                total_stock_obj.brass_qc_few_cases_accptance = True
                total_stock_obj.brass_qc_rejection = False
                print("✅ Partial rejection: brass_qc_few_cases_accptance=True, brass_onhold_picking=True")
                update_fields = ['brass_qc_few_cases_accptance', 'brass_onhold_picking', 'brass_qc_rejection', 'bq_last_process_date_time']
            
            total_stock_obj.brass_qc_accepted_qty = available_qty - total_qty
            total_stock_obj.bq_last_process_date_time = timezone.now()
            update_fields.append('brass_qc_accepted_qty')
            
            total_stock_obj.save(update_fields=update_fields)
            
            # ✅ ENHANCED: Return detailed information about what was saved
            return Response({
                'success': True, 
                'message': f'Tray rejections saved: {len(saved_rejections)} individual records created for {len(unique_tray_ids)} trays.',
                'saved_rejections': saved_rejections,
                'total_qty': total_qty,
                'total_records': len(saved_rejections),
                'unique_tray_ids': unique_tray_ids,
                'updated_tray_count': updated_tray_count
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
def brass_reject_check_tray_id(request):
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

        # ✅ CHECK 4: Must NOT be in Brass_QC_Rejected_TrayScan for this lot
        already_rejected_in_brass = Brass_QC_Rejected_TrayScan.objects.filter(
            lot_id=lot_id,
            rejected_tray_id=tray_id
        ).exists()
        
        if already_rejected_in_brass:
            return JsonResponse({
                'exists': False,
                'error': 'Already rejected in Brass QC',
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
# --- FIX: Allow tray reuse if rejection_qty <= ANY tray's qty, not just the selected tray's qty ---
@require_GET
def brass_reject_check_tray_id_simple(request):
    tray_id = request.GET.get('tray_id', '')
    current_lot_id = request.GET.get('lot_id', '')
    if current_lot_id in ['null', 'None', '', None]:
        return JsonResponse({
            'exists': False,
            'valid_for_rejection': False,
            'error': 'Lot ID is required',
            'status_message': 'Missing Lot ID'
        })
    rejection_qty = int(request.GET.get('rejection_qty', 0))
    current_session_allocations_str = request.GET.get('current_session_allocations', '[]')
    rejection_reason_id = request.GET.get('rejection_reason_id', '')

    try:
        current_session_allocations = json.loads(current_session_allocations_str)
    except Exception:
        current_session_allocations = []

    # ✅ UPDATED: Include delinked trays as available (delink_tray=True means reusable)
    tray_objs = BrassTrayId.objects.filter(lot_id=current_lot_id, rejected_tray=False)
    tray_id_obj = TrayId.objects.filter(tray_id=tray_id).first()

    if not tray_id_obj:
        return JsonResponse({
            'exists': False,
            'valid_for_rejection': False,
            'error': 'Tray ID not found',
            'status_message': 'Not Found'
        })

    is_new_tray = getattr(tray_id_obj, 'new_tray', False)
    is_delinked = getattr(tray_id_obj, 'delink_tray', False)
    
    # ✅ NEW: Check if this is a delinked tray - treat as reusable
    if is_delinked:
        print(f"[BRASS_QC_REJECT_VALIDATION] ✅ DELINKED tray detected: {tray_id} - treating as reusable")
        
        # Validate tray capacity compatibility
        tray_capacity_validation = validate_brass_tray_capacity_compatibility(tray_id_obj, current_lot_id)
        if not tray_capacity_validation['is_compatible']:
            return JsonResponse({
                'exists': False,
                'valid_for_rejection': False,
                'error': tray_capacity_validation['error'],
                'status_message': tray_capacity_validation['status_message'],
                'tray_capacity_mismatch': True,
                'scanned_tray_capacity': tray_capacity_validation['scanned_tray_capacity'],
                'expected_tray_capacity': tray_capacity_validation['expected_tray_capacity']
            })
        
        # Delinked trays are always available for rejection reuse
        return JsonResponse({
            'exists': True,
            'valid_for_rejection': True,
            'status_message': 'Delinked Tray Available for Reuse',
            'validation_type': 'delinked_tray_reusable',
            'tray_capacity_compatible': True,
            'is_delinked': True,
            'tray_capacity': tray_id_obj.tray_capacity or tray_capacity_validation.get('expected_tray_capacity', 12)
        })

# ✅ RESTRUCTURED LOGIC: Follow Brass Audit pattern
    # Get session-adjusted tray quantities for accurate rearrangement calculation
    available_tray_quantities, _ = get_brass_available_quantities_with_session_allocations(current_lot_id, current_session_allocations)

    # Step 1: Check if tray exists in BrassTrayId (existing tray)
    tray_obj = BrassTrayId.objects.filter(tray_id=tray_id, lot_id=current_lot_id).first()

    if tray_obj:
        print(f"[Brass QC Reject Validation] Found in BrassTrayId for lot {current_lot_id}")

        # Check if already rejected
        if tray_obj.rejected_tray:
            return JsonResponse({
                'exists': False,
                'valid_for_rejection': False,
                'error': 'Already rejected in Brass QC',
                'status_message': 'Already Rejected'
            })

        # Validate tray capacity and rearrangement logic for existing tray
        tray_qty = tray_obj.tray_quantity or 0
        tray_capacity = tray_obj.tray_capacity or 12
        remaining_in_tray = tray_qty - rejection_qty

        print(f"[Brass QC Reject Validation] Existing tray analysis:")
        print(f"  - adjusted_current_qty: {tray_qty}")
        print(f"  - rejection_qty: {rejection_qty}")
        print(f"  - remaining_in_tray: {remaining_in_tray}")

        # Check rearrangement needs based on remaining pieces
        if remaining_in_tray > 0:
            # Pieces will remain in this tray - check if they can fit in other trays
            other_trays = BrassTrayId.objects.filter(
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
                    'status_message': 'Need New Tray',
                    'validation_type': 'existing_no_space',
                    'remaining_in_tray': remaining_in_tray,
                    'available_space_in_other_trays': available_space_in_other_trays
                })
        
        elif remaining_in_tray < 0:
            # This tray doesn't have enough pieces - need additional pieces from other trays
            additional_needed = abs(remaining_in_tray)
            
            # Get other trays and their available quantities (after accounting for session allocations)
            other_trays = BrassTrayId.objects.filter(
                lot_id=current_lot_id,
                rejected_tray=False
            ).exclude(tray_id=tray_id)
            
            # Calculate how many additional pieces are available from other trays
            # (their current quantities represent what's available for redistribution)
            available_from_other_trays = 0
            for t in other_trays:
                current_qty = t.tray_quantity or 0
                available_from_other_trays += current_qty
            
            if additional_needed > available_from_other_trays:
                return JsonResponse({
                    'exists': False,
                    'valid_for_rejection': False,
                    'error': f'Cannot reject: need {additional_needed} more pieces but only {available_from_other_trays} available in other trays',
                    'status_message': 'Need New Tray',
                    'validation_type': 'existing_no_space',
                    'additional_needed': additional_needed,
                    'available_from_other_trays': available_from_other_trays
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
    
    # Step 2: Not found in BrassTrayId, check TrayId for new/existing tray availability
    print(f"[Brass QC Reject Validation] Not found in BrassTrayId, checking TrayId")
    
    # Check if tray belongs to a different lot
    if tray_id_obj.lot_id and str(tray_id_obj.lot_id).strip():
        if str(tray_id_obj.lot_id).strip() != str(current_lot_id).strip():
            return JsonResponse({
                'exists': False,
                'valid_for_rejection': False,
                'error': 'Tray belongs to different lot',
                'status_message': 'Different Lot'
            })
    
    # Check if it's a new tray (no lot_id or empty lot_id)
    is_new_tray = (not tray_id_obj.lot_id or str(tray_id_obj.lot_id).strip() == '')
    
    if is_new_tray:
        # Validate tray capacity compatibility
        tray_capacity_validation = validate_brass_tray_capacity_compatibility(tray_id_obj, current_lot_id)
        if not tray_capacity_validation['is_compatible']:
            return JsonResponse({
                'exists': False,
                'valid_for_rejection': False,
                'error': tray_capacity_validation['error'],
                'status_message': tray_capacity_validation['status_message'],
                'tray_capacity_mismatch': True,
                'scanned_tray_capacity': tray_capacity_validation['scanned_tray_capacity'],
                'expected_tray_capacity': tray_capacity_validation['expected_tray_capacity']
            })
        
        return JsonResponse({
            'exists': True,
            'valid_for_rejection': True,
            'status_message': 'New Tray Available',
            'validation_type': 'new_tray_from_master',
            'tray_capacity_compatible': True,
            'tray_capacity': tray_id_obj.tray_capacity or tray_capacity_validation['expected_tray_capacity']
        })
    
    # If we reach here, tray exists in TrayId with same lot_id but not in BrassTrayId
    # This could be a valid scenario - treat as available existing tray
    tray_capacity_validation = validate_brass_tray_capacity_compatibility(tray_id_obj, current_lot_id)
    if not tray_capacity_validation['is_compatible']:
        return JsonResponse({
            'exists': False,
            'valid_for_rejection': False,
            'error': tray_capacity_validation['error'],
            'status_message': tray_capacity_validation['status_message'],
            'tray_capacity_mismatch': True,
            'scanned_tray_capacity': tray_capacity_validation['scanned_tray_capacity'],
            'expected_tray_capacity': tray_capacity_validation['expected_tray_capacity']
        })
    
    return JsonResponse({
        'exists': True,
        'valid_for_rejection': True,
        'status_message': 'Available (from TrayId)',
        'validation_type': 'existing_tray_from_master',
        'tray_capacity_compatible': True,
        'tray_capacity': tray_id_obj.tray_capacity or tray_capacity_validation['expected_tray_capacity']
    })


      
# ✅ NEW: Helper function to validate tray capacity compatibility for Brass QC
def validate_brass_tray_capacity_compatibility(tray_obj, lot_id):
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


# ✅ NEW: Helper function to get expected tray capacity for a Brass QC lot
def get_expected_tray_capacity_for_brass_lot(lot_id):
    """
    Get the expected tray capacity for a specific lot in Brass QC
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
        existing_tray = BrassTrayId.objects.filter(
            lot_id=lot_id, 
            rejected_tray=False,
            tray_capacity__isnull=False
        ).first()
        if existing_tray and existing_tray.tray_capacity:
            print(f"[Expected Brass Tray Capacity] Found from existing tray: {existing_tray.tray_capacity}")
            return existing_tray.tray_capacity
        
        # Method 3: Get from IP_Accepted_TrayID_Store (if tray was processed in IP)
        ip_accepted = IP_Accepted_TrayID_Store.objects.filter(lot_id=lot_id).first()
        if ip_accepted and ip_accepted.top_tray_id:
            ip_tray = BrassTrayId.objects.filter(tray_id=ip_accepted.top_tray_id).first()
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
    Calculate available tray quantities and ACTUAL free space for Brass QC
    """
    try:
        # Get original distribution and track free space separately
        original_distribution = get_brass_original_tray_distribution(lot_id)
        original_capacities = get_brass_tray_capacities_for_lot(lot_id)
        
        available_quantities = original_distribution.copy()
        new_tray_usage_count = 0  # Track NEW tray usage for free space calculation
        
        print(f"[Brass Session Validation] Starting with: {available_quantities}")
        
        # First, apply saved rejections
        saved_rejections = Brass_QC_Rejected_TrayScan.objects.filter(lot_id=lot_id).order_by('id')
        
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
    Reduce quantities optimally for Brass QC with enhanced logic
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
    Get original tray quantity distribution for the lot in Brass QC context
    ✅ FIXED: Exclude trays rejected in Input Screening (rejected_tray=True)
    """
    try:
        print(f"[Brass Original Distribution] Getting distribution for lot_id: {lot_id}")
        
        # ✅ CRITICAL FIX: Exclude trays rejected in Input Screening AND Brass QC
        tray_objects = BrassTrayId.objects.filter(lot_id=lot_id).exclude(
            rejected_tray=True  # ✅ Exclude Input Screening rejected trays
        ).exclude(
            rejected_tray=True  # ✅ Exclude Brass QC rejected trays
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
        
        # Fallback: Calculate from brass_physical_qty and standard capacity
        total_stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
        if not total_stock:
            print(f"[Brass Original Distribution] No TotalStockModel found for lot_id: {lot_id}")
            return []
        
        # ✅ UPDATED: Only use brass_physical_qty
        total_qty = 0
        if hasattr(total_stock, 'brass_physical_qty') and total_stock.brass_physical_qty:
            total_qty = total_stock.brass_physical_qty
        else:
            print(f"[Brass Original Distribution] No brass_physical_qty available for lot_id: {lot_id}")
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
    Get all tray capacities for a lot in Brass QC context
    ✅ FIXED: Exclude rejected trays from capacity calculation
    """
    try:
        print(f"[get_brass_tray_capacities_for_lot] Getting all capacities for lot_id: {lot_id}")
        
        # ✅ CRITICAL FIX: Exclude rejected trays from capacity calculation
        tray_objects = BrassTrayId.objects.filter(lot_id=lot_id).exclude(
            rejected_tray=True  # ✅ Exclude Input Screening rejected trays
        ).exclude(
            rejected_tray=True  # ✅ Exclude Brass QC rejected trays
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
    Get tray capacity for a lot from BrassTrayId table (DYNAMIC) - Brass QC version
    """
    try:
        print(f"[get_brass_tray_capacity_for_lot] Getting capacity for lot_id: {lot_id}")
        
        # Get tray capacity from BrassTrayId table for this specific lot
        tray_objects = BrassTrayId.objects.filter(lot_id=lot_id).exclude(rejected_tray=True)
        
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
    ✅ FIXED: Check BrassTrayId first (Brass QC context), then TrayId
    """
    try:
        # First check BrassTrayId (Brass QC specific)
        brass_tray_obj = BrassTrayId.objects.filter(tray_id=tray_id).first()
        if brass_tray_obj:
            is_new = getattr(brass_tray_obj, 'new_tray', False)
            print(f"[is_new_tray_by_id] Found in BrassTrayId: {tray_id}, new_tray={is_new}")
            return is_new
        
        # Fall back to TrayId (modelmasterapp)
        from modelmasterapp.models import TrayId
        tray_obj = TrayId.objects.filter(tray_id=tray_id).first()
        if tray_obj:
            is_new = getattr(tray_obj, 'new_tray', False)
            print(f"[is_new_tray_by_id] Found in TrayId: {tray_id}, new_tray={is_new}")
            return is_new
        
        print(f"[is_new_tray_by_id] Tray not found: {tray_id}, defaulting to False")
        return False
    except Exception as e:
        print(f"[is_new_tray_by_id] Error: {e}")
        import traceback
        traceback.print_exc()
        return False
#=======================================================
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_brass_qc_tray_details_for_modal(request):
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

        if stock_obj.current_stage == 'Brass QC':
            if BrassTrayId.objects.filter(lot_id=lot_id).exists():
                # ✅ FIX: Order by top_tray first, then tray_quantity ascending (smallest qty = top tray)
                trays = BrassTrayId.objects.filter(lot_id=lot_id).order_by('-top_tray', 'tray_quantity')
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
                # Fetch from TrayId, but update qtys and top_tray from BrassAuditTrayId
                tray_objs = TrayId.objects.filter(lot_id=lot_id)
                brass_audit_trays = {t.tray_id: t for t in BrassAuditTrayId.objects.filter(lot_id=lot_id)}
                for tray in tray_objs:
                    audit_tray = brass_audit_trays.get(tray.tray_id)
                    qty = audit_tray.tray_quantity if audit_tray else tray.tray_capacity or 12
                    top_tray = audit_tray.top_tray if audit_tray else False
                    accepted_trays.append({
                        'tray_id': tray.tray_id,
                        'tray_quantity': qty,
                        'top_tray': top_tray
                    })
                    total_accepted_qty += qty
        else:
            # ✅ FIX: Order by top_tray first, then tray_quantity ascending (smallest qty = top tray)
            trays = BrassTrayId.objects.filter(lot_id=lot_id).order_by('-top_tray', 'tray_quantity')
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

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def brass_get_delink_tray_data(request):
    try:
        lot_id = request.GET.get('lot_id')
        if not lot_id:
            return Response({'success': False, 'error': 'Missing lot_id'}, status=400)
        
        stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
        if not stock:
            return Response({'success': False, 'error': 'Stock not found'}, status=400)
        
        original_distribution = get_brass_actual_tray_distribution_for_delink(lot_id, stock)
        print(f"🔍 [brass_get_delink_tray_data] Original distribution: {original_distribution}")

        if not original_distribution:
            return Response({'success': True, 'delink_trays': [], 'message': 'No tray distribution found'})

        # Apply rejections first
        current_distribution = brass_calculate_distribution_after_rejections_enhanced(lot_id, original_distribution)
        print(f"🔍 [brass_get_delink_tray_data] Current distribution after rejections: {current_distribution}")

        # ✅ NEW: Apply missing_qty (shortage) after rejections
        missing_qty = stock.brass_missing_qty or 0
        if missing_qty > 0:
            print(f"🔍 [brass_get_delink_tray_data] Applying missing_qty: {missing_qty}")
            current_distribution = brass_consume_shortage_from_distribution(current_distribution, missing_qty)
            print(f"🔍 [brass_get_delink_tray_data] Distribution after missing_qty: {current_distribution}")

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

        print(f"🔍 [brass_get_delink_tray_data] Empty tray positions: {empty_tray_positions}")
        print(f"🔍 [brass_get_delink_tray_data] Total empty trays needing delink: {len(delink_trays)}")

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
    ✅ FIXED: Always calculate from brass_physical_qty for accurate delink detection
    """
    try:
        print(f"🔍 [get_brass_actual_tray_distribution_for_delink] Getting distribution for lot_id: {lot_id}")
        
        # ✅ ALWAYS use brass_physical_qty for delink calculations
        total_qty = 0
        if hasattr(stock, 'brass_missing_qty') and stock.brass_missing_qty:
            # If brass_missing_qty is present, use IP accepted quantity
            total_qty = getattr(stock, 'total_IP_accpeted_quantity', 0)
        elif hasattr(stock, 'brass_physical_qty') and stock.brass_physical_qty:
            total_qty = stock.brass_physical_qty
        else:
            print(f"❌ No brass_physical_qty available for lot_id: {lot_id}")
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

def brass_calculate_distribution_after_rejections_enhanced(lot_id, original_distribution):
    """
    Enhanced calculation with detailed logging for debugging delink logic.
    Properly handles NEW tray rejections vs EXISTING tray rejections.
    """
    current_distribution = original_distribution.copy()
    
    # Get all rejections for this lot ordered by creation
    rejections = Brass_QC_Rejected_TrayScan.objects.filter(lot_id=lot_id).order_by('id')
    
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
    
    # ✅ ENHANCED: Analyze empty positions
    empty_positions = [i for i, qty in enumerate(current_distribution) if qty == 0]
    print(f"🔧 [Enhanced Distribution Calc] Empty positions: {empty_positions}")
    
    return current_distribution

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
    rejections = Brass_QC_Rejected_TrayScan.objects.filter(lot_id=lot_id).order_by('id')
    
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
def brass_delink_check_tray_id(request):
    """
    Validate tray ID for delink process in Brass QC
    Check if tray exists in same lot and is not already rejected
    ✅ UPDATED: Do NOT allow new trays (without lot_id)
    """
    tray_id = request.GET.get('tray_id', '').strip()
    current_lot_id = request.GET.get('lot_id', '').strip()

    try:
        if not tray_id:
            return JsonResponse({
                'exists': False,
                'valid_for_rejection': False,
                'error': 'Tray ID is required',
                'status_message': 'Required'
            })

        # --- NEW: Check if tray exists in TrayId table (master table) ---
        tray_master = TrayId.objects.filter(tray_id=tray_id).first()
        
        if not tray_master:
            return JsonResponse({
                'exists': False,
                'valid_for_rejection': False,
                'error': 'Tray ID not found',
                'status_message': 'Not Found'
            })

        # --- NEW: Tray exists in master table, now check if it's in BrassTrayId for this lot ---
        tray_obj = BrassTrayId.objects.filter(tray_id=tray_id, lot_id=current_lot_id).first()
        
        if not tray_obj:
            # Tray exists in master table but not in this lot's BrassTrayId
            return JsonResponse({
                'exists': False,
                'valid_for_rejection': False,
                'error': f'Tray ID {tray_id} already exists in different lot',
                'status_message': f'Exists in TrayId table (Lot: {tray_master.lot_id})'
            })

        # --- NEW: Check if tray is a new tray (never assigned to any lot) ---
        if not tray_obj.lot_id or tray_obj.lot_id == '' or tray_obj.lot_id is None:
            return JsonResponse({
                'exists': False,
                'valid_for_rejection': False,
                'error': 'New trays not allowed for delink',
                'status_message': 'New Tray Not Allowed'
            })

        # ✅ Check if tray is already rejected in IPTrayId
        ip_tray_obj = IPTrayId.objects.filter(tray_id=tray_id, lot_id=current_lot_id).first()
        if ip_tray_obj and getattr(ip_tray_obj, 'rejected_tray', False):
            return JsonResponse({
                'exists': False,
                'valid_for_rejection': False,
                'error': 'Already rejected in Input Screening',
                'status_message': 'Already Rejected in IP'
            })

        # ✅ Do NOT allow new trays (without lot_id)
        if not tray_obj.lot_id or tray_obj.lot_id == '' or tray_obj.lot_id is None:
            return JsonResponse({
                'exists': False,
                'valid_for_rejection': False,
                'error': 'New trays not allowed for delink',
                'status_message': 'New Tray Not Allowed'
            })

        # ✅ Must belong to same lot
        if str(tray_obj.lot_id).strip() != str(current_lot_id).strip():
            return JsonResponse({
                'exists': False,
                'valid_for_rejection': False,
                'error': 'Different lot',
                'status_message': 'Different Lot'
            })

        # ✅ Must NOT be already rejected
        if hasattr(tray_obj, 'rejected_tray') and tray_obj.rejected_tray:
            return JsonResponse({
                'exists': False,
                'valid_for_rejection': False,
                'error': 'Already rejected',
                'status_message': 'Already Rejected'
            })

        # ✅ Must NOT be in Brass_QC_Rejected_TrayScan for this lot
        already_rejected_in_brass = Brass_QC_Rejected_TrayScan.objects.filter(
            lot_id=current_lot_id,
            rejected_tray_id=tray_id
        ).exists()
        if already_rejected_in_brass:
            return JsonResponse({
                'exists': False,
                'valid_for_rejection': False,
                'error': 'Already rejected in Brass QC',
                'status_message': 'Already Rejected'
            })

        # ✅ Must NOT be already delinked
        if hasattr(tray_obj, 'delink_tray') and tray_obj.delink_tray:
            return JsonResponse({
                'exists': False,
                'valid_for_rejection': False,
                'error': 'Already delinked',
                'status_message': 'Already Delinked'
            })

        # ✅ Must be verified (additional validation for delink)
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
        print(f"❌ [brass_delink_check_tray_id] Error: {e}")
        return JsonResponse({
            'exists': False,
            'valid_for_rejection': False,
            'error': 'System error',
            'status_message': 'System Error'
        })
        
        
@require_GET
def brass_top_tray_check_tray_id(request):
    """
    Validate tray ID for top tray scan in Brass QC
    Rules:
    1. Accept only same lot ID (Valid Tray)
    2. Not accept different lot ID (Tray ID already exists in different lot)
    3. Accept new tray IDs (Available New trays)
    4. If Tray ID not in model master table (Tray ID not found)
    5. Top tray and delink tray cannot be the same (checked in frontend)
    """
    tray_id = request.GET.get('tray_id', '').strip()
    current_lot_id = request.GET.get('lot_id', '').strip()

    try:
        if not tray_id:
            return JsonResponse({
                'exists': False,
                'valid_for_top_tray': False,
                'error': 'Tray ID is required',
                'status_message': 'Required'
            })

        # 1. Check if tray exists in TrayId table (master table)
        tray_master = TrayId.objects.filter(tray_id=tray_id).first()
        
        if not tray_master:
            return JsonResponse({
                'exists': False,
                'valid_for_top_tray': False,
                'error': 'Tray ID not found',
                'status_message': 'Tray ID not found'
            })

        # 2. Check if tray is already assigned to this lot (same lot)
        tray_in_lot = BrassTrayId.objects.filter(tray_id=tray_id, lot_id=current_lot_id).first()
        
        if tray_in_lot:
            # Tray exists in same lot - check if it's rejected or already used as top tray
            if getattr(tray_in_lot, 'rejected_tray', False):
                return JsonResponse({
                    'exists': False,
                    'valid_for_top_tray': False,
                    'error': 'Tray is rejected',
                    'status_message': 'Tray is rejected'
                })
            
            # ✅ ADDED: Check if tray has been rejected in Brass_QC_Rejected_TrayScan
            already_rejected_in_brass = Brass_QC_Rejected_TrayScan.objects.filter(
                lot_id=current_lot_id,
                rejected_tray_id=tray_id
            ).exists()
            if already_rejected_in_brass:
                return JsonResponse({
                    'exists': False,
                    'valid_for_top_tray': False,
                    'error': 'Tray has been rejected in Brass QC',
                    'status_message': 'Tray has been rejected in Brass QC'
                })
            
            # Valid tray from same lot
            return JsonResponse({
                'exists': True,
                'valid_for_top_tray': True,
                'status_message': 'Valid Tray',
                'validation_type': 'same_lot',
                'tray_quantity': getattr(tray_in_lot, 'tray_quantity', 0) or 0
            })

        # 3. Tray exists in master table but not in this lot - check if it's assigned to different lot
        if tray_master.lot_id and str(tray_master.lot_id).strip():
            # Tray is assigned to a different lot
            return JsonResponse({
                'exists': False,
                'valid_for_top_tray': False,
                'error': f'Tray ID {tray_id} already exists in different lot',
                'status_message': f'Tray ID {tray_id} already exists in different lot'
            })

        # 4. Tray exists in master table but not assigned to this lot - NOT ALLOWED for top tray
        # For top tray, only existing trays from the lot are allowed, no new trays
        return JsonResponse({
            'exists': False,
            'valid_for_top_tray': False,
            'error': 'Only existing trays from this lot can be used as top tray',
            'status_message': 'Only existing lot trays allowed'
        })

    except Exception as e:
        print(f"❌ [brass_top_tray_check_tray_id] Error: {e}")
        return JsonResponse({
            'exists': False,
            'valid_for_top_tray': False,
            'error': 'System error',
            'status_message': 'System Error'
        })
        
        
#=========================================================

# This endpoint retrieves top tray scan data for a given lot_id
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def brass_get_accepted_tray_scan_data(request):
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
        reason_store = Brass_QC_Rejection_ReasonStore.objects.filter(lot_id=lot_id).first()
        total_rejection_qty = reason_store.total_rejection_quantity if reason_store else 0

        # ✅ UPDATED: Only use brass_physical_qty
        if stock.brass_physical_qty and stock.brass_physical_qty > 0:
            brass_physical_qty = stock.brass_physical_qty
        else:
            return Response({'success': False, 'error': 'No brass physical quantity available'}, status=400)

        # ✅ CORRECTED: Calculate available_qty after subtracting rejections
        available_qty = brass_physical_qty - total_rejection_qty
        
        print(f"📐 [brass_get_accepted_tray_scan_data] brass_physical_qty = {brass_physical_qty}")
        print(f"📐 [brass_get_accepted_tray_scan_data] total_rejection_qty = {total_rejection_qty}")
        print(f"📐 [brass_get_accepted_tray_scan_data] available_qty = {available_qty}")

        # ✅ NEW: Check if this is for delink-only mode (when available_qty = 0 but have rejections with NEW trays)
        is_delink_only_case = (available_qty <= 0 and total_rejection_qty > 0)
        
        if is_delink_only_case:
            print(f"🚨 [brass_get_accepted_tray_scan_data] Delink-only case detected: all pieces rejected")
            # ✅ NEW: For delink-only case, set minimal values but still allow the process to continue
            return Response({
                'success': True,
                'model_no': model_no,
                'tray_capacity': tray_capacity,
                'brass_physical_qty': brass_physical_qty,
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

        print(f"📊 [brass_get_accepted_tray_scan_data] Tray calculation: {available_qty} qty = {full_trays} full trays + {top_tray_qty} top tray")

        # Check for existing draft data
        has_draft = Brass_Qc_Accepted_TrayID_Store.objects.filter(lot_id=lot_id, is_draft=True).exists()
        draft_tray_id = ""
        
        if has_draft:
            draft_record = Brass_Qc_Accepted_TrayID_Store.objects.filter(lot_id=lot_id, is_draft=True).first()
            if (draft_record):
                draft_tray_id = draft_record.tray_id
        
        return Response({
            'success': True,
            'model_no': model_no,
            'tray_capacity': tray_capacity,
            'brass_physical_qty': brass_physical_qty,
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
def brass_save_single_top_tray_scan(request):
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
            top_tray_obj = BrassTrayId.objects.filter(tray_id=tray_id, lot_id=lot_id).first()
            if top_tray_obj:
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
                    delink_tray_obj = BrassTrayId.objects.filter(tray_id=delink_tray_id, lot_id=lot_id).first()
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
            print(f"🔍 [TOP TRAY SCAN] Updating BrassTrayId for lot_id={lot_id}, tray_id={tray_id}, qty={tray_qty}")
            
            # ✅ UPDATED: Update top tray only if provided
            if tray_id:
                top_tray_obj = BrassTrayId.objects.filter(tray_id=tray_id, lot_id=lot_id).first()
                if top_tray_obj:
                    old_qty = top_tray_obj.tray_quantity
                    top_tray_obj.top_tray = True
                    top_tray_obj.tray_quantity = tray_qty
                    top_tray_obj.save(update_fields=['top_tray', 'tray_quantity'])
                    print(f"✅ [brass_save_single_top_tray_scan] Updated top tray: {tray_id}, old_qty={old_qty}, new_qty={tray_qty}")
                else:
                    # Create new BrassTrayId for new tray
                    stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
                    batch_id = stock.batch_id if stock else None
                    BrassTrayId.objects.create(
                        lot_id=lot_id,
                        tray_id=tray_id,
                        tray_quantity=tray_qty,
                        batch_id=batch_id,
                        user=user,
                        top_tray=True,
                        new_tray=True,
                        delink_tray=False,
                        rejected_tray=False,
                        IP_tray_verified=False
                    )
                    # Save to Brass_Qc_Accepted_TrayID_Store
                    Brass_Qc_Accepted_TrayID_Store.objects.create(
                        lot_id=lot_id,
                        tray_id=tray_id,
                        tray_qty=tray_qty,
                        user=user,
                        is_draft=False,
                        is_save=True
                    )
                    print(f"✅ [brass_save_single_top_tray_scan] Created new top tray: {tray_id}")
        
                # Update all other trays (except rejected and top tray) to have tray_quantity = tray_capacity
                all_trays_in_lot = BrassTrayId.objects.filter(lot_id=lot_id, rejected_tray=False)
                for tray in all_trays_in_lot:
                    if tray.tray_id == tray_id or tray.delink_tray:
                        continue
                    old_qty = tray.tray_quantity
                    tray.tray_quantity = tray.tray_capacity
                    tray.top_tray = False
                    tray.save(update_fields=['tray_quantity', 'top_tray'])
                    print(f"   Updated BrassTrayId tray {tray.tray_id}: qty {old_qty}→{tray.tray_capacity}, top_tray=False")

            # ✅ UPDATED: Process delink trays (works for both normal and delink-only modes)
            for delink in delink_trays:
                delink_tray_id = delink.get('tray_id', '').strip()
                if delink_tray_id:
                    delink_count += 1
                    
                    # BrassTrayId
                    brass_delink_tray_obj = BrassTrayId.objects.filter(tray_id=delink_tray_id, lot_id=lot_id).first()
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
        
                    # IPTrayId
                    ip_delink_tray_obj = IPTrayId.objects.filter(tray_id=delink_tray_id, lot_id=lot_id).first()
                    if ip_delink_tray_obj:
                        ip_delink_tray_obj.delink_tray = True
                        ip_delink_tray_obj.save(update_fields=['delink_tray'])
                        print(f"✅ Delinked IPTrayId tray: {delink_tray_id} for lot: {lot_id}")
                    
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
                    stock.brass_accepted_tray_scan_status = True  # Mark as completed
                    stock.next_process_module = "Jig Loading"     # Or appropriate next module
                    stock.last_process_module = "Brass QC"
                    stock.bq_last_process_date_time = timezone.now()  # Set the last process date/time
                    stock.brass_onhold_picking = False
                    stock.send_brass_qc = False
                    stock.send_brass_audit_to_qc = False
                    print(f"✅ Updated stock for DELINK-ONLY mode")
                else:
                    # Normal mode
                    stock.brass_accepted_tray_scan_status = True
                    stock.next_process_module = "Jig Loading"
                    stock.last_process_module = "Brass QC"
                    stock.brass_onhold_picking = False
                    stock.send_brass_qc = False
                    stock.send_brass_audit_to_qc = False
                    stock.bq_last_process_date_time = timezone.now()  # Set the last process date/time

                    # Update accepted qty
                    # Per user calculations:
                    # Lot Qty: 78
                    # Rej Qty: 40
                    # Remaining: 78 - 40 = 38
                    # Tray Distribution after rejection:
                    # NB-A00220: 14 - 8 (rejected) = 6 remaining
                    # NB-A00221: 16
                    # NB-A00222: 16
                    # Total remaining: 6 + 16 + 16 = 38
                    # For new top trays (e.g., NB-A00205 for 6), don't add to brass_qc_accepted_qty
                    # as the 38 already includes remaining tray quantities.
                    total_accepted = 0  # Exclude top_tray_qty for new trays to keep at 38
                    for item in delink_trays:
                        total_accepted += item.get('original_capacity', 0)

                    if total_accepted > 0:
                        stock.brass_qc_accepted_qty = F('brass_qc_accepted_qty') + total_accepted
                        stock.save(update_fields=['brass_qc_accepted_qty'])

                    print(f"✅ Updated stock for NORMAL mode")
                
                stock.save(update_fields=[
                    'brass_accepted_tray_scan_status',
                    'bq_last_process_date_time',
                    'next_process_module',
                    'last_process_module',
                    'brass_onhold_picking',
                    'send_brass_qc',
                    'send_brass_audit_to_qc'
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
            draft_obj, created = Brass_TopTray_Draft_Store.objects.update_or_create(
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
def brass_get_top_tray_scan_draft(request):
    lot_id = request.GET.get('lot_id')
    if not lot_id:
        return Response({'success': False, 'error': 'Missing lot_id'}, status=400)
    try:
        draft_obj = Brass_TopTray_Draft_Store.objects.filter(lot_id=lot_id).first()
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


@method_decorator(login_required, name='dispatch')
class BrassValidateTrayIdAPIView(APIView):
    def get(self, request):
        tray_id = request.GET.get('tray_id')
        lot_id = request.GET.get('lot_id')
        exists = BrassTrayId.objects.filter(tray_id=tray_id, lot_id=lot_id).exists()
        return Response({
            'exists': exists,
            'valid_for_lot': exists
        })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def brass_view_tray_list(request):
    """
    Returns tray list for a given lot_id based on different conditions:
    1. If brass_qc_accptance is True: get from BrassTrayId table
    2. If batch_rejection is True: split total_rejection_quantity by tray_capacity and get tray_ids from TrayId
    3. If batch_rejection is False: return all trays from IQF_Accepted_TrayID_Store
    """
    lot_id = request.GET.get('lot_id')
    if not lot_id:
        return Response({'success': False, 'error': 'Missing lot_id'}, status=400)

    try:
        # Check if this lot has brass_qc_accptance = True
        stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
        brass_qc_accptance = False
        tray_capacity = 0
        
        if stock:
            brass_qc_accptance = stock.brass_qc_accptance or False
            if stock.batch_id and hasattr(stock.batch_id, 'tray_capacity'):
                tray_capacity = stock.batch_id.tray_capacity or 0

        tray_list = []

        # Include rejected trays from Input Screening if any
        rejected_trays = IP_Rejected_TrayScan.objects.filter(lot_id=lot_id).order_by('id')
        rejected_count = 0
        for tray in rejected_trays:
            tray_list.append({
                'sno': rejected_count + 1,
                'tray_id': tray.rejected_tray_id or '',
                'tray_qty': 'REJECTED',
                'is_rejected': True,
                'ip_top_tray': False,
                'brass_top_tray': False,
                'top_tray': False,
            })
            rejected_count += 1
        
        # ✅ NEW: Check for Brass QC lot rejection (brass_rejected_tray=True in TrayId)
        # Priority order: 1. BrassTrayId (most recent), 2. TrayId, 3. Brass_QC_Rejected_TrayScan
        # Use a set to track already-added tray_ids to prevent duplicates
        added_tray_ids = set()
        
        # ✅ FIXED: Exclude delinked trays - only show actual rejected trays
        brass_rejected_trays_brass = BrassTrayId.objects.filter(
            lot_id=lot_id, 
            rejected_tray=True,
            delink_tray=False  # ✅ Exclude delinked trays
        ).order_by('tray_id')
        brass_rejected_trays_trayid = TrayId.objects.filter(lot_id=lot_id, brass_rejected_tray=True).order_by('tray_id')
        brass_rejected_scans = Brass_QC_Rejected_TrayScan.objects.filter(lot_id=lot_id).order_by('id')
        
        # Check if we have any rejected trays from any source
        has_brass_rejection = (brass_rejected_trays_brass.exists() or 
                              brass_rejected_trays_trayid.exists() or 
                              brass_rejected_scans.exists())
        
        if has_brass_rejection:
            print(f"✅ [BRASS LOT REJECTION] Found brass rejected trays for lot {lot_id}")
            print(f"   - BrassTrayId count: {brass_rejected_trays_brass.count()}")
            print(f"   - TrayId count: {brass_rejected_trays_trayid.count()}")
            print(f"   - Brass_QC_Rejected_TrayScan count: {brass_rejected_scans.count()}")
            
            # Priority 1: Get from BrassTrayId table (most authoritative for Brass Audit rejections)
            for tray in brass_rejected_trays_brass:
                if tray.tray_id and tray.tray_id not in added_tray_ids:
                    tray_list.append({
                        'sno': rejected_count + 1,
                        'tray_id': tray.tray_id,
                        'tray_qty': tray.tray_quantity or 0,
                        'is_rejected': True,
                        'rejected_status': 'REJECTED',
                        'ip_top_tray': False,
                        'brass_top_tray': tray.top_tray if hasattr(tray, 'top_tray') else False,
                        'top_tray': tray.top_tray if hasattr(tray, 'top_tray') else False,
                    })
                    added_tray_ids.add(tray.tray_id)
                    rejected_count += 1
                    print(f"   ✅ Added rejected tray from BrassTrayId: {tray.tray_id} (qty: {tray.tray_quantity})")
            
            # Priority 2: Get from TrayId table (only if not already added from BrassTrayId)
            for tray in brass_rejected_trays_trayid:
                if tray.tray_id and tray.tray_id not in added_tray_ids:
                    tray_list.append({
                        'sno': rejected_count + 1,
                        'tray_id': tray.tray_id,
                        'tray_qty': tray.tray_quantity or 0,
                        'is_rejected': True,
                        'rejected_status': 'REJECTED',
                        'ip_top_tray': tray.ip_top_tray if hasattr(tray, 'ip_top_tray') else False,
                        'brass_top_tray': tray.brass_top_tray if hasattr(tray, 'brass_top_tray') else False,
                        'top_tray': tray.top_tray if hasattr(tray, 'top_tray') else False,
                    })
                    added_tray_ids.add(tray.tray_id)
                    rejected_count += 1
                    print(f"   ✅ Added rejected tray from TrayId: {tray.tray_id} (qty: {tray.tray_quantity})")
            
            # Priority 3: Get from Brass_QC_Rejected_TrayScan table (only if not already added)
            for scan in brass_rejected_scans:
                tray_id = scan.rejected_tray_id or ''
                if tray_id and tray_id not in added_tray_ids:
                    tray_list.append({
                        'sno': rejected_count + 1,
                        'tray_id': tray_id,
                        'tray_qty': int(scan.rejected_tray_quantity) if scan.rejected_tray_quantity else 0,
                        'is_rejected': True,
                        'rejected_status': 'REJECTED',
                        'ip_top_tray': False,
                        'brass_top_tray': False,
                        'top_tray': False,
                    })
                    added_tray_ids.add(tray_id)
                    rejected_count += 1
                    print(f"   ✅ Added rejected tray from Brass_QC_Rejected_TrayScan: {tray_id} (qty: {scan.rejected_tray_quantity})")
            
            # If we have Brass QC lot rejections, return rejected trays
            print(f"   📊 Total rejected trays to return: {len(tray_list)}")
            return Response({
                'success': True,
                'brass_qc_accptance': brass_qc_accptance,
                'batch_rejection': True,
                'total_rejection_qty': 0,
                'tray_capacity': tray_capacity,
                'trays': tray_list,
                'is_brass_lot_rejection': True,
            })

        # Condition 1: If brass_qc_accptance is True, get from BrassTrayId table
        # ✅ FIXED: Use duplicate prevention for brass_qc_accptance trays too
        if brass_qc_accptance:
            # ✅ NEW: Check if data is coming back from IQF with different quantity
            brass_physical_qty = stock.brass_physical_qty if stock and stock.brass_physical_qty else 0
            iqf_accepted_qty = stock.iqf_accepted_qty if stock and stock.iqf_accepted_qty else 0
            
            # If brass_physical_qty is set and differs from original acceptance, redistribute trays
            if brass_physical_qty > 0 and iqf_accepted_qty > 0:
                print(f"✅ [brass_view_tray_list] IQF accepted data returned to Brass QC")
                print(f"   brass_physical_qty: {brass_physical_qty}, iqf_accepted_qty: {iqf_accepted_qty}")
                
                # Get all tray IDs for this lot (unique)
                all_trays = BrassTrayId.objects.filter(lot_id=lot_id).order_by('id')
                unique_tray_ids = []
                seen = set()
                for tray in all_trays:
                    if tray.tray_id and tray.tray_id not in seen:
                        unique_tray_ids.append(tray.tray_id)
                        seen.add(tray.tray_id)
                
                print(f"   Unique tray IDs: {unique_tray_ids}")
                
                # Redistribute brass_physical_qty across trays
                qty_left = brass_physical_qty
                tray_idx = 0
                
                while qty_left > 0 and tray_idx < len(unique_tray_ids):
                    tray_id = unique_tray_ids[tray_idx]
                    qty = min(tray_capacity, qty_left) if tray_capacity > 0 else qty_left
                    
                    tray_list.append({
                        'sno': rejected_count + len(tray_list) + 1,
                        'tray_id': tray_id,
                        'tray_qty': qty,
                        'ip_top_tray': (tray_idx == 0),  # First tray is top tray
                        'brass_top_tray': (tray_idx == 0),
                        'top_tray': (tray_idx == 0),
                    })
                    
                    print(f"   Redistributed tray {tray_id}: qty={qty}")
                    qty_left -= qty
                    tray_idx += 1
                
                print(f"✅ [brass_view_tray_list] Redistributed {brass_physical_qty} qty across {len(tray_list)} trays")
                
                return Response({
                    'success': True,
                    'brass_qc_accptance': True,
                    'batch_rejection': False,
                    'total_rejection_qty': 0,
                    'tray_capacity': tray_capacity,
                    'trays': tray_list,
                })

            # Original logic: Get unique tray records, order by id to maintain consistency
            trays = BrassTrayId.objects.filter(lot_id=lot_id).order_by('tray_id', 'id')
            
            print(f"✅ [brass_view_tray_list] Found {trays.count()} BrassTrayId records for lot {lot_id}")
            
            # Track which tray_ids we've seen
            seen_tray_count = {}
            
            # Use same duplicate prevention logic
            for idx, tray_obj in enumerate(trays):
                tray_id = tray_obj.tray_id
                
                # Count occurrences for debugging
                if tray_id not in seen_tray_count:
                    seen_tray_count[tray_id] = 0
                seen_tray_count[tray_id] += 1
                
                if tray_id and tray_id not in added_tray_ids:
                    tray_list.append({
                        'sno': rejected_count + len(added_tray_ids) + 1,
                        'tray_id': tray_id,
                        'tray_qty': tray_obj.tray_quantity,
                    })
                    added_tray_ids.add(tray_id)
                    print(f"   ✅ Added tray {tray_id} (qty: {tray_obj.tray_quantity})")
                else:
                    print(f"   ⚠️ Skipped duplicate tray {tray_id} (occurrence #{seen_tray_count[tray_id]})")
            
            # ✅ FIXED: If no BrassTrayId records found, fallback to Brass_Qc_Accepted_TrayID_Store
            if not tray_list:
                print(f"⚠️ [brass_view_tray_list] No BrassTrayId records found, checking Brass_Qc_Accepted_TrayID_Store")
                accepted_trays = Brass_Qc_Accepted_TrayID_Store.objects.filter(lot_id=lot_id).order_by('id')
                print(f"✅ [brass_view_tray_list] Found {accepted_trays.count()} Brass_Qc_Accepted_TrayID_Store records for lot {lot_id}")
                for idx, tray_obj in enumerate(accepted_trays):
                    tray_id = tray_obj.tray_id
                    if tray_id and tray_id not in added_tray_ids:
                        tray_list.append({
                            'sno': rejected_count + len(added_tray_ids) + 1,
                            'tray_id': tray_id,
                            'tray_qty': tray_obj.tray_qty,
                        })
                        added_tray_ids.add(tray_id)
                        print(f"   ✅ Added tray from Brass_Qc_Accepted_TrayID_Store {tray_id} (qty: {tray_obj.tray_qty})")
            
            print(f"✅ [brass_view_tray_list] Returned {len(tray_list)} unique trays for brass_qc_accptance")
            print(f"   Duplicate summary: {seen_tray_count}")
            
            return Response({
                'success': True,
                'brass_qc_accptance': True,
                'batch_rejection': False,
                'total_rejection_qty': 0,
                'tray_capacity': tray_capacity,
                'trays': tray_list,
            })

        # Condition 2 & 3: Check rejection reason store (existing logic)
        reason_store = Brass_QC_Rejection_ReasonStore.objects.filter(lot_id=lot_id).order_by('-id').first()
        batch_rejection = False
        total_rejection_qty = 0
        
        if reason_store:
            batch_rejection = reason_store.batch_rejection
            total_rejection_qty = reason_store.total_rejection_quantity

        if batch_rejection and total_rejection_qty > 0:
            # Batch rejection: get actual rejected quantities and tray IDs from Brass_QC_Rejected_TrayScan AND modelmasterapp.TrayId
            rejected_scans = Brass_QC_Rejected_TrayScan.objects.filter(lot_id=lot_id).order_by('id')
            
            if rejected_scans.exists():
                for idx, scan in enumerate(rejected_scans):
                    tray_id = scan.rejected_tray_id
                    # ✅ If no tray_id in scan record, get from main TrayId table
                    if not tray_id:
                        main_tray = TrayId.objects.filter(lot_id=lot_id, brass_rejected_tray=True).first()
                        if main_tray:
                            tray_id = main_tray.tray_id
                    
                    tray_list.append({
                        'sno': rejected_count + idx + 1,
                        'tray_id': tray_id or '',
                        'tray_qty': int(scan.rejected_tray_quantity) if scan.rejected_tray_quantity else 0,
                    })
            else:
                # Fallback: get from main TrayId table or BrassTrayId table for lot rejections
                main_trays = TrayId.objects.filter(lot_id=lot_id, brass_rejected_tray=True)
                brass_trays = BrassTrayId.objects.filter(lot_id=lot_id, rejected_tray=True)
                
                if main_trays.exists():
                    for idx, tray in enumerate(main_trays):
                        tray_list.append({
                            'sno': rejected_count + idx + 1,
                            'tray_id': tray.tray_id,
                            'tray_qty': tray.tray_quantity or 0,
                        })
                elif brass_trays.exists():
                    for idx, tray in enumerate(brass_trays):
                        tray_list.append({
                            'sno': rejected_count + idx + 1,
                            'tray_id': tray.tray_id,
                            'tray_qty': tray.tray_quantity or 0,
                        })
                else:
                    # Final fallback: split total_rejection_qty by tray_capacity if no records found
                    tray_ids = list(BrassTrayId.objects.filter(lot_id=lot_id).values_list('tray_id', flat=True))
                    if tray_capacity > 0:
                        num_trays = ceil(total_rejection_qty / tray_capacity)
                        qty_left = total_rejection_qty
                        
                        for i in range(num_trays):
                            qty = tray_capacity if qty_left > tray_capacity else qty_left
                            tray_id = tray_ids[i] if i < len(tray_ids) else ""
                            tray_list.append({
                                'sno': rejected_count + i + 1,
                                'tray_id': tray_id,
                                'tray_qty': qty,
                            })
                            qty_left -= qty
        else:
            # Not batch rejection: get from Brass_Qc_Accepted_TrayID_Store
            trays = Brass_Qc_Accepted_TrayID_Store.objects.filter(lot_id=lot_id).order_by('id')
            for idx, obj in enumerate(trays):
                tray_list.append({
                    'sno': rejected_count + idx + 1,
                    'tray_id': obj.tray_id,
                    'tray_qty': obj.tray_qty,
                })

        return Response({
            'success': True,
            'brass_qc_accptance': brass_qc_accptance,
            'batch_rejection': batch_rejection,
            'total_rejection_qty': total_rejection_qty,
            'tray_capacity': tray_capacity,
            'trays': tray_list,
        })
        
    except Exception as e:
        return Response({'success': False, 'error': str(e)}, status=500)

@method_decorator(login_required, name='dispatch')
@method_decorator(csrf_exempt, name='dispatch')
class BrassTrayValidateAPIView(APIView):
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
                # Continue anyway since we're checking BrassTrayId which uses lot_id directly

            # Step 2: Check if the tray exists in BrassTrayId for this lot_id
            print(f"[DEBUG] Checking if tray '{tray_id}' exists in BrassTrayId for lot_id: '{lot_id_input}'")
            
            tray_exists = BrassTrayId.objects.filter(
                lot_id=lot_id_input,  # Use lot_id directly
                tray_id=tray_id
            ).exists()
            
            print(f"[DEBUG] Tray exists in BrassTrayId: {tray_exists}")
            
            # Additional debugging: show all trays for this lot_id in BrassTrayId
            all_trays = BrassTrayId.objects.filter(
                lot_id=lot_id_input
            ).values_list('tray_id', flat=True)
            print(f"[DEBUG] All trays in BrassTrayId for lot_id '{lot_id_input}': {list(all_trays)}")
            
            # Also check if tray exists anywhere in BrassTrayId (for debugging)
            tray_anywhere = BrassTrayId.objects.filter(tray_id=tray_id, lot_id=lot_id_input)
            if tray_anywhere.exists():
                tray_lot_ids = list(tray_anywhere.values_list('lot_id', flat=True))
                print(f"[DEBUG] Tray '{tray_id}' found in BrassTrayId for lot_ids: {tray_lot_ids}")
            
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
def brass_check_accepted_tray_draft(request):
    """Check if draft data exists for accepted tray scan"""
    lot_id = request.GET.get('lot_id')
    if not lot_id:
        return Response({'success': False, 'error': 'Missing lot_id'}, status=400)
    
    try:
        has_draft = Brass_Qc_Accepted_TrayID_Store.objects.filter(
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
def brass_save_accepted_tray_scan(request):
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
            if not tray_id or not BrassTrayId.objects.filter(tray_id=tray_id, lot_id=lot_id).exists():
                return Response({
                    'success': False,
                    'error': f'Tray ID "{tray_id}" is not existing (Row {idx+1}).'
                }, status=400)

        # Remove existing tray IDs for this lot (to avoid duplicates)
        Brass_Qc_Accepted_TrayID_Store.objects.filter(lot_id=lot_id).delete()

        # ✅ ENHANCED: Calculate ALL accepted trays for this lot
        # This includes both the scanned top tray and remaining accepted trays
        
        # Get TotalStockModel to understand the acceptance context
        stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
        if not stock:
            return Response({'success': False, 'error': 'Stock record not found'}, status=400)
        
        # Calculate total quantity from passed rows (usually just the top tray)
        scanned_trays = {}
        total_scanned_qty = 0
        for row in rows:
            tray_id = row.get('tray_id')
            tray_qty = row.get('tray_qty')
            if not tray_id or tray_qty is None:
                continue
            scanned_trays[tray_id] = int(tray_qty)
            total_scanned_qty += int(tray_qty)
        
        print(f"🔍 [brass_save_accepted_tray_scan] Scanned trays: {scanned_trays}, total: {total_scanned_qty}")
        print(f"🔍 Expected brass_qc_accepted_qty: {stock.brass_qc_accepted_qty}")
        
        # ✅ NEW: Add remaining accepted trays from TrayId table
        # Get all trays for this lot that are not rejected
        from modelmasterapp.models import TrayId
        
        all_trays = TrayId.objects.filter(
            lot_id=lot_id,
            tray_quantity__gt=0
        ).exclude(
            delink_tray=True
        )
        
        # Calculate which trays should be accepted based on quantities
        total_required = stock.brass_qc_accepted_qty
        remaining_qty_needed = total_required - total_scanned_qty
        
        print(f"🔍 Remaining qty needed: {remaining_qty_needed}")
        
        accepted_trays = {}
        accepted_trays.update(scanned_trays)  # Start with scanned trays
        
        if remaining_qty_needed > 0:
            # Add other trays that were not rejected to make up the accepted quantity
            for tray in all_trays:
                if tray.tray_id not in scanned_trays:
                    # Check if this tray was rejected in Brass QC
                    from Brass_QC.models import Brass_QC_Rejected_TrayScan
                    
                    rejected_scans = Brass_QC_Rejected_TrayScan.objects.filter(
                        lot_id=lot_id,
                        rejected_tray_id=tray.tray_id
                    )
                    
                    rejected_qty = sum(int(scan.rejected_tray_quantity or 0) for scan in rejected_scans)
                    available_qty = tray.tray_quantity - rejected_qty
                    
                    if available_qty > 0 and remaining_qty_needed > 0:
                        take_qty = min(available_qty, remaining_qty_needed)
                        accepted_trays[tray.tray_id] = take_qty
                        remaining_qty_needed -= take_qty
                        print(f"   ✅ Added accepted tray: {tray.tray_id} = {take_qty} (from original {tray.tray_quantity}, rejected {rejected_qty})")
        
        # Save all accepted trays to the store
        total_qty = 0
        for tray_id, tray_qty in accepted_trays.items():
            Brass_Qc_Accepted_TrayID_Store.objects.create(
                lot_id=lot_id,
                tray_id=tray_id,
                tray_qty=tray_qty,
                user=user,
                is_draft=draft_save,      # True if Draft button clicked
                is_save=not draft_save    # True if Submit button clicked
            )
            total_qty += tray_qty
            print(f"   💾 Saved accepted tray: {tray_id} = {tray_qty}")
        
        print(f"✅ Total accepted qty saved: {total_qty} (expected: {total_required})")

        # Save/Update Brass_Qc_Accepted_TrayScan for this lot
        accepted_scan, created = Brass_Qc_Accepted_TrayScan.objects.get_or_create(
            lot_id=lot_id,
            user=user,
            defaults={'accepted_tray_quantity': total_qty}
        )
        if not created:
            accepted_scan.accepted_tray_quantity = total_qty
            accepted_scan.save(update_fields=['accepted_tray_quantity'])

        # Update TotalStockModel flags only if it's a final save (not draft)
        if not draft_save:
            if total_qty != stock.brass_qc_accepted_qty:
                return Response({
                    'success': False, 
                    'error': f'Total accepted quantity ({total_qty}) must match the calculated accepted quantity ({stock.brass_qc_accepted_qty})'
                }, status=400)
            if stock:
                stock.accepted_tray_scan_status = True
                stock.next_process_module = "Brass Audit"  # ✅ CORRECTED: Send to Brass Audit instead of Jig Loading
                stock.last_process_module = "Brass QC"
                stock.brass_onhold_picking = False  # Reset onhold picking status
                stock.save(update_fields=['accepted_tray_scan_status', 'next_process_module', 'last_process_module', 'brass_onhold_picking'])
                
                # ✅ NEW: Transfer data to Brass Audit when tray scan is saved
                transfer_success = transfer_brass_qc_data_to_brass_audit(lot_id, user)
                if transfer_success:
                    print(f"✅ [brass_save_accepted_tray_scan] Data transferred to Brass Audit for lot: {lot_id}")
                else:
                    print(f"⚠️ [brass_save_accepted_tray_scan] Failed to transfer data to Brass Audit for lot: {lot_id}")

        return Response({'success': True, 'message': 'Accepted tray scan saved.'})

    except Exception as e:
        return Response({'success': False, 'error': str(e)}, status=500)



@require_GET
def brass_check_tray_id(request):
    tray_id = request.GET.get('tray_id', '')
    lot_id = request.GET.get('lot_id', '')  # This is your stock_lot_id

    # 1. Must exist in BrassTrayId table and lot_id must match
    tray_obj = BrassTrayId.objects.filter(tray_id=tray_id, lot_id=lot_id).first()
    exists = bool(tray_obj)
    same_lot = exists and str(tray_obj.lot_id) == str(lot_id)

    # 2. Must NOT be rejected in any module (Input Screening OR Brass QC)
    already_rejected = False
    if exists and same_lot and lot_id:
        # ✅ CHECK 1: Check if rejected in Input Screening (rejected_tray=True)
        input_screening_rejected = getattr(tray_obj, 'rejected_tray', False)
        
        # ✅ CHECK 2: Check if rejected in Brass QC (rejected_tray=True)
        brass_qc_rejected = getattr(tray_obj, 'rejected_tray', False)
        
        # ✅ CHECK 3: Check if rejected in Brass_QC_Rejected_TrayScan for this lot
        brass_qc_scan_rejected = Brass_QC_Rejected_TrayScan.objects.filter(
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
def brass_get_rejected_tray_scan_data(request):
    lot_id = request.GET.get('lot_id')
    if not lot_id:
        return Response({'success': False, 'error': 'Missing lot_id'}, status=400)
    try:
        rows = []
        for obj in Brass_QC_Rejected_TrayScan.objects.filter(lot_id=lot_id):
            rows.append({
                'tray_id': obj.rejected_tray_id,
                'qty': obj.rejected_tray_quantity,
                'reason': obj.rejection_reason.rejection_reason,
                'reason_id': obj.rejection_reason.rejection_reason_id,
                # 'top_tray': obj.top_tray,  # REMOVED: Field doesn't exist in model
            })
        return Response({'success': True, 'rows': rows})
    except Exception as e:
        return Response({'success': False, 'error': str(e)}, status=500)


""" Brass Qc - complete table view """
""" Brass Qc - complete table view """
@method_decorator(login_required, name='dispatch')
class BrassCompletedView(APIView):
    renderer_classes = [TemplateHTMLRenderer]
    template_name = 'Brass_Qc/Brass_Completed.html'

    def get(self, request):
        user = request.user
        
        # Handle sorting parameters
        sort = request.GET.get('sort')
        order = request.GET.get('order', 'asc')  # Default to ascending
        
        # Field mapping for proper model field references
        sort_field_mapping = {
            'serial_number': 'lot_id',  # Use lot_id for serial number sorting
            'date_time': 'bq_last_process_date_time',
            'plating_stk_no': 'batch_id__plating_stk_no',
            'polishing_stk_no': 'batch_id__polishing_stk_no',
            'plating_color': 'batch_id__plating_color',
            'category': 'batch_id__category',
            'polish_finish': 'batch_id__polish_finish',
            'tray_capacity': 'batch_id__tray_capacity',
            'vendor_location': 'batch_id__vendor_internal',
            'no_of_trays': 'batch_id__no_of_trays',
            'total_ip_accepted_qty': 'total_IP_accpeted_quantity',
            'accepted_qty': 'brass_qc_accepted_qty',
            'rejected_qty': 'brass_rejection_qty',
            'process_status': 'last_process_module',
            'lot_status': 'last_process_module',
            'current_stage': 'next_process_module',
            'remarks': 'Bq_pick_remarks',
            'rejection_remarks': 'brass_rejection_qty'
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
                from_date = datetime.datetime.strptime(from_date_str, '%Y-%m-%d').date()
                to_date = datetime.datetime.strptime(to_date_str, '%Y-%m-%d').date()
            except ValueError:
                from_date = yesterday
                to_date = today
        else:
            from_date = yesterday
            to_date = today

        from_datetime = timezone.make_aware(datetime.datetime.combine(from_date, datetime.datetime.min.time()))
        to_datetime = timezone.make_aware(datetime.datetime.combine(to_date, datetime.datetime.max.time()))

        # ✅ CHANGED: Query TotalStockModel directly instead of ModelMasterCreation
        brass_rejection_qty_subquery = Brass_QC_Rejection_ReasonStore.objects.filter(
            lot_id=OuterRef('lot_id')
        ).values('total_rejection_quantity')[:1]

        queryset = TotalStockModel.objects.select_related(
            'batch_id',
            'batch_id__model_stock_no',
            'batch_id__version',
            'batch_id__location'
        ).filter(
            batch_id__total_batch_quantity__gt=0,
            bq_last_process_date_time__range=(from_datetime, to_datetime)
        ).annotate(
            brass_rejection_qty=brass_rejection_qty_subquery,
        ).filter(
            Q(brass_qc_accptance=True) |
            Q(brass_qc_rejection=True) |
            Q(brass_qc_few_cases_accptance=True, brass_onhold_picking=False)
        )

        # Apply sorting if requested
        if sort and sort in sort_field_mapping:
            field = sort_field_mapping[sort]
            if order == 'desc':
                field = '-' + field
            queryset = queryset.order_by(field)
        else:
            queryset = queryset.order_by('-bq_last_process_date_time', '-lot_id')  # Default sorting

        print(f"📊 Found {queryset.count()} brass records in date range {from_date} to {to_date}")
        print("All lot_ids in completed queryset:", list(queryset.values_list('lot_id', flat=True)))

        # Pagination
        page_number = request.GET.get('page', 1)
        paginator = Paginator(queryset, 10)
        page_obj = paginator.get_page(page_number)

        master_data = []
        for stock_obj in page_obj.object_list:
            batch = stock_obj.batch_id
            
            data = {
                'batch_id': batch.batch_id,
                'lot_id': stock_obj.lot_id,
                'date_time': batch.date_time,
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
                'stock_lot_id': stock_obj.lot_id,
                'last_process_module': stock_obj.last_process_module,
                'next_process_module': stock_obj.next_process_module,
                'brass_qc_accepted_qty_verified': stock_obj.brass_qc_accepted_qty_verified,
                'brass_qc_accepted_qty': stock_obj.brass_qc_accepted_qty,
                'brass_rejection_qty': stock_obj.brass_rejection_qty,
                'brass_missing_qty': stock_obj.brass_missing_qty,
                'brass_physical_qty': stock_obj.brass_physical_qty,
                'brass_physical_qty_edited': stock_obj.brass_physical_qty_edited,
                'accepted_Ip_stock': stock_obj.accepted_Ip_stock,
                'rejected_ip_stock': stock_obj.rejected_ip_stock,
                'few_cases_accepted_Ip_stock': stock_obj.few_cases_accepted_Ip_stock,
                'accepted_tray_scan_status': stock_obj.accepted_tray_scan_status,
                'Bq_pick_remarks': stock_obj.Bq_pick_remarks,
                'brass_qc_accptance': stock_obj.brass_qc_accptance,
                'brass_accepted_tray_scan_status': stock_obj.brass_accepted_tray_scan_status,
                'brass_qc_rejection': stock_obj.brass_qc_rejection,
                'brass_qc_few_cases_accptance': stock_obj.brass_qc_few_cases_accptance,
                'brass_onhold_picking': stock_obj.brass_onhold_picking,
                'iqf_acceptance': stock_obj.iqf_acceptance,
                'send_brass_qc': stock_obj.send_brass_qc,
                'total_IP_accpeted_quantity': stock_obj.total_IP_accpeted_quantity,
                'bq_last_process_date_time': stock_obj.bq_last_process_date_time,
                'brass_hold_lot': stock_obj.brass_hold_lot,
                'brass_audit_accepted_qty_verified': stock_obj.brass_audit_accepted_qty_verified,
                'iqf_accepted_qty_verified': stock_obj.iqf_accepted_qty_verified,
                'plating_stk_no': batch.plating_stk_no,
                'polishing_stk_no': batch.polishing_stk_no,
                'category': batch.category,
                'no_of_trays': 0,
            }
            data['lot_remarks'] = stock_obj.Bq_pick_remarks or ""
            master_data.append(data)

        print(f"[BrassCompletedView] Total master_data records: {len(master_data)}")
        
        for data in master_data:
            total_IP_accpeted_quantity = data.get('total_IP_accpeted_quantity', 0)
            tray_capacity = data.get('tray_capacity', 0)
            data['vendor_location'] = f"{data.get('vendor_internal', '')}_{data.get('location__location_name', '')}"
            lot_id = data.get('stock_lot_id')
            
            if total_IP_accpeted_quantity and total_IP_accpeted_quantity > 0:
                data['display_accepted_qty'] = total_IP_accpeted_quantity
            else:
                total_rejection_qty = 0
                rejection_store = IP_Rejection_ReasonStore.objects.filter(lot_id=lot_id).first()
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
                
            batch_obj = ModelMasterCreation.objects.filter(batch_id=data['batch_id']).first()
            images = []
            if batch_obj and batch_obj.model_stock_no:
                for img in batch_obj.model_stock_no.images.all():
                    if img.master_image:
                        images.append(img.master_image.url)
            if not images:
                images = [static('assets/images/imagePlaceholder.png')]
            data['model_images'] = images

            if data.get('brass_physical_qty') and data.get('brass_physical_qty') > 0:
                data['available_qty'] = data['brass_physical_qty']
            else:
                data['available_qty'] = data.get('display_accepted_qty', 0)

            rejection_scans = Brass_QC_Rejected_TrayScan.objects.filter(
                lot_id=lot_id
            ).select_related('rejection_reason')
            
            rejection_remarks_list = []
            for scan in rejection_scans:
                rejection_remarks_list.append({
                    'reason': scan.rejection_reason.rejection_reason if scan.rejection_reason else 'Unknown',
                    'qty': scan.rejected_tray_quantity or 0,
                    'tray_id': scan.rejected_tray_id or '',
                })
            
            data['rejection_remarks_list'] = rejection_remarks_list
            
            lot_remarks = ""
            batch_rejection_store = Brass_QC_Rejection_ReasonStore.objects.filter(
                lot_id=lot_id,
                batch_rejection=True
            ).first()
            
            if batch_rejection_store and batch_rejection_store.lot_rejected_comment:
                lot_remarks = batch_rejection_store.lot_rejected_comment
            else:
                lot_remarks = ""
            
            data['lot_remarks'] = lot_remarks

            # ✅ FIXED: Add tray status for completed table (re-used/rejected)
            trays = BrassTrayId.objects.filter(lot_id=lot_id)
            tray_list = []
            for tray in trays:
                is_rejected = getattr(tray, 'rejected_tray', False)
                is_reused_tray = False
                
                # Check if tray appears in rejection records 
                rejection_record = Brass_QC_Rejected_TrayScan.objects.filter(
                    rejected_tray_id=tray.tray_id,
                    lot_id=lot_id
                ).first()
                
                # ✅ FIXED: Proper categorization logic
                # Reject Tray: Has rejection record OR marked as rejected_tray=True
                # Re-used Tray: Was used for rejection but still has quantity available (not fully rejected)
                if rejection_record:
                    # If tray appears in rejection records, it's a reject tray (not re-used)
                    is_rejected = True
                    is_reused_tray = False
                elif not is_rejected and tray.tray_quantity > 0:
                    # This is for trays that were potentially reused but don't have rejection records
                    # Only mark as reused if it's not rejected and has available quantity
                    is_reused_tray = True

                tray_list.append({
                    'tray_id': tray.tray_id,
                    'tray_quantity': tray.tray_quantity,
                    'is_reused_tray': is_reused_tray,
                    'is_rejected': is_rejected,
                })
            data['tray_list'] = tray_list

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

  
@method_decorator(login_required, name='dispatch')  
@method_decorator(csrf_exempt, name='dispatch')
class BrassTrayIdList_Complete_APIView(APIView):
    def get(self, request):
        batch_id = request.GET.get('batch_id')
        stock_lot_id = request.GET.get('stock_lot_id')
        lot_id = request.GET.get('lot_id') or stock_lot_id
        brass_qc_accptance = request.GET.get('brass_qc_accptance', 'false').lower() == 'true'
        brass_qc_rejection = request.GET.get('brass_qc_rejection', 'false').lower() == 'true'
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
        print(f"Rejected trays (Brass QC): {rejected_trays.count()}")
        print(f"Accepted trays: {accepted_trays.count()}")
        
        # Apply filtering based on stock status
        if brass_qc_accptance and not brass_qc_few_cases_accptance:
            # Show only accepted trays
            queryset = accepted_trays
            print("Filtering for accepted trays only")
        elif brass_qc_rejection and not brass_qc_few_cases_accptance:
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
        if brass_qc_accptance and not brass_qc_few_cases_accptance:
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
                # Get rejection details from Brass_QC_Rejected_TrayScan if needed
                rejected_scans = Brass_QC_Rejected_TrayScan.objects.filter(
                    lot_id=lot_id,
                    rejected_tray_id=tray_obj.tray_id
                ).select_related('rejection_reason')
                
                # Deduplicate rejection details similar to brass_get_rejection_remarks
                seen = set()
                for scan in rejected_scans:
                    reason = (scan.rejection_reason.rejection_reason if getattr(scan, 'rejection_reason', None) else '') or ''
                    qty = scan.rejected_tray_quantity or 0
                    # Use tuple key to deduplicate (reason, qty) - tray_id is already filtered
                    key = (reason.strip(), int(qty) if qty is not None else 0)
                    if key not in seen:
                        seen.add(key)
                        rejection_details.append({
                            'rejected_quantity': qty,
                            'rejection_reason': reason,
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
        shortage_count = Brass_QC_Rejected_TrayScan.objects.filter(
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


@method_decorator(login_required, name='dispatch')       
@method_decorator(csrf_exempt, name='dispatch')
class BrassTrayValidate_Complete_APIView(APIView):
    def post(self, request):
        try:
            data = request.data if hasattr(request, 'data') else json.loads(request.body.decode('utf-8'))
            batch_id_input = str(data.get('batch_id')).strip()
            tray_id = str(data.get('tray_id')).strip()
            
            # Get stock status parameters (optional, for enhanced validation)
            brass_qc_accptance = data.get('brass_qc_accptance', False)
            brass_qc_rejection = data.get('brass_qc_rejection', False)
            brass_qc_few_cases_accptance = data.get('brass_qc_few_cases_accptance', False)

            print(f"[BrassTrayValidate_Complete_APIView] User entered: batch_id={batch_id_input}, tray_id={tray_id}")
            print(f"Stock status: accepted={brass_qc_accptance}, rejected={brass_qc_rejection}, few_cases={brass_qc_few_cases_accptance}")

            # Base queryset for trays
            base_queryset = BrassTrayId.objects.filter(
                batch_id__batch_id__icontains=batch_id_input,
                tray_quantity__gt=0
            )
            
            # Apply the same filtering logic as the list API
            if brass_qc_accptance and not brass_qc_few_cases_accptance:
                # Only validate against accepted trays
                trays = base_queryset.filter(rejected_tray=False)
                print(f"Validating against accepted trays only")
            elif brass_qc_rejection and not brass_qc_few_cases_accptance:
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
           
@method_decorator(login_required, name='dispatch')       
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
class BrassBatchRejectionDraftAPIView(APIView):
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
            draft_obj, created = Brass_QC_Draft_Store.objects.update_or_create(
                lot_id=lot_id,
                draft_type='batch_rejection',
                defaults={
                    'batch_id': batch_id,
                    'user': request.user,
                    'draft_data': draft_data
                }
            )

            # ✅ FIX: Update brass_draft flag in TotalStockModel to change lot status to "draft"
            TotalStockModel.objects.filter(lot_id=lot_id).update(brass_draft=True)

            return Response({
                'success': True, 
                'message': 'Batch rejection draft saved successfully',
                'draft_id': draft_obj.id
            })

        except Exception as e:
            import traceback
            traceback.print_exc()
            return Response({'success': False, 'error': str(e)}, status=500)

# Brass QC - Rejection Draft API
@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(login_required, name='dispatch')
class BrassTrayRejectionDraftAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            data = request.data
            lot_id = data.get('lot_id') or data.get('stock_lot_id')
            batch_id = data.get('batch_id', '')
            user = request.user

            if not lot_id:
                return Response({'success': False, 'error': 'Missing lot_id'}, status=400)

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
                
                # ✅ ENHANCED: Include associated_trays for better draft restoration
                associated_trays = it.get('associated_trays', [])
                
                cleaned.append({
                    'reason_id': str(it.get('reason_id') or it.get('reason') or '').strip(),
                    'qty': qty,
                    'tray_id': str(it.get('tray_id') or it.get('rejected_tray_id') or '').strip(),
                    'associated_trays': associated_trays
                })

            # Create or update the single draft row (unique per lot_id + draft_type)
            draft_obj, created = Brass_QC_Draft_Store.objects.get_or_create(
                lot_id=lot_id,
                draft_type='tray_rejection',
                defaults={
                    'batch_id': batch_id or '',
                    'user': user,
                    'draft_data': {
                        'is_draft': True,
                        'batch_rejection': False,
                        'tray_rejections': cleaned,
                        'tray_id_mappings': tray_id_mappings  # ✅ Store tray ID mappings
                    }
                }
            )

            if not created:
                # ✅ FIXED: Replace entire draft data instead of appending
                # This ensures all current rejection reasons are stored correctly
                existing = draft_obj.draft_data or {}
                existing['tray_rejections'] = cleaned  # Replace, don't append
                existing['tray_id_mappings'] = tray_id_mappings  # ✅ Update tray mappings
                existing['is_draft'] = True
                existing['batch_rejection'] = False

                # update metadata
                draft_obj.batch_id = batch_id or draft_obj.batch_id
                draft_obj.user = user
                draft_obj.draft_data = existing
                draft_obj.save()

            # ✅ FIX: Update brass_draft flag in TotalStockModel to change lot status to "draft"
            TotalStockModel.objects.filter(lot_id=lot_id).update(brass_draft=True)

            return Response({'success': True, 'draft': draft_obj.draft_data})
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
        draft_obj = Brass_QC_Draft_Store.objects.filter(
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
            deleted_count, _ = Brass_QC_Draft_Store.objects.filter(
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
        batch_draft = Brass_QC_Draft_Store.objects.filter(
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
        tray_draft = Brass_QC_Draft_Store.objects.filter(
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
@method_decorator(login_required, name='dispatch')
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
            
            print(f"[TrayValidate_Complete_APIView] User entered: batch_id={batch_id_input}, tray_id={tray_id}")
            print(f"Stock status: accepted={accepted_ip_stock}, rejected={rejected_ip_stock}, few_cases={few_cases_accepted_ip_stock}")

            # Base queryset for trays
            base_queryset = IPTrayId.objects.filter(
                batch_id__batch_id__icontains=batch_id_input,
                tray_quantity__gt=0
            )
            
            # Apply the same filtering logic as the list API
            if accepted_ip_stock and not few_cases_accepted_ip_stock:
                # Only validate against accepted trays
                trays = base_queryset.filter(rejected_tray=False)
                print(f"Validating against accepted trays only")
            elif rejected_ip_stock and not few_cases_accepted_ip_stock:
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
                        'top_tray': tray.top_tray,  # ✅ UPDATED: Use top_tray instead of ip_top_tray
                        'tray_quantity': tray.tray_quantity  # ✅ UPDATED: Include tray_quantity
                    }

            return JsonResponse({
                'success': True, 
                'exists': exists,
                'tray_info': tray_info
            })
            
        except Exception as e:
            print(f"[TrayValidate_Complete_APIView] Error: {str(e)}")
            return JsonResponse({'success': False, 'error': str(e)}, status=500)    
           
@method_decorator(login_required, name='dispatch')          
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

        # Check flags for different tray models
        send_brass_qc = False
        send_brass_audit_to_qc = False
        
        total_stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
        if total_stock:
            send_brass_qc = getattr(total_stock, 'send_brass_qc', False)
            send_brass_audit_to_qc = getattr(total_stock, 'send_brass_audit_to_qc', False)

        # Determine which tray model to use based on flags
        if send_brass_audit_to_qc:
            # Use BrassAuditTrayId for audit trays
            print(f"🔍 [DEBUG] Checking BrassAuditTrayId records for:")
            print(f"   batch_id: {batch_id}")
            print(f"   lot_id: {lot_id}")
            
            # First check if ANY records exist for this batch/lot
            all_records = BrassAuditTrayId.objects.filter(
                batch_id__batch_id=batch_id,
                lot_id=lot_id
            )
            print(f"   Total BrassAuditTrayId records found (no filters): {all_records.count()}")
            
            if all_records.exists():
                # Show details of existing records
                for record in all_records[:5]:  # Show first 5 records
                    print(f"     Record: tray_id={record.tray_id}, qty={getattr(record, 'tray_quantity', 'N/A')}, "
                          f"rejected={getattr(record, 'rejected_tray', 'N/A')}, "
                          f"delinked={getattr(record, 'delink_tray', 'N/A')}")
            
            # Apply full filtering
            base_queryset = BrassAuditTrayId.objects.filter(
                batch_id__batch_id=batch_id,
                tray_quantity__gt=0,
                lot_id=lot_id,
                rejected_tray=True,
            )
            print(f"   After applying filters (qty>0, rejected): {base_queryset.count()}")
            tray_model_used = 'BrassAuditTrayId'
            
            # Fallback: If no trays found, use BrassTrayId
            if base_queryset.count() == 0:
                base_queryset = BrassTrayId.objects.filter(
                    batch_id__batch_id=batch_id,
                    tray_quantity__gt=0,
                    lot_id=lot_id
                )
                tray_model_used = 'BrassTrayId'
                print(f"   Fallback: Using BrassTrayId, found {base_queryset.count()} trays")
        
        elif send_brass_qc:
            # Use IQFTrayId for accepted trays
            base_queryset = IQFTrayId.objects.filter(
                batch_id__batch_id=batch_id,
                tray_quantity__gt=0,
                lot_id=lot_id,
                rejected_tray=False,
                delink_tray=False
            )
            tray_model_used = 'IQFTrayId'

            # Fallback: If no trays found in IQFTrayId, use BrassTrayId
            if base_queryset.count() == 0:
                base_queryset = BrassTrayId.objects.filter(
                    batch_id__batch_id=batch_id,
                    tray_quantity__gt=0,
                    lot_id=lot_id,
                    rejected_tray=False,
                    delink_tray=False
                )
                tray_model_used = 'BrassTrayId'
        else:
            # If brass_qc_accepted_qty_verified is True, show BrassTrayId, else show IPTrayId
            total_stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
            if total_stock and getattr(total_stock, 'brass_qc_accepted_qty_verified', False):
                base_queryset = BrassTrayId.objects.filter(
                    batch_id__batch_id=batch_id,
                    tray_quantity__gt=0,
                    lot_id=lot_id,
                    rejected_tray=False,
                    delink_tray=False
                )
                tray_model_used = 'BrassTrayId'
            else:
                base_queryset = IPTrayId.objects.filter(
                    batch_id__batch_id=batch_id,
                    tray_quantity__gt=0,
                    lot_id=lot_id,
                    rejected_tray=False,
                    delink_tray=False
                )
                tray_model_used = 'IPTrayId'
            tray_model_used = 'IPTrayId'

        print(f"✅ [PickTrayIdList_Complete_APIView] Using {tray_model_used} model")
        print(f"Flags: send_brass_qc={send_brass_qc}, send_brass_audit_to_qc={send_brass_audit_to_qc}")
        print(f"Total accepted trays found: {base_queryset.count()}")

        # Find top tray from accepted trays only
        top_tray = base_queryset.filter(top_tray=True).first()
        other_trays = base_queryset.exclude(pk=top_tray.pk if top_tray else None).order_by('id')

        data = []
        row_counter = 1

        def create_tray_data(tray_obj, is_top=False):
            nonlocal row_counter
            return {
                's_no': row_counter,
                'tray_id': tray_obj.tray_id,
                'tray_quantity': tray_obj.tray_quantity,
                'position': row_counter - 1,
                'is_top_tray': is_top,
                'rejected_tray': False,
                'delink_tray': False,
                'rejection_details': [],
                'top_tray': getattr(tray_obj, 'top_tray', False),
                'tray_quantity': getattr(tray_obj, 'tray_quantity', None),
                'model_used': tray_model_used  # Add info about which model was used
            }

        if top_tray:
            tray_data = create_tray_data(top_tray, is_top=True)
            data.append(tray_data)
            row_counter += 1

        for tray in other_trays:
            tray_data = create_tray_data(tray, is_top=False)
            data.append(tray_data)
            row_counter += 1

        print(f"✅ [PickTrayIdList_Complete_APIView] Total accepted trays returned: {len(data)}")

        summary = {
            'total_accepted_trays': base_queryset.count(),
            'accepted_tray_ids': list(base_queryset.values_list('tray_id', flat=True)),
            'total_rejected_trays': 0,
            'rejected_tray_ids': [],
            'shortage_rejections': 0,
            'filter_applied': 'accepted_only',
            'tray_model_used': tray_model_used,
            'flags': {
                'send_brass_qc': send_brass_qc,
                'send_brass_audit_to_qc': send_brass_audit_to_qc
            }
        }

        return JsonResponse({
            'success': True, 
            'trays': data,
            'rejection_summary': summary
        })   
    
        
#After SaveIPCHeckbox tray validation and list
# ✅ CORRECTED: AfterCheckTrayValidate_Complete_APIView - Use BrassTrayId and remove False filtering
@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(login_required, name='dispatch')
class AfterCheckTrayValidate_Complete_APIView(APIView):
    def post(self, request):
        try:
            data = request.data if hasattr(request, 'data') else json.loads(request.body.decode('utf-8'))
            batch_id_input = str(data.get('batch_id')).strip()
            tray_id = str(data.get('tray_id')).strip()
            
            # ✅ Get Brass QC status parameters
            brassQcAccptance = data.get('brass_qc_accptance', False)
            brassQcRejection = data.get('brass_qc_rejection', False)
            brassQcFewCases = data.get('brass_qc_few_cases_accptance', False)

            print(f"🔧 [AfterCheckTrayValidate_Complete_APIView] Received:")
            print(f"   batch_id: {batch_id_input}")
            print(f"   tray_id: {tray_id}")
            print(f"   brass_qc_accptance: {brassQcAccptance}")
            print(f"   brass_qc_rejection: {brassQcRejection}")
            print(f"   brass_qc_few_cases_accptance: {brassQcFewCases}")

            # ✅ CORRECTED: Use BrassTrayId model (created after brass checkbox verification)
            base_queryset = BrassTrayId.objects.filter(
                batch_id__batch_id__icontains=batch_id_input,
                tray_quantity__gt=0
            )
            
            print(f"✅ [AfterCheckTrayValidate] Using BrassTrayId model")
            print(f"✅ [AfterCheckTrayValidate] Base queryset count: {base_queryset.count()}")
            
            # ✅ CORRECTED: Only apply filtering if at least one Brass QC parameter is True
            has_brass_qc_status = brassQcAccptance or brassQcRejection or brassQcFewCases
            
            if has_brass_qc_status:
                # Apply filtering only when there's actual Brass QC status
                if brassQcAccptance and not brassQcFewCases:
                    # Only validate against Brass QC accepted trays
                    trays = base_queryset.filter(rejected_tray=False)
                    print(f"✅ [AfterCheckTrayValidate] Validating against Brass QC ACCEPTED trays only")
                elif brassQcRejection and not brassQcFewCases:
                    # Only validate against Brass QC rejected trays
                    trays = base_queryset.filter(rejected_tray=True)
                    print(f"✅ [AfterCheckTrayValidate] Validating against Brass QC REJECTED trays only")
                else:
                    # Validate against all trays (few_cases or default)
                    trays = base_queryset
                    print(f"✅ [AfterCheckTrayValidate] Validating against ALL BrassTrayId records")
            else:
                # ✅ NEW: When all parameters are False, validate against all BrassTrayId records
                trays = base_queryset
                print(f"✅ [AfterCheckTrayValidate] All Brass QC parameters are False - validating against ALL BrassTrayId records")
            
            print(f"✅ [AfterCheckTrayValidate] Available tray_ids: {[t.tray_id for t in trays[:10]]}...")  # Show first 10

            exists = trays.filter(tray_id=tray_id).exists()
            print(f"🔍 [AfterCheckTrayValidate] Tray ID '{tray_id}' exists in BrassTrayId results? {exists}")

            # Get additional info about the tray if it exists
            tray_info = {}
            if exists:
                tray = trays.filter(tray_id=tray_id).first()
                if tray:
                    tray_info = {
                        'rejected_tray': getattr(tray, 'rejected_tray', False),
                        'tray_quantity': tray.tray_quantity,
                        'top_tray': getattr(tray, 'top_tray', False),
                        'top_tray': getattr(tray, 'top_tray', False),
                        'rejected_tray': getattr(tray, 'rejected_tray', False),  # This might not exist in BrassTrayId
                        'ip_top_tray': getattr(tray, 'ip_top_tray', False),  # Add IP top tray info
                        'data_source': 'BrassTrayId'  # ✅ NEW: Indicate data source
                    }

            return JsonResponse({
                'success': True, 
                'exists': exists,
                'tray_info': tray_info,
                'data_source': 'BrassTrayId',  # ✅ NEW: Indicate data source
                'filtering_applied': has_brass_qc_status  # ✅ NEW: Indicate if filtering was applied
            })
            
        except Exception as e:
            print(f"❌ [AfterCheckTrayValidate_Complete_APIView] Error: {str(e)}")
            import traceback
            traceback.print_exc()
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
           
class AfterCheckPickTrayIdList_Complete_APIView(APIView):
    def get(self, request):
        batch_id = request.GET.get('batch_id')
        stock_lot_id = request.GET.get('stock_lot_id')
        lot_id = request.GET.get('lot_id') or stock_lot_id

        if not batch_id:
            return JsonResponse({'success': False, 'error': 'Missing batch_id'}, status=400)
        if not lot_id:
            return JsonResponse({'success': False, 'error': 'Missing lot_id or stock_lot_id'}, status=400)

        print(f"🔍 [AfterCheckPickTrayIdList_Complete_APIView] Parameters:")
        print(f"   batch_id: {batch_id}")
        print(f"   lot_id: {lot_id}")

        # Check flags for different tray models
        send_brass_qc = False
        send_brass_audit_to_qc = False
        
        total_stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
        if total_stock:
            send_brass_qc = getattr(total_stock, 'send_brass_qc', False)
            send_brass_audit_to_qc = getattr(total_stock, 'send_brass_audit_to_qc', False)

        # Determine which tray model to use based on flags
        if send_brass_audit_to_qc:
            # Use BrassAuditTrayId for audit trays
            print(f"🔍 [DEBUG] Checking BrassAuditTrayId records for:")
            print(f"   batch_id: {batch_id}")
            print(f"   lot_id: {lot_id}")
            
            # First check if ANY records exist for this batch/lot
            all_records = BrassAuditTrayId.objects.filter(
                batch_id__batch_id=batch_id,
                lot_id=lot_id
            )
            print(f"   Total BrassAuditTrayId records found (no filters): {all_records.count()}")
            
            if all_records.exists():
                # Show details of existing records
                for record in all_records[:5]:  # Show first 5 records
                    print(f"     Record: tray_id={record.tray_id}, qty={getattr(record, 'tray_quantity', 'N/A')}, "
                          f"rejected={getattr(record, 'rejected_tray', 'N/A')}, "
                          f"delinked={getattr(record, 'delink_tray', 'N/A')}")
            
            # Apply full filtering
            queryset = BrassAuditTrayId.objects.filter(
                batch_id__batch_id=batch_id,
                tray_quantity__gt=0,
                lot_id=lot_id,
                rejected_tray=True,
            )
            print(f"   After applying filters (qty>0, rejected): {queryset.count()}")
            tray_model_used = 'BrassAuditTrayId'
            
            # Fallback: If no trays found, use BrassTrayId
            if queryset.count() == 0:
                queryset = BrassTrayId.objects.filter(
                    batch_id__batch_id=batch_id,
                    tray_quantity__gt=0,
                    lot_id=lot_id
                )
                tray_model_used = 'BrassTrayId'
                print(f"   Fallback: Using BrassTrayId, found {queryset.count()} trays")
        
        elif send_brass_qc:
            # Use IQFTrayId for accepted trays
            queryset = IQFTrayId.objects.filter(
                batch_id__batch_id=batch_id,
                tray_quantity__gt=0,
                lot_id=lot_id,
                rejected_tray=False,
                delink_tray=False
            )
            tray_model_used = 'IQFTrayId'

            # Fallback: If no trays found in IQFTrayId, use BrassTrayId
            if queryset.count() == 0:
                queryset = BrassTrayId.objects.filter(
                    batch_id__batch_id=batch_id,
                    tray_quantity__gt=0,
                    lot_id=lot_id,
                    rejected_tray=False,
                    delink_tray=False
                )
                tray_model_used = 'BrassTrayId'
        else:
            # If brass_qc_accepted_qty_verified is True, show BrassTrayId, else show IPTrayId
            total_stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
            if total_stock and getattr(total_stock, 'brass_qc_accepted_qty_verified', False):
                queryset = BrassTrayId.objects.filter(
                    batch_id__batch_id=batch_id,
                    tray_quantity__gt=0,
                    lot_id=lot_id,
                    rejected_tray=False,
                    delink_tray=False
                )
                tray_model_used = 'BrassTrayId'
            else:
                queryset = IPTrayId.objects.filter(
                    batch_id__batch_id=batch_id,
                    tray_quantity__gt=0,
                    lot_id=lot_id,
                    rejected_tray=False,
                    delink_tray=False
                )
                tray_model_used = 'IPTrayId'
            tray_model_used = 'IPTrayId'

        print(f"✅ [AfterCheckPickTrayIdList_Complete_APIView] Using {tray_model_used} model")
        print(f"Flags: send_brass_qc={send_brass_qc}, send_brass_audit_to_qc={send_brass_audit_to_qc}")
        print(f"Total trays found: {queryset.count()}")

        # Get stock information to check for delinked quantities and lot rejection status
        stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
        if not stock:
            return JsonResponse({'success': False, 'error': 'Stock not found'}, status=404)

        # ✅ CRITICAL: Check if this is a lot rejection (batch_rejection=True)
        reason_store = Brass_QC_Rejection_ReasonStore.objects.filter(lot_id=lot_id).first()
        is_lot_rejection = reason_store and reason_store.batch_rejection
        total_rejection_qty = reason_store.total_rejection_quantity if reason_store else 0
        
        print(f"🔍 [AfterCheckPickTrayIdList_Complete_APIView] Lot rejection check:")
        print(f"   is_lot_rejection: {is_lot_rejection}")
        print(f"   total_rejection_qty: {total_rejection_qty}")

        data = []
        row_counter = 1

        def create_tray_data(tray_obj, category, is_top=False, rejection_qty=None):
            nonlocal row_counter
            rejection_details = []
            
            # Calculate display quantity based on category and context
            if category == 'rejected':
                if is_lot_rejection:
                    # For lot rejection, show full tray quantity as rejected
                    display_quantity = tray_obj.tray_quantity or 0
                    print(f"🔍 [create_tray_data] LOT REJECTION - {tray_obj.tray_id}: REJECTED with full quantity={display_quantity}")
                else:
                    # For individual tray rejections, show specific rejection quantity
                    rejected_scans = Brass_QC_Rejected_TrayScan.objects.filter(
                        lot_id=lot_id,
                        rejected_tray_id=tray_obj.tray_id
                    )
                    display_quantity = rejection_qty or 0
                    for scan in rejected_scans:
                        if not rejection_qty:
                            display_quantity += int(scan.rejected_tray_quantity or 0)
                        rejection_details.append({
                            'rejected_quantity': scan.rejected_tray_quantity,
                            'rejection_reason': scan.rejection_reason.rejection_reason if scan.rejection_reason else 'Unknown',
                            'rejection_reason_id': scan.rejection_reason.rejection_reason_id if scan.rejection_reason else None,
                            'user': scan.user.username if scan.user else None
                        })
                    print(f"🔍 [create_tray_data] TRAY REJECTION - {tray_obj.tray_id}: REJECTED with quantity={display_quantity}")
            else:
                display_quantity = tray_obj.tray_quantity or 0
                print(f"🔍 [create_tray_data] {tray_obj.tray_id}: {category.upper()} with quantity={display_quantity}")
                
            return {
                's_no': row_counter,
                'tray_id': tray_obj.tray_id,
                'tray_quantity': display_quantity,
                'position': row_counter - 1,
                'is_top_tray': is_top,
                'rejected_tray': category == 'rejected',
                'is_reused_tray': False,
                'delink_tray': category == 'delinked',
                'rejection_details': rejection_details,
                'top_tray': getattr(tray_obj, 'top_tray', False),
                'ip_top_tray': getattr(tray_obj, 'ip_top_tray', False),
                'ip_top_tray_qty': display_quantity if is_top else getattr(tray_obj, 'ip_top_tray_qty', None)
            }

        # ✅ FIXED LOGIC: Handle lot rejection vs individual tray categorization
        if is_lot_rejection:
            # LOT REJECTION: All trays should be shown as REJECTED
            print(f"🚨 [AfterCheckPickTrayIdList_Complete_APIView] LOT REJECTION detected - All trays will be REJECTED")
            
            for tray in queryset:
                is_top = getattr(tray, 'top_tray', False) or getattr(tray, 'ip_top_tray', False)
                tray_data = create_tray_data(tray, 'rejected', is_top)
                data.append(tray_data)
                row_counter += 1
                
            print(f"✅ [AfterCheckPickTrayIdList_Complete_APIView] LOT REJECTION: {len(data)} trays marked as REJECTED")
            
        else:
            # NORMAL PROCESSING: Categorize based on acceptance/rejection/delink logic
            print(f"📋 [AfterCheckPickTrayIdList_Complete_APIView] NORMAL PROCESSING - Categorizing trays")
            
            brass_physical_qty = stock.brass_physical_qty or 0
            
            # Calculate accepted quantity target (physical - rejected)
            accepted_qty_target = brass_physical_qty - total_rejection_qty
            
            print(f"🔍 [AfterCheckPickTrayIdList_Complete_APIView] Quantity analysis:")
            print(f"   brass_physical_qty: {brass_physical_qty}")
            print(f"   total_rejection_qty: {total_rejection_qty}")
            print(f"   accepted_qty_target: {accepted_qty_target}")

            # Separate trays into categories first
            rejected_tray_ids = set()
            rejected_scans = Brass_QC_Rejected_TrayScan.objects.filter(lot_id=lot_id)
            for scan in rejected_scans:
                rejected_tray_ids.add(scan.rejected_tray_id)

            # Categorize trays
            accepted_trays = []
            rejected_trays = []
            
            for tray in queryset:
                if tray.tray_id in rejected_tray_ids:
                    rejected_trays.append(tray)
                else:
                    accepted_trays.append(tray)

            # ✅ Determine which accepted trays should be delinked
            accepted_trays_sorted = sorted(accepted_trays, key=lambda x: x.tray_quantity or 0, reverse=True)
            
            running_accepted_qty = 0
            final_accepted_trays = []  # list of TrayId objects
            partial_qty_map = {}  # tray_id -> qty for partial accepted top tray
            delinked_trays = []
            
            for tray in accepted_trays_sorted:
                tray_qty = int(tray.tray_quantity or 0)
                if running_accepted_qty + tray_qty <= accepted_qty_target:
                    # This tray can be accepted in full
                    final_accepted_trays.append(tray)
                    running_accepted_qty += tray_qty
                    print(f"🔍 [Categorization] {tray.tray_id}: ACCEPTED (running total: {running_accepted_qty})")
                else:
                    # This tray would exceed the target - accept a partial if possible
                    remaining = accepted_qty_target - running_accepted_qty
                    if remaining > 0:
                        # Prefer a pre-flagged top tray among candidates
                        preferred = next((t for t in accepted_trays_sorted if getattr(t, 'top_tray', False) or getattr(t, 'ip_top_tray', False)), None)
                        partial_tray = preferred or tray
                        if partial_tray.tray_id not in [t.tray_id for t in final_accepted_trays]:
                            final_accepted_trays.append(partial_tray)
                        partial_qty_map[partial_tray.tray_id] = int(remaining)
                        running_accepted_qty += remaining
                        print(f"🔍 [Categorization] {partial_tray.tray_id}: PARTIAL ACCEPTED = {remaining} (running total: {running_accepted_qty})")
                    # Remaining candidates become delinked
                    break

            print(f"🔍 [Final Categorization] Accepted: {len(final_accepted_trays)}, Rejected: {len(rejected_trays)}, Delinked: {len(delinked_trays)}")

            # ✅ Add trays in proper order: Accepted -> Rejected -> Delinked
            
            # 1. Add accepted trays (ensure partial top tray appears first)
            top_id = next(iter(partial_qty_map.keys())) if partial_qty_map else None
            if top_id:
                top_obj = next((t for t in final_accepted_trays if t.tray_id == top_id), None)
                if top_obj:
                    data.append(create_tray_data(top_obj, 'accepted', True, rejection_qty=partial_qty_map.get(top_obj.tray_id)))
                    row_counter += 1
            # Add the rest (skip the already added top)
            for tray in final_accepted_trays:
                if top_id and tray.tray_id == top_id:
                    continue
                is_top = getattr(tray, 'top_tray', False) or getattr(tray, 'ip_top_tray', False)
                qty_override = partial_qty_map.get(tray.tray_id)
                tray_data = create_tray_data(tray, 'accepted', is_top, rejection_qty=qty_override)
                data.append(tray_data)
                row_counter += 1

            # 2. Add rejected trays
            for tray in rejected_trays:
                tray_data = create_tray_data(tray, 'rejected', False)
                data.append(tray_data)
                row_counter += 1

            # 3. Add delinked trays
            for tray in delinked_trays:
                tray_data = create_tray_data(tray, 'delinked', False)
                data.append(tray_data)
                row_counter += 1

        print(f"✅ [AfterCheckPickTrayIdList_Complete_APIView] Total trays returned: {len(data)}")

        return JsonResponse({
            'success': True,
            'trays': data,
            'rejection_summary': {
                'total_trays': queryset.count(),
                'is_lot_rejection': is_lot_rejection,
                'total_rejection_qty': total_rejection_qty,
                'accepted_trays': 0 if is_lot_rejection else len([t for t in data if not t['rejected_tray'] and not t['delink_tray']]),
                'rejected_trays': len([t for t in data if t['rejected_tray']]),
                'delinked_trays': 0 if is_lot_rejection else len([t for t in data if t['delink_tray']])
            }
        })
          
#After SaveIPCHeckbox tray validation and list
# ✅ CORRECTED: AfterCheckTrayValidate_Complete_APIView - Use BrassTrayId and remove False filtering
@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(login_required, name='dispatch')
class AfterCheckTrayValidate_Complete_APIView(APIView):
    def post(self, request):
        try:
            data = request.data if hasattr(request, 'data') else json.loads(request.body.decode('utf-8'))
            batch_id_input = str(data.get('batch_id')).strip()
            tray_id = str(data.get('tray_id')).strip()
            
            # ✅ Get Brass QC status parameters
            brassQcAccptance = data.get('brass_qc_accptance', False)
            brassQcRejection = data.get('brass_qc_rejection', False)
            brassQcFewCases = data.get('brass_qc_few_cases_accptance', False)

            print(f"🔧 [AfterCheckTrayValidate_Complete_APIView] Received:")
            print(f"   batch_id: {batch_id_input}")
            print(f"   tray_id: {tray_id}")
            print(f"   brass_qc_accptance: {brassQcAccptance}")
            print(f"   brass_qc_rejection: {brassQcRejection}")
            print(f"   brass_qc_few_cases_accptance: {brassQcFewCases}")

            # ✅ CORRECTED: Use BrassTrayId model (created after brass checkbox verification)
            base_queryset = BrassTrayId.objects.filter(
                batch_id__batch_id__icontains=batch_id_input,
                tray_quantity__gt=0
            )
            
            print(f"✅ [AfterCheckTrayValidate] Using BrassTrayId model")
            print(f"✅ [AfterCheckTrayValidate] Base queryset count: {base_queryset.count()}")
            
            # ✅ CORRECTED: Only apply filtering if at least one Brass QC parameter is True
            has_brass_qc_status = brassQcAccptance or brassQcRejection or brassQcFewCases
            
            if has_brass_qc_status:
                # Apply filtering only when there's actual Brass QC status
                if brassQcAccptance and not brassQcFewCases:
                    # Only validate against Brass QC accepted trays
                    trays = base_queryset.filter(rejected_tray=False)
                    print(f"✅ [AfterCheckTrayValidate] Validating against Brass QC ACCEPTED trays only")
                elif brassQcRejection and not brassQcFewCases:
                    # Only validate against Brass QC rejected trays
                    trays = base_queryset.filter(rejected_tray=True)
                    print(f"✅ [AfterCheckTrayValidate] Validating against Brass QC REJECTED trays only")
                else:
                    # Validate against all trays (few_cases or default)
                    trays = base_queryset
                    print(f"✅ [AfterCheckTrayValidate] Validating against ALL BrassTrayId records")
            else:
                # ✅ NEW: When all parameters are False, validate against all BrassTrayId records
                trays = base_queryset
                print(f"✅ [AfterCheckTrayValidate] All Brass QC parameters are False - validating against ALL BrassTrayId records")
            
            print(f"✅ [AfterCheckTrayValidate] Available tray_ids: {[t.tray_id for t in trays[:10]]}...")  # Show first 10

            exists = trays.filter(tray_id=tray_id).exists()
            print(f"🔍 [AfterCheckTrayValidate] Tray ID '{tray_id}' exists in BrassTrayId results? {exists}")

            # Get additional info about the tray if it exists
            tray_info = {}
            if exists:
                tray = trays.filter(tray_id=tray_id).first()
                if tray:
                    tray_info = {
                        'rejected_tray': getattr(tray, 'rejected_tray', False),
                        'tray_quantity': tray.tray_quantity,
                        'top_tray': getattr(tray, 'top_tray', False),
                        'top_tray': getattr(tray, 'top_tray', False),
                        'rejected_tray': getattr(tray, 'rejected_tray', False),  # This might not exist in BrassTrayId
                        'ip_top_tray': getattr(tray, 'ip_top_tray', False),  # Add IP top tray info
                        'data_source': 'BrassTrayId'  # ✅ NEW: Indicate data source
                    }

            return JsonResponse({
                'success': True, 
                'exists': exists,
                'tray_info': tray_info,
                'data_source': 'BrassTrayId',  # ✅ NEW: Indicate data source
                'filtering_applied': has_brass_qc_status  # ✅ NEW: Indicate if filtering was applied
            })
            
        except Exception as e:
            print(f"❌ [AfterCheckTrayValidate_Complete_APIView] Error: {str(e)}")
            import traceback
            traceback.print_exc()
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
           
        

  # ==========================================
# BARCODE SCANNER API - Brass QC
# ==========================================

@api_view(['GET'])
@permission_classes([IsAuthenticated]) 
def brass_qc_get_lot_id_for_tray(request):
    """
    API endpoint to get lot_id for a given tray_id for Brass QC barcode scanner.
    Similar to Input Screening's brass_qc_get_lot_id_for_tray function.
    
    GET /brass_qc/brass_qc_get_lot_id_for_tray/?tray_id=<tray_id>
    
    Response:
    {
        "success": true,
        "lot_id": "LOT-20240101-001"
    }
    """
    tray_id = request.GET.get('tray_id', '').strip()
    if not tray_id:
        return JsonResponse({'success': False, 'error': 'Missing tray_id'}, status=400)
    
    try:
        # Look for tray in multiple tables similar to Day Planning logic
        lot_id = None
        
        # First check BrassTrayId table
        brass_tray = BrassTrayId.objects.filter(tray_id=tray_id).first()
        if brass_tray and brass_tray.lot_id:
            lot_id = str(brass_tray.lot_id)
            
        # If not found, check TotalStockModel
        if not lot_id:
            total_stock = TotalStockModel.objects.filter(
                lot_id__icontains=tray_id
            ).first()
            if total_stock:
                lot_id = str(total_stock.lot_id)
        
        # If not found, check main TrayId table
        if not lot_id:
            tray = TrayId.objects.filter(tray_id=tray_id).first()
            if tray and tray.lot_id:
                lot_id = str(tray.lot_id)
        
        if lot_id:
            return JsonResponse({
                'success': True, 
                'lot_id': lot_id,
                'tray_id': tray_id
            })
        else:
            return JsonResponse({
                'success': False, 
                'error': 'Tray not found or lot_id missing',
                'tray_id': tray_id
            })
            
    except Exception as e:
        return JsonResponse({
            'success': False, 
            'error': str(e),
            'tray_id': tray_id
        }, status=500)      


     
    

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def brass_get_rejection_remarks(request):
    """
    API endpoint used by the Brass_Completed template to return rejection remarks
    for a given lot_id. Returns JSON:
      { success: True, rejection_remarks: [{ reason, qty, tray_id }, ...] }
    """
    lot_id = request.GET.get('lot_id') or request.GET.get('lotid') or request.GET.get('stock_lot_id')
    if not lot_id:
        return Response({'success': False, 'error': 'Missing lot_id'}, status=400)

    try:
        scans = Brass_QC_Rejected_TrayScan.objects.filter(lot_id=lot_id).select_related('rejection_reason').order_by('id')
        remarks = []
        seen = set()
        for s in scans:
            reason = (s.rejection_reason.rejection_reason if getattr(s, 'rejection_reason', None) else '') or ''
            qty = s.rejected_tray_quantity or 0
            tray = s.rejected_tray_id or ''
            # Use tuple key to deduplicate while preserving first-seen order
            key = (reason.strip(), int(qty) if qty is not None else 0, str(tray).strip())
            if key in seen:
                continue
            seen.add(key)
            remarks.append({
                'reason': reason,
                'qty': qty,
                'tray_id': tray
            })
        return Response({'success': True, 'rejection_remarks': remarks})
    except Exception as e:
        # Keep error message concise for UI
        return Response({'success': False, 'error': str(e)}, status=500)




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


# ===== AUTO-SAVE FUNCTIONALITY VIEWS =====

@method_decorator(csrf_exempt, name='dispatch')
class BrassSaveRejectionDraftAPIView(APIView):
    """
    Auto-save endpoint for rejection form data.
    Saves rejection reasons and tray data as draft.
    Also updates brass_draft flag in TotalStockModel for the lot.
    """
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        try:
            # Accept both JSON body and DRF request.data
            try:
                data = json.loads(request.body)
            except Exception:
                data = request.data

            lot_id = data.get('lot_id')
            batch_id = data.get('batch_id')
            rejection_reasons = data.get('rejection_reasons', [])
            tray_rejections = data.get('tray_rejections', [])
            auto_save = data.get('auto_save', False)

            if not lot_id:
                return Response({
                    'success': False,
                    'error': 'Missing lot_id'
                }, status=400)

            # Save batch rejection draft if there are rejection reasons
            if rejection_reasons:
                Brass_QC_Draft_Store.objects.update_or_create(
                    lot_id=lot_id,
                    draft_type='batch_rejection',
                    defaults={
                        'batch_id': batch_id,
                        'user': request.user,
                        'draft_data': {
                            'rejection_reasons': rejection_reasons,
                            'auto_saved': auto_save,
                            'timestamp': timezone.now().isoformat()
                        }
                    }
                )

            # Save tray rejection draft if there are tray rejections
            if tray_rejections:
                Brass_QC_Draft_Store.objects.update_or_create(
                    lot_id=lot_id,
                    draft_type='tray_rejection',
                    defaults={
                        'batch_id': batch_id,
                        'user': request.user,
                        'draft_data': {
                            'tray_rejections': tray_rejections,
                            'auto_saved': auto_save,
                            'timestamp': timezone.now().isoformat()
                        }
                    }
                )

            # ✅ FIX: Update brass_draft flag in TotalStockModel
            TotalStockModel.objects.filter(lot_id=lot_id).update(brass_draft=True)

            return Response({
                'success': True,
                'message': 'Rejection draft saved successfully' if not auto_save else 'Auto-saved rejection data',
                'reasons_count': len(rejection_reasons),
                'trays_count': len(tray_rejections)
            })

        except Exception as e:
            return Response({
                'success': False,
                'error': f'Failed to save rejection draft: {str(e)}'
            }, status=500)
            
            
            
@method_decorator(csrf_exempt, name='dispatch')
class BrassSaveAcceptedTrayDraftAPIView(APIView):
    """
    Auto-save endpoint for accepted tray form data.
    Saves accepted tray and delink data as draft.
    """
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        try:
            data = json.loads(request.body)
            lot_id = data.get('lot_id')
            batch_id = data.get('batch_id')
            rows = data.get('rows', [])
            delink_trays = data.get('delink_trays', [])
            auto_save = data.get('auto_save', False)
            draft_save = data.get('draft_save', True)
            
            if not lot_id:
                return Response({
                    'success': False,
                    'error': 'Missing lot_id'
                }, status=400)
            
            # Use existing Brass_TopTray_Draft_Store model for top tray drafts
            
            # Find the main tray (first non-empty row)
            main_tray_id = ''
            main_tray_qty = 0
            
            for row in rows:
                if row.get('tray_id') and row.get('tray_qty'):
                    main_tray_id = row.get('tray_id', '')
                    main_tray_qty = int(row.get('tray_qty', 0))
                    break
            
            # Prepare delink trays data with position information
            delink_data = {
                'positions': []
            }
            
            for i, delink_tray in enumerate(delink_trays):
                if delink_tray.get('tray_id'):
                    delink_data['positions'].append({
                        'position': i,
                        'tray_id': delink_tray['tray_id'],
                        'original_capacity': delink_tray.get('original_capacity', 0)
                    })
            
            # Save to existing top tray draft store
            Brass_TopTray_Draft_Store.objects.update_or_create(
                lot_id=lot_id,
                defaults={
                    'batch_id': batch_id,
                    'user': request.user,
                    'tray_id': main_tray_id,
                    'tray_qty': main_tray_qty,
                    'delink_trays_data': delink_data
                }
            )
            
            # Also save accepted tray data using the existing accepted tray store
            # Clear existing drafts for this lot
            Brass_Qc_Accepted_TrayID_Store.objects.filter(lot_id=lot_id, is_draft=True).delete()
            
            # Save new draft data
            for i, row in enumerate(rows):
                if row.get('tray_id'):
                    Brass_Qc_Accepted_TrayID_Store.objects.create(
                        lot_id=lot_id,
                        tray_id=row.get('tray_id', ''),
                        tray_qty=int(row.get('tray_qty', 0)) if row.get('tray_qty') else None,
                        user=request.user,
                        is_draft=True,
                        is_save=False
                    )
            
            return Response({
                'success': True,
                'message': 'Accepted tray draft saved successfully' if not auto_save else 'Auto-saved accepted tray data',
                'rows_count': len(rows),
                'delink_count': len(delink_trays)
            })
            
        except Exception as e:
            return Response({
                'success': False,
                'error': f'Failed to save accepted tray draft: {str(e)}'
            }, status=500)


@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(login_required, name='dispatch')
class BrassSetManualDraftAPIView(APIView):
    """
    API endpoint to save manual draft data when user clicks draft button
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            data = request.data
            lot_id = data.get('lot_id')
            batch_id = data.get('batch_id')
            draft_type = data.get('draft_type')
            draft_data = data.get('draft_data')
            
            if not lot_id:
                return Response({
                    'success': False,
                    'error': 'Missing lot_id parameter'
                }, status=400)

            if not batch_id:
                return Response({
                    'success': False,
                    'error': 'Missing batch_id parameter'
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
                ModelMasterCreation.objects.get(lot_id=lot_id)
            except ModelMasterCreation.DoesNotExist:
                return Response({
                    'success': False,
                    'error': f'Lot {lot_id} not found'
                }, status=404)

            # Save the manual draft data
            draft_obj, created = Brass_QC_Draft_Store.objects.update_or_create(
                lot_id=lot_id,
                draft_type=draft_type,
                defaults={
                    'batch_id': batch_id,
                    'user': request.user,
                    'draft_data': draft_data
                }
            )

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

