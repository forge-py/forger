"""Admin configuration for accounts."""

from django.contrib import admin
from .models import CustomUser

admin.site.register(CustomUser)
