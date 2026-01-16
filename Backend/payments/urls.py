"""
Payment app URL configuration
"""
from django.urls import path
from . import views

app_name = 'payments'

urlpatterns = [
    # Wallet views
    path('wallet/', views.wallet_dashboard, name='wallet_dashboard'),
    path('wallet/deposit/', views.initiate_deposit, name='initiate_deposit'),
    path('wallet/callback/', views.payment_callback, name='payment_callback'),
    
    # Invoice views
    path('invoice/create/', views.create_invoice_view, name='create_invoice'),
    path('invoice/<uuid:reference_id>/', views.invoice_detail, name='invoice_detail'),
    
    # API endpoints
    path('api/balance/', views.get_balance_api, name='api_balance'),
    path('api/transactions/', views.list_transactions_api, name='api_transactions'),
]
