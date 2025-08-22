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
from .models import GRN, GRNLine, OTP, DN, CustomUser, Location
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
                    grn = form.save()
                    
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
                    
                    # Generate OTPs and send emails for each line
                    otp_codes = []
                    for line in lines:
                        otp_code = OTP.generate_otp()
                        OTP.objects.create(otp=otp_code, grn_line=line)
                        otp_codes.append((line, otp_code))
                    
                    # Send combined email with all OTPs
                    self.send_otp_email(grn, otp_codes)
                    
                    messages.success(
                        self.request, 
                        f'GRN {grn.id} created successfully with {len(lines)} lines. OTPs sent to {grn.receiver.email}'
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

    def send_otp_email(self, grn, otp_codes):
        subject = f'Parcel Delivery Notification - GRN {grn.id}'
        
        # Build message with all line items and their OTPs
        lines_info = []
        for line, otp_code in otp_codes:
            line_info = f"""
Line {line.line_number}:
  - Sender: {line.sender_name or 'Unknown'}
  - Parcel Type: {line.get_parcel_type_display()}
  - Courier: {line.get_courier_name_display()}
  - OTP: {otp_code}
"""
            lines_info.append(line_info)
        
        message = f"""
Dear {grn.receiver.name},

You have received {len(otp_codes)} parcel(s) at {grn.delivery_location.name}:

GRN ID: {grn.id}
{''.join(lines_info)}

Please visit the collection center with the respective OTP to collect each parcel.
All OTPs are valid for 24 hours.

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
            'delivery_location', 'receiver'
        ).prefetch_related(
            Prefetch('lines', queryset=GRNLine.objects.select_related('dn', 'otp'))
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
            'delivery_location', 'receiver'
        ).prefetch_related(
            Prefetch('lines', queryset=GRNLine.objects.select_related('dn', 'otp'))
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
        
        # Add line information with their OTPs and DNs
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


@login_required
@require_POST
def grn_delete_view(request, pk):
    """Delete a GRN (function-based view)"""
    # Check if user is admin
    if not (request.user.is_staff or request.user.is_superuser):
        messages.error(request, "You don't have permission to delete GRNs.")
        return redirect('grn:grn_list')

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

    # Delete the GRN
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
        
        # Get GRN line info
        grn_line_id = self.request.GET.get('grn_line_id')
        
        if grn_line_id:
            try:
                grn_line = get_object_or_404(GRNLine, id=grn_line_id)
                
                # Check location permissions
                if not has_location_permission(self.request.user, grn_line.grn.delivery_location, self.request.session):
                    messages.error(self.request, "You don't have permission to verify this GRN line.")
                    return context
                
                context['grn_line'] = grn_line
                context['grn'] = grn_line.grn
                
                # Add OTP info if available
                try:
                    context['otp_obj'] = grn_line.otp
                except OTP.DoesNotExist:
                    context['otp_obj'] = None
                    
            except Exception as e:
                messages.error(self.request, f"Error loading GRN Line: {str(e)}")
                
        return context

    def get_initial(self):
        initial = super().get_initial()
        grn_line_id = self.request.GET.get('grn_line_id')
        if grn_line_id:
            initial['grn_line_id'] = grn_line_id
        return initial

    def form_valid(self, form):
        otp_code = form.cleaned_data['otp']
        
        try:
            # Find OTP and corresponding GRN line
            otp = OTP.objects.select_related('grn_line__grn').get(otp=otp_code, valid=True)
            grn_line = otp.grn_line
            
            # Check location permissions
            if not has_location_permission(self.request.user, grn_line.grn.delivery_location, self.request.session):
                messages.error(self.request, "You don't have permission to verify this GRN line.")
                return self.form_invalid(form)

            # Check if OTP is expired
            if otp.is_expired():
                messages.error(self.request, 'OTP has expired. Please contact the administrator.')
                return self.form_invalid(form)

            # Check if DN already exists for this line
            if hasattr(grn_line, 'dn'):
                messages.error(self.request, 'This item has already been delivered.')
                return self.form_invalid(form)

            # Create DN and invalidate OTP
            with transaction.atomic():
                dn = DN.objects.create(
                    grn_line=grn_line, 
                    remark=f"Verified by {self.request.user.username}"
                )
                otp.valid = False
                otp.save()

            messages.success(
                self.request,
                f'Line {grn_line.line_number} from {grn_line.sender_name} (GRN {grn_line.grn.id}) delivered successfully. '
                f'DN #{dn.id} created.'
            )
            return redirect(self.success_url)

        except OTP.DoesNotExist:
            messages.error(self.request, 'Invalid OTP. Please check the OTP and try again.')
            return self.form_invalid(form)
        except Exception as e:
            messages.error(self.request, f'An error occurred: {str(e)}')
            return self.form_invalid(form)


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
            'grn_line__grn__receiver'
        ).order_by('-created_at')

        # Apply location permissions
        current_location_id = self.request.session.get('current_location_id')
        
        if self.request.user.is_staff or self.request.user.is_superuser:
            # Admin users can see DNs from their selected location
            if current_location_id:
                try:
                    current_location = Location.objects.get(id=current_location_id)
                    queryset = queryset.filter(grn_line__grn__delivery_location=current_location)
                except Location.DoesNotExist:
                    pass
        else:
            # Non-admin users can see DNs from their assigned location
            if self.request.user.location:
                queryset = queryset.filter(grn_line__grn__delivery_location=self.request.user.location)
            else:
                return DN.objects.none()

        # Apply filters
        queryset = self.apply_filters(queryset)
        return queryset

    def apply_filters(self, queryset):
        """Apply filters to the DN queryset"""
        q = self.request.GET.get('q')
        if q:
            queryset = queryset.filter(
                Q(grn_line__sender_name__icontains=q) |
                Q(grn_line__grn__receiver__name__icontains=q) |
                Q(grn_line__grn__receiver__username__icontains=q) |
                Q(id__icontains=q) |
                Q(grn_line__grn__id__icontains=q)
            )

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

        # Validate parcel type filter
        parcel_type = self.request.GET.get('parcel_type')
        if parcel_type:
            valid_types = [choice[0] for choice in GRNLine.PARCEL_TYPE_CHOICES]
            if parcel_type in valid_types:
                queryset = queryset.filter(grn_line__parcel_type=parcel_type)

        # Validate courier filter
        courier = self.request.GET.get('courier')
        if courier:
            valid_couriers = [choice[0] for choice in GRNLine.COURIER_CHOICES]
            if courier in valid_couriers:
                queryset = queryset.filter(grn_line__courier_name=courier)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Get current location
        current_location = self.get_current_location()
        
        # Add context data
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
                if 'current_location_id' in self.request.session:
                    del self.request.session['current_location_id']
        
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
            'courier': self.request.GET.get('courier', ''),
        }


# New view to handle individual GRN line operations
class GRNLineDetailView(LoginRequiredMixin, DetailView):
    model = GRNLine
    template_name = 'grn/grn_line_detail.html'
    context_object_name = 'grn_line'

    def get_queryset(self):
        queryset = GRNLine.objects.select_related(
            'grn__delivery_location', 'grn__receiver', 'dn', 'otp'
        )
        
        # Apply location permissions
        current_location_id = self.request.session.get('current_location_id')
        
        if self.request.user.is_staff or self.request.user.is_superuser:
            if current_location_id:
                try:
                    current_location = Location.objects.get(id=current_location_id)
                    queryset = queryset.filter(grn__delivery_location=current_location)
                except Location.DoesNotExist:
                    pass
        else:
            if self.request.user.location:
                queryset = queryset.filter(grn__delivery_location=self.request.user.location)
            else:
                return GRNLine.objects.none()

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

        return context


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