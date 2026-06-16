from django.db.models import Sum
from django.utils import timezone

from .models import VentaItem, Venta


# =============================================================
# STOCKS INICIALES POR TIPO DE BOLSA
# Formato:
# (
#   bolsas_8_lav,
#   bolsas_20_lav,
#   bolsas_8_carbon,
#   bolsas_20_carbon,
#   bolsas_20_talco,
#   bolsas_20_cafe,
# )
# =============================================================
STOCK_INICIAL_8_LAV = 1095 - 4
STOCK_INICIAL_20_LAV = (550 + 862) - 8
STOCK_INICIAL_8_CARBON = 999 - 6
STOCK_INICIAL_20_CARBON = 400 + 750
STOCK_INICIAL_20_TALCO = (300 + 150) - 1

# Reemplaza 0 por la cantidad real de bolsas de 20 kg aroma café recibidas.
STOCK_INICIAL_20_CAFE = 0


# =============================================================
# MAPA SKU -> (
#   b8_lav,
#   b20_lav,
#   b8_carbon,
#   b20_carbon,
#   b20_talco,
#   b20_cafe,
# )
#
# PRODUCTOS LAVANDA (sin carbón)
#   SKU 1  →  8 kg lavanda              = 1 bolsa de 8 kg lavanda
#   SKU 2  → 16 kg lavanda              = 2 bolsas de 8 kg lavanda
#   SKU 3  → 20 kg lavanda              = 1 bolsa de 20 kg lavanda
#   SKU 4  → 24 kg lavanda              = 3 bolsas de 8 kg lavanda
#   SKU 5  → 28 kg lavanda              = 1 bolsa de 8 kg + 1 bolsa de 20 kg
#   SKU 6  → 32 kg lavanda              = 4 bolsas de 8 kg lavanda
#   SKU 7  → 40 kg lavanda              = 2 bolsas de 20 kg lavanda
#   SKU 8  → 40 kg lavanda en bolsas 8  = 5 bolsas de 8 kg lavanda
#
# PRODUCTOS LAVANDA CON CARBÓN ACTIVADO
#   SKU 9  →  8 kg carbón               = 1 bolsa de 8 kg carbón
#   SKU 10 → 16 kg carbón               = 2 bolsas de 8 kg carbón
#   SKU 11 → 20 kg carbón               = 1 bolsa de 20 kg carbón
#   SKU 12 → 24 kg carbón               = 3 bolsas de 8 kg carbón
#   SKU 13 → 28 kg carbón               = 1 bolsa de 8 kg + 1 bolsa de 20 kg
#   SKU 14 → 32 kg carbón               = 4 bolsas de 8 kg carbón
#   SKU 15 → 40 kg carbón               = 2 bolsas de 20 kg carbón
#   SKU 16 → 40 kg carbón en bolsas 8   = 5 bolsas de 8 kg carbón
#
# PRODUCTOS TALCO DE BEBÉ CON CARBÓN ACTIVADO
#   SKU 17 → 20 kg talco                = 1 bolsa de 20 kg talco
#   SKU 18 → 40 kg talco                = 2 bolsas de 20 kg talco
#
# PRODUCTOS CAFÉ CON CARBÓN ACTIVADO
#   SKU 19 → 20 kg café                 = 1 bolsa de 20 kg café
#   SKU 20 → 40 kg café                 = 2 bolsas de 20 kg café
# =============================================================
SKU_BOLSAS_MAP = {
    # --- Lavanda sin carbón ---
    "1":  (1, 0, 0, 0, 0, 0),
    "2":  (2, 0, 0, 0, 0, 0),
    "3":  (0, 1, 0, 0, 0, 0),
    "4":  (3, 0, 0, 0, 0, 0),
    "5":  (1, 1, 0, 0, 0, 0),
    "6":  (4, 0, 0, 0, 0, 0),
    "7":  (0, 2, 0, 0, 0, 0),
    "8":  (5, 0, 0, 0, 0, 0),

    # --- Lavanda con carbón activado ---
    "9":  (0, 0, 1, 0, 0, 0),
    "10": (0, 0, 2, 0, 0, 0),
    "11": (0, 0, 0, 1, 0, 0),
    "12": (0, 0, 3, 0, 0, 0),
    "13": (0, 0, 1, 1, 0, 0),
    "14": (0, 0, 4, 0, 0, 0),
    "15": (0, 0, 0, 2, 0, 0),
    "16": (0, 0, 5, 0, 0, 0),

    # --- Talco de bebé con carbón activado ---
    "17": (0, 0, 0, 0, 1, 0),
    "18": (0, 0, 0, 0, 2, 0),

    # --- Café con carbón activado ---
    "19": (0, 0, 0, 0, 0, 1),
    "20": (0, 0, 0, 0, 0, 2),
}


def consumo_bolsas(desde=None, hasta=None):
    if desde is None:
        desde = timezone.datetime(2025, 8, 1).date()

    if hasta is None:
        hasta = timezone.localdate()

    items_qs = (
        VentaItem.objects
        .select_related("producto", "venta")
        .filter(
            venta__fecha__date__gte=desde,
            venta__fecha__date__lte=hasta,
        )
        .values(
            "producto__sku",
            "producto__nombre",
            "venta__tipo_documento",
        )
        .annotate(unidades_sku=Sum("cantidad"))
    )

    total_8_lav = 0
    total_20_lav = 0
    total_8_carbon = 0
    total_20_carbon = 0
    total_20_talco = 0
    total_20_cafe = 0

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

        (
            b8_lav,
            b20_lav,
            b8_c,
            b20_c,
            b20_t,
            b20_cafe,
        ) = SKU_BOLSAS_MAP[sku]

        c8_lav = signo * unidades * b8_lav
        c20_lav = signo * unidades * b20_lav
        c8_c = signo * unidades * b8_c
        c20_c = signo * unidades * b20_c
        c20_t = signo * unidades * b20_t
        c20_cafe = signo * unidades * b20_cafe

        total_8_lav += c8_lav
        total_20_lav += c20_lav
        total_8_carbon += c8_c
        total_20_carbon += c20_c
        total_20_talco += c20_t
        total_20_cafe += c20_cafe

        detalle.append({
            "sku": sku,
            "nombre": nombre,
            "tipo_doc": tipo_doc,
            "unidades_sku": unidades * signo,

            "bolsas_8_lav": c8_lav,
            "bolsas_20_lav": c20_lav,
            "bolsas_8_carbon": c8_c,
            "bolsas_20_carbon": c20_c,
            "bolsas_20_talco": c20_t,
            "bolsas_20_cafe": c20_cafe,

            # Campos legacy para mantener compatibilidad.
            "bolsas_8": c8_lav + c8_c,
            "bolsas_20": c20_lav + c20_c + c20_t + c20_cafe,
        })

    detalle.sort(
        key=lambda x: (
            abs(x["bolsas_8_lav"])
            + abs(x["bolsas_20_lav"])
            + abs(x["bolsas_8_carbon"])
            + abs(x["bolsas_20_carbon"])
            + abs(x["bolsas_20_talco"])
            + abs(x["bolsas_20_cafe"])
        ),
        reverse=True,
    )

    return {
        # --- Consumos por tipo ---
        "consumo_8_lav": total_8_lav,
        "consumo_20_lav": total_20_lav,
        "consumo_8_carbon": total_8_carbon,
        "consumo_20_carbon": total_20_carbon,
        "consumo_20_talco": total_20_talco,
        "consumo_20_cafe": total_20_cafe,

        # --- Stocks iniciales ---
        "stock_inicial_8_lav": STOCK_INICIAL_8_LAV,
        "stock_inicial_20_lav": STOCK_INICIAL_20_LAV,
        "stock_inicial_8_carbon": STOCK_INICIAL_8_CARBON,
        "stock_inicial_20_carbon": STOCK_INICIAL_20_CARBON,
        "stock_inicial_20_talco": STOCK_INICIAL_20_TALCO,
        "stock_inicial_20_cafe": STOCK_INICIAL_20_CAFE,

        # --- Inventarios actuales ---
        "inventario_8_lav": STOCK_INICIAL_8_LAV - total_8_lav,
        "inventario_20_lav": STOCK_INICIAL_20_LAV - total_20_lav,
        "inventario_8_carbon": STOCK_INICIAL_8_CARBON - total_8_carbon,
        "inventario_20_carbon": STOCK_INICIAL_20_CARBON - total_20_carbon,
        "inventario_20_talco": STOCK_INICIAL_20_TALCO - total_20_talco,
        "inventario_20_cafe": STOCK_INICIAL_20_CAFE - total_20_cafe,

        # --- Campos legacy ---
        "consumo_8": total_8_lav + total_8_carbon,
        "consumo_20": (
            total_20_lav
            + total_20_carbon
            + total_20_talco
            + total_20_cafe
        ),
        "inventario_8": (
            STOCK_INICIAL_8_LAV - total_8_lav
        ) + (
            STOCK_INICIAL_8_CARBON - total_8_carbon
        ),
        "inventario_20": (
            STOCK_INICIAL_20_LAV - total_20_lav
        ) + (
            STOCK_INICIAL_20_CARBON - total_20_carbon
        ) + (
            STOCK_INICIAL_20_TALCO - total_20_talco
        ) + (
            STOCK_INICIAL_20_CAFE - total_20_cafe
        ),
        "stock_inicial_8": (
            STOCK_INICIAL_8_LAV
            + STOCK_INICIAL_8_CARBON
        ),
        "stock_inicial_20": (
            STOCK_INICIAL_20_LAV
            + STOCK_INICIAL_20_CARBON
            + STOCK_INICIAL_20_TALCO
            + STOCK_INICIAL_20_CAFE
        ),

        "detalle": detalle,
        "skus_sin_mapa": sorted(
            [sku for sku in skus_sin_mapa if sku]
        ),
    }
