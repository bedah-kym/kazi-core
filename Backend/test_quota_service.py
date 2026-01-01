import logging
import os
import time
import django
from django.conf import settings
from datetime import datetime
from django.core.cache import cache

# Setup
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Backend.settings')
django.setup()

from users.quota_service import QuotaService

# Config logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_quota_service():
    logger.info("--- Testing Quota Service ---")
    service = QuotaService()
    user_id = 999  # Test User ID

    # 1. Clear previous test keys
    today = datetime.now().strftime("%Y-%m-%d")
    current_minute = datetime.now().strftime('%Y-%m-%d-%H-%M')
    
    keys = [
        f"search_limit:{user_id}:{today}",
        f"mcp_rate:{user_id}",
        f"rate_limit:{user_id}:{current_minute}"
    ]
    cache.delete_many(keys)

    # 2. Simulate Usage
    # Search: 8/10 used (Warning/Orange)
    cache.set(f"search_limit:{user_id}:{today}", 8, 3600)
    
    # MCP: 105/100 used (Exhausted/Red)
    cache.set(f"mcp_rate:{user_id}", 105, 3600)
    
    # Message: 5/30 used (Good/Green)
    cache.set(f"rate_limit:{user_id}:{current_minute}", 5, 60)

    # 3. Get Quotas
    quotas = service.get_user_quotas(user_id)
    
    # 4. Assertions
    logger.info(f"Quotas: {quotas}")
    
    # Search
    assert quotas['search']['used'] == 8
    assert quotas['search']['status'] == 'critical' # >= 80%
    assert quotas['search']['color'] == 'orange'
    logger.info("✅ Search Quota Verified")

    # MCP
    assert quotas['actions']['used'] == 105
    assert quotas['actions']['status'] == 'exhausted' # >= 100%
    assert quotas['actions']['color'] == 'red'
    logger.info("✅ MCP Quota Verified")

    # Messages
    assert quotas['messages']['used'] == 5
    assert quotas['messages']['status'] == 'good' # < 50%
    assert quotas['messages']['color'] == 'green'
    logger.info("✅ Message Quota Verified")

    print("\n--- ALL TESTS PASSED ---")

if __name__ == "__main__":
    test_quota_service()
