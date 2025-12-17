from django.db import models
from django.contrib.auth.models import User

class UserProfile(models.Model):
    ROLE_ADMIN = 'ADMIN'
    ROLE_SECTOR = 'SECTOR'
    ROLE_READONLY = 'READONLY'

    ROLE_CHOICES = [
        (ROLE_ADMIN, 'Administración'),
        (ROLE_SECTOR, 'Sector'),
        (ROLE_READONLY, 'Solo lectura'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_SECTOR)

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"
