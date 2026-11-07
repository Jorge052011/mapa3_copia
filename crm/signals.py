from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from .models import Material, Producto, ProductoMaterial, VentaItem


@receiver(post_save, sender=VentaItem)
def actualizar_monto_venta_al_guardar_item(sender, instance, **kwargs):
    instance.venta.recalcular_monto_total()


@receiver(post_delete, sender=VentaItem)
def actualizar_monto_venta_al_borrar_item(sender, instance, **kwargs):
    instance.venta.recalcular_monto_total()

def _recalcular_producto(producto_id):
    producto = Producto.objects.filter(pk=producto_id).first()
    if producto is not None:
        producto.recalcular_peso()


@receiver(post_save, sender=ProductoMaterial)
@receiver(post_delete, sender=ProductoMaterial)
def actualizar_peso_producto_por_receta(sender, instance, **kwargs):
    _recalcular_producto(instance.producto_id)


@receiver(post_save, sender=Material)
@receiver(post_delete, sender=Material)
def actualizar_pesos_por_material(sender, instance, **kwargs):
    for producto_id in (
        Producto.objects.filter(componentes__material_id=instance.codigo)
        .values_list("pk", flat=True)
        .distinct()
    ):
        _recalcular_producto(producto_id)
