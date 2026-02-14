#!/usr/bin/env python
"""
Diagnostic script to test all Week 4 features
Run with: docker compose exec web python Backend/diagnose_features.py
"""

import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Backend.settings')
sys.path.insert(0, '/app/Backend')
django.setup()

from django.contrib.auth import get_user_model
from travel.models import Itinerary
from users.quota_service import QuotaService
import requests

User = get_user_model()

print("=" * 60)
print("WEEK 4 FEATURES DIAGNOSTIC")
print("=" * 60)

# 1. Check Templates
print("\n[1] TEMPLATE FILES")
template_paths = [
    "/app/Backend/chatbot/templates/chatbot/chatbot.html",
    "/app/Backend/travel/templates/travel/plan_trip.html",
    "/app/Backend/travel/templates/travel/itinerary_detail.html"
]

for path in template_paths:
    exists = os.path.exists(path)
    size = os.path.getsize(path) if exists else 0
    modified = os.path.getmtime(path) if exists else 0
    print(f"  {'✅' if exists else '❌'} {os.path.basename(path):<30} {size:>8} bytes")
    if exists and 'chatbot.html' in path:
        with open(path, 'r') as f:
            content = f.read()
            has_quotas = 'fetchQuotas' in content
            has_voice = 'toggleVoice' in content
            has_markdown = 'marked.min.js' in content
            print(f"      - Quotas feature: {'✅' if has_quotas else '❌'}")
            print(f"      - Voice feature: {'✅' if has_voice else '❌'}")
            print(f"      - Markdown libs: {'✅' if has_markdown else '❌'}")

# 2. Check URLs
print("\n[2] URL CONFIGURATION")
from Backend.urls import urlpatterns
travel_url = any('travel' in str(pattern) for pattern in urlpatterns)
print(f"  {'✅' if travel_url else '❌'} Travel URLs registered in main urls.py")

# 3. Check Database Models
print("\n[3] DATABASE MODELS")
try:
    user_count = User.objects.count()
    itinerary_count = Itinerary.objects.count()
    print(f"  ✅ Users: {user_count}")
    print(f"  ✅ Itineraries: {itinerary_count}")
except Exception as e:
    print(f"  ❌ Database error: {e}")

# 4. Check API Keys
print("\n[4] API KEYS CONFIGURATION")
keys_to_check = {
    'ANTHROPIC_API_KEY': os.environ.get('ANTHROPIC_API_KEY', ''),
    'OPENWEATHER_API_KEY': os.environ.get('OPENWEATHER_API_KEY', ''),
    'GIPHY_API_KEY': os.environ.get('GIPHY_API_KEY', ''),
    'HF_API_TOKEN': os.environ.get('HF_API_TOKEN', ''),
}

for key, value in keys_to_check.items():
    status = '✅' if value and value != 'dummy' else '❌'
    masked = f"{value[:8]}..." if value and len(value) > 8 else "NOT SET"
    print(f"  {status} {key:<25} {masked}")

# 5. Check QuotaService
print("\n[5] QUOTA SERVICE")
try:
    if user_count > 0:
        test_user = User.objects.first()
        service = QuotaService()
        quotas = service.get_user_quotas(test_user.id)
        print(f"  ✅ QuotaService working")
        print(f"      - Search: {quotas['search']['used']}/{quotas['search']['limit']}")
        print(f"      - Actions: {quotas['actions']['used']}/{quotas['actions']['limit']}")
        print(f"      - Messages: {quotas['messages']['used']}/{quotas['messages']['limit']}")
    else:
        print(f"  ⚠️  No users to test")
except Exception as e:
    print(f"  ❌ QuotaService error: {e}")

# 6. Test HTTP Endpoints
print("\n[6] HTTP ENDPOINTS")
try:
    # Test travel pages
    response = requests.get('http://localhost:8000/travel/plan/', timeout=5)
    print(f"  {'✅' if response.status_code == 200 else '❌'} /travel/plan/ -> {response.status_code}")
except requests.exceptions.ConnectionError:
    print(f"  ❌ /travel/plan/ -> Connection refused")
except Exception as e:
    print(f"  ❌ /travel/plan/ -> {e}")

# 7. Check Static Files
print("\n[7] STATIC FILES")
staticfiles_dir = "/app/Backend/staticfiles"
if os.path.exists(staticfiles_dir):
    file_count = sum([len(files) for r, d, files in os.walk(staticfiles_dir)])
    print(f"  ✅ Staticfiles dir exists ({file_count} files)")
else:
    print(f"  ❌ Staticfiles dir missing")

print("\n" + "=" * 60)
print("DIAGNOSIS COMPLETE")
print("=" * 60)
