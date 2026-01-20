from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone
from .encryption import TokenEncryption

User = get_user_model()


class CalendlyProfile(models.Model):
	user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='calendly')
	is_connected = models.BooleanField(default=False)
	encrypted_access_token = models.TextField(blank=True, null=True)
	encrypted_refresh_token = models.TextField(blank=True, null=True)
	calendly_user_uri = models.CharField(max_length=255, blank=True, null=True)
	event_type_uri = models.CharField(max_length=255, blank=True, null=True)
	event_type_name = models.CharField(max_length=255, blank=True, null=True)
	booking_link = models.CharField(max_length=1024, blank=True, null=True)
	webhook_subscription_id = models.CharField(max_length=255, blank=True, null=True)
	connected_at = models.DateTimeField(blank=True, null=True)

	def connect(self, access_token, refresh_token, calendly_user_uri, event_type_uri=None, event_type_name=None, booking_link=None, subscription_id=None):
		"""
		Store Calendly credentials securely.
		
		Args:
			access_token: OAuth access token
			refresh_token: OAuth refresh token
			calendly_user_uri: User's Calendly URI
			event_type_uri: Event type URI (optional)
			event_type_name: Event type name (optional)
			booking_link: Public booking link (optional)
			subscription_id: Webhook subscription ID (optional)
		"""
		# Use secure encryption from TokenEncryption utility
		self.encrypted_access_token = TokenEncryption.encrypt(access_token)
		if refresh_token:
			self.encrypted_refresh_token = TokenEncryption.encrypt(refresh_token)
		self.calendly_user_uri = calendly_user_uri
		self.event_type_uri = event_type_uri
		self.event_type_name = event_type_name
		self.booking_link = booking_link
		self.webhook_subscription_id = subscription_id
		self.is_connected = True
		self.connected_at = timezone.now()
		self.save()

	def disconnect(self):
		"""Securely clear all Calendly credentials."""
		self.encrypted_access_token = None
		self.encrypted_refresh_token = None
		self.calendly_user_uri = None
		self.event_type_uri = None
		self.event_type_name = None
		self.booking_link = None
		self.webhook_subscription_id = None
		self.is_connected = False
		self.connected_at = None
		self.save()

	def __str__(self):
		return f"CalendlyProfile({self.user.username})"

	def get_access_token(self):
		"""Securely retrieve and decrypt access token."""
		if not self.encrypted_access_token:
			return None
		return TokenEncryption.safe_decrypt(self.encrypted_access_token, default=None)

	def get_refresh_token(self):
		"""Securely retrieve and decrypt refresh token."""
		if not self.encrypted_refresh_token:
			return None
		return TokenEncryption.safe_decrypt(self.encrypted_refresh_token, default=None)


class UserProfile(models.Model):
	"""
	User Profile with bio, avatar, and social links
	"""
	user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
	
	# Basic Info
	bio = models.TextField(max_length=500, blank=True, help_text="Short bio or description")
	avatar = models.FileField(upload_to='avatars/', blank=True, null=True)
	location = models.CharField(max_length=100, blank=True)
	website = models.URLField(max_length=200, blank=True)
	
	# Professional Profile (NEW)
	user_type = models.CharField(max_length=20, choices=[
		('personal', 'Personal Brand'),
		('team', 'Team'),
		('business', 'Business')
	], default='personal', help_text="Account type")
	
	industry = models.CharField(max_length=100, blank=True,
		help_text="e.g., Design, Development, Marketing, Consulting")
	
	company_name = models.CharField(max_length=255, blank=True,
		help_text="Company or brand name")
	
	company_size = models.CharField(max_length=20, blank=True, choices=[
		('1', 'Just me'),
		('2-5', '2-5 people'),
		('6-10', '6-10 people'),
		('11-25', '11-25 people'),
		('26-50', '26-50 people'),
		('50+', '50+ people')
	])
	
	role = models.CharField(max_length=100, blank=True,
		help_text="Job title/role (e.g., Founder, Designer, Developer)")
	
	# Social links (individual fields - backward compatible)
	twitter_handle = models.CharField(max_length=50, blank=True)
	linkedin_url = models.URLField(max_length=200, blank=True)
	github_url = models.URLField(max_length=200, blank=True)
	
	# Social links as JSON for flexibility (NEW)
	social_links = models.JSONField(default=dict, blank=True,
		help_text='{"twitter": "@handle", "linkedin": "url", "github": "url", "portfolio": "url"}')
	
	# Onboarding tracking (NEW)
	onboarding_completed = models.BooleanField(default=False)
	onboarding_step = models.IntegerField(default=0,
		help_text="Current onboarding step (0-6)")
	
	# Preferences (NEW)
	notification_preferences = models.JSONField(default=dict, blank=True,
		help_text='{"email_notifications": true, "push_notifications": false, "digest_frequency": "daily"}')
	
	theme_preference = models.CharField(max_length=10, choices=[
		('light', 'Light'),
		('dark', 'Dark'),
		('auto', 'Auto')
	], default='auto')
	
	# Existing preferences
	timezone = models.CharField(max_length=50, default='UTC')
	language = models.CharField(max_length=10, default='en')
	
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)
	
	def __str__(self):
		return f"{self.user.username}'s Profile"
	
	def get_display_name(self):
		"""Returns full name if available, otherwise username"""
		if self.user.first_name and self.user.last_name:
			return f"{self.user.first_name} {self.user.last_name}"
		elif self.user.first_name:
			return self.user.first_name
		return self.user.username
	
	def get_avatar_url(self):
		"""Returns avatar URL or default placeholder"""
		if self.avatar:
			return self.avatar.url
		# Return default avatar based on username initial
		initial = self.user.username[0].upper()
		return f"https://ui-avatars.com/api/?name={initial}&background=4f8cff&color=fff&size=128"
	
	def consolidate_social_links(self):
		"""Migrate individual social fields to social_links JSON"""
		if not self.social_links:
			self.social_links = {}
		
		if self.twitter_handle and 'twitter' not in self.social_links:
			self.social_links['twitter'] = self.twitter_handle
		if self.linkedin_url and 'linkedin' not in self.social_links:
			self.social_links['linkedin'] = self.linkedin_url
		if self.github_url and 'github' not in self.social_links:
			self.social_links['github'] = self.github_url
		
		return self.social_links


class GoalProfile(models.Model):
	"""
	AI personalization engine: User goals, skills, needs, and roadmap
	"""
	workspace = models.OneToOneField('Workspace', on_delete=models.CASCADE, related_name='goals')
	
	# Professional Goals
	goals = models.JSONField(default=list, blank=True,
		help_text='["grow_audience", "increase_revenue", "book_more_clients", etc.]')
	custom_goals = models.TextField(blank=True, help_text="Free-form custom goals")
	
	# Skills & Expertise
	industry = models.CharField(max_length=100, blank=True,
		help_text="Primary industry (Design, Dev, Marketing, etc.)")
	skills = models.JSONField(default=list, blank=True,
		help_text='["React", "SEO", "Copywriting", "B2B Sales"]')
	experience_level = models.CharField(max_length=20, choices=[
		('beginner', 'Beginner'),
		('intermediate', 'Intermediate'),
		('expert', 'Expert'),
	], default='intermediate')
	
	# Current Needs (What user wants help with)
	needs = models.JSONField(default=list, blank=True,
		help_text='["content_ideas", "engagement_strategies", "lead_generation", etc.]')
	custom_needs = models.TextField(blank=True)
	
	# Use Cases (How they use KwikChat)
	use_cases = models.JSONField(default=list, blank=True,
		help_text='["client_management", "team_collaboration", "social_monitoring", etc.]')
	
	# Brand Roadmap (Timeline-based goals)
	roadmap = models.JSONField(default=list, blank=True,
		help_text='[{"quarter": "Q1 2025", "goals": ["Launch newsletter"], "status": "in_progress"}]')
	
	# Target Metrics
	target_revenue = models.DecimalField(max_digits=10, decimal_places=2,
		null=True, blank=True, help_text="Revenue goal (e.g., $10,000 MRR)")
	target_followers = models.IntegerField(null=True, blank=True,
		help_text="Social media follower target")
	target_clients = models.IntegerField(null=True, blank=True,
		help_text="Client acquisition target")
	target_email_subscribers = models.IntegerField(null=True, blank=True)
	
	# AI Context
	ai_personalization_enabled = models.BooleanField(default=True,
		help_text="Allow AI to use goals for personalized suggestions")
	
	updated_at = models.DateTimeField(auto_now=True)
	created_at = models.DateTimeField(auto_now_add=True)
	
	def __str__(self):
		return f"Goals for {self.workspace.name}"
	
	def get_active_goals(self):
		"""Return goals formatted for AI context"""
		return {
			'professional_goals': self.goals,
			'custom_goals': self.custom_goals,
			'skills': self.skills,
			'needs': self.needs,
			'roadmap': [r for r in self.roadmap if r.get('status') != 'completed'],
			'targets': {
				'revenue': float(self.target_revenue) if self.target_revenue else None,
				'followers': self.target_followers,
				'clients': self.target_clients,
				'email_subscribers': self.target_email_subscribers
			}
		}


class Workspace(models.Model):
    PLAN_CHOICES = (
        ('free', 'Free'),
        ('pro', 'Pro'),
        ('agency', 'Agency'),
    )
    ACCOUNT_TYPE_CHOICES = (
        ('personal', 'Personal Brand'),
        ('team', 'Team'),
        ('business', 'Business'),
    )
    
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='workspace')
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='owned_workspaces', null=True)
    name = models.CharField(max_length=255, default="My Workspace")
    plan = models.CharField(max_length=20, choices=PLAN_CHOICES, default='free')
    account_type = models.CharField(max_length=20, choices=ACCOUNT_TYPE_CHOICES, default='personal')
    onboarding_completed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.plan})"


class Wallet(models.Model):
    """
    KwikChat Wallet for holding funds from payments (e.g. Intersend Pay).
    """
    workspace = models.OneToOneField(Workspace, on_delete=models.CASCADE, related_name='wallet')
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    currency = models.CharField(max_length=3, default='KES')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def deposit(self, amount, reference, description="Deposit"):
        """Atomic deposit"""
        from django.db.models import F
        # Use F expression for atomic update to avoid race conditions
        Wallet.objects.filter(pk=self.pk).update(balance=F('balance') + amount)
        self.refresh_from_db()
        WalletTransaction.objects.create(
            wallet=self,
            type='CREDIT',
            amount=amount,
            currency=self.currency,
            reference=reference,
            description=description,
            status='COMPLETED'
        )

    def withdraw(self, amount, reference, description="Withdrawal"):
        """Atomic withdrawal"""
        from django.db.models import F
        if self.balance < amount:
            return False, "Insufficient funds"
        Wallet.objects.filter(pk=self.pk).update(balance=F('balance') - amount)
        self.refresh_from_db()
        WalletTransaction.objects.create(
            wallet=self,
            type='DEBIT',
            amount=amount,
            currency=self.currency,
            reference=reference,
            description=description,
            status='COMPLETED'
        )
        return True, "Withdrawal successful"

    def __str__(self):
        return f"{self.workspace.name} Wallet - {self.currency} {self.balance}"


class WalletTransaction(models.Model):
    TRANSACTION_TYPES = (('CREDIT', 'Credit'), ('DEBIT', 'Debit'))
    STATUS_CHOICES = (('PENDING', 'Pending'), ('COMPLETED', 'Completed'), ('FAILED', 'Failed'))

    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name='transactions')
    type = models.CharField(max_length=10, choices=TRANSACTION_TYPES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default='KES')
    reference = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDING')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.type} {self.currency} {self.amount} - {self.status}"


class UserIntegration(models.Model):
    """
    Generic model to store credentials for various third-party integrations
    (WhatsApp, Mailgun, IntaSend, etc.) replacing ad-hoc profiles.
    """
    INTEGRATION_TYPES = (
        ('whatsapp', 'WhatsApp Business'),
        ('mailgun', 'Mailgun'),
        ('intasend', 'IntaSend Pay'),
        ('calendly', 'Calendly'), # Keeping explicit CalendlyProfile for now for backward compat, but can migrate later
        ('gmail', 'Gmail'),
        ('notion', 'Notion'),
    )
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='integrations')
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='integrations', null=True, blank=True)
    integration_type = models.CharField(max_length=50, choices=INTEGRATION_TYPES)
    is_connected = models.BooleanField(default=False)
    
    # Security: Credentials should be encrypted before storage
    encrypted_credentials = models.TextField(blank=True, null=True)
    
    # Extra data (e.g. phone number, domain, webhook IDs)
    metadata = models.JSONField(default=dict, blank=True)
    
    connected_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ('user', 'integration_type')

    def __str__(self):
        return f"{self.integration_type} - {self.user.username}"

