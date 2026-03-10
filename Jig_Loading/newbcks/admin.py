from django.contrib import admin
from Jig_Loading.models import *
from django.utils.html import format_html



class JigLoadingMasterAdmin(admin.ModelAdmin):
    list_display = ['get_model_stock_no', 'jig_type', 'jig_capacity', 'forging_info']
    list_filter = ['jig_type']
    search_fields = ['model_stock_no__model_no', 'model_stock_no__plating_stk_no', 'jig_type', 'forging_info']
    
    def get_model_stock_no(self, obj):
        """Display the model stock number in a readable format"""
        if obj.model_stock_no and obj.model_stock_no.model_no:
            return obj.model_stock_no.model_no
        return '-'
    get_model_stock_no.short_description = 'Model Stock No'
    get_model_stock_no.admin_order_field = 'model_stock_no__model_no'
    
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "model_stock_no":
            # Ensure queryset shows model_no properly
            kwargs["queryset"] = db_field.remote_field.model.objects.exclude(model_no__isnull=True).exclude(model_no__exact='')
            kwargs["empty_label"] = "Select Model Number"
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


# Jigs Table
class JigAdmin(admin.ModelAdmin):
    list_display = ['jig_qr_id', 'is_loaded', 'get_is_drafted', 'get_current_user', 'get_locked_at',  'created_at', 'updated_at']  # <-- ADD 'get_is_drafted' HERE
    list_filter = ['is_loaded', 'drafted', 'created_at', 'updated_at']
    search_fields = ['jig_qr_id', 'current_user__username', 'current_user__first_name', 'current_user__last_name']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['-updated_at']
    
    #is_drafted
    def get_is_drafted(self, obj):
        return obj.drafted
    get_is_drafted.boolean = True
    get_is_drafted.short_description = 'Is Drafted'
    get_is_drafted.admin_order_field = 'drafted'
    
    #is_loaded
    def mark_as_unloaded(self, request, queryset):
        """Mark selected jigs as unloaded"""
        updated = queryset.update(is_loaded=False)
        self.message_user(request, f"Successfully marked {updated} jig(s) as unloaded.")
    mark_as_unloaded.short_description = "Mark selected jigs as unloaded"
    
    #current user
    def get_current_user(self, obj):
        """Display current user in a readable format"""
        if obj.current_user:
            return f"{obj.current_user.username} ({obj.current_user.get_full_name() or 'No name'})"
        return '-'
    get_current_user.short_description = 'Current User'
    get_current_user.admin_order_field = 'current_user__username'
    
    #locked at
    def get_locked_at(self, obj):
        """Display locked timestamp in a readable format"""
        if obj.locked_at:
            return obj.locked_at.strftime("%Y-%m-%d %H:%M:%S")
        return '-'
    get_locked_at.short_description = 'Locked At'
    get_locked_at.admin_order_field = 'locked_at'
    
    # Add actions for bulk operations
    actions = ['clear_user_locks', 'mark_as_unloaded']
    
    # Clear user locks action
    def clear_user_locks(self, request, queryset):
        """Clear user locks for selected jigs"""
        updated = 0
        for jig in queryset:
            jig.clear_user_lock()
            updated += 1
        self.message_user(request, f"Successfully cleared user locks for {updated} jig(s).")
    clear_user_locks.short_description = "Clear user locks for selected jigs"


# Jig Load Tray ID Table
class JigLoadTrayIdAdmin(admin.ModelAdmin):
    list_display = ['tray_id', 'lot_id', 'tray_quantity', 'batch_id', 'user', 'date', 'delink_tray', 'IP_tray_verified', 'rejected_tray']
    list_filter = ['delink_tray', 'IP_tray_verified', 'rejected_tray', 'date', 'user']
    search_fields = ['tray_id', 'lot_id', 'batch_id__batch_id']
    readonly_fields = ['date']
    ordering = ['-date']


# Bath Numbers Table
class BathNumbersAdmin(admin.ModelAdmin):
    list_display = ['bath_number', 'bath_type', 'is_active', 'created_at']
    list_filter = ['bath_type', 'is_active', 'created_at']
    search_fields = ['bath_number', 'bath_type']
    readonly_fields = ['created_at']


# Auto Save Table
class JigAutoSaveAdmin(admin.ModelAdmin):
    list_display = ['user', 'batch_id', 'lot_id', 'session_key', 'updated_at']
    list_filter = ['updated_at', 'user']
    search_fields = ['batch_id', 'lot_id', 'user__username', 'session_key']
    readonly_fields = ['updated_at', 'auto_save_data']


class JigCompletedAdmin(admin.ModelAdmin):
    list_display = ['lot_id', 'jig_id', 'user', 'delink_tray_count', 'updated_lot_qty', 'broken_hooks', 'draft_status', 'updated_at', 'original_lot_qty', 'jig_capacity', 'loaded_cases_qty', 'delink_tray_qty', 'half_filled_tray_qty']
    list_filter = ['updated_at', 'user', 'draft_status']
    search_fields = ['lot_id', 'jig_id', 'user__username']
    readonly_fields = ['updated_at', 'draft_data']


# Jig Details Table
class JigDetailsAdmin(admin.ModelAdmin):
    list_display = [
        'jig_qr_id',
        'lot_id',
        'total_cases_loaded',
        'jig_cases_remaining_count',
        'no_of_cycle',
        'get_created_by',
        'date_time',
        'draft_save',
        'jig_lot_id',
    ]
    list_filter = ['draft_save', 'no_of_cycle', 'date_time', 'created_by']
    search_fields = ['jig_qr_id', 'lot_id', 'jig_lot_id', 'created_by__username']
    readonly_fields = ['date_time', 'jig_lot_id']
    ordering = ['-date_time']
    
    def get_created_by(self, obj):
        """Display created by user in a readable format"""
        if obj.created_by:
            return f"{obj.created_by.username} ({obj.created_by.get_full_name() or 'No name'})"
        return '-'
    get_created_by.short_description = 'Created By'
    get_created_by.admin_order_field = 'created_by__username'


admin.site.register(Jig, JigAdmin)
admin.site.register(JigLoadTrayId, JigLoadTrayIdAdmin)
admin.site.register(JigLoadingMaster, JigLoadingMasterAdmin)
admin.site.register(BathNumbers, BathNumbersAdmin)
admin.site.register(JigAutoSave, JigAutoSaveAdmin)
admin.site.register(JigCompleted, JigCompletedAdmin)