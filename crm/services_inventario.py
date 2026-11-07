from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from .models import (
    IngresoMaterial,
    InventarioBolsasAjuste,
    Material,
    ProductoMaterial,
    Venta,
    VentaItem,
)


def materiales_catalogo():
    return list(Material.objects.all().order_by("peso_kg", "nombre", "codigo"))


def _materiales_por_peso(peso):
    return [
        material
        for material in Material.objects.all()
        if material.peso_kg == Decimal(peso)
    ]


def stock_inicial_por_tipo():
    """Stock inicial por material, calculado desde IngresoMaterial."""
    totales = {
        fila["tipo_bolsa__codigo"]: fila["total"] or 0
        for fila in (
            IngresoMaterial.objects
            .values("tipo_bolsa__codigo")
            .annotate(total=Sum("cantidad"))
        )
    }
    return {
        material.codigo: totales.get(material.codigo, 0)
        for material in Material.objects.all()
    }


def _recetas_por_sku():
    recetas = defaultdict(list)
    for componente in (
        ProductoMaterial.objects
        .select_related("producto", "material")
        .order_by("producto__sku", "material__nombre")
    ):
        recetas[(componente.producto.sku or "").strip()].append(
            (componente.material.codigo, componente.cantidad)
        )
    return recetas


def consumo_bolsas(desde=None, hasta=None):
    stock_inicial = stock_inicial_por_tipo()
    materiales = materiales_catalogo()
    recetas = _recetas_por_sku()

    if desde is None:
        desde = timezone.datetime(2025, 8, 1).date()

    filtros = {"venta__fecha__date__gte": desde}
    if hasta is not None:
        filtros["venta__fecha__date__lte"] = hasta

    items_qs = (
        VentaItem.objects
        .select_related("producto", "venta")
        .filter(**filtros)
        .values(
            "producto__sku",
            "producto__nombre",
            "venta__tipo_documento",
            "venta__devolucion_fisica",
        )
        .annotate(unidades_sku=Sum("cantidad"))
    )

    totales = defaultdict(int)
    detalle = []
    skus_sin_receta = set()

    for r in items_qs:
        sku = (r["producto__sku"] or "").strip()
        nombre = r["producto__nombre"] or ""
        tipo_doc = r["venta__tipo_documento"]
        devolucion_fisica = r["venta__devolucion_fisica"]
        unidades = int(r["unidades_sku"] or 0)

        # Una nota de crédito solo mueve bolsas si representa una devolución
        # física. Las notas puramente monetarias no alteran el inventario.
        if tipo_doc == Venta.TipoDocumento.NOTA_CREDITO and not devolucion_fisica:
            continue

        signo = -1 if tipo_doc == Venta.TipoDocumento.NOTA_CREDITO else 1
        componentes = recetas.get(sku, [])

        if not componentes:
            if unidades:
                skus_sin_receta.add(sku)
            continue

        fila = {
            "sku": sku,
            "nombre": nombre,
            "tipo_doc": tipo_doc,
            "unidades_sku": unidades * signo,
        }
        valores = {}
        for codigo, cantidad in componentes:
            valor = signo * unidades * cantidad
            totales[codigo] += valor
            valores[codigo] = valor
            fila[f"bolsas_{codigo}"] = valor

        fila["bolsas_lista"] = [
            {
                "codigo": material.codigo,
                "nombre": material.nombre,
                "peso_kg": material.peso_kg,
                "cantidad": valores.get(material.codigo, 0),
            }
            for material in materiales
        ]
        fila["bolsas_8"] = sum(
            valor for codigo, valor in valores.items()
            if next(
                (m.peso_kg for m in materiales if m.codigo == codigo), 0
            ) == Decimal("8")
        )
        fila["bolsas_20"] = sum(
            valor for codigo, valor in valores.items()
            if next(
                (m.peso_kg for m in materiales if m.codigo == codigo), 0
            ) == Decimal("20")
        )
        detalle.append(fila)

    detalle.sort(
        key=lambda x: (
            sum(
                abs(v) for k, v in x.items()
                if k.startswith("bolsas_")
                and k not in ("bolsas_8", "bolsas_20", "bolsas_lista")
            ),
        ),
        reverse=True,
    )

    data = {
        "materiales": [],
        "detalle": detalle,
        "skus_sin_receta": sorted(sku for sku in skus_sin_receta if sku),
        # Se conserva la clave histórica para compatibilidad de vistas/tests.
        "skus_sin_mapa": sorted(sku for sku in skus_sin_receta if sku),
    }

    for material in materiales:
        codigo = material.codigo
        consumo = totales.get(codigo, 0)
        inicial = stock_inicial.get(codigo, 0)
        data[f"consumo_{codigo}"] = consumo
        data[f"stock_inicial_{codigo}"] = inicial
        data[f"inventario_{codigo}"] = inicial - consumo
        data["materiales"].append({
            "codigo": codigo,
            "nombre": material.nombre,
            "peso_kg": material.peso_kg,
            "activo": material.activo,
            "consumo": consumo,
            "stock_inicial": inicial,
            "stock_actual": inicial - consumo,
        })

    for peso in (8, 20):
        grupo = _materiales_por_peso(peso)
        codigos = [m.codigo for m in grupo]
        data[f"consumo_{peso}"] = sum(totales.get(c, 0) for c in codigos)
        data[f"stock_inicial_{peso}"] = sum(
            stock_inicial.get(c, 0) for c in codigos
        )
        data[f"inventario_{peso}"] = sum(
            stock_inicial.get(c, 0) - totales.get(c, 0) for c in codigos
        )

    return data


def get_ajustes_bolsas():
    """Retorna diccionario con suma histórica de ajustes por material."""
    ajustes = InventarioBolsasAjuste.objects.values(
        "tipo_bolsa__codigo"
    ).annotate(suma=Sum("diferencia"))
    resultado = {material.codigo: 0 for material in Material.objects.all()}
    for ajuste in ajustes:
        clave = ajuste["tipo_bolsa__codigo"]
        if clave in resultado:
            resultado[clave] = ajuste["suma"] or 0
    return resultado


def inventario_bolsas_actual():
    """Stock actual incorporando ajustes: stock_base - consumo + ajustes."""
    data = consumo_bolsas()
    ajustes = get_ajustes_bolsas()

    for material in data["materiales"]:
        codigo = material["codigo"]
        stock = (
            data.get(f"stock_inicial_{codigo}", 0)
            - data.get(f"consumo_{codigo}", 0)
            + ajustes.get(codigo, 0)
        )
        data[f"stock_actual_{codigo}"] = stock
        material["stock_actual"] = stock

    for peso in (8, 20):
        codigos = [m.codigo for m in _materiales_por_peso(peso)]
        data[f"stock_actual_{peso}"] = sum(
            data.get(f"stock_actual_{codigo}", 0) for codigo in codigos
        )

    return data


def stock_actual_post_inventario_kg():
    """Stock actual post-inventario en kilogramos, según el peso de cada material."""
    data = inventario_bolsas_actual()
    total_kg = sum(
        max(data.get(f"stock_actual_{material.codigo}", 0), 0)
        * material.peso_kg
        for material in Material.objects.all()
    )
    return total_kg
