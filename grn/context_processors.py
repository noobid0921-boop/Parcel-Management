from .models import Location

def location_context(request):
    """Add location data to all templates"""
    context = {
        'locations': Location.objects.all().order_by('name'),
    }
    
    # Add current location
    if hasattr(request, 'session'):
        current_location_id = request.session.get('current_location_id')
        if current_location_id:
            try:
                context['current_location'] = Location.objects.get(id=current_location_id)
            except Location.DoesNotExist:
                context['current_location'] = None
        elif hasattr(request, 'user') and request.user.is_authenticated:
            if not (request.user.is_staff or request.user.is_superuser):
                context['current_location'] = getattr(request.user, 'location', None)
    
    return context