from django import forms
from .models import Patient, Attachment


class QRPatientForm(forms.ModelForm):
    class Meta:
        model = Patient
        fields = [
            'full_name', 'dni', 'phone', 'email',
            'coverage', 'doctor', 'service', 'planned_date',
            'external_observations',
        ]
        widgets = {
            'planned_date': forms.DateInput(attrs={'type': 'date'}),
            'external_observations': forms.Textarea(attrs={'rows': 3}),
        }


class AttachmentForm(forms.ModelForm):
    class Meta:
        model = Attachment
        fields = ['type', 'file']


class PatientFilterForm(forms.Form):
    q = forms.CharField(required=False, label='Buscar')

    status = forms.ChoiceField(
        required=False,
        choices=[('', 'Todos')] + Patient.STATUS_CHOICES,
        label='Estado'
    )

    # Se completan dinámicamente
    coverage = forms.ChoiceField(required=False, label='Cobertura', choices=[])
    doctor = forms.ChoiceField(required=False, label='Médico', choices=[])
    assigned_to = forms.ChoiceField(required=False, label='Usuario asignado', choices=[])

    service = forms.CharField(required=False, label='Servicio')
    
    # Filtros especiales
    urgent_only = forms.BooleanField(required=False, label='Solo urgentes (≤2 días)')
    missing_docs = forms.BooleanField(required=False, label='Con documentos faltantes')

    # Fechas de cirugía
    date_from = forms.DateField(
        required=False,
        label='Fecha cirugía desde',
        widget=forms.DateInput(attrs={'type': 'date'})
    )
    date_to = forms.DateField(
        required=False,
        label='Fecha cirugía hasta',
        widget=forms.DateInput(attrs={'type': 'date'})
    )

    # Fechas de carga
    created_from = forms.DateField(
        required=False,
        label='Cargado desde',
        widget=forms.DateInput(attrs={'type': 'date'})
    )
    created_to = forms.DateField(
        required=False,
        label='Cargado hasta',
        widget=forms.DateInput(attrs={'type': 'date'})
    )

    # 🔥 AQUI ESTÁ EL NUEVO SELECTOR DE ORDEN
    order_by = forms.ChoiceField(
        required=False,
        label='Ordenar por',
        choices=[
            ("", "---------"),
            ("planned_date_asc", "Fecha de cirugía (ascendente)"),
            ("planned_date_desc", "Fecha de cirugía (descendente)"),
            ("created_at_asc", "Fecha de carga (ascendente)"),
            ("created_at_desc", "Fecha de carga (descendente)"),
        ]
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Coberturas en DB
        coverages = (
            Patient.objects
            .exclude(coverage__isnull=True)
            .exclude(coverage__exact="")
            .values_list('coverage', flat=True)
            .distinct()
            .order_by('coverage')
        )
        self.fields['coverage'].choices = [('', 'Todas')] + [
            (c, c) for c in coverages
        ]

        # Médicos en DB
        doctors = (
            Patient.objects
            .exclude(doctor__isnull=True)
            .exclude(doctor__exact="")
            .values_list('doctor', flat=True)
            .distinct()
            .order_by('doctor')
        )
        self.fields['doctor'].choices = [('', 'Todos')] + [
            (d, d) for d in doctors
        ]
        
        # Usuarios asignados
        from django.contrib.auth.models import User
        users = User.objects.filter(is_active=True).order_by('username')
        self.fields['assigned_to'].choices = [('', 'Todos'), ('unassigned', 'Sin asignar')] + [
            (u.id, u.username) for u in users
        ]


class ReprogramForm(forms.ModelForm):
    reprogram_reason = forms.CharField(
        required=True,
        widget=forms.Textarea(attrs={'rows': 3}),
        label='Motivo de reprogramación'
    )

    class Meta:
        model = Patient
        fields = ['planned_date']
        widgets = {
            'planned_date': forms.DateInput(attrs={'type': 'date'}),
        }
