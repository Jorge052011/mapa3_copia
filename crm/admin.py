from django.contrib import admin
from django.db.models import Sum, Count, Max
from .models import (
    Cliente,
    Producto,
    Venta,
    VentaItem,
    Importacion,
    GastoOperacional,
)
from .services import segmentar_cliente


# =========================
# PRODUCTOS ✅
# =========================
@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ("sku", "nombre", "peso_kg", "precio_sugerido", "activo")
    list_filter = ("activo",)
    search_fields = ("sku", "nombre")
    ordering = ("nombre",)


# =========================
# CLIENTES
# =========================
@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "nombre",
        "telefono",
        "email",
        "get_kilos_total",
        "get_gasto_total",
        "get_compras",
        "get_ultima_compra",
        "get_segmento",
    )

    list_filter = ("comuna",)
    search_fields = ("nombre", "telefono", "email")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            kilos_total=Sum("ventas__kilos_total"),
            gasto_total=Sum("ventas__monto_total"),
            compras=Count("ventas"),
            ultima_compra=Max("ventas__fecha"),
        )

    def get_kilos_total(self, obj):
        return obj.kilos_total or 0
    get_kilos_total.short_description = "Kilos totales"
    get_kilos_total.admin_order_field = "kilos_total"

    def get_gasto_total(self, obj):
        return obj.gasto_total or 0
    get_gasto_total.short_description = "Gasto total ($)"
    get_gasto_total.admin_order_field = "gasto_total"

    def get_compras(self, obj):
        return obj.compras
    get_compras.short_description = "N° compras"
    get_compras.admin_order_field = "compras"

    def get_ultima_compra(self, obj):
        return obj.ultima_compra
    get_ultima_compra.short_description = "Última compra"

    def get_segmento(self, obj):
        return segmentar_cliente(obj)[0]
    get_segmento.short_description = "Segmento"


# =========================
# VENTAS
# =========================
@admin.register(Venta)
class VentaAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "cliente",
        "fecha",
        "tipo_documento",
        "numero_documento",
        "kilos_total",
        "monto_total",
        "canal",
    )
    list_filter = ("canal", "fecha", "tipo_documento")
    date_hierarchy = "fecha"
    search_fields = ("cliente__nombre", "numero_documento")
    ordering = ("-id",)


# =========================
# ÍTEMS DE VENTA
# =========================
@admin.register(VentaItem)
class VentaItemAdmin(admin.ModelAdmin):
    list_display = (
        "venta",
        "producto",
        "cantidad",
        "precio_unitario",
        "subtotal",
    )
    search_fields = ("producto__nombre", "producto__sku", "venta__cliente__nombre")
    ordering = ("-id",)


# =========================
# IMPORTACIONES ✅
# =========================
@admin.register(Importacion)
class ImportacionAdmin(admin.ModelAdmin):
    list_display = (
        "fecha",
        "descripcion",
        "kilos_ingresados",
        "merma_kg",
        "kilos_restantes",
        "costo_total",
        "costo_por_kg",
        "activo",
    )
    list_filter = ("activo",)
    ordering = ("-fecha",)

# =========================
# GASTOS OPERACIONALES ✅
# =========================
@admin.register(GastoOperacional)
class GastoOperacionalAdmin(admin.ModelAdmin):
    list_display = ("fecha", "tipo", "descripcion", "monto_neto", "aplica_iva")
    list_filter = ("tipo", "aplica_iva")
    search_fields = ("descripcion",)
    ordering = ("-fecha",)
