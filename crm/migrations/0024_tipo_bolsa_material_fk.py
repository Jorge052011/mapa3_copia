from django.db import migrations, models
import django.db.models.deletion


TABLAS = (
    "IngresoMaterial",
    "MovimientoBolsasVenta",
    "InventarioBolsasAjuste",
)
CODIGOS_VALIDOS = {
    "8_lav",
    "20_lav",
    "8_carbon",
    "20_carbon",
    "20_talco",
    "20_cafe",
}


def validar_codigos(apps, schema_editor):
    for nombre in TABLAS:
        Modelo = apps.get_model("crm", nombre)
        codigos = set(
            Modelo.objects.values_list("tipo_bolsa", flat=True).distinct()
        )
        invalidos = codigos - CODIGOS_VALIDOS
        if invalidos:
            raise RuntimeError(
                f"{nombre} contiene tipos de bolsa desconocidos: {sorted(invalidos)}. "
                "Migración detenida para no alterar datos."
            )
        if None in codigos or "" in codigos:
            raise RuntimeError(
                f"{nombre} contiene tipos de bolsa vacíos o nulos. "
                "Migración detenida para no alterar datos."
            )


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0023_material_productomaterial"),
    ]

    operations = [
        migrations.RunPython(validar_codigos, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="producto",
            name="peso_kg",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                editable=False,
                help_text="Peso calculado desde la receta de materiales.",
                max_digits=6,
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="ingresomaterial",
            name="tipo_bolsa",
            field=models.ForeignKey(
                db_column="tipo_bolsa",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="ingresos_material",
                to="crm.material",
                to_field="codigo",
                verbose_name="Tipo de bolsa",
            ),
        ),
        migrations.AlterField(
            model_name="inventariobolsasajuste",
            name="tipo_bolsa",
            field=models.ForeignKey(
                db_column="tipo_bolsa",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="ajustes_inventario",
                to="crm.material",
                to_field="codigo",
                verbose_name="Tipo de bolsa",
            ),
        ),
        migrations.AlterField(
            model_name="movimientobolsasventa",
            name="tipo_bolsa",
            field=models.ForeignKey(
                db_column="tipo_bolsa",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="movimientos_venta",
                to="crm.material",
                to_field="codigo",
                verbose_name="Tipo de bolsa",
            ),
        ),
    ]
