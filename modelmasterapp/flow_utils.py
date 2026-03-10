from django.db import transaction
from Brass_QC.models import (
    BrassTrayId, Brass_QC_Rejected_TrayScan, Brass_Qc_Accepted_TrayScan, 
    Brass_Qc_Accepted_TrayID_Store, Brass_QC_Draft_Store, Brass_TopTray_Draft_Store,
    Brass_QC_Rejection_ReasonStore
)
from BrassAudit.models import (
    BrassAuditTrayId, Brass_Audit_Rejected_TrayScan, Brass_Audit_Accepted_TrayScan,
    Brass_Audit_Accepted_TrayID_Store, Brass_Audit_Draft_Store, Brass_Audit_TopTray_Draft_Store,
    Brass_Audit_Rejection_ReasonStore
)


def clear_downstream_lot_data(lot_id):
    """
    Clears all temporary and processed data for a lot in Brass QC and Brass Audit modules.
    Used when a lot is being sent back to an earlier stage (e.g. from IQF to Brass QC)
    to ensure there are no stale or duplicate records.
    
    Cleans up: tray records, draft stores, rejection scans, accepted stores,
    top tray drafts, and rejection reason stores in both modules.
    """
    with transaction.atomic():
        # --- Clear Brass Audit Data ---
        del_audit_trays = BrassAuditTrayId.objects.filter(lot_id=lot_id).delete()[0]
        del_audit_rej_scans = Brass_Audit_Rejected_TrayScan.objects.filter(lot_id=lot_id).delete()[0]
        del_audit_acc_scans = Brass_Audit_Accepted_TrayScan.objects.filter(lot_id=lot_id).delete()[0]
        del_audit_acc_store = Brass_Audit_Accepted_TrayID_Store.objects.filter(lot_id=lot_id).delete()[0]
        del_audit_drafts = Brass_Audit_Draft_Store.objects.filter(lot_id=lot_id).delete()[0]
        del_audit_tt_drafts = Brass_Audit_TopTray_Draft_Store.objects.filter(lot_id=lot_id).delete()[0]
        del_audit_reasons = Brass_Audit_Rejection_ReasonStore.objects.filter(lot_id=lot_id).delete()[0]
        
        # --- Clear Brass QC Data ---
        del_qc_trays = BrassTrayId.objects.filter(lot_id=lot_id).delete()[0]
        del_qc_rej_scans = Brass_QC_Rejected_TrayScan.objects.filter(lot_id=lot_id).delete()[0]
        del_qc_acc_scans = Brass_Qc_Accepted_TrayScan.objects.filter(lot_id=lot_id).delete()[0]
        del_qc_acc_store = Brass_Qc_Accepted_TrayID_Store.objects.filter(lot_id=lot_id).delete()[0]
        del_qc_drafts = Brass_QC_Draft_Store.objects.filter(lot_id=lot_id).delete()[0]
        del_qc_tt_drafts = Brass_TopTray_Draft_Store.objects.filter(lot_id=lot_id).delete()[0]
        del_qc_reasons = Brass_QC_Rejection_ReasonStore.objects.filter(lot_id=lot_id).delete()[0]
        
        total_deleted = (
            del_audit_trays + del_audit_rej_scans + del_audit_acc_scans + del_audit_acc_store +
            del_audit_drafts + del_audit_tt_drafts + del_audit_reasons +
            del_qc_trays + del_qc_rej_scans + del_qc_acc_scans + del_qc_acc_store +
            del_qc_drafts + del_qc_tt_drafts + del_qc_reasons
        )
        
        print(f"✅ [FLOW UTILS] Cleared {total_deleted} downstream records for lot {lot_id}")
        print(f"   Brass Audit: {del_audit_trays} trays, {del_audit_rej_scans} rej scans, "
              f"{del_audit_acc_store} acc store, {del_audit_drafts} drafts, {del_audit_reasons} reasons")
        print(f"   Brass QC: {del_qc_trays} trays, {del_qc_rej_scans} rej scans, "
              f"{del_qc_acc_store} acc store, {del_qc_drafts} drafts, {del_qc_reasons} reasons")
        return True
