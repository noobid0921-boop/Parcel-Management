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

    def __str__(self):
        return self.name


class CustomUser(AbstractUser):
    name = models.CharField(max_length=255)
    phone = models.BigIntegerField(null=True, blank=True)
    location = models.ForeignKey(Location, on_delete=models.CASCADE, null=True, blank=True)

    def __str__(self):
        return self.name


class GRN(models.Model):
    """Main GRN header - contains common information for all lines"""
    receiver = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE
    )
    delivery_location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name='grn_deliveries',
        null=True,   # ✅ allow null for old rows
        blank=True   # ✅ allow empty in admin/forms
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    # ✅ use place (not general_remark)
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

    # Link to parent GRN
    grn = models.ForeignKey(
        GRN, related_name="lines", on_delete=models.CASCADE
    )
    
    # Line-specific details
    sender_name = models.CharField(max_length=255, blank=True, null=True)
    phone = models.BigIntegerField(blank=True, null=True)
    sender_location = models.CharField(max_length=255, blank=True, null=True)
    courier_name = models.CharField(max_length=50, choices=COURIER_CHOICES)
    courier_id = models.CharField(max_length=100, blank=True, null=True)
    parcel_type = models.CharField(max_length=100, choices=PARCEL_TYPE_CHOICES)
    remark = models.CharField(max_length=255, blank=True, null=True)
    
    # Line tracking
    created_at = models.DateTimeField(auto_now_add=True)
    line_number = models.PositiveIntegerField(default=1)
    
    class Meta:
        ordering = ['line_number']
        unique_together = ['grn', 'line_number']

    def __str__(self):
        return f"Line {self.line_number} - {self.sender_name or 'No Sender'} (GRN {self.grn.id})"
    
    def save(self, *args, **kwargs):
        if not self.line_number:
            last_line = GRNLine.objects.filter(grn=self.grn).order_by('-line_number').first()
            self.line_number = (last_line.line_number + 1) if last_line else 1
        super().save(*args, **kwargs)


class OTP(models.Model):
    """OTP is now linked to individual GRN lines instead of the whole GRN"""
    otp = models.CharField(max_length=10)
    grn_line = models.OneToOneField(
        GRNLine,
        on_delete=models.CASCADE,
        related_name='otp',
        null=True,   # ✅ allow null for old rows
        blank=True   # ✅ allow empty in admin/forms
    )
    created_at = models.DateTimeField(auto_now_add=True)
    valid = models.BooleanField(default=True)

    @classmethod
    def generate_otp(cls):
        return ''.join(random.choices(string.digits, k=6))

    def is_expired(self):
        return timezone.now() > self.created_at + timedelta(hours=24)

    def __str__(self):
        if self.grn_line:
            return f"OTP {self.otp} for GRN Line {self.grn_line.id}"
        return f"OTP {self.otp} (no GRN Line linked)"


class DN(models.Model):
    """Delivery Note is now linked to individual GRN lines"""
    grn_line = models.OneToOneField(
        GRNLine,
        on_delete=models.CASCADE,
        related_name='dn',
        null=True,   # ✅ allow null for old rows
        blank=True   # ✅ allow empty in admin/forms
    )
    created_at = models.DateTimeField(auto_now_add=True)
    remark = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        if self.grn_line:
            return f"DN {self.id} for GRN Line {self.grn_line.id}"
        return f"DN {self.id} (no GRN Line linked)"
