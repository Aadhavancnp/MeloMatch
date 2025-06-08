from django.db import models
from django.utils import timezone

from users.models import CustomUser


class Genre(models.Model):
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


class Artist(models.Model):
    name = models.CharField(max_length=100)
    spotify_id = models.CharField(max_length=100, unique=True, db_index=True)
    genres = models.ManyToManyField(Genre, related_name='artists')

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


class Track(models.Model):
    title = models.CharField(max_length=200, db_index=True)
    spotify_id = models.CharField(max_length=100, unique=True, db_index=True)
    album = models.CharField(max_length=200)
    duration = models.DurationField(null=True)
    preview_url = models.URLField(null=True, blank=True)
    image_url = models.URLField(null=True, blank=True)
    popularity = models.IntegerField(default=0)
    release_date = models.DateField(null=True, blank=True)

    artists = models.ManyToManyField(Artist, related_name='tracks')
    genres = models.ForeignKey(Genre, on_delete=models.CASCADE)
    audio_features = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title

    @property
    def artists_names(self):
        return [artist.name for artist in self.artists.all()]

    @property
    def primary_genre(self):
        primary_genre = self.genres.first()
        return primary_genre.name if primary_genre else 'Unknown'

    class Meta:
        ordering = ['-created_at']


class Playlist(models.Model):
    owner = models.ForeignKey(CustomUser, related_name='owned_playlists', on_delete=models.CASCADE, null=True, blank=True)
    name = models.CharField(max_length=200, db_index=True) # Added db_index back
    spotify_id = models.CharField(max_length=100, unique=True, db_index=True, null=True, blank=True) # Made nullable
    description = models.TextField(null=True, blank=True)
    tracks = models.ManyToManyField(Track, related_name='member_of_playlists', blank=True) # Changed related_name, blank=True
    collaborators = models.ManyToManyField(CustomUser, related_name='collaborative_playlists', blank=True) # Added
    image_url = models.URLField(null=True, blank=True)
    is_public = models.BooleanField(default=False) # Added
    created_at = models.DateTimeField(auto_now_add=True, db_index=True) # Added db_index back
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        owner_username = self.owner.username if self.owner else "System"
        return f"{owner_username} - {self.name}"

    @property
    def track_count(self):
        return self.tracks.count()

    def can_edit(self, user):
        if not user or not user.is_authenticated:
            return False
        return self.owner == user or user in self.collaborators.all()


# --- Re-adding models that were lost due to reset_all ---

class Cart(models.Model):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='music_cart') # Added related_name
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Cart for {self.user.username}"


class CartItem(models.Model):
    cart = models.ForeignKey(Cart, related_name='items', on_delete=models.CASCADE)
    track = models.ForeignKey(Track, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)

    class Meta:
        unique_together = ('cart', 'track')

    def __str__(self):
        return f"{self.quantity} x {self.track.title} in cart for {self.cart.user.username}"


class Order(models.Model):
    ORDER_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('refunded', 'Refunded'), # Added more states
        ('cancelled', 'Cancelled'),
    ]
    user = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='music_orders') # SET_NULL if user deleted
    total_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    status = models.CharField(max_length=20, choices=ORDER_STATUS_CHOICES, default='pending', db_index=True)
    stripe_payment_intent_id = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Order {self.id} by {self.user.username if self.user else 'N/A'} - {self.status}"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, related_name='items', on_delete=models.CASCADE)
    track = models.ForeignKey(Track, on_delete=models.PROTECT) # PROTECT to prevent track deletion if ordered
    quantity = models.PositiveIntegerField(default=1)
    price_at_purchase = models.DecimalField(max_digits=10, decimal_places=2) # Renamed from 'price'

    class Meta:
        unique_together = ('order', 'track')

    def __str__(self):
        return f"{self.quantity} x {self.track.title} in order {self.order.id}"


class LocalAudioClip(models.Model):
    track = models.ForeignKey(Track, related_name='local_clips', on_delete=models.CASCADE)
    audio_file = models.FileField(upload_to='track_previews/')
    source_url = models.URLField(max_length=1024, blank=True, null=True)
    source_type = models.CharField(max_length=50, blank=True, null=True, db_index=True)
    duration = models.IntegerField(help_text="Duration in seconds", blank=True, null=True)
    extracted_at = models.DateTimeField(auto_now_add=True, db_index=True)
    metadata = models.JSONField(null=True, blank=True, help_text="Any other source-specific metadata")

    class Meta:
        ordering = ['-extracted_at']
        # unique_together = [['track', 'source_type']] # Consider if needed

    def __str__(self):
        return f"Local clip for {self.track.title} from {self.source_type or 'unknown source'}"


class UserLikedTrack(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='music_liked_tracks') # Added related_name
    track = models.ForeignKey(Track, on_delete=models.CASCADE, related_name='liked_by_users_through_music') # Added related_name
    liked_at = models.DateTimeField(auto_now_add=True, db_index=True) # Added db_index

    class Meta:
        unique_together = ('user', 'track')
        ordering = ['-liked_at']
        indexes = [
            models.Index(fields=['user', 'liked_at']), # For user's liked tracks over time
        ]

    def __str__(self):
        return f"{self.user.username} likes {self.track.title}"

    def can_view(self, user):
        if self.is_public:
            return True
        if not user or not user.is_authenticated: # Check if user is authenticated before accessing collaborators
            return False
        return self.owner == user or user in self.collaborators.all()
