from django.urls import path

from . import views

urlpatterns = [
    path('plans/', views.subscription_plans, name='subscription_plans'),
    path('subscribe/<int:plan_id>/', views.subscribe, name='subscribe'),
    path('create-subscription-checkout/<int:plan_id>/', views.create_subscription_checkout_session,
         name='create_subscription_checkout_session'),
]
