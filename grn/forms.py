from django import forms
from django.contrib.auth import get_user_model
from .models import GRN, GRNLine, Location, OTP, DN

User = get_user_model()


class GRNForm(forms.ModelForm):
    class Meta:
        model = GRN
        fields = ['receiver', 'delivery_location', 'place']
        widgets = {
            'receiver': forms.Select(attrs={
                'class': 'form-control',
                'required': True
            }),
            'delivery_location': forms.Select(attrs={
                'class': 'form-control',
                'required': True
            }),
            'place': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter place (optional)'
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only show active users
        self.fields['receiver'].queryset = User.objects.filter(is_active=True).order_by('name')
        self.fields['delivery_location'].queryset = Location.objects.all().order_by('name')
        
        # Add empty label
        self.fields['receiver'].empty_label = "Select Receiver"
        self.fields['delivery_location'].empty_label = "Select Location"


class GRNLineForm(forms.ModelForm):
    class Meta:
        model = GRNLine
        fields = [
            'sender_name', 'phone', 'sender_location', 
            'courier_name', 'courier_id', 'parcel_type', 'remark'
        ]
        widgets = {
            'sender_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter sender name',
                'required': True
            }),
            'phone': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter phone number'
            }),
            'sender_location': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter sender location'
            }),
            'courier_name': forms.Select(attrs={
                'class': 'form-control',
                'required': True
            }),
            'courier_id': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter courier tracking ID'
            }),
            'parcel_type': forms.Select(attrs={
                'class': 'form-control',
                'required': True
            }),
            'remark': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter any remarks'
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Add empty labels for choice fields
        self.fields['courier_name'].empty_label = "Select Courier"
        self.fields['parcel_type'].empty_label = "Select Parcel Type"


class OTPVerificationForm(forms.Form):
    otp = forms.CharField(
        max_length=10,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter OTP',
            'required': True,
            'pattern': '[0-9]{6}',
            'title': 'Please enter a 6-digit OTP'
        }),
        label='OTP Code'
    )
    
    grn_id = forms.CharField(
        widget=forms.HiddenInput(),
        required=False
    )

    def clean_otp(self):
        otp = self.cleaned_data.get('otp')
        if otp and not otp.isdigit():
            raise forms.ValidationError("OTP must contain only numbers.")
        if otp and len(otp) != 6:
            raise forms.ValidationError("OTP must be exactly 6 digits.")
        return otp


class DNForm(forms.ModelForm):
    class Meta:
        model = DN
        fields = ['remark']
        widgets = {
            'remark': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter delivery remarks (optional)'
            }),
        }


# Formset for handling multiple GRN lines
from django.forms import inlineformset_factory

GRNLineFormSet = inlineformset_factory(
    GRN, 
    GRNLine,
    form=GRNLineForm,
    fields=['sender_name', 'phone', 'sender_location', 'courier_name', 'courier_id', 'parcel_type', 'remark'],
    extra=1,  # Number of empty forms to display initially
    can_delete=True,  # Allow deletion of lines
    min_num=1,  # At least one line is required
    validate_min=True
)