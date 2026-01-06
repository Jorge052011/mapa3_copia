"""
URL configuration for DistribucionApp project.
"""

from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('rutas.urls')),  # todas las URLs de la app 'rutas'
    path("crm/", include(("crm.urls", "crm"), namespace="crm")),
]
