# from django.urls import path
# from django.contrib.auth import views as auth_views
# from . import views

# app_name = 'grn'

# urlpatterns = [
#     # Login & Logout views
#     path("login/", auth_views.LoginView.as_view(template_name="login.html"), name="login"),
#     path("logout/", auth_views.LogoutView.as_view(next_page="grn:login"), name="logout"),
#     path('', views.GRNListView.as_view(), name='grn_list'),
#     path('grn/create/', views.GRNCreateView.as_view(), name='grn_create'),
#     path('grn/<int:pk>/', views.GRNDetailView.as_view(), name='grn_detail'),
#     path('grn/delete/<int:pk>/', views.GRNDeleteView.as_view(), name='grn_delete'),
#     path('otp/verify/', views.OTPVerificationView.as_view(), name='otp_verify'),
#     path('otp/resend/<int:grn_id>/', views.resend_otp, name='resend_otp'),
#     path('dn/', views.DNListView.as_view(), name='dn_list'),
#     path('warehouse-grns/', views.WarehouseGRNListView.as_view(), name='warehouse_grn_list'),
#     path('warehouse-inward/', views.warehouse_inward_process, name='warehouse_inward'),
#     path('warehouse-assign-floor/', views.assign_to_floor, name='assign_to_floor'),
#     path('warehouse-floor-delivery/', views.warehouse_floor_delivery, name='warehouse_floor_delivery'),
#     path('warehouse-floor-delivery-view/', views.WarehouseFloorDeliveryView.as_view(), name='warehouse_floor_delivery_view'),
#     path('change-location/', views.change_location, name='change_location'),
#     path('warehouse-inward/', views.warehouse_inward_process, name='warehouse_inward_process'),
# ]

from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

app_name = 'grn'

urlpatterns = [
    # Login & Logout views
    path("login/", auth_views.LoginView.as_view(template_name="login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(next_page="grn:login"), name="logout"),
    
    # GRN Management
    path('', views.GRNListView.as_view(), name='grn_list'),
    path('grn/create/', views.GRNCreateView.as_view(), name='grn_create'),
    path('grn/<int:pk>/', views.GRNDetailView.as_view(), name='grn_detail'),
    path('grn/delete/<int:pk>/', views.GRNDeleteView.as_view(), name='grn_delete'),
    
    # OTP Management
    path('otp/verify/', views.OTPVerificationView.as_view(), name='otp_verify'),
    path('otp/resend/<int:grn_id>/', views.resend_otp, name='resend_otp'),
    
    # Delivery Notes
    path('dn/', views.DNListView.as_view(), name='dn_list'),
    
    # Warehouse Operations
    path('warehouse-grns/', views.WarehouseGRNListView.as_view(), name='warehouse_grn_list'),
    path('warehouse-inward/', views.warehouse_inward_process, name='warehouse_inward_process'),
    path('warehouse-assign-floor/', views.assign_to_floor, name='assign_to_floor'),
    path('warehouse-floor-delivery/', views.warehouse_floor_delivery, name='warehouse_floor_delivery'),
    path('warehouse-floor-delivery-view/', views.WarehouseFloorDeliveryView.as_view(), name='warehouse_floor_delivery_view'),
    
    # NEW: Warehouse Inward Tracking
    path('warehouse-inward-tracking/', views.WarehouseInwardTrackingView.as_view(), name='warehouse_inward_tracking'),
    
    # Location Management
    path('change-location/', views.change_location, name='change_location'),
]