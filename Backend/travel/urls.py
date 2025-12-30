"""Travel app URL routes"""
from django.urls import path
from . import views

app_name = 'travel'

urlpatterns = [
    # API endpoints
    path('api/search/', views.search_travel, name='search'),
    path('api/itinerary/', views.itinerary_list, name='itinerary_list'),
    path('api/itinerary/<int:itinerary_id>/', views.itinerary_detail, name='itinerary_detail'),
    path('api/itinerary/<int:itinerary_id>/items/', views.itinerary_items, name='itinerary_items'),
    path('api/events/', views.search_events, name='search_events'),
]
