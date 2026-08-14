"""URL routing for accounts app."""

from django.urls import path
from . import views

app_name = "accounts"

urlpatterns = [
    path("register/", views.register, name="register"),
    path("profile/", views.profile, name="profile"),
    path("logout/", views.user_logout, name="logout"),
]
