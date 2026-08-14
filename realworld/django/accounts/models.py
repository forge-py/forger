"""Accounts models."""

from django.db import models
from django.contrib.auth.models import AbstractUser


class CustomUser(AbstractUser):
    bio = models.TextField(blank=True)
    website = models.URLField(blank=True)

    class Meta:
        db_table = "accounts_user"

    def __str__(self) -> str:
        return self.username
