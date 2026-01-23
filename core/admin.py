from django.contrib import admin
from .models import Patient, Attachment, AuditLog, PatientHistory

@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'dni', 'coverage', 'doctor', 'service', 'planned_date', 'assigned_to', 'status')
    list_filter = ('status', 'coverage', 'service', 'doctor', 'assigned_to')
    search_fields = ('full_name', 'dni', 'coverage', 'doctor', 'service')

@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    list_display = ('patient', 'type', 'uploaded_at')
    list_filter = ('type',)

@admin.register(PatientHistory)
class PatientHistoryAdmin(admin.ModelAdmin):
    list_display = ('patient', 'action', 'user', 'field_name', 'created_at')
    list_filter = ('action', 'user', 'created_at')
    readonly_fields = ('patient', 'user', 'action', 'field_name', 'old_value', 'new_value', 'notes', 'created_at')
    search_fields = ('patient__full_name', 'patient__dni')

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('user', 'path', 'method', 'ip_address', 'created_at')
    list_filter = ('method', 'user')
    readonly_fields = ('user', 'path', 'method', 'ip_address', 'created_at')
