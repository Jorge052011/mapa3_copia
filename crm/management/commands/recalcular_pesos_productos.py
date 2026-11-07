from django.core.management.base import BaseCommand
from django.db import transaction

from crm.models import Producto


class Command(BaseCommand):
    help = "Recalcula Producto.peso_kg desde las recetas ProductoMaterial."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Muestra los cambios sin guardarlos.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        cambios = []
        sin_receta = []

        productos = list(
            Producto.objects.prefetch_related("componentes__material").order_by("sku")
        )
        for producto in productos:
            actual = producto.peso_kg
            calculado = producto.recalcular_peso(guardar=False)
            if calculado is None:
                sin_receta.append(producto)
            if actual != calculado:
                cambios.append((producto, actual, calculado))

        for producto, actual, calculado in cambios:
            self.stdout.write(
                f"SKU {producto.sku}: {actual} -> {calculado}"
            )
        for producto in sin_receta:
            self.stdout.write(
                self.style.WARNING(f"SKU {producto.sku}: sin receta; peso = None")
            )

        self.stdout.write(
            f"Productos revisados: {len(productos)}; "
            f"cambios: {len(cambios)}; sin receta: {len(sin_receta)}."
        )

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry-run: no se guardaron cambios."))
            return

        with transaction.atomic():
            for producto, _actual, calculado in cambios:
                producto.peso_kg = calculado
                producto.save(update_fields=["peso_kg"])

        self.stdout.write(self.style.SUCCESS("Pesos recalculados."))
