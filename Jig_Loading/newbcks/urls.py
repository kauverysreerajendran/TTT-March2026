from django.urls import path
from . import views
from .views import *
urlpatterns = [
    path('JigView/', JigView.as_view(), name='JigView'),
    path('JigCompletedTable/', JigCompletedTable.as_view(), name='JigCompletedTable'),
    path('tray-info/', TrayInfoView.as_view(), name='tray_info'),
    path('tray-validate/', TrayValidateAPIView.as_view(), name='tray_validate'),
    path('jig-add-modal-data/', JigAddModalDataView.as_view(), name='jig_add_modal_data'),
    path('validate-lock-jig-id/', views.validate_lock_jig_id, name='validate_lock_jig_id'),
    path('jig_tray_id_list/', views.jig_tray_id_list, name='jig_tray_id_list'),
    path('submit-jig-with-split/', views.submit_jig_with_qty_split, name='submit_jig_with_split'),
]