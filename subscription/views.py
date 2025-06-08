import stripe
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.urls import reverse
from django.contrib import messages

from .models import SubscriptionPlan, Subscription
from music.models import Order, OrderItem, Cart # Added Cart
from users.models import UserActivity # For logging activity

from django.utils import timezone
from datetime import timedelta

stripe.api_key = settings.STRIPE_SECRET_KEY


@login_required
def create_checkout_session(request, order_id):
    order = get_object_or_404(Order, id=order_id, user=request.user)

    if order.status == 'completed':
        messages.error(request, "This order has already been paid.")
        return redirect('order_history')

    if order.status == 'failed': # Allow retrying payment for failed orders
        order.status = 'pending'
        order.save()

    line_items = []
    for item in order.items.all():
        line_items.append({
            'price_data': {
                'currency': 'usd',
                'product_data': {
                    'name': item.track.title,
                    'images': [item.track.image_url] if item.track.image_url else [],
                },
                'unit_amount': int(item.price * 100),  # Price in cents
            },
            'quantity': item.quantity,
        })

    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=line_items,
            mode='payment',
            success_url=request.build_absolute_uri(reverse('payment_success', args=[order.id])),
            cancel_url=request.build_absolute_uri(reverse('payment_cancel', args=[order.id])),
            metadata={
                'order_id': order.id,
                'user_id': request.user.id,
            },
            payment_intent_data={
                'metadata': {
                    'order_id': order.id,
                    'user_id': request.user.id,
                }
            }
        )
        order.stripe_payment_intent_id = checkout_session.payment_intent
        order.save()
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        messages.error(request, f"Error creating Stripe checkout session: {str(e)}")
        return redirect('order_history')


@csrf_exempt
def stripe_webhook(request):
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')
    event = None

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except ValueError as e: # Invalid payload
        return HttpResponse(status=400)
    except stripe.error.SignatureVerificationError as e: # Invalid signature
        return HttpResponse(status=400)

    # Handle the event
    if event.type == 'checkout.session.completed':
        session = event.data.object
        order_id = session.metadata.get('order_id')
        payment_intent_id = session.payment_intent

        try:
            order = Order.objects.get(id=order_id)
            if order.status != 'completed': # Ensure idempotency
                order.status = 'completed'
                order.stripe_payment_intent_id = payment_intent_id
                order.save()

                # Log successful payment activity
                UserActivity.objects.create(
                    user=order.user,
                    activity_type='payment_successful',
                    description=f"Payment successful for Order ID: {order.id}, Stripe PI: {payment_intent_id}"
                )

                # Clear the cart for the user (if items were from a cart-based order)
                # This logic might be better placed immediately after order creation if payment is deferred
                # For now, we assume cart was cleared when order was initiated
                # cart = Cart.objects.filter(user=order.user).first() # Cart model is in music app
                # if cart:
                #     cart.items.all().delete()

                # Call Celery task to send order confirmation email
                from music.tasks import send_order_confirmation_email_task
                send_order_confirmation_email_task.delay(order.id)

        except Order.DoesNotExist:
            return HttpResponse(status=404) # Order not found

    elif event.type == 'payment_intent.payment_failed':
        payment_intent = event.data.object
        order_id = payment_intent.metadata.get('order_id')
        try:
            order = Order.objects.get(id=order_id)
            if order.status != 'completed': # Don't mark completed orders as failed
                order.status = 'failed'
                order.save()
                UserActivity.objects.create(
                    user=order.user,
                    activity_type='payment_failed',
                    description=f"Payment failed for Order ID: {order.id}, Stripe PI: {payment_intent.id}"
                )
        except Order.DoesNotExist:
            return HttpResponse(status=404) # Order not found

    # Other event types can be handled here

    return HttpResponse(status=200)


@login_required
def payment_success(request, order_id):
    order = get_object_or_404(Order, id=order_id, user=request.user)
    if order.status == 'completed':
        messages.success(request, "Your payment was successful and the order is confirmed.")
    else: # Should ideally be completed by webhook, but handle if webhook is delayed
        order.status = 'completed'
        order.save()
        messages.info(request, "Payment successful. Order status updated.")
        # Log user activity if not already logged by webhook (idempotency check needed in webhook)
        UserActivity.objects.get_or_create(
            user=request.user,
            activity_type='payment_successful',
            description=f"Payment successful for Order ID: {order.id} (confirmed via success page)"
        )

    return render(request, 'subscription/payment_success.html', {'order': order})


@login_required
def payment_cancel(request, order_id):
    order = get_object_or_404(Order, id=order_id, user=request.user)
    if order.status != 'completed': # Don't mark completed orders as failed
        order.status = 'failed'
        order.save()
        messages.warning(request, "Your payment was cancelled or failed. Please try again or contact support.")
        UserActivity.objects.get_or_create(
            user=request.user,
            activity_type='payment_cancelled',
            description=f"Payment cancelled for Order ID: {order.id}"
        )
    else:
        messages.info(request, "Payment was cancelled, but this order was already completed.")

    return render(request, 'subscription/payment_cancel.html', {'order': order})


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
