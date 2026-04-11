from django.db import models
from django.contrib.auth.models import User

# NOTA: UserProfile no se usa actualmente en la aplicación.
# Si necesitas roles/permisos, puedes usar is_staff y grupos de Django.
# Descomenta y crea migración si necesitas implementar roles personalizados.

# class UserProfile(models.Model):
#     ROLE_ADMIN = 'ADMIN'
#     ROLE_SECTOR = 'SECTOR'
#     ROLE_READONLY = 'READONLY'
#
#     ROLE_CHOICES = [
#         (ROLE_ADMIN, 'Administración'),
#         (ROLE_SECTOR, 'Sector'),
#         (ROLE_READONLY, 'Solo lectura'),
#     ]
#
#     user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
#     role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_SECTOR)
#
#     def __str__(self):
#         return f"{self.user.username} ({self.get_role_display()})"
