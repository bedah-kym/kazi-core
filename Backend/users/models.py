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
