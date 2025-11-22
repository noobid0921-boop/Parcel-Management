from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.decorators import login_required
from django.views.generic import CreateView, ListView, DetailView, FormView
from django.views import View
from django.contrib import messages
from django.urls import reverse_lazy
from django.core.mail import send_mail
from django.conf import settings
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.utils.timezone import make_aware
from django.db.models import Q, Prefetch, F, Count
from datetime import datetime
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_protect
from django.forms import inlineformset_factory
from django.utils import timezone
from .models import GRN, GRNLine, OTP, DN, CustomUser, Location, WarehouseInward
from .forms import GRNForm, OTPVerificationForm, DNForm


class AdminRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_authenticated and (
            self.request.user.is_staff or self.request.user.is_superuser
        )


@csrf_protect
@require_POST
@login_required
def change_location(request):
    """Handle AJAX request to change user's current location"""
    location_id = request.POST.get('location_id')
    
    if not location_id:
        return JsonResponse({
            'success': False,
            'error': 'Location ID not provided'
        })
    
    try:
        location_id = int(location_id)
        location = get_object_or_404(Location, id=location_id)
        
        # Store the current location in the user's session
        request.session['current_location_id'] = location_id
        
        return JsonResponse({
            'success': True,
            'location_name': location.name
        })
        
    except (ValueError, TypeError):
        return JsonResponse({
            'success': False,
            'error': 'Invalid location ID format'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error changing location: {str(e)}'
        })


# Create inline formset for GRN Lines
GRNLineFormSet = inlineformset_factory(
    GRN, GRNLine,
    fields=('sender_name', 'phone', 'sender_location', 'courier_name', 'courier_id', 'parcel_type', 'remark'),
    extra=1,  # Number of empty forms to display
    can_delete=True
)


class GRNCreateView(LoginRequiredMixin, AdminRequiredMixin, CreateView):
    model = GRN
    form_class = GRNForm
    template_name = 'grn/grn_create.html'
    success_url = reverse_lazy('grn:grn_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['locations'] = Location.objects.all().order_by('name')
        
        # Get current location
        current_location_id = self.request.session.get('current_location_id')
        if current_location_id:
            try:
                context['current_location'] = Location.objects.get(id=current_location_id)
            except Location.DoesNotExist:
                context['current_location'] = None
        elif not (self.request.user.is_staff or self.request.user.is_superuser):
            context['current_location'] = self.request.user.location
        
        # Add formset for GRN lines
        if self.request.POST:
            context['formset'] = GRNLineFormSet(self.request.POST)
        else:
            context['formset'] = GRNLineFormSet()
        
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        formset = context['formset']
        
        if formset.is_valid():
            try:
                with transaction.atomic():
                    # Save the main GRN first
                    grn = form.save(commit=False)
                    grn.created_by = self.request.user  # Track who created the GRN
                    grn.save()
                    
                    # Process each form in the formset and assign line numbers
                    lines = []
                    line_number = 1
                    
                    for form_instance in formset.forms:
                        if form_instance.cleaned_data and not form_instance.cleaned_data.get('DELETE', False):
                            # Check if the form has valid data (not empty)
                            if any(form_instance.cleaned_data.get(field) for field in ['sender_name', 'phone', 'courier_name', 'parcel_type']):
                                line = form_instance.save(commit=False)
                                line.grn = grn
                                line.line_number = line_number  # Assign sequential line number
                                line.save()
                                lines.append(line)
                                line_number += 1
                    
                    if not lines:
                        messages.error(self.request, 'At least one parcel line is required.')
                        grn.delete()  # Clean up the created GRN
                        return self.form_invalid(form)
                    
                    # Generate OTP only for non-warehouse deliveries
                    if not grn.delivery_location.is_warehouse:
                        # Generate ONE OTP for the entire GRN
                        otp_code = OTP.generate_otp()
                        OTP.objects.create(otp=otp_code, grn=grn)
                        
                        # Send email with single OTP for all parcels
                        self.send_otp_email(grn, otp_code, lines)
                        
                        messages.success(
                            self.request, 
                            f'GRN {grn.id} created successfully with {len(lines)} lines. OTP sent to {grn.receiver.email}'
                        )
                    else:
                        # Warehouse delivery - no OTP needed at creation
                        messages.success(
                            self.request, 
                            f'GRN {grn.id} created successfully with {len(lines)} lines for warehouse delivery. OTP will be generated when inwarded by floor user.'
                        )
                    
                    return redirect(self.success_url)
                    
            except Exception as e:
                messages.error(self.request, f'Error creating GRN: {str(e)}')
                return self.form_invalid(form)
        else:
            # Add formset errors to messages
            for form_errors in formset.errors:
                for field, errors in form_errors.items():
                    for error in errors:
                        messages.error(self.request, f'{field}: {error}')
            if formset.non_form_errors():
                for error in formset.non_form_errors():
                    messages.error(self.request, error)
            return self.form_invalid(form)

    def form_invalid(self, form):
        # Add form errors to messages
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(self.request, f'{field}: {error}')
        context = self.get_context_data()
        return self.render_to_response(context)

    def send_otp_email(self, grn, otp_code, lines):
        subject = f'Parcel Delivery Notification - GRN {grn.id}'
        
        # Build message with all line items
        lines_info = []
        for line in lines:
            line_info = f"""
Line {line.line_number}:
  - Sender: {line.sender_name or 'Unknown'}
  - Parcel Type: {line.get_parcel_type_display()}
  - Courier: {line.get_courier_name_display()}
"""
            lines_info.append(line_info)
        
        message = f"""
Dear {grn.receiver.name},

You have received {len(lines)} parcel(s) at {grn.delivery_location.name}:

GRN ID: {grn.id}
{''.join(lines_info)}

Please visit the collection center with this OTP to collect ALL your parcels:
OTP: {otp_code}

This OTP is valid for 24 hours and will allow you to collect all {len(lines)} parcel(s) in this GRN.

Best regards,
Parcel Tracking Team
"""
        try:
            send_mail(
                subject,
                message,
                settings.DEFAULT_FROM_EMAIL,
                [grn.receiver.email],
                fail_silently=False,
            )
        except Exception as e:
            messages.error(self.request, f'Failed to send email: {str(e)}')
            raise  # Re-raise to trigger transaction rollback


class GRNListView(LoginRequiredMixin, ListView):
    model = GRN
    template_name = 'grn/grn_list.html'
    context_object_name = 'grns'
    paginate_by = 100

    def get_queryset(self):
        queryset = GRN.objects.select_related(
            'delivery_location', 'receiver', 'otp'
        ).prefetch_related(
            Prefetch('lines', queryset=GRNLine.objects.select_related('dn'))
        ).order_by('-created_at')
        
        # Get current location from session
        current_location_id = self.request.session.get('current_location_id')
        
        # Apply location filtering
        if self.request.user.is_staff or self.request.user.is_superuser:
            # Admin users can see GRNs from their selected location
            if current_location_id:
                try:
                    current_location = Location.objects.get(id=current_location_id)
                    queryset = queryset.filter(delivery_location=current_location)
                except Location.DoesNotExist:
                    # Clear invalid location from session
                    if 'current_location_id' in self.request.session:
                        del self.request.session['current_location_id']
        else:
            # Non-admin users can only see GRNs from their assigned location
            if self.request.user.location:
                queryset = queryset.filter(delivery_location=self.request.user.location)
            else:
                return GRN.objects.none()

        # Apply search and filters
        queryset = self.apply_filters(queryset)
        return queryset

    def apply_filters(self, queryset):
        """Apply various filters to the queryset"""
        # Search filter - now searches in GRN lines
        q = self.request.GET.get('q')
        if q:
            queryset = queryset.filter(
                Q(lines__sender_name__icontains=q) |
                Q(receiver__name__icontains=q) |
                Q(receiver__username__icontains=q) |
                Q(id__icontains=q)
            ).distinct()

        # Date range filters
        start_date = self.request.GET.get('start_date')
        if start_date:
            try:
                start = make_aware(datetime.strptime(start_date, '%Y-%m-%d'))
                queryset = queryset.filter(created_at__gte=start)
            except ValueError:
                pass

        end_date = self.request.GET.get('end_date')
        if end_date:
            try:
                end = make_aware(datetime.strptime(end_date + ' 23:59:59', '%Y-%m-%d %H:%M:%S'))
                queryset = queryset.filter(created_at__lte=end)
            except ValueError:
                pass

        # Parcel type filter - validate against choices
        parcel_type = self.request.GET.get('parcel_type')
        if parcel_type:
            valid_types = [choice[0] for choice in GRNLine.PARCEL_TYPE_CHOICES]
            if parcel_type in valid_types:
                queryset = queryset.filter(lines__parcel_type=parcel_type).distinct()

        # Status filter - check if all lines are delivered or not
        status = self.request.GET.get('status')
        if status == 'delivered':
            # GRNs where all lines have DNs
            queryset = queryset.filter(lines__dn__isnull=False).annotate(
                delivered_lines=Count('lines__dn'),
                total_lines_count=Count('lines')
            ).filter(delivered_lines=F('total_lines_count')).distinct()
        elif status == 'pending':
            # GRNs where at least one line doesn't have a DN
            queryset = queryset.filter(lines__dn__isnull=True).distinct()

        # Courier filter - validate against choices
        courier = self.request.GET.get('courier')
        if courier:
            valid_couriers = [choice[0] for choice in GRNLine.COURIER_CHOICES]
            if courier in valid_couriers:
                queryset = queryset.filter(lines__courier_name=courier).distinct()

        # Phone number filter
        phone = self.request.GET.get('phone')
        if phone:
            queryset = queryset.filter(lines__phone__icontains=phone).distinct()

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Get current location
        current_location = self.get_current_location()
        
        # Add location data to context
        context.update({
            'current_location': current_location,
            'locations': Location.objects.all().order_by('name'),
            'parcel_type_choices': GRNLine.PARCEL_TYPE_CHOICES,
            'courier_choices': GRNLine.COURIER_CHOICES,
            'current_filters': self.get_current_filters(),
        })
        
        return context

    def get_current_location(self):
        """Get the current location for the user"""
        current_location_id = self.request.session.get('current_location_id')
        current_location = None
        
        if current_location_id:
            try:
                current_location = Location.objects.get(id=current_location_id)
            except Location.DoesNotExist:
                # Clear invalid location from session
                if 'current_location_id' in self.request.session:
                    del self.request.session['current_location_id']
        
        # If no location selected and user is admin, use first location as default
        if not current_location and (self.request.user.is_staff or self.request.user.is_superuser):
            first_location = Location.objects.first()
            if first_location:
                current_location = first_location
                self.request.session['current_location_id'] = first_location.id
        
        # For non-admin users, use their assigned location
        if not (self.request.user.is_staff or self.request.user.is_superuser):
            current_location = self.request.user.location

        return current_location

    def get_current_filters(self):
        """Get current filter values to maintain state"""
        return {
            'q': self.request.GET.get('q', ''),
            'start_date': self.request.GET.get('start_date', ''),
            'end_date': self.request.GET.get('end_date', ''),
            'parcel_type': self.request.GET.get('parcel_type', ''),
            'status': self.request.GET.get('status', ''),
            'courier': self.request.GET.get('courier', ''),
            'phone': self.request.GET.get('phone', ''),
        }


class GRNDetailView(LoginRequiredMixin, DetailView):
    model = GRN
    template_name = 'grn/grn_details.html'
    context_object_name = 'grn'

    def get_queryset(self):
        queryset = GRN.objects.select_related(
            'delivery_location', 'receiver', 'otp', 'created_by'
        ).prefetch_related(
            Prefetch('lines', queryset=GRNLine.objects.select_related(
                'dn', 
                'warehouse_inward',
                'warehouse_inward__inwarded_by',
                'warehouse_inward__inwarded_by__location'
            ))
        )
        
        # Apply location permissions
        current_location_id = self.request.session.get('current_location_id')
        
        if self.request.user.is_staff or self.request.user.is_superuser:
            # Admin users can see GRNs from their selected location
            if current_location_id:
                try:
                    current_location = Location.objects.get(id=current_location_id)
                    queryset = queryset.filter(delivery_location=current_location)
                except Location.DoesNotExist:
                    pass
        else:
            # Non-admin users can only see GRNs from their assigned location
            if self.request.user.location:
                queryset = queryset.filter(delivery_location=self.request.user.location)
            else:
                return GRN.objects.none()

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Add location context
        context['locations'] = Location.objects.all().order_by('name')
        
        # Get current location
        current_location_id = self.request.session.get('current_location_id')
        if current_location_id:
            try:
                context['current_location'] = Location.objects.get(id=current_location_id)
            except Location.DoesNotExist:
                context['current_location'] = None
        elif not (self.request.user.is_staff or self.request.user.is_superuser):
            context['current_location'] = self.request.user.location
        
        # Add line information with their DNs
        context['grn_lines'] = self.object.lines.all().order_by('line_number')

        return context


class GRNDeleteView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Class-based delete view for GRN"""
    
    def post(self, request, pk):
        """Handle POST request to delete GRN"""
        # Get the GRN object
        grn = get_object_or_404(GRN, pk=pk)

        # Check location permissions
        if not has_location_permission(request.user, grn.delivery_location, request.session):
            messages.error(request, "You don't have permission to delete this GRN.")
            return redirect('grn:grn_list')

        # Check if any line is already delivered
        delivered_lines = grn.lines.filter(dn__isnull=False)
        if delivered_lines.exists():
            messages.error(request, "Cannot delete a GRN with delivered items.")
            return redirect('grn:grn_list')

        # Delete the GRN (will cascade to lines, OTPs, etc.)
        grn_id = grn.id
        total_lines = grn.lines.count()
        try:
            with transaction.atomic():
                grn.delete()
            messages.success(request, f"GRN {grn_id} with {total_lines} lines deleted successfully.")
        except Exception as e:
            messages.error(request, f"Error deleting GRN: {str(e)}")

        return redirect('grn:grn_list')


class OTPVerificationView(LoginRequiredMixin, AdminRequiredMixin, FormView):
    form_class = OTPVerificationForm
    template_name = 'grn/otp_verification.html'
    success_url = reverse_lazy('grn:grn_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Add location context
        context['locations'] = Location.objects.all().order_by('name')
        
        # Get current location
        current_location_id = self.request.session.get('current_location_id')
        if current_location_id:
            try:
                context['current_location'] = Location.objects.get(id=current_location_id)
            except Location.DoesNotExist:
                context['current_location'] = None
        elif not (self.request.user.is_staff or self.request.user.is_superuser):
            context['current_location'] = self.request.user.location
        
        # Get GRN info (instead of GRN line)
        grn_id = self.request.GET.get('grn_id')
        
        if grn_id:
            try:
                grn = get_object_or_404(GRN, id=grn_id)
                
                # Check location permissions
                if not has_location_permission(self.request.user, grn.delivery_location, self.request.session):
                    messages.error(self.request, "You don't have permission to verify this GRN.")
                    return context
                
                context['grn'] = grn
                context['grn_lines'] = grn.lines.all().order_by('line_number')
                
                # Add OTP info if available
                try:
                    context['otp_obj'] = grn.otp
                except OTP.DoesNotExist:
                    context['otp_obj'] = None
                    
            except Exception as e:
                messages.error(self.request, f"Error loading GRN: {str(e)}")
                
        return context

    def get_initial(self):
        initial = super().get_initial()
        grn_id = self.request.GET.get('grn_id')
        if grn_id:
            initial['grn_id'] = grn_id
        return initial

    def form_valid(self, form):
        otp_code = form.cleaned_data['otp']
        
        try:
            # Find OTP and corresponding GRN
            otp = OTP.objects.select_related('grn').get(otp=otp_code, valid=True)
            grn = otp.grn
            
            # Check location permissions
            if not has_location_permission(self.request.user, grn.delivery_location, self.request.session):
                messages.error(self.request, "You don't have permission to verify this GRN.")
                return self.form_invalid(form)

            # Check if OTP is expired
            if otp.is_expired():
                messages.error(self.request, 'OTP has expired. Please contact the administrator.')
                return self.form_invalid(form)

            # Check if all items are already delivered
            undelivered_lines = grn.lines.filter(dn__isnull=True)
            if not undelivered_lines.exists():
                messages.error(self.request, 'All items in this GRN have already been collected.')
                return self.form_invalid(form)

            # Create DNs for all undelivered lines and invalidate OTP
            with transaction.atomic():
                dns_created = []
                for line in undelivered_lines:
                    dn = DN.objects.create(
                        grn_line=line, 
                        remark=f"Parcel collected via OTP verification by {self.request.user.username}"
                    )
                    dns_created.append(dn)
                
                otp.valid = False
                otp.save()

            messages.success(
                self.request,
                f'GRN {grn.id} processed successfully. {len(dns_created)} parcel(s) have been collected by the receiver.'
            )
            return redirect(self.success_url)

        except OTP.DoesNotExist:
            messages.error(self.request, 'Invalid OTP. Please check the OTP and try again.')
            return self.form_invalid(form)
        except Exception as e:
            messages.error(self.request, f'An error occurred: {str(e)}')
            return self.form_invalid(form)


@login_required
@require_POST
def resend_otp(request, grn_id):
    """Resend OTP for a specific GRN"""
    # Check if user is admin
    if not (request.user.is_staff or request.user.is_superuser):
        messages.error(request, "You don't have permission to resend OTPs.")
        return redirect('grn:grn_list')

    grn = get_object_or_404(GRN, id=grn_id)
    
    # Check location permissions
    if not has_location_permission(request.user, grn.delivery_location, request.session):
        messages.error(request, "You don't have permission to resend OTP for this GRN.")
        return redirect('grn:grn_list')

    # Check if GRN is already fully delivered
    if grn.is_delivered:
        messages.error(request, "Cannot resend OTP for a fully delivered GRN.")
        return redirect('grn:grn_detail', pk=grn.id)

    try:
        with transaction.atomic():
            # Get or create OTP for this GRN
            otp_obj, created = OTP.objects.get_or_create(
                grn=grn,
                defaults={
                    'otp': OTP.generate_otp(),
                    'valid': True
                }
            )
            
            # If OTP already exists, regenerate it and mark as valid
            if not created:
                otp_obj.otp = OTP.generate_otp()
                otp_obj.valid = True
                otp_obj.created_at = timezone.now()  # Reset creation time
                otp_obj.save()
            
            # Get undelivered lines for email content
            undelivered_lines = grn.lines.filter(dn__isnull=True)
            
            # Send email with new OTP
            send_resend_otp_email(grn, otp_obj.otp, undelivered_lines)
            
            messages.success(
                request, 
                f'New OTP has been generated and sent to {grn.receiver.email} for GRN {grn.id}'
            )
            
    except Exception as e:
        messages.error(request, f'Error resending OTP: {str(e)}')
    
    return redirect('grn:grn_detail', pk=grn.id)


def send_resend_otp_email(grn, otp_code, undelivered_lines):
    """Send resend OTP email"""
    subject = f'Resend: Parcel Collection OTP - GRN {grn.id}'
    
    # Build message with undelivered line items
    lines_info = []
    for line in undelivered_lines:
        line_info = f"""
Line {line.line_number}:
  - Sender: {line.sender_name or 'Unknown'}
  - Parcel Type: {line.get_parcel_type_display()}
  - Courier: {line.get_courier_name_display()}
"""
        lines_info.append(line_info)
    
    message = f"""
Dear {grn.receiver.name},

This is a resend of your parcel collection OTP.

You have {len(undelivered_lines)} undelivered parcel(s) at {grn.delivery_location.name}:

GRN ID: {grn.id}
{''.join(lines_info)}

Please visit the collection center with this OTP to collect your remaining parcels:
OTP: {otp_code}

This OTP is valid for 24 hours and will allow you to collect all remaining parcels in this GRN.

If you did not request this resend, please contact us immediately.

Best regards,
Parcel Tracking Team
"""
    try:
        send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            [grn.receiver.email],
            fail_silently=False,
        )
    except Exception as e:
        raise Exception(f'Failed to send resend email: {str(e)}')


# ==================== UPDATED DN LIST VIEW ====================
class DNListView(LoginRequiredMixin, ListView):
    model = DN
    template_name = 'grn/dn_list.html'
    context_object_name = 'dns'
    paginate_by = 100

    def get_queryset(self):
        queryset = DN.objects.select_related(
            'grn_line',
            'grn_line__grn',
            'grn_line__grn__delivery_location',
            'grn_line__grn__receiver',
            'grn_line__warehouse_inward',
            'grn_line__warehouse_inward__inwarded_by',
            'grn_line__warehouse_inward__inwarded_by__location'
        ).order_by('-created_at')

        # Location-based filtering
        location_filter_id = self.request.GET.get('location_filter')
        
        if self.request.user.is_staff or self.request.user.is_superuser:
            # Admin users can filter by location if specified
            if location_filter_id:
                try:
                    location = Location.objects.get(id=location_filter_id)
                    queryset = queryset.filter(grn_line__grn__delivery_location=location)
                except Location.DoesNotExist:
                    pass
            # Otherwise show ALL DNs from ALL locations
        else:
            # Non-admin users can only see DNs from their assigned location
            if self.request.user.location:
                queryset = queryset.filter(grn_line__grn__delivery_location=self.request.user.location)
            else:
                return DN.objects.none()

        # Apply filters
        queryset = self.apply_filters(queryset)
        return queryset

    def apply_filters(self, queryset):
        """Apply filters to the DN queryset - ENHANCED WITH DELIVERY TYPE FILTER"""
        
        # General search filter
        q = self.request.GET.get('q')
        if q:
            queryset = queryset.filter(
                Q(grn_line__sender_name__icontains=q) |
                Q(grn_line__grn__receiver__name__icontains=q) |
                Q(grn_line__grn__receiver__username__icontains=q) |
                Q(id__icontains=q) |
                Q(grn_line__grn__id__icontains=q)
            )

        # Date range filters
        start_date = self.request.GET.get('start_date')
        if start_date:
            try:
                start = make_aware(datetime.strptime(start_date, '%Y-%m-%d'))
                queryset = queryset.filter(created_at__gte=start)
            except ValueError:
                pass

        end_date = self.request.GET.get('end_date')
        if end_date:
            try:
                end = make_aware(datetime.strptime(end_date + ' 23:59:59', '%Y-%m-%d %H:%M:%S'))
                queryset = queryset.filter(created_at__lte=end)
            except ValueError:
                pass

        # Parcel type filter
        parcel_type = self.request.GET.get('parcel_type')
        if parcel_type:
            valid_types = [choice[0] for choice in GRNLine.PARCEL_TYPE_CHOICES]
            if parcel_type in valid_types:
                queryset = queryset.filter(grn_line__parcel_type=parcel_type)

        # Courier filter
        courier = self.request.GET.get('courier')
        if courier:
            valid_couriers = [choice[0] for choice in GRNLine.COURIER_CHOICES]
            if courier in valid_couriers:
                queryset = queryset.filter(grn_line__courier_name=courier)

        # Phone filter
        phone = self.request.GET.get('phone')
        if phone:
            queryset = queryset.filter(grn_line__phone__icontains=phone)

        # Location filter (delivery location name)
        location = self.request.GET.get('location')
        if location:
            queryset = queryset.filter(grn_line__grn__delivery_location__name__icontains=location)

        # Sender filter
        sender = self.request.GET.get('sender')
        if sender:
            queryset = queryset.filter(grn_line__sender_name__icontains=sender)

        # Receiver filter (searches both name and username)
        receiver = self.request.GET.get('receiver')
        if receiver:
            queryset = queryset.filter(
                Q(grn_line__grn__receiver__name__icontains=receiver) |
                Q(grn_line__grn__receiver__username__icontains=receiver)
            )
        
        # ==================== NEW: DELIVERY TYPE FILTER ====================
        delivery_type = self.request.GET.get('delivery_type')
        if delivery_type == 'otp':
            # Show only OTP-verified deliveries (from_warehouse_inward=False)
            queryset = queryset.filter(from_warehouse_inward=False)
        elif delivery_type == 'warehouse':
            # Show only warehouse-inwarded deliveries (from_warehouse_inward=True)
            queryset = queryset.filter(from_warehouse_inward=True)
        # If delivery_type is empty or 'all', show all DNs (no filter)
        
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Get selected location if filter is applied
        location_filter_id = self.request.GET.get('location_filter')
        selected_location = None
        
        if location_filter_id:
            try:
                selected_location = Location.objects.get(id=location_filter_id)
            except Location.DoesNotExist:
                pass
        
        # Get current location for non-admin users
        current_location = None
        if not (self.request.user.is_staff or self.request.user.is_superuser):
            current_location = self.request.user.location
        
        # Calculate statistics
        total_dns = self.get_queryset().count()
        otp_deliveries = self.get_queryset().filter(from_warehouse_inward=False).count()
        warehouse_deliveries = self.get_queryset().filter(from_warehouse_inward=True).count()
        
        # Add context data
        context.update({
            'current_location': current_location,
            'selected_location': selected_location,
            'locations': Location.objects.all().order_by('name'),
            'parcel_type_choices': GRNLine.PARCEL_TYPE_CHOICES,
            'courier_choices': GRNLine.COURIER_CHOICES,
            'current_filters': self.get_current_filters(),
            'total_dns': total_dns,
            'otp_deliveries': otp_deliveries,
            'warehouse_deliveries': warehouse_deliveries,
        })
        
        return context

    def get_current_filters(self):
        """Get current filter values to maintain state - ENHANCED"""
        return {
            'q': self.request.GET.get('q', ''),
            'start_date': self.request.GET.get('start_date', ''),
            'end_date': self.request.GET.get('end_date', ''),
            'parcel_type': self.request.GET.get('parcel_type', ''),
            'courier': self.request.GET.get('courier', ''),
            'phone': self.request.GET.get('phone', ''),
            'location': self.request.GET.get('location', ''),
            'sender': self.request.GET.get('sender', ''),
            'receiver': self.request.GET.get('receiver', ''),
            'location_filter': self.request.GET.get('location_filter', ''),
            'delivery_type': self.request.GET.get('delivery_type', ''),  # NEW
        }


class WarehouseGRNListView(LoginRequiredMixin, ListView):
    """View to show GRNs delivered to warehouse locations
    Shows all lines with their inward status for warehouse users"""
    model = GRN
    template_name = 'grn/warehouse_grn_list.html'
    context_object_name = 'grns'
    paginate_by = 100

    def get_queryset(self):
        # Get GRNs where delivery_location is a warehouse
        # Include lines with warehouse_inward to show inward status
        queryset = GRN.objects.select_related(
            'delivery_location', 'receiver', 'otp', 'created_by'
        ).prefetch_related(
            Prefetch('lines', queryset=GRNLine.objects.select_related(
                'dn', 
                'warehouse_inward',
                'warehouse_inward__inwarded_by',
                'warehouse_inward__inwarded_by__location'
            ).order_by('line_number'))
        ).filter(
            delivery_location__is_warehouse=True
        ).order_by('-created_at')
        
        # Get warehouse filter from URL parameter (not session)
        warehouse_id = self.request.GET.get('warehouse_id')
        
        if warehouse_id:
            try:
                warehouse = Location.objects.get(id=warehouse_id, is_warehouse=True)
                queryset = queryset.filter(delivery_location=warehouse)
            except Location.DoesNotExist:
                # If invalid warehouse ID, show no results
                queryset = GRN.objects.none()
        else:
            # If no warehouse selected, show first warehouse by default for staff
            if self.request.user.is_staff or self.request.user.is_superuser:
                first_warehouse = Location.objects.filter(is_warehouse=True).first()
                if first_warehouse:
                    queryset = queryset.filter(delivery_location=first_warehouse)
                else:
                    queryset = GRN.objects.none()
            else:
                # Non-admin users can view all warehouse GRNs
                # They can select which warehouse to process inward from
                pass

        # Apply search and filters
        queryset = self.apply_filters(queryset)
        return queryset

    def apply_filters(self, queryset):
        """Apply various filters to the queryset"""
        # Search filter
        q = self.request.GET.get('q')
        if q:
            queryset = queryset.filter(
                Q(lines__sender_name__icontains=q) |
                Q(receiver__name__icontains=q) |
                Q(receiver__username__icontains=q) |
                Q(id__icontains=q)
            ).distinct()

        # Date range filters
        start_date = self.request.GET.get('start_date')
        if start_date:
            try:
                start = make_aware(datetime.strptime(start_date, '%Y-%m-%d'))
                queryset = queryset.filter(created_at__gte=start)
            except ValueError:
                pass

        end_date = self.request.GET.get('end_date')
        if end_date:
            try:
                end = make_aware(datetime.strptime(end_date + ' 23:59:59', '%Y-%m-%d %H:%M:%S'))
                queryset = queryset.filter(created_at__lte=end)
            except ValueError:
                pass

        # Parcel type filter
        parcel_type = self.request.GET.get('parcel_type')
        if parcel_type:
            valid_types = [choice[0] for choice in GRNLine.PARCEL_TYPE_CHOICES]
            if parcel_type in valid_types:
                queryset = queryset.filter(lines__parcel_type=parcel_type).distinct()

        # Status filter
        status = self.request.GET.get('status')
        if status == 'delivered':
            queryset = queryset.filter(lines__dn__isnull=False).annotate(
                delivered_lines=Count('lines__dn'),
                total_lines_count=Count('lines')
            ).filter(delivered_lines=F('total_lines_count')).distinct()
        elif status == 'pending':
            queryset = queryset.filter(lines__dn__isnull=True).distinct()

        # Courier filter
        courier = self.request.GET.get('courier')
        if courier:
            valid_couriers = [choice[0] for choice in GRNLine.COURIER_CHOICES]
            if courier in valid_couriers:
                queryset = queryset.filter(lines__courier_name=courier).distinct()

        # Phone number filter
        phone = self.request.GET.get('phone')
        if phone:
            queryset = queryset.filter(lines__phone__icontains=phone).distinct()

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Get current location
        current_location = self.get_current_location()
        
        # Get all warehouse locations
        warehouse_locations = Location.objects.filter(is_warehouse=True).order_by('name')
        
        # Add location data to context
        context.update({
            'current_location': current_location,
            'locations': Location.objects.all().order_by('name'),
            'warehouse_locations': warehouse_locations,
            'parcel_type_choices': GRNLine.PARCEL_TYPE_CHOICES,
            'courier_choices': GRNLine.COURIER_CHOICES,
            'current_filters': self.get_current_filters(),
        })
        
        return context

    def get_current_location(self):
        """Get the current warehouse being viewed from URL parameter"""
        warehouse_id = self.request.GET.get('warehouse_id')
        selected_warehouse = None
        
        if warehouse_id:
            try:
                selected_warehouse = Location.objects.get(id=warehouse_id, is_warehouse=True)
            except Location.DoesNotExist:
                pass
        
        # If no warehouse selected, use first warehouse as default
        if not selected_warehouse:
            selected_warehouse = Location.objects.filter(is_warehouse=True).first()

        return selected_warehouse

    def get_current_filters(self):
        """Get current filter values to maintain state"""
        return {
            'q': self.request.GET.get('q', ''),
            'start_date': self.request.GET.get('start_date', ''),
            'end_date': self.request.GET.get('end_date', ''),
            'parcel_type': self.request.GET.get('parcel_type', ''),
            'status': self.request.GET.get('status', ''),
            'courier': self.request.GET.get('courier', ''),
            'phone': self.request.GET.get('phone', ''),
        }


@csrf_protect
@login_required
def warehouse_inward_process(request):
    """Handle warehouse inward processing for selected GRN lines - Stage 1: Receiving
    When receptionist inwards items from warehouse:
    1. Creates a NEW GRN with receptionist's location
    2. Moves selected lines to the new GRN
    3. Original GRN remains visible to warehouse user
    4. OTP is generated for new GRN and sent to receiver"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})
    
    try:
        import json
        data = json.loads(request.body)
        selected_lines = data.get('selected_lines', [])
        remark = data.get('remark', '').strip()
        
        if not selected_lines:
            return JsonResponse({'success': False, 'error': 'No items selected'})
        
        # Check if user has a location assigned
        if not request.user.location:
            return JsonResponse({'success': False, 'error': 'You must have a location assigned to process inward'})
        
        # Process selected lines grouped by original GRN
        inwarded_count = 0
        errors = []
        grns_to_process = {}  # Group lines by original GRN: {grn_id: [lines]}
        new_grns_created = []  # Track newly created GRNs
        
        with transaction.atomic():
            # First, validate and group all selected lines by their original GRN
            for line_id in selected_lines:
                try:
                    line = GRNLine.objects.select_related('grn', 'grn__delivery_location', 'grn__receiver').get(id=line_id)
                    
                    # Check if it's a warehouse location
                    if not line.grn.delivery_location.is_warehouse:
                        errors.append(f"Line {line.line_number} is not from a warehouse location")
                        continue
                    
                    # Check if already inwarded
                    if hasattr(line, 'warehouse_inward'):
                        errors.append(f"Line {line.line_number} is already inwarded")
                        continue
                    
                    # Group by original GRN
                    if line.grn.id not in grns_to_process:
                        grns_to_process[line.grn.id] = {
                            'grn': line.grn,
                            'lines': []
                        }
                    grns_to_process[line.grn.id]['lines'].append(line)
                    
                except GRNLine.DoesNotExist:
                    errors.append(f"Line ID {line_id} not found")
                except Exception as e:
                    errors.append(f"Error validating line {line_id}: {str(e)}")
            
            # Process each original GRN's selected lines
            for grn_id, grn_data in grns_to_process.items():
                try:
                    original_grn = grn_data['grn']
                    lines_to_move = grn_data['lines']
                    old_location = original_grn.delivery_location
                    
                    # Create NEW GRN with receptionist's location
                    new_grn = GRN.objects.create(
                        receiver=original_grn.receiver,
                        delivery_location=request.user.location,
                        created_by=request.user,  # Receptionist who processed the inward
                        place=f"Transferred from {old_location.name}"
                    )
                    
                    # Move selected lines to new GRN and create warehouse inward records
                    moved_lines = []
                    for line in lines_to_move:
                        # Store old line number for reference
                        old_line_number = line.line_number
                        
                        # Move line to new GRN (will auto-assign new line numbers)
                        line.grn = new_grn
                        line.save()
                        
                        # Create warehouse inward record
                        WarehouseInward.objects.create(
                            grn_line=line,
                            inwarded_by=request.user,
                            inward_remark=remark or f"Transferred from {old_location.name} - Original GRN {original_grn.id} Line {old_line_number}"
                        )
                        
                        moved_lines.append(line)
                        inwarded_count += 1
                    
                    # Renumber remaining lines in original GRN (if any)
                    remaining_lines = GRNLine.objects.filter(grn=original_grn).order_by('line_number')
                    for idx, remaining_line in enumerate(remaining_lines, start=1):
                        if remaining_line.line_number != idx:
                            remaining_line.line_number = idx
                            remaining_line.save()
                    
                    # Renumber lines in new GRN
                    new_grn_lines = GRNLine.objects.filter(grn=new_grn).order_by('line_number')
                    for idx, new_line in enumerate(new_grn_lines, start=1):
                        if new_line.line_number != idx:
                            new_line.line_number = idx
                            new_line.save()
                    
                    # Generate OTP for the new GRN and send email to receiver
                    try:
                        otp_code = OTP.generate_otp()
                        OTP.objects.create(otp=otp_code, grn=new_grn, valid=True)
                        
                        # Send OTP email to receiver
                        send_warehouse_inward_otp_email(
                            new_grn, 
                            otp_code, 
                            moved_lines, 
                            old_location, 
                            request.user.location
                        )
                        
                        new_grns_created.append(new_grn.id)
                        
                    except Exception as e:
                        errors.append(f"Error generating OTP for new GRN: {str(e)}")
                        
                except Exception as e:
                    errors.append(f"Error processing GRN {grn_id}: {str(e)}")
        
        if inwarded_count > 0:
            grn_list = ', '.join([f"GRN {grn_id}" for grn_id in new_grns_created])
            message = f'Successfully received {inwarded_count} item(s) from warehouse. '
            message += f'New GRN(s) created: {grn_list}. '
            message += f'Items are now at {request.user.location.name}. '
            message += 'OTP has been generated and sent to receivers for parcel collection.'
            
            return JsonResponse({
                'success': True,
                'message': message,
                'new_grns': new_grns_created,
                'errors': errors if errors else None
            })
        else:
            return JsonResponse({
                'success': False,
                'error': 'No items were inwarded',
                'errors': errors
            })
            
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON data'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


def send_warehouse_inward_otp_email(grn, otp_code, lines, from_location, to_location):
    """Send OTP email when warehouse items are inwarded to floor location"""
    subject = f'Parcel Ready for Collection - GRN {grn.id}'
    
    # Build message with line items
    lines_info = []
    for line in lines:
        line_info = f"""
Line {line.line_number}:
  - Sender: {line.sender_name or 'Unknown'}
  - Parcel Type: {line.get_parcel_type_display()}
  - Courier: {line.get_courier_name_display()}
"""
        lines_info.append(line_info)
    
    message = f"""
Dear {grn.receiver.name},

Your parcel(s) have been transferred from {from_location.name} and are now ready for collection at {to_location.name}.

GRN ID: {grn.id}
Number of Parcels: {len(lines)}

{''.join(lines_info)}

Please visit {to_location.name} with this OTP to collect your parcel(s):
OTP: {otp_code}

This OTP is valid for 24 hours.

Best regards,
Parcel Tracking Team
"""
    try:
        send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            [grn.receiver.email],
            fail_silently=False,
        )
    except Exception as e:
        raise Exception(f'Failed to send OTP email: {str(e)}')


@csrf_protect
@login_required
def assign_to_floor(request):
    """Handle floor assignment for inwarded items - Stage 2: Floor Assignment"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})
    
    try:
        import json
        data = json.loads(request.body)
        selected_inwards = data.get('selected_inwards', [])
        floor = data.get('floor', '').strip()
        rack = data.get('rack', '').strip()
        remark = data.get('remark', '').strip()
        
        if not selected_inwards:
            return JsonResponse({'success': False, 'error': 'No items selected'})
        
        if not floor:
            return JsonResponse({'success': False, 'error': 'Floor is required'})
        
        # Process each selected inward
        assigned_count = 0
        errors = []
        
        with transaction.atomic():
            for inward_id in selected_inwards:
                try:
                    inward = WarehouseInward.objects.select_related('grn_line').get(id=inward_id)
                    
                    # Check if already assigned to floor
                    if inward.is_on_floor:
                        errors.append(f"Item {inward.grn_line.line_number} already assigned to floor")
                        continue
                    
                    # Assign to floor
                    inward.floor = floor
                    inward.rack = rack
                    inward.assigned_to_floor_by = request.user
                    inward.assigned_to_floor_at = timezone.now()
                    inward.floor_remark = remark
                    inward.save()
                    
                    assigned_count += 1
                    
                except WarehouseInward.DoesNotExist:
                    errors.append(f"Inward ID {inward_id} not found")
                except Exception as e:
                    errors.append(f"Error processing inward {inward_id}: {str(e)}")
        
        if assigned_count > 0:
            return JsonResponse({
                'success': True,
                'message': f'Successfully assigned {assigned_count} item(s) to floor {floor}',
                'errors': errors if errors else None
            })
        else:
            return JsonResponse({
                'success': False,
                'error': 'No items were assigned to floor',
                'errors': errors
            })
            
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON data'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@csrf_protect
@login_required
def warehouse_floor_delivery(request):
    """Handle delivery from warehouse floor to receiver - Stage 3: Final Delivery"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})
    
    try:
        import json
        data = json.loads(request.body)
        selected_inwards = data.get('selected_inwards', [])
        delivery_remark = data.get('remark', '').strip()
        
        if not selected_inwards:
            return JsonResponse({'success': False, 'error': 'No items selected'})
        
        delivered_count = 0
        errors = []
        
        with transaction.atomic():
            for inward_id in selected_inwards:
                try:
                    inward = WarehouseInward.objects.select_related(
                        'grn_line', 'grn_line__grn', 'grn_line__grn__receiver'
                    ).get(id=inward_id)
                    
                    # Check if already delivered
                    if inward.delivered_to_receiver:
                        errors.append(f"Item already delivered to receiver")
                        continue
                    
                    # Check if DN already exists
                    if hasattr(inward.grn_line, 'dn'):
                        errors.append(f"DN already exists for this item")
                        continue
                    
                    # Mark as delivered to receiver
                    inward.delivered_to_receiver = True
                    inward.delivered_at = timezone.now()
                    inward.delivered_by = request.user
                    inward.delivery_remark = delivery_remark
                    inward.save()
                    
                    # Create DN for final delivery
                    DN.objects.create(
                        grn_line=inward.grn_line,
                        remark=delivery_remark or f"Parcel collected from warehouse by receiver via {request.user.name}",
                        from_warehouse_inward=True
                    )
                    
                    delivered_count += 1
                    
                except WarehouseInward.DoesNotExist:
                    errors.append(f"Inward ID {inward_id} not found")
                except Exception as e:
                    errors.append(f"Error processing inward {inward_id}: {str(e)}")
        
        if delivered_count > 0:
            return JsonResponse({
                'success': True,
                'message': f'Successfully delivered {delivered_count} parcel(s). Receiver has collected the items.',
                'errors': errors if errors else None
            })
        else:
            return JsonResponse({
                'success': False,
                'error': 'No items were delivered',
                'errors': errors
            })
            
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON data'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


class WarehouseFloorDeliveryView(LoginRequiredMixin, ListView):
    """View for warehouse staff to deliver items from warehouse to floor/receiver"""
    model = WarehouseInward
    template_name = 'grn/warehouse_floor_delivery.html'
    context_object_name = 'inwards'
    paginate_by = 100

    def get_queryset(self):
        # Get inwarded items that haven't been delivered to receiver yet
        queryset = WarehouseInward.objects.select_related(
            'grn_line',
            'grn_line__grn',
            'grn_line__grn__receiver',
            'grn_line__grn__delivery_location',
            'inwarded_by'
        ).filter(
            delivered_to_receiver=False,  # Only pending floor deliveries
            grn_line__grn__delivery_location__is_warehouse=True
        ).order_by('-inwarded_at')
        
        # Apply location filtering
        current_location_id = self.request.session.get('current_location_id')
        
        if self.request.user.is_staff or self.request.user.is_superuser:
            if current_location_id:
                try:
                    current_location = Location.objects.get(id=current_location_id, is_warehouse=True)
                    queryset = queryset.filter(grn_line__grn__delivery_location=current_location)
                except Location.DoesNotExist:
                    return WarehouseInward.objects.none()
        else:
            if self.request.user.location and self.request.user.location.is_warehouse:
                queryset = queryset.filter(grn_line__grn__delivery_location=self.request.user.location)
            else:
                return WarehouseInward.objects.none()

        # Apply filters
        queryset = self.apply_filters(queryset)
        return queryset

    def apply_filters(self, queryset):
        """Apply various filters to the queryset"""
        # Search filter
        q = self.request.GET.get('q')
        if q:
            queryset = queryset.filter(
                Q(grn_line__sender_name__icontains=q) |
                Q(grn_line__grn__receiver__name__icontains=q) |
                Q(grn_line__grn__id__icontains=q) |
                Q(floor__icontains=q) |
                Q(rack__icontains=q)
            )

        # Date range filters
        start_date = self.request.GET.get('start_date')
        if start_date:
            try:
                start = make_aware(datetime.strptime(start_date, '%Y-%m-%d'))
                queryset = queryset.filter(inwarded_at__gte=start)
            except ValueError:
                pass

        end_date = self.request.GET.get('end_date')
        if end_date:
            try:
                end = make_aware(datetime.strptime(end_date + ' 23:59:59', '%Y-%m-%d %H:%M:%S'))
                queryset = queryset.filter(inwarded_at__lte=end)
            except ValueError:
                pass

        # Floor filter
        floor = self.request.GET.get('floor')
        if floor:
            queryset = queryset.filter(floor__icontains=floor)

        # Rack filter
        rack = self.request.GET.get('rack')
        if rack:
            queryset = queryset.filter(rack__icontains=rack)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Get current location
        current_location = self.get_current_location()
        
        # Add context data
        context.update({
            'current_location': current_location,
            'locations': Location.objects.filter(is_warehouse=True).order_by('name'),
            'current_filters': self.get_current_filters(),
        })
        
        return context

    def get_current_location(self):
        """Get the current location for the user"""
        current_location_id = self.request.session.get('current_location_id')
        current_location = None
        
        if current_location_id:
            try:
                current_location = Location.objects.get(id=current_location_id, is_warehouse=True)
            except Location.DoesNotExist:
                if 'current_location_id' in self.request.session:
                    del self.request.session['current_location_id']
        
        if not (self.request.user.is_staff or self.request.user.is_superuser):
            if self.request.user.location and self.request.user.location.is_warehouse:
                current_location = self.request.user.location

        return current_location

    def get_current_filters(self):
        """Get current filter values to maintain state"""
        return {
            'q': self.request.GET.get('q', ''),
            'start_date': self.request.GET.get('start_date', ''),
            'end_date': self.request.GET.get('end_date', ''),
            'floor': self.request.GET.get('floor', ''),
            'rack': self.request.GET.get('rack', ''),
        }


def has_location_permission(user, location, session):
    """Helper function to check if user has permission for a location"""
    if user.is_staff or user.is_superuser:
        current_location_id = session.get('current_location_id')
        if current_location_id:
            try:
                current_location = Location.objects.get(id=current_location_id)
                return location == current_location
            except Location.DoesNotExist:
                return False
        return True
    else:
        return user.location == location if user.location else False


class WarehouseInwardTrackingView(LoginRequiredMixin, ListView):
    """View to show detailed tracking of all warehouse inward operations
    Displays all inwarded items with complete journey from warehouse to delivery"""
    model = WarehouseInward
    template_name = 'grn/warehouse_inward_tracking.html'
    context_object_name = 'inwards'
    paginate_by = 100

    def get_queryset(self):
        # Get all warehouse inward records with related data
        queryset = WarehouseInward.objects.select_related(
            'grn_line',
            'grn_line__grn',
            'grn_line__grn__receiver',
            'grn_line__grn__delivery_location',
            'grn_line__grn__created_by',
            'grn_line__dn',
            'inwarded_by',
            'inwarded_by__location',
            'assigned_to_floor_by',
            'delivered_by'
        ).order_by('-inwarded_at')
        
        # Apply location filtering based on user permissions
        if self.request.user.is_staff or self.request.user.is_superuser:
            # Admin can filter by location if specified
            location_filter_id = self.request.GET.get('location_filter')
            if location_filter_id:
                try:
                    location = Location.objects.get(id=location_filter_id)
                    # Filter by inwarded_by location (where items were received)
                    queryset = queryset.filter(inwarded_by__location=location)
                except Location.DoesNotExist:
                    pass
            # Otherwise show all warehouse inwards
        else:
            # Non-admin users see only inwards for their location
            if self.request.user.location:
                queryset = queryset.filter(inwarded_by__location=self.request.user.location)
            else:
                return WarehouseInward.objects.none()

        # Apply filters
        queryset = self.apply_filters(queryset)
        return queryset

    def apply_filters(self, queryset):
        """Apply various filters to the queryset"""
        
        # Search filter
        q = self.request.GET.get('q')
        if q:
            queryset = queryset.filter(
                Q(grn_line__sender_name__icontains=q) |
                Q(grn_line__grn__receiver__name__icontains=q) |
                Q(grn_line__grn__receiver__username__icontains=q) |
                Q(grn_line__grn__id__icontains=q) |
                Q(grn_line__grn__delivery_location__name__icontains=q) |
                Q(inwarded_by__name__icontains=q) |
                Q(floor__icontains=q) |
                Q(rack__icontains=q)
            )

        # Date range filters
        start_date = self.request.GET.get('start_date')
        if start_date:
            try:
                start = make_aware(datetime.strptime(start_date, '%Y-%m-%d'))
                queryset = queryset.filter(inwarded_at__gte=start)
            except ValueError:
                pass

        end_date = self.request.GET.get('end_date')
        if end_date:
            try:
                end = make_aware(datetime.strptime(end_date + ' 23:59:59', '%Y-%m-%d %H:%M:%S'))
                queryset = queryset.filter(inwarded_at__lte=end)
            except ValueError:
                pass

        # Stage filter (received/on_floor/delivered)
        stage = self.request.GET.get('stage')
        if stage == 'received':
            # Items received but not assigned to floor yet
            queryset = queryset.filter(floor__isnull=True, delivered_to_receiver=False)
        elif stage == 'on_floor':
            # Items on floor but not delivered yet
            queryset = queryset.filter(floor__isnull=False, delivered_to_receiver=False)
        elif stage == 'delivered':
            # Items delivered to receiver
            queryset = queryset.filter(delivered_to_receiver=True)

        # Warehouse filter (original warehouse location)
        warehouse = self.request.GET.get('warehouse')
        if warehouse:
            queryset = queryset.filter(grn_line__grn__delivery_location__name__icontains=warehouse)

        # Receiver location filter (where items were inwarded to)
        receiver_location = self.request.GET.get('receiver_location')
        if receiver_location:
            queryset = queryset.filter(inwarded_by__location__name__icontains=receiver_location)

        # Floor filter
        floor = self.request.GET.get('floor')
        if floor:
            queryset = queryset.filter(floor__icontains=floor)

        # Rack filter
        rack = self.request.GET.get('rack')
        if rack:
            queryset = queryset.filter(rack__icontains=rack)

        # Receiver filter
        receiver = self.request.GET.get('receiver')
        if receiver:
            queryset = queryset.filter(
                Q(grn_line__grn__receiver__name__icontains=receiver) |
                Q(grn_line__grn__receiver__username__icontains=receiver)
            )

        # Parcel type filter
        parcel_type = self.request.GET.get('parcel_type')
        if parcel_type:
            valid_types = [choice[0] for choice in GRNLine.PARCEL_TYPE_CHOICES]
            if parcel_type in valid_types:
                queryset = queryset.filter(grn_line__parcel_type=parcel_type)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Get selected location if filter is applied
        location_filter_id = self.request.GET.get('location_filter')
        selected_location = None
        
        if location_filter_id:
            try:
                selected_location = Location.objects.get(id=location_filter_id)
            except Location.DoesNotExist:
                pass
        
        # Get current location for non-admin users
        current_location = None
        if not (self.request.user.is_staff or self.request.user.is_superuser):
            current_location = self.request.user.location
        
        # Calculate statistics
        all_inwards = self.get_queryset()
        total_inwards = all_inwards.count()
        
        # Count by stage
        received_count = all_inwards.filter(floor__isnull=True, delivered_to_receiver=False).count()
        on_floor_count = all_inwards.filter(floor__isnull=False, delivered_to_receiver=False).count()
        delivered_count = all_inwards.filter(delivered_to_receiver=True).count()
        
        # Get unique warehouses and receiver locations - FIXED
        warehouses = Location.objects.filter(
            is_warehouse=True,
            grn_deliveries__lines__warehouse_inward__isnull=False
        ).distinct().order_by('name')
        
        receiver_locations = Location.objects.filter(
            users__warehouse_inwards__isnull=False
        ).distinct().order_by('name')
        
        # Add context data
        context.update({
            'current_location': current_location,
            'selected_location': selected_location,
            'locations': Location.objects.all().order_by('name'),
            'warehouses': warehouses,
            'receiver_locations': receiver_locations,
            'parcel_type_choices': GRNLine.PARCEL_TYPE_CHOICES,
            'current_filters': self.get_current_filters(),
            'total_inwards': total_inwards,
            'received_count': received_count,
            'on_floor_count': on_floor_count,
            'delivered_count': delivered_count,
        })
        
        return context
    
    def get_current_filters(self):
        """Get current filter values to maintain state"""
        return {
            'q': self.request.GET.get('q', ''),
            'start_date': self.request.GET.get('start_date', ''),
            'end_date': self.request.GET.get('end_date', ''),
            'stage': self.request.GET.get('stage', ''),
            'warehouse': self.request.GET.get('warehouse', ''),
            'receiver_location': self.request.GET.get('receiver_location', ''),
            'floor': self.request.GET.get('floor', ''),
            'rack': self.request.GET.get('rack', ''),
            'receiver': self.request.GET.get('receiver', ''),
            'parcel_type': self.request.GET.get('parcel_type', ''),
            'location_filter': self.request.GET.get('location_filter', ''),
        }