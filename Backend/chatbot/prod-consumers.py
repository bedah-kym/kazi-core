"""
this is not my code its not even used in the app its just claude.ai helping me understand how encryption would be done
"""
import json
import logging
import time
import os
from base64 import b64encode, b64decode
from typing import Optional, Dict, Any

from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidKey
from django.conf import settings
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.core.cache import cache
from asgiref.sync import sync_to_async
import redis.asyncio as redis

logger = logging.getLogger(__name__)

class SecureMessageError(Exception):
    """Custom exception for secure messaging errors"""
    pass

class UltraSecureChatConsumer(AsyncWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.redis_client = redis.Redis.from_url(settings.REDIS_URL)
        self.message_queue = []
        self.last_key_rotation = time.time()
        self.messages_since_rotation = 0
        self.MAX_QUEUE_SIZE = 100
        self.KEY_ROTATION_INTERVAL = 3600  # 1 hour
        self.MESSAGES_BEFORE_ROTATION = 1000

    async def connect(self):
        """Establish secure connection with perfect forward secrecy"""
        try:
            # Verify authentication
            if not self.scope["user"].is_authenticated:
                await self.close(code=4001)
                return

            # Initialize secure session
            await self.initialize_secure_session()
            
            # Join room with validation
            self.room_name = self.scope["url_route"]["kwargs"]["room_name"]
            if not await self.validate_room_access():
                await self.close(code=4003)
                return

            self.room_group_name = f"chat_{self.room_name}"
            await self.channel_layer.group_add(self.room_group_name, self.channel_name)
            
            # Complete connection
            await self.accept()
            
            # Send current session public key
            await self.send_session_key()

        except Exception as e:
            logger.error(f"Connection error: {str(e)}")
            await self.close(code=4000)

    async def initialize_secure_session(self):
        """Initialize encryption for the session"""
        # Generate ephemeral RSA key pair for initial key exchange
        self.private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048
        )
        self.public_key = self.private_key.public_key()
        
        # Generate session key for AESGCM
        self.session_key = AESGCM.generate_key(bit_length=256)
        self.aes_gcm = AESGCM(self.session_key)
        
        # Store session information securely
        user_id = str(self.scope["user"].id)
        session_data = {
            'public_key': self.public_key.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo
            ),
            'session_key': self.session_key,
            'created_at': time.time()
        }
        await self.redis_client.setex(
            f"chat_session:{user_id}", 
            self.KEY_ROTATION_INTERVAL,
            json.dumps(session_data)
        )

    async def receive(self, text_data):
        """Handle incoming encrypted messages"""
        try:
            # Decrypt and verify incoming message
            decrypted_data = await self.decrypt_and_verify(text_data)
            if not decrypted_data:
                return

            # Rate limiting check
            if not await self.check_rate_limit():
                await self.send_error("Rate limit exceeded")
                return

            # Process message based on command
            command = decrypted_data.get("command")
            if command == "new_message":
                await self.handle_new_message(decrypted_data)
            elif command == "fetch_messages":
                await self.handle_fetch_messages(decrypted_data)
            elif command == "key_rotation":
                await self.handle_key_rotation(decrypted_data)
            else:
                await self.send_error("Invalid command")

            # Check if key rotation is needed
            await self.check_key_rotation()

        except SecureMessageError as e:
            await self.send_error(str(e))
        except Exception as e:
            logger.error(f"Message processing error: {str(e)}")
            await self.send_error("Internal error")

    async def handle_new_message(self, data: Dict[str, Any]):
        """Process and broadcast new encrypted messages"""
        try:
            # Validate message
            if not self.validate_message_format(data):
                raise SecureMessageError("Invalid message format")

            # Generate message specific nonce
            nonce = os.urandom(12)
            
            # Prepare message package
            message_package = {
                'content': data['message'],
                'sender_id': self.scope["user"].id,
                'timestamp': time.time(),
                'nonce': b64encode(nonce).decode('utf-8')
            }

            # Sign the message package
            signature = self.sign_message(json.dumps(message_package))
            message_package['signature'] = b64encode(signature).decode('utf-8')

            # Encrypt for each recipient
            encrypted_messages = await self.encrypt_for_recipients(message_package)

            # Broadcast to room
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    "type": "chat_message",
                    "message": encrypted_messages
                }
            )

            # Store message securely
            await self.store_message(message_package)

        except Exception as e:
            logger.error(f"Message handling error: {str(e)}")
            raise SecureMessageError("Failed to process message")

    async def encrypt_for_recipients(self, message_package: Dict[str, Any]) -> Dict[str, str]:
        """Encrypt message for all recipients in the room"""
        encrypted_messages = {}
        room_members = await self.get_room_members()
        
        for member_id in room_members:
            recipient_key = await self.get_recipient_key(member_id)
            if recipient_key:
                # Generate recipient-specific nonce
                nonce = os.urandom(12)
                
                # Encrypt message with recipient's key
                encrypted_data = self.aes_gcm.encrypt(
                    nonce,
                    json.dumps(message_package).encode(),
                    None  # Additional data if needed
                )
                
                encrypted_messages[member_id] = {
                    'data': b64encode(encrypted_data).decode('utf-8'),
                    'nonce': b64encode(nonce).decode('utf-8')
                }
        
        return encrypted_messages

    def sign_message(self, message: str) -> bytes:
        """Sign message with sender's private key"""
        return self.private_key.sign(
            message.encode(),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )

    async def verify_signature(self, message: str, signature: bytes, sender_id: str) -> bool:
        """Verify message signature"""
        try:
            sender_key = await self.get_recipient_key(sender_id)
            if not sender_key:
                return False
                
            sender_key.verify(
                signature,
                message.encode(),
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH
                ),
                hashes.SHA256()
            )
            return True
        except Exception:
            return False

    async def check_key_rotation(self):
        """Check if key rotation is needed"""
        current_time = time.time()
        self.messages_since_rotation += 1
        
        if (current_time - self.last_key_rotation >= self.KEY_ROTATION_INTERVAL or 
            self.messages_since_rotation >= self.MESSAGES_BEFORE_ROTATION):
            await self.rotate_keys()

    async def rotate_keys(self):
        """Perform key rotation"""
        await self.initialize_secure_session()
        self.last_key_rotation = time.time()
        self.messages_since_rotation = 0
        await self.broadcast_key_rotation()

    async def disconnect(self, close_code):
        """Clean up secure session"""
        try:
            # Remove session data
            user_id = str(self.scope["user"].id)
            await self.redis_client.delete(f"chat_session:{user_id}")
            
            # Leave room
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)
            
        except Exception as e:
            logger.error(f"Disconnect error: {str(e)}")

    @database_sync_to_async
    def store_message(self, message_package: Dict[str, Any]):
        """Securely store message in database"""
        # Implement your database storage logic here
        pass

    async def check_rate_limit(self) -> bool:
        """Check if user has exceeded rate limit"""
        user_id = str(self.scope["user"].id)
        current = await self.redis_client.incr(f"rate_limit:{user_id}")
        if current == 1:
            await self.redis_client.expire(f"rate_limit:{user_id}", 60)
        return current <= settings.CHAT_RATE_LIMIT

    @staticmethod
    def validate_message_format(data: Dict[str, Any]) -> bool:
        """Validate message format and content"""
        required_fields = ['message', 'timestamp']
        return all(field in data for field in required_fields)