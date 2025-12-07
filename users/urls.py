from django.urls import path

from . import views

urlpatterns = [
    path('signup/', views.signup, name='signup'),
    path('login/', views.user_login, name='login'),
    path('logout/', views.user_logout, name='logout'),
    path('user_activities/', views.user_activities, name='user_activities'),
    path('settings/', views.settings, name='settings'),
    path('change-theme/', views.change_theme, name='change_theme'),

    # Profile URLs
    path('profile/', views.user_profile, name='my_profile'),  # For the logged-in user's own profile
    path('profile/<str:username>/', views.user_profile, name='user_profile'),  # For viewing other users' profiles

    # Follow/Unfollow URLs
    path('follow/<str:username_to_follow>/', views.follow_user, name='follow_user'),
    path('unfollow/<str:username_to_unfollow>/', views.unfollow_user, name='unfollow_user'),

    # List Followers/Following URLs
    path('profile/<str:username>/followers/', views.list_followers, name='list_followers'),
    path('profile/<str:username>/following/', views.list_following, name='list_following'),
]
