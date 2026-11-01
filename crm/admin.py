from django.contrib import admin, messages
from django.db.models import Sum, Count, Max
from .models import (
    Cliente,
    Producto,
    Venta,
    VentaItem,
    Importacion,
    IngresoMaterial,
    GastoOperacional,
    ControlTrazabilidad,
    MovimientoBolsasVenta,
)
from .services import segmentar_cliente
from .services_trazabilidad import importaciones_recepcionables


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
        "devolucion_fisica",
    )
    list_filter = ("canal", "fecha", "tipo_documento", "devolucion_fisica")
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
        "item_origen",
        "cantidad",
        "precio_unitario",
        "subtotal",
    )
    search_fields = ("producto__nombre", "producto__sku", "venta__cliente__nombre")
    ordering = ("-id",)


# =========================
# IMPORTACIONES ✅
# =========================
class IngresoMaterialInline(admin.TabularInline):
    model = IngresoMaterial
    extra = 0
    fields = (
        "fecha",
        "tipo_bolsa",
        "cantidad",
        "control_contenedor",
        "recepcion_key",
        "observacion",
        "creado_por",
        "creado_en",
    )
    readonly_fields = ("creado_por", "creado_en", "recepcion_key")
    can_delete = False


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
    inlines = [IngresoMaterialInline]

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        permitidas = set(importaciones_recepcionables().values_list("pk", flat=True))
        for instance in instances:
            if isinstance(instance, IngresoMaterial):
                if instance.pk is None and instance.importacion_id not in permitidas:
                    messages.error(
                        request,
                        "No se puede agregar una recepción a esa importación: "
                        "está cerrada para el control por contenedor.",
                    )
                    continue
                if instance.creado_por_id is None:
                    instance.creado_por = request.user
            instance.save()
        formset.save_m2m()


@admin.register(IngresoMaterial)
class IngresoMaterialAdmin(admin.ModelAdmin):
    list_display = (
        "fecha",
        "importacion",
        "tipo_bolsa",
        "cantidad",
        "control_contenedor",
        "recepcion_key",
        "creado_por",
        "creado_en",
    )
    list_filter = ("tipo_bolsa", "fecha", "importacion", "control_contenedor")
    search_fields = ("importacion__descripcion", "observacion")
    ordering = ("-fecha", "-id")
    date_hierarchy = "fecha"
    readonly_fields = ("creado_por", "creado_en")

    def save_model(self, request, obj, form, change):
        if obj.creado_por_id is None:
            obj.creado_por = request.user
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

# =========================
# TRAZABILIDAD POR CONTENEDOR
# =========================
@admin.register(ControlTrazabilidad)
class ControlTrazabilidadAdmin(admin.ModelAdmin):
    list_display = ("fecha_inicio", "activo", "creado_por", "creado_en")
    readonly_fields = ("creado_en",)

    def has_add_permission(self, request):
        return not ControlTrazabilidad.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MovimientoBolsasVenta)
class MovimientoBolsasVentaAdmin(admin.ModelAdmin):
    list_display = (
        "creado_en",
        "importacion",
        "tipo_bolsa",
        "tipo_movimiento",
        "cantidad",
        "venta_ref",
        "venta_item_ref",
        "creado_por",
    )
    list_filter = ("tipo_movimiento", "tipo_bolsa", "importacion")
    search_fields = ("sku", "venta_ref", "venta_item_ref", "observacion")
    ordering = ("-creado_en", "-id")
    date_hierarchy = "creado_en"
    readonly_fields = (
        "venta_item", "venta_item_ref", "venta_ref", "sku", "importacion",
        "tipo_bolsa", "cantidad", "tipo_movimiento", "movimiento_origen",
        "idempotency_key", "observacion", "creado_por", "creado_en",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return True

    def has_delete_permission(self, request, obj=None):
        return False


# =========================
# GASTOS OPERACIONALES ✅
# =========================
@admin.register(GastoOperacional)
class GastoOperacionalAdmin(admin.ModelAdmin):
    list_display = ("fecha", "tipo", "descripcion", "monto_neto", "aplica_iva")
    list_filter = ("tipo", "aplica_iva")
    search_fields = ("descripcion",)
    ordering = ("-fecha",)
