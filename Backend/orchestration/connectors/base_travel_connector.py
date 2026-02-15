"""
Base Travel Connector with caching, retry, and rate-limit handling
Inherited by all travel-specific connectors (buses, hotels, flights, transfers, events)
"""
import hashlib
import json
import logging
import asyncio
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from django.core.cache import cache
from django.utils import timezone
from django_redis import get_redis_connection
from asgiref.sync import sync_to_async

from orchestration.base_connector import BaseConnector
from travel.models import SearchCache

logger = logging.getLogger(__name__)


class BaseTravelConnector(BaseConnector):
    """
    Base class for all travel connectors
    Provides caching, retry logic, rate-limit handling
    """
    
    PROVIDER_NAME = None  # Override in subclass (e.g., 'buupass', 'booking', 'duffel')
    CACHE_TTL_SECONDS = 3600  # 1 hour default
    MAX_RETRIES = 3
    RETRY_BACKOFF = 2  # seconds, exponential
    RATE_LIMIT_PER_HOUR = 100  # per provider per user
    
    def __init__(self):
        super().__init__()
        self.redis = None
        
    async def execute(self, parameters: Dict, context: Dict) -> Dict:
        """
        Execute travel search with caching and retry
        
        Args:
            parameters: {
                'search_type': 'buses|hotels|flights|transfers|events',
                'origin': 'Nairobi',
                'destination': 'Mombasa',
                'start_date': '2025-12-25',
                'end_date': '2025-12-31',
                'passengers': 2,
                'budget_ksh': 50000,
                ...
            }
            context: {user_id, room, ...}
        
        Returns: {
            'status': 'success|error',
            'count': 5,
            'results': [...],
            'cached': True|False,
            'message': '',
            'metadata': {}
        }
        """
        try:
            # Validate rate limit
            rate_limit_ok = await self._check_rate_limit(context.get('user_id'))
            if not rate_limit_ok:
                return {
                    'status': 'error',
                    'count': 0,
                    'results': [],
                    'cached': False,
                    'message': f'Rate limit exceeded for {self.PROVIDER_NAME}. Max {self.RATE_LIMIT_PER_HOUR} requests/hour'
                }
            
            # Build cache key
            query_hash = self._hash_query(parameters)
            
            # Check cache first
            cached_result = await self._get_cached_result(query_hash)
            if cached_result:
                logger.info(f"{self.PROVIDER_NAME}: Cache hit for {query_hash[:16]}")
                return {
                    'status': 'success',
                    'count': len(cached_result.get('results', [])),
                    'results': cached_result.get('results', []),
                    'cached': True,
                    'message': f'Results from {self.PROVIDER_NAME} (cached)',
                    'metadata': {'cache_age_seconds': cached_result.get('cache_age', 0)}
                }
            
            # Cache miss: fetch fresh data with retry
            logger.info(f"{self.PROVIDER_NAME}: Cache miss for {query_hash[:16]}, fetching fresh data")
            
            result = None
            last_error = None
            
            for attempt in range(self.MAX_RETRIES):
                try:
                    result = await self._fetch(parameters, context)
                    break  # Success
                except Exception as e:
                    last_error = e
                    if attempt < self.MAX_RETRIES - 1:
                        wait_time = self.RETRY_BACKOFF ** attempt
                        logger.warning(
                            f"{self.PROVIDER_NAME} attempt {attempt + 1} failed: {e}. "
                            f"Retrying in {wait_time}s..."
                        )
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(f"{self.PROVIDER_NAME}: All {self.MAX_RETRIES} attempts failed: {e}")
            
            if not result:
                return {
                    'status': 'error',
                    'count': 0,
                    'results': [],
                    'cached': False,
                    'message': f'Failed to fetch from {self.PROVIDER_NAME}: {str(last_error)}'
                }

            # Remember last results per user to enable follow-up booking by option number/ID
            self._store_last_results(
                user_id=context.get('user_id'),
                action=parameters.get('action'),
                result=result
            )
            
            # Cache successful result
            await self._cache_result(query_hash, parameters, result)
            
            return {
                'status': 'success',
                'count': len(result.get('results', [])),
                'results': result.get('results', []),
                'cached': False,
                'message': f'Results from {self.PROVIDER_NAME}',
                'metadata': result.get('metadata', {})
            }
            
        except Exception as e:
            logger.error(f"{self.PROVIDER_NAME} execution error: {e}")
            return {
                'status': 'error',
                'count': 0,
                'results': [],
                'cached': False,
                'message': f'Error: {str(e)}'
            }
    
    async def _fetch(self, parameters: Dict, context: Dict) -> Dict:
        """
        Fetch data from API or scraper
        Override in subclass
        
        Returns: {results: [...], metadata: {...}}
        """
        raise NotImplementedError(f"{self.__class__.__name__} must implement _fetch()")
    
    def _hash_query(self, parameters: Dict) -> str:
        """Create deterministic hash of query parameters"""
        query_str = json.dumps(parameters, sort_keys=True, default=str)
        return hashlib.sha256(query_str.encode()).hexdigest()
    
    async def _check_rate_limit(self, user_id: Optional[str]) -> bool:
        """Check if user/provider combo is within rate limits"""
        if not user_id:
            return True
        
        try:
            redis = get_redis_connection('default')
            key = f"travel_rate:{self.PROVIDER_NAME}:{user_id}:hour"
            current = await sync_to_async(redis.get)(key)
            current = int(current) if current else 0
            
            if current >= self.RATE_LIMIT_PER_HOUR:
                return False
            
            # Increment with 1 hour expiry
            pipe = redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, 3600)
            await sync_to_async(pipe.execute)()
            
            return True
        except Exception as e:
            logger.warning(f"Rate limit check failed (allowing): {e}")
            return True
    
    async def _get_cached_result(self, query_hash: str) -> Optional[Dict]:
        """Get cached search result from database"""
        try:
            def _get_cache():
                cache_obj = SearchCache.objects.filter(
                    query_hash=query_hash,
                    provider=self.PROVIDER_NAME,
                    expires_at__gt=timezone.now()
                ).first()
                
                if cache_obj:
                    cache_obj.hit_count += 1
                    cache_obj.save(update_fields=['hit_count'])
                    
                    age_seconds = (timezone.now() - cache_obj.created_at).total_seconds()
                    return {
                        'results': cache_obj.result_json.get('results', []),
                        'cache_age': int(age_seconds),
                        'metadata': cache_obj.result_json.get('metadata', {})
                    }
                return None
            
            return await sync_to_async(_get_cache)()
        except Exception as e:
            logger.warning(f"Cache retrieval failed: {e}")
            return None
    
    async def _cache_result(self, query_hash: str, query_json: Dict, result: Dict):
        """Store search result in cache"""
        try:
            def _store_cache():
                expires_at = timezone.now() + timedelta(seconds=self.CACHE_TTL_SECONDS)
                SearchCache.objects.update_or_create(
                    query_hash=query_hash,
                    provider=self.PROVIDER_NAME,
                    defaults={
                        'query_json': query_json,
                        'result_json': result,
                        'ttl_seconds': self.CACHE_TTL_SECONDS,
                        'expires_at': expires_at,
                        'hit_count': 0
                    }
                )
            
            await sync_to_async(_store_cache)()
            logger.info(f"Cached results for {self.PROVIDER_NAME}: {query_hash[:16]}")
        except Exception as e:
            logger.error(f"Cache storage failed: {e}")
    
    async def _parallel_fetch(self, tasks: List) -> List[Dict]:
        """Helper to execute multiple async tasks in parallel"""
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            return [r for r in results if not isinstance(r, Exception)]
        except Exception as e:
            logger.error(f"Parallel fetch error: {e}")
            return []

    def _store_last_results(self, user_id: Any, action: Optional[str], result: Dict) -> None:
        """Persist last search results for quick booking resolution."""
        if not user_id or not action or not isinstance(result, dict):
            return
        try:
            from travel.search_state import store_last_results

            metadata = dict(result.get('metadata') or {})
            metadata.setdefault('search_action', action)

            store_last_results(
                user_id=user_id,
                action=action,
                results=result.get('results', []),
                metadata=metadata,
                ttl_seconds=self.CACHE_TTL_SECONDS,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(f"{self.PROVIDER_NAME}: unable to store last results: {exc}")
