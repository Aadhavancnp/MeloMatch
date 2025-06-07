from django.contrib import admin

from music.models import Track, Playlist, Cart, CartItem, Order, OrderItem

# Register your models here.
admin.site.register(Track)
admin.site.register(Playlist)
admin.site.register(Cart)
admin.site.register(CartItem)
admin.site.register(Order)
admin.site.register(OrderItem)
from .models import LocalAudioClip
admin.site.register(LocalAudioClip)
