from django.contrib import admin
from django.urls import path

from core.api import api

urlpatterns = [
    path("_maintenance/", admin.site.urls),
    path("api/v1/", api.urls),
]
