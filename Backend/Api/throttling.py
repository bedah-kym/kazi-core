from rest_framework.throttling import UserRateThrottle

class AIRequestThrottle(UserRateThrottle):
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

class GlobalApiThrottle(UserRateThrottle):
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
