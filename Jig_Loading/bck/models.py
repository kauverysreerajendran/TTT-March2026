from django.db import models
from django.utils import timezone
from django.db.models import F
from django.core.exceptions import ValidationError
import datetime
from datetime import timedelta
from django.contrib.auth.models import User
from django.utils.timezone import now
from django.db.models import JSONField
from django.contrib.postgres.fields import ArrayField
import uuid

#jig qr model
class Jig(models.Model):
    jig_qr_id = models.CharField(max_length=100, unique=True, help_text="Unique Jig QR ID")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_loaded = models.BooleanField(default=False, help_text="Is this Jig currently loaded?")
    current_user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, help_text="User currently using this jig")
    locked_at = models.DateTimeField(null=True, blank=True, help_text="When the jig was locked for draft")
    drafted = models.BooleanField(default=False, help_text="Is this Jig currently drafted?")
    batch_id = models.CharField(max_length=100, null=True, blank=True, help_text="Batch ID for which Jig is locked")  
    lot_id = models.CharField(max_length=100, null=True, blank=True, help_text="Lot ID for which Jig is locked")

    def __str__(self):
        return self.jig_qr_id
    
    
    def clear_user_lock(self):
        """Clear user lock when jig is unloaded or draft is cleared"""
        self.current_user = None
        self.locked_at = None
        self.save()
    
    def is_locked_by_other_user(self, user):
        """Check if jig is locked by a different user"""
        return (self.current_user is not None and 
                self.current_user != user and 
                self.has_active_draft())
    
    def has_active_draft(self):
        """Check if jig has active draft that hasn't been unloaded"""
        return JigDetails.objects.filter(
            jig_qr_id=self.jig_qr_id,
            draft_save=True,
            unload_over=False
        ).exists()

# Create your models here.
class JigLoadTrayId(models.Model):
    """
    BrassTrayId Model
    Represents a tray identifier in the Titan Track and Traceability system.
    """
    lot_id = models.CharField(max_length=50, null=True, blank=True, db_index=True, help_text="Lot ID")
    tray_id = models.CharField(max_length=100,  help_text="Tray ID")
    tray_quantity = models.IntegerField(null=True, blank=True, help_text="Quantity in the tray")
    batch_id = models.ForeignKey('modelmasterapp.ModelMasterCreation', on_delete=models.CASCADE, blank=True, null=True)
    recovery_batch_id = models.ForeignKey('Recovery_DP.RecoveryMasterCreation', on_delete=models.CASCADE, blank=True, null=True)
    date = models.DateTimeField(default=timezone.now)
    user = models.ForeignKey(User, on_delete=models.CASCADE, blank=True, null=True)
    top_tray = models.BooleanField(default=False)


    delink_tray = models.BooleanField(default=False, help_text="Is tray delinked")
    delink_tray_qty = models.CharField(max_length=50, null=True, blank=True, help_text="Delinked quantity")
    
    # Broken hooks segregation fields
    broken_hooks_effective_tray = models.BooleanField(default=False, help_text="Is tray part of effective quantity after broken hooks")
    broken_hooks_excluded_qty = models.IntegerField(default=0, help_text="Quantity excluded due to broken hooks")
    effective_tray_qty = models.IntegerField(null=True, blank=True, help_text="Effective quantity for this tray after broken hooks calculation")
    
    IP_tray_verified= models.BooleanField(default=False, help_text="Is tray verified in IP")
    
    rejected_tray= models.BooleanField(default=False, help_text="Is tray rejected")

    new_tray=models.BooleanField(default=True, help_text="Is tray new")
    
    # Tray configuration fields (filled by admin)
    tray_type = models.CharField(max_length=50, null=True, blank=True, help_text="Type of tray (Jumbo, Normal, etc.) - filled by admin")
    tray_capacity = models.IntegerField(null=True, blank=True, help_text="Capacity of this specific tray - filled by admin")

    def __str__(self):
        return f"{self.tray_id} - {self.lot_id} - {self.tray_quantity}"

    @property
    def is_available_for_scanning(self):
        """
        Check if tray is available for scanning
        Available if: not scanned OR delinked (can be reused)
        """
        return not self.scanned or self.delink_tray

    @property
    def status_display(self):
        """Get human-readable status"""
        if self.delink_tray:
            return "Delinked (Reusable)"
        elif self.scanned:
            return "Already Scanned"
        elif self.batch_id:
            return "In Use"
        else:
            return "Available"

    class Meta:
        verbose_name = "Jig Load Tray ID"
        verbose_name_plural = "Jig Load Tray IDs"
        
#jig Loading master
class JigLoadingMaster(models.Model):
    model_stock_no = models.ForeignKey('modelmasterapp.ModelMaster', on_delete=models.CASCADE, help_text="Model Stock Number")
    jig_type = models.CharField(max_length=100, help_text="Jig Type")
    jig_capacity = models.IntegerField(help_text="Jig Capacity")
    forging_info = models.CharField(max_length=100, help_text="Forging Info")
    
    def __str__(self):
        return f"{self.model_stock_no} - {self.jig_type} - {self.jig_capacity}"

class BathNumbers(models.Model):
    BATH_TYPE_CHOICES = [
        ('Bright', 'Bright'),
        ('Semi Bright', 'Semi Bright'),
        ('Dull', 'Dull'),
    ]
    
    bath_number = models.CharField(max_length=100)
    bath_type = models.CharField(
        max_length=20, 
        choices=BATH_TYPE_CHOICES,
        help_text="Type of bath this number belongs to"
    )
    is_active = models.BooleanField(default=True, help_text="Is this bath number active")
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['bath_number', 'bath_type']
        verbose_name = "Bath Number"
        verbose_name_plural = "Bath Numbers"
    
    def __str__(self):
        return f"{self.bath_number} ({self.bath_type})"

class JigDetails(models.Model):
    JIG_POSITION_CHOICES = [
        ('Top', 'Top'),
        ('Middle', 'Middle'),
        ('Bottom', 'Bottom'),
    ]
    jig_qr_id = models.CharField(max_length=100)
    faulty_slots = models.IntegerField(default=0)
    broken_hooks = models.IntegerField(default=0, help_text="Number of broken hooks")
    jig_type = models.CharField(max_length=50)  # New field
    jig_capacity = models.IntegerField()        # New field
    bath_tub = models.CharField(max_length=100, help_text="Bath Tub",blank=True, null=True)
    plating_color = models.CharField(max_length=50)
    empty_slots = models.IntegerField(default=0)
    ep_bath_type = models.CharField(max_length=50)
    total_cases_loaded = models.IntegerField()
    
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, help_text="User who created this draft")
    
    #JIG Loading Module - Remaining cases
    jig_cases_remaining_count=models.IntegerField(default=0,blank=True,null=True)
    updated_lot_qty = models.IntegerField(default=0, blank=True, null=True)
    original_lot_qty = models.IntegerField(default=0, blank=True, null=True)
    tray_info = models.JSONField(default=list, blank=True, null=True)
    delink_tray_info = models.JSONField(default=list, blank=True, null=True)
    partial_tray_info = models.JSONField(default=list, blank=True, null=True)
    half_filled_tray_info = models.JSONField(default=list, blank=True, null=True)
    

    forging = models.CharField(max_length=100)
    no_of_model_cases = ArrayField(models.CharField(max_length=50), blank=True, default=list)  # Correct ArrayField
    no_of_cycle=models.IntegerField(default=1)
    lot_id = models.CharField(max_length=100)
    new_lot_ids = ArrayField(models.CharField(max_length=50), blank=True, default=list)  # Correct ArrayField
    electroplating_only = models.BooleanField(default=False)
    lot_id_quantities = JSONField(blank=True, null=True)
    draft_save = models.BooleanField(default=False, help_text="Draft Save")
    delink_tray_data = models.JSONField(default=list, blank=True, null=True)  # Add this field
    half_filled_tray_data = models.JSONField(default=list, blank=True, null=True)
    date_time = models.DateTimeField(default=timezone.now)
    bath_numbers = models.ForeignKey(
            BathNumbers,
            on_delete=models.SET_NULL,  
            null=True,                  
            blank=True,
            help_text="Related Bath Numbers"
        )   
    
    jig_position = models.CharField(
        max_length=10,
        choices=JIG_POSITION_CHOICES,
        blank=True,
        null=True,
        default=None,
        help_text="Jig position: Top, Middle, or Bottom"
    )
    remarks = models.CharField(
        max_length=50,
        blank=True,
        help_text="Remarks (max 50 words)"
    )
    pick_remarks=models.CharField(
        max_length=50,
        blank=True,
        help_text="Remarks (max 50 words)"
    )
     
    jig_unload_draft = models.BooleanField(default=False)
    combined_lot_ids = ArrayField(models.CharField(max_length=50), blank=True, default=list)  # Correct ArrayField
    jig_loaded_date_time = models.DateTimeField(null=True, blank=True, help_text="Last Process Date/Time")
    IP_loaded_date_time = models.DateTimeField(null=True, blank=True, help_text="Ip last Process Date/Time")
    last_process_module = models.CharField(max_length=100, blank=True, help_text="Last Process Module")
    unload_over=models.BooleanField(default=False)
    Un_loaded_date_time = models.DateTimeField(null=True, blank=True, help_text="Ip last Process Date/Time")
    jig_lot_id = models.CharField(max_length=100, unique=True, blank=True, null=True, help_text="Unique Jig Lot ID")

        
    unload_holding_reason = models.CharField(max_length=255, null=True, blank=True, help_text="Unload Reason for holding the batch")
    unload_release_reason = models.CharField(max_length=255, null=True, blank=True, help_text="Unload Reason for releasing the batch")
    unload_hold_lot = models.BooleanField(default=False, help_text="Indicates if the lot is on hold n Unload")
    unload_release_lot = models.BooleanField(default=False)
    unloading_remarks = models.CharField(max_length=100, null=True, blank=True, help_text="JIG Pick Remarks")

    def __str__(self):
        return f"{self.jig_qr_id} - {self.jig_lot_id} - {self.no_of_cycle}"
    
    def save(self, *args, **kwargs):
        if not self.jig_lot_id:
            self.jig_lot_id = f"JLOT-{uuid.uuid4().hex[:12].upper()}"
        super().save(*args, **kwargs)
    
#  Auto Save Table
class JigAutoSave(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    lot_id = models.CharField(max_length=100, db_index=True)
    batch_id = models.CharField(max_length=100, db_index=True)
    session_key = models.CharField(max_length=40, blank=True)
    auto_save_data = models.JSONField(default=dict, blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['user', 'batch_id', 'lot_id']
        indexes = [models.Index(fields=['user', 'batch_id', 'lot_id', 'updated_at'])]

    def __str__(self):
        return f"AutoSave: {self.user.username} - {self.batch_id} - {self.lot_id}"
                    
# New JigDraft model for perfect accountability (no hardcoding)
class JigDraft(models.Model):
    """
    Immutable draft snapshot that splits original trays into
    delinked trays + half-filled trays, with backend-only reconciliation.
    """
    
    # ----------------------------
    # Identity & locking
    # ----------------------------
    jig_qr_id = models.CharField(
        max_length=100,
        help_text="Scanned Jig QR ID"
    )

    lot_id = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Lot ID under jig loading"
    )

    batch_id = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Batch ID reference"
    )

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="User who created the draft"
    )

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    is_active = models.BooleanField(
        default=True,
        help_text="Only one active draft allowed per jig + lot"
    )

    # ----------------------------
    # MASTER-DERIVED VALUES
    # (no hardcoding)
    # ----------------------------
    jig_capacity = models.IntegerField(
        help_text="Jig capacity derived from JigLoadingMaster"
    )

    tray_type = models.CharField(
        max_length=50,
        help_text="Tray type derived from batch / model master"
    )

    tray_capacity = models.IntegerField(
        help_text="Tray capacity derived from tray type master"
    )

    # ----------------------------
    # LOT QUANTITY ACCOUNTING
    # ----------------------------
    jig_original_qty = models.IntegerField(
        help_text="Original lot quantity at draft creation"
    )

    jig_delinked_qty = models.IntegerField(
        help_text="Qty that will go into jig (<= jig capacity)"
    )

    jig_excess_qty = models.IntegerField(
        help_text="Qty remaining after delink (original - delinked)"
    )

    # ----------------------------
    # TRAY DISTRIBUTION (IMMUTABLE SOURCE)
    # ----------------------------
    original_tray_info = models.JSONField(
        default=list,
        help_text="""
        Immutable original tray breakup.
        Example:
        [
          {"tray_id": "JB-A00020", "qty": 6, "is_top_tray": true},
          {"tray_id": "JB-A00021", "qty": 12},
          ...
        ]
        """
    )

    # ----------------------------
    # DELINKED TRAYS (INTO JIG)
    # ----------------------------
    delink_tray_info = models.JSONField(
        default=list,
        help_text="""
        Trays consumed into jig until jig capacity reached.
        Partial tray allowed.
        Qty sum MUST equal jig_delinked_qty.
        """
    )

    # ----------------------------
    # HALF-FILLED / REMAINING TRAYS
    # ----------------------------
    half_filled_tray_info = models.JSONField(
        default=list,
        help_text="""
        Remaining qty after delink:
        - Remaining qty from partial tray
        - All unscanned trays
        Qty sum MUST equal jig_excess_qty.
        """
    )

    # ----------------------------
    # BACKEND RECONCILIATION FLAGS
    # ----------------------------
    reconciliation_ok = models.BooleanField(
        default=False,
        help_text="Set true only after backend reconciliation passes"
    )

    remarks = models.CharField(
        max_length=255,
        blank=True,
        help_text="Backend remarks if reconciliation fails"
    )

    class Meta:
        db_table = "jig_draft"
        unique_together = ("jig_qr_id", "lot_id", "is_active")

    def __str__(self):
        return f"JigDraft - {self.jig_qr_id} | Lot: {self.lot_id} | Qty: {self.jig_original_qty}"

    def reconcile_draft(self):
        """
        Mandatory backend reconciliation logic.
        Returns (is_valid, error_message)
        """
        try:
            # Calculate sums from JSON fields
            original_sum = sum(t.get("qty", 0) for t in self.original_tray_info)
            delinked_sum = sum(t.get("qty", 0) for t in self.delink_tray_info)
            half_filled_sum = sum(t.get("qty", 0) for t in self.half_filled_tray_info)

            # Validation checks
            if original_sum != self.jig_original_qty:
                return False, f"Original tray qty mismatch: {original_sum} != {self.jig_original_qty}"

            if delinked_sum != self.jig_delinked_qty:
                return False, f"Delink tray qty mismatch: {delinked_sum} != {self.jig_delinked_qty}"

            if half_filled_sum != self.jig_excess_qty:
                return False, f"Half-filled tray qty mismatch: {half_filled_sum} != {self.jig_excess_qty}"

            if delinked_sum + half_filled_sum != original_sum:
                return False, f"Final reconciliation failed: {delinked_sum} + {half_filled_sum} != {original_sum}"

            # Additional validation: delinked qty should not exceed jig capacity
            if delinked_sum > self.jig_capacity:
                return False, f"Delinked qty exceeds jig capacity: {delinked_sum} > {self.jig_capacity}"

            return True, None

        except Exception as e:
            return False, f"Reconciliation error: {str(e)}"

    def save(self, *args, **kwargs):
        """Override save to ensure reconciliation before saving"""
        is_valid, error_msg = self.reconcile_draft()
        
        if not is_valid:
            self.reconciliation_ok = False
            self.remarks = error_msg
        else:
            self.reconciliation_ok = True
            self.remarks = ""
        
        super().save(*args, **kwargs)
        
    @classmethod
    def get_master_data(cls, batch_id, lot_id, jig_qr_id):
        """
        Fetch all master data dynamically - NO HARDCODING
        Returns: (jig_capacity, tray_type, tray_capacity, original_qty)
        """
        from modelmasterapp.models import ModelMasterCreation, TrayType
        import logging
        logger = logging.getLogger(__name__)
        
        try:
            logger.info(f"🔍 Step 1: Looking for batch_id={batch_id}")
            # Get batch/model info
            batch = ModelMasterCreation.objects.get(batch_id=batch_id)
            model_master = batch.model_stock_no
            logger.info(f"✅ Step 1: Found batch, model_master={model_master}")
            
            logger.info(f"🔍 Step 2: Looking for jig master for model={model_master}")
            # Get jig capacity from JigLoadingMaster
            jig_master = JigLoadingMaster.objects.filter(model_stock_no=model_master).first()
            if not jig_master:
                logger.error(f"❌ Step 2: No jig master found for model: {model_master}")
                raise ValueError(f"No jig master found for model: {model_master}")
            
            jig_capacity = jig_master.jig_capacity
            logger.info(f"✅ Step 2: Found jig_capacity={jig_capacity}")
            
            logger.info(f"🔍 Step 3: Looking for tray type from batch")
            # Get tray type and capacity from batch
            tray_type = batch.tray_type
            logger.info(f"✅ Step 3: Found tray_type={tray_type}")
            
            logger.info(f"🔍 Step 4: Looking for tray capacity for tray_type={tray_type}")
            # Get tray capacity from TrayType master
            tray_type_obj = TrayType.objects.filter(tray_type=tray_type).first()
            if not tray_type_obj:
                logger.error(f"❌ Step 4: No tray type master found for: {tray_type}")
                raise ValueError(f"No tray type master found for: {tray_type}")
            
            tray_capacity = tray_type_obj.tray_capacity
            logger.info(f"✅ Step 4: Found tray_capacity={tray_capacity}")
            
            logger.info(f"🔍 Step 5: Looking for stock for lot_id={lot_id}, batch_id={batch_id}")
            # Get original lot quantity from TotalStockModel
            from modelmasterapp.models import TotalStockModel
            stock = TotalStockModel.objects.filter(
                lot_id=lot_id,
                batch_id__batch_id=batch_id
            ).first()
            
            if not stock:
                logger.error(f"❌ Step 5: No stock found for lot: {lot_id}, batch: {batch_id}")
                raise ValueError(f"No stock found for lot: {lot_id}, batch: {batch_id}")
            
            original_qty = stock.total_stock or 0
            logger.info(f"✅ Step 5: Found original_qty={original_qty}")
            
            logger.info(f"🎉 All master data fetched successfully!")
            return jig_capacity, tray_type, tray_capacity, original_qty
            
        except Exception as e:
            logger.error(f"❌ Master data fetch failed: {str(e)}")
            raise ValueError(f"Failed to fetch master data: {str(e)}")


# Manual draft model to save all input fields       
class JigLoadingManualDraft(models.Model):
    batch_id = models.CharField(max_length=100, db_index=True)
    lot_id = models.CharField(max_length=100, db_index=True)
    user = models.ForeignKey('auth.User', on_delete=models.CASCADE)
    draft_data = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)
    jig_cases_remaining_count = models.IntegerField(default=0, blank=True, null=True)
    updated_lot_qty = models.IntegerField(default=0, blank=True, null=True)
    original_lot_qty = models.IntegerField(default=0, blank=True, null=True)
    jig_id = models.CharField(max_length=100, blank=True, null=True)
    delink_tray_info = models.JSONField(default=list, blank=True, null=True)
    delink_tray_qty = models.IntegerField(default=0, blank=True, null=True)
    delink_tray_count = models.IntegerField(default=0, blank=True, null=True)
    half_filled_tray_info = models.JSONField(default=list, blank=True, null=True)
    half_filled_tray_qty = models.IntegerField(default=0, blank=True, null=True)
    half_filled_lot_qty = models.IntegerField(default=0, blank=True, null=True)
    jig_capacity = models.IntegerField(default=0, blank=True, null=True)
    broken_hooks = models.IntegerField(default=0, blank=True, null=True)
    loaded_cases_qty = models.IntegerField(default=0, blank=True, null=True)
    draft_status = models.CharField(max_length=20, choices=[('active', 'Active'), ('submitted', 'Submitted')], default='active')
    
    # Multi-model support fields
    is_multi_model = models.BooleanField(default=False, help_text="Is this a multi-model jig")
    model_tabs_data = models.JSONField(default=list, blank=True, null=True, help_text="Data for multiple models in jig")
    primary_model_lot_id = models.CharField(max_length=100, blank=True, null=True, help_text="Primary model lot ID")
    additional_models_data = models.JSONField(default=list, blank=True, null=True, help_text="Additional models data")

    class Meta:
        unique_together = ['batch_id', 'lot_id', 'user']  # <-- FIXED!

    def __str__(self):
        return f"Draft: {self.batch_id} by {self.user.username}"
                    
# Jig Completed model - duplicate of JigLoadingManualDraft
class JigCompleted(models.Model):
    batch_id = models.CharField(max_length=100, db_index=True)
    lot_id = models.CharField(max_length=100, db_index=True)
    user = models.ForeignKey('auth.User', on_delete=models.CASCADE)
    draft_data = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)
    jig_cases_remaining_count = models.IntegerField(default=0, blank=True, null=True)
    updated_lot_qty = models.IntegerField(default=0, blank=True, null=True)
    original_lot_qty = models.IntegerField(default=0, blank=True, null=True)
    jig_id = models.CharField(max_length=100, blank=True, null=True)
    delink_tray_info = models.JSONField(default=list, blank=True, null=True)
    delink_tray_qty = models.IntegerField(default=0, blank=True, null=True)
    delink_tray_count = models.IntegerField(default=0, blank=True, null=True)
    half_filled_tray_info = models.JSONField(default=list, blank=True, null=True)
    half_filled_tray_qty = models.IntegerField(default=0, blank=True, null=True)
    half_filled_lot_qty = models.IntegerField(default=0, blank=True, null=True)
    jig_capacity = models.IntegerField(default=0, blank=True, null=True)
    broken_hooks = models.IntegerField(default=0, blank=True, null=True)
    loaded_cases_qty = models.IntegerField(default=0, blank=True, null=True)
    draft_status = models.CharField(max_length=20, choices=[('active', 'Active'), ('submitted', 'Submitted')], default='active')
    hold_status = models.BooleanField(default=False)
    hold_reason = models.CharField(max_length=255, blank=True, null=True)
    unhold_reason = models.CharField(max_length=255, blank=True, null=True)
    
    # Multi-model support fields
    is_multi_model = models.BooleanField(default=False, help_text="Is this a multi-model jig")
    model_tabs_data = models.JSONField(default=list, blank=True, null=True, help_text="Data for multiple models in jig")
    primary_model_lot_id = models.CharField(max_length=100, blank=True, null=True, help_text="Primary model lot ID")
    additional_models_data = models.JSONField(default=list, blank=True, null=True, help_text="Additional models data")

    class Meta:
        unique_together = ['batch_id', 'lot_id', 'user']
        verbose_name = "Jig Completed"
        verbose_name_plural = "Jig Completed"

    def __str__(self):
        return f"Jig Completed: {self.batch_id} by {self.user.username}"