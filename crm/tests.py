from django.test import TestCase
from decimal import Decimal
from .models import Cliente, Producto, Venta, Importacion

class ClienteTestCase(TestCase):
    def test_crear_cliente(self):
        cliente = Cliente.objects.create(
            nombre="Test Cliente",
            telefono="912345678"
        )
        self.assertEqual(cliente.nombre, "Test Cliente")
        
    def test_segmento_nuevo(self):
        cliente = Cliente.objects.create(nombre="Nuevo")
        self.assertEqual(cliente.segmento, "Ocasional")