from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from .models import Patient
from .views import _normalize_whatsapp_phone


class PatientStatusSignalTests(TestCase):
    def test_status_since_persists_when_saving_with_update_fields(self):
        patient = Patient.objects.create(
            full_name="Paciente Prueba",
            dni="12345678",
            phone="",
            email="",
            coverage="Cobertura",
            doctor="Medico",
            service="Servicio",
            planned_date=timezone.localdate(),
        )
        Patient.objects.filter(pk=patient.pk).update(status_since=None)
        patient.refresh_from_db()
        self.assertIsNone(patient.status_since)

        patient.status = Patient.STATUS_AUTORIZADO
        patient.save(update_fields=["status", "updated_at", "status_since", "solicitado_since"])
        patient.refresh_from_db()

        self.assertEqual(patient.status, Patient.STATUS_AUTORIZADO)
        self.assertIsNotNone(patient.status_since)
        self.assertIsNone(patient.solicitado_since)

    def test_bulk_update_fields_include_status_since_contract(self):
        patient = Patient.objects.create(
            full_name="Paciente Lote",
            dni="87654321",
            phone="",
            email="",
            coverage="Cobertura",
            doctor="Medico",
            service="Servicio",
            planned_date=timezone.localdate(),
        )
        user = User.objects.create_user("operador", password="secret", is_staff=True)
        self.client.force_login(user)

        response = self.client.post(
            "/bulk/change-status/",
            data='{"patient_ids": [%d], "new_status": "%s"}'
            % (patient.pk, Patient.STATUS_PENDIENTE_PRESTADOR),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        patient.refresh_from_db()
        self.assertEqual(patient.status, Patient.STATUS_PENDIENTE_PRESTADOR)
        self.assertIsNotNone(patient.status_since)
        self.assertIsNotNone(patient.solicitado_since)


class WhatsAppPhoneTests(TestCase):
    def test_normalize_argentina_mobile_phone(self):
        self.assertEqual(_normalize_whatsapp_phone("011 15 5592-2359"), "5491155922359")
        self.assertEqual(_normalize_whatsapp_phone("+54 9 11 5592-2359"), "5491155922359")
