# crm/services_inventario.py
from decimal import Decimal
from django.db.models import Sum
from django.utils import timezone

from .models import VentaItem, Venta


# inventario original las mermas se deben restar del original
STOCK_INICIAL_8 = 1095-4
STOCK_INICIAL_20 = 862-8
# ---------------------------------------------------------
# MAPA SKU -> (bolsas_8kg, bolsas_20kg)
# AJUSTA las llaves para que calcen con tu Producto.sku real
# ---------------------------------------------------------
SKU_BOLSAS_MAP = {
    "1":  (1, 0),  # 1 bolsa de 8
    "2":  (2, 0),  # 2 bolsas de 8
    "3":  (0, 1),  # 1 bolsa de 20
    "4":  (3, 0),  # 3 bolsas de 8
    "5":  (1, 1),  # 1 bolsa de 8 + 1 de 20
    "6":  (4, 0),  # 4 bolsas de 8
    "7":  (0, 2),  # 2 bolsas de 20
    "8":  (5, 0),  # 5 bolsas de 8
}

def consumo_bolsas(desde=None, hasta=None):
    if desde is None:
        hoy = timezone.localdate()
        desde = hoy.replace(day=1) - timezone.timedelta(days=180)
    if hasta is None:
        hasta = timezone.localdate()

    items_qs = (
        VentaItem.objects
        .select_related("producto", "venta")
        .filter(venta__fecha__date__gte=desde, venta__fecha__date__lte=hasta)
        .values(
            "producto__sku",
            "producto__nombre",
            "venta__tipo_documento",
        )
        .annotate(unidades_sku=Sum("cantidad"))
    )

    total_8 = 0
    total_20 = 0
    detalle = []
    skus_sin_mapa = set()

    for r in items_qs:
        sku = (r["producto__sku"] or "").strip()
        nombre = r["producto__nombre"] or ""
        tipo_doc = r["venta__tipo_documento"]
        unidades = int(r["unidades_sku"] or 0)

        signo = -1 if tipo_doc == Venta.TipoDocumento.NOTA_CREDITO else 1

        if sku not in SKU_BOLSAS_MAP:
            if unidades:
                skus_sin_mapa.add(sku)
            continue

        b8, b20 = SKU_BOLSAS_MAP[sku]
        c8 = signo * unidades * b8
        c20 = signo * unidades * b20

        total_8 += c8
        total_20 += c20

        detalle.append({
            "sku": sku,
            "nombre": nombre,
            "tipo_doc": tipo_doc,
            "unidades_sku": unidades * signo,
            "bolsas_8": c8,
            "bolsas_20": c20,
        })

    detalle.sort(
        key=lambda x: abs(x["bolsas_8"]) + abs(x["bolsas_20"]),
        reverse=True
    )

    inventario_8 = STOCK_INICIAL_8 - total_8
    inventario_20 = STOCK_INICIAL_20 - total_20

    return {
        # consumo
        "consumo_8": total_8,
        "consumo_20": total_20,

        # inventario
        "stock_inicial_8": STOCK_INICIAL_8,
        "stock_inicial_20": STOCK_INICIAL_20,
        "inventario_8": STOCK_INICIAL_8 - total_8,
        "inventario_20": STOCK_INICIAL_20 - total_20,

        "detalle": detalle,
        "skus_sin_mapa": sorted([s for s in skus_sin_mapa if s]),

        "inventario_8": inventario_8,
        "inventario_20": inventario_20,
        "stock_inicial_8": STOCK_INICIAL_8,
        "stock_inicial_20": STOCK_INICIAL_20,



    }
