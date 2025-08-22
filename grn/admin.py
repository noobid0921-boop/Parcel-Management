from django.contrib import admin
from .models import Location, CustomUser, GRN, GRNLine, OTP, DN
from django.contrib.auth.admin import UserAdmin


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ['name']
    search_fields = ['name']


# @admin.register(CustomUser)
# class CustomUserAdmin(admin.ModelAdmin):
#     list_display = ['username', 'name', 'email', 'location', 'is_staff']
#     list_filter = ['location', 'is_staff']
#     search_fields = ['username', 'name', 'email']

class CustomUserAdmin(UserAdmin):
    model = CustomUser
    list_display = ["username", "email", "name", "phone", "location", "is_staff", "is_active"]
    fieldsets = UserAdmin.fieldsets + (
        (None, {"fields": ("name", "phone", "location")}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        (None, {"fields": ("name", "phone", "location")}),
    )
admin.site.register(CustomUser, CustomUserAdmin)

class GRNLineInline(admin.TabularInline):
    model = GRNLine
    extra = 1
    fields = ['line_number', 'sender_name', 'phone', 'courier_name',
              'courier_id', 'parcel_type', 'remark']


@admin.register(GRN)
class GRNAdmin(admin.ModelAdmin):
    list_display = ['id', 'receiver', 'delivery_location', 'created_at']
    list_filter = ['delivery_location', 'created_at']
    search_fields = ['receiver__name']
    readonly_fields = ['created_at']
    inlines = [GRNLineInline]


@admin.register(GRNLine)
class GRNLineAdmin(admin.ModelAdmin):
    list_display = ['id', 'grn', 'line_number', 'sender_name', 'phone',
                    'courier_name', 'courier_id', 'parcel_type', 'created_at']
    list_filter = ['courier_name', 'parcel_type', 'created_at']
    search_fields = ['sender_name', 'phone', 'courier_id']
    readonly_fields = ['created_at']


@admin.register(OTP)
class OTPAdmin(admin.ModelAdmin):
    list_display = ['otp', 'grn_line', 'created_at', 'valid']
    list_filter = ['valid', 'created_at']
    search_fields = ['otp', 'grn_line__id', 'grn_line__sender_name']
    readonly_fields = ['created_at']


@admin.register(DN)
class DNAdmin(admin.ModelAdmin):
    list_display = ['id', 'grn_line', 'created_at', 'remark']
    list_filter = ['created_at']
    search_fields = ['grn_line__id', 'grn_line__sender_name']
    readonly_fields = ['created_at']
