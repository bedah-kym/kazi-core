"""Travel app views (REST API endpoints)"""
import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from django.shortcuts import get_object_or_404

from .models import Itinerary, ItineraryItem, Event
from .serializers import ItinerarySerializer, ItineraryItemSerializer, EventSerializer

logger = logging.getLogger(__name__)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
async def search_travel(request):
    """
    Search for travel items (buses, hotels, flights, transfers, events)
    
    POST /api/travel/search/
    {
        "search_type": "buses|hotels|flights|transfers|events",
        "parameters": {...}
    }
    """
    try:
        from orchestration.mcp_router import get_mcp_router
        
        search_type = request.data.get('search_type')
        parameters = request.data.get('parameters', {})
        
        router = get_mcp_router()
        
        # Map search_type to connector action
        action_map = {
            'buses': 'search_buses',
            'hotels': 'search_hotels',
            'flights': 'search_flights',
            'transfers': 'search_transfers',
            'events': 'search_events',
        }
        
        action = action_map.get(search_type)
        if not action:
            return Response(
                {'error': f'Unknown search type: {search_type}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Route to connector
        context = {
            'user_id': request.user.id,
            'room_id': request.data.get('room_id')
        }
        
        result = await router.route(
            intent={'action': action, 'parameters': parameters},
            user_context=context
        )
        
        return Response(result)
        
    except Exception as e:
        logger.error(f"Search error: {e}")
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def itinerary_list_api(request):
    """
    GET: List user's itineraries
    POST: Create new itinerary
    """
    if request.method == 'GET':
        itineraries = Itinerary.objects.filter(user=request.user)
        serializer = ItinerarySerializer(itineraries, many=True)
        return Response(serializer.data)
    
    elif request.method == 'POST':
        serializer = ItinerarySerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(user=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET', 'PUT', 'DELETE'])
@permission_classes([IsAuthenticated])
def itinerary_detail(request, itinerary_id):
    """
    GET: Retrieve itinerary
    PUT: Update itinerary
    DELETE: Delete itinerary
    """
    itinerary = get_object_or_404(Itinerary, id=itinerary_id, user=request.user)
    
    if request.method == 'GET':
        serializer = ItinerarySerializer(itinerary)
        return Response(serializer.data)
    
    elif request.method == 'PUT':
        serializer = ItinerarySerializer(itinerary, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    elif request.method == 'DELETE':
        itinerary.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def itinerary_items(request, itinerary_id):
    """
    GET: List items in itinerary
    POST: Add item to itinerary
    """
    itinerary = get_object_or_404(Itinerary, id=itinerary_id, user=request.user)
    
    if request.method == 'GET':
        items = itinerary.items.all().order_by('sort_order', 'start_datetime')
        serializer = ItineraryItemSerializer(items, many=True)
        return Response(serializer.data)
    
    elif request.method == 'POST':
        data = request.data.copy()
        data['itinerary'] = itinerary.id
        serializer = ItineraryItemSerializer(data=data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def search_events(request):
    """
    Search for events
    Query params: location, category, date_range
    """
    try:
        location = request.query_params.get('location')
        category = request.query_params.get('category')
        
        events = Event.objects.all()
        
        if location:
            events = events.filter(location_country__icontains=location)
        
        if category:
            events = events.filter(category__icontains=category)
        
        serializer = EventSerializer(events, many=True)
        return Response(serializer.data)
        
    except Exception as e:
        logger.error(f"Event search error: {e}")
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

# --- HTML Views ---
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required

@login_required
def plan_trip_wizard(request):
    """Render the Trip Planning Wizard"""
    return render(request, 'travel/plan_trip.html')

@login_required
def itinerary_list(request):
    """Render the Itinerary List (user-friendly UI)"""
    itineraries = Itinerary.objects.filter(user=request.user).order_by('-created_at')
    return render(request, 'travel/itinerary_list.html', {'itineraries': itineraries})

@login_required
def view_itinerary(request, itinerary_id):
    """Render the Itinerary Detail View"""
    itinerary = get_object_or_404(Itinerary, id=itinerary_id, user=request.user)
    return render(request, 'travel/itinerary_detail.html', {'itinerary': itinerary})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def archive_itinerary(request, itinerary_id):
    """Archive an itinerary"""
    itinerary = get_object_or_404(Itinerary, id=itinerary_id, user=request.user)
    itinerary.status = 'archived'
    itinerary.save()
    # Redirect if HTML request, else JSON
    if request.accepted_renderer.format == 'html':
        return redirect('travel:itinerary_list')
    return Response({'status': 'success', 'message': 'Itinerary archived'})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def delete_itinerary_view(request, itinerary_id):
    """Delete an itinerary (Hard Delete)"""
    itinerary = get_object_or_404(Itinerary, id=itinerary_id, user=request.user)
    itinerary.delete()
    if request.accepted_renderer.format == 'html':
        return redirect('travel:itinerary_list')
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def delete_itinerary_item_view(request, item_id):
    """Delete an itinerary item"""
    item = get_object_or_404(ItineraryItem, id=item_id, itinerary__user=request.user)
    itinerary_id = item.itinerary.id
    item.delete()
    if request.accepted_renderer.format == 'html':
        return redirect('travel:view_itinerary', itinerary_id=itinerary_id)
    return Response(status=status.HTTP_204_NO_CONTENT)
