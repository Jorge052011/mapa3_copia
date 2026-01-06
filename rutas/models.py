# rutas/models.py
from django.db import models


class PuntoEntrega(models.Model):
    nombre = models.CharField(max_length=255)
    direccion = models.CharField(max_length=255)
    latitud = models.DecimalField(max_digits=9, decimal_places=6)
    longitud = models.DecimalField(max_digits=9, decimal_places=6)
    orden_optimo = models.IntegerField(null=True, blank=True)

    # ✅ NUEVO: Meta con índices
    class Meta:
        verbose_name = "Punto de Entrega"
        verbose_name_plural = "Puntos de Entrega"
        indexes = [
            models.Index(fields=['orden_optimo']),
            models.Index(fields=['nombre']),
        ]

    def __str__(self):
        return self.nombre