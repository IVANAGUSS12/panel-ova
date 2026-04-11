from django.contrib import admin
from .models import (
    Attachment,
    AuditLog,
    Patient,
    PatientHistory,
    SavedFilter,
)

@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'dni', 'coverage', 'doctor', 'service', 'planned_date', 'status', 'assigned_to')
    list_filter = ('status', 'coverage', 'service', 'doctor', 'assigned_to')
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

@admin.register(PatientHistory)
class PatientHistoryAdmin(admin.ModelAdmin):
    list_display = ('patient', 'action', 'field_name', 'user', 'timestamp')
    list_filter = ('action', 'timestamp', 'user')
    search_fields = ('patient__full_name', 'patient__tracking_id', 'field_name')
    readonly_fields = ('patient', 'user', 'action', 'field_name', 'old_value', 'new_value', 'timestamp')
    date_hierarchy = 'timestamp'

@admin.register(SavedFilter)
class SavedFilterAdmin(admin.ModelAdmin):
    list_display = ('name', 'user', 'is_default', 'created_at')
    list_filter = ('is_default', 'user')
    search_fields = ('name', 'user__username')
