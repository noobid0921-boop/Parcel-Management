from django.db import models
from django.contrib.auth.models import AbstractUser
from django.conf import settings
import random
import string
from datetime import timedelta
from django.utils import timezone


class Location(models.Model):
    name = models.CharField(max_length=255)
    detail = models.JSONField(blank=True, null=True)

    is_warehouse = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.name} ({'Warehouse' if self.is_warehouse else 'Normal'})"


class CustomUser(AbstractUser):
    name = models.CharField(max_length=255)
    phone = models.BigIntegerField(null=True, blank=True)
    location = models.ForeignKey(
        Location, 
        on_delete=models.CASCADE, 
        null=True, 
        blank=True,
        related_name='users'  # Added for cleaner reverse lookups
    )
    
    is_warehouse_user = models.BooleanField(
        default=False,
        help_text="Designates whether this user is a warehouse user who creates GRNs."
    )

    def __str__(self):
        return self.name 


class GRN(models.Model):
    """Main GRN header - contains common information for all lines"""
    receiver = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='received_grns'
    )
    delivery_location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name='grn_deliveries',
        null=True,
        blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_grns',
        help_text="User who created this GRN (warehouse user)"
    )
    
    place = models.CharField(max_length=255, blank=True, null=True)
    
    def __str__(self):
        return f"GRN {self.id} - {self.receiver}"
    
    @property
    def total_lines(self):
        """Get total number of lines in this GRN"""
        return self.lines.count()
    
    @property
    def is_delivered(self):
        """Check if all lines in this GRN are delivered"""
        return self.lines.filter(dn__isnull=True).count() == 0
    
    @property
    def is_fully_inwarded(self):
        """Check if all lines in this GRN are inwarded (for warehouse locations)"""
        return self.lines.filter(warehouse_inward__isnull=False).count() == self.total_lines
    
    @property
    def inwarded_count(self):
        """Get count of inwarded lines"""
        return self.lines.filter(warehouse_inward__isnull=False).count()
    
    @property
    def pending_inward_count(self):
        """Get count of lines pending inward (for warehouse locations)"""
        if not self.delivery_location or not self.delivery_location.is_warehouse:
            return 0
        return self.lines.filter(warehouse_inward__isnull=True).count()
    
    @property
    def inward_status(self):
        """Get inward status text for warehouse GRNs"""
        if not self.delivery_location or not self.delivery_location.is_warehouse:
            return None
        
        total = self.total_lines
        inwarded = self.inwarded_count
        
        if inwarded == 0:
            return "Pending Inward"
        elif inwarded == total:
            return "Fully Inwarded"
        else:
            return f"Partially Inwarded ({inwarded}/{total})"


class GRNLine(models.Model):
    """Individual line items within a GRN"""
    PARCEL_TYPE_CHOICES = [
        ('document', 'Document'),
        ('cheque', 'Cheque'),
        ('bill', 'Bill'),
        ('sample', 'Sample'),
        ('box_cloths', 'Box (Cloths)'),
        ('food', 'Food'),
        ('medicine', 'Medicine'),
    ]
    
    COURIER_CHOICES = [
        ('professional_courier', 'Professional Courier'),
        ('blue_dart', 'Blue Dart'),
        ('branch_express', 'Branch Express'),
        ('dtdc', 'DTDC'),
        ('shree_maruthi', 'Shree Maruthi'),
        ('truck_on', 'Truck On'),
        ('express_air_service', 'Express Air Service'),
        ('st_retail', 'ST Retail'),
        ('post', 'Post'),
        ('sky_king', 'Sky King'),
        ('tirupathi', 'Tirupathi'),
        ('gokulam', 'Gokulam'),
    ]

    grn = models.ForeignKey(
        GRN, related_name="lines", on_delete=models.CASCADE
    )
    
    sender_name = models.CharField(max_length=255, blank=True, null=True)
    phone = models.BigIntegerField(blank=True, null=True)
    sender_location = models.CharField(max_length=255, blank=True, null=True)
    courier_name = models.CharField(max_length=50, choices=COURIER_CHOICES)
    courier_id = models.CharField(max_length=100, blank=True, null=True)
    parcel_type = models.CharField(max_length=100, choices=PARCEL_TYPE_CHOICES)
    remark = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    line_number = models.PositiveIntegerField(default=1)
    
    class Meta:
        ordering = ['line_number']
        unique_together = ['grn', 'line_number']

    def __str__(self):
        return f"Line {self.line_number} - {self.sender_name or 'No Sender'} (GRN {self.grn.id})"
    
    @property
    def is_inwarded(self):
        """Check if this line has been inwarded from warehouse"""
        return hasattr(self, 'warehouse_inward')
    
    @property
    def inward_location(self):
        """Get the location where this line was inwarded to"""
        if self.is_inwarded:
            return self.warehouse_inward.inwarded_by.location
        return None
    
    def save(self, *args, **kwargs):
        if not self.line_number:
            last_line = GRNLine.objects.filter(grn=self.grn).order_by('-line_number').first()
            self.line_number = (last_line.line_number + 1) if last_line else 1
        super().save(*args, **kwargs)


class OTP(models.Model):
    """OTP is now linked to GRN instead of individual GRN lines"""
    otp = models.CharField(max_length=10)
    grn = models.OneToOneField(
        GRN,
        on_delete=models.CASCADE,
        related_name='otp',
        null=True,
        blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    valid = models.BooleanField(default=True)

    @classmethod
    def generate_otp(cls):
        return ''.join(random.choices(string.digits, k=6))

    def is_expired(self):
        """Check if OTP has expired (24 hour validity)"""
        now = timezone.now()
        expiry_time = self.created_at + timedelta(hours=24)
        return now > expiry_time
    
    def time_until_expiry(self):
        """Get time remaining until expiry"""
        now = timezone.now()
        expiry_time = self.created_at + timedelta(hours=24)
        
        if now > expiry_time:
            return timedelta(0)  
        
        return expiry_time - now
    
    def expiry_datetime(self):
        """Get the exact expiry datetime"""
        return self.created_at + timedelta(hours=24)
    
    def regenerate(self):
        """Regenerate OTP with new creation time"""
        self.otp = self.generate_otp()
        self.valid = True
        self.created_at = timezone.now()
        self.save()
        return self.otp

    def __str__(self):
        if self.grn:
            return f"OTP {self.otp} for GRN {self.grn.id}"
        return f"OTP {self.otp} (no GRN linked)"


class DN(models.Model):
    """Delivery Note is still linked to individual GRN lines"""
    grn_line = models.OneToOneField(
        GRNLine,
        on_delete=models.CASCADE,
        related_name='dn',
        null=True,
        blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    remark = models.CharField(max_length=255, blank=True, null=True)
    from_warehouse_inward = models.BooleanField(default=False)

    def __str__(self):
        if self.grn_line:
            return f"DN {self.id} for GRN Line {self.grn_line.id}"
        return f"DN {self.id} (no GRN Line linked)"


class WarehouseInward(models.Model):
    """Track warehouse inward process for GRN lines - 3 Stage Process"""
    grn_line = models.OneToOneField(
        GRNLine,
        on_delete=models.CASCADE,
        related_name='warehouse_inward',
        null=True,
        blank=True
    )
    
    inwarded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='warehouse_inwards'
    )
    inwarded_at = models.DateTimeField(auto_now_add=True)
    inward_remark = models.CharField(max_length=255, blank=True, null=True)
    
    floor = models.CharField(max_length=100, blank=True, null=True)
    rack = models.CharField(max_length=100, blank=True, null=True)
    assigned_to_floor_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='floor_assignments'
    )
    assigned_to_floor_at = models.DateTimeField(null=True, blank=True)
    floor_remark = models.CharField(max_length=255, blank=True, null=True)
    
    delivered_to_receiver = models.BooleanField(default=False)
    delivered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='warehouse_deliveries'
    )
    delivered_at = models.DateTimeField(null=True, blank=True)
    delivery_remark = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        if self.grn_line:
            return f"Inward for GRN Line {self.grn_line.id} at {self.inwarded_at}"
        return f"Warehouse Inward {self.id}"
    
    @property
    def is_on_floor(self):
        """Check if item has been assigned to a floor"""
        return bool(self.floor)
    
    @property
    def stage(self):
        """Get current stage of the item"""
        if self.delivered_to_receiver:
            return "delivered"
        elif self.floor:
            return "on_floor"
        else:
            return "received"

    class Meta:
        ordering = ['-inwarded_at']