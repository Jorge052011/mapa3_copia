from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from .models import VentaItem


@receiver(post_save, sender=VentaItem)
def actualizar_monto_venta_al_guardar_item(sender, instance, **kwargs):
    instance.venta.recalcular_monto_total()


@receiver(post_delete, sender=VentaItem)
def actualizar_monto_venta_al_borrar_item(sender, instance, **kwargs):
    instance.venta.recalcular_monto_total()
