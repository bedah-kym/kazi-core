"""
Travel Planning Services
High-level services for itinerary building, composition, and management
"""
import logging
import json
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from django.db import transaction
from django.utils import timezone
from asgiref.sync import sync_to_async

from orchestration.llm_client import get_llm_client, extract_json
from travel.models import Itinerary, ItineraryItem, BookingReference
from orchestration.mcp_router import MCPRouter

logger = logging.getLogger(__name__)


class ItineraryBuilder:
    """
    High-level service to compose itineraries from search results
    Uses LLM to generate natural summaries and recommendations
    """
    
    def __init__(self):
        self.llm_client = get_llm_client()
        self.router = MCPRouter()
    
    async def create_from_searches(self, user_id: int, trip_name: str, 
                                  origin: str, destination: str,
                                  start_date: str, end_date: str,
                                  search_results: Dict[str, List[Dict]]) -> Itinerary:
        """
        Create an itinerary from search results
        
        Args:
            user_id: User creating the itinerary
            trip_name: Name of the trip
            origin: Origin city
            destination: Destination city
            start_date: Trip start date (YYYY-MM-DD)
            end_date: Trip end date (YYYY-MM-DD)
            search_results: {
                'buses': [...],
                'hotels': [...],
                'flights': [...],
                'transfers': [...],
                'events': [...]
            }
        
        Returns:
            Itinerary object with items added
        """
        from django.contrib.auth.models import User
        
        user = await sync_to_async(User.objects.get)(id=user_id)
        
        # Parse dates and make timezone-aware
        start = datetime.strptime(start_date, '%Y-%m-%d')
        end = datetime.strptime(end_date, '%Y-%m-%d')
        # Make timezone-aware (Django USE_TZ=True requires this)
        start = timezone.make_aware(start) if timezone.is_naive(start) else start
        end = timezone.make_aware(end) if timezone.is_naive(end) else end
        
        def _coerce_item_datetime(value: Optional[str], fallback: datetime) -> datetime:
            if not value:
                return fallback
            raw = str(value).strip()
            try:
                if len(raw) == 10:
                    dt = datetime.strptime(raw, '%Y-%m-%d')
                    dt = dt.replace(hour=fallback.hour, minute=fallback.minute)
                else:
                    dt = datetime.fromisoformat(raw)
            except Exception:
                return fallback
            return timezone.make_aware(dt) if timezone.is_naive(dt) else dt

        # Create itinerary
        itinerary = await sync_to_async(Itinerary.objects.create)(
            user=user,
            title=trip_name,
            description=f"Trip from {origin} to {destination}",
            start_date=start,
            end_date=end,
            status='draft',
            metadata={'origin': origin, 'destination': destination}
        )
        
        # Integrate LLM Composer
        from travel.llm_composer import LLMComposer
        
        logger.info(f"Created itinerary {itinerary.id} for user {user_id}")
        
        items_added = 0
        ai_success = False
        
        # Try AI Composition first
        try:
            composer = LLMComposer()
            trip_details = {
                'origin': origin,
                'destination': destination,
                'start_date': start_date,
                'end_date': end_date
            }
            
            ai_selected_items = await composer.compose_itinerary(trip_details, search_results)
            
            if ai_selected_items:
                for item_data in ai_selected_items:
                    # Determine type (fallback to guessing if _category missing)
                    cat = item_data.get('_category', 'other')
                    
                    # Map category to item_type
                    if 'bus' in cat: item_type = 'bus'
                    elif 'flight' in cat: item_type = 'flight'
                    elif 'hotel' in cat: item_type = 'hotel'
                    elif 'event' in cat: item_type = 'event'
                    elif 'transfer' in cat: item_type = 'transfer'
                    else: item_type = 'activity'

                    # Create Item
                    title = item_data.get('title') or item_data.get('name') or item_data.get('company') or f"{item_type.title()} Option"
                    
                    notes = item_data.get('ai_reasoning', '')
                    start_dt = _coerce_item_datetime(
                        item_data.get('start_datetime') or item_data.get('date'),
                        start,
                    )

                    await sync_to_async(ItineraryItem.objects.create)(
                        itinerary=itinerary,
                        item_type=item_type,
                        title=title,
                        description=json.dumps(item_data),
                        start_datetime=start_dt,
                        price_ksh=item_data.get('price_ksh', 0),
                        status='planned',
                        metadata={
                            'provider': item_data.get('provider', 'unknown'), 
                            'booking_url': item_data.get('booking_url'),
                            'ai_selected': True,
                            'ai_reasoning': notes,
                        }
                    )
                    items_added += 1
                
                ai_success = True
                logger.info(f"AI Composer successfully added {items_added} items.")
                
        except Exception as e:
            logger.error(f"AI Composition failed: {e}. Falling back to standard selection.")
        
        # FALLBACK: Standard Top-N Logic if AI failed or returned nothing
        if not ai_success or items_added == 0:
            logger.info("Using standard Top-N selection fallback.")
            
            # Add buses
            for bus in search_results.get('buses', [])[:3]:  # Add top 3 buses
                start_dt = _coerce_item_datetime(bus.get('departure_datetime'), start)
                item = await sync_to_async(ItineraryItem.objects.create)(
                    itinerary=itinerary,
                    item_type='bus',
                    title=f"{bus.get('company', 'Bus')} - {bus.get('departure_time')} to {bus.get('arrival_time')}",
                    description=json.dumps(bus),
                    start_datetime=start_dt,
                    price_ksh=bus.get('price_ksh', 0),
                    status='planned',
                    metadata={'provider': 'buupass', 'booking_url': bus.get('booking_url')}
                )
                items_added += 1
            
            # Add hotels
            for hotel in search_results.get('hotels', [])[:2]:  # Add top 2 hotels
                nights = (end - start).days or 1
                total_price = hotel.get('price_ksh', 0) * nights
                
                item = await sync_to_async(ItineraryItem.objects.create)(
                    itinerary=itinerary,
                    item_type='hotel',
                    title=f"{hotel.get('name', 'Hotel')} - {nights} nights",
                    description=json.dumps(hotel),
                    start_datetime=start,
                    end_datetime=end,
                    price_ksh=total_price,
                    status='planned',
                    metadata={'provider': 'booking', 'booking_url': hotel.get('booking_url'), 'nights': nights}
                )
                items_added += 1
            
            # Add flights if available
            for flight in search_results.get('flights', [])[:1]:  # Add top flight
                start_dt = _coerce_item_datetime(flight.get('departure_datetime'), start)
                item = await sync_to_async(ItineraryItem.objects.create)(
                    itinerary=itinerary,
                    item_type='flight',
                    title=f"{flight.get('airline', 'Flight')} {flight.get('flight_number', '')} - {flight.get('departure_time')}",
                    description=json.dumps(flight),
                    start_datetime=start_dt,
                    price_ksh=flight.get('price_ksh', 0),
                    status='planned',
                    metadata={'provider': 'duffel', 'booking_url': flight.get('booking_url')}
                )
                items_added += 1
            
            # Add transfers
            for transfer in search_results.get('transfers', [])[:1]:  # Add top transfer
                start_dt = _coerce_item_datetime(transfer.get('travel_datetime'), start)
                item = await sync_to_async(ItineraryItem.objects.create)(
                    itinerary=itinerary,
                    item_type='transfer',
                    title=f"{transfer.get('provider', 'Transfer')} - {transfer.get('vehicle_type', 'Vehicle')}",
                    description=json.dumps(transfer),
                    start_datetime=start_dt,
                    price_ksh=transfer.get('price_ksh', 0),
                    status='planned',
                    metadata={'provider': 'karibu', 'booking_url': transfer.get('booking_url')}
                )
                items_added += 1
            
            # Add events
            for event in search_results.get('events', [])[:3]:  # Add top 3 events
                start_dt = _coerce_item_datetime(event.get('start_datetime'), start)
                item = await sync_to_async(ItineraryItem.objects.create)(
                    itinerary=itinerary,
                    item_type='event',
                    title=event.get('title', 'Event'),
                    description=json.dumps(event),
                    start_datetime=start_dt,
                    price_ksh=event.get('price_ksh', 0),
                    status='planned',
                    metadata={'provider': 'eventbrite', 'booking_url': event.get('ticket_url')}
                )
                items_added += 1
        
        logger.info(f"Added {items_added} items to itinerary {itinerary.id}")
        
        return itinerary
    
    async def generate_summary(self, itinerary_id: int) -> str:
        """
        Generate LLM-powered summary of an itinerary
        """
        itinerary = await sync_to_async(Itinerary.objects.select_related('user').get)(id=itinerary_id)
        items = await sync_to_async(lambda: list(itinerary.items.all()))()
        
        # Build context
        item_summaries = []
        for item in items:
            item_summaries.append(f"- {item.title} (KES {item.price_ksh})")
        
        prompt = f"""
        Generate a concise travel itinerary summary for this trip:
        
        Trip: {itinerary.title}
        From {itinerary.start_date.date()} to {itinerary.end_date.date()}
        
        Planned items:
        {chr(10).join(item_summaries)}
        
        Provide a brief, engaging summary (2-3 sentences) highlighting the key activities and approximate budget.
        """
        
        try:
            summary = await self.llm_client.generate_text(
                system_prompt="You are a helpful travel assistant.",
                user_prompt=prompt,
                temperature=0.4,
                max_tokens=400,
            )
            return summary
        except Exception as e:
            logger.error(f"Error generating summary: {str(e)}")
            return f"Trip to {itinerary.title} with {len(items)} planned items"
    
    async def get_recommendations(self, itinerary_id: int, category: str) -> List[Dict]:
        """
        Get LLM-powered recommendations for an itinerary using RecommendationService
        """
        from travel.recommendation_service import RecommendationService
        
        itinerary = await sync_to_async(Itinerary.objects.get)(id=itinerary_id)
        items = await sync_to_async(lambda: list(itinerary.items.values('title', 'item_type', 'start_datetime')))()
        context_items = []
        for item in items:
            context_items.append({
                'title': item.get('title'),
                'time': item.get('start_datetime').isoformat() if item.get('start_datetime') else 'Anytime',
            })

        meta = itinerary.metadata if isinstance(itinerary.metadata, dict) else {}
        destination = meta.get('destination') or itinerary.title or "Unknown"
        
        service = RecommendationService()
        
        if category == 'dining':
            return await service.recommend_dining(destination)
        elif category == 'hidden_gems':
            return await service.get_hidden_gems(destination)
        else:
            # Default to context-aware activity suggestions
            return await service.recommend_activities(destination, context_items)

    async def add_practical_info(self, itinerary_id: int, user_passport_code: str = 'US'):
        """
        Enrich itinerary with Visa and Weather info.
        """
        from travel.practical_service import VisaService, WeatherService
        
        itinerary = await sync_to_async(Itinerary.objects.get)(id=itinerary_id)
        
        # 1. Visa Check
        # Assume destination is always Kenya for this MVP, or parse from itinerary
        destination_code = 'KE' 
        visa_service = VisaService()
        visa_status = visa_service.check_requirements(user_passport_code, destination_code)
        
        # 2. Weather
        weather_service = WeatherService()
        destination = (itinerary.metadata or {}).get('destination') or itinerary.title or "Nairobi"
        weather = await weather_service.get_trip_forecast(destination)
        
        # Update Metadata
        meta = itinerary.metadata or {}
        meta['practical_info'] = {
            'visa_status': visa_status,
            'passport_used': user_passport_code,
            'weather_forecast': weather
        }
        
        itinerary.metadata = meta
        await sync_to_async(itinerary.save)()
        
        return meta


class ExportService:
    """
    Service to export itineraries in various formats
    Supports PDF, JSON, and iCalendar formats
    """
    
    async def export_json(self, itinerary_id: int) -> Dict:
        """Export itinerary as JSON"""
        itinerary = await sync_to_async(Itinerary.objects.get)(id=itinerary_id)
        items = await sync_to_async(lambda: list(itinerary.items.all()))()
        
        return {
            'title': itinerary.title,
            'description': itinerary.description,
            'start_date': itinerary.start_date.isoformat(),
            'end_date': itinerary.end_date.isoformat(),
            'duration_days': itinerary.duration_days,
            'budget_ksh': float(itinerary.budget_ksh or 0),
            'items': [
                {
                    'title': item.title,
                    'type': item.item_type,
                    'date': item.start_datetime.isoformat() if item.start_datetime else None,
                    'price_ksh': float(item.price_ksh or 0),
                    'status': item.status,
                    'notes': item.description or ''
                }
                for item in items
            ],
            'total_cost': float(sum(item.price_ksh or 0 for item in items)),
            'created_at': itinerary.created_at.isoformat(),
            'updated_at': itinerary.updated_at.isoformat()
        }
    
    async def export_ical(self, itinerary_id: int) -> str:
        """Export itinerary as iCalendar format"""
        itinerary = await sync_to_async(Itinerary.objects.get)(id=itinerary_id)
        items = await sync_to_async(lambda: list(itinerary.items.all()))()
        
        # Build iCal format
        ical_lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            f"PRODID:-//Mathia Travel//EN",
            f"CALSCALE:GREGORIAN",
            f"X-WR-CALNAME:{itinerary.title}",
            f"X-WR-TIMEZONE:Africa/Nairobi",
        ]
        
        for item in items:
            if item.start_datetime:
                event_date = item.start_datetime.strftime('%Y%m%d')
                ical_lines.extend([
                    "BEGIN:VEVENT",
                    f"DTSTART:{event_date}",
                    f"SUMMARY:{item.title}",
                    f"DESCRIPTION:{item.description or ''}",
                    "END:VEVENT"
                ])
        
        ical_lines.append("END:VCALENDAR")
        return '\n'.join(ical_lines)
    
    async def export_pdf(self, itinerary_id: int) -> bytes:
        """
        Export itinerary as PDF
        Requires reportlab library
        """
        try:
            from reportlab.lib.pagesizes import letter
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
            from reportlab.lib.styles import getSampleStyleSheet
            from io import BytesIO
            
            itinerary = await sync_to_async(Itinerary.objects.get)(id=itinerary_id)
            items = await sync_to_async(lambda: list(itinerary.items.all()))()
            
            # Create PDF in memory
            pdf_buffer = BytesIO()
            doc = SimpleDocTemplate(pdf_buffer, pagesize=letter)
            
            styles = getSampleStyleSheet()
            story = []
            
            # Title
            story.append(Paragraph(f"<b>{itinerary.title}</b>", styles['Title']))
            story.append(Spacer(1, 12))
            
            # Trip dates
            story.append(Paragraph(
                f"<b>Trip Duration:</b> {itinerary.start_date.date()} to {itinerary.end_date.date()} ({itinerary.duration_days} days)",
                styles['Normal']
            ))
            story.append(Spacer(1, 12))
            
            # Items table
            data = [['Item', 'Type', 'Price (KES)', 'Status']]
            for item in items:
                data.append([
                    item.title[:30],
                    item.get_item_type_display(),
                    f"KES {item.price_ksh:,.0f}",
                    item.status
                ])
            
            # Total
            total = sum(item.price_ksh for item in items)
            data.append(['', '', f"<b>Total: KES {total:,.0f}</b>", ''])
            
            table = Table(data)
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), (0, 0, 0)),
                ('TEXTCOLOR', (0, 0), (-1, 0), (1, 1, 1)),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('GRID', (0, 0), (-1, -1), 1, (0, 0, 0)),
            ]))
            
            story.append(table)
            doc.build(story)
            
            return pdf_buffer.getvalue()
        
        except ImportError:
            logger.warning("reportlab not installed, cannot generate PDF")
            return b'PDF export requires reportlab library'


class BookingOrchestrator:
    """
    Service to manage bookings and redirect to partner booking pages
    Tracks confirmation codes and booking status
    """
    
    async def get_booking_url(self, item_id: int, user_id: int) -> str:
        """
        Get booking URL for an itinerary item
        Adds affiliate parameters and tracking
        """
        item = await sync_to_async(ItineraryItem.objects.get)(id=item_id)
        
        # Extract booking URL from metadata
        booking_url = item.metadata.get('booking_url', '')
        
        # Add affiliate parameters based on provider
        provider = item.metadata.get('provider', '')
        
        if provider == 'booking':
            # Add Booking.com affiliate ID
            affiliate_id = 'MATHIA-TRAVEL-2025'  # Placeholder
            if '?' in booking_url:
                booking_url += f"&aid={affiliate_id}"
            else:
                booking_url += f"?aid={affiliate_id}"
        
        elif provider == 'buupass':
            # Add Buupass referral code
            if '?' in booking_url:
                booking_url += f"&ref=MATHIA"
            else:
                booking_url += f"?ref=MATHIA"
        
        logger.info(f"Generated booking URL for item {item_id}: {booking_url}")
        return booking_url
    
    async def record_booking(self, item_id: int, confirmation_code: str, booking_reference: str) -> BookingReference:
        """Record a completed booking"""
        item = await sync_to_async(ItineraryItem.objects.get)(id=item_id)
        provider = item.metadata.get('provider') if isinstance(item.metadata, dict) else None
        booking_url = item.metadata.get('booking_url') if isinstance(item.metadata, dict) else None
        provider_booking_id = booking_reference or item.provider_id or str(item.id)

        booking_ref = await sync_to_async(BookingReference.objects.create)(
            itinerary_item=item,
            provider=item.provider or provider or "Unknown",
            provider_booking_id=provider_booking_id,
            booking_reference=booking_reference,
            confirmation_code=confirmation_code,
            status='confirmed',
            booking_url=booking_url or item.booking_url or "https://amadeus.com",
            metadata={'booked_at': datetime.now().isoformat()}
        )
        
        # Update item status
        item.status = 'booked'
        await sync_to_async(item.save)()
        
        logger.info(f"Recorded booking for item {item_id}: {confirmation_code}")
        return booking_ref
    
    async def get_booking_status(self, item_id: int) -> Dict:
        """Get booking status and confirmation details"""
        try:
            booking_ref = await sync_to_async(BookingReference.objects.get)(itinerary_item_id=item_id)
            return {
                'status': booking_ref.status,
                'confirmation_code': booking_ref.confirmation_code or booking_ref.provider_booking_id,
                'booking_reference': booking_ref.booking_reference or booking_ref.provider_booking_id,
                'booked_at': booking_ref.booked_at.isoformat() if booking_ref.booked_at else None,
                'expires_at': (
                    booking_ref.booked_at + timedelta(days=30)
                ).isoformat() if booking_ref.booked_at else None
            }
        except BookingReference.DoesNotExist:
            return {
                'status': 'not_booked',
                'confirmation_code': None
            }
