from datetime import date

from django.db import migrations

SALDO_INICIAL = {
    "8_lav": 1089,
    "20_lav": 1403,
    "8_carbon": 985,
    "20_carbon": 1142,
    "20_talco": 447,
    "20_cafe": 50,
}

FECHA_SALDO = date(2025, 7, 31)
OBSERVACION = "Saldo inicial histórico migrado desde constantes"


def crear_saldos_iniciales(apps, schema_editor):
    IngresoMaterial = apps.get_model("crm", "IngresoMaterial")

    for tipo_bolsa, cantidad in SALDO_INICIAL.items():
        IngresoMaterial.objects.get_or_create(
            tipo_bolsa=tipo_bolsa,
            importacion=None,
            defaults={
                "fecha": FECHA_SALDO,
                "cantidad": cantidad,
                "observacion": OBSERVACION,
            },
        )


def eliminar_saldos_iniciales(apps, schema_editor):
    IngresoMaterial = apps.get_model("crm", "IngresoMaterial")

    IngresoMaterial.objects.filter(
        tipo_bolsa__in=SALDO_INICIAL.keys(),
        importacion=None,
        fecha=FECHA_SALDO,
        observacion=OBSERVACION,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0019_ingresomaterial"),
    ]

    operations = [
        migrations.RunPython(
            crear_saldos_iniciales,
            eliminar_saldos_iniciales,
        ),
    ]
