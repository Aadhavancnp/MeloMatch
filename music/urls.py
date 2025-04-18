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

]
