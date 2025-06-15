from django.urls import reverse
from django.test import Client, TestCase # Use TestCase for DB operations
from users.models import CustomUser
from music.models import Track, Genre, Artist # Added Artist
from .models import Cart, CartItem, Order, OrderItem # Added OrderItem

class EcommerceTestCase(TestCase):
    def setUp(self):
        # Create a user
        self.user = CustomUser.objects.create_user(username='testuser', email='test@example.com', password='password')

        # Create a genre
        self.genre = Genre.objects.create(name='Test Genre')

        # Create an artist (Track model requires artists)
        # Note: Track.artists is a ManyToManyField, so it needs to be handled after Track creation or by passing instances.
        # For simplicity in setUp, if Track model doesn't strictly require an artist on create (e.g. can be added later),
        # this can be omitted or an artist created but not immediately linked if not needed for the test's core logic.
        # However, if Track.artists is required or accessed (e.g., track.artists_names), it's better to set it up.
        # The current Track model seems to have artists as ManyToMany, so it's not required on create.
        # self.artist = Artist.objects.create(name='Test Artist', spotify_id='testartistspotifyid')

        # Create a track
        self.track = Track.objects.create(
            title='Test Track',
            genres=self.genre,
            spotify_id='testspotifyid',
            price=0.99,
            album='Test Album' # Assuming album is a CharField and required
        )
        # If artists are needed for the track and it's a ManyToManyField:
        # self.track.artists.add(self.artist)

        # Set up the client and log in
        self.client = Client()
        self.client.login(username='testuser', password='password')

    def test_add_to_cart(self):
        """Test adding a track to the cart."""
        # Ensure cart is empty initially or doesn't exist
        Cart.objects.filter(user=self.user).delete()

        response = self.client.post(reverse('add_to_cart', args=[self.track.pk]))

        # Check if redirected to view_cart (status code 302)
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('view_cart'))

        # Verify cart and cart item
        try:
            cart = Cart.objects.get(user=self.user)
            self.assertEqual(cart.items.count(), 1)
            cart_item = cart.items.first()
            self.assertEqual(cart_item.track, self.track)
            self.assertEqual(cart_item.quantity, 1)
        except Cart.DoesNotExist:
            self.fail("Cart was not created for the user.")

    def test_view_cart_empty(self):
        """Test viewing an empty cart."""
        Cart.objects.filter(user=self.user).delete() # Ensure cart is empty
        response = self.client.get(reverse('view_cart'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your cart is empty")

    def test_view_cart_with_items(self):
        """Test viewing a cart with items."""
        # Add item to cart first
        CartItem.objects.create(cart=Cart.objects.create(user=self.user), track=self.track, quantity=2)

        response = self.client.get(reverse('view_cart'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.track.title)
        self.assertContains(response, "Cart Total")
        # Check for quantity if displayed, e.g., using response.context['cart'].items.first().quantity

    def test_remove_from_cart(self):
        """Test removing an item from the cart."""
        cart = Cart.objects.create(user=self.user)
        cart_item = CartItem.objects.create(cart=cart, track=self.track, quantity=1)

        response = self.client.post(reverse('remove_from_cart', args=[cart_item.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('view_cart'))

        # Verify item is removed
        with self.assertRaises(CartItem.DoesNotExist):
            CartItem.objects.get(pk=cart_item.pk)
        # Or check cart items count
        self.assertEqual(cart.items.count(), 0)

    # Mocking Stripe for order creation/webhook tests is complex and outside the scope of typical unit tests
    # unless using a dedicated library like `pytest-stripe` or heavy mocking.
    # For now, these tests focus on cart interactions.
    # A test for `order_history` or `order_detail` would involve creating Order/OrderItem instances directly.

    def test_order_history_empty(self):
        """Test viewing order history when no orders exist."""
        response = self.client.get(reverse('order_history'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You have no orders yet.")

    def test_order_history_with_orders(self):
        """Test viewing order history with existing orders."""
        order = Order.objects.create(user=self.user, total_price=self.track.price, status='completed')
        OrderItem.objects.create(order=order, track=self.track, price=self.track.price, quantity=1)

        response = self.client.get(reverse('order_history'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Order #{order.id}") # Check if order ID is in the response

    def test_order_detail(self):
        """Test viewing a specific order's detail."""
        order = Order.objects.create(user=self.user, total_price=self.track.price, status='completed')
        order_item = OrderItem.objects.create(order=order, track=self.track, price=self.track.price, quantity=1)

        response = self.client.get(reverse('order_detail', args=[order.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Order #{order.id}")
        self.assertContains(response, self.track.title)
        self.assertContains(response, f"${order_item.price:.2f}") # Check for formatted price
        self.assertContains(response, str(order_item.quantity))

    def test_order_detail_unauthorized(self):
        """Test that a user cannot view another user's order."""
        other_user = CustomUser.objects.create_user(username='otheruser', password='password')
        order = Order.objects.create(user=other_user, total_price=10.00, status='completed')

        response = self.client.get(reverse('order_detail', args=[order.id]))
        self.assertEqual(response.status_code, 404) # get_object_or_404 with user filter
