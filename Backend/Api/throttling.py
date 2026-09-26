from rest_framework.throttling import UserRateThrottle


class PlanAwareUserThrottle(UserRateThrottle):
    """Resolve the per-request rate lazily.

    DRF parses ``self.rate`` in ``__init__``, before the request exists, so a
    plan-aware ``get_rate()`` must be re-applied in ``get_cache_key`` where
    ``request.user`` is available.
    """

    def get_cache_key(self, request, view):
        self.user = getattr(request, 'user', None)
        self.rate = self.get_rate()
        self.num_requests, self.duration = self.parse_rate(self.rate)
        return super().get_cache_key(request, view)


class AIRequestThrottle(PlanAwareUserThrottle):
    scope = 'ai_request'

    def get_rate(self):
        user = getattr(self, 'user', None)
        if not user or not user.is_authenticated:
            return '0/day'  # No AI for anon

        # Check for workspace plan
        try:
            plan = user.workspace.plan
        except AttributeError:
            # Fallback if no workspace exists yet
            return '10/day'

        if plan == 'free':
            return '10/day'
        elif plan == 'pro':
            return '500/day'
        elif plan == 'agency':
            return '10000/day'
        return '10/day'


class GlobalApiThrottle(PlanAwareUserThrottle):
    scope = 'global_api'

    def get_rate(self):
        user = getattr(self, 'user', None)
        if not user or not user.is_authenticated:
            return '60/min'

        try:
            plan = user.workspace.plan
        except AttributeError:
            return '60/min'

        if plan == 'free':
            return '60/min'
        elif plan == 'pro':
            return '1000/min'
        elif plan == 'agency':
            return '10000/min'
        return '60/min'
