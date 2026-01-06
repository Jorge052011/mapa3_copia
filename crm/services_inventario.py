# crm/services_inventario.py
from decimal import Decimal
from django.db.models import Sum
from django.utils import timezone

from .models import VentaItem, Venta

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
    """
    Retorna consumo de bolsas 8kg y 20kg según ventas reales (con items),
    descontando Nota de Crédito (si existen items cargados en la NC).

    Output:
    {
      "bolsas_8": int,
      "bolsas_20": int,
      "detalle": [
          {"sku": "...", "nombre": "...", "unidades_sku": 3, "bolsas_8": 6, "bolsas_20": 0},
          ...
      ],
      "skus_sin_mapa": ["..."]
    }
    """
    if desde is None:
        hoy = timezone.localdate()
        desde = hoy.replace(day=1) - timezone.timedelta(days=180)
    if hasta is None:
        hasta = timezone.localdate()

    # OJO: Venta.fecha es DateTimeField, así que filtramos por rango datetime inclusivo
    # usando fecha__date para comparar con DateField (localdate).
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

        # Signo: Nota de crédito resta consumo (devuelve stock / revierte venta)
        signo = -1 if tipo_doc == Venta.TipoDocumento.NOTA_CREDITO else 1

        if sku not in SKU_BOLSAS_MAP:
            if unidades:
                skus_sin_mapa.add(sku)
            # si no está mapeado, lo dejamos fuera del conteo
            # (así no inventamos bolsas)
            continue

        b8, b20 = SKU_BOLSAS_MAP[sku]
        c8 = signo * unidades * int(b8)
        c20 = signo * unidades * int(b20)

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

    # Ordena detalle por mayor consumo absoluto de bolsas (útil para revisar)
    detalle.sort(key=lambda x: (abs(x["bolsas_8"]) + abs(x["bolsas_20"])), reverse=True)

    return {
        "bolsas_8": total_8,
        "bolsas_20": total_20,
        "detalle": detalle,
        "skus_sin_mapa": sorted([s for s in skus_sin_mapa if s]),
    }
