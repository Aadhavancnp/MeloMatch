from django.urls import path

from . import views

urlpatterns = [
    # Cart URLs
    path('cart/', views.view_cart, name='view_cart'),
    path('cart/add/<int:track_id>/', views.add_to_cart, name='add_to_cart'),
    path('cart/add/spotify/<str:spotify_id>/',
         views.add_to_cart_by_spotify_id, name='add_to_cart_by_spotify_id'),
    path('cart/remove/<int:item_id>/',
         views.remove_from_cart, name='remove_from_cart'),
    path('cart/update/<int:item_id>/',
         views.update_cart_quantity, name='update_cart_quantity'),
    path('cart/clear/', views.clear_cart, name='clear_cart'),

    # Checkout & Payment URLs
    path('create-checkout-session/', views.create_checkout_session,
         name='create_checkout_session'),
    # Modified: no longer takes track_id
    path('stripe-webhook/', views.stripe_webhook, name='stripe_webhook'),
    path('payment-success/', views.payment_success, name='payment_success'),
    path('payment-cancel/', views.payment_cancel, name='payment_cancel'),

    # Order URLs
    path('orders/', views.order_history, name='order_history'),
    path('orders/<int:order_id>/', views.order_detail, name='order_detail'),
]
