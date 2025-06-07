from django.urls import path
from . import views

urlpatterns = [
    path('callback', views.callback, name='callback'),
    path('search/', views.search, name='search'),
    path('track/<str:track_id>/', views.track_detail, name='track_detail'),
    path('artist/<str:artist_name>/', views.artist_detail, name='artist_detail'),
    path('playlist/<str:playlist_id>/', views.playlist_detail, name='playlist_detail'),
    path('playlist/create', views.create_playlist, name='create_playlist'),
    path('playlist/add-track', views.add_to_playlist, name='add_to_playlist'),
    path('playlist/<str:playlist_id>/delete/', views.delete_playlist, name='delete_playlist'),
    path('playlist/<str:playlist_id>/delete-track/<str:track_id>/', views.delete_track, name='delete_track'),

    # Cart URLs
    path('cart/', views.view_cart, name='view_cart'),
    path('cart/add/<str:track_id>/', views.add_to_cart, name='add_to_cart'),
    path('cart/remove/<int:item_id>/', views.remove_from_cart, name='remove_from_cart'),

    # Order URLs
    path('order/place/', views.place_order, name='place_order'),
    path('order/history/', views.order_history, name='order_history'),

    # Test URL for mood playlist generation
    path('generate-mood-playlist/<str:mood_key>/', views.test_generate_mood_playlist_view, name='test_generate_mood_playlist'),

    # URL for mood/activity playlist generation and display
    path('mood-playlist-generator/', views.generate_and_display_mood_playlist, name='mood_playlist_generator'),
]
