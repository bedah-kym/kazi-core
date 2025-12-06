from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone

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
		from django.conf import settings
		from cryptography.fernet import Fernet
		import base64, hashlib
		# Derive a deterministic Fernet key from SECRET_KEY (for demo only). In production use KMS.
		secret = (settings.SECRET_KEY or 'changeme').encode('utf-8')
		hash = hashlib.sha256(secret).digest()
		fernet_key = base64.urlsafe_b64encode(hash)
		f = Fernet(fernet_key)
		# store tokens encrypted
		self.encrypted_access_token = f.encrypt(access_token.encode('utf-8')).decode('utf-8')
		if refresh_token:
			self.encrypted_refresh_token = f.encrypt(refresh_token.encode('utf-8')).decode('utf-8')
		self.calendly_user_uri = calendly_user_uri
		self.event_type_uri = event_type_uri
		self.event_type_name = event_type_name
		self.booking_link = booking_link
		self.webhook_subscription_id = subscription_id
		self.is_connected = True
		self.connected_at = timezone.now()
		self.save()

	def disconnect(self):
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

	def _fernet(self):
		from django.conf import settings
		from cryptography.fernet import Fernet
		import base64, hashlib
		secret = (settings.SECRET_KEY or 'changeme').encode('utf-8')
		hash = hashlib.sha256(secret).digest()
		fernet_key = base64.urlsafe_b64encode(hash)
		return Fernet(fernet_key)

	def get_access_token(self):
		if not self.encrypted_access_token:
			return None
		f = self._fernet()
		try:
			return f.decrypt(self.encrypted_access_token.encode('utf-8')).decode('utf-8')
		except Exception:
			return None

	def get_refresh_token(self):
		if not self.encrypted_refresh_token:
			return None
		f = self._fernet()
		try:
			return f.decrypt(self.encrypted_refresh_token.encode('utf-8')).decode('utf-8')
		except Exception:
			return None


class Workspace(models.Model):
    PLAN_CHOICES = (
        ('free', 'Free'),
        ('pro', 'Pro'),
        ('agency', 'Agency'),
    )
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='workspace')
    name = models.CharField(max_length=255, default="My Workspace")
    plan = models.CharField(max_length=20, choices=PLAN_CHOICES, default='free')
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


