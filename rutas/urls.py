from django.urls import path
from . import views

urlpatterns = [
    path('', views.mapa_view, name='mapa'),
    path('agregar_punto/', views.agregar_punto, name='agregar_punto'),
    path('optimizar_ruta/', views.optimizar_ruta, name='optimizar_ruta'),
    path('borrar_puntos/', views.borrar_puntos, name='borrar_puntos'),
    path('borrar_punto/<int:punto_id>/', views.borrar_punto, name='borrar_punto'),
]
