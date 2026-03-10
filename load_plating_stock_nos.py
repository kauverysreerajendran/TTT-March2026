"""
Management command to load 44 Plating Stock Numbers into ModelMaster
Based on the Excel data provided

Usage:
    python manage.py load_plating_stock_nos
"""

from django.core.management.base import BaseCommand
from modelmasterapp.models import ModelMaster, PolishFinishType, TrayType, Vendor
from django.contrib.auth.models import User


class Command(BaseCommand):
    help = 'Loads 44 Plating Stock Numbers from Excel into ModelMaster'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting to load Plating Stock Numbers...'))
        self.stdout.write(self.style.SUCCESS('Creating missing Version entries if needed...'))

        # Get or create default references
        admin_user = User.objects.filter(is_superuser=True).first()
        
        # Get or create Polish Finish Types
        shotblasting, _ = PolishFinishType.objects.get_or_create(
            polish_internal='S',
            defaults={'polish_finish': 'Shotblasting (S)'}
        )
        buffed, _ = PolishFinishType.objects.get_or_create(
            polish_internal='A',
            defaults={'polish_finish': 'Buffed (A)'}
        )
        bi_finish, _ = PolishFinishType.objects.get_or_create(
            polish_internal='B',
            defaults={'polish_finish': 'Bi-Finish (B)'}
        )
        # Create PolishFinishType for C with unique name but same meaning as B
        chrome_finish, _ = PolishFinishType.objects.get_or_create(
            polish_internal='C',
            defaults={'polish_finish': 'Bi-Finish (C)'}
        )
        
        # Get or create missing Version entries
        from modelmasterapp.models import Version
        
        # Get existing versions or create missing ones
        try:
            version_a = Version.objects.get(version_internal='A')
        except Version.DoesNotExist:
            version_a = Version.objects.create(version_name='A', version_internal='A', createdby=admin_user)
            
        try:
            version_d = Version.objects.get(version_internal='D')
        except Version.DoesNotExist:
            version_d = Version.objects.create(version_name='D', version_internal='D', createdby=admin_user)
            
        try:
            version_e = Version.objects.get(version_internal='E')
        except Version.DoesNotExist:
            version_e = Version.objects.create(version_name='E', version_internal='E', createdby=admin_user)
            
        try:
            version_f = Version.objects.get(version_internal='F')
        except Version.DoesNotExist:
            version_f = Version.objects.create(version_name='F', version_internal='F', createdby=admin_user)
            
        try:
            version_k = Version.objects.get(version_internal='K')
        except Version.DoesNotExist:
            version_k = Version.objects.create(version_name='K', version_internal='K', createdby=admin_user)
            
        try:
            version_r = Version.objects.get(version_internal='R')
        except Version.DoesNotExist:
            version_r = Version.objects.create(version_name='R', version_internal='R', createdby=admin_user)
        
        # Create missing versions B, C, L, P that don't exist yet
        version_b, created_b = Version.objects.get_or_create(
            version_internal='B',
            defaults={'version_name': 'B', 'createdby': admin_user}
        )
        version_c, created_c = Version.objects.get_or_create(
            version_internal='C',
            defaults={'version_name': 'C', 'createdby': admin_user}
        )
        version_l, created_l = Version.objects.get_or_create(
            version_internal='L',
            defaults={'version_name': 'L', 'createdby': admin_user}
        )
        version_p, created_p = Version.objects.get_or_create(
            version_internal='P',
            defaults={'version_name': 'P', 'createdby': admin_user}
        )
        
        # Report created versions
        created_versions = []
        if created_b:
            created_versions.append('B')
        if created_c:
            created_versions.append('C')
        if created_l:
            created_versions.append('L')
        if created_p:
            created_versions.append('P')
        
        if created_versions:
            self.stdout.write(self.style.SUCCESS(f'✅ Created missing versions: {", ".join(created_versions)}'))
        else:
            self.stdout.write(self.style.SUCCESS('✅ All required versions already exist'))
        
        # Get or create Tray Types
        normal_tray, _ = TrayType.objects.get_or_create(
            tray_type='Normal',
            defaults={'tray_capacity': 16}
        )
        jumbo_tray, _ = TrayType.objects.get_or_create(
            tray_type='Jumbo',
            defaults={'tray_capacity': 12}
        )
        
        # Get or create Vendors
        demo_vendor, _ = Vendor.objects.get_or_create(
            vendor_internal='Demo',
            defaults={'vendor_name': 'Demo Vendor'}
        )
        demo2_vendor, _ = Vendor.objects.get_or_create(
            vendor_internal='Demo2',
            defaults={'vendor_name': 'Demo2 Vendor'}
        )

        # Plating Stock Numbers from Excel - with calculated polishing stock numbers
        plating_data = [
            # Model 2617 entries  
            {'plating_stk_no': '2617SAA02', 'polishing_stk_no': '2617XAA02', 'model_no': '2617', 'version': version_a, 'polish': buffed, 'bath': 'Bright', 'tray': normal_tray, 'vendor': demo2_vendor, 'brand': 'Titan', 'wiping': False},
            {'plating_stk_no': '2617WAA02', 'polishing_stk_no': '2617XAA02', 'model_no': '2617', 'version': version_a, 'polish': buffed, 'bath': 'Bright', 'tray': normal_tray, 'vendor': demo2_vendor, 'brand': 'Titan', 'wiping': False},
            {'plating_stk_no': '2617SAB02', 'polishing_stk_no': '2617XAB02', 'model_no': '2617', 'version': version_b, 'polish': buffed, 'bath': 'Bright', 'tray': normal_tray, 'vendor': demo2_vendor, 'brand': 'Titan', 'wiping': False},
            {'plating_stk_no': '2617WAB02', 'polishing_stk_no': '2617XAB02', 'model_no': '2617', 'version': version_b, 'polish': buffed, 'bath': 'Bright', 'tray': normal_tray, 'vendor': demo2_vendor, 'brand': 'Titan', 'wiping': False},
            {'plating_stk_no': '2617WAC02', 'polishing_stk_no': '2617XAC02', 'model_no': '2617', 'version': version_c, 'polish': buffed, 'bath': 'Bright', 'tray': normal_tray, 'vendor': demo2_vendor, 'brand': 'Titan', 'wiping': False},
            {'plating_stk_no': '2617YAC02/2N', 'polishing_stk_no': '2617XAC02', 'model_no': '2617', 'version': version_c, 'polish': buffed, 'bath': 'Bright', 'tray': normal_tray, 'vendor': demo2_vendor, 'brand': 'Titan', 'wiping': False},
            {'plating_stk_no': '2617NAD02', 'polishing_stk_no': '2617XAD02', 'model_no': '2617', 'version': version_d, 'polish': buffed, 'bath': 'Bright', 'tray': normal_tray, 'vendor': demo2_vendor, 'brand': 'Titan', 'wiping': False},
            {'plating_stk_no': '2617SAD02', 'polishing_stk_no': '2617XAD02', 'model_no': '2617', 'version': version_d, 'polish': buffed, 'bath': 'Bright', 'tray': normal_tray, 'vendor': demo2_vendor, 'brand': 'Titan', 'wiping': False},
            {'plating_stk_no': '2617YAD02/2N', 'polishing_stk_no': '2617XAD02', 'model_no': '2617', 'version': version_d, 'polish': buffed, 'bath': 'Bright', 'tray': normal_tray, 'vendor': demo2_vendor, 'brand': 'Titan', 'wiping': False},
            {'plating_stk_no': '2617NSA02', 'polishing_stk_no': '2617XSA02', 'model_no': '2617', 'version': version_a, 'polish': shotblasting, 'bath': 'Dull', 'tray': normal_tray, 'vendor': demo2_vendor, 'brand': 'Titan', 'wiping': True},
            
            # Model 2648 entries
            {'plating_stk_no': '2648NAA02', 'polishing_stk_no': '2648XAA02', 'model_no': '2648', 'version': version_a, 'polish': buffed, 'bath': 'Bright', 'tray': normal_tray, 'vendor': demo2_vendor, 'brand': 'Vista', 'wiping': False},
            {'plating_stk_no': '2648QAA02/BRN', 'polishing_stk_no': '2648XAA02', 'model_no': '2648', 'version': version_a, 'polish': buffed, 'bath': 'Bright', 'tray': normal_tray, 'vendor': demo2_vendor, 'brand': 'Vista', 'wiping': False},
            {'plating_stk_no': '2648SAA02', 'polishing_stk_no': '2648XAA02', 'model_no': '2648', 'version': version_a, 'polish': buffed, 'bath': 'Bright', 'tray': normal_tray, 'vendor': demo2_vendor, 'brand': 'Vista', 'wiping': False},
            {'plating_stk_no': '2648WAA02', 'polishing_stk_no': '2648XAA02', 'model_no': '2648', 'version': version_a, 'polish': buffed, 'bath': 'Bright', 'tray': normal_tray, 'vendor': demo2_vendor, 'brand': 'Vista', 'wiping': False},
            {'plating_stk_no': '2648YAA02/2N', 'polishing_stk_no': '2648XAA02', 'model_no': '2648', 'version': version_a, 'polish': buffed, 'bath': 'Bright', 'tray': normal_tray, 'vendor': demo2_vendor, 'brand': 'Vista', 'wiping': False},
            {'plating_stk_no': '2648KAB02/RGSS', 'polishing_stk_no': '2648XAB02', 'model_no': '2648', 'version': version_b, 'polish': buffed, 'bath': 'Bright', 'tray': normal_tray, 'vendor': demo2_vendor, 'brand': 'Vista', 'wiping': False},
            {'plating_stk_no': '2648QAB02/GUN', 'polishing_stk_no': '2648XAB02', 'model_no': '2648', 'version': version_b, 'polish': buffed, 'bath': 'Bright', 'tray': normal_tray, 'vendor': demo2_vendor, 'brand': 'Vista', 'wiping': False},
            {'plating_stk_no': '2648SAB02', 'polishing_stk_no': '2648XAB02', 'model_no': '2648', 'version': version_b, 'polish': buffed, 'bath': 'Bright', 'tray': normal_tray, 'vendor': demo2_vendor, 'brand': 'Vista', 'wiping': False},
            {'plating_stk_no': '2648WAB02', 'polishing_stk_no': '2648XAB02', 'model_no': '2648', 'version': version_b, 'polish': buffed, 'bath': 'Bright', 'tray': normal_tray, 'vendor': demo2_vendor, 'brand': 'Vista', 'wiping': False},
            {'plating_stk_no': '2648QAD02/BRN', 'polishing_stk_no': '2648XAD02', 'model_no': '2648', 'version': version_d, 'polish': buffed, 'bath': 'Bright', 'tray': normal_tray, 'vendor': demo2_vendor, 'brand': 'Vista', 'wiping': False},
            {'plating_stk_no': '2648SAD02', 'polishing_stk_no': '2648XAD02', 'model_no': '2648', 'version': version_d, 'polish': buffed, 'bath': 'Bright', 'tray': normal_tray, 'vendor': demo2_vendor, 'brand': 'Vista', 'wiping': False},
            {'plating_stk_no': '2648WAD02', 'polishing_stk_no': '2648XAD02', 'model_no': '2648', 'version': version_d, 'polish': buffed, 'bath': 'Bright', 'tray': normal_tray, 'vendor': demo2_vendor, 'brand': 'Vista', 'wiping': False},
            {'plating_stk_no': '2648SAE02', 'polishing_stk_no': '2648XAE02', 'model_no': '2648', 'version': version_e, 'polish': buffed, 'bath': 'Bright', 'tray': normal_tray, 'vendor': demo2_vendor, 'brand': 'Vista', 'wiping': False},
            {'plating_stk_no': '2648WAE02', 'polishing_stk_no': '2648XAE02', 'model_no': '2648', 'version': version_e, 'polish': buffed, 'bath': 'Bright', 'tray': normal_tray, 'vendor': demo2_vendor, 'brand': 'Vista', 'wiping': False},
            {'plating_stk_no': '2648QAF02/BRN', 'polishing_stk_no': '2648XAF02', 'model_no': '2648', 'version': version_f, 'polish': buffed, 'bath': 'Bright', 'tray': normal_tray, 'vendor': demo2_vendor, 'brand': 'Vista', 'wiping': False},
            {'plating_stk_no': '2648SAF02', 'polishing_stk_no': '2648XAF02', 'model_no': '2648', 'version': version_f, 'polish': buffed, 'bath': 'Bright', 'tray': normal_tray, 'vendor': demo2_vendor, 'brand': 'Vista', 'wiping': False},
            {'plating_stk_no': '2648WAF02', 'polishing_stk_no': '2648XAF02', 'model_no': '2648', 'version': version_f, 'polish': buffed, 'bath': 'Bright', 'tray': normal_tray, 'vendor': demo2_vendor, 'brand': 'Vista', 'wiping': False},
            {'plating_stk_no': '2648QAE02/BRN', 'polishing_stk_no': '2648XAE02', 'model_no': '2648', 'version': version_e, 'polish': buffed, 'bath': 'Bright', 'tray': normal_tray, 'vendor': demo2_vendor, 'brand': 'Vista', 'wiping': False},
            
            # Model 1805 entries
            {'plating_stk_no': '1805NAA02', 'polishing_stk_no': '1805XAA02', 'model_no': '1805', 'version': version_a, 'polish': buffed, 'bath': 'Bright', 'tray': jumbo_tray, 'vendor': demo_vendor, 'brand': 'Vista', 'wiping': False},
            {'plating_stk_no': '1805SAA02', 'polishing_stk_no': '1805XAA02', 'model_no': '1805', 'version': version_a, 'polish': buffed, 'bath': 'Bright', 'tray': jumbo_tray, 'vendor': demo_vendor, 'brand': 'Vista', 'wiping': False},
            {'plating_stk_no': '1805WAA02', 'polishing_stk_no': '1805XAA02', 'model_no': '1805', 'version': version_a, 'polish': buffed, 'bath': 'Bright', 'tray': jumbo_tray, 'vendor': demo_vendor, 'brand': 'Vista', 'wiping': False},
            {'plating_stk_no': '1805NAD02', 'polishing_stk_no': '1805XAD02', 'model_no': '1805', 'version': version_d, 'polish': buffed, 'bath': 'Bright', 'tray': jumbo_tray, 'vendor': demo_vendor, 'brand': 'Vista', 'wiping': False},
            {'plating_stk_no': '1805QAD02/GUN', 'polishing_stk_no': '1805XAD02', 'model_no': '1805', 'version': version_d, 'polish': buffed, 'bath': 'Bright', 'tray': jumbo_tray, 'vendor': demo_vendor, 'brand': 'Vista', 'wiping': False},
            {'plating_stk_no': '1805SAD02', 'polishing_stk_no': '1805XAD02', 'model_no': '1805', 'version': version_d, 'polish': buffed, 'bath': 'Bright', 'tray': jumbo_tray, 'vendor': demo_vendor, 'brand': 'Vista', 'wiping': False},
            {'plating_stk_no': '1805NAK02', 'polishing_stk_no': '1805XAK02', 'model_no': '1805', 'version': version_k, 'polish': buffed, 'bath': 'Bright', 'tray': jumbo_tray, 'vendor': demo_vendor, 'brand': 'Vista', 'wiping': False},
            {'plating_stk_no': '1805SAK02', 'polishing_stk_no': '1805XAK02', 'model_no': '1805', 'version': version_k, 'polish': buffed, 'bath': 'Bright', 'tray': jumbo_tray, 'vendor': demo_vendor, 'brand': 'Vista', 'wiping': False},
            {'plating_stk_no': '1805WAK02', 'polishing_stk_no': '1805XAK02', 'model_no': '1805', 'version': version_k, 'polish': buffed, 'bath': 'Bright', 'tray': jumbo_tray, 'vendor': demo_vendor, 'brand': 'Vista', 'wiping': False},
            {'plating_stk_no': '1805YAK02/2N', 'polishing_stk_no': '1805XAK02', 'model_no': '1805', 'version': version_k, 'polish': buffed, 'bath': 'Bright', 'tray': jumbo_tray, 'vendor': demo_vendor, 'brand': 'Vista', 'wiping': False},
            {'plating_stk_no': '1805NAR02', 'polishing_stk_no': '1805XAR02', 'model_no': '1805', 'version': version_r, 'polish': buffed, 'bath': 'Bright', 'tray': jumbo_tray, 'vendor': demo_vendor, 'brand': 'Vista', 'wiping': False},
            {'plating_stk_no': '1805QBK02/GUN', 'polishing_stk_no': '1805XBK02', 'model_no': '1805', 'version': version_k, 'polish': bi_finish, 'bath': 'Semi Bright', 'tray': jumbo_tray, 'vendor': demo_vendor, 'brand': 'Vista', 'wiping': True},
            {'plating_stk_no': '1805WBK02', 'polishing_stk_no': '1805XBK02', 'model_no': '1805', 'version': version_k, 'polish': bi_finish, 'bath': 'Semi Bright', 'tray': jumbo_tray, 'vendor': demo_vendor, 'brand': 'Vista', 'wiping': True},
            {'plating_stk_no': '1805QCL02/GUN', 'polishing_stk_no': '1805XCL02', 'model_no': '1805', 'version': version_l, 'polish': chrome_finish, 'bath': 'Semi Bright', 'tray': jumbo_tray, 'vendor': demo_vendor, 'brand': 'Vista', 'wiping': True},
            {'plating_stk_no': '1805QSP02/GUN', 'polishing_stk_no': '1805XSP02', 'model_no': '1805', 'version': version_p, 'polish': shotblasting, 'bath': 'Dull', 'tray': jumbo_tray, 'vendor': demo_vendor, 'brand': 'Vista', 'wiping': True},
            {'plating_stk_no': '1805BAA02', 'polishing_stk_no': '1805XAA02', 'model_no': '1805', 'version': version_b, 'polish': buffed, 'bath': 'Bright', 'tray': jumbo_tray, 'vendor': demo_vendor, 'brand': 'Vista', 'wiping': False},  # Fixed: B1805SAA02 → 1805BAA02 with ColorCode 'B'
        ]

        created_count = 0
        updated_count = 0
        skipped_count = 0

        for data in plating_data:
            try:
                # Check if plating_stk_no already exists
                existing = ModelMaster.objects.filter(plating_stk_no=data['plating_stk_no']).first()
                
                if existing:
                    # Update existing record
                    existing.model_no = data['model_no']
                    existing.version = data['version'].version_name  # Use version_name from Version object
                    existing.polish_finish = data['polish']
                    existing.ep_bath_type = data['bath']
                    existing.tray_type = data['tray']
                    existing.tray_capacity = data['tray'].tray_capacity
                    existing.vendor_internal = data['vendor']
                    existing.brand = data['brand']
                    existing.wiping_required = data['wiping']
                    existing.polishing_stk_no = data['polishing_stk_no']  # Added polishing stock number
                    existing.createdby = admin_user
                    existing.save()
                    updated_count += 1
                    self.stdout.write(self.style.WARNING(f'Updated: {data["plating_stk_no"]}'))
                else:
                    # Create new record
                    ModelMaster.objects.create(
                        model_no=data['model_no'],
                        plating_stk_no=data['plating_stk_no'],
                        polishing_stk_no=data['polishing_stk_no'],  # Added polishing stock number
                        version=data['version'].version_name,  # Use version_name from Version object
                        polish_finish=data['polish'],
                        ep_bath_type=data['bath'],
                        tray_type=data['tray'],
                        tray_capacity=data['tray'].tray_capacity,
                        vendor_internal=data['vendor'],
                        brand=data['brand'],
                        wiping_required=data['wiping'],
                        createdby=admin_user
                    )
                    created_count += 1
                    self.stdout.write(self.style.SUCCESS(f'Created: {data["plating_stk_no"]}'))
                    
            except Exception as e:
                skipped_count += 1
                self.stdout.write(self.style.ERROR(f'Error with {data["plating_stk_no"]}: {str(e)}'))

        # Summary
        self.stdout.write(self.style.SUCCESS('\n' + '='*60))
        self.stdout.write(self.style.SUCCESS('LOADING COMPLETE!'))
        self.stdout.write(self.style.SUCCESS('='*60))
        if created_versions:
            self.stdout.write(self.style.SUCCESS(f'📝 Created missing versions: {", ".join(created_versions)}'))
        self.stdout.write(self.style.SUCCESS(f'✅ Created: {created_count} records'))
        self.stdout.write(self.style.WARNING(f'🔄 Updated: {updated_count} records'))
        self.stdout.write(self.style.ERROR(f'❌ Skipped: {skipped_count} records'))
        self.stdout.write(self.style.SUCCESS('='*60))
        self.stdout.write(self.style.SUCCESS(f'\n📊 Total Plating Stock Numbers in database: {ModelMaster.objects.count()}'))
        
        # Final success message
        if skipped_count == 0:
            self.stdout.write(self.style.SUCCESS(f'\n🎉 ALL 44 PLATING STOCK NUMBERS LOADED SUCCESSFULLY!'))
