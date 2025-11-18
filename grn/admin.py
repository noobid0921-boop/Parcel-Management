from django.contrib import admin
from .models import Location, CustomUser, GRN, GRNLine, OTP, DN, WarehouseInward
from django.contrib.auth.admin import UserAdmin


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ['name', 'is_warehouse']
    list_filter = ['is_warehouse']
    search_fields = ['name']


class CustomUserAdmin(UserAdmin):
    model = CustomUser
    list_display = ["username", "email", "name", "phone", "location", "is_staff", "is_active"]
    list_filter = ["is_staff", "is_active", "location"]
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
    readonly_fields = ['line_number']


class OTPInline(admin.TabularInline):
    model = OTP
    extra = 0
    max_num = 1  # Only one OTP per GRN
    fields = ['otp', 'valid', 'created_at']
    readonly_fields = ['created_at']


@admin.register(GRN)
class GRNAdmin(admin.ModelAdmin):
    list_display = ['id', 'receiver', 'delivery_location', 'is_warehouse_location', 
                    'created_at', 'has_otp', 'otp_status', 'inward_status']
    list_filter = ['delivery_location', 'delivery_location__is_warehouse', 'created_at']
    search_fields = ['receiver__name', 'id']
    readonly_fields = ['created_at']
    inlines = [GRNLineInline, OTPInline]
    
    def is_warehouse_location(self, obj):
        return obj.delivery_location.is_warehouse if obj.delivery_location else False
    is_warehouse_location.boolean = True
    is_warehouse_location.short_description = 'Warehouse'
    
    def has_otp(self, obj):
        return hasattr(obj, 'otp') and obj.otp is not None
    has_otp.boolean = True
    has_otp.short_description = 'Has OTP'
    
    def otp_status(self, obj):
        if hasattr(obj, 'otp') and obj.otp:
            if obj.otp.valid and not obj.otp.is_expired():
                return "Active"
            elif obj.otp.is_expired():
                return "Expired"
            else:
                return "Used"
        return "No OTP"
    otp_status.short_description = 'OTP Status'
    
    def inward_status(self, obj):
        if obj.delivery_location and obj.delivery_location.is_warehouse:
            if obj.is_fully_inwarded:
                return "✓ Fully Inwarded"
            elif obj.inwarded_count > 0:
                return f"⚠ {obj.inwarded_count}/{obj.total_lines} Inwarded"
            else:
                return "✗ Not Inwarded"
        return "N/A"
    inward_status.short_description = 'Inward Status'


@admin.register(GRNLine)
class GRNLineAdmin(admin.ModelAdmin):
    list_display = ['id', 'grn', 'line_number', 'sender_name', 'phone',
                    'courier_name', 'courier_id', 'parcel_type', 'is_inwarded', 'created_at']
    list_filter = ['courier_name', 'parcel_type', 'created_at']
    search_fields = ['sender_name', 'phone', 'courier_id', 'grn__id']
    readonly_fields = ['created_at']
    
    def is_inwarded(self, obj):
        return hasattr(obj, 'warehouse_inward') and obj.warehouse_inward is not None
    is_inwarded.boolean = True
    is_inwarded.short_description = 'Inwarded'


@admin.register(OTP)
class OTPAdmin(admin.ModelAdmin):
    list_display = ['otp', 'grn', 'created_at', 'valid', 'is_expired_status']
    list_filter = ['valid', 'created_at']
    search_fields = ['otp', 'grn__id', 'grn__receiver__name']
    readonly_fields = ['created_at']
    
    def is_expired_status(self, obj):
        return obj.is_expired()
    is_expired_status.boolean = True
    is_expired_status.short_description = 'Expired'


@admin.register(DN)
class DNAdmin(admin.ModelAdmin):
    list_display = ['id', 'grn_line', 'created_at', 'remark']
    list_filter = ['created_at']
    search_fields = ['grn_line__id', 'grn_line__sender_name']
    readonly_fields = ['created_at']


@admin.register(WarehouseInward)
class WarehouseInwardAdmin(admin.ModelAdmin):
    list_display = ['id', 'grn_line', 'grn_id', 'receiver', 'inwarded_by', 
                    'floor', 'rack', 'inwarded_at']
    list_filter = ['inwarded_at', 'inwarded_by', 'floor']
    search_fields = ['grn_line__id', 'grn_line__grn__id', 'grn_line__grn__receiver__name', 
                     'inwarded_by__name', 'floor', 'rack']
    readonly_fields = ['inwarded_at']
    
    def grn_id(self, obj):
        return obj.grn_line.grn.id if obj.grn_line and obj.grn_line.grn else None
    grn_id.short_description = 'GRN ID'
    
    def receiver(self, obj):
        return obj.grn_line.grn.receiver.name if obj.grn_line and obj.grn_line.grn else None
    receiver.short_description = 'Receiver'