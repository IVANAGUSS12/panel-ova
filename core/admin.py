from django.contrib import admin
from .models import Patient, Attachment, AuditLog

@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'dni', 'coverage', 'doctor', 'service', 'planned_date', 'status')
    list_filter = ('status', 'coverage', 'service', 'doctor')
    search_fields = ('full_name', 'dni', 'coverage', 'doctor', 'service')

@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    list_display = ('patient', 'type', 'uploaded_at')
    list_filter = ('type',)

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('user', 'path', 'method', 'ip_address', 'created_at')
    list_filter = ('method', 'user')
    readonly_fields = ('user', 'path', 'method', 'ip_address', 'created_at')
