"""
URL configuration for DistribucionApp project.
"""

from django.contrib import admin
from django.urls import path, include
from django.views.generic.base import RedirectView

urlpatterns = [
    path('', RedirectView.as_view(url='/crm/dashboard/', permanent=False)),
    path('admin/', admin.site.urls),
    path("crm/", include(("crm.urls", "crm"), namespace="crm")),
]
