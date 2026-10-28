from datetime import date

from django.db import migrations

FECHA_INICIO = date(2026, 10, 28)
IMPORTACION_INICIAL_ID = 5
INGRESOS_ESPERADOS = {
    "20_lav": 195,
    "20_carbon": 611,
    "20_talco": 299,
    "20_cafe": 195,
}
NOTAS_CREDITO_FISICAS = (196, 321)
NOTA_CONTROL = (
    "Inicio del control operativo por contenedor. "
    "La importación #5 reutiliza sus ingresos existentes."
)


def configurar_trazabilidad(apps, schema_editor):
    ControlTrazabilidad = apps.get_model("crm", "ControlTrazabilidad")
    Importacion = apps.get_model("crm", "Importacion")
    IngresoMaterial = apps.get_model("crm", "IngresoMaterial")
    Venta = apps.get_model("crm", "Venta")

    ControlTrazabilidad.objects.update_or_create(
        pk=1,
        defaults={
            "fecha_inicio": FECHA_INICIO,
            "activo": True,
            "nota": NOTA_CONTROL,
        },
    )

    # Todos los ingresos existentes quedan fuera del control operativo...
    IngresoMaterial.objects.update(control_contenedor=False)

    # ...salvo los cuatro ingresos ya registrados de la importación #5.
    importacion = (
        Importacion.objects.filter(pk=IMPORTACION_INICIAL_ID).first()
        or Importacion.objects.filter(
            fecha=FECHA_INICIO,
            descripcion__iexact="cuarta",
        ).first()
    )

    if importacion is not None:
        ingresos = list(
            IngresoMaterial.objects.filter(importacion_id=importacion.pk)
            .values_list("tipo_bolsa", "cantidad")
        )
        if ingresos:
            reales = {}
            for tipo_bolsa, cantidad in ingresos:
                reales[tipo_bolsa] = reales.get(tipo_bolsa, 0) + cantidad

            if reales != INGRESOS_ESPERADOS:
                raise RuntimeError(
                    "Los ingresos de la importación #5 no coinciden con lo esperado. "
                    f"Esperado: {INGRESOS_ESPERADOS}. Encontrado: {reales}. "
                    "Migración detenida para no alterar datos."
                )

            IngresoMaterial.objects.filter(
                importacion_id=importacion.pk,
                tipo_bolsa__in=INGRESOS_ESPERADOS.keys(),
            ).update(control_contenedor=True)

            total_control = IngresoMaterial.objects.filter(
                importacion_id=importacion.pk,
                control_contenedor=True,
            ).count()
            if total_control != len(INGRESOS_ESPERADOS):
                raise RuntimeError(
                    "La importación #5 no quedó con exactamente cuatro ingresos "
                    f"operativos; se encontraron {total_control}. Migración detenida."
                )

    # Las notas de crédito históricas se consideran devoluciones físicas para
    # preservar el inventario general que ya las descontaba. No se crean
    # asignaciones ni movimientos por contenedor para ellas.
    Venta.objects.filter(
        pk__in=NOTAS_CREDITO_FISICAS,
        tipo_documento="nota_credito",
    ).update(devolucion_fisica=True)


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0021_trazabilidad_contenedores"),
    ]

    operations = [
        migrations.RunPython(
            configurar_trazabilidad,
            migrations.RunPython.noop,
        ),
    ]
