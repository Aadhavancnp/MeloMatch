from django.contrib import admin

from users.models import CustomUser, Follow # Added Follow model

# Register your models here.
admin.site.register(CustomUser)
admin.site.register(Follow)
