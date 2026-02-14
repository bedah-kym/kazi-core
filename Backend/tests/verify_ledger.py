
import os
import django
import sys
from decimal import Decimal

# Setup Django environment
sys.path.append('/app/Backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Backend.settings')
django.setup()

from django.contrib.auth import get_user_model
from payments.services import LedgerService, WalletService, InvoiceService
from payments.models import LedgerAccount, FeeSchedule, PaymentRequest

User = get_user_model()

def run_verification():
    print("=== String Double-Entry Ledger Verification ===")
    
    # 1. Setup Data
    user, _ = User.objects.get_or_create(username='ledger_test_user', email='ledger@test.com')
    
    # Ensure fee schedule exists
    FeeSchedule.objects.get_or_create(
        transaction_type='DEPOSIT',
        defaults={'platform_fee': Decimal('50.00')}
    )
    
    # 2. Test Deposit (1000 KES)
    # IntaSend Fee: 45 KES, Platform Fee: 50 KES
    # Expected: User gets 905 KES (Wait, logic in plan: 1000 - 45 - 50 = 905)
    # Let's check the Service implementation logic again... 
    # Logic in code: user_credit = gross_amount - intasend_fee - platform_fee
    
    print("\n[Test 1] Simulating Deposit of 1000 KES...")
    print("  - IntaSend Fee: 45.00")
    print("  - Platform Fee: 50.00")
    
    journal = WalletService.process_deposit(
        user=user,
        gross_amount=Decimal('1000.00'),
        intasend_fee=Decimal('45.00'),
        provider_ref='TEST-TX-001'
    )
    
    print(f"  > Transaction Posted: {journal.reference_id}")
    print(f"  > Balanced? {journal.verify_balance()}")
    
    if not journal.verify_balance():
        print("!! FAILURE: Journal Entry is not balanced !!")
        return
        
    # Check Balances
    wallet_balance = WalletService.get_balance(user)
    print(f"  > User Wallet Balance: {wallet_balance} KES (Expected: 905.00)")
    
    system_accounts = LedgerService.get_system_accounts()
    sys_asset = system_accounts['system_asset'].get_balance()
    fee_rev = system_accounts['fee_revenue'].get_balance()
    fee_exp = system_accounts['fee_expense'].get_balance()
    
    print(f"  > System Assets: {sys_asset} (Expected: 955.00)")
    print(f"  > Fee Revenue: {fee_rev} (Expected: 50.00)")
    print(f"  > Fee Expense: {fee_exp} (Expected: 45.00)")
    
    # Verify Equation: Assets = Liabilities (User Wallet) + Equity (Revenue - Expense)
    # 955 = 905 + (50 - 45) -> 955 = 905 + 5 -> 955 = 910 ... Wait.
    # Logic: 
    # Debit Asset 955
    # Debit Expense 45
    # Credit Liability 905
    # Credit Revenue 50
    # Debits: 1000, Credits: 955.  Difference 45.
    
    # Ah, the Service logic was:
    # entries = [
    #    {'account_id': system_accounts['system_asset'].id, 'amount': net_received (955), 'dr_cr': 'DEBIT'},
    #    {'account_id': system_accounts['fee_expense'].id, 'amount': intasend_fee (45), 'dr_cr': 'DEBIT'},
    #    {'account_id': user_wallet.id, 'amount': user_credit (905), 'dr_cr': 'CREDIT'},
    #    {'account_id': system_accounts['fee_revenue'].id, 'amount': platform_fee (50), 'dr_cr': 'CREDIT'},
    # ]
    # Sum Debits = 955 + 45 = 1000
    # Sum Credits = 905 + 50 = 955 ... ERROR IN LOGIC!
    # User Credit should be 905? 
    # 1000 (Gross) - 45 (Inta) - 50 (Plat) = 905.
    # Credits sum = 955. 
    # Discrepancy of 45. 
    # Platform Fee Revenue is 50.
    
    # Let's re-read the Plan Option C:
    # Debit System Asset: +955
    # Debit Expense: +45
    # Credit Liability: +950 (User gets 950? No, 1000 - 45 - 50 = 905)
    # Credit Revenue: +50 
    
    # If user deposits 1000. 
    # We pay 45 fee. We get 955.
    # We take 50 fee. User gets 905.
    # Debits: 955 + 45 = 1000.
    # Credits: 905 + 50 = 955.
    # Missing Credit! Where?
    
    # Ah, "Transaction Fee Expense" vs "Platform Fee Revenue".
    # User paid 1000. 
    # Asset went up 955.
    # Expense went up 45.
    # User Wallet (Liability) went up 905.
    # Revenue (Equity) went up 50.
    
    # Wait, 1000 - 45 - 50 = 905.
    # 905 + 50 = 955.
    # 955 + 45 = 1000.
    
    # The missing 45 credit...
    # The 45 expense we paid... who paid it?
    # The user "paid" it by receiving less.
    # But effectively, if we track "Expense 45", we need an offsetting Credit?
    # No, typically you Debit Expense and Credit Cash. But here Cash came IN.
    
    # Correct accounting for "Net Settlement":
    # Cash In: 955 (Debit Asset)
    # We recognize the FULL 1000 revenue? No.
    # The user sent 1000. 
    # In reality:
    # 1. User sends 1000.  (IntaSend takes 45). We get 955.
    # 2. We owe user X.
    # 3. We earn Y.
    
    # If we want Debits=Credits:
    # Debit Asset: 955
    # Debit Expense: 45
    # Credit User Liability: 905
    # Credit Revenue: 50
    # Sum Debits: 1000
    # Sum Credits: 955
    
    # The "Expense" of 45... implies we paid it from somewhere. 
    # But we never held the 45. IntaSend took it.
    # If we book the Expense, we must effectively "Gross Up" the receipt.
    # Imagine we received 1000 (Debit Asset 1000). 
    # Then paid 45 (Credit Asset 45, Debit Expense 45).
    # Net Asset Debit: 955.
    
    # So the Journal Entry should implicitly handle this?
    # If we Debit Expense 45, we need to Credit... something?
    # If we treat it as:
    # Debit Asset: 955
    # Debit Expense: 45
    # Credit User Liability: 905
    # Credit Revenue: 50
    # ... We are still off by 45.
    
    # The User "paid" the expense. 
    # If User Liability is 905... and we took 50... 
    # Where did the other 45 go? It went to IntaSend.
    # From the user's perspective: 1000 sent.
    
    # Maybe we just don't book the Expense if we are passing it to the user?
    # Use Net Method:
    # Debit Asset: 955
    # Credit User Liability: 905
    # Credit Revenue: 50
    # 955 = 955. Balanced!
    
    # BUT, then we don't track the 45 expense in our books. 
    # If we want to track it (Gross Method):
    # Debit Asset: 955
    # Debit Expense: 45
    # Credit User Liability: 905
    # Credit Revenue: 95 (50 profit + 45 cost reimbursement?)
    
    # Let's run the script and see if it fails.
    
if __name__ == '__main__':
    run_verification()
