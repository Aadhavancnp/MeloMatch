import stripe # Added for Stripe integration
from django.conf import settings # Added for Stripe keys
from django.shortcuts import render, redirect, get_object_or_404 # Added get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse # Added for error responses
from django.urls import reverse # Added for reversing URLs
from .models import SubscriptionPlan, Subscription
from django.utils import timezone
from datetime import timedelta


@login_required
def subscription_plans(request):
    plans = SubscriptionPlan.objects.all()
    user_subscription = Subscription.objects.filter(user=request.user).first()

    for plan in plans:
        plan.features_list = plan.features.split(',')

    context = {
        'plans': plans,
        'user_subscription': user_subscription,
    }
    return render(request, 'subscription/plans.html', context)


@login_required
def subscribe(request, plan_id):
    plan = SubscriptionPlan.objects.get(id=plan_id)
    user_subscription = Subscription.objects.filter(user=request.user).first()

    if user_subscription:
        user_subscription.plan = plan
        user_subscription.start_date = timezone.now()
        user_subscription.end_date = timezone.now() + timedelta(days=plan.duration_days)
        user_subscription.save()
    else:
        Subscription.objects.create(
            user=request.user,
            plan=plan,
            end_date=timezone.now() + timedelta(days=plan.duration_days)
        )

    return redirect('subscription_plans')


@login_required
def create_subscription_checkout_session(request, plan_id):
    stripe.api_key = settings.STRIPE_SECRET_KEY
    plan = get_object_or_404(SubscriptionPlan, pk=plan_id)

    if not plan.stripe_price_id:
        # Log this critical error: Plan in DB is missing its Stripe Price ID
        print(f"ERROR: SubscriptionPlan {plan.id} ('{plan.name}') is missing its stripe_price_id.")
        return JsonResponse({'error': 'Configuration error: Subscription plan is not linked to a Stripe Price ID.'}, status=500)

    if not settings.STRIPE_SECRET_KEY:
         # Log this critical error
        print("ERROR: Stripe secret key not configured in Django settings.")
        return JsonResponse({'error': 'Stripe secret key not configured.'}, status=500)

    stripe_customer_id = request.user.stripe_customer_id
    customer_kwargs = {}

    if stripe_customer_id:
        customer_kwargs['customer'] = stripe_customer_id
    else:
        # If creating customer here, pass email, name, etc.
        # This example lets Checkout create the customer if one isn't provided.
        # Stripe automatically creates a customer if 'customer' is not provided,
        # and it will be available in the 'checkout.session.completed' event as session.customer.
        # We then associate it with the user in the webhook.
        # Alternatively, create/retrieve customer explicitly:
        # try:
        #     customer = stripe.Customer.create(
        #         email=request.user.email,
        #         name=request.user.get_full_name() or request.user.username,
        #         metadata={'user_id': request.user.id}
        #     )
        #     stripe_customer_id = customer.id
        #     request.user.stripe_customer_id = stripe_customer_id
        #     request.user.save()
        #     customer_kwargs['customer'] = stripe_customer_id
        # except Exception as e:
        #     print(f"Error creating Stripe customer for user {request.user.id}: {e}")
        #     return JsonResponse({'error': 'Could not create Stripe customer.'}, status=500)
        pass # Let Stripe Checkout create the customer


    success_url = request.build_absolute_uri(reverse('payment_success')) + '?session_id={CHECKOUT_SESSION_ID}'
    cancel_url = request.build_absolute_uri(reverse('payment_cancel'))

    try:
        checkout_session_params = {
            'payment_method_types': ['card'],
            'line_items': [{'price': plan.stripe_price_id, 'quantity': 1}],
            'mode': 'subscription',
            'success_url': success_url,
            'cancel_url': cancel_url,
            'client_reference_id': str(request.user.id),
            'metadata': {'plan_db_id': plan.id, 'user_db_id': request.user.id} # Store local IDs for webhook
        }
        if stripe_customer_id: # Pass customer if already exists
             checkout_session_params['customer'] = stripe_customer_id
        else: # Otherwise, ask Stripe to collect email for new customer
             checkout_session_params['customer_email'] = request.user.email


        checkout_session = stripe.checkout.Session.create(**checkout_session_params)

        # It's generally better to create the local Subscription record after Stripe confirms
        # via webhook ('customer.subscription.created' or 'invoice.payment_succeeded').
        # Creating a 'pending' subscription here can lead to orphaned records if user abandons checkout.

        return redirect(checkout_session.url, code=303)
    except Exception as e:
        print(f"Error creating Stripe checkout session: {e}") # Log this error
        # Provide a generic error to user, or specific if safe.
        return JsonResponse({'error': 'Could not initiate subscription. Please try again later or contact support.'}, status=500)

# Reminder: The stripe_webhook in ecommerce/views.py handles subscription events.
# to handle subscription events like:
# - 'invoice.payment_succeeded': To provision or update the subscription.
# - 'customer.subscription.updated': To handle changes like upgrades, downgrades, cancellations.
# - 'customer.subscription.deleted': To handle cancellations.
# These events should update the local Subscription model accordingly.
