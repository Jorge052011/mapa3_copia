import django.core.validators
from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion


MATERIALES = (
    ("8_lav", "Lavanda 8 kg", Decimal("8.00")),
    ("20_lav", "Lavanda 20 kg", Decimal("20.00")),
    ("8_carbon", "Carbón 8 kg", Decimal("8.00")),
    ("20_carbon", "Carbón 20 kg", Decimal("20.00")),
    ("20_talco", "Talco 20 kg", Decimal("20.00")),
    ("20_cafe", "Café 20 kg", Decimal("20.00")),
)

RECETAS = {
    "1": (("8_lav", 1),),
    "2": (("8_lav", 2),),
    "3": (("20_lav", 1),),
    "4": (("8_lav", 3),),
    "5": (("8_lav", 1), ("20_lav", 1)),
    "6": (("8_lav", 4),),
    "7": (("20_lav", 2),),
    "8": (("8_lav", 5),),
    "9": (("8_carbon", 1),),
    "10": (("8_carbon", 2),),
    "11": (("20_carbon", 1),),
    "12": (("8_carbon", 3),),
    "13": (("8_carbon", 1), ("20_carbon", 1)),
    "14": (("8_carbon", 4),),
    "15": (("20_carbon", 2),),
    "16": (("8_carbon", 5),),
    "17": (("20_talco", 1),),
    "18": (("20_talco", 2),),
    "19": (("20_cafe", 1),),
    "20": (("20_cafe", 2),),
}


def sembrar_catalogo(apps, schema_editor):
    Material = apps.get_model("crm", "Material")
    Producto = apps.get_model("crm", "Producto")
    ProductoMaterial = apps.get_model("crm", "ProductoMaterial")

    pesos = {}
    for codigo, nombre, peso in MATERIALES:
        material, _ = Material.objects.update_or_create(
            codigo=codigo,
            defaults={"nombre": nombre, "peso_kg": peso, "activo": True},
        )
        pesos[codigo] = material.peso_kg

    if Producto.objects.exists():
        for sku, componentes in RECETAS.items():
            producto = Producto.objects.filter(sku=sku).first()
            if producto is None:
                raise RuntimeError(
                    f"No existe el producto SKU {sku}; migración detenida."
                )
            for codigo, cantidad in componentes:
                ProductoMaterial.objects.update_or_create(
                    producto=producto,
                    material_id=codigo,
                    defaults={"cantidad": cantidad},
                )

        for producto in Producto.objects.all():
            componentes = list(
                ProductoMaterial.objects.filter(producto=producto).values_list(
                    "material_id", "cantidad"
                )
            )
            if componentes:
                total = sum(
                    (pesos[codigo] * cantidad for codigo, cantidad in componentes),
                    Decimal("0"),
                ).quantize(Decimal("0.01"))
            else:
                total = None
            if producto.peso_kg != total:
                raise RuntimeError(
                    f"El peso del producto {producto.sku} no coincide con su receta. "
                    f"Original: {producto.peso_kg}; recalculado: {total}. "
                    "Migración detenida para no alterar datos."
                )

    if schema_editor.connection.vendor == "sqlite":
        schema_editor.execute(
            """
            CREATE TRIGGER crm_material_codigo_inmutable
            BEFORE UPDATE OF codigo ON crm_material
            FOR EACH ROW WHEN OLD.codigo <> NEW.codigo
            BEGIN
                SELECT RAISE(ABORT, 'El código de un material no se puede modificar');
            END;
            """
        )


def eliminar_trigger(apps, schema_editor):
    if schema_editor.connection.vendor == "sqlite":
        schema_editor.execute("DROP TRIGGER IF EXISTS crm_material_codigo_inmutable")


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0022_configurar_trazabilidad_inicial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Material",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "codigo",
                    models.CharField(max_length=20, unique=True, verbose_name="Código"),
                ),
                ("nombre", models.CharField(max_length=100, verbose_name="Nombre")),
                (
                    "peso_kg",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=6,
                        verbose_name="Peso por bolsa (kg)",
                    ),
                ),
                ("activo", models.BooleanField(default=True, verbose_name="Activo")),
            ],
            options={
                "verbose_name": "Material",
                "verbose_name_plural": "Materiales",
                "ordering": ["nombre", "codigo"],
            },
        ),
        migrations.CreateModel(
            name="ProductoMaterial",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "cantidad",
                    models.PositiveIntegerField(
                        help_text="Cantidad de bolsas de este material por producto.",
                        validators=[django.core.validators.MinValueValidator(1)],
                    ),
                ),
                (
                    "material",
                    models.ForeignKey(
                        db_column="material_codigo",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="productos",
                        to="crm.material",
                        to_field="codigo",
                    ),
                ),
                (
                    "producto",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="componentes",
                        to="crm.producto",
                    ),
                ),
            ],
            options={
                "verbose_name": "Material del producto",
                "verbose_name_plural": "Materiales del producto",
                "ordering": ["producto", "material"],
            },
        ),
        migrations.AddConstraint(
            model_name="material",
            constraint=models.CheckConstraint(
                check=models.Q(("peso_kg__gt", 0)),
                name="material_peso_kg_positivo",
            ),
        ),
        migrations.AddConstraint(
            model_name="productomaterial",
            constraint=models.UniqueConstraint(
                fields=("producto", "material"),
                name="uniq_producto_material",
            ),
        ),
        migrations.AddConstraint(
            model_name="productomaterial",
            constraint=models.CheckConstraint(
                check=models.Q(("cantidad__gte", 1)),
                name="pm_cantidad_min_1",
            ),
        ),
        migrations.RunPython(sembrar_catalogo, eliminar_trigger),
    ]
