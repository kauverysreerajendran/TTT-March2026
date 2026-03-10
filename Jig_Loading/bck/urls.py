from django.urls import path
from . import views
from .views import *
urlpatterns = [
    path('JigView/', JigView.as_view(), name='JigView'),
    path('JigCompletedTable/', JigCompletedTable.as_view(), name='JigCompletedTable'),
    path('tray-info/', TrayInfoView.as_view(), name='tray_info'),
    path('tray-validate/', TrayValidateAPIView.as_view(), name='tray_validate'),
    path('jig-add-modal-data/', JigAddModalDataView.as_view(), name='jig_add_modal_data'),
    path('delink-table/', DelinkTableAPIView.as_view(), name='delink_table_api'),
    path('validate-tray-id/', views.validate_tray_id, name='validate_tray_id'),
    
    # NEW PRIMARY SYSTEM: JigDraft API endpoints - Perfect Accountability
    path('jig-draft-create/', JigDraftCreateAPIView.as_view(), name='jig_draft_create'),
    path('jig-draft-update/', JigDraftUpdateAPIView.as_view(), name='jig_draft_update'),
    path('jig-draft-retrieve/', JigDraftRetrieveAPIView.as_view(), name='jig_draft_retrieve'),
    path('jig-draft-validate/', JigDraftValidateAPIView.as_view(), name='jig_draft_validate'),
    
    # LEGACY (deprecated - will be removed)
    path('manual-draft/', JigLoadingManualDraftAPIView.as_view(), name='jig_loading_manual_draft'),
    path('manual-draft-fetch/', JigLoadingManualDraftFetchAPIView.as_view(), name='jig_loading_manual_draft_fetch'),
    path('jig-submit/', JigSubmitAPIView.as_view(), name='jig_submit'),
    path('validate-lock-jig-id/', views.validate_lock_jig_id, name='validate_lock_jig_id'),
    path('jig_tray_id_list/', views.jig_tray_id_list, name='jig_tray_id_list'),
    path('update-jig-draft-status/<str:batch_id>/<str:lot_id>/', views.update_jig_draft_status, name='update_jig_draft_status'),
    path('add-model-pick-table/', AddModelPickTableView.as_view(), name='add_model_pick_table'),
    path('add-selected-models/', AddSelectedModelsAPIView.as_view(), name='add_selected_models'),

]