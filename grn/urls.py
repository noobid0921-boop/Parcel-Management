from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

app_name = 'grn'

urlpatterns = [
    # Login & Logout views
    path("login/", auth_views.LoginView.as_view(template_name="login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(next_page="grn:login"), name="logout"),
    path('', views.GRNListView.as_view(), name='grn_list'),
    path('grn/create/', views.GRNCreateView.as_view(), name='grn_create'),
    path('grn/<int:pk>/', views.GRNDetailView.as_view(), name='grn_detail'),  # Fixed the pattern
    path('grn/delete/<int:pk>/', views.GRNDeleteView.as_view(), name='grn_delete'),  # Fixed the pattern
    path('otp/verify/', views.OTPVerificationView.as_view(), name='otp_verify'),
    path('dn/', views.DNListView.as_view(), name='dn_list'),
    path('change-location/', views.change_location, name='change_location'),
]