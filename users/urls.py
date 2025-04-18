from django.urls import path
from . import views

urlpatterns = [
    path('signup/', views.signup, name='signup'),
    path('login/', views.user_login, name='login'),
    path('logout/', views.user_logout, name='logout'),
    path('user_activities/', views.user_activities, name='user_activities'),
    path('settings/', views.settings, name='settings'),
    path('change-theme/', views.change_theme, name='change_theme'),
    path('profile/', views.profile, name='profile'),
]
