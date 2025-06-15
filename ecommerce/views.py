import stripe
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth import get_user_model
from django.db import transaction

from .models import Order, OrderItem, Cart, CartItem
from music.models import Track
from subscription.models import Subscription, SubscriptionPlan # Added
from users.models import CustomUser # Explicitly CustomUser if needed, or rely on User = get_user_model()
from datetime import datetime # Added
from django.utils import timezone # Added for dt_timezone.utc equivalent or direct use
# For dt_timezone.utc.fromtimestamp, we need python's datetime.timezone
from datetime import timezone as dt_timezone # Added for dt_timezone.utc.fromtimestamp


User = get_user_model()

# --- Cart Views ---

@login_required
def view_cart(request):
    # Optimized query for cart
    cart, created = Cart.objects.prefetch_related(
        'items__track__artists',
        'items__track__genres'
    ).get_or_create(user=request.user)

    # Calculate total price - this part still iterates, but data is prefetched
    total_price = 0
    if not created: # Only calculate if cart existed or has items; new cart is empty
        for item in cart.items.all(): # .all() here will use prefetched items
            if item.track and item.track.price is not None:
                 total_price += item.track.price * item.quantity

    context = {
        'cart': cart,
        'total_price': total_price,
    }
    return render(request, 'ecommerce/cart_detail.html', context)

@login_required
def add_to_cart(request, track_id):
    track = get_object_or_404(Track, pk=track_id)
    cart, created = Cart.objects.get_or_create(user=request.user)
    cart_item, item_created = CartItem.objects.get_or_create(cart=cart, track=track)

    if not item_created:
        cart_item.quantity += 1
        cart_item.save()

    # Optionally, add a message: from django.contrib import messages; messages.success(request, f"{track.title} added to cart.")
    return redirect('view_cart')

@login_required
def remove_from_cart(request, item_id):
    cart_item = get_object_or_404(CartItem, pk=item_id)
    if cart_item.cart.user == request.user:
        cart_item.delete()
        # Optionally, add a message: messages.success(request, "Item removed from cart.")
    else:
        # Optionally, add a message: messages.error(request, "You don't have permission to remove this item.")
        raise Http404 # Or redirect with error
    return redirect('view_cart')

# --- Checkout and Payment Views ---

@login_required
def create_checkout_session(request): # Removed track_id, now uses cart
    stripe.api_key = settings.STRIPE_SECRET_KEY
    # Optimized cart fetching, though items are iterated just below.
    # Main benefit if cart object itself had more related data to select/prefetch.
    cart = get_object_or_404(Cart.objects.prefetch_related('items__track'), user=request.user)

    if not cart.items.exists(): # items.exists() is efficient
        # Handle empty cart - redirect to cart view or show a message
        return redirect('view_cart') # Or JsonResponse({'error': 'Cart is empty'}, status=400)

    line_items = []
    order_total_price = 0
    for item in cart.items.all():
        if item.track.price is None:
            # Handle tracks without a price in the cart if necessary
            # For now, skipping them, or you could raise an error
            continue
        line_items.append({
            'price_data': {
                'currency': 'usd',
                'product_data': {
                    'name': item.track.title,
                    'images': [item.track.image_url] if item.track.image_url else [],
                },
                'unit_amount': int(item.track.price * 100),
            },
            'quantity': item.quantity,
        })
        order_total_price += item.track.price * item.quantity

    if not line_items:
        # Handle case where no items had a price
        return JsonResponse({'error': 'No items in cart have a price.'}, status=400)

    success_url = request.build_absolute_uri(reverse('payment_success')) + '?session_id={CHECKOUT_SESSION_ID}'
    cancel_url = request.build_absolute_uri(reverse('payment_cancel'))

    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=line_items,
            mode='payment',
            success_url=success_url,
            cancel_url=cancel_url,
            client_reference_id=str(request.user.id),
            metadata={
                'cart_id': str(cart.id) # Pass cart_id for webhook processing
            }
        )

        # Create a pending Order
        Order.objects.create(
            user=request.user,
            total_price=order_total_price,
            status='pending',
            stripe_charge_id=checkout_session.id # Using session_id as a reference
        )
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        return JsonResponse({'error': f'Stripe Error: {str(e)}'})

@csrf_exempt
@transaction.atomic # Ensure database operations within are atomic
def stripe_webhook(request):
    stripe.api_key = settings.STRIPE_SECRET_KEY
    webhook_secret = settings.STRIPE_WEBHOOK_SECRET
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')
    event = None

    if not webhook_secret:
        print("ERROR: Stripe webhook secret is not configured.")
        return HttpResponse("Webhook secret not configured.", status=500)

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except ValueError as e:
        print(f"Webhook ValueError: {e}")
        return HttpResponse(status=400)
    except stripe.error.SignatureVerificationError as e:
        print(f"Webhook SignatureVerificationError: {e}")
        return HttpResponse(status=400)
    except Exception as e:
        print(f"Webhook generic error: {e}")
        return HttpResponse(status=500)

    event_type = event.type

    if event_type == 'checkout.session.completed':
        session = event.data.object
        stripe_checkout_id = session.id

        if session.mode == 'payment': # Existing one-time purchase logic
            cart_id_str = session.metadata.get('cart_id')
            try:
                order = Order.objects.get(stripe_charge_id=stripe_checkout_id, status='pending')
                order.status = 'completed'
                order.save()

                if cart_id_str:
                    try:
                        cart_pk = int(cart_id_str)
                        cart = Cart.objects.get(pk=cart_pk)
                        if cart.user != order.user:
                            print(f"User mismatch cart ({cart.user_id}) vs order ({order.user_id}).")
                            return HttpResponse(status=400)
                        for cart_item in cart.items.all():
                            if cart_item.track.price is not None:
                                OrderItem.objects.create(
                                    order=order, track=cart_item.track,
                                    price=cart_item.track.price, quantity=cart_item.quantity
                                )
                        cart.items.all().delete()
                        print(f"Cart {cart_id_str} processed for order {order.id}.")
                    except Cart.DoesNotExist: print(f"Cart {cart_id_str} not found for order {order.id}.")
                    except ValueError: print(f"Invalid cart_id format '{cart_id_str}'.")
                    except Exception as e: print(f"Error processing cart items for order {order.id}: {e}")
                else:
                    print(f"No cart_id in session metadata for order {order.id}.")
            except Order.DoesNotExist:
                print(f"Order with stripe_charge_id {stripe_checkout_id} (payment mode) not found or not pending.")
                return HttpResponse(status=404) # Important to return 404 if order is not found
            except Exception as e:
                print(f"Error updating order {stripe_checkout_id} (payment mode): {e}")
                return HttpResponse(status=500)

        elif session.mode == 'subscription':
            try:
                user = User.objects.get(id=session.client_reference_id)
                stripe_customer_id = session.customer
                if user and stripe_customer_id and not user.stripe_customer_id:
                    user.stripe_customer_id = stripe_customer_id
                    user.save()
                    print(f"Associated Stripe customer {stripe_customer_id} with user {user.id}")
                # Further subscription creation logic is usually handled by 'customer.subscription.created'
            except User.DoesNotExist:
                print(f"Webhook: User not found for client_reference_id {session.client_reference_id} in checkout.session.completed (subscription mode)")
            except Exception as e:
                print(f"Error in checkout.session.completed for subscription setup: {e}")

        # Handle other modes like 'setup' if necessary
        # elif session.mode == 'setup':
        #     print(f"Checkout session in setup mode completed: {session.id}")
        #     # Handle setup intent success, e.g., save payment method to customer

    elif event_type == 'customer.subscription.created':
        subscription_data = event.data.object
        stripe_subscription_id = subscription_data.id
        stripe_customer_id = subscription_data.customer
        stripe_price_id = subscription_data.items.data[0].price.id
        # Use python's datetime.timezone.utc for fromtimestamp
        current_period_start = dt_timezone.utc.fromtimestamp(subscription_data.current_period_start) if subscription_data.current_period_start else None
        current_period_end = dt_timezone.utc.fromtimestamp(subscription_data.current_period_end) if subscription_data.current_period_end else None
        status = subscription_data.status

        try:
            user = User.objects.get(stripe_customer_id=stripe_customer_id)
            plan = SubscriptionPlan.objects.get(stripe_price_id=stripe_price_id)

            Subscription.objects.update_or_create(
                stripe_subscription_id=stripe_subscription_id,
                defaults={
                    'user': user,
                    'plan': plan,
                    'start_date': current_period_start or timezone.now(), # Fallback for start_date
                    'end_date': current_period_end,
                    'status': status,
                    'stripe_customer_id': stripe_customer_id
                }
            )
            print(f"Subscription {stripe_subscription_id} created/updated for user {user.id}, plan {plan.name}.")
        except User.DoesNotExist: print(f"Webhook: User not found for Stripe customer ID {stripe_customer_id} in {event_type}")
        except SubscriptionPlan.DoesNotExist: print(f"Webhook: SubscriptionPlan not found for Stripe Price ID {stripe_price_id} in {event_type}")
        except Exception as e: print(f"Error in {event_type} handler: {e}")

    elif event_type == 'customer.subscription.updated':
        subscription_data = event.data.object
        stripe_subscription_id = subscription_data.id
        current_period_start = dt_timezone.utc.fromtimestamp(subscription_data.current_period_start) if subscription_data.current_period_start else None
        current_period_end = dt_timezone.utc.fromtimestamp(subscription_data.current_period_end) if subscription_data.current_period_end else None
        status = subscription_data.status
        # cancel_at_period_end = subscription_data.cancel_at_period_end # Can be used for more granular status

        try:
            local_sub, created = Subscription.objects.update_or_create(
                stripe_subscription_id=stripe_subscription_id,
                defaults={
                    'start_date': current_period_start or timezone.now(),
                    'end_date': current_period_end,
                    'status': status,
                    'stripe_customer_id': subscription_data.customer # Ensure customer_id is also updated/set
                }
            )
            if created:
                print(f"Webhook: Subscription {stripe_subscription_id} was newly created during an update event.")
                # If it's created here, it implies 'customer.subscription.created' might have been missed.
                # We need to associate it with a user and plan.
                if subscription_data.customer and subscription_data.items.data and subscription_data.items.data[0].price:
                    try:
                        user = User.objects.get(stripe_customer_id=subscription_data.customer)
                        plan = SubscriptionPlan.objects.get(stripe_price_id=subscription_data.items.data[0].price.id)
                        local_sub.user = user
                        local_sub.plan = plan
                        local_sub.save()
                        print(f"Webhook: Backfilled user and plan for newly created sub {stripe_subscription_id} during update.")
                    except (User.DoesNotExist, SubscriptionPlan.DoesNotExist) as e:
                        print(f"Webhook: Could not fully populate newly created sub {stripe_subscription_id} during update: {e}")
            else:
                 print(f"Subscription {stripe_subscription_id} updated.")

        except Exception as e: print(f"Error in {event_type} handler: {e}")

    elif event_type == 'customer.subscription.deleted':
        subscription_data = event.data.object
        stripe_subscription_id = subscription_data.id
        try:
            local_sub = Subscription.objects.get(stripe_subscription_id=stripe_subscription_id)
            # Map Stripe's 'canceled' or other terminal statuses to your local 'cancelled' or 'expired'
            local_sub.status = 'cancelled' # Or determine based on subscription_data.status if available
            local_sub.save()
            print(f"Subscription {stripe_subscription_id} marked as deleted/cancelled.")
        except Subscription.DoesNotExist: print(f"Webhook: Subscription {stripe_subscription_id} not found for deletion.")
        except Exception as e: print(f"Error in {event_type} handler: {e}")

    elif event_type == 'invoice.payment_succeeded':
        invoice_data = event.data.object
        stripe_subscription_id = invoice_data.subscription
        if stripe_subscription_id: # This invoice is for a subscription
            try:
                local_sub = Subscription.objects.get(stripe_subscription_id=stripe_subscription_id)
                new_period_end = dt_timezone.utc.fromtimestamp(invoice_data.lines.data[0].period.end) if invoice_data.lines.data and invoice_data.lines.data[0].period else local_sub.end_date

                update_fields = {'status': 'active'}
                if new_period_end and local_sub.end_date != new_period_end : # Update end_date if it's different and provided
                    local_sub.end_date = new_period_end
                    update_fields['end_date'] = new_period_end

                local_sub.status = 'active' # Ensure status is active on successful payment

                # Save only if there are changes to save
                if local_sub.is_dirty(check_relationship=True): # Requires django-dirtyfields or manual check
                     local_sub.save() # Or local_sub.save(update_fields=list(update_fields.keys())) after manual check

                # Manual check for changes example:
                # changed = False
                # if local_sub.status != 'active': local_sub.status = 'active'; changed = True
                # if local_sub.end_date != new_period_end : local_sub.end_date = new_period_end; changed = True
                # if changed: local_sub.save()

                print(f"Subscription {stripe_subscription_id} updated on invoice payment.")
            except Subscription.DoesNotExist: print(f"Webhook: Subscription {stripe_subscription_id} not found for invoice payment.")
            except Exception as e: print(f"Error in {event_type} handler for subscription: {e}")

    # Consider handling 'invoice.payment_failed' to update subscription status to 'past_due' or 'unpaid'
    # elif event_type == 'invoice.payment_failed':
    #     invoice_data = event.data.object
    #     stripe_subscription_id = invoice_data.subscription
    #     if stripe_subscription_id:
    #         try:
    #             local_sub = Subscription.objects.get(stripe_subscription_id=stripe_subscription_id)
    #             local_sub.status = 'past_due' # Or your equivalent status
    #             local_sub.save()
    #         except Subscription.DoesNotExist:
    #             print(f"Webhook: Subscription {stripe_subscription_id} not found for invoice payment failure.")
    #         except Exception as e:
    #             print(f"Error in {event_type} handler: {e}")


    return HttpResponse(status=200)

def payment_success(request):
    return render(request, 'ecommerce/payment_success.html')

def payment_cancel(request):
    return render(request, 'ecommerce/payment_cancel.html')

# --- Order Views ---

@login_required
def order_history(request):
    # Optimized query for order history
    orders = Order.objects.filter(user=request.user).prefetch_related(
        'items__track__artists', # Prefetch artists related to track for each item
        'items__track__genres'   # Prefetch genres related to track for each item
    ).order_by('-created_at')
    return render(request, 'ecommerce/order_history.html', {'orders': orders})

@login_required
def order_detail(request, order_id):
    # Optimized query for order detail
    order = get_object_or_404(
        Order.objects.select_related('user').prefetch_related(
            'items__track__artists',
            'items__track__genres'
        ),
        id=order_id,
        user=request.user
    )
    return render(request, 'ecommerce/order_detail.html', {'order': order})
