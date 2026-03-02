from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from modelmasterapp.models import *
from django.db.models import OuterRef, Subquery, Exists, F, TextField
from django.db.models.functions import Cast
from django.db.models.fields.json import KeyTextTransform
from django.core.paginator import Paginator
import math
import json
import re
from django.utils.decorators import method_decorator
from rest_framework.views import APIView
from django.views.generic import TemplateView
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.views.decorators.http import require_GET
from Jig_Loading.models import *
from Jig_Unloading.models import *
from Recovery_DP.models import *
from Inprocess_Inspection.models import InprocessInspectionTrayCapacity

class Jig_Unloading_MainTable(TemplateView):
    template_name = "Jig_Unloading/Jig_Unloading_Main.html"

    def get_dynamic_tray_capacity(self, tray_type_name):
        """
        Get dynamic tray capacity using InprocessInspectionTrayCapacity for overrides
        Rules:
        - Normal: Use custom capacity from InprocessInspectionTrayCapacity (20)
        - Others: Use ModelMaster capacity
        """
        try:
            # First try to get custom capacity for this tray type
            custom_capacity = InprocessInspectionTrayCapacity.objects.filter(
                tray_type__tray_type=tray_type_name,
                is_active=True
            ).first()
            
            if custom_capacity:
                return custom_capacity.custom_capacity
            
            # Fallback to ModelMaster tray capacity
            tray_type = TrayType.objects.filter(tray_type=tray_type_name).first()
            if tray_type:
                return tray_type.tray_capacity
                
            # Default fallback
            return 0
            
        except Exception as e:
            print(f"⚠️ Error getting dynamic tray capacity: {e}")
            return 0

    def get_stock_model_data(self, lot_id):
        """
        Helper function to get stock model data from either TotalStockModel or RecoveryStockModel
        Returns: (stock_model, is_recovery, batch_model_class)
        """
        # Try TotalStockModel first
        tsm = TotalStockModel.objects.filter(lot_id=lot_id).first()
        if tsm:
            return tsm, False, ModelMasterCreation
        
        # Try RecoveryStockModel if not found in TotalStockModel
        try:
            rsm = RecoveryStockModel.objects.filter(lot_id=lot_id).first()
            if rsm:
                # Try to import RecoveryMasterCreation safely
                try:
                    from Recovery_DP.models import RecoveryMasterCreation
                    return rsm, True, RecoveryMasterCreation
                except ImportError:
                    print("⚠️ RecoveryMasterCreation not found, using ModelMasterCreation as fallback")
                    return rsm, True, ModelMasterCreation
        except Exception as e:
            print(f"⚠️ Error accessing RecoveryStockModel: {e}")
        
        return None, False, None

    def get_lot_specific_data(self, lot_id, model_no):
        """Get lot-specific data from either TotalStockModel or RecoveryStockModel"""
        
        print(f"🔍 get_lot_specific_data called for lot_id: {lot_id}, model_no: {model_no}")
        
        # STEP 1: Get stock model data using the proven method
        stock_model, is_recovery, batch_model_class = self.get_stock_model_data(lot_id)
        
        if not stock_model or not stock_model.batch_id:
            print(f"❌ No stock model or batch_id found for lot_id: {lot_id}")
            return None
            
        batch_id = stock_model.batch_id.id
        print(f"✅ Found stock model: {'Recovery' if is_recovery else 'Total'}Stock -> batch_id: {batch_id}")
        
        # STEP 2: Get MasterCreation data using batch_id and correct model class
        try:
            print(f"🏭 Looking up {batch_model_class.__name__} for batch_id: {batch_id}")
            
            master_creation = batch_model_class.objects.select_related(
                'version', 'model_stock_no', 'model_stock_no__tray_type', 'location'
            ).filter(id=batch_id).first()
            
            if master_creation:
                print(f"🎯 Found {batch_model_class.__name__} for batch_id: {batch_id}")
                print(f"🏭 Details: plating_stk_no={getattr(master_creation, 'plating_stk_no', None)}, polishing_stk_no={getattr(master_creation, 'polishing_stk_no', None)}, version={getattr(master_creation, 'version', None)}")
                
                # Safe version access - prioritize version_internal
                version_name = "No Version"
                if hasattr(master_creation, 'version') and master_creation.version:
                    version_name = getattr(master_creation.version, 'version_internal', None) or getattr(master_creation.version, 'version_name', 'No Version')
                
                return {
                    'version': version_name,
                    'vendor': getattr(master_creation, 'vendor_internal', None) or "No Vendor",
                    'location': master_creation.location.location_name if hasattr(master_creation, 'location') and master_creation.location else "No Location",
                    'tray_type': master_creation.model_stock_no.tray_type.tray_type if master_creation.model_stock_no and master_creation.model_stock_no.tray_type else "No Tray Type",
                    'tray_capacity': self.get_dynamic_tray_capacity(master_creation.model_stock_no.tray_type.tray_type) if master_creation.model_stock_no and master_creation.model_stock_no.tray_type else 0,
                    'plating_stk_no': getattr(master_creation, 'plating_stk_no', None) or "No Plating Stock No",
                    'polishing_stk_no': getattr(master_creation, 'polishing_stk_no', None) or "No Polishing Stock No",
                    'plating_color': getattr(master_creation, 'plating_color', None) or "N/A",
                    'source': batch_model_class.__name__
                }
            else:
                print(f"❌ No {batch_model_class.__name__} found for batch_id: {batch_id}")
                
        except Exception as e:
            print(f"❌ Error getting {batch_model_class.__name__} data: {e}")
        
        print(f"❌ No lot-specific data found for lot_id: {lot_id}")
        return None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Zone 1: Only IPS color should be routed here
        # Get only IPS plating color for Zone 1
        allowed_colors = Plating_Color.objects.filter(
            plating_color='IPS'
        ).values_list('plating_color', flat=True)
        
        print(f"🔍 Zone 1 - Allowed colors: {list(allowed_colors)}")

        # Get all plating colors and strip "IP-" prefix from stored values for matching
        allowed_colors_list = list(allowed_colors)
        
        # Build list of patterns to match (handle both "IPS" and "IP-IPS" formats)
        plating_patterns = allowed_colors_list + [f"IP-{color}" for color in allowed_colors_list]
        
        # Create polish_finish subquery for annotation
        polish_finish_subquery = TotalStockModel.objects.filter(
            lot_id=OuterRef('lot_id')
        ).values('polish_finish__polish_finish')[:1]
        
        jig_unload = JigCompleted.objects.select_related('bath_numbers').annotate(
            plating_color_cast=KeyTextTransform('plating_color', 'draft_data'),
            polish_finish_name=Subquery(polish_finish_subquery)
        ).filter(
            plating_color_cast__in=plating_patterns
        ).order_by('-IP_loaded_date_time')
        
        # ENHANCED FILTER: Also get jigs where plating_color is not in draft_data 
        # but can be determined from TotalStockModel or RecoveryStockModel
        jigs_without_plating_in_draft = JigCompleted.objects.select_related('bath_numbers').annotate(
            plating_color_cast=KeyTextTransform('plating_color', 'draft_data'),
            polish_finish_name=Subquery(polish_finish_subquery)
        ).filter(
            plating_color_cast__isnull=True  # draft_data has no plating_color
        ).order_by('-IP_loaded_date_time')
        
        # Get lot_ids from jigs without plating color in draft_data
        lot_ids_without_plating = list(jigs_without_plating_in_draft.values_list('lot_id', flat=True))
        
        if lot_ids_without_plating:
            # Get plating colors from TotalStockModel for these lot_ids
            total_stock_colors = TotalStockModel.objects.filter(
                lot_id__in=lot_ids_without_plating,
                plating_color__plating_color__in=allowed_colors_list
            ).values_list('lot_id', 'plating_color__plating_color')
            
            # Get plating colors from RecoveryStockModel for these lot_ids
            try:
                from Recovery_DP.models import RecoveryStockModel
                recovery_stock_colors = RecoveryStockModel.objects.filter(
                    lot_id__in=lot_ids_without_plating,
                    plating_color__plating_color__in=allowed_colors_list
                ).values_list('lot_id', 'plating_color__plating_color')
            except:
                recovery_stock_colors = []
            
            # Combine all valid lot_ids that have matching plating colors
            valid_lot_ids_for_zone1 = set()
            for lot_id, color in total_stock_colors:
                if color in allowed_colors_list:
                    valid_lot_ids_for_zone1.add(lot_id)
                    
            for lot_id, color in recovery_stock_colors:
                if color in allowed_colors_list:
                    valid_lot_ids_for_zone1.add(lot_id)
            
            # Get additional jigs that match by lot_id even if draft_data lacks plating_color
            additional_jigs = jigs_without_plating_in_draft.filter(lot_id__in=valid_lot_ids_for_zone1)
            
            # Combine both sets: jigs with plating_color in draft_data + jigs with valid colors from TotalStock/Recovery
            jig_unload = jig_unload.union(additional_jigs, all=False)
        
        # 🧠 SMART FILTER: Remove jigs with ALL lot_ids unloaded
        jig_unload = self.filter_fully_unloaded_jigs(jig_unload)
        
        # Fetch all Bath Numbers for dropdown
        bath_numbers = BathNumbers.objects.all().order_by('bath_number')
        
        # Get all unique model numbers from all jig_unload for bulk processing
        all_model_numbers = set()
        all_lot_ids = set()
        for jig_detail in jig_unload:
            if jig_detail.no_of_model_cases:
                all_model_numbers.update(jig_detail.no_of_model_cases)
            # 🔥 NEW: Collect all lot_ids for dual-table lookup
            if hasattr(jig_detail, 'lot_id_quantities') and jig_detail.lot_id_quantities:
                all_lot_ids.update(jig_detail.lot_id_quantities.keys())
                
        # Define color palette for model circles
        color_palette = [
            "#e74c3c",  # Red
            "#f1c40f",  # Yellow
            "#2ecc71",  # Green
            "#3498db",  # Blue
            "#9b59b6",  # Purple
            "#e67e22",  # Orange
            "#1abc9c",  # Turquoise
            "#34495e",  # Dark Blue-Gray
            "#f39c12",  # Dark Orange
            "#d35400",  # Dark Orange
            "#c0392b",  # Dark Red
            "#8e44ad",  # Dark Purple
            "#2980b9",  # Dark Blue
            "#27ae60",  # Dark Green
            "#16a085"   # Dark Turquoise
        ]
        
        # Create a global color mapping for all unique model numbers
        global_model_colors = {}
        sorted_model_numbers = sorted(list(all_model_numbers))
        print(f"🎨 Creating color mapping for models: {sorted_model_numbers}")

        for idx, model_no in enumerate(sorted_model_numbers):
            color_index = idx % len(color_palette)
            assigned_color = color_palette[color_index]
            # Ensure model_no is always stored as a complete string
            model_key = str(model_no)  # Force string conversion
            global_model_colors[model_key] = assigned_color
            print(f"🎨 Assigned {assigned_color} to model '{model_key}'")

        # 🚀 ENHANCED: Dual-table model data fetching (TotalStock + Recovery)
        model_data_map = {}
        model_images_map = {}
        
        if all_model_numbers:
            # First, try ModelMasterCreation (linked to TotalStockModel)
            model_master_creations = ModelMasterCreation.objects.filter(
                model_stock_no__model_no__in=all_model_numbers
            ).select_related(
                'version', 
                'model_stock_no', 
                'model_stock_no__tray_type', 
                'location'
            ).values(
                'model_stock_no__model_no', 
                'version__version_name',
                'vendor_internal',
                'location__location_name',
                'model_stock_no__tray_type__tray_type',
                'model_stock_no__tray_capacity',
                'plating_stk_no',
                'polishing_stk_no',
            ).distinct()
            
            # Store found model numbers to avoid duplicates
            found_model_numbers = set()
            
            for mmc in model_master_creations:
                model_no = mmc['model_stock_no__model_no']
                found_model_numbers.add(model_no)
                
                if model_no not in model_data_map:
                    tray_type_name = mmc['model_stock_no__tray_type__tray_type'] or "No Tray Type"
                    dynamic_capacity = self.get_dynamic_tray_capacity(tray_type_name) if tray_type_name != "No Tray Type" else 0
                    model_data_map[model_no] = {
                        'version': mmc['version__version_name'] or "No Version",
                        'vendor': mmc['vendor_internal'] or "No Vendor",
                        'location': mmc['location__location_name'] or "No Location",
                        'tray_type': tray_type_name,
                        'tray_capacity': dynamic_capacity,
                        'plating_stk_no': mmc['plating_stk_no'] or "No Plating Stock No",
                        'polishing_stk_no': mmc['polishing_stk_no'] or "No Polishing Stock No",
                        'plating_color': "N/A",
                        'source': 'ModelMasterCreation'
                    }

            # 🔥 NEW: For missing models, try RecoveryMasterCreation
            missing_model_numbers = all_model_numbers - found_model_numbers
            
            if missing_model_numbers:
                print(f"🔍 Checking RecoveryMasterCreation for missing models: {missing_model_numbers}")
                
                recovery_master_creations = RecoveryMasterCreation.objects.filter(
                    model_stock_no__model_no__in=missing_model_numbers
                ).select_related(
                    'version', 
                    'model_stock_no', 
                    'model_stock_no__tray_type', 
                    'location'
                ).values(
                    'model_stock_no__model_no', 
                    'version__version_name',
                    'vendor_internal',
                    'location__location_name',
                    'model_stock_no__tray_type__tray_type',
                    'model_stock_no__tray_capacity',
                    'plating_stk_no',
                    'polishing_stk_no',
                ).distinct()
                
                for rmc in recovery_master_creations:
                    model_no = rmc['model_stock_no__model_no']
                    
                    if model_no not in model_data_map:
                        tray_type_name = rmc['model_stock_no__tray_type__tray_type'] or "No Tray Type"
                        dynamic_capacity = self.get_dynamic_tray_capacity(tray_type_name) if tray_type_name != "No Tray Type" else 0
                        model_data_map[model_no] = {
                            'version': rmc['version__version_name'] or "No Version",
                            'vendor': rmc['vendor_internal'] or "No Vendor", 
                            'location': rmc['location__location_name'] or "No Location",
                            'tray_type': tray_type_name,
                            'tray_capacity': dynamic_capacity,
                            'plating_stk_no': rmc['plating_stk_no'] or "No Plating Stock No",
                            'polishing_stk_no': rmc['polishing_stk_no'] or "No Polishing Stock No",
                            'plating_color': "N/A",
                            'source': 'RecoveryMasterCreation'
                        }
                        print(f"✅ Found {model_no} in RecoveryMasterCreation")

            # SEPARATE QUERY TO GET PLATING COLORS - ENHANCED FOR DUAL SOURCE
            print("=== DEBUGGING PLATING COLOR FIELD ===")
            try:
                # Try ModelMasterCreation first
                mmc_sample = ModelMasterCreation.objects.first()
                if mmc_sample and hasattr(mmc_sample, 'plating_color'):
                    if isinstance(mmc_sample.plating_color, str):
                        # Direct string field - try both tables
                        plating_colors = ModelMasterCreation.objects.filter(
                            model_stock_no__model_no__in=all_model_numbers
                        ).values('model_stock_no__model_no', 'plating_color').distinct()
                        
                        for pc in plating_colors:
                            model_no = pc['model_stock_no__model_no']
                            if model_no in model_data_map:
                                model_data_map[model_no]['plating_color'] = pc['plating_color'] or "N/A"
                        
                        # Also try RecoveryMasterCreation for plating colors
                        recovery_plating_colors = RecoveryMasterCreation.objects.filter(
                            model_stock_no__model_no__in=missing_model_numbers
                        ).values('model_stock_no__model_no', 'plating_color').distinct()
                        
                        for pc in recovery_plating_colors:
                            model_no = pc['model_stock_no__model_no']
                            if model_no in model_data_map:
                                model_data_map[model_no]['plating_color'] = pc['plating_color'] or "N/A"
                    
                    elif hasattr(mmc_sample.plating_color, '_meta'):
                        # Related object field
                        related_model = type(mmc_sample.plating_color)
                        related_fields = [f.name for f in related_model._meta.get_fields()]
                        
                        color_field = None
                        for field_name in ['name', 'color_name', 'color', 'title']:
                            if field_name in related_fields:
                                color_field = field_name
                                break
                        
                        if color_field:
                            # ModelMasterCreation plating colors
                            plating_colors = ModelMasterCreation.objects.filter(
                                model_stock_no__model_no__in=all_model_numbers
                            ).select_related('plating_color').values(
                                'model_stock_no__model_no', 
                                f'plating_color__{color_field}'
                            ).distinct()
                            
                            for pc in plating_colors:
                                model_no = pc['model_stock_no__model_no']
                                if model_no in model_data_map:
                                    model_data_map[model_no]['plating_color'] = pc[f'plating_color__{color_field}'] or "N/A"
                            
                            # RecoveryMasterCreation plating colors
                            recovery_plating_colors = RecoveryMasterCreation.objects.filter(
                                model_stock_no__model_no__in=missing_model_numbers
                            ).select_related('plating_color').values(
                                'model_stock_no__model_no', 
                                f'plating_color__{color_field}'
                            ).distinct()
                            
                            for pc in recovery_plating_colors:
                                model_no = pc['model_stock_no__model_no']
                                if model_no in model_data_map:
                                    model_data_map[model_no]['plating_color'] = pc[f'plating_color__{color_field}'] or "N/A"
                
            except Exception as e:
                print(f"Error debugging plating color: {e}")

            # Fetch images for each model
            print(f"🔍 DEBUG: Looking up images for models: {all_model_numbers}")
            
            # ✅ FIX: Create clean model mapping for image lookup (like Inprocess Inspection)
            clean_model_mapping = {}
            for model_no in all_model_numbers:
                clean_model_no = model_no
                # Extract just the numeric part (e.g., "1805SAD02" -> "1805")
                match = re.match(r'^(\d+)', str(model_no))
                if match:
                    clean_model_no = match.group(1)
                clean_model_mapping[model_no] = clean_model_no
                print(f"🔍 Model mapping: {model_no} -> {clean_model_no}")
            
            # Get unique clean model numbers for lookup
            clean_model_numbers = set(clean_model_mapping.values())
            
            model_masters = ModelMaster.objects.filter(
                model_no__in=clean_model_numbers
            ).prefetch_related('images')
            
            # Create lookup map for clean model numbers to images
            clean_model_images = {}
            for model_master in model_masters:
                images = list(model_master.images.all())
                print(f"📸 DEBUG: Clean model {model_master.model_no} has {len(images)} images")
                
                if model_master.model_no == '1805' or '1805' in model_master.model_no:
                    print(f"🎯 SPECIAL DEBUG for clean model {model_master.model_no}:")
                    print(f"   Found {len(images)} images")
                    for i, img in enumerate(images):
                        print(f"   Image {i+1}: {img.master_image.url if img.master_image else 'NO IMAGE FILE'}")
                
                clean_model_images[model_master.model_no] = {
                    'images': [img.master_image.url for img in images if img.master_image] if images else [],
                    'first_image': images[0].master_image.url if images and images[0].master_image else None
                }
                
                # Debug log for specific model
                if model_master.model_no == '1805' or '1805' in model_master.model_no:
                    print(f"🎯 Final structure for clean model {model_master.model_no}: {clean_model_images[model_master.model_no]}")
            
            # Map back to original model numbers
            for original_model, clean_model in clean_model_mapping.items():
                if clean_model in clean_model_images:
                    model_images_map[original_model] = clean_model_images[clean_model]
                    print(f"📸 Mapped {original_model} -> {clean_model} -> {len(clean_model_images[clean_model]['images'])} images")
                else:
                    model_images_map[original_model] = {'images': [], 'first_image': None}
                    print(f"❌ No images found for {original_model} (clean: {clean_model})")
        
        # Process each JigCompleted to attach all information
        for jig_detail in jig_unload:
            # Check if this lot_id already has unload data
            jig_detail.is_unloaded = JigUnload_TrayId.objects.filter(lot_id=jig_detail.lot_id).exists()
            
            # --- Tray Info Fallback Logic ---
            # Try to get tray_type and tray_capacity from lot-level, else fallback to model-level
            tray_type = None
            tray_capacity = None
            
            # 1. Try lot-level (from lot_id_quantities)
            if hasattr(jig_detail, 'lot_id_quantities') and jig_detail.lot_id_quantities:
                for lot_id in jig_detail.lot_id_quantities.keys():
                    lot_data = self.get_lot_specific_data(lot_id, None)
                    if lot_data and lot_data.get('tray_type') and lot_data.get('tray_type') != 'No Tray Type':
                        tray_type = lot_data['tray_type']
                    if lot_data and lot_data.get('tray_capacity') and lot_data.get('tray_capacity') > 0:
                        tray_capacity = lot_data['tray_capacity']
                    if tray_type and tray_capacity:
                        break
            
            # 2. Fallback to model-level (from first model in no_of_model_cases)
            if (not tray_type or not tray_capacity) and hasattr(jig_detail, 'no_of_model_cases') and jig_detail.no_of_model_cases:
                model_no = list(jig_detail.no_of_model_cases)[0]
                model_data = model_data_map.get(model_no)
                if model_data:
                    if not tray_type and model_data.get('tray_type') and model_data.get('tray_type') != 'No Tray Type':
                        tray_type = model_data['tray_type']
                    if not tray_capacity and model_data.get('tray_capacity') and model_data.get('tray_capacity') > 0:
                        tray_capacity = model_data['tray_capacity']
            
            # 3. Set on jig_detail for template
            jig_detail.tray_type = tray_type
            jig_detail.tray_capacity = tray_capacity
            
            # *** NEW: Add helper properties for lot_id_quantities values ***
            if hasattr(jig_detail, 'lot_id_quantities') and jig_detail.lot_id_quantities:
                jig_detail.quantity_values = list(jig_detail.lot_id_quantities.values())
                jig_detail.quantity_values_str = ", ".join(map(str, jig_detail.lot_id_quantities.values()))
                jig_detail.total_quantity = sum(jig_detail.lot_id_quantities.values())
            else:
                jig_detail.quantity_values = []
                jig_detail.quantity_values_str = "0"
                jig_detail.total_quantity = 0
            
            # 🚀 ENHANCED: Dual-table lot_id to model mapping (using full plating_stk_no)
            lot_id_model_map = {}
            if getattr(jig_detail, 'lot_id_quantities', None):
                for lot_id in jig_detail.lot_id_quantities.keys():
                    plating_stk_no = None
                    
                    # Try TotalStockModel → batch_id → ModelMasterCreation first
                    tsm = TotalStockModel.objects.filter(lot_id=lot_id).first()
                    if tsm and tsm.batch_id:
                        mmc = ModelMasterCreation.objects.filter(id=tsm.batch_id.id).first()
                        if mmc:
                            plating_stk_no = getattr(mmc, 'plating_stk_no', None) or (mmc.model_stock_no.model_no if mmc.model_stock_no else None)
                            print(f"🎯 Found {lot_id} in ModelMasterCreation -> {plating_stk_no}")
                    
                    if not plating_stk_no:
                        # 🔥 NEW: Try RecoveryStockModel → batch_id → RecoveryMasterCreation
                        rsm = RecoveryStockModel.objects.filter(lot_id=lot_id).first()
                        if rsm and rsm.batch_id:
                            rmc = RecoveryMasterCreation.objects.filter(id=rsm.batch_id.id).first()
                            if rmc:
                                plating_stk_no = getattr(rmc, 'plating_stk_no', None) or (rmc.model_stock_no.model_no if rmc.model_stock_no else None)
                                print(f"🔄 Found {lot_id} in RecoveryMasterCreation -> {plating_stk_no}")
                    
                    if not plating_stk_no:
                        # Existing fallback logic
                        tray = TrayId.objects.filter(lot_id=lot_id).select_related('batch_id__model_stock_no').first()
                        if tray and tray.batch_id:
                            plating_stk_no = getattr(tray.batch_id, 'plating_stk_no', None) or (tray.batch_id.model_stock_no.model_no if tray.batch_id.model_stock_no else None)
                            print(f"📦 Found {lot_id} in TrayId -> {plating_stk_no}")
                        else:
                            # Final fallback - use model_no from stock models
                            if tsm and hasattr(tsm, 'model_stock_no') and tsm.model_stock_no:
                                plating_stk_no = tsm.model_stock_no.model_no
                                print(f"📊 Found {lot_id} in TotalStockModel (fallback) -> {plating_stk_no}")
                            elif rsm and hasattr(rsm, 'model_stock_no') and rsm.model_stock_no:
                                plating_stk_no = rsm.model_stock_no.model_no
                                print(f"♻️ Found {lot_id} in RecoveryStockModel (fallback) -> {plating_stk_no}")
                    
                    lot_id_model_map[lot_id] = plating_stk_no
            
            print(f"DEBUG: {jig_detail.lot_id} lot_id_model_map = {lot_id_model_map}")
            jig_detail.lot_id_model_map = lot_id_model_map
            
            # Also check for individual model lot_ids if they exist
            if hasattr(jig_detail, 'lot_id_list') and jig_detail.lot_id_list:
                unloaded_model_lot_ids = set(
                    JigUnload_TrayId.objects.filter(
                        lot_id__in=jig_detail.lot_id_list
                    ).values_list('lot_id', flat=True)
                )
                jig_detail.unloaded_model_lot_ids = list(unloaded_model_lot_ids)
            else:
                jig_detail.unloaded_model_lot_ids = []
                
            if jig_detail.no_of_model_cases:
                # Initialize collections for this jig
                jig_versions = {}
                jig_vendors = {}
                jig_locations = {}
                jig_tray_types = {}
                jig_tray_capacities = {}
                jig_plating_stk_nos = {}
                jig_polishing_stk_nos = {}
                jig_plating_colors = {}
                jig_model_colors = {}
                jig_model_images = {}
                
                all_versions = []
                all_vendors = []
                all_locations = []
                all_tray_types = []
                all_tray_capacities = []
                all_plating_stk_nos = []
                all_polishing_stk_nos = []
                
                # 🚀 FIXED: Use no_of_model_cases as primary source for color assignment
                # This ensures we use full model numbers instead of truncated database values
                print(f"🎯 PRIMARY COLOR ASSIGNMENT: Using no_of_model_cases: {jig_detail.no_of_model_cases}")
                
                for model_no in jig_detail.no_of_model_cases:
                    print(f"🔍 PRIMARY: Processing model_no='{model_no}'")
                    
                    # Use global color mapping with full model number
                    model_key = str(model_no)  # Force string conversion
                    assigned_color = global_model_colors.get(model_key, "#cccccc")
                    jig_model_colors[model_key] = assigned_color
                    print(f"🎨 PRIMARY: Assigned color {assigned_color} to full model '{model_key}'")
                    
                    # Get images for this model
                    if model_key in model_images_map:
                        jig_model_images[model_key] = model_images_map[model_key]
                    else:
                        jig_model_images[model_key] = {'images': [], 'first_image': None}
                    
                    # Get additional data from model_data_map if available
                    if model_key in model_data_map:
                        model_data = model_data_map[model_key]
                        
                        # Collect from model_data_map
                        version = model_data['version']
                        if version and version != "No Version":
                            jig_versions[model_key] = version
                            all_versions.append(version)
                        
                        vendor = model_data['vendor']
                        if vendor and vendor != "No Vendor":
                            all_vendors.append(vendor)
                        
                        location = model_data['location']
                        if location and location != "No Location":
                            all_locations.append(location)
                        
                        tray_type = model_data['tray_type']
                        if tray_type and tray_type != "No Tray Type":
                            all_tray_types.append(tray_type)
                        
                        tray_capacity = model_data['tray_capacity']
                        if tray_capacity and tray_capacity > 0:
                            all_tray_capacities.append(tray_capacity)
                        
                        plating_stk_no = model_data['plating_stk_no']
                        if plating_stk_no and plating_stk_no != "No Plating Stock No":
                            all_plating_stk_nos.append(plating_stk_no)
                        
                        polishing_stk_no = model_data['polishing_stk_no']
                        if polishing_stk_no and polishing_stk_no != "No Polishing Stock No":
                            all_polishing_stk_nos.append(polishing_stk_no)
                
                all_plating_colors = []
                
                # Replace the lot_id_quantities processing section with this version that keeps ALL values including duplicates:

                # 🚀 ENHANCED: Use lot_id_quantities for comprehensive lot-specific data collection (INCLUDING DUPLICATES)
                if hasattr(jig_detail, 'lot_id_quantities') and jig_detail.lot_id_quantities:
                    print(f"🔥 Processing lot_id_quantities for ALL individual values: {jig_detail.lot_id_quantities}")
                    
                    for lot_id, quantity in jig_detail.lot_id_quantities.items():
                        # Get model_no for this specific lot_id
                        model_no = jig_detail.lot_id_model_map.get(lot_id)
                        if not model_no:
                            print(f"❌ No model_no found for lot_id: {lot_id}")
                            continue
                            
                        print(f"🔄 Processing lot_id: {lot_id} -> model_no: {model_no}")
                        
                        # 🐛 DEBUG: Check for model number truncation
                        print(f"🔍 DEBUG: model_no type: {type(model_no)}, value: '{model_no}', length: {len(str(model_no))}")
                        
                        # Use global color mapping for model_no with explicit string conversion
                        model_key = str(model_no)  # Force string conversion
                        assigned_color = global_model_colors.get(model_key, "#cccccc")
                        # 🚨 SKIP: Don't assign color here - potentially truncated model_no
                        # jig_model_colors[model_key] = assigned_color  # COMMENTED OUT
                        print(f"🚨 SKIPPING color assignment for potentially truncated '{model_key}' -> {assigned_color}")
                        print(f"🔍 DEBUG: jig_model_colors kept unchanged: {jig_model_colors}")
                        
                        # Get images for this model
                        if model_no in model_images_map:
                            jig_model_images[model_no] = model_images_map[model_no]
                        else:
                            jig_model_images[model_no] = {
                                'images': [], 'first_image': None}
                        
                        # 🎯 PRIMARY: Get comprehensive lot-specific data
                        lot_specific_data = self.get_lot_specific_data(lot_id, model_no)
                        
                        if lot_specific_data:
                            # Store lot-specific data in dictionaries
                            # Complete field mapping per lot_id
                            jig_versions[lot_id] = lot_specific_data['version']
                            jig_vendors[lot_id] = lot_specific_data['vendor']
                            jig_locations[lot_id] = lot_specific_data['location']
                            jig_tray_types[lot_id] = lot_specific_data['tray_type']
                            jig_tray_capacities[lot_id] = lot_specific_data['tray_capacity']
                            jig_plating_stk_nos[lot_id] = lot_specific_data['plating_stk_no']
                            jig_polishing_stk_nos[lot_id] = lot_specific_data['polishing_stk_no']
                            jig_plating_colors[lot_id] = lot_specific_data['plating_color']
                            # ✅ COLLECT ALL VALUES (INCLUDING DUPLICATES)
                            version = lot_specific_data['version']
                            if version and version != "No Version":
                                all_versions.append(version)  # NO duplicate check - add every value
                                print(f"✅ Added version: '{version}' from lot_id: {lot_id}")
                            
                            vendor = lot_specific_data['vendor']
                            if vendor and vendor != "No Vendor":
                                all_vendors.append(vendor)  # NO duplicate check
                            
                            location = lot_specific_data['location']
                            if location and location != "No Location":
                                all_locations.append(location)  # NO duplicate check
                            
                            tray_type = lot_specific_data['tray_type']
                            if tray_type and tray_type != "No Tray Type":
                                all_tray_types.append(tray_type)  # NO duplicate check
                            
                            tray_capacity = lot_specific_data['tray_capacity']
                            if tray_capacity and tray_capacity > 0:
                                all_tray_capacities.append(tray_capacity)  # NO duplicate check
                            
                            # ✅ CRITICAL: Collect ALL plating_stk_no values (including duplicates)
                            plating_stk_no = lot_specific_data['plating_stk_no']
                            if plating_stk_no and plating_stk_no != "No Plating Stock No":
                                all_plating_stk_nos.append(plating_stk_no)  # NO duplicate check - add every value
                                print(f"✅ Added plating_stk_no: '{plating_stk_no}' from lot_id: {lot_id}")
                            
                            # ✅ CRITICAL: Collect ALL polishing_stk_no values (including duplicates)
                            polishing_stk_no = lot_specific_data['polishing_stk_no']
                            if polishing_stk_no and polishing_stk_no != "No Polishing Stock No":
                                all_polishing_stk_nos.append(polishing_stk_no)  # NO duplicate check - add every value
                                print(f"✅ Added polishing_stk_no: '{polishing_stk_no}' from lot_id: {lot_id}")
                            
                            plating_color = lot_specific_data['plating_color']
                            if plating_color and plating_color != "N/A":
                                all_plating_colors.append(plating_color)  # NO duplicate check
                            
                            print(f"✅ Processed lot {lot_id}: plating={plating_stk_no}, polishing={polishing_stk_no}, version={version}")
                            
                        else:
                            print(f"⚠️ No lot-specific data from get_lot_specific_data for lot_id: {lot_id}, trying fallback...")
                            
                            # ✅ ENHANCED FALLBACK: Get data directly from stock models
                            stock_model, is_recovery, batch_model_class = self.get_stock_model_data(lot_id)
                            if stock_model:
                                print(f"📋 Using stock model fallback for lot_id: {lot_id}")
                                
                                # Extract plating_stk_no directly from stock model
                                fallback_plating = getattr(stock_model, 'plating_stk_no', None)
                                if fallback_plating:
                                    all_plating_stk_nos.append(fallback_plating)  # NO duplicate check
                                    jig_plating_stk_nos[lot_id] = fallback_plating
                                    print(f"📋 Added fallback plating_stk_no: '{fallback_plating}'")
                                
                                # Extract polishing_stk_no (try multiple field names)
                                fallback_polishing = (getattr(stock_model, 'polish_stk_no', None) or 
                                                    getattr(stock_model, 'polishing_stk_no', None))
                                if fallback_polishing:
                                    all_polishing_stk_nos.append(fallback_polishing)  # NO duplicate check
                                    jig_polishing_stk_nos[lot_id] = fallback_polishing
                                    print(f"📋 Added fallback polishing_stk_no: '{fallback_polishing}'")
                                
                                # Extract version from stock model
                                fallback_version = None
                                if hasattr(stock_model, 'version') and stock_model.version:
                                    if hasattr(stock_model.version, 'version_internal'):
                                        fallback_version = stock_model.version.version_internal
                                    elif hasattr(stock_model.version, 'version_name'):
                                        fallback_version = stock_model.version.version_name
                                    else:
                                        fallback_version = str(stock_model.version)
                                
                                if fallback_version:
                                    all_versions.append(fallback_version)  # NO duplicate check
                                    jig_versions[lot_id] = fallback_version
                                    print(f"📋 Added fallback version: '{fallback_version}'")
                            else:
                                print(f"❌ No fallback data available for lot_id: {lot_id}")
                
                else:
                    # Fallback to model-based logic if no lot_id_quantities
                    print(f"⚠️ No lot_id_quantities found, falling back to model-based logic for {jig_detail.lot_id}")
                    for model_no in jig_detail.no_of_model_cases:
                        # 🐛 DEBUG: Check for model number truncation in fallback
                        print(f"🔍 FALLBACK DEBUG: model_no type: {type(model_no)}, value: '{model_no}', length: {len(str(model_no))}")
                        
                        # Use global color mapping for model_no with explicit string conversion
                        model_key = str(model_no)  # Force string conversion
                        assigned_color = global_model_colors.get(model_key, "#cccccc")
                        # 🚨 SKIP: Color already assigned in primary section above
                        # jig_model_colors[model_key] = assigned_color  # COMMENTED OUT
                        print(f"🚨 FALLBACK SKIPPED: Color already assigned for '{model_key}' -> {assigned_color}")
                        print(f"🔍 FALLBACK DEBUG: jig_model_colors unchanged: {jig_model_colors}")
                        
                        # Get images for this model
                        if model_no in model_images_map:
                            jig_model_images[model_no] = model_images_map[model_no]
                            print(f"📸 Found images for model {model_no}: {len(model_images_map[model_no]['images'])} images")
                        else:
                            jig_model_images[model_no] = {'images': [], 'first_image': None}
                            print(f"❌ No images found for model {model_no}")
                            
                        # Special debug for 1805 models
                        if '1805' in str(model_no):
                            print(f"🎯 1805 MODEL DEBUG:")
                            print(f"   model_no: {model_no}")
                            print(f"   in model_images_map: {model_no in model_images_map}")
                            if model_no in model_images_map:
                                print(f"   images count: {len(model_images_map[model_no]['images'])}")
                                print(f"   first_image: {model_images_map[model_no]['first_image']}")
                            print(f"   assigned images: {jig_model_images.get(model_no, 'NOT FOUND')}")

                    # Attach all collected data to jig_detail object
                    jig_detail.model_colors = jig_model_colors
                    jig_detail.model_images = jig_model_images
                    
                    # Debug log for 1805 models
                    has_1805 = any('1805' in str(k) for k in jig_model_images.keys())
                    if has_1805:
                        print(f"🎯 JIG {jig_detail.lot_id} - Final model_images with 1805:")
                        for model_key, image_data in jig_model_images.items():
                            if '1805' in str(model_key):
                                print(f"   {model_key}: {len(image_data['images'])} images, first: {image_data['first_image']}")
                    
                    # Basic debug info
                    print(f"📊 JIG {jig_detail.jig_id} - Total models: {len(jig_model_images)}, Colors: {len(jig_model_colors)}")

                    # 🎨 ENHANCED DEBUG: Print color assignments for this jig
                    print(f"🎨 FINAL COLOR ASSIGNMENTS for {jig_detail.lot_id}:")
                    print(f"   🎯 model_colors dict: {jig_detail.model_colors}")
                    print(f"   📋 no_of_model_cases list: {jig_detail.no_of_model_cases}")
                    for model_no in (jig_detail.no_of_model_cases or []):
                        color = jig_detail.model_colors.get(model_no, 'NOT_FOUND')
                        print(f"   🔍 Model {model_no} -> Color {color}")
                        
                        # 🐛 EXTRA DEBUG: Check model number type and value
                        print(f"   🔍 EXTRA DEBUG: model_no type: {type(model_no)}, repr: {repr(model_no)}")
                        
                        if model_no in model_data_map:
                            model_data = model_data_map[model_no]
                            
                            # Collect from model_data_map (keep duplicates here too for consistency)
                            version = model_data['version']
                            if version and version != "No Version":
                                jig_versions[model_no] = version
                                all_versions.append(version)  # NO duplicate check
                            
                            vendor = model_data['vendor']
                            if vendor and vendor != "No Vendor":
                                jig_vendors[model_no] = vendor
                                all_vendors.append(vendor)  # NO duplicate check
                            
                            location = model_data['location']
                            if location and location != "No Location":
                                jig_locations[model_no] = location
                                all_locations.append(location)  # NO duplicate check
                            
                            tray_type = model_data['tray_type']
                            if tray_type and tray_type != "No Tray Type":
                                jig_tray_types[model_no] = tray_type
                                all_tray_types.append(tray_type)  # NO duplicate check
                            
                            tray_capacity = model_data['tray_capacity']
                            if tray_capacity and tray_capacity > 0:
                                jig_tray_capacities[model_no] = tray_capacity
                                all_tray_capacities.append(tray_capacity)  # NO duplicate check
                            
                            # ✅ CRITICAL: Collect ALL values including duplicates
                            plating_stk_no = model_data['plating_stk_no']
                            if plating_stk_no and plating_stk_no != "No Plating Stock No":
                                jig_plating_stk_nos[model_no] = plating_stk_no
                                all_plating_stk_nos.append(plating_stk_no)  # NO duplicate check
                            
                            polishing_stk_no = model_data['polishing_stk_no']
                            if polishing_stk_no and polishing_stk_no != "No Polishing Stock No":
                                jig_polishing_stk_nos[model_no] = polishing_stk_no
                                all_polishing_stk_nos.append(polishing_stk_no)  # NO duplicate check
                              
                            plating_color = model_data['plating_color']
                            if plating_color and plating_color != "N/A":
                                jig_plating_colors[model_no] = plating_color
                                all_plating_colors.append(plating_color)  # NO duplicate check
                
                # Attach all collected data to jig_detail object
                jig_detail.model_versions = jig_versions
                jig_detail.model_vendors = jig_vendors
                jig_detail.model_locations = jig_locations
                jig_detail.model_tray_types = jig_tray_types
                jig_detail.model_tray_capacities = jig_tray_capacities
                jig_detail.model_plating_stk_nos = jig_plating_stk_nos
                jig_detail.model_polishing_stk_nos = jig_polishing_stk_nos
                jig_detail.model_plating_colors = jig_plating_colors
                
                jig_detail.model_colors = jig_model_colors
                jig_detail.model_images = jig_model_images
                
                # *** NEW: Prepare colored quantity data for multi-model display ***
                if hasattr(jig_detail, 'lot_id_quantities') and jig_detail.lot_id_quantities:
                    jig_detail.colored_quantities = []
                    if hasattr(jig_detail, 'lot_id_model_map') and jig_detail.lot_id_model_map:
                        for lot_id, quantity in jig_detail.lot_id_quantities.items():
                            model_no = jig_detail.lot_id_model_map.get(lot_id, 'Unknown')
                            color = jig_detail.model_colors.get(model_no, '#6c757d')
                            jig_detail.colored_quantities.append({
                                'quantity': quantity,
                                'model': model_no,
                                'color': color,
                                'tooltip': f'Model: {model_no}, Lot: {lot_id}, Qty: {quantity}'
                            })
                    else:
                        # Fallback: if no model mapping, use default color
                        for lot_id, quantity in jig_detail.lot_id_quantities.items():
                            jig_detail.colored_quantities.append({
                                'quantity': quantity,
                                'model': 'Unknown',
                                'color': '#6c757d',
                                'tooltip': f'Lot: {lot_id}, Qty: {quantity}'
                            })
                else:
                    jig_detail.colored_quantities = []
                
                # 🎨 DEBUG: Print color assignments for this jig
                print(f"🎨 FINAL COLOR ASSIGNMENTS for {jig_detail.lot_id}:")
                print(f"   🎯 model_colors: {jig_detail.model_colors}")
                print(f"   📋 no_of_model_cases: {jig_detail.no_of_model_cases}")
                for model_no in (jig_detail.no_of_model_cases or []):
                    color = jig_detail.model_colors.get(model_no, 'NOT_FOUND')
                    print(f"   🔍 Model {model_no} -> Color {color}")
                
                # ✅ KEEP ALL VALUES INCLUDING DUPLICATES (no set() function used)
                jig_detail.all_versions = [v for v in all_versions if v and v != "No Version"]
                jig_detail.all_vendors = [v for v in all_vendors if v and v != "No Vendor"]
                jig_detail.all_locations = [v for v in all_locations if v and v != "No Location"]
                jig_detail.all_plating_stk_nos = [v for v in all_plating_stk_nos if v and v != "No Plating Stock No"]
                jig_detail.all_polishing_stk_nos = [v for v in all_polishing_stk_nos if v and v != "No Polishing Stock No"]
                jig_detail.all_plating_colors = [v for v in all_plating_colors if v and v != "N/A"]
                
                # ✅ ALSO PROVIDE UNIQUE VERSIONS (for backward compatibility)
                jig_detail.unique_versions = sorted(list(set(jig_detail.all_versions)))
                jig_detail.unique_vendors = sorted(list(set(jig_detail.all_vendors)))
                jig_detail.unique_locations = sorted(list(set(jig_detail.all_locations)))
                jig_detail.unique_plating_stk_nos = sorted(list(set(jig_detail.all_plating_stk_nos)))
                jig_detail.unique_polishing_stk_nos = sorted(list(set(jig_detail.all_polishing_stk_nos)))
                jig_detail.unique_plating_colors = sorted(list(set(jig_detail.all_plating_colors)))
                
                # ✅ ENHANCED DEBUG: Print both all and unique values
                print(f"🎊 FINAL RESULTS for {jig_detail.lot_id}:")
                print(f"   📋 ALL plating_stk_nos ({len(jig_detail.all_plating_stk_nos)}): {jig_detail.all_plating_stk_nos}")
                print(f"   📋 ALL polishing_stk_nos ({len(jig_detail.all_polishing_stk_nos)}): {jig_detail.all_polishing_stk_nos}")
                print(f"   📋 ALL versions ({len(jig_detail.all_versions)}): {jig_detail.all_versions}")
                print(f"   🎯 UNIQUE plating_stk_nos ({len(jig_detail.unique_plating_stk_nos)}): {jig_detail.unique_plating_stk_nos}")
                print(f"   🎯 UNIQUE polishing_stk_nos ({len(jig_detail.unique_polishing_stk_nos)}): {jig_detail.unique_polishing_stk_nos}")
                print(f"   🎯 UNIQUE versions ({len(jig_detail.unique_versions)}): {jig_detail.unique_versions}")
                
                # Set default values for remaining fields
                jig_detail.jig_type = getattr(jig_detail, 'jig_type', "N/A")
                jig_detail.jig_capacity = getattr(jig_detail, 'jig_capacity', 0)
                jig_detail.plating_color = jig_detail.plating_color or "N/A"
                
                # Calculate no_of_trays based on total_cases_loaded and tray_capacity
                valid_capacities = [cap for cap in all_tray_capacities if cap and cap > 0]
                if valid_capacities and hasattr(jig_detail, 'total_cases_loaded') and jig_detail.total_cases_loaded:
                    primary_tray_capacity = valid_capacities[0]
                    jig_detail.calculated_no_of_trays = math.ceil(jig_detail.total_cases_loaded / primary_tray_capacity)
                    jig_detail.primary_tray_capacity = primary_tray_capacity
                else:
                    jig_detail.calculated_no_of_trays = 0
                    jig_detail.primary_tray_capacity = 0
            else:
                jig_detail.model_versions = {}
                jig_detail.model_vendors = {}
                jig_detail.model_locations = {}
                jig_detail.model_tray_types = {}
                jig_detail.model_tray_capacities = {}
                jig_detail.model_plating_stk_nos = {}
                jig_detail.model_polishing_stk_nos = {}
                jig_detail.model_plating_colors = {}
                jig_detail.model_colors = {}
                jig_detail.model_images = {}
                jig_detail.unique_versions = []
                jig_detail.unique_vendors = []
                jig_detail.unique_locations = []
                jig_detail.unique_tray_types = []
                jig_detail.unique_tray_capacities = []
                jig_detail.unique_plating_stk_nos = []
                jig_detail.unique_polishing_stk_nos = []
                jig_detail.calculated_no_of_trays = 0
                jig_detail.primary_tray_capacity = 0
                jig_detail.plating_color = "N/A"
                
        # This converts the QuerySet to a list, so it MUST be last
        jig_unload = self.check_draft_status_for_jigs(jig_unload)
        
        # Ensure required fields are set for template compatibility
        for jig_detail in jig_unload:
            # Set jig_qr_id for template (use jig_id or fallback to lot_id)
            jig_detail.jig_qr_id = getattr(jig_detail, 'jig_id', None) or jig_detail.lot_id
            
            # Parse draft_data if needed
            draft_data = {}
            if hasattr(jig_detail, 'draft_data') and jig_detail.draft_data:
                if isinstance(jig_detail.draft_data, str):
                    try:
                        import json
                        draft_data = json.loads(jig_detail.draft_data)
                    except:
                        draft_data = {}
                elif isinstance(jig_detail.draft_data, dict):
                    draft_data = jig_detail.draft_data
            
            # Set plating_color from draft_data or fetch from models
            plating_color = draft_data.get('plating_color')
            if not plating_color:
                # Try to get plating color from stock models
                stock_model, is_recovery, batch_model_class = self.get_stock_model_data(jig_detail.lot_id)
                if stock_model and hasattr(stock_model, 'plating_color') and stock_model.plating_color:
                    plating_color = stock_model.plating_color.plating_color
            jig_detail.plating_color = plating_color or 'N/A'
            
            # Set tray info from draft_data or fetch from models
            tray_type = draft_data.get('tray_type')
            tray_capacity = draft_data.get('tray_capacity')
            if not tray_type or not tray_capacity:
                # Try to get tray info from model data
                lot_data = self.get_lot_specific_data(jig_detail.lot_id, None)
                if lot_data:
                    if not tray_type:
                        tray_type = lot_data.get('tray_type')
                    if not tray_capacity:
                        tray_capacity = lot_data.get('tray_capacity')
            jig_detail.tray_type = tray_type
            jig_detail.tray_capacity = tray_capacity
            
            # Set lot_id_quantities from draft_data or create from stock data
            lot_id_quantities = draft_data.get('lot_id_quantities', {})
            if not lot_id_quantities:
                # Use delink_tray_qty as primary source for processed quantity
                delink_qty = getattr(jig_detail, 'delink_tray_qty', 0)
                if delink_qty and delink_qty > 0:
                    lot_id_quantities = {jig_detail.lot_id: delink_qty}
                else:
                    # Try to get quantity from stock models as fallback
                    stock_model, is_recovery, batch_model_class = self.get_stock_model_data(jig_detail.lot_id)
                    if stock_model and hasattr(stock_model, 'total_stock'):
                        lot_id_quantities = {jig_detail.lot_id: stock_model.total_stock}
                    else:
                        lot_id_quantities = {jig_detail.lot_id: getattr(jig_detail, 'updated_lot_qty', 0)}
            jig_detail.lot_id_quantities = lot_id_quantities
            
            # Ensure no_of_model_cases is a list
            model_cases = draft_data.get('no_of_model_cases', getattr(jig_detail, 'no_of_model_cases', None))
            if model_cases is None:
                # Try to get model info from stock models
                stock_model, is_recovery, batch_model_class = self.get_stock_model_data(jig_detail.lot_id)
                if stock_model and hasattr(stock_model, 'model_stock_no') and stock_model.model_stock_no:
                    model_cases = [stock_model.model_stock_no.model_no]
                else:
                    model_cases = []
            if isinstance(model_cases, str):
                try:
                    jig_detail.no_of_model_cases = json.loads(model_cases)
                except:
                    jig_detail.no_of_model_cases = [model_cases] if model_cases else []
            elif isinstance(model_cases, list):
                jig_detail.no_of_model_cases = model_cases
            else:
                jig_detail.no_of_model_cases = []
            
            # Ensure other list fields are lists
            for field in ['all_versions', 'all_vendors', 'all_locations', 'all_plating_stk_nos', 'all_polishing_stk_nos', 'all_plating_colors']:
                if not hasattr(jig_detail, field) or getattr(jig_detail, field) is None:
                    setattr(jig_detail, field, [])
            
            # If we don't have polishing stock numbers, try to fetch them
            if not jig_detail.all_polishing_stk_nos and jig_detail.no_of_model_cases:
                polishing_stk_nos = []
                for model_no in jig_detail.no_of_model_cases:
                    lot_data = self.get_lot_specific_data(jig_detail.lot_id, model_no)
                    if lot_data and lot_data.get('polishing_stk_no') and lot_data['polishing_stk_no'] != 'No Polishing Stock No':
                        polishing_stk_nos.append(lot_data['polishing_stk_no'])
                jig_detail.all_polishing_stk_nos = polishing_stk_nos
        
        # Add pagination with consistent logic as Inprocess Inspection
        page_number = self.request.GET.get('page', 1)
        paginator = Paginator(jig_unload, 10)  # 10 items per page like Inprocess Inspection
        page_obj = paginator.get_page(page_number)
        
        print(f"\n📄 Jig Unloading Main - Pagination: Page {page_number}, Total items: {len(jig_unload)}")
        print(f"📄 Current page items: {len(page_obj.object_list)}")
        print(f"📄 Total pages: {paginator.num_pages}")
        
        context['jig_unload'] = page_obj  # For table data
        context['jig_details'] = page_obj  # For pagination controls (template uses this name)
        context['bath_numbers'] = bath_numbers
        
        return context
    
    def filter_fully_unloaded_jigs(self, queryset):
        """Smart filter to remove jigs where ALL lot_ids are unloaded"""
        
        def parse_combined_lot_id(combined_id):
            try:
                return combined_id.rsplit('-', 1) if combined_id and '-' in combined_id else (None, None)
            except:
                return None, None

        # Get all unload records once
        unload_records = JigUnloadAfterTable.objects.filter(
            combine_lot_ids__isnull=False
        ).exclude(combine_lot_ids__exact=[]).values('combine_lot_ids')
        
        # Build unload mapping: jig_lot_id -> set of unloaded lot_ids
        unload_map = {}
        for record in unload_records:
            if record['combine_lot_ids']:
                for combined_id in record['combine_lot_ids']:
                    parts = parse_combined_lot_id(combined_id)
                    if len(parts) == 2 and parts[0] and parts[1]:
                        jig_id, lot_id = parts[0], parts[1]
                        unload_map.setdefault(jig_id, set()).add(lot_id)
        
        # Also check direct unloads
        direct_unloads = set(JigUnload_TrayId.objects.values_list('lot_id', flat=True))
        
        # Filter jigs
        filtered_jigs = []
        for jig in queryset:
            if not (hasattr(jig, 'lot_id_quantities') and jig.lot_id_quantities):
                filtered_jigs.append(jig)
                continue
                
            jig_lot_ids = set(jig.lot_id_quantities.keys())
            unloaded_lot_ids = unload_map.get(jig.lot_id, set()) | (jig_lot_ids & direct_unloads)
            
            # Keep jig if ANY lot_id is NOT unloaded
            if not jig_lot_ids.issubset(unloaded_lot_ids):
                filtered_jigs.append(jig)
            else:
                print(f"🚫 Hiding fully unloaded jig: {jig.lot_id}")
        
        return filtered_jigs

    def check_draft_status_for_jigs(self, jig_queryset):
        """Check if any jig has draft records based on main_lot_id in new_lot_ids or lot_id_quantities"""
        
        # Get all main_lot_ids from saved drafts
        draft_lot_ids = set(JigUnloadDraft.objects.values_list('main_lot_id', flat=True))
        
        print(f"🔍 Found {len(draft_lot_ids)} draft lot_ids: {draft_lot_ids}")
        
        # Convert QuerySet to list explicitly
        jig_list = list(jig_queryset)
        
        # Check each jig's lot_ids against draft lot_ids
        for jig_detail in jig_list:
            has_draft = False
            
            print(f"🔍 Checking jig {jig_detail.lot_id}:")
            
            # Check new_lot_ids array field
            if hasattr(jig_detail, 'new_lot_ids') and jig_detail.new_lot_ids:
                print(f"   - new_lot_ids: {jig_detail.new_lot_ids}")
                for lot_id in jig_detail.new_lot_ids:
                    if lot_id in draft_lot_ids:
                        has_draft = True
                        print(f"✅ DRAFT MATCH in new_lot_ids: {lot_id}")
                        break
            
            # Check lot_id_quantities keys
            if not has_draft and hasattr(jig_detail, 'lot_id_quantities') and jig_detail.lot_id_quantities:
                print(f"   - lot_id_quantities keys: {list(jig_detail.lot_id_quantities.keys())}")
                for lot_id in jig_detail.lot_id_quantities.keys():
                    if lot_id in draft_lot_ids:
                        has_draft = True
                        print(f"✅ DRAFT MATCH in lot_id_quantities: {lot_id}")
                        break
            
            # Check main lot_id as fallback
            if not has_draft and hasattr(jig_detail, 'lot_id') and jig_detail.lot_id:
                print(f"   - main lot_id: {jig_detail.lot_id}")
                if jig_detail.lot_id in draft_lot_ids:
                    has_draft = True
                    print(f"✅ DRAFT MATCH in main lot_id: {jig_detail.lot_id}")
            
            # Set BOTH flags to ensure template works
            jig_detail.has_unload_draft = has_draft
            jig_detail.jig_unload_draft = has_draft
            
            if has_draft:
                print(f"🎯 JIG {jig_detail.lot_id} MARKED AS DRAFT")
            else:
                print(f"❌ JIG {jig_detail.lot_id} NO DRAFT")
        
        print(f"🔍 Draft check complete for {len(jig_list)} jigs")
        return jig_list  # Return the list, not queryset


@csrf_exempt
@require_http_methods(["POST"])
def get_model_details(request):
    """
    Fetch model details including tray type and capacity for a given model number
    """
    try:
        data = json.loads(request.body)
        model_number = data.get('model_number')
        lot_id = data.get('lot_id')
        
        if not model_number:
            return JsonResponse({
                'success': False,
                'error': 'Model number is required'
            })
        
        # Fetch model details from ModelMaster and related tables
        # FIXED: Search by plating_stk_no since frontend sends full plating stock number
        model_master_creation = ModelMasterCreation.objects.filter(
            plating_stk_no=model_number
        ).select_related(
            'model_stock_no__tray_type',
            'model_stock_no'
        ).first()
        
        # Also try to get from TotalStockModel to get plating_color using lot_id
        total_stock_model = None
        if lot_id:
            total_stock_model = TotalStockModel.objects.filter(
                lot_id=lot_id
            ).select_related(
                'plating_color'
            ).first()
        
        # Fallback to plating_stk_no search if lot_id not provided or not found
        if not total_stock_model:
            # FIXED: Search by ModelMasterCreation instance, not batch_id string
            if model_master_creation:
                total_stock_model = TotalStockModel.objects.filter(
                    batch_id=model_master_creation
                ).select_related(
                    'plating_color'
                ).first()
        
        if not model_master_creation:
            return JsonResponse({
                'success': False,
                'error': f'Model {model_number} not found'
            })
        
        # Get tray details with dynamic capacity override
        tray_type = model_master_creation.model_stock_no.tray_type.tray_type if model_master_creation.model_stock_no.tray_type else "Normal"
        
        # Use dynamic tray capacity (checks InprocessInspectionTrayCapacity for overrides)
        view_instance = Jig_Unloading_MainTable()
        tray_capacity = view_instance.get_dynamic_tray_capacity(tray_type)
        
        # Get plating color information
        plating_color = None
        plating_color_name = None
        tray_id_color = "#dc3545"  # Default red
        tray_id_prefix = "NR" if tray_type == "Normal" else "JR"
        
        print(f"[DEBUG] Zone 1 get_model_details - model_number: {model_number}, lot_id: {lot_id}")
        print(f"[DEBUG] Zone 1 - model_master_creation found: {model_master_creation is not None}")
        
        # ENHANCED: Get plating color from ModelMasterCreation first (most reliable)
        if model_master_creation and model_master_creation.plating_color:
            plating_color_name = model_master_creation.plating_color
            print(f"[DEBUG] Zone 1 - Found plating_color in ModelMasterCreation: {plating_color_name}")
        elif total_stock_model and total_stock_model.plating_color:
            plating_color = total_stock_model.plating_color
            plating_color_name = plating_color.plating_color
            print(f"[DEBUG] Zone 1 - Found plating_color in TotalStockModel: {plating_color_name}")
        else:
            # Try to get plating color from JigCompleted if not in other sources
            print(f"[DEBUG] Zone 1 - No plating_color in ModelMasterCreation/TotalStockModel, checking JigCompleted...")
            jig_detail = JigCompleted.objects.filter(lot_id=lot_id).first()
            if jig_detail and jig_detail.plating_color:
                plating_color_name = jig_detail.plating_color
                print(f"[DEBUG] Zone 1 - Found plating_color in JigCompleted: {plating_color_name}")
            else:
                print(f"[DEBUG] Zone 1 - No plating_color found in any source")
        
        # Determine tray ID color based on plating color
        if plating_color_name == "IPS":
            tray_id_color = "#dc3545"  # Red for IPS
            tray_id_prefix = "NR" if tray_type == "Normal" else "JR"
        elif plating_color_name and any(bi in plating_color_name.lower() for bi in ["bi color", "bicolor", "bi-color"]):
            tray_id_color = "#90EE90"  # Light green for Bi Color
            tray_id_prefix = "NL" if tray_type == "Normal" else "JL"
        else:
            tray_id_color = "#006400"  # Dark green for other colors
            tray_id_prefix = "ND" if tray_type == "Normal" else "JD"
        
        # Check for multiple models in the same lot/jig
        multiple_models = False
        if lot_id:
            # Check if there are multiple different models in the same JIG lot
            jig_detail = JigCompleted.objects.filter(jig_lot_id=lot_id).first()
            if jig_detail and jig_detail.no_of_model_cases:
                # Count unique models in the array
                multiple_models = len(set(jig_detail.no_of_model_cases)) > 1
            else:
                multiple_models = False
        
        model_details = {
            'model_number': model_number,
            'lot_id': lot_id,
            'tray_type': tray_type,
            'tray_capacity': tray_capacity,
            'plating_color': plating_color_name,
            'plating_stk_no': getattr(model_master_creation, 'plating_stk_no', None) or model_number,
            'multiple_models': multiple_models,
            'tray_id_color': tray_id_color,
            'tray_id_prefix': tray_id_prefix,
            'model_name': model_master_creation.model_stock_no.model_name if hasattr(model_master_creation.model_stock_no, 'model_name') else model_number
        }
        
        print(f"[DEBUG] Zone 1 - Final model details: {model_details}")
        
        return JsonResponse({
            'success': True,
            **model_details  # Spread the model_details directly into the response
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON data'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        })

@method_decorator(csrf_exempt, name='dispatch')
class UnLoadSaveHoldUnholdReasonAPIView(APIView):
    """
    POST with:
    {
        "remark": "Reason text",
        "action": "hold"  # or "unhold"
        "lot_id": "LOT123"
    }
    """
    def post(self, request):
        try:
            data = request.data if hasattr(request, 'data') else json.loads(request.body.decode('utf-8'))
            jig_lot_id = data.get('jig_lot_id')
            remark = data.get('remark', '').strip()
            action = data.get('action', '').strip().lower()

            if not jig_lot_id or not remark or action not in ['hold', 'unhold']:
                return JsonResponse({'success': False, 'error': 'Missing or invalid parameters.'}, status=400)

            # Only use JigCompleted table
            obj = JigCompleted.objects.filter(jig_lot_id=jig_lot_id).first()
            if not obj:
                return JsonResponse({'success': False, 'error': 'JigCompleted record not found.'}, status=404)

            if action == 'hold':
                obj.unload_holding_reason = remark
                obj.unload_hold_lot = True
                obj.unload_release_reason = ''
                obj.unload_release_lot = False
            elif action == 'unhold':
                obj.unload_release_reason = remark
                obj.unload_hold_lot = False
                obj.unload_release_lot = True

            obj.save(update_fields=['unload_holding_reason', 'unload_release_reason', 'unload_hold_lot', 'unload_release_lot'])
            return JsonResponse({'success': True, 'message': 'Reason saved.'})

        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)

@method_decorator(csrf_exempt, name='dispatch')
class UnloadJigPickRemarkAPIView(APIView):
    def post(self, request):
        try:
            data = request.data if hasattr(request, 'data') else json.loads(request.body.decode('utf-8'))
            jig_lot_id = data.get('jig_lot_id')
            remark = data.get('unloading_remarks', '').strip()
            if not jig_lot_id:
                return JsonResponse({'success': False, 'error': 'JIG Lot ID not found.'}, status=400)
            jig_detail = JigCompleted.objects.filter(jig_lot_id=jig_lot_id).first()
            if not jig_detail:
                return JsonResponse({'success': False, 'error': 'Lot not found in JigCompleted'}, status=404)
            jig_detail.unloading_remarks = remark
            jig_detail.save(update_fields=['unloading_remarks'])
            return JsonResponse({'success': True, 'message': 'Remark saved'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)

def populate_jig_unload_fields(jig_unload_instance, lot_ids, jig_lot_id=None):
    """
    Smart helper to populate JigUnloadAfterTable fields from TotalStockModel + JigCompleted
    """
    if not lot_ids:
        print("[SMART POPULATE] ⚠️ No lot_ids provided")
        return
    
    print(f"[SMART POPULATE] 🎯 Starting populate for lot_ids: {lot_ids}")
    
    # Get first available lot to extract data
    for raw_lot_id in lot_ids:
        try:
            # Clean lot_id - extract actual lot ID from JLOT format if needed
            lot_id = raw_lot_id
            if 'JLOT-' in raw_lot_id and '-' in raw_lot_id:
                parts = raw_lot_id.split('-')
                if len(parts) >= 3:  # JLOT-ABC123-LID456...
                    lot_id = '-'.join(parts[2:])  # Get LID... part
                elif len(parts) == 2:  # JLOT-ABC123
                    lot_id = raw_lot_id  # Keep original
                print(f"[SMART POPULATE] 🔄 Extracted lot_id: '{lot_id}' from '{raw_lot_id}'")
            
            print(f"[SMART POPULATE] 🔍 Processing lot_id: {lot_id}")
            
            # Get TotalStockModel data
            total_stock = TotalStockModel.objects.select_related(
                'batch_id', 'version', 'model_stock_no', 'plating_color',
                'model_stock_no__polish_finish', 'model_stock_no__tray_type'
            ).prefetch_related('location').filter(lot_id=lot_id).first()
            
            if not total_stock:
                print(f"[SMART POPULATE] ❌ No TotalStockModel found for lot_id: {lot_id}")
                continue
                
            print(f"[SMART POPULATE] ✅ Found TotalStockModel for lot_id: {lot_id}")
            
            # Get JigCompleted data
            jig_detail = None
            if jig_lot_id:
                # Try to find JigCompleted using jig_lot_id field
                jig_detail = JigCompleted.objects.filter(jig_lot_id=jig_lot_id).first()
                if not jig_detail:
                    # Fallback: try with lot_id_quantities
                    jig_detail = JigCompleted.objects.filter(
                        lot_id_quantities__has_key=lot_id
                    ).first()
            
            if not total_stock.batch_id:
                print(f"[SMART POPULATE] ❌ No batch_id in TotalStockModel for lot_id: {lot_id}")
                continue
                
            batch = total_stock.batch_id
            
            # Populate fields from TotalStockModel/ModelMasterCreation
            jig_unload_instance.version = total_stock.version
            
            # 🔧 ENHANCED PLATING COLOR LOGIC: Try TotalStockModel first, fallback to ModelMasterCreation
            plating_color_assigned = False
            if total_stock.plating_color:
                jig_unload_instance.plating_color = total_stock.plating_color
                plating_color_assigned = True
                print(f"[SMART POPULATE] ✅ Got plating_color from TotalStock: {total_stock.plating_color} (ID: {total_stock.plating_color.id})")
            else:
                # Fallback: Try to get from ModelMasterCreation.plating_color (string field)
                if batch.plating_color:
                    try:
                        from modelmasterapp.models import Plating_Color
                        plating_color_obj = Plating_Color.objects.filter(
                            plating_color=batch.plating_color
                        ).first()
                        if plating_color_obj:
                            jig_unload_instance.plating_color = plating_color_obj
                            plating_color_assigned = True
                            print(f"[SMART POPULATE] ✅ Fallback: Got plating_color from ModelMasterCreation: {plating_color_obj} (ID: {plating_color_obj.id})")
                        else:
                            print(f"[SMART POPULATE] ❌ No Plating_Color object found for string: '{batch.plating_color}'")
                    except Exception as e:
                        print(f"[SMART POPULATE] ❌ Error in plating_color fallback: {e}")
                        
            if not plating_color_assigned:
                print(f"[SMART POPULATE] ⚠️ No plating_color assigned from either source")
            
            jig_unload_instance.plating_stk_no = batch.plating_stk_no
            jig_unload_instance.polish_stk_no = batch.polishing_stk_no
            jig_unload_instance.category = batch.category
            
            # Debug logging for final plating color
            print(f"[SMART POPULATE] 🎨 Final plating_color: {jig_unload_instance.plating_color} (ID: {jig_unload_instance.plating_color.id if jig_unload_instance.plating_color else 'None'})")
            print(f"[SMART POPULATE] 🎯 Setting plating_stk_no: {batch.plating_stk_no}")
            print(f"[SMART POPULATE] ✨ Setting version: {total_stock.version}")
            
            # Polish finish from model master
            if total_stock.model_stock_no:
                jig_unload_instance.polish_finish = total_stock.model_stock_no.polish_finish
                if total_stock.model_stock_no.tray_type:
                    jig_unload_instance.tray_type = total_stock.model_stock_no.tray_type.tray_type
                    jig_unload_instance.tray_capacity = total_stock.model_stock_no.tray_capacity
            
            # 🧠 SMART: Get jig_qr_id from JigCompleted
            if jig_detail:
                if hasattr(jig_detail, 'jig_qr_id'):
                    jig_unload_instance.jig_qr_id = jig_detail.jig_qr_id
                    print(f"[SMART POPULATE] ✅ Got jig_qr_id: {jig_detail.jig_qr_id} from JigCompleted")
                else:
                    print(f"[SMART POPULATE] ⚠️ JigCompleted found but no jig_qr_id field")
            else:
                print(f"[SMART POPULATE] ⚠️ No JigCompleted found for jig_lot_id: {jig_lot_id}")
            
            # Save to persist field changes
            print(f"[SMART POPULATE] 💾 Saving instance before checking persistence...")
            jig_unload_instance.save()
            
            # Verify the save was successful
            jig_unload_instance.refresh_from_db()
            print(f"[SMART POPULATE] ✅ After save - plating_color: {jig_unload_instance.plating_color}")
            print(f"[SMART POPULATE] ✅ After save - plating_color ID: {jig_unload_instance.plating_color.id if jig_unload_instance.plating_color else 'None'}")
            
            # 🧠 SMART LOCATION: Try multiple sources for location
            locations_to_set = []
            
            # First try: Get from TotalStockModel
            if total_stock.location.exists():
                locations_to_set = list(total_stock.location.all())
                print(f"[SMART POPULATE] ✅ Got locations from TotalStock: {[l.location_name for l in locations_to_set]}")
            
            # Second try: Get from ModelMasterCreation batch
            elif batch.location:
                locations_to_set = [batch.location]
                print(f"[SMART POPULATE] ✅ Got location from Batch: {batch.location.location_name}")
            
            # Third try: Get from JigCompleted if it has location field
            elif jig_detail and hasattr(jig_detail, 'location') and jig_detail.location:
                if hasattr(jig_detail.location, 'all'):  # ManyToMany
                    locations_to_set = list(jig_detail.location.all())
                else:  # ForeignKey
                    locations_to_set = [jig_detail.location]
                print(f"[SMART POPULATE] ✅ Got locations from JigCompleted")
            
            # Set locations if found
            if locations_to_set:
                jig_unload_instance.location.set(locations_to_set)
                print(f"[SMART POPULATE] ✅ Set {len(locations_to_set)} locations")
                
            print(f"[SMART POPULATE] ✅ Saved instance with plating_color: {jig_unload_instance.plating_color}")
            print(f"[SMART POPULATE] ✅ Populated all fields from lot: {lot_id}")
            break  # Use first valid lot found
            
        except Exception as e:
            print(f"[SMART POPULATE] ⚠️ Error with lot {lot_id}: {e}")
            import traceback
            traceback.print_exc()
            continue

@csrf_exempt
@require_POST
def save_jig_unload_tray_ids(request):
    import json
    import logging
    
    logger = logging.getLogger(__name__)
    
    try:
        data = json.loads(request.body)
        trays = data.get('trays', [])
        combined_lot_ids = data.get('combined_lot_ids', [])
        main_lot_id = data.get('main_lot_id', '')
        jig_lot_id = data.get('jig_lot_id', '')
        
        # 🔧 NEW: Get jig-aware sources from frontend
        jig_aware_sources = data.get('jig_aware_sources', [])

        print(f"[SMART SAVE] jig_lot_id: '{jig_lot_id}'")
        print(f"[SMART SAVE] combined_lot_ids: {combined_lot_ids}")
        print(f"[SMART SAVE] main_lot_id: '{main_lot_id}'")
        print(f"[SMART SAVE] jig_aware_sources: {jig_aware_sources}")
        print(f"[SMART SAVE] trays count: {len(trays)}")

        if not trays:
            return JsonResponse({'success': False, 'error': 'Trays data is missing.'})

        # Collect tray data
        all_lot_ids_from_trays = set()
        total_case_qty = 0
        
        for i, tray in enumerate(trays):
            tray_id = tray.get('tray_id')
            tray_qty = tray.get('tray_qty')
            tray_lot_id = tray.get('lot_id')

            if not all([tray_id, tray_qty, tray_lot_id]):
                return JsonResponse({'success': False, 'error': f'Missing data for tray {i}'})

            all_lot_ids_from_trays.add(tray_lot_id)
            total_case_qty += int(tray_qty)

        # 🔧 FIXED: Smart combined lot_ids formatting with PRESERVED jig_lot_id mapping
        final_formatted_combined_lot_ids = []
        
        if jig_aware_sources:
            print(f"[SMART SAVE] 🎯 Using jig-aware sources for accurate formatting")
            
            # 🔧 FIXED: Parse jig-aware sources and preserve original jig_lot_id
            for jig_aware_source in jig_aware_sources:
                if ':' in jig_aware_source:
                    # Format: "JLOT-8CEDE491A4A3:LID210820252112300004"
                    original_jig_lot_id, lot_id = jig_aware_source.split(':', 1)
                    formatted_id = f"{original_jig_lot_id}-{lot_id}"
                    final_formatted_combined_lot_ids.append(formatted_id)
                    print(f"[SMART SAVE] ✅ Preserved mapping: {jig_aware_source} -> {formatted_id}")
                else:
                    print(f"[SMART SAVE] ⚠️ Invalid jig-aware source format: {jig_aware_source}")
            
        elif combined_lot_ids and jig_lot_id:
            print(f"[SMART SAVE] 📋 Fallback: Using current jig_lot_id for all lot_ids")
            # Fallback: use current jig_lot_id for all (original behavior)
            for lot_id in combined_lot_ids:
                formatted_id = f"{jig_lot_id}-{lot_id}"
                final_formatted_combined_lot_ids.append(formatted_id)
                print(f"[SMART SAVE] 🔄 Formatted (fallback): {lot_id} -> {formatted_id}")
        else:
            # Final fallback: use lot_ids from trays
            final_formatted_combined_lot_ids = list(all_lot_ids_from_trays)
            print(f"[SMART SAVE] 🆘 Final fallback to tray lot_ids: {final_formatted_combined_lot_ids}")
        
        # Clean and validate lot_ids
        cleaned_combined_lot_ids = combined_lot_ids if combined_lot_ids else list(all_lot_ids_from_trays)
        cleaned_combined_lot_ids = list(set([lot_id.strip() for lot_id in cleaned_combined_lot_ids if lot_id and lot_id.strip()]))
        
        print(f"[SMART SAVE] 📋 cleaned_combined_lot_ids: {cleaned_combined_lot_ids}")
        print(f"[SMART SAVE] 🎯 FINAL formatted_combined_lot_ids for DB: {final_formatted_combined_lot_ids}")

        # 🧠 SMART DEDUPLICATION: Check existing records
        existing_formatted_ids = set()
        if final_formatted_combined_lot_ids:
            # Find existing records that contain any of these formatted IDs
            existing_records = JigUnloadAfterTable.objects.filter(
                combine_lot_ids__isnull=False
            ).exclude(combine_lot_ids__exact=[])
            
            for record in existing_records:
                if record.combine_lot_ids:
                    for existing_combined_id in record.combine_lot_ids:
                        for new_formatted_id in final_formatted_combined_lot_ids:
                            if existing_combined_id == new_formatted_id:
                                existing_formatted_ids.add(existing_combined_id)
            
            print(f"[SMART SAVE] existing formatted IDs in DB: {existing_formatted_ids}")

        # 🧠 SMART MERGE: Remove duplicates and merge with existing
        unique_formatted_ids = []
        for formatted_id in final_formatted_combined_lot_ids:
            if formatted_id not in existing_formatted_ids:
                unique_formatted_ids.append(formatted_id)
                print(f"[SMART SAVE] ✅ Adding new: {formatted_id}")
            else:
                print(f"[SMART SAVE] ⚠️ Skipping duplicate: {formatted_id}")
        
        # Final formatted list for database
        final_db_combined_lot_ids = unique_formatted_ids
        print(f"[SMART SAVE] 📋 FINAL DB combined_lot_ids: {final_db_combined_lot_ids}")

        # 🔧 FIXED: Enhanced get_stock_model_data_for_save function
        def get_stock_model_data_for_save(lot_id):
            """Get stock model data for individual lot_id during save operation"""
            print(f"🔍 [SAVE] get_stock_model_data_for_save called for lot_id: '{lot_id}'")
            
            # Handle JLOT format - extract actual lot_id
            search_lot_ids = [lot_id]
            
            # 🔧 FIXED: Better parsing for various lot_id formats
            if 'JLOT-' in lot_id and (':' in lot_id or '-' in lot_id):
                # Handle both "JLOT-XXX:LID123" and "JLOT-XXX-LID123" formats
                if ':' in lot_id:
                    actual_lot_id = lot_id.split(':')[-1]  # Get part after ':'
                elif '-' in lot_id:
                    parts = lot_id.split('-')
                    if len(parts) > 1:
                        actual_lot_id = parts[-1]  # Get last part after final '-'
                    else:
                        actual_lot_id = lot_id
                else:
                    actual_lot_id = lot_id
                    
                search_lot_ids.append(actual_lot_id)
                print(f"📋 [SAVE] Extracted actual lot_id: '{actual_lot_id}' from '{lot_id}'")
            
            # Try each possible lot_id format
            for search_id in search_lot_ids:
                # Try TotalStockModel first
                tsm = TotalStockModel.objects.select_related('batch_id').filter(lot_id=search_id).first()
                if tsm:
                    print(f"✅ [SAVE] Found in TotalStockModel with lot_id: '{search_id}'")
                    return tsm, False, ModelMasterCreation
                
                # Try RecoveryStockModel
                try:
                    rsm = RecoveryStockModel.objects.select_related('batch_id').filter(lot_id=search_id).first()
                    if rsm:
                        print(f"✅ [SAVE] Found in RecoveryStockModel with lot_id: '{search_id}'")
                        from Recovery_DP.models import RecoveryMasterCreation
                        return rsm, True, RecoveryMasterCreation
                except:
                    pass
            
            print(f"❌ [SAVE] No stock model found for lot_id: '{lot_id}'")
            return None, False, None

        # Calculate missing_qty (existing logic)
        missing_qty = 0
        expected_qty = 0
        jig_detail = None
        if cleaned_combined_lot_ids:
            for lot_id in cleaned_combined_lot_ids:
                jig_detail = JigCompleted.objects.filter(draft_data__lot_id_quantities__has_key=lot_id).first()
                if jig_detail:
                    break
            if jig_detail and jig_detail.lot_id_quantities:
                for lot_id in cleaned_combined_lot_ids:
                    expected_qty += int(jig_detail.lot_id_quantities.get(lot_id, 0))
            missing_qty = max(expected_qty - total_case_qty, 0)

        # ✅ COLLECT ALL VALUES FROM INDIVIDUAL LOT_IDS (corrected lot_id extraction)
        all_plating_stk_nos = []
        all_polish_stk_nos = []
        all_versions = []

        print(f"🔍 [SAVE] ===== COLLECTING VALUES FROM LOT_IDS WITH FIXED EXTRACTION =====")

        for lot_id in cleaned_combined_lot_ids:
            print(f"🔄 [SAVE] Processing lot_id: '{lot_id}'")
            
            try:
                stock_model, is_recovery, batch_model_class = get_stock_model_data_for_save(lot_id)
                
                if stock_model:
                    print(f"✅ [SAVE] Found stock model for {lot_id}: {type(stock_model).__name__}")
                    
                    # 🔧 FIXED: Get data from batch_id (ModelMasterCreation) instead of stock model directly
                    batch_model = stock_model.batch_id if hasattr(stock_model, 'batch_id') and stock_model.batch_id else None
                    
                    if batch_model:
                        print(f"✅ [SAVE] Found batch model for {lot_id}: {type(batch_model).__name__}")
                        
                        # Check for plating field in batch model
                        plating_fields_to_check = [
                            'plating_stk_no', 'plating_stock_no', 'plating_stk_number',
                            'plating_number', 'plat_stk_no', 'plat_stock_no'
                        ]
                        
                        found_plating = False
                        for field_name in plating_fields_to_check:
                            if hasattr(batch_model, field_name):
                                field_value = getattr(batch_model, field_name)
                                if field_value:
                                    all_plating_stk_nos.append(str(field_value))
                                    found_plating = True
                                    print(f"✅ [SAVE] Added plating_stk_no: '{field_value}' from batch field '{field_name}' in {lot_id}")
                                    break
                        
                        if not found_plating:
                            print(f"⚠️ [SAVE] No plating_stk_no found in batch model for {lot_id}")
                        
                        # Check for polish field in batch model
                        polish_fields_to_check = [
                            'polish_stk_no', 'polishing_stk_no', 'polish_stock_no', 
                            'polishing_stock_no', 'polish_number', 'pol_stk_no'
                        ]
                        
                        found_polish = False
                        for field_name in polish_fields_to_check:
                            if hasattr(batch_model, field_name):
                                field_value = getattr(batch_model, field_name)
                                if field_value:
                                    all_polish_stk_nos.append(str(field_value))
                                    found_polish = True
                                    print(f"✅ [SAVE] Added polish_stk_no: '{field_value}' from batch field '{field_name}' in {lot_id}")
                                    break
                        
                        if not found_polish:
                            print(f"⚠️ [SAVE] No polish_stk_no found in batch model for {lot_id}")
                    else:
                        print(f"⚠️ [SAVE] No batch_id found in stock model for {lot_id}")
                        
                        # 🔧 FALLBACK: Try to get plating/polish directly from stock model
                        plating_fields_to_check = [
                            'plating_stk_no', 'plating_stock_no', 'plating_stk_number',
                            'plating_number', 'plat_stk_no', 'plat_stock_no'
                        ]
                        
                        for field_name in plating_fields_to_check:
                            if hasattr(stock_model, field_name):
                                field_value = getattr(stock_model, field_name)
                                if field_value:
                                    all_plating_stk_nos.append(str(field_value))
                                    print(f"✅ [SAVE] Added plating_stk_no: '{field_value}' from stock field '{field_name}' in {lot_id}")
                                    break
                        
                        polish_fields_to_check = [
                            'polish_stk_no', 'polishing_stk_no', 'polish_stock_no', 
                            'polishing_stock_no', 'polish_number', 'pol_stk_no'
                        ]
                        
                        for field_name in polish_fields_to_check:
                            if hasattr(stock_model, field_name):
                                field_value = getattr(stock_model, field_name)
                                if field_value:
                                    all_polish_stk_nos.append(str(field_value))
                                    print(f"✅ [SAVE] Added polish_stk_no: '{field_value}' from stock field '{field_name}' in {lot_id}")
                                    break
                    
                    # Collect version (can be from stock model or batch model)
                    version_display = None
                    
                    # Try stock model first for version
                    if hasattr(stock_model, 'version') and stock_model.version:
                        if hasattr(stock_model.version, 'version_internal'):
                            version_display = stock_model.version.version_internal
                        elif hasattr(stock_model.version, 'version_name'):
                            version_display = stock_model.version.version_name
                        elif hasattr(stock_model.version, 'version'):
                            version_display = stock_model.version.version
                        else:
                            version_display = str(stock_model.version)
                    # Fallback to batch model for version
                    elif batch_model and hasattr(batch_model, 'version') and batch_model.version:
                        if hasattr(batch_model.version, 'version_internal'):
                            version_display = batch_model.version.version_internal
                        elif hasattr(batch_model.version, 'version_name'):
                            version_display = batch_model.version.version_name
                        elif hasattr(batch_model.version, 'version'):
                            version_display = batch_model.version.version
                        else:
                            version_display = str(batch_model.version)
                    
                    if version_display:
                        all_versions.append(str(version_display))
                        print(f"✅ [SAVE] Added version: '{version_display}' from {lot_id}")
                    else:
                        print(f"⚠️ [SAVE] No version found for {lot_id}")
                
                else:
                    print(f"⚠️ [SAVE] No stock model found for lot_id: '{lot_id}'")
                    
            except Exception as e:
                print(f"❌ [SAVE] Error processing lot_id '{lot_id}': {e}")
                import traceback
                traceback.print_exc()
                continue

        print(f"🔍 [SAVE] ===== FINAL COLLECTION RESULTS =====")
        print(f"✅ [SAVE] Collected plating_stk_nos: {all_plating_stk_nos}")
        print(f"✅ [SAVE] Collected polish_stk_nos: {all_polish_stk_nos}")
        print(f"✅ [SAVE] Collected versions: {all_versions}")

        # 🧠 SMART CREATE/UPDATE: Check if we should update existing record or create new
        jig_unload_fter_instance = None
        unload_lot_id = None
        
        if final_db_combined_lot_ids and total_case_qty > 0:
            # Check if there's an existing record we should update
            existing_record = None
            
            # Find record that contains any of these specific formatted lot_ids
            potential_records = JigUnloadAfterTable.objects.filter(
                combine_lot_ids__isnull=False
            ).exclude(combine_lot_ids__exact=[])
            
            for record in potential_records:
                if record.combine_lot_ids:
                    # Check if this record contains any of our new formatted lot_ids
                    if any(formatted_id in record.combine_lot_ids for formatted_id in final_db_combined_lot_ids):
                        existing_record = record
                        break

            if existing_record:
                # 🔄 UPDATE existing record
                # Merge with existing combine_lot_ids (avoid duplicates)
                updated_combine_lot_ids = list(set(existing_record.combine_lot_ids + final_db_combined_lot_ids))
                
                existing_record.combine_lot_ids = updated_combine_lot_ids
                existing_record.total_case_qty += total_case_qty  # Add to existing
                existing_record.missing_qty = max(existing_record.missing_qty, missing_qty)
                existing_record.Un_loaded_date_time = timezone.now()
                existing_record.last_process_module = "Jig Unloading"
                
                # ✅ UPDATE: MERGE COLLECTED VALUES AS LISTS
                if all_plating_stk_nos:
                    existing_plating = existing_record.plating_stk_no_list or []
                    merged_plating = list(set(existing_plating + all_plating_stk_nos))
                    existing_record.plating_stk_no_list = merged_plating
                    
                if all_polish_stk_nos:
                    existing_polish = existing_record.polish_stk_no_list or []
                    merged_polish = list(set(existing_polish + all_polish_stk_nos))
                    existing_record.polish_stk_no_list = merged_polish
                    
                if all_versions:
                    existing_versions = existing_record.version_list or []
                    merged_versions = list(set(existing_versions + all_versions))
                    existing_record.version_list = merged_versions
                
                existing_record.save()
                
                # 🧠 SMART POPULATE: Auto-fill other fields from TotalStockModel (for existing records too)
                populate_jig_unload_fields(existing_record, cleaned_combined_lot_ids, jig_lot_id)
                
                # Save again after population to ensure plating_color and other fields are persisted
                existing_record.save()
                
                jig_unload_fter_instance = existing_record
                unload_lot_id = str(existing_record.lot_id)
                print(f"[SMART SAVE] 🔄 Updated existing record {existing_record.id}")
                print(f"[SMART SAVE] 📋 Final combine_lot_ids: {updated_combine_lot_ids}")
                print(f"[SMART SAVE] ✅ Refreshed plating color and other fields from TotalStockModel")
                print(f"[SMART SAVE] 🎨 Final plating_color: {existing_record.plating_color}")
                
            else:
                # 🆕 CREATE new record
                try:
                    # ✅ CREATE WITH PRESERVED JIG-LOT MAPPING
                    create_data = {
                        'combine_lot_ids': final_db_combined_lot_ids,  # 🔧 FIXED: Use preserved mapping
                        'total_case_qty': total_case_qty,
                        'missing_qty': missing_qty,
                        'Un_loaded_date_time': timezone.now(),
                        'last_process_module': "Jig Unloading"
                    }
                    
                    # ✅ ADD COLLECTED VALUES AS LISTS
                    if all_plating_stk_nos:
                        create_data['plating_stk_no_list'] = all_plating_stk_nos
                    if all_polish_stk_nos:
                        create_data['polish_stk_no_list'] = all_polish_stk_nos
                    if all_versions:
                        create_data['version_list'] = all_versions
                    
                    jig_unload_fter_instance = JigUnloadAfterTable.objects.create(**create_data)
                    
                    # 🧠 SMART POPULATE: Auto-fill other fields from TotalStockModel
                    populate_jig_unload_fields(jig_unload_fter_instance, cleaned_combined_lot_ids, jig_lot_id)
                    
                    # 🔧 FIX: Save again after population to ensure plating_color and other fields are persisted
                    jig_unload_fter_instance.save()
                    
                    unload_lot_id = str(jig_unload_fter_instance.lot_id)
                    print(f"[SMART SAVE] 🆕 Created new record {jig_unload_fter_instance.id}")
                    print(f"[SMART SAVE] 📋 Saved combine_lot_ids: {final_db_combined_lot_ids}")
                    print(f"[SMART SAVE] ✅ Saved plating_stk_no_list: {all_plating_stk_nos}")
                    print(f"[SMART SAVE] ✅ Saved polish_stk_no_list: {all_polish_stk_nos}")
                    print(f"[SMART SAVE] ✅ Saved version_list: {all_versions}")
                    
                except Exception as e:
                    logger.error(f"Error creating JigUnloadAfterTable: {str(e)}")
                    return JsonResponse({'success': False, 'error': f'Failed to create unload record: {str(e)}'})

        if not unload_lot_id:
            return JsonResponse({'success': False, 'error': 'Failed to generate unload_lot_id'})

        # Update trays (existing logic)
        unload_lot_id = str(jig_unload_fter_instance.lot_id)
        saved_trays = []
        for i, tray in enumerate(trays):
            tray_id = tray.get('tray_id')
            tray_qty = tray.get('tray_qty')
            original_lot_id = tray.get('lot_id')
            is_top_tray = tray.get('is_top_tray', False)  # Get top_tray flag from frontend

            # Update TrayId
            try:
                tray_obj = TrayId.objects.get(tray_id=tray_id)
                tray_obj.lot_id = unload_lot_id
                tray_obj.tray_quantity = tray_qty
                tray_obj.save(update_fields=['lot_id', 'tray_quantity'])
            except TrayId.DoesNotExist:
                logger.warning(f"TrayId '{tray_id}' does not exist")

            # Update JigUnload_TrayId
            jig_unload_tray, created = JigUnload_TrayId.objects.update_or_create(
                tray_id=tray_id,
                lot_id=unload_lot_id,
                defaults={
                    'tray_qty': tray_qty,
                    'top_tray': is_top_tray
                }
            )

            saved_trays.append({
                'tray_id': tray_id,
                'original_lot_id': original_lot_id,
                'unload_lot_id': unload_lot_id,
                'tray_qty': tray_qty,
                'top_tray': is_top_tray,  # Include in response
                'created': created
            })

        # Update JigCompleted (existing logic)
        if cleaned_combined_lot_ids:
            try:
                jig_detail = None
                for lot_id in cleaned_combined_lot_ids:
                    potential_jig_details = JigCompleted.objects.filter(lot_id_quantities__has_key=lot_id)
                    if potential_jig_details.exists():
                        jig_detail = potential_jig_details.first()
                        break
                
                if jig_detail:
                    jig_detail.combined_lot_ids = cleaned_combined_lot_ids
                    jig_detail.last_process_module = "Jig Unloading"
                    jig_detail.save()
                    print(f"[SMART SAVE] ✅ Updated JigCompleted {jig_detail.id}")
            except Exception as e:
                logger.error(f"Error updating JigCompleted: {str(e)}")
        
        # 🆕 UPDATE: Update Inprocess Inspection completed table records
        # Update all JigCompleted records that contain any of the cleaned_combined_lot_ids
        # This ensures the "Lot Status" shows "Released" and "Current Stage" shows "Jig Unloading"
        if cleaned_combined_lot_ids:
            try:
                # Find all JigCompleted records that have any of the lot_ids in their lot_id_quantities
                affected_jig_details = JigCompleted.objects.filter(
                    lot_id_quantities__has_any_keys=cleaned_combined_lot_ids
                )
                
                updated_count = 0
                for jig_detail in affected_jig_details:
                    # Update last_process_module to "Jig Unloading" 
                    # This will make Lot Status show "Released" and Current Stage show "Jig Unloading"
                    jig_detail.last_process_module = "Jig Unloading"
                    jig_detail.save(update_fields=['last_process_module'])
                    updated_count += 1
                    print(f"[SMART SAVE] ✅ Updated Inprocess Inspection record {jig_detail.id} (lot_ids: {list(jig_detail.lot_id_quantities.keys())})")

                # 🆕 UPDATE: Update Models Present field (no_of_model_cases) based on remaining lot_ids
                for jig_detail in affected_jig_details:
                    try:
                        # Get all remaining lot_ids in this jig after unloading
                        remaining_lot_ids = list(jig_detail.lot_id_quantities.keys())
                        
                        # Remove the unloaded lot_ids from remaining_lot_ids
                        remaining_lot_ids = [lot_id for lot_id in remaining_lot_ids if lot_id not in cleaned_combined_lot_ids]
                        
                        print(f"[MODELS UPDATE] Jig {jig_detail.id} - Remaining lot_ids after unload: {remaining_lot_ids}")
                        
                        # Map remaining lot_ids to model numbers
                        remaining_model_numbers = set()
                        
                        for lot_id in remaining_lot_ids:
                            try:
                                # Get stock model data for this lot_id
                                stock_model, is_recovery, batch_model_class = get_stock_model_data_for_save(lot_id)
                                
                                if stock_model and stock_model.batch_id:
                                    # Get the model number from the batch
                                    batch_model = batch_model_class.objects.filter(id=stock_model.batch_id.id).first()
                                    if batch_model and hasattr(batch_model, 'model_stock_no') and batch_model.model_stock_no:
                                        model_no = batch_model.model_stock_no.model_no
                                        if model_no:
                                            remaining_model_numbers.add(model_no)
                                            print(f"[MODELS UPDATE] Found model '{model_no}' for lot_id '{lot_id}'")
                            
                            except Exception as e:
                                print(f"[MODELS UPDATE] Error getting model for lot_id '{lot_id}': {e}")
                                continue
                        
                        # Update no_of_model_cases with remaining models
                        remaining_model_list = list(remaining_model_numbers)
                        jig_detail.no_of_model_cases = remaining_model_list
                        jig_detail.save(update_fields=['no_of_model_cases'])
                        
                        print(f"[MODELS UPDATE] ✅ Updated no_of_model_cases for jig {jig_detail.id}: {remaining_model_list}")
                        
                    except Exception as e:
                        print(f"[MODELS UPDATE] Error updating no_of_model_cases for jig {jig_detail.id}: {e}")
                
                print(f"[SMART SAVE] ✅ Updated {updated_count} Inprocess Inspection completed table records")
                
            except Exception as e:
                logger.error(f"Error updating Inprocess Inspection completed table: {str(e)}")
                # Don't fail the entire operation if this update fails
                print(f"[SMART SAVE] ⚠️ Failed to update Inprocess Inspection completed table: {str(e)}")
        
        # ✅ ENHANCED JIG RELEASE LOGIC - Release jig QR ID after successful unloading with data consistency fix
        # This implements the user's requirement: "unload func is to release the jig qr id and making it free to use for next cycle so once unloaded - is_loaded - will be unchecked"
        if jig_lot_id and jig_unload_fter_instance:
            try:
                # Mark the corresponding JigCompleted record as unloaded (unload_over=True)
                jig_details_to_release = JigCompleted.objects.filter(
                    lot_id_quantities__has_any_keys=cleaned_combined_lot_ids,
                    unload_over=False
                )
                
                released_jig_details_count = 0
                jig_qr_ids_to_release = set()  # Collect all jig QR IDs that need to be released
                
                for jig_detail in jig_details_to_release:
                    jig_detail.unload_over = True
                    jig_detail.save(update_fields=['unload_over'])
                    released_jig_details_count += 1
                    
                    # Collect jig QR ID for release
                    if jig_detail.jig_qr_id:
                        jig_qr_ids_to_release.add(jig_detail.jig_qr_id)
                    
                    print(f"[JIG RELEASE] ✅ Set unload_over=True for JigCompleted {jig_detail.id}")
                
                # Extract JIG QR ID from jig_lot_id (remove JLOT- prefix) as primary method
                primary_jig_qr_id = jig_lot_id
                if primary_jig_qr_id.startswith('JLOT-'):
                    primary_jig_qr_id = primary_jig_qr_id[5:]  # Remove 'JLOT-' prefix
                
                # Add primary jig QR ID to the release set
                jig_qr_ids_to_release.add(primary_jig_qr_id)
                
                # Release all collected Jig QR IDs
                total_released_jigs_count = 0
                for jig_qr_id in jig_qr_ids_to_release:
                    if not jig_qr_id:
                        continue
                        
                    print(f"[JIG RELEASE] 🔍 Processing Jig QR ID: '{jig_qr_id}'")
                    
                    # 🔧 ENHANCED: Data consistency check and fix
                    # Check if the jig exists but is incorrectly marked as not loaded
                    jig_obj = Jig.objects.filter(jig_qr_id=jig_qr_id).first()
                    if jig_obj and not jig_obj.is_loaded:
                        # Check if there are corresponding active JigCompleted
                        active_jig_details = JigCompleted.objects.filter(
                            jig_qr_id=jig_qr_id, 
                            unload_over=False
                        ).exists()
                        
                        if active_jig_details:
                            print(f"[JIG RELEASE] 🔧 Data inconsistency detected: JigCompleted '{jig_qr_id}' is active but Jig is not loaded")
                            print(f"[JIG RELEASE] 🔧 Auto-fixing: Setting Jig '{jig_qr_id}' as loaded before release")
                            jig_obj.is_loaded = True
                            jig_obj.save(update_fields=['is_loaded'])
                    
                    # Now proceed with normal release logic
                    released_jigs = Jig.objects.filter(jig_qr_id=jig_qr_id, is_loaded=True)
                    released_jigs_count = released_jigs.count()
                    
                    if released_jigs_count > 0:
                        # Update all matching jigs
                        released_jigs.update(is_loaded=False)
                        total_released_jigs_count += released_jigs_count
                        print(f"[JIG RELEASE] ✅ Released {released_jigs_count} Jig QR ID(s) '{jig_qr_id}' (set is_loaded=False)")
                        
                        # Log each released jig for tracking
                        for jig in released_jigs:
                            print(f"[JIG RELEASE] ✅ Jig ID {jig.id} - QR: '{jig.jig_qr_id}' is now available for reuse")
                    else:
                        print(f"[JIG RELEASE] ⚠️ No loaded Jig found with QR ID '{jig_qr_id}' to release")
                
                print(f"[JIG RELEASE] ✅ Jig release completed - {total_released_jigs_count} total jigs released, {released_jig_details_count} JigCompleted marked as unloaded")
                
                # Store release info for response
                released_jigs_count = total_released_jigs_count
                
            except Exception as e:
                logger.error(f"Error releasing jig QR ID: {str(e)}")
                print(f"[JIG RELEASE] ❌ Failed to release jig QR ID '{jig_lot_id}': {str(e)}")
                import traceback
                traceback.print_exc()
                # Don't fail the entire operation if jig release fails
        
        print(f"[SMART SAVE] ✅ Successfully saved {len(saved_trays)} trays with PRESERVED jig mappings")
        
        return JsonResponse({
            'success': True,
            'message': f'Unloaded Successfully - {released_jigs_count if "released_jigs_count" in locals() else 0} Jig QR ID(s) Released',
            'saved_trays': saved_trays,
            'unload_lot_id': unload_lot_id,
            'jig_release_info': {
                'jig_qr_ids_released': list(jig_qr_ids_to_release) if 'jig_qr_ids_to_release' in locals() else [primary_jig_qr_id] if 'primary_jig_qr_id' in locals() else [],
                'jigs_released_count': released_jigs_count if 'released_jigs_count' in locals() else 0,
                'jig_details_marked_unloaded': released_jig_details_count if 'released_jig_details_count' in locals() else 0
            },
            'preserved_mappings': {
                'jig_aware_sources': jig_aware_sources,
                'final_formatted_ids': final_db_combined_lot_ids
            },
            'collected_values': {
                'plating_stk_nos': all_plating_stk_nos,
                'polish_stk_nos': all_polish_stk_nos,
                'versions': all_versions
            },
            'jig_unload_fter_table': {
                'created': bool(jig_unload_fter_instance),
                'id': jig_unload_fter_instance.id if jig_unload_fter_instance else None,
                'combine_lot_ids': jig_unload_fter_instance.combine_lot_ids if jig_unload_fter_instance else [],
                'total_case_qty': jig_unload_fter_instance.total_case_qty if jig_unload_fter_instance else 0
            }
        })

    except Exception as e:
        logger.error(f'Unexpected error: {str(e)}', exc_info=True)
        return JsonResponse({'success': False, 'error': str(e)})


@csrf_exempt


@require_POST
def save_jig_unload_draft(request):
    """
    Save draft data for Jig Unload - stores all data as JSON
    """
    import json
    import logging
    
    logger = logging.getLogger(__name__)
    
    try:
        data = json.loads(request.body)
        
        # Extract main data
        
        main_lot_id = data.get('main_lot_id', '')
        model_number = data.get('model_number', '')
        total_quantity = data.get('total_quantity', 0)
        trays = data.get('trays', [])
        combined_lot_ids = data.get('combined_lot_ids', [])
        
        logger.info(f"Saving draft for model: {model_number}, lot_id: {main_lot_id}")
        
        if not main_lot_id or not model_number or not trays:
            return JsonResponse({
                'success': False, 
                'error': 'Missing required data: main_lot_id, model_number, or trays'
            })
        
        # Prepare draft data JSON
        draft_data = {
            'model_number': model_number,
            'main_lot_id': main_lot_id,
            'total_quantity': total_quantity,
            'tray_data': [],
            'tray_type_capacity': data.get('tray_type_capacity', 'Normal - 16'),
            'created_timestamp': timezone.now().isoformat(),
            'combined_lot_ids': combined_lot_ids
        }
        
        # Process tray data
        for i, tray in enumerate(trays):
            tray_entry = {
                'sno': i + 1,
                'tray_id': tray.get('tray_id', ''),
                'tray_qty': tray.get('tray_qty', 0),
                'lot_id': tray.get('lot_id', main_lot_id),
                'is_top_tray': tray.get('is_top_tray', i == 0)  # Fix: use is_top_tray not isTopTray
            }
            draft_data['tray_data'].append(tray_entry)
        
        # Save or update draft
        draft, created = JigUnloadDraft.objects.update_or_create(
            main_lot_id=main_lot_id,
            model_number=model_number,
            defaults={
                'total_quantity': total_quantity,
                'draft_data': draft_data,
                'combined_lot_ids': combined_lot_ids,
                'created_by': 'System'  # You can get from request.user if available
            }
        )
        
        logger.info(f"{'Created' if created else 'Updated'} draft with ID: {draft.draft_id}")
        
        return JsonResponse({
            'success': True,
            'message': f'Draft {"saved" if created else "updated"} successfully',
            'draft_id': draft.draft_id,
            'model_number': model_number,
            'total_quantity': total_quantity,
            'tray_count': len(trays)
        })
        
    except json.JSONDecodeError as e:
        logger.error(f'JSON decode error: {e}')
        return JsonResponse({'success': False, 'error': 'Invalid JSON data'})
    except Exception as e:
        logger.error(f'Unexpected error: {e}', exc_info=True)
        return JsonResponse({'success': False, 'error': str(e)})

@csrf_exempt
def load_jig_unload_draft(request):
    """Load draft data for Jig Unload based on main_lot_id"""
    try:
        main_lot_id = request.GET.get('main_lot_id')
        if not main_lot_id:
            return JsonResponse({'success': False, 'error': 'main_lot_id required'})
        
        draft = JigUnloadDraft.objects.filter(main_lot_id=main_lot_id).first()
        
        if draft:
            return JsonResponse({
                'success': True,
                'has_draft': True,
                'draft_data': draft.draft_data,
                'draft_id': draft.draft_id
            })
        else:
            return JsonResponse({
                'success': True,
                'has_draft': False
            })
            
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})
    
    
@csrf_exempt
@require_POST
def validate_tray_id(request):
    import json
    data = json.loads(request.body)
    tray_id = data.get('tray_id', '').strip()
    lot_id = data.get('lot_id', '').strip()

    print(f"[DEBUG] validate_tray_id called with tray_id: '{tray_id}', lot_id: '{lot_id}'")

    if not tray_id:
        return JsonResponse({'success': False, 'error': 'Tray ID required.'})

    try:
        tray = TrayId.objects.get(tray_id=tray_id)
        tray_tray_type = tray.tray_type if tray.tray_type else "Unknown"
        print(f"[DEBUG] Tray '{tray_id}' tray_type: {tray_tray_type}")

        # 🔧 FIXED: Handle combined lot_ids (comma-separated)
        lot_tray_type = None
        
        # Check if lot_id is combined (contains comma)
        if ',' in lot_id:
            # Split combined lot_ids and try each one
            individual_lot_ids = [lid.strip() for lid in lot_id.split(',')]
            print(f"[DEBUG] Combined lot_id detected. Individual lot_ids: {individual_lot_ids}")
            
            for individual_lot_id in individual_lot_ids:
                jig_load_tray = JigLoadTrayId.objects.filter(lot_id=individual_lot_id).first()
                if jig_load_tray and hasattr(jig_load_tray, 'tray_type') and jig_load_tray.tray_type:
                    lot_tray_type = jig_load_tray.tray_type
                    print(f"[DEBUG] Found tray_type '{lot_tray_type}' from individual lot_id: '{individual_lot_id}'")
                    break
            
            if not lot_tray_type:
                print(f"[DEBUG] No tray_type found in JigLoadTrayId for any individual lot_id")
        else:
            # Single lot_id - original logic
            jig_load_tray = JigLoadTrayId.objects.filter(lot_id=lot_id).first()
            if jig_load_tray and hasattr(jig_load_tray, 'tray_type') and jig_load_tray.tray_type:
                lot_tray_type = jig_load_tray.tray_type
            print(f"[DEBUG] Single lot_id '{lot_id}' tray_type from JigLoadTrayId: {lot_tray_type}")

        # 🔧 ENHANCED: More flexible tray type comparison
        if lot_tray_type:
            # Normalize tray types for comparison (case-insensitive, strip spaces)
            tray_type_normalized = str(tray_tray_type).strip().lower()
            lot_type_normalized = str(lot_tray_type).strip().lower()
            
            print(f"[DEBUG] Comparing tray types: '{tray_type_normalized}' vs '{lot_type_normalized}'")
            
            if tray_type_normalized != lot_type_normalized:
                return JsonResponse({
                    'success': False, 
                    'error': f'Tray Type Mismatch'
                })
            else:
                print(f"[DEBUG] ✅ Tray types match: {tray_tray_type}")
        else:
            print(f"[DEBUG] ⚠️ No lot tray_type found to compare with")

        # 🔧 ENHANCED: Check if tray is available
        print(f"[DEBUG] Tray status check:")
        print(f"   tray.lot_id: {tray.lot_id}")
        print(f"   tray.delink_tray: {tray.delink_tray}")
        print(f"   tray.scanned: {getattr(tray, 'scanned', 'N/A')}")

        # Tray availability logic
        if tray.lot_id is None:
            print(f"[DEBUG] ✅ Tray available: lot_id is None")
            return JsonResponse({'success': True, 'message': 'Tray available - not assigned'})
        elif tray.delink_tray:
            print(f"[DEBUG] ✅ Tray available: delinked and reusable")
            return JsonResponse({'success': True, 'message': 'Tray available - delinked'})
        else:
            print(f"[DEBUG] ❌ Tray s available: already assigned to lot_id {tray.lot_id}")
            return JsonResponse({
                'success': False, 
                'error': f'Different Lot'
            })
            
    except TrayId.DoesNotExist:
        print(f"[DEBUG] ❌ Tray '{tray_id}' not found in TrayId table")
        return JsonResponse({'success': False, 'error': 'Invalid TrayID'})
    except Exception as e:
        print(f"[DEBUG] ❌ Unexpected error: {str(e)}")
        import traceback
        traceback.print_exc()
        return JsonResponse({'success': False, 'error': f'Validation error: {str(e)}'})


@csrf_exempt
@require_POST
def validate_tray_id_dynamic(request):
    """
    Enhanced Dynamic Tray Validation API for Jig Unloading Zone 1 (IPS)
    Validates tray ID format and availability like Day Planning
    """
    try:
        data = json.loads(request.body)
        tray_id = data.get('tray_id', '').strip()
        lot_id = data.get('lot_id', '').strip()
        plating_color = data.get('plating_color', '').strip()

        print(f"[DEBUG] validate_tray_id_dynamic Zone 1 called with:")
        print(f"  - tray_id: '{tray_id}'")
        print(f"  - lot_id: '{lot_id}'")
        print(f"  - plating_color: '{plating_color}' (type: {type(plating_color)})")
        print(f"  - raw plating_color: {repr(plating_color)}")

        # ✅ FORCE IPS for Zone 1 if empty or unknown
        if not plating_color or plating_color.lower() in ['', 'unknown', 'none']:
            plating_color = 'IPS'
            print(f"[DEBUG] Zone 1 - Forcing plating_color to IPS (was empty/unknown)")
        
        print(f"[DEBUG] Final plating_color for validation: '{plating_color}'")

        if not tray_id:
            return JsonResponse({
                'success': False,
                'error': 'Please enter a tray ID',
                'message': '⚠️ Tray ID is required',
                'validation_type': 'empty_input'
            }, status=400)

        # ✅ ENHANCED: Validate tray ID format based on color and tray capacity (strict enforcement)
        def validate_tray_format(tray_id, expected_color, lot_id_for_capacity=None):
            """Validate tray ID format based on plating color and, when available, lot's tray capacity.

            Rules enforced:
            - IPS + Normal (capacity 20) -> only 'NR-' allowed
            - IPS + Jumbo (capacity 12)  -> only 'JR-' allowed
            - IPS when capacity unknown -> allow 'NR-' or 'JR-'
            - Non-IPS other colors: allow 'ND-'/'JD-' or 'NL-'/'JL-' as before
            """
            print(f"[DEBUG] validate_tray_format called with tray_id: '{tray_id}', expected_color: '{expected_color}', lot_id_for_capacity: {lot_id_for_capacity}")

            # Prefer tray_type (Jumbo/Normal) derived from lot/tray metadata; fall back to capacity
            determined_capacity = None
            determined_tray_type = None
            try:
                if lot_id_for_capacity:
                    # 1) Try TrayId record for lot
                    t = TrayId.objects.filter(lot_id__icontains=lot_id_for_capacity).first()
                    if t:
                        if getattr(t, 'tray_type', None):
                            determined_tray_type = t.tray_type
                            print(f"[DEBUG] Found tray_type from TrayId record: {determined_tray_type}")
                        if getattr(t, 'tray_capacity', None):
                            determined_capacity = int(t.tray_capacity)
                            print(f"[DEBUG] Found tray_capacity from TrayId record: {determined_capacity}")

                    # 2) Try JigCompleted for tray_type or capacity
                    if not determined_tray_type:
                        jd = JigCompleted.objects.filter(lot_id_quantities__has_key=lot_id_for_capacity).first()
                        if jd:
                            if getattr(jd, 'tray_type', None):
                                determined_tray_type = jd.tray_type
                                print(f"[DEBUG] Found tray_type from JigCompleted: {determined_tray_type}")
                            if getattr(jd, 'tray_capacity', None) and not determined_capacity:
                                determined_capacity = int(jd.tray_capacity)
                                print(f"[DEBUG] Found tray_capacity from JigCompleted: {determined_capacity}")

                    # 3) Try TotalStockModel -> model_stock_no.tray_type/tray_capacity
                    try:
                        tsm = TotalStockModel.objects.select_related('model_stock_no__tray_type').filter(lot_id=lot_id_for_capacity).first()
                        if tsm and getattr(tsm, 'model_stock_no', None):
                            ms = tsm.model_stock_no
                            if not determined_tray_type and getattr(ms, 'tray_type', None):
                                # tray_type may be a FK object with attribute tray_type
                                determined_tray_type = ms.tray_type.tray_type if getattr(ms.tray_type, 'tray_type', None) else ms.tray_type
                                print(f"[DEBUG] Found tray_type from TotalStockModel.model_stock_no: {determined_tray_type}")
                            if getattr(ms, 'tray_capacity', None) and not determined_capacity:
                                determined_capacity = int(ms.tray_capacity)
                                print(f"[DEBUG] Found tray_capacity from TotalStockModel.model_stock_no: {determined_capacity}")
                        else:
                            # 4) Try RecoveryStockModel similarly
                            rsm = RecoveryStockModel.objects.select_related('model_stock_no__tray_type').filter(lot_id=lot_id_for_capacity).first()
                            if rsm and getattr(rsm, 'model_stock_no', None):
                                rms = rsm.model_stock_no
                                if not determined_tray_type and getattr(rms, 'tray_type', None):
                                    determined_tray_type = rms.tray_type.tray_type if getattr(rms.tray_type, 'tray_type', None) else rms.tray_type
                                    print(f"[DEBUG] Found tray_type from RecoveryStockModel.model_stock_no: {determined_tray_type}")
                                if getattr(rms, 'tray_capacity', None) and not determined_capacity:
                                    determined_capacity = int(rms.tray_capacity)
                                    print(f"[DEBUG] Found tray_capacity from RecoveryStockModel.model_stock_no: {determined_capacity}")
                    except Exception as inner_e:
                        print(f"[DEBUG] Error checking Total/RecoveryStockModel for lot '{lot_id_for_capacity}': {inner_e}")
            except Exception as e:
                print(f"[DEBUG] Error attempting to determine tray_type/capacity for lot '{lot_id_for_capacity}': {e}")

            # Build allowed prefixes depending on color and derived tray_type (preferred) or capacity
            valid_prefixes = []
            if expected_color == 'IPS':
                # Prefer tray_type mapping
                if determined_tray_type:
                    dt = str(determined_tray_type).strip().lower()
                    if 'jumbo' in dt:
                        valid_prefixes = ['JR-']
                        print(f"[DEBUG] IPS derived tray_type Jumbo - only 'JR-' allowed")
                    elif 'normal' in dt:
                        valid_prefixes = ['NR-']
                        print(f"[DEBUG] IPS derived tray_type Normal - only 'NR-' allowed")
                    else:
                        # unknown map, fall back to capacity
                        print(f"[DEBUG] IPS derived tray_type '{determined_tray_type}' not recognized, falling back to capacity")
                if not valid_prefixes:
                    # Fallback to capacity if tray_type not decisive
                    if determined_capacity == 20:
                        valid_prefixes = ['NR-']
                        print(f"[DEBUG] IPS capacity Normal (20) detected - only 'NR-' allowed")
                    elif determined_capacity == 12:
                        valid_prefixes = ['JR-']
                        print(f"[DEBUG] IPS capacity Jumbo (12) detected - only 'JR-' allowed")
                    else:
                        valid_prefixes = ['NR-', 'JR-']
                        print(f"[DEBUG] IPS detected - capacity unknown and tray_type unavailable, allowing both 'NR-' and 'JR-'")
            else:
                # Non-IPS: keep previous permissive set (ND/JD for dark, NL/JL for bi-color)
                valid_prefixes = ['ND-', 'JD-', 'NL-', 'JL-']
                print(f"[DEBUG] Non-IPS detected - valid prefixes: {valid_prefixes}")

            # Check if tray starts with valid prefix
            has_valid_prefix = any(tray_id.startswith(prefix) for prefix in valid_prefixes)
            if not has_valid_prefix:
                # If capacity known, give a strict message
                if determined_capacity in (12, 20):
                    expected_prefix = valid_prefixes[0]
                    error_msg = f"Tray capacity is {determined_capacity}. Expected prefix: {expected_prefix} (e.g. {expected_prefix}A00001)"
                else:
                    expected_formats = ' or '.join(valid_prefixes)
                    error_msg = f"Expected format: {expected_formats}A00001"
                print(f"[DEBUG] Invalid prefix - error message: {error_msg}")
                return False, error_msg

            # Check if format matches: PREFIX-A00000
            import re
            pattern = r'^(NR-|JR-|ND-|JD-|NL-|JL-)A\d{5}$'
            if not re.match(pattern, tray_id):
                return False, "Format should be: PREFIX-A00001 (e.g., NR-A00001)"

            print(f"[DEBUG] Tray format validation passed")
            return True, "Valid format"

        # Validate format first
        format_valid, format_message = validate_tray_format(tray_id, plating_color or 'IPS', lot_id_for_capacity=lot_id or None)
        if not format_valid:
            return JsonResponse({
                'success': False,
                'error': f'Invalid tray ID format',
                'message': f'❌ {tray_id} - {format_message}',
                'validation_type': 'invalid_format'
            }, status=400)

        try:
            # Step 1: Check if tray exists in system
            existing_tray = TrayId.objects.filter(tray_id=tray_id).first()
            
            if not existing_tray:
                return JsonResponse({
                    'success': False,
                    'error': f'Tray ID "{tray_id}" not found in system',
                    'message': f'❌ {tray_id} - Not found in system. Contact admin to add tray.',
                    'validation_type': 'tray_not_found'
                }, status=404)
            
            # Step 2: ✅ ENHANCED - Check if tray is already scanned (like Day Planning)
            if existing_tray.scanned and not existing_tray.delink_tray:
                return JsonResponse({
                    'success': False,
                    'error': f'Tray {tray_id} is already scanned',
                    'message': f'❌ {tray_id} - Already scanned ({existing_tray.date.strftime("%d-%m-%Y %H:%M") if existing_tray.date else "Unknown date"})',
                    'validation_type': 'already_scanned',
                    'details': {
                        'linked_lot': existing_tray.lot_id,
                        'batch_id': existing_tray.batch_id.batch_id if existing_tray.batch_id else None,
                        'scan_date': existing_tray.date.strftime('%d-%m-%Y %H:%M') if existing_tray.date else None
                    }
                }, status=400)
            
            # NEW: Derive tray_type (prefer over capacity) and enforce prefix if known
            derived_tray_type = None
            try:
                # If TrayId has tray_type directly, use it
                if getattr(existing_tray, 'tray_type', None):
                    derived_tray_type = existing_tray.tray_type
                    print(f"[DEBUG] Derived tray_type from TrayId: {derived_tray_type}")
                else:
                    # Try to derive from lot -> TotalStockModel -> model_stock_no.tray_type
                    if existing_tray.lot_id:
                        # Use last segment heuristic like before
                        candidate = existing_tray.lot_id
                        if ':' in candidate:
                            candidate = candidate.split(':')[-1]
                        if '-' in candidate:
                            parts = candidate.split('-')
                            if len(parts) > 1:
                                candidate = parts[-1]
                        tsm = TotalStockModel.objects.select_related('model_stock_no').filter(lot_id__icontains=candidate).first()
                        if tsm and getattr(tsm.model_stock_no, 'tray_type', None):
                            derived_tray_type = tsm.model_stock_no.tray_type.tray_type if getattr(tsm.model_stock_no, 'tray_type', None) and getattr(tsm.model_stock_no.tray_type, 'tray_type', None) else None
                            if derived_tray_type:
                                print(f"[DEBUG] Derived tray_type from TotalStockModel.model_stock_no: {derived_tray_type}")
            except Exception as e:
                print(f"[DEBUG] Error deriving tray_type: {e}")

            # If we know tray_type, enforce strict prefix mapping: Jumbo -> JR-, Normal -> NR-
            if derived_tray_type:
                derived_tray_type_norm = str(derived_tray_type).strip().lower()
                if derived_tray_type_norm == 'jumbo' and not tray_id.startswith('JR-'):
                    return JsonResponse({
                        'success': False,
                        'error': f'Tray {tray_id} prefix mismatch for tray type Jumbo',
                        'message': f'❌ {tray_id} - Tray Type is Jumbo. Expected prefix: JR- (e.g. JR-A00001)',
                        'validation_type': 'invalid_prefix_for_tray_type'
                    }, status=400)
                if derived_tray_type_norm == 'normal' and not tray_id.startswith('NR-'):
                    return JsonResponse({
                        'success': False,
                        'error': f'Tray {tray_id} prefix mismatch for tray type Normal',
                        'message': f'❌ {tray_id} - Tray Type is Normal. Expected prefix: NR- (e.g. NR-A00001)',
                        'validation_type': 'invalid_prefix_for_tray_type'
                    }, status=400)

            # Step 3: Validate tray is ready for Jig Unloading process
            # Check if tray has been through required processes (should be loaded in jig)
            if existing_tray.lot_id:
                # existing_tray.lot_id may store different formats. Try multiple candidate lookups
                search_candidates = []
                raw_lot = existing_tray.lot_id
                search_candidates.append(raw_lot)

                # If lot contains ':' (from older formatting), add trailing part as candidate
                if ':' in raw_lot:
                    search_candidates.append(raw_lot.split(':')[-1])

                # If lot contains multiple hyphens (JLOT-XXX-LID...), add last segment
                if '-' in raw_lot:
                    parts = raw_lot.split('-')
                    if len(parts) > 1:
                        search_candidates.append(parts[-1])

                # Also try contains-based search for safety
                search_candidates.append(raw_lot)
                search_candidates = list(dict.fromkeys([c for c in search_candidates if c]))

                print(f"[DEBUG] validate_tray_id_dynamic - searching JigCompleted for candidates: {search_candidates}")

                jig_details = None
                for candidate in search_candidates:
                    try:
                        jig_details = JigCompleted.objects.filter(
                            lot_id__contains=candidate,
                            plating_color='IPS'  # Zone 1 handles IPS
                        ).first()
                        print(f"[DEBUG] JigCompleted lookup with candidate '{candidate}' -> {'found' if jig_details else 'not found'}")
                        if jig_details:
                            break
                    except Exception as e:
                        print(f"[DEBUG] JigCompleted lookup error for candidate '{candidate}': {e}")

                if not jig_details:
                    return JsonResponse({
                        'success': False,
                        'error': f'Tray {tray_id} not found in jig loading records for IPS processing',
                        'message': f'⚠️ {tray_id} - Already Occupied',
                        'validation_type': 'not_in_jig',
                        'searched_candidates': search_candidates
                    }, status=400)
            
            # Step 4: Check if tray has already been unloaded
            already_unloaded = JigUnload_TrayId.objects.filter(tray_id=tray_id).exists()
            if already_unloaded:
                return JsonResponse({
                    'success': False,
                    'error': f'Tray {tray_id} has already been unloaded',
                    'message': f'❌ {tray_id} - Already unloaded',
                    'validation_type': 'already_unloaded'
                }, status=400)
            
            # Step 5: ✅ SUCCESS - Tray is valid for unloading
            return JsonResponse({
                'success': True,
                'message': f'✅ {tray_id} - Valid for unloading',
                'validation_type': 'valid',
                'tray_details': {
                    'tray_id': tray_id,
                    'tray_type': existing_tray.tray_type or 'Unknown',
                    'capacity': existing_tray.tray_capacity or 0,
                    'current_quantity': existing_tray.tray_quantity or 0,
                    'lot_id': existing_tray.lot_id,
                    'batch_id': existing_tray.batch_id.batch_id if existing_tray.batch_id else None,
                    'is_available': existing_tray.is_available_for_scanning
                }
            }, status=200)
            
        except Exception as inner_e:
            print(f"[ERROR] validate_tray_id_dynamic inner exception: {str(inner_e)}")
            return JsonResponse({
                'success': False,
                'error': f'Validation failed for tray {tray_id}',
                'message': f'❌ {tray_id} - Validation error',
                'validation_type': 'validation_error'
            }, status=500)
            
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON data',
            'message': '❌ Invalid request format',
            'validation_type': 'invalid_json'
        }, status=400)
    except Exception as e:
        print(f"[ERROR] validate_tray_id_dynamic outer exception: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': 'Server error during validation',
            'message': '❌ Server error - Please try again',
            'validation_type': 'server_error'
        }, status=500)


@csrf_exempt
@require_POST
def check_unload_status(request):
    """Smart cross-jig multi-lot checker"""
    
    def parse_combined_lot_id(combined_id):
        try:
            if not combined_id or '-' not in combined_id:
                return None, None
            parts = combined_id.rsplit('-', 1)
            return (parts[0], parts[1]) if len(parts) == 2 else (None, None)
        except:
            return None, None

    try:
        data = json.loads(request.body)
        lot_ids = data.get('lot_ids', [])
        jig_lot_id = data.get('jig_lot_id')

        print(f"🎯 [CROSS-JIG] Input - lot_ids: {lot_ids}, jig: {jig_lot_id}")

        if not lot_ids:
            return JsonResponse({'success': True, 'unloaded_lot_ids': []})

        # Get all unload records
        unload_records = JigUnloadAfterTable.objects.filter(
            combine_lot_ids__isnull=False
        ).exclude(combine_lot_ids__exact=[]).values('id', 'combine_lot_ids')

        # Build master unload mapping: lot_id -> jig_lot_id
        lot_to_jig_map = {}
        unloaded_lot_ids = set()
        
        for record in unload_records:
            if not record['combine_lot_ids']:
                continue
                
            for combined_id in record['combine_lot_ids']:
                parsed_jig_id, parsed_lot_id = parse_combined_lot_id(combined_id)
                
                if parsed_jig_id and parsed_lot_id:
                    lot_to_jig_map[parsed_lot_id] = parsed_jig_id
                    print(f"🗺️ [MAP] {parsed_lot_id} belongs to jig {parsed_jig_id}")

        # Smart matching: check if each lot_id is unloaded (regardless of which jig it came from originally)
        for lot_id in lot_ids:
            if lot_id in lot_to_jig_map:
                original_jig = lot_to_jig_map[lot_id]
                if original_jig == jig_lot_id:
                    unloaded_lot_ids.add(lot_id)
                    print(f"✅ [FOUND] {lot_id} is unloaded for jig {original_jig}")
                else:
                    print(f"❌ [SKIP] {lot_id} is unloaded for jig {original_jig}, not {jig_lot_id}")
            else:
                print(f"❌ [NOT_FOUND] {lot_id} not in unload records")
        # Also check JigUnload_TrayId for direct unloads
        direct_unloaded = set()
        for tray in JigUnload_TrayId.objects.filter(lot_id__in=lot_ids):
            if lot_to_jig_map.get(tray.lot_id) == jig_lot_id:
                direct_unloaded.add(tray.lot_id)
        if direct_unloaded:
            unloaded_lot_ids.update(direct_unloaded)
            print(f"✅ [DIRECT] Additional unloaded: {list(direct_unloaded)}")
        
        if direct_unloaded:
            unloaded_lot_ids.update(direct_unloaded)
            print(f"✅ [DIRECT] Additional unloaded: {list(direct_unloaded)}")

        result = list(unloaded_lot_ids)
        print(f"🎯 [FINAL] Unloaded lot_ids: {result}")

        return JsonResponse({
            'success': True,
            'unloaded_lot_ids': result
        })

    except Exception as e:
        print(f"❌ [ERROR] {e}")
        import traceback
        traceback.print_exc()
        return JsonResponse({'success': False, 'error': str(e)})        

@method_decorator(csrf_exempt, name='dispatch')
class JigUnloadListAPIView(APIView):
    def get(self, request):
        lot_id = request.GET.get('lot_id')
        if not lot_id:
            return JsonResponse({'success': False, 'error': 'Missing lot_id'}, status=400)

        trays = JigUnload_TrayId.objects.filter(lot_id=lot_id).order_by('id')
        tray_list = [
            {
                'tray_id': tray.tray_id,
                'tray_quantity': tray.tray_qty,
                'top_tray':tray.top_tray
            }
            for tray in trays
        ]

        return JsonResponse({
            'success': True,
            'trays': tray_list
        })


class JigUnloading_Completedtable(TemplateView):
    template_name = 'Jig_Unloading/JigUnloading_Completedtable.html'

    def get_dynamic_tray_capacity(self, tray_type_name):
        """
        Get dynamic tray capacity using InprocessInspectionTrayCapacity for overrides
        Rules:
        - Normal: Use custom capacity from InprocessInspectionTrayCapacity (20)
        - Others: Use ModelMaster capacity
        """
        try:
            # First try to get custom capacity for this tray type
            custom_capacity = InprocessInspectionTrayCapacity.objects.filter(
                tray_type__tray_type=tray_type_name,
                is_active=True
            ).first()
            
            if custom_capacity:
                return custom_capacity.custom_capacity
            
            # Fallback to ModelMaster tray capacity
            tray_type = TrayType.objects.filter(tray_type=tray_type_name).first()
            if tray_type:
                return tray_type.tray_capacity
                
            # Default fallback
            return 0
            
        except Exception as e:
            print(f"⚠️ Error getting dynamic tray capacity: {e}")
            return 0

    def get_stock_model_data(self, lot_id):
        """
        Helper function to get stock model data from either TotalStockModel or RecoveryStockModel
        Returns: (stock_model, is_recovery, batch_model_class)
        """
        # Try TotalStockModel first
        tsm = TotalStockModel.objects.filter(lot_id=lot_id).first()
        if tsm:
            return tsm, False, ModelMasterCreation
        
        # Try RecoveryStockModel if not found in TotalStockModel
        try:
            rsm = RecoveryStockModel.objects.filter(lot_id=lot_id).first()
            if rsm:
                try:
                    from Recovery_DP.models import RecoveryMasterCreation
                    return rsm, True, RecoveryMasterCreation
                except ImportError:
                    print("⚠️ RecoveryMasterCreation not found, using ModelMasterCreation as fallback")
                    return rsm, True, ModelMasterCreation
        except Exception as e:
            print(f"⚠️ Error accessing RecoveryStockModel: {e}")
        
        return None, False, None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # 🔥 NEW: Add date filtering logic (same as Inprocess Inspection)
        from django.utils import timezone
        import pytz
        from datetime import datetime, timedelta
        
        # Use IST timezone
        tz = pytz.timezone("Asia/Kolkata")
        now_local = timezone.now().astimezone(tz)
        today = now_local.date()
        yesterday = today - timedelta(days=1)

        # Get date filter parameters from request
        from_date_str = self.request.GET.get('from_date')
        to_date_str = self.request.GET.get('to_date')

        # Calculate date range
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

        print(f"[DEBUG] Zone 1 Completed - Date filter: {from_date} to {to_date}")

        # DEBUG: Check total records in JigUnloadAfterTable
        total_unload_records = JigUnloadAfterTable.objects.all().count()
        print(f"[DEBUG] Total JigUnloadAfterTable records: {total_unload_records}")

        # DEBUG: Check allowed color IDs for Zone 1 (Only IPS)
        allowed_color_ids = Plating_Color.objects.filter(
            plating_color='IPS'
        ).values_list('id', flat=True)
        print(f"[DEBUG] Zone 1 Completed - Allowed color IDs (IPS only): {list(allowed_color_ids)}")

        # Filter completed_unloads by Un_loaded_date_time with date filtering
        completed_unloads = JigUnloadAfterTable.objects.filter(
            plating_color_id__in=allowed_color_ids,
            Un_loaded_date_time__date__gte=from_date,
            Un_loaded_date_time__date__lte=to_date
        ).select_related(
            'plating_color', 'polish_finish', 'version'
        ).prefetch_related('location').order_by('-Un_loaded_date_time')
        
        print(f"[DEBUG] Filtered completed_unloads count: {completed_unloads.count()}")
        
        # Debug individual records
        for record in completed_unloads:
            print(f"[DEBUG] Record {record.id}: plating_color={record.plating_color}, plating_color_id={record.plating_color_id}")
        
        # Also check all records to see what plating colors they have
        all_records = JigUnloadAfterTable.objects.all()
        print(f"[DEBUG] All records plating colors:")
        for record in all_records:
            color_name = record.plating_color.plating_color if record.plating_color else "None"
            print(f"[DEBUG]   Record {record.id}: plating_color={color_name} (ID: {record.plating_color_id})")

        # ✅ ENHANCED: Get all model numbers from combine_lot_ids for bulk processing
        all_model_numbers = set()
        all_lot_ids = set()
        
        for unload in completed_unloads:
            if unload.combine_lot_ids:
                all_lot_ids.update(unload.combine_lot_ids)
                # Get model numbers from lot_ids
                for lot_id in unload.combine_lot_ids:
                    stock_model, is_recovery, batch_model_class = self.get_stock_model_data(lot_id)
                    if stock_model and hasattr(stock_model, 'model_stock_no') and stock_model.model_stock_no:
                        all_model_numbers.add(stock_model.model_stock_no.model_no)

        print(f"[DEBUG] Found {len(all_model_numbers)} unique model numbers: {all_model_numbers}")
        print(f"[DEBUG] Found {len(all_lot_ids)} unique lot IDs")

        # ✅ ENHANCED: Use same color palette as Jig_Unloading_MainTable
        color_palette = [
            "#e74c3c", "#f1c40f", "#2ecc71", "#3498db", "#9b59b6",
            "#e67e22", "#1abc9c", "#34495e", "#f39c12", "#d35400",
            "#c0392b", "#8e44ad", "#2980b9", "#27ae60", "#16a085"
        ]
        
        # Create a global color mapping for all unique model numbers
        global_model_colors = {}
        sorted_model_numbers = sorted(list(all_model_numbers))
        for idx, model_no in enumerate(sorted_model_numbers):
            color_index = idx % len(color_palette)
            global_model_colors[model_no] = color_palette[color_index]

        # ✅ ENHANCED: Comprehensive model images fetching (same as MainTable)
        model_images_map = {}
        if all_model_numbers:
            print(f"[DEBUG] Fetching images for {len(all_model_numbers)} models")
            
            # Fetch images for each model using ModelMaster
            model_masters = ModelMaster.objects.filter(
                model_no__in=all_model_numbers
            ).prefetch_related('images')
            
            for model_master in model_masters:
                images = list(model_master.images.all())
                image_urls = []
                first_image = "/static/assets/images/imagePlaceholder.png"
                
                for img in images:
                    if img.master_image:
                        try:
                            img_url = img.master_image.url if hasattr(img.master_image, 'url') else str(img.master_image)
                            image_urls.append(img_url)
                            if first_image == "/static/assets/images/imagePlaceholder.png":
                                first_image = img_url
                        except Exception as img_err:
                            print(f"[DEBUG] Error processing image {img.id}: {img_err}")
                            continue
                
                model_images_map[model_master.model_no] = {
                    'images': image_urls,
                    'first_image': first_image
                }
                print(f"[DEBUG] Model {model_master.model_no}: {len(image_urls)} images, first: {first_image}")

        # ✅ ENHANCED: Process each unload record with comprehensive data
        table_data = []
        for idx, unload in enumerate(completed_unloads):
            print(f"\n[DEBUG] ===== PROCESSING RECORD {idx + 1} =====")
            print(f"[DEBUG] Unload lot_id: {unload.lot_id}")
            print(f"[DEBUG] combine_lot_ids: {unload.combine_lot_ids}")
            
            # Get jig_qr_id and remarks from JigCompleted using jig_qr_id
            jig_qr_id = unload.jig_qr_id
            unloading_remarks = None
            if jig_qr_id:
                jig_detail = JigCompleted.objects.filter(jig_qr_id=jig_qr_id).first()
                if jig_detail:
                    unloading_remarks = jig_detail.unloading_remarks

            # Handle location (many-to-many)
            try:
                locations = unload.location.all()
                location_names = [loc.location_name for loc in locations]
                location_display = ", ".join(location_names) if location_names else "N/A"
            except Exception as e:
                print(f"[DEBUG] Error processing location: {e}")
                location_display = "N/A"
                
            # 🔧 ENHANCED: Calculate tray info using dynamic capacity method
            tray_type_display = unload.tray_type or "N/A"
            
            # Use get_dynamic_tray_capacity method for proper Normal tray handling (16 -> 20)
            if tray_type_display != "N/A":
                dynamic_tray_capacity = self.get_dynamic_tray_capacity(tray_type_display)
                tray_capacity = dynamic_tray_capacity if dynamic_tray_capacity > 0 else (unload.tray_capacity if unload.tray_capacity else 1)
                print(f"[DEBUG] Zone 1 - Dynamic tray capacity for '{tray_type_display}': {dynamic_tray_capacity} (was: {unload.tray_capacity})")
            else:
                tray_capacity = unload.tray_capacity if unload.tray_capacity else 1
                print(f"[DEBUG] Zone 1 - Using raw tray capacity: {tray_capacity}")
            
            total_case_qty = unload.total_case_qty if unload.total_case_qty else 0
            no_of_trays = math.ceil(total_case_qty / tray_capacity) if tray_capacity > 0 else 0

            # ✅ SIMPLIFIED: Use saved list fields from database instead of processing individual lot_ids
            print(f"[DEBUG] ===== USING SAVED LIST FIELDS FROM DATABASE =====")
            
            # ✅ GET SAVED LISTS from database fields
            saved_plating_list = getattr(unload, 'plating_stk_no_list', []) or []
            saved_polish_list = getattr(unload, 'polish_stk_no_list', []) or []
            saved_version_list = getattr(unload, 'version_list', []) or []
            
            print(f"[DEBUG] saved_plating_stk_no_list: {saved_plating_list} (type: {type(saved_plating_list)})")
            print(f"[DEBUG] saved_polish_stk_no_list: {saved_polish_list} (type: {type(saved_polish_list)})")
            print(f"[DEBUG] saved_version_list: {saved_version_list} (type: {type(saved_version_list)})")
            
            # ✅ USE SAVED LISTS DIRECTLY (including duplicates as saved)
            all_plating_stk_nos = saved_plating_list if saved_plating_list else []
            all_polish_stk_nos = saved_polish_list if saved_polish_list else []
            all_versions = saved_version_list if saved_version_list else []
            
            # ✅ ALSO PROVIDE UNIQUE VERSIONS for backward compatibility
            unique_plating_stk_nos = list(set(all_plating_stk_nos)) if all_plating_stk_nos else []
            unique_polish_stk_nos = list(set(all_polish_stk_nos)) if all_polish_stk_nos else []
            unique_versions = list(set(all_versions)) if all_versions else []
            
            # ✅ FALLBACK: Use single field values if no saved lists (for backward compatibility)
            if not all_plating_stk_nos and unload.plating_stk_no:
                all_plating_stk_nos = [unload.plating_stk_no]
                unique_plating_stk_nos = [unload.plating_stk_no]
                print(f"[DEBUG] Fallback: Using single plating_stk_no: {unload.plating_stk_no}")
            
            if not all_polish_stk_nos and unload.polish_stk_no:
                all_polish_stk_nos = [unload.polish_stk_no]
                unique_polish_stk_nos = [unload.polish_stk_no]
                print(f"[DEBUG] Fallback: Using single polish_stk_no: {unload.polish_stk_no}")
            
            if not all_versions and unload.version:
                version_display = getattr(unload.version, 'version_internal', str(unload.version)) if unload.version else "N/A"
                all_versions = [version_display]
                unique_versions = [version_display]
                print(f"[DEBUG] Fallback: Using single version: {version_display}")

            # ✅ BASIC MODEL DATA PROCESSING (simplified - no complex lot_id processing needed)
            model_images = {}
            model_colors = {}
            no_of_model_cases = []
            lot_id_quantities = {}
            
            if unload.combine_lot_ids:
                for lot_id in unload.combine_lot_ids:
                    try:
                        stock_model, is_recovery, batch_model_class = self.get_stock_model_data(lot_id)
                        if stock_model and hasattr(stock_model, 'model_stock_no') and stock_model.model_stock_no:
                            model_no = stock_model.model_stock_no.model_no
                            no_of_model_cases.append(model_no)
                            
                            # Get lot quantity
                            if hasattr(stock_model, 'total_stock'):
                                lot_id_quantities[lot_id] = stock_model.total_stock
                            elif hasattr(stock_model, 'stock_qty'):
                                lot_id_quantities[lot_id] = stock_model.stock_qty
                            else:
                                lot_id_quantities[lot_id] = 0
                            
                            # Use global color mapping
                            model_colors[model_no] = global_model_colors.get(model_no, "#cccccc")
                            
                            # Get model images from the pre-built map
                            if model_no in model_images_map:
                                model_images[model_no] = model_images_map[model_no]
                            else:
                                model_images[model_no] = {
                                    'images': [],
                                    'first_image': "/static/assets/images/imagePlaceholder.png"
                                }
                    except Exception as e:
                        print(f"[DEBUG] Error processing lot_id {lot_id}: {e}")
                        continue

            # Remove duplicates while preserving order
            no_of_model_cases = list(dict.fromkeys(no_of_model_cases))
            
            print(f"[DEBUG] ===== FINAL VALUES SUMMARY =====")
            print(f"[DEBUG] ALL plating_stk_nos ({len(all_plating_stk_nos)}): {all_plating_stk_nos}")
            print(f"[DEBUG] ALL polish_stk_nos ({len(all_polish_stk_nos)}): {all_polish_stk_nos}")
            print(f"[DEBUG] ALL versions ({len(all_versions)}): {all_versions}")
            print(f"[DEBUG] UNIQUE plating_stk_nos ({len(unique_plating_stk_nos)}): {unique_plating_stk_nos}")
            print(f"[DEBUG] UNIQUE polish_stk_nos ({len(unique_polish_stk_nos)}): {unique_polish_stk_nos}")
            print(f"[DEBUG] UNIQUE versions ({len(unique_versions)}): {unique_versions}")

            # Extract foreign key display values
            plating_color_display = "N/A"
            if unload.plating_color:
                plating_color_display = getattr(unload.plating_color, 'plating_color', str(unload.plating_color))
            
            polish_finish_display = "N/A"
            if unload.polish_finish:
                polish_finish_display = getattr(unload.polish_finish, 'polish_finish', str(unload.polish_finish))
            
            # Use the first version from all_versions or fallback
            version_display = all_versions[0] if all_versions else "N/A"

            # ✅ ENHANCED: Create comprehensive table data entry using SAVED LIST FIELDS
            table_entry = {
                # Basic fields
                'id': unload.id,
                'lot_id': unload.lot_id,
                'jig_qr_id': unload.jig_qr_id,
                'combine_lot_ids': unload.combine_lot_ids,
                'total_case_qty': unload.total_case_qty,
                'missing_qty': unload.missing_qty,
                
                # ✅ PRIMARY: Stock numbers (using first value from saved lists)
                'plating_stk_no': all_plating_stk_nos[0] if all_plating_stk_nos else (unload.plating_stk_no or "N/A"),
                'polish_stk_no': all_polish_stk_nos[0] if all_polish_stk_nos else (unload.polish_stk_no or "N/A"),
                
                # ✅ ALL VALUES INCLUDING DUPLICATES (from saved database fields)
                'all_plating_stk_nos': all_plating_stk_nos,
                'all_polishing_stk_nos': all_polish_stk_nos,  # Using 'polishing' to match template expectations
                'all_versions': all_versions,
                
                # ✅ UNIQUE VALUES ONLY (for backward compatibility)
                'unique_plating_stk_nos': unique_plating_stk_nos,
                'unique_polishing_stk_nos': unique_polish_stk_nos,  # Using 'polishing' to match original name
                'unique_versions': unique_versions,
                
                # Foreign key displays
                'plating_color': plating_color_display,
                'polish_finish': polish_finish_display,
                'polish_finish_name': polish_finish_display,
                'version': version_display,  # First version from ALL list or fallback
                
                # Location
                'location': location_display,
                'unique_locations': location_names if location_names else ["N/A"],
                
                # Tray information (using dynamic capacity)
                'tray_type': tray_type_display,
                'tray_capacity': tray_capacity,  # Using calculated dynamic capacity
                'jig_type': tray_type_display,  # Alias for template
                'jig_capacity': tray_capacity,  # Using calculated dynamic capacity
                'no_of_trays': no_of_trays,
                'calculated_no_of_trays': no_of_trays,
                'last_process_module': unload.last_process_module,
                
                # Dates
                'created_at': unload.created_at,
                'un_loaded_date_time': unload.Un_loaded_date_time,
                'Un_loaded_date_time': unload.Un_loaded_date_time,
                
                # Status fields
                'jig_unload_draft': False,  # Default for completed
                'electroplating_only': False,  # Default
                
                # ✅ ENHANCED: Model data with comprehensive fetching
                'no_of_model_cases': no_of_model_cases,
                'model_images': model_images,
                'model_colors': model_colors,
                'lot_id_quantities': lot_id_quantities,
                'lot_id_model_map': {},  # Can be populated if needed
                
                # Remarks
                'unloading_remarks': unloading_remarks,
                
                # Bath numbers
                'bath_numbers': {'bath_number': 'N/A'},
            }
            
            print(f"[DEBUG] ✅ Created table entry for {unload.lot_id}")
            table_data.append(table_entry)

        print(f"\n[DEBUG] ===== FINAL SUMMARY =====")
        print(f"[DEBUG] Total table_data entries: {len(table_data)}")
        
        # Add pagination with consistent logic as Inprocess Inspection
        page_number = self.request.GET.get('page', 1)
        paginator = Paginator(table_data, 10)  # 10 items per page like Inprocess Inspection
        page_obj = paginator.get_page(page_number)
        
        print(f"\n📄 Jig Unloading Completed - Pagination: Page {page_number}, Total items: {len(table_data)}")
        print(f"📄 Current page items: {len(page_obj.object_list)}")
        print(f"📄 Total pages: {paginator.num_pages}")
        
        # ✅ CONTEXT: Use consistent context variable name with pagination
        context['page_obj'] = page_obj  # For pagination controls
        context['completed_unloads'] = page_obj  # For table data
        context['debug'] = True  # Enable debug section in template (remove in production)
        
        # 🔥 NEW: Add date filter context variables
        context['from_date'] = from_date
        context['to_date'] = to_date
        
        return context

@require_GET
def get_model_images(request):
    lot_id = request.GET.get('lot_id')
    model_number = request.GET.get('model_number')  # Added optional model_number parameter
    
    if not lot_id:
        return JsonResponse({'success': False, 'error': 'lot_id required'}, status=400)
    
    try:
        print(f"[DEBUG] get_model_images called with lot_id: {lot_id}, model_number: {model_number}")
        
        # Method 1: Try to fetch polish_stk_no from JigUnloadAfterTable using lot_id
        jig_unload = JigUnloadAfterTable.objects.filter(lot_id=lot_id).first()
        
        if jig_unload and jig_unload.polish_stk_no:
            print(f"[DEBUG] Found JigUnloadAfterTable record with polish_stk_no: {jig_unload.polish_stk_no}")
            # Extract model_no before 'X'
            model_no = jig_unload.polish_stk_no.split('X')[0] if 'X' in jig_unload.polish_stk_no else jig_unload.polish_stk_no
            print(f"[DEBUG] Extracted model_no from polish_stk_no: {model_no}")
        elif model_number:
            # Method 2: Use provided model_number as fallback
            print(f"[DEBUG] No JigUnloadAfterTable record found, using provided model_number: {model_number}")
            
            # 🎯 ZONE 1 ENHANCEMENT: Extract numeric part for Zone 1 compatibility
            # Zone 1 should also extract numeric part like Zone 2 for consistency
            import re
            match = re.match(r'^(\d+)', str(model_number))
            if match:
                model_no = match.group(1)
                print(f"[DEBUG] Zone 1 - Extracted numeric part from model_number {model_number} -> {model_no}")
            else:
                model_no = model_number
                print(f"[DEBUG] Zone 1 - Using full model_number as-is: {model_no}")
        else:
            # Method 3: Try to extract from TotalStockModel
            total_stock = TotalStockModel.objects.filter(lot_id=lot_id).first()
            if total_stock and total_stock.model_stock_no:
                model_no = total_stock.model_stock_no.model_no
                print(f"[DEBUG] Found model_no from TotalStockModel: {model_no}")
            else:
                print(f"[DEBUG] No JigUnloadAfterTable, model_number, or TotalStockModel record found for lot_id: {lot_id}")
                return JsonResponse({
                    'success': False, 
                    'image': None,
                    'error': f'No model data found for lot_id: {lot_id}',
                    'debug_info': {
                        'jig_unload_found': jig_unload is not None,
                        'polish_stk_no': jig_unload.polish_stk_no if jig_unload else None,
                        'model_number_provided': model_number,
                        'total_stock_found': False
                    }
                })
        
        # Find the model in ModelMaster
        print(f"[DEBUG] Looking for ModelMaster with model_no: {model_no}")
        model = ModelMaster.objects.prefetch_related('images').filter(model_no=model_no).first()
        
        if not model:
            print(f"[DEBUG] No ModelMaster found for model_no: {model_no}")
            return JsonResponse({
                'success': False, 
                'image': None,
                'error': f'Model {model_no} not found in ModelMaster',
                'debug_info': {
                    'model_no_searched': model_no,
                    'extracted_from': 'polish_stk_no' if jig_unload and jig_unload.polish_stk_no else 'model_number_param'
                }
            })
        
        # Get all images for this model
        images = [img.master_image.url for img in model.images.all()]
        print(f"[DEBUG] Found {len(images)} images for model_no {model_no}: {images}")
        
        if images:
            return JsonResponse({
                'success': True, 
                'image': images[0],  # Return first image
                'total_images': len(images),
                'all_images': images,  # Return all images for future multi-image support
                'model_no': model_no
            })
        else:
            return JsonResponse({
                'success': False, 
                'image': None,
                'error': f'Model {model_no} found but has no images',
                'debug_info': {
                    'model_found': True,
                    'model_no': model_no,
                    'images_count': 0
                }
            })
            
    except Exception as e:
        print(f"[DEBUG] Error in get_model_images: {str(e)}")
        return JsonResponse({
            'success': False, 
            'error': str(e), 
            'image': None,
            'debug_info': {
                'exception_type': type(e).__name__,
                'exception_message': str(e)
            }
        })

@require_GET
def debug_model_availability(request):
    """
    Debug endpoint to check model availability and suggest working models
    Call this at: /jig_unloading/debug_models/
    """
    try:
        print("[DEBUG] Starting debug_model_availability analysis...")
        
        # Find models with images
        models_with_images = ModelMaster.objects.prefetch_related('images').filter(images__isnull=False).distinct()[:20]
        
        working_models = []
        for model in models_with_images:
            try:
                images = [img.master_image.url for img in model.images.all()]
                working_models.append({
                    'model_no': model.model_no,
                    'model_name': getattr(model, 'model_name', 'N/A'),
                    'image_count': len(images),
                    'first_image': images[0] if images else None
                })
                print(f"[DEBUG] Found model {model.model_no} with {len(images)} images")
            except Exception as e:
                print(f"[DEBUG] Error processing model {model.model_no}: {e}")
        
        # Check specific model 1805SSA02
        specific_model = ModelMaster.objects.filter(model_no='1805SSA02').first()
        specific_model_info = None
        if specific_model:
            try:
                images = [img.master_image.url for img in specific_model.images.all()]
                specific_model_info = {
                    'exists': True,
                    'model_no': specific_model.model_no,
                    'image_count': len(images),
                    'images': images
                }
                print(f"[DEBUG] Model 1805SSA02 exists with {len(images)} images")
            except Exception as e:
                print(f"[DEBUG] Error checking model 1805SSA02: {e}")
                specific_model_info = {'exists': True, 'error': str(e)}
        else:
            specific_model_info = {'exists': False}
            print("[DEBUG] Model 1805SSA02 does not exist")
        
        # Check lot_id data
        lot_id = 'LID021020251920110018'
        lot_data = {'lot_id': lot_id}
        
        try:
            # Check TotalStockModel
            total_stock = TotalStockModel.objects.select_related('model_stock_no').filter(lot_id=lot_id).first()
            if total_stock and total_stock.model_stock_no:
                lot_data['total_stock_model'] = total_stock.model_stock_no.model_no
                lot_data['total_stock_images'] = total_stock.model_stock_no.images.count()
                print(f"[DEBUG] TotalStockModel found: {total_stock.model_stock_no.model_no}")
            else:
                lot_data['total_stock_model'] = None
                print(f"[DEBUG] No TotalStockModel found for {lot_id}")
                
            # Also try RecoveryStockModel
            try:
                from Recovery_DP.models import RecoveryStockModel
                recovery_stock = RecoveryStockModel.objects.select_related('model_stock_no').filter(lot_id=lot_id).first()
                if recovery_stock and recovery_stock.model_stock_no:
                    lot_data['recovery_stock_model'] = recovery_stock.model_stock_no.model_no
                    lot_data['recovery_stock_images'] = recovery_stock.model_stock_no.images.count()
                    print(f"[DEBUG] RecoveryStockModel found: {recovery_stock.model_stock_no.model_no}")
                else:
                    lot_data['recovery_stock_model'] = None
                    print(f"[DEBUG] No RecoveryStockModel found for {lot_id}")
            except ImportError:
                lot_data['recovery_stock_model'] = 'Import error'
                print("[DEBUG] Could not import RecoveryStockModel")
        except Exception as e:
            print(f"[DEBUG] Error checking stock models: {e}")
            lot_data['stock_model_error'] = str(e)
        
        try:
            # Check JigUnloadAfterTable
            jig_unload = JigUnloadAfterTable.objects.filter(lot_id=lot_id).first()
            if jig_unload:
                lot_data['jig_unload_polish_stk_no'] = jig_unload.polish_stk_no
                print(f"[DEBUG] JigUnloadAfterTable found: polish_stk_no = {jig_unload.polish_stk_no}")
                if jig_unload.polish_stk_no:
                    extracted_model = jig_unload.polish_stk_no.split('X')[0] if 'X' in jig_unload.polish_stk_no else jig_unload.polish_stk_no
                    lot_data['extracted_model'] = extracted_model
                    # Check if extracted model exists
                    extracted_model_obj = ModelMaster.objects.filter(model_no=extracted_model).first()
                    if extracted_model_obj:
                        lot_data['extracted_model_exists'] = True
                        lot_data['extracted_model_images'] = extracted_model_obj.images.count()
                        print(f"[DEBUG] Extracted model {extracted_model} exists with {extracted_model_obj.images.count()} images")
                    else:
                        lot_data['extracted_model_exists'] = False
                        print(f"[DEBUG] Extracted model {extracted_model} does not exist")
            else:
                lot_data['jig_unload_polish_stk_no'] = None
                print(f"[DEBUG] No JigUnloadAfterTable found for {lot_id}")
        except Exception as e:
            print(f"[DEBUG] Error checking JigUnloadAfterTable: {e}")
            lot_data['jig_unload_error'] = str(e)
        
        # Get some sample lot_ids that might have working models
        sample_working_lot_ids = []
        try:
            # Find lot_ids from TotalStockModel that have models with images
            for model in working_models[:5]:
                sample_lots = TotalStockModel.objects.filter(
                    model_stock_no__model_no=model['model_no']
                ).values_list('lot_id', flat=True)[:2]
                sample_working_lot_ids.extend(list(sample_lots))
            print(f"[DEBUG] Found sample working lot_ids: {sample_working_lot_ids}")
        except Exception as e:
            print(f"[DEBUG] Error finding sample lot_ids: {e}")
        
        return JsonResponse({
            'success': True,
            'total_models_with_images': models_with_images.count(),
            'working_models': working_models,
            'specific_model_1805SSA02': specific_model_info,
            'lot_id_analysis': lot_data,
            'sample_working_lot_ids': sample_working_lot_ids,
            'recommendations': {
                'solution_1': 'Add images to model 1805SSA02 in Django admin',
                'solution_2': f'Test with working models: {[m["model_no"] for m in working_models[:3]]}' if working_models else 'No models with images found',
                'solution_3': f'Test with these lot_ids that have images: {sample_working_lot_ids[:3]}' if sample_working_lot_ids else 'No working lot_ids found',
                'solution_4': 'Create model 1805SSA02 in ModelMaster and add images'
            },
            'next_steps': [
                f'Go to http://localhost:8000/admin/',
                'Navigate to: modelmasterapp → Model masters',
                'Search for "1805SSA02" or create it if not found',
                'Add images to the model',
                'Test the image display functionality'
            ]
        })
        
    except Exception as e:
        print(f"[DEBUG] Error in debug_model_availability: {str(e)}")
        import traceback
        print(f"[DEBUG] Traceback: {traceback.format_exc()}")
        return JsonResponse({
            'success': False,
            'error': str(e),
            'message': 'Error analyzing model availability',
            'traceback': traceback.format_exc()
        })

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def jig_after_view_tray_list(request):
    """
    Returns tray list for all lot_ids in combine_lot_ids for the given stock_lot_id (UNLOT ID) from JigUnloadAfterTable.
    """
    stock_lot_id = request.GET.get('stock_lot_id')

    if not stock_lot_id or stock_lot_id in ['None', 'null', '']:
        return Response({
            'success': False, 
            'error': 'Valid stock_lot_id parameter is required'
        }, status=400)

    try:
        print(f"[DEBUG] Received stock_lot_id: {stock_lot_id}")
        
        # Find the JigUnloadAfterTable record by the auto-generated lot_id (UNLOT ID)
        jig_unload_record = JigUnloadAfterTable.objects.filter(lot_id=stock_lot_id).first()
        
        if not jig_unload_record:
            print(f"[DEBUG] JigUnloadAfterTable record not found for lot_id: {stock_lot_id}")
            return Response({
                'success': False, 
                'error': f'JigUnloadAfterTable record not found for lot_id: {stock_lot_id}'
            }, status=404)

        # Get all lot_ids from combine_lot_ids
        combine_lot_ids = jig_unload_record.combine_lot_ids or []
        print(f"[DEBUG] Found combine_lot_ids: {combine_lot_ids}")
        
        if not combine_lot_ids:
            return Response({
                'success': False, 
                'error': 'No combine_lot_ids found in the record'
            }, status=404)

        # Query all trays for these combine_lot_ids from JigUnload_TrayId
        trays = JigUnload_TrayId.objects.filter(lot_id__in=combine_lot_ids).order_by('id')
        print(f"[DEBUG] Found {trays.count()} trays for combine_lot_ids")
        
        tray_list = []
        for idx, tray in enumerate(trays):
            tray_list.append({
                'sno': idx + 1,
                'tray_id': tray.tray_id,
                'tray_qty': tray.tray_qty,
                'lot_id': tray.lot_id,  # This will be one of the original lot_ids
                'is_top_tray': idx == 0  # Mark first tray as top tray
            })

        return Response({
            'success': True,
            'unlot_id': stock_lot_id,  # The UNLOT ID that was searched
            'combine_lot_ids': combine_lot_ids,  # All the original lot IDs
            'total_trays': len(tray_list),
            'trays': tray_list,
        })
        
    except Exception as e:
        print(f"[DEBUG] ERROR in jig_after_view_tray_list: {str(e)}")
        import traceback
        traceback.print_exc()
        return Response({
            'success': False, 
            'error': str(e)
        }, status=500)


@method_decorator(csrf_exempt, name='dispatch')
class JigAfterTrayValidateAPIView(APIView):
    def post(self, request):
        try:
            data = request.data if hasattr(request, 'data') else json.loads(request.body.decode('utf-8'))
            lot_id = data.get('lot_id', '').strip()  # This is the UNLOT ID
            tray_id = str(data.get('tray_id', '')).strip()

            print(f"[DEBUG] Raw request data: {data}")
            print(f"[DEBUG] Extracted lot_id: '{lot_id}'")
            print(f"[DEBUG] Extracted tray_id: '{tray_id}'")

            if not lot_id or not tray_id:
                return JsonResponse({
                    'success': False, 
                    'error': 'Both lot_id and tray_id are required'
                }, status=400)

            # Get combine_lot_ids for this UNLOT ID
            jig_unload_record = JigUnloadAfterTable.objects.filter(lot_id=lot_id).first()
            if not jig_unload_record or not jig_unload_record.combine_lot_ids:
                print(f"[DEBUG] No JigUnloadAfterTable or combine_lot_ids found for lot_id: {lot_id}")
                return JsonResponse({
                    'success': False, 
                    'error': 'No combine_lot_ids found for this UNLOT ID'
                }, status=404)


            # Check for tray in any of the original lot_ids
            tray_record = JigUnload_TrayId.objects.filter(
                tray_id=tray_id,
            ).first()

            tray_exists = tray_record is not None
            print(f"[DEBUG] Tray exists in JigUnload_TrayId: {tray_exists}")

            if tray_record:
                print(f"[DEBUG] Tray '{tray_id}' found for lot_id: {tray_record.lot_id} with qty: {tray_record.tray_qty}")

            print(f"[DEBUG] Final result - exists: {tray_exists}")
            print("="*50)

            return JsonResponse({
                'success': True, 
                'exists': tray_exists,
                'tray_info': {
                    'tray_id': tray_record.tray_id,
                    'tray_qty': tray_record.tray_qty,
                    'lot_id': tray_record.lot_id
                } if tray_record else None
            })

        except Exception as e:
            print(f"[DEBUG] ERROR in JigAfterTrayValidateAPIView: {str(e)}")
            import traceback
            traceback.print_exc()
            return JsonResponse({
                'success': False, 
                'error': str(e)
            }, status=500)


# ==================== AUTO-SAVE FUNCTIONALITY ====================

@csrf_exempt 
def autosave_jig_unload(request):
    """Auto-save jig unload modal data on typing/changes"""
    import json
    import logging
    from .models import JigUnloadAutoSave
    
    logger = logging.getLogger(__name__)
    
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            
            main_lot_id = data.get('main_lot_id', '')
            if not main_lot_id:
                return JsonResponse({'success': False, 'error': 'main_lot_id required'})
            
            # Get user or session key
            user = request.user if request.user.is_authenticated else None
            session_key = request.session.session_key if not user else None
            
            if not user and not session_key:
                # Create session if it doesn't exist
                request.session.create()
                session_key = request.session.session_key
            
            # Update or create auto-save record
            filter_kwargs = {'main_lot_id': main_lot_id}
            if user:
                filter_kwargs['user'] = user
                defaults = {'session_key': None}
            else:
                filter_kwargs['session_key'] = session_key
                defaults = {'user': None}
            
            # Add all the data fields to defaults
            defaults.update({
                'model_number': data.get('model_number', ''),
                'total_quantity': data.get('total_quantity', 0),
                'tray_data': data.get('tray_data', []),
                'combined_lot_ids': data.get('combined_lot_ids', []),
                'tray_type_capacity': data.get('tray_type_capacity', 'Normal - 16'),
                'missing_qty': data.get('missing_qty', 0),
                'jig_id': data.get('jig_id', ''),
            })
            
            autosave, created = JigUnloadAutoSave.objects.update_or_create(
                **filter_kwargs,
                defaults=defaults
            )
            
            logger.info(f"Auto-save {'created' if created else 'updated'} for lot_id: {main_lot_id}")
            
            return JsonResponse({
                'success': True,
                'action': 'created' if created else 'updated',
                'timestamp': autosave.updated_at.isoformat()
            })
            
        except json.JSONDecodeError as e:
            logger.error(f'Auto-save JSON decode error: {e}')
            return JsonResponse({'success': False, 'error': 'Invalid JSON data'})
        except Exception as e:
            logger.error(f'Auto-save error: {e}', exc_info=True)
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'POST method required'})


@csrf_exempt
def load_autosave_jig_unload(request, main_lot_id):
    """Load auto-saved data for a specific lot_id"""
    import logging
    from .models import JigUnloadAutoSave
    
    logger = logging.getLogger(__name__)
    
    try:
        # Get user or session key
        user = request.user if request.user.is_authenticated else None
        session_key = request.session.session_key if not user else None
        
        # Build filter for auto-save lookup
        filter_kwargs = {'main_lot_id': main_lot_id}
        if user:
            filter_kwargs['user'] = user
        elif session_key:
            filter_kwargs['session_key'] = session_key
        else:
            return JsonResponse({
                'success': True,
                'has_autosave': False,
                'message': 'No user session found'
            })
        
        autosave = JigUnloadAutoSave.objects.filter(**filter_kwargs).first()
        
        if autosave and autosave.has_meaningful_data():
            return JsonResponse({
                'success': True,
                'has_autosave': True,
                'autosave_data': autosave.to_dict(),
                'last_updated': autosave.updated_at.isoformat()
            })
        else:
            return JsonResponse({
                'success': True,
                'has_autosave': False
            })
            
    except Exception as e:
        logger.error(f'Load auto-save error: {e}', exc_info=True)
        return JsonResponse({'success': False, 'error': str(e)})


@csrf_exempt
def clear_autosave_jig_unload(request, main_lot_id):
    """Clear auto-save data after successful submit/draft"""
    import logging
    from .models import JigUnloadAutoSave
    
    logger = logging.getLogger(__name__)
    
    if request.method == 'DELETE':
        try:
            # Get user or session key
            user = request.user if request.user.is_authenticated else None
            session_key = request.session.session_key if not user else None
            
            # Build filter for auto-save deletion
            filter_kwargs = {'main_lot_id': main_lot_id}
            if user:
                filter_kwargs['user'] = user
            elif session_key:
                filter_kwargs['session_key'] = session_key
            else:
                return JsonResponse({'success': True, 'message': 'No session found'})
            
            deleted_count, _ = JigUnloadAutoSave.objects.filter(**filter_kwargs).delete()
            
            logger.info(f"Cleared {deleted_count} auto-save record(s) for lot_id: {main_lot_id}")
            
            return JsonResponse({
                'success': True,
                'deleted_count': deleted_count
            })
            
        except Exception as e:
            logger.error(f'Clear auto-save error: {e}', exc_info=True)
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'DELETE method required'})


@csrf_exempt
@require_POST
def delete_jig_details(request):
    """
    Delete a jig record from JigCompleted table
    """
    try:
        data = json.loads(request.body)
        lot_id = data.get('lot_id')
        
        if not lot_id:
            return JsonResponse({
                'success': False, 
                'message': 'Lot ID is required'
            })
        
        # Find the jig detail record
        jig_detail = JigCompleted.objects.filter(lot_id=lot_id).first()
        
        if not jig_detail:
            return JsonResponse({
                'success': False,
                'message': f'No record found with Lot ID: {lot_id}'
            })
        
        # Check if the jig has any dependent records that should be preserved
        # You might want to add validation here based on business rules
        
        # Delete the record
        jig_detail.delete()
        
        return JsonResponse({
            'success': True,
            'message': f'Record with Lot ID {lot_id} deleted successfully'
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'message': 'Invalid JSON data'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'Error deleting record: {str(e)}'
        })

@require_GET
def get_jig_for_tray(request):
    """Get jig information for a scanned tray ID"""
    tray_id = request.GET.get('tray_id', '').strip()
    
    if not tray_id:
        return JsonResponse({
            'success': False,
            'error': 'Tray ID is required'
        })
    
    try:
        # Find JigCompleted that contain this tray_id in their lot_id_quantities
        jig_detail = JigCompleted.objects.filter(
            lot_id_quantities__has_key=tray_id
        ).first()
        
        if not jig_detail:
            return JsonResponse({
                'success': False,
                'error': 'Tray ID not found in any jig'
            })
        
        # Check if this jig is in draft mode
        is_draft = bool(jig_detail.jig_unload_draft)
        
        return JsonResponse({
            'success': True,
            'jig_id': jig_detail.id,
            'lot_id': jig_detail.lot_id,
            'is_draft': is_draft
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'System error: {str(e)}'
        })