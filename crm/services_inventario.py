# crm/services_inventario.py
from decimal import Decimal
from django.db.models import Sum
from django.utils import timezone

from .models import VentaItem, Venta


# =============================================================
# STOCKS INICIALES POR TIPO DE BOLSA
# Formato: (bolsas_8_lav, bolsas_20_lav, bolsas_8_carbon, bolsas_20_carbon, bolsas_20_talco)
# =============================================================
STOCK_INICIAL_8_LAV    = 1095 - 4   # bolsas 8kg lavanda (original)
STOCK_INICIAL_20_LAV   = 862 - 8    # bolsas 20kg lavanda (original)
STOCK_INICIAL_8_CARBON  = 999       # bolsas 8kg lavanda con carbón activado
STOCK_INICIAL_20_CARBON = 750       # bolsas 20kg lavanda con carbón activado
STOCK_INICIAL_20_TALCO  = 150       # bolsas 20kg talco de bebé con carbón activado

# =============================================================
# MAPA SKU -> (b8_lav, b20_lav, b8_carbon, b20_carbon, b20_talco)
#
# PRODUCTOS LAVANDA (sin carbón) — stock original
#   SKU 1  →  8kg lavanda          = 1 bolsa de 8kg lav
#   SKU 2  → 16kg lavanda          = 2 bolsas de 8kg lav
#   SKU 3  → 20kg lavanda          = 1 bolsa de 20kg lav
#   SKU 4  → 24kg lavanda          = 3 bolsas de 8kg lav
#   SKU 5  → 28kg lavanda          = 1 bolsa 8kg lav + 1 bolsa 20kg lav
#   SKU 6  → 32kg lavanda          = 4 bolsas de 8kg lav
#   SKU 7  → 40kg lavanda          = 2 bolsas de 20kg lav
#   SKU 8  → 40kg lav en bolsas 8  = 5 bolsas de 8kg lav
#
# PRODUCTOS LAVANDA CON CARBÓN ACTIVADO — stock nuevo
#   SKU 9  →  8kg carbón           = 1 bolsa de 8kg carbón
#   SKU 10 → 16kg carbón           = 2 bolsas de 8kg carbón
#   SKU 11 → 20kg carbón           = 1 bolsa de 20kg carbón
#   SKU 12 → 24kg carbón           = 3 bolsas de 8kg carbón
#   SKU 13 → 28kg carbón           = 1 bolsa 8kg carbón + 1 bolsa 20kg carbón
#   SKU 14 → 32kg carbón           = 4 bolsas de 8kg carbón
#   SKU 15 → 40kg carbón           = 2 bolsas de 20kg carbón
#   SKU 16 → 40kg carbón en bolsas 8 = 5 bolsas de 8kg carbón
#
# PRODUCTOS TALCO DE BEBÉ CON CARBÓN ACTIVADO — stock nuevo
#   SKU 17 → 20kg talco            = 1 bolsa de 20kg talco
#   SKU 18 → 40kg talco            = 2 bolsas de 20kg talco
# =============================================================
SKU_BOLSAS_MAP = {
    # SKU: (b8_lav, b20_lav, b8_carbon, b20_carbon, b20_talco)

    # --- Lavanda sin carbón ---
    "1":  (1, 0, 0, 0, 0),  # 8kg  lavanda
    "2":  (2, 0, 0, 0, 0),  # 16kg lavanda
    "3":  (0, 1, 0, 0, 0),  # 20kg lavanda
    "4":  (3, 0, 0, 0, 0),  # 24kg lavanda
    "5":  (1, 1, 0, 0, 0),  # 28kg lavanda (8+20)
    "6":  (4, 0, 0, 0, 0),  # 32kg lavanda
    "7":  (0, 2, 0, 0, 0),  # 40kg lavanda (2x20)
    "8":  (5, 0, 0, 0, 0),  # 40kg lavanda en bolsas de 8

    # --- Lavanda con carbón activado ---
    "9":  (0, 0, 1, 0, 0),  # 8kg  carbón
    "10": (0, 0, 2, 0, 0),  # 16kg carbón
    "11": (0, 0, 0, 1, 0),  # 20kg carbón
    "12": (0, 0, 3, 0, 0),  # 24kg carbón
    "13": (0, 0, 1, 1, 0),  # 28kg carbón (8+20)
    "14": (0, 0, 4, 0, 0),  # 32kg carbón
    "15": (0, 0, 0, 2, 0),  # 40kg carbón (2x20)
    "16": (0, 0, 5, 0, 0),  # 40kg carbón en bolsas de 8

    # --- Talco de bebé con carbón activado ---
    "17": (0, 0, 0, 0, 1),  # 20kg talco
    "18": (0, 0, 0, 0, 2),  # 40kg talco (2x20)
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

    total_8_lav    = 0
    total_20_lav   = 0
    total_8_carbon  = 0
    total_20_carbon = 0
    total_20_talco  = 0

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

        b8_lav, b20_lav, b8_c, b20_c, b20_t = SKU_BOLSAS_MAP[sku]

        c8_lav   = signo * unidades * b8_lav
        c20_lav  = signo * unidades * b20_lav
        c8_c     = signo * unidades * b8_c
        c20_c    = signo * unidades * b20_c
        c20_t    = signo * unidades * b20_t

        total_8_lav    += c8_lav
        total_20_lav   += c20_lav
        total_8_carbon  += c8_c
        total_20_carbon += c20_c
        total_20_talco  += c20_t

        detalle.append({
            "sku": sku,
            "nombre": nombre,
            "tipo_doc": tipo_doc,
            "unidades_sku": unidades * signo,
            # consumos por tipo
            "bolsas_8_lav":    c8_lav,
            "bolsas_20_lav":   c20_lav,
            "bolsas_8_carbon":  c8_c,
            "bolsas_20_carbon": c20_c,
            "bolsas_20_talco":  c20_t,
            # campos legacy para no romper templates existentes
            "bolsas_8":  c8_lav + c8_c,
            "bolsas_20": c20_lav + c20_c + c20_t,
        })

    detalle.sort(
        key=lambda x: (
            abs(x["bolsas_8_lav"]) + abs(x["bolsas_20_lav"]) +
            abs(x["bolsas_8_carbon"]) + abs(x["bolsas_20_carbon"]) +
            abs(x["bolsas_20_talco"])
        ),
        reverse=True
    )

    return {
        # --- Consumos por tipo ---
        "consumo_8_lav":    total_8_lav,
        "consumo_20_lav":   total_20_lav,
        "consumo_8_carbon":  total_8_carbon,
        "consumo_20_carbon": total_20_carbon,
        "consumo_20_talco":  total_20_talco,

        # --- Stocks iniciales ---
        "stock_inicial_8_lav":    STOCK_INICIAL_8_LAV,
        "stock_inicial_20_lav":   STOCK_INICIAL_20_LAV,
        "stock_inicial_8_carbon":  STOCK_INICIAL_8_CARBON,
        "stock_inicial_20_carbon": STOCK_INICIAL_20_CARBON,
        "stock_inicial_20_talco":  STOCK_INICIAL_20_TALCO,

        # --- Inventarios actuales (stock inicial - consumido) ---
        "inventario_8_lav":    STOCK_INICIAL_8_LAV    - total_8_lav,
        "inventario_20_lav":   STOCK_INICIAL_20_LAV   - total_20_lav,
        "inventario_8_carbon":  STOCK_INICIAL_8_CARBON  - total_8_carbon,
        "inventario_20_carbon": STOCK_INICIAL_20_CARBON - total_20_carbon,
        "inventario_20_talco":  STOCK_INICIAL_20_TALCO  - total_20_talco,

        # --- Campos legacy (suma total 8kg y 20kg) para no romper vistas existentes ---
        "consumo_8":  total_8_lav + total_8_carbon,
        "consumo_20": total_20_lav + total_20_carbon + total_20_talco,
        "inventario_8":  (STOCK_INICIAL_8_LAV - total_8_lav) + (STOCK_INICIAL_8_CARBON - total_8_carbon),
        "inventario_20": (STOCK_INICIAL_20_LAV - total_20_lav) + (STOCK_INICIAL_20_CARBON - total_20_carbon) + (STOCK_INICIAL_20_TALCO - total_20_talco),
        "stock_inicial_8":  STOCK_INICIAL_8_LAV + STOCK_INICIAL_8_CARBON,
        "stock_inicial_20": STOCK_INICIAL_20_LAV + STOCK_INICIAL_20_CARBON + STOCK_INICIAL_20_TALCO,

        "detalle": detalle,
        "skus_sin_mapa": sorted([s for s in skus_sin_mapa if s]),
    }