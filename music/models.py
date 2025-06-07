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
    name = models.CharField(max_length=100, db_index=True) # Added db_index
    spotify_id = models.CharField(max_length=100, unique=True, db_index=True)
    genres = models.ManyToManyField(Genre, related_name='artists')

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


class Track(models.Model):
    title = models.CharField(max_length=200, db_index=True)
    spotify_id = models.CharField(max_length=100, unique=True, db_index=True)
    album = models.CharField(max_length=200, db_index=True) # Added db_index
    duration = models.DurationField(null=True)
    preview_url = models.URLField(null=True, blank=True)
    image_url = models.URLField(null=True, blank=True)
    popularity = models.IntegerField(default=0, db_index=True) # Added db_index
    release_date = models.DateField(null=True, blank=True, db_index=True) # Added db_index

    artists = models.ManyToManyField(Artist, related_name='tracks')
    genres = models.ForeignKey(Genre, on_delete=models.CASCADE) # FKs are indexed by default
    audio_features = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True) # Added db_index

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
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE) # FKs are indexed by default
    name = models.CharField(max_length=200, db_index=True) # Added db_index
    spotify_id = models.CharField(max_length=100, unique=True, db_index=True)
    description = models.TextField(null=True, blank=True)
    tracks = models.ManyToManyField(Track, related_name='playlists')
    image_url = models.URLField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True) # Added db_index
    updated_at = models.DateTimeField(auto_now=True) # Not indexing updated_at unless specific queries need it

    def __str__(self):
        return f"{self.user.username} - {self.name}"

    @property
    def track_count(self):
        return self.tracks.count()


class Cart(models.Model):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Cart for {self.user.username}"


class CartItem(models.Model):
    cart = models.ForeignKey(Cart, related_name='items', on_delete=models.CASCADE)
    track = models.ForeignKey(Track, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)

    def __str__(self):
        return f"{self.quantity} x {self.track.title} in cart for {self.cart.user.username}"


class Order(models.Model):
    ORDER_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE) # FKs are indexed by default
    total_price = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=ORDER_STATUS_CHOICES, default='pending', db_index=True) # Added db_index
    stripe_payment_intent_id = models.CharField(max_length=255, blank=True, null=True, db_index=True) # Added db_index
    created_at = models.DateTimeField(auto_now_add=True, db_index=True) # Added db_index
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Order {self.id} by {self.user.username} - {self.status}"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, related_name='items', on_delete=models.CASCADE)
    track = models.ForeignKey(Track, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2)  # Price at the time of purchase

    def __str__(self):
        return f"{self.quantity} x {self.track.title} in order {self.order.id}"


class LocalAudioClip(models.Model):
    # Consider OneToOneField if a Track should only have one canonical local preview.
    # Using ForeignKey for now for flexibility (e.g., multiple versions/sources if ever needed),
    # but application logic should enforce one primary local clip if that's the intent.
    track = models.ForeignKey(Track, related_name='local_clips', on_delete=models.CASCADE) # FKs are indexed by default
    audio_file = models.FileField(upload_to='track_previews/')
    source_url = models.URLField(max_length=1024, blank=True, null=True) # Original URL if downloaded
    source_type = models.CharField(max_length=50, blank=True, null=True, db_index=True) # Added db_index
    duration = models.IntegerField(help_text="Duration in seconds", blank=True, null=True) # e.g., 30 for a 30-second preview
    extracted_at = models.DateTimeField(auto_now_add=True, db_index=True) # Added db_index
    metadata = models.JSONField(null=True, blank=True, help_text="Any other source-specific metadata")

    def __str__(self):
        return f"Local clip for {self.track.title} from {self.source_type or 'unknown source'}"

    class Meta:
        ordering = ['-extracted_at']
        # Add a unique constraint if one track should only have one clip from a specific source_type
        # unique_together = [['track', 'source_type']]

class UserLikedTrack(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='liked_spotify_tracks')
    track = models.ForeignKey(Track, on_delete=models.CASCADE, related_name='liked_by_users')
    liked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'track')
        ordering = ['-liked_at']

    def __str__(self):
        return f"{self.user.username} likes {self.track.title}"
