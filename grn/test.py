# tests.py
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core import mail
from .models import Location, GRN, OTP, DN

User = get_user_model()

class ParcelTrackingTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        
        # Create locations
        self.location1 = Location.objects.create(name="Location 1")
        self.location2 = Location.objects.create(name="Location 2")
        
        # Create users
        self.admin_user = User.objects.create_user(
            username='admin',
            email='admin@example.com',
            password='password',
            is_staff=True,
            name='Admin User'
        )
        
        self.receiver_user = User.objects.create_user(
            username='receiver',
            email='receiver@example.com',
            password='password',
            name='Receiver User',
            location=self.location1
        )
        
        self.other_location_user = User.objects.create_user(
            username='other',
            email='other@example.com',
            password='password',
            name='Other User',
            location=self.location2
        )
    
    def test_grn_creation_and_otp_generation(self):
        """Test GRN creation automatically generates OTP and sends email"""
        self.client.login(username='admin', password='password')
        
        grn_data = {
            'sender': 'Test Sender',
            'phone': 1234567890,
            'courier': 'Test Courier',
            'place': 'Test Place',
            'courier_id': 'TC123',
            'location': self.location1.id,
            'receiver': self.receiver_user.id,
            'parcel_type': 'Document',
            'remark': 'Test remark'
        }
        
        response = self.client.post(reverse('grn_create'), grn_data)
        self.assertEqual(response.status_code, 302)
        
        # Check GRN was created
        grn = GRN.objects.get(sender='Test Sender')
        self.assertEqual(grn.receiver, self.receiver_user)
        
        # Check OTP was generated
        otp = OTP.objects.get(grn=grn)
        self.assertIsNotNone(otp.otp)
        self.assertTrue(otp.valid)
        
        # Check email was sent
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(otp.otp, mail.outbox[0].body)
    
    def test_location_based_access_control(self):
        """Test users can only see GRNs from their location"""
        # Create GRN for location1
        grn1 = GRN.objects.create(
            sender='Sender 1',
            phone=1234567890,
            location=self.location1,
            receiver=self.receiver_user,
            parcel_type='Document'
        )
        
        # Create GRN for location2
        grn2 = GRN.objects.create(
            sender='Sender 2',
            phone=1234567890,
            location=self.location2,
            receiver=self.other_location_user,
            parcel_type='Package'
        )
        
        # Login as receiver_user (location1)
        self.client.login(username='receiver', password='password')
        response = self.client.get(reverse('grn_list'))
        
        # Should only see GRN from location1
        self.assertContains(response, 'Sender 1')
        self.assertNotContains(response, 'Sender 2')
    
    def test_otp_verification_and_dn_creation(self):
        """Test OTP verification creates DN"""
        # Create GRN and OTP
        grn = GRN.objects.create(
            sender='Test Sender',
            phone=1234567890,
            location=self.location1,
            receiver=self.receiver_user,
            parcel_type='Document'
        )
        otp = OTP.objects.create(grn=grn, otp='123456')
        
        # Login as admin
        self.client.login(username='admin', password='password')
        
        # Verify OTP
        response = self.client.post(reverse('otp_verify'), {
            'otp': '123456',
            'grn_id': grn.id
        })
        
        self.assertEqual(response.status_code, 302)
        
        # Check DN was created
        dn = DN.objects.get(grn=grn)
        self.assertIsNotNone(dn)
        
        # Check OTP was invalidated
        otp.refresh_from_db()
        self.assertFalse(otp.valid)
    
    def test_admin_only_access(self):
        """Test only admin users can create GRNs and verify OTPs"""
        # Test non-admin cannot access GRN creation
        self.client.login(username='receiver', password='password')
        response = self.client.get(reverse('grn_create'))
        self.assertEqual(response.status_code, 403)
        
        # Test non-admin cannot access OTP verification
        response = self.client.get(reverse('otp_verify'))
        self.assertEqual(response.status_code, 403)