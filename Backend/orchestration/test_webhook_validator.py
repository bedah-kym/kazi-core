import hashlib
import hmac
from unittest import mock

from django.test import SimpleTestCase

from orchestration import webhook_validator
from orchestration.webhook_validator import verify_intasend_signature

SECRET = "fake-intasend-challenge"  # nosec B105 - test fixture, not a credential
BODY = b'{"invoice_id": "INV-1"}'


class IntaSendSignatureTests(SimpleTestCase):
    def test_challenge_match_accepted(self):
        self.assertTrue(verify_intasend_signature(SECRET, SECRET, BODY))
        self.assertTrue(verify_intasend_signature(f"  {SECRET}\n", SECRET, BODY))

    def test_challenge_compared_in_constant_time(self):
        with mock.patch.object(webhook_validator.hmac, "compare_digest", wraps=hmac.compare_digest) as spy:
            self.assertTrue(verify_intasend_signature(SECRET, SECRET, BODY))
        spy.assert_called_once_with(SECRET.encode(), SECRET.encode())

    def test_wrong_or_missing_challenge_rejected(self):
        self.assertFalse(verify_intasend_signature("wrong", SECRET, BODY))
        self.assertFalse(verify_intasend_signature(SECRET[:-1], SECRET, BODY))
        self.assertFalse(verify_intasend_signature("", SECRET, BODY))
        self.assertFalse(verify_intasend_signature(None, SECRET, BODY))
        self.assertFalse(verify_intasend_signature(SECRET, "", BODY))

    def test_non_ascii_header_rejected_without_error(self):
        self.assertFalse(verify_intasend_signature("sécret-ü", SECRET, BODY))

    def test_hmac_signature_still_accepted(self):
        digest = hmac.new(SECRET.encode(), BODY, hashlib.sha256).hexdigest()
        self.assertTrue(verify_intasend_signature(f"sha256={digest}", SECRET, BODY))
        self.assertFalse(verify_intasend_signature(f"sha256={digest}", SECRET, BODY + b" "))
