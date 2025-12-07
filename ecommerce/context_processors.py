from .models import Cart


def cart_context(request):
    """
    Context processor to add cart count to all templates.
    Note: For async views, cart_count should be passed directly in the view context.
    """
    # Skip DB queries in async context - async views should pass cart_count directly
    # Check if cart_count is already in the request (set by async view)
    if hasattr(request, '_cart_count_set'):
        return {}

    cart_count = 0
    if request.user.is_authenticated:
        try:
            cart = Cart.objects.get(user=request.user)
            cart_count = cart.items.count()
        except Cart.DoesNotExist:
            cart_count = 0
        except Exception:
            # Handle async context errors gracefully
            cart_count = 0

    return {
        'cart_count': cart_count
    }
