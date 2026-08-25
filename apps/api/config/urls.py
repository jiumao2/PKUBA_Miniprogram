from django.urls import path

from core.admin import maintenance_site
from core.api import api

urlpatterns = [
    path("_maintenance/", maintenance_site.urls),
    path("api/v1/", api.urls),
]
