"""
URL configuration for MeloMatch project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls, name="admin"),
    path('', include('core.urls')),
    path('users/', include('users.urls')),
    path('subscription/', include('subscription.urls')),
    path('music/', include('music.urls')),
    # API URLs
    # path('api/analytics/', include('services.analytics_service.urls')), # This was lost in reset, can be re-added later if needed
    path('api/v1/sync/', include('live_sync.urls')),
    path('api/v1/chat/', include('chat.urls')), # Added chat API URLs
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
