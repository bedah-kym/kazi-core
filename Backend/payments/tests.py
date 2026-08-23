"""
Payments money-integrity charter (QA Phase 2a)

Owned invariants:
- LedgerService.post_transaction rejects unbalanced entries atomically (no partial journal rows).
- Account balance deltas follow the ASSET/EXPENSE vs LIABILITY/EQUITY/INCOME sign convention.
- Deposits credit gross - provider_fee - platform_fee; never negative; replayed provider refs are idempotent.
- Withdrawals never drive a wallet below zero, even under concurrency (Postgres row lock); replayed refs are idempotent.
- An invoice pays exactly once: same-ref replay returns the original tx, any other second payment hits the status guard.
- The IntaSend webhook is unauthenticated garbage until the challenge signature verifies; bad signatures change nothing.

Compatibility notes:
- users.Wallet is the source of truth for balances; JournalEntry/LedgerEntry are audit-side
  and are NOT yet posted on the wallet hot path (audit F5.3, scheduled Phase 2c). Tests here
  pin wallet-level invariants, not ledger linkage.
- reconcile_daily is a known placeholder tautology (F5.3); intentionally untested until fixed.

Lanes:
- Everything here is DB-integration (TransactionTestCase, real DB): money paths require it.
- ConcurrentWithdrawalTests requires Postgres row locking; skipped on SQLite where
  deferred transactions cannot enforce the invariant (see AGENTS.md known limitations).
"""
import json
import os
import sys
import threading
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from .models import (
    DepositIntent,
    FeeSchedule,
    JournalEntry,
    LedgerAccount,
    LedgerEntry,
    PaymentRequest,
    ReconciliationDiscrepancy,
)
from .services import InvoiceService, LedgerService, WalletService
from users.models import Wallet, WalletTransaction


def _credit_balance(wallet, amount):
    Wallet.objects.filter(pk=wallet.pk).update(balance=amount)
    wallet.refresh_from_db()


@override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})
class LedgerPostingTests(TransactionTestCase):
    def setUp(self):
        self.asset = LedgerAccount.objects.create(
            name='Test Asset', account_type='ASSET', currency='KES'
        )
        self.income = LedgerAccount.objects.create(
            name='Test Income', account_type='INCOME', currency='KES'
        )

    def test_balanced_entry_posts_journal_and_updates_balances(self):
        journal = LedgerService.post_transaction(
            'DEPOSIT', 'test entry',
            [
                {'account_id': self.asset.id, 'amount': Decimal('100.00'), 'dr_cr': 'DEBIT'},
                {'account_id': self.income.id, 'amount': Decimal('100.00'), 'dr_cr': 'CREDIT'},
            ],
            provider_ref='TRACK-1',
        )
        self.assertTrue(journal.verify_balance())
        self.assertEqual(journal.provider_reference, 'TRACK-1')
        self.assertEqual(journal.ledger_entries.count(), 2)
        self.asset.refresh_from_db()
        self.income.refresh_from_db()
        self.assertEqual(self.asset.balance, Decimal('100.00'))
        self.assertEqual(self.income.balance, Decimal('100.00'))

    def test_unbalanced_entry_rejected_without_partial_rows(self):
        with self.assertRaises(ValueError):
            LedgerService.post_transaction(
                'DEPOSIT', 'unbalanced',
                [
                    {'account_id': self.asset.id, 'amount': Decimal('100.00'), 'dr_cr': 'DEBIT'},
                    {'account_id': self.income.id, 'amount': Decimal('90.00'), 'dr_cr': 'CREDIT'},
                ],
            )
        self.assertEqual(JournalEntry.objects.count(), 0)
        self.assertEqual(LedgerEntry.objects.count(), 0)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.balance, Decimal('0.00'))

    def test_verify_balance_detects_unbalanced_entries(self):
        journal = JournalEntry.objects.create(transaction_type='FEE', description='tampered')
        LedgerEntry.objects.create(
            journal_entry=journal, ledger_account=self.asset,
            amount=Decimal('10.00'), dr_cr='DEBIT',
        )
        self.assertFalse(journal.verify_balance())


@override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})
class WalletDepositTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='deposit-user', email='example@example.com', password='fake-token',
        )
        self.wallet = WalletService.get_or_create_user_wallet(self.user)

    def test_fee_split_credits_gross_minus_fees(self):
        FeeSchedule.objects.create(transaction_type='DEPOSIT', platform_fee=Decimal('25.00'))
        tx = WalletService.process_deposit(
            self.user, Decimal('1000.00'), Decimal('30.00'), 'DEP-REF-1'
        )
        self.assertEqual(tx.amount, Decimal('945.00'))
        self.assertEqual(tx.type, 'CREDIT')
        self.assertEqual(tx.status, 'COMPLETED')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('945.00'))

    def test_default_platform_fee_when_no_schedule(self):
        tx = WalletService.process_deposit(self.user, Decimal('200.00'), Decimal('0.00'), 'DEP-REF-2')
        self.assertEqual(tx.amount, Decimal('150.00'))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('150.00'))

    def test_deposit_too_small_after_fees_rejected(self):
        FeeSchedule.objects.create(transaction_type='DEPOSIT', platform_fee=Decimal('50.00'))
        with self.assertRaises(ValueError):
            WalletService.process_deposit(self.user, Decimal('60.00'), Decimal('20.00'), 'DEP-REF-3')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('0.00'))
        self.assertEqual(WalletTransaction.objects.count(), 0)

    def test_replayed_reference_is_idempotent(self):
        FeeSchedule.objects.create(transaction_type='DEPOSIT', platform_fee=Decimal('25.00'))
        first = WalletService.process_deposit(self.user, Decimal('1000.00'), Decimal('30.00'), 'DEP-REPLAY')
        second = WalletService.process_deposit(self.user, Decimal('1000.00'), Decimal('30.00'), 'DEP-REPLAY')
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(WalletTransaction.objects.count(), 1)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('945.00'))


@override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})
class WalletWithdrawalTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='withdraw-user', email='example@example.com', password='fake-token',
        )
        self.wallet = WalletService.get_or_create_user_wallet(self.user)

    def test_withdrawal_debits_balance_and_records_tx(self):
        _credit_balance(self.wallet, Decimal('500.00'))
        tx = WalletService.process_withdrawal(self.user, Decimal('120.00'), 'WD-REF-1')
        self.assertEqual(tx.amount, Decimal('120.00'))
        self.assertEqual(tx.type, 'DEBIT')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('380.00'))

    def test_overdraft_rejected_leaving_state_untouched(self):
        _credit_balance(self.wallet, Decimal('100.00'))
        with self.assertRaises(ValueError):
            WalletService.process_withdrawal(self.user, Decimal('150.00'), 'WD-OVER')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('100.00'))
        self.assertEqual(WalletTransaction.objects.count(), 0)

    def test_exact_balance_is_allowed_boundary(self):
        _credit_balance(self.wallet, Decimal('100.00'))
        WalletService.process_withdrawal(self.user, Decimal('100.00'), 'WD-EXACT')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('0.00'))

    def test_replayed_reference_is_idempotent(self):
        _credit_balance(self.wallet, Decimal('500.00'))
        first = WalletService.process_withdrawal(self.user, Decimal('100.00'), 'WD-REPLAY')
        second = WalletService.process_withdrawal(self.user, Decimal('100.00'), 'WD-REPLAY')
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(WalletTransaction.objects.count(), 1)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('400.00'))


@override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})
class ConcurrentWithdrawalTests(TransactionTestCase):
    """Two simultaneous withdrawals of the same funds must produce at most one debit."""

    def setUp(self):
        from django.db import connection
        self.is_postgres = connection.vendor == 'postgresql'

        self.user = get_user_model().objects.create_user(
            username='race-user', email='example@example.com', password='fake-token',
        )
        self.wallet = WalletService.get_or_create_user_wallet(self.user)
        _credit_balance(self.wallet, Decimal('100.00'))

    def test_concurrent_withdrawals_cannot_overdraft(self):
        if not self.is_postgres:
            self.skipTest('Requires Postgres row locking; SQLite cannot enforce the invariant')

        barrier = threading.Barrier(2)
        succeeded = []
        rejected = []
        unexpected = []

        def attempt(index):
            try:
                barrier.wait(timeout=10)
                WalletService.process_withdrawal(self.user, Decimal('80.00'), f'WD-RACE-{index}')
                succeeded.append(index)
            except ValueError:
                rejected.append(index)
            except Exception as exc:
                unexpected.append(exc)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        self.assertEqual(unexpected, [])
        self.assertEqual(len(succeeded), 1, f'expected one winner, got {succeeded}')
        self.assertEqual(len(rejected), 1)
        self.assertEqual(WalletTransaction.objects.filter(type='DEBIT').count(), 1)
        self.wallet.refresh_from_db()
        self.assertGreaterEqual(self.wallet.balance, Decimal('0.00'))
        self.assertEqual(self.wallet.balance, Decimal('20.00'))


@override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})
class InvoicePaymentTests(TransactionTestCase):
    def setUp(self):
        self.issuer = get_user_model().objects.create_user(
            username='issuer-user', email='example@example.com', password='fake-token',
        )
        self.wallet = WalletService.get_or_create_user_wallet(self.issuer)
        self.invoice = PaymentRequest.objects.create(
            issuer=self.issuer,
            amount=Decimal('500.00'),
            description='test invoice',
            expires_at='2099-01-01T00:00:00Z',
        )

    def test_payment_credits_issuer_and_marks_paid(self):
        tx = InvoiceService.process_invoice_payment(self.invoice.id, 'INV-PAY-1')
        self.assertEqual(tx.amount, Decimal('500.00'))
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, 'PAID')
        self.assertIsNotNone(self.invoice.paid_at)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('500.00'))

    def test_same_ref_replay_returns_original_tx(self):
        first = InvoiceService.process_invoice_payment(self.invoice.id, 'INV-REPLAY')
        second = InvoiceService.process_invoice_payment(self.invoice.id, 'INV-REPLAY')
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(WalletTransaction.objects.count(), 1)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('500.00'))

    def test_second_payment_under_different_ref_hits_status_guard(self):
        InvoiceService.process_invoice_payment(self.invoice.id, 'INV-FIRST')
        with self.assertRaises(ValueError):
            InvoiceService.process_invoice_payment(self.invoice.id, 'INV-SECOND')
        self.assertEqual(WalletTransaction.objects.count(), 1)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('500.00'))

    def test_non_pending_invoice_rejected(self):
        self.invoice.status = 'CANCELLED'
        self.invoice.save(update_fields=['status'])
        with self.assertRaises(ValueError):
            InvoiceService.process_invoice_payment(self.invoice.id, 'INV-CANCELLED')
        self.assertEqual(WalletTransaction.objects.count(), 0)


@override_settings(INTASEND_WEBHOOK_SECRET='test-challenge-secret')
@override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})
class WebhookCallbackTests(TransactionTestCase):
    def setUp(self):
        FeeSchedule.objects.create(transaction_type='DEPOSIT', platform_fee=Decimal('25.00'))
        self.user = get_user_model().objects.create_user(
            username='webhook-user', email='webhook@example.com', password='fake-token',
        )
        self.wallet = WalletService.get_or_create_user_wallet(self.user)
        self.url = '/payments/wallet/callback/'

    def _post(self, payload, signature='test-challenge-secret'):
        return self.client.post(
            self.url, data=json.dumps(payload), content_type='application/json',
            HTTP_X_INTASEND_SIGNATURE=signature,
        )

    def test_invalid_signature_rejected_without_side_effects(self):
        response = self._post(
            {
                'state': 'COMPLETE', 'invoice_id': 'TCK-BAD-SIG',
                'value': 1000, 'fee': 30, 'api_ref': f'wallet:{self.user.id}',
            },
            signature='wrong-secret',
        )
        self.assertEqual(response.status_code, 401)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('0.00'))
        self.assertEqual(WalletTransaction.objects.count(), 0)

    def test_unconfigured_secret_rejected(self):
        with override_settings(INTASEND_WEBHOOK_SECRET=None):
            response = self._post({'state': 'COMPLETE', 'invoice_id': 'TCK-X'})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(WalletTransaction.objects.count(), 0)

    def test_complete_deposit_payload_credits_wallet(self):
        response = self._post({
            'state': 'COMPLETE', 'invoice_id': 'TCK-GOOD-1',
            'value': 1000, 'fee': 30, 'api_ref': f'wallet:{self.user.id}',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'success')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('945.00'))
        self.assertTrue(WalletTransaction.objects.filter(reference='TCK-GOOD-1').exists())

    def test_complete_deposit_without_any_id_rejected(self):
        response = self._post({
            'state': 'COMPLETE', 'value': 1000, 'fee': 30,
            'api_ref': f'wallet:{self.user.id}',
        })
        self.assertEqual(response.status_code, 400)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('0.00'))
        self.assertEqual(WalletTransaction.objects.count(), 0)

    def test_replayed_callback_credits_only_once(self):
        payload = {
            'state': 'COMPLETE', 'invoice_id': 'TCK-REPLAY',
            'value': 1000, 'fee': 30, 'api_ref': f'wallet:{self.user.id}',
        }
        self._post(payload)
        self._post(payload)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('945.00'))
        self.assertEqual(WalletTransaction.objects.count(), 1)

    def test_non_terminal_state_is_ignored(self):
        response = self._post({
            'state': 'IN_PROGRESS', 'invoice_id': 'TCK-PENDING',
            'value': 1000, 'fee': 30, 'api_ref': f'wallet:{self.user.id}',
        })
        self.assertEqual(response.json()['status'], 'ignored')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('0.00'))
        self.assertEqual(WalletTransaction.objects.count(), 0)

    def test_intent_routes_deposit_without_api_ref(self):
        from .models import DepositIntent
        DepositIntent.objects.create(
            tracking_id='TCK-INTENT-1', user=self.user, amount=Decimal('1000.00'),
        )
        response = self._post({
            'state': 'COMPLETE', 'invoice_id': 'TCK-INTENT-1',
            'value': 1000, 'fee': 30,
        })
        self.assertEqual(response.status_code, 200)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('945.00'))
        self.assertTrue(WalletTransaction.objects.filter(reference='TCK-INTENT-1').exists())

    def test_shared_email_does_not_route_deposit(self):
        response = self._post({
            'state': 'COMPLETE', 'invoice_id': 'TCK-STRANGER',
            'value': 1000, 'fee': 30,
            'email': self.user.email,
        })
        self.assertEqual(response.status_code, 404)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('0.00'))
        self.assertEqual(WalletTransaction.objects.count(), 0)

    def test_unknown_user_rejected(self):
        response = self._post({
            'state': 'COMPLETE', 'invoice_id': 'TCK-NONE',
            'value': 1000, 'fee': 30,
        })
        self.assertEqual(response.status_code, 404)
        self.assertEqual(WalletTransaction.objects.count(), 0)


@override_settings(INTASEND_WEBHOOK_SECRET='test-challenge-secret')
@override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})
class WebhookInvoiceRoutingTests(TransactionTestCase):
    def setUp(self):
        self.issuer = get_user_model().objects.create_user(
            username='inv-route-user', email='inv-route@example.com', password='fake-token',
        )
        self.wallet = WalletService.get_or_create_user_wallet(self.issuer)
        self.invoice = PaymentRequest.objects.create(
            issuer=self.issuer,
            amount=Decimal('500.00'),
            description='routed invoice',
            expires_at='2099-01-01T00:00:00Z',
            intasend_invoice_id='INV-ROUTED-1',
        )
        self.url = '/payments/wallet/callback/'

    def _post(self, payload, signature='test-challenge-secret'):
        return self.client.post(
            self.url, data=json.dumps(payload), content_type='application/json',
            HTTP_X_INTASEND_SIGNATURE=signature,
        )

    def test_complete_invoice_webhook_pays_invoice_once(self):
        payload = {'state': 'COMPLETE', 'invoice_id': 'INV-ROUTED-1', 'value': 500, 'fee': 0}
        response = self._post(payload)
        self.assertEqual(response.status_code, 200)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, 'PAID')
        self.assertEqual(self.invoice.intasend_invoice_id, 'INV-ROUTED-1')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('500.00'))

        self._post(payload)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('500.00'))
        self.assertEqual(WalletTransaction.objects.count(), 1)

    def test_expired_webhook_cancels_pending_invoice(self):
        response = self._post({'state': 'EXPIRED', 'invoice_id': 'INV-ROUTED-1'})
        self.assertEqual(response.json()['status'], 'ignored')
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, 'EXPIRED')


class DepositInitiationTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='initiate-user', email='initiate@example.com', password='fake-token',
        )
        WalletService.get_or_create_user_wallet(self.user)
        self.client.force_login(self.user)
        self.fake_intasend = MagicMock()
        self.fake_intasend.APIService.return_value.collect.checkout.return_value = {
            'url': 'https://sandbox.intasend.com/checkout/abc',
            'invoice_id': 'TCK-NEW-1',
            'id': 'TCK-NEW-1',
        }

    def _initiate(self, amount='1000'):
        env = {
            'INTASEND_PUBLISHABLE_KEY': 'test-pk',
            'INTASEND_API_KEY': 'test-sk',
            'INTASEND_IS_TEST': 'true',
        }
        with patch.dict(sys.modules, {'intasend': self.fake_intasend}), \
             patch.dict(os.environ, env):
            return self.client.post('/payments/wallet/deposit/', {'amount': amount})

    def test_initiation_persists_intent_with_tracking_id(self):
        response = self._initiate('1000')
        self.assertEqual(response.status_code, 200)
        intent = DepositIntent.objects.get(tracking_id='TCK-NEW-1')
        self.assertEqual(intent.user, self.user)
        self.assertEqual(intent.amount, Decimal('1000.00'))

    def test_initiation_without_tracking_id_persists_nothing(self):
        self.fake_intasend.APIService.return_value.collect.checkout.return_value = {
            'url': 'https://sandbox.intasend.com/checkout/abc',
        }
        response = self._initiate()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(DepositIntent.objects.count(), 0)


class DepositLedgerPostingTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='ledger-deposit-user', email='example@example.com', password='fake-token',
        )
        self.wallet = WalletService.get_or_create_user_wallet(self.user)

    def _deposit(self):
        FeeSchedule.objects.create(transaction_type='DEPOSIT', platform_fee=Decimal('25.00'))
        return WalletService.process_deposit(self.user, Decimal('1000.00'), Decimal('30.00'), 'DEP-JOURNAL')

    def test_deposit_posts_balanced_journal(self):
        self._deposit()
        journal = JournalEntry.objects.get(transaction_type='DEPOSIT')
        self.assertTrue(journal.verify_balance())
        self.assertEqual(journal.provider_reference, 'DEP-JOURNAL')
        lines = {e.ledger_account.name: (e.amount, e.dr_cr) for e in journal.ledger_entries.all()}
        self.assertEqual(lines['System IntaSend Wallet'], (Decimal('970.00'), 'DEBIT'))
        self.assertEqual(lines['Platform Fee Revenue'], (Decimal('25.00'), 'CREDIT'))
        self.assertEqual(
            lines[f'Wallet Liability: {self.user.username}'], (Decimal('945.00'), 'CREDIT')
        )

    def test_deposit_replay_does_not_double_post_journal(self):
        self._deposit()
        WalletService.process_deposit(self.user, Decimal('1000.00'), Decimal('30.00'), 'DEP-JOURNAL')
        self.assertEqual(JournalEntry.objects.filter(transaction_type='DEPOSIT').count(), 1)


class WithdrawalLedgerPostingTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='ledger-wd-user', email='example@example.com', password='fake-token',
        )
        self.wallet = WalletService.get_or_create_user_wallet(self.user)
        _credit_balance(self.wallet, Decimal('500.00'))

    def test_withdrawal_posts_balanced_journal(self):
        WalletService.process_withdrawal(self.user, Decimal('120.00'), 'WD-JOURNAL')
        journal = JournalEntry.objects.get(transaction_type='WITHDRAWAL')
        self.assertTrue(journal.verify_balance())
        self.assertEqual(journal.provider_reference, 'WD-JOURNAL')
        lines = {e.ledger_account.name: (e.amount, e.dr_cr) for e in journal.ledger_entries.all()}
        self.assertEqual(lines[f'Wallet Liability: {self.user.username}'], (Decimal('120.00'), 'DEBIT'))
        self.assertEqual(lines['System IntaSend Wallet'], (Decimal('120.00'), 'CREDIT'))


@override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})
class InvoiceJournalLinkTests(TransactionTestCase):
    def setUp(self):
        self.issuer = get_user_model().objects.create_user(
            username='journal-inv-user', email='example@example.com', password='fake-token',
        )
        self.wallet = WalletService.get_or_create_user_wallet(self.issuer)
        self.invoice = PaymentRequest.objects.create(
            issuer=self.issuer,
            amount=Decimal('500.00'),
            description='journal invoice',
            expires_at='2099-01-01T00:00:00Z',
        )

    def test_invoice_payment_links_journal_entry(self):
        InvoiceService.process_invoice_payment(self.invoice.id, 'INV-JOURNAL')
        self.invoice.refresh_from_db()
        self.assertIsNotNone(self.invoice.journal_entry)
        journal = self.invoice.journal_entry
        self.assertEqual(journal.transaction_type, 'INVOICE_PAYMENT')
        self.assertTrue(journal.verify_balance())
        self.assertEqual(journal.provider_reference, 'INV-JOURNAL')


class ReconcileDailyTests(TransactionTestCase):
    def setUp(self):
        self.asset, _ = LedgerAccount.objects.get_or_create(
            name='System IntaSend Wallet',
            defaults={'account_type': 'ASSET', 'currency': 'KES'},
        )

    def _run(self, details=None, credentials=True):
        fake = MagicMock()
        if details is not None:
            fake.Wallets.return_value.details.return_value = details
        env = {
            'INTASEND_PUBLISHABLE_KEY': 'test-pk' if credentials else '',
            'INTASEND_API_KEY': 'test-sk' if credentials else '',
            'INTASEND_IS_TEST': 'true',
        }
        modules = {'intasend': fake} if credentials is not False else {}
        with patch.dict(sys.modules, modules), patch.dict(os.environ, env):
            LedgerService.reconcile_daily()

    def test_discrepancy_recorded_when_provider_balance_differs(self):
        Wallet.objects.none()
        LedgerAccount.objects.filter(pk=self.asset.pk).update(balance=Decimal('1000.00'))
        self._run({'available_balance': 900})
        row = ReconciliationDiscrepancy.objects.get(date=timezone.now().date())
        self.assertEqual(row.expected_balance, Decimal('1000.00'))
        self.assertEqual(row.actual_balance, Decimal('900'))
        self.assertEqual(row.difference, Decimal('100.00'))
        self.assertEqual(row.severity, 'MEDIUM')

    def test_critical_severity_for_large_gap(self):
        LedgerAccount.objects.filter(pk=self.asset.pk).update(balance=Decimal('2000.00'))
        self._run({'available_balance': 0})
        row = ReconciliationDiscrepancy.objects.get(date=timezone.now().date())
        self.assertEqual(row.severity, 'CRITICAL')

    def test_no_discrepancy_when_balances_match(self):
        LedgerAccount.objects.filter(pk=self.asset.pk).update(balance=Decimal('500.00'))
        self._run({'available_balance': 500})
        self.assertEqual(ReconciliationDiscrepancy.objects.count(), 0)

    def test_missing_credentials_is_noop(self):
        self._run(credentials=False)
        self.assertEqual(ReconciliationDiscrepancy.objects.count(), 0)
