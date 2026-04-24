"""
URL configuration for design_workflow project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.contrib import admin
from django.urls import path, include, re_path
from django.views.static import serve
from django.http import JsonResponse

from ws.views import GetMaintenanceView


def health_check(request):
    """Simple health check endpoint for Docker/load balancer health checks."""
    return JsonResponse({"status": "healthy"})


def custom_404(request, exception=None):
    """Custom 404 handler returning JSON."""
    return JsonResponse(
        {"status_code": 404, "message": "Page introuvable", "details": {}},
        status=404,
    )


def custom_500(request):
    """Custom 500 handler returning JSON."""
    return JsonResponse(
        {"status_code": 500, "message": "Erreur interne du serveur", "details": {}},
        status=500,
    )


# Custom error handlers
handler404 = custom_404
handler500 = custom_500

urlpatterns = [
    # Health check endpoint (unauthenticated)
    path("api/health/", health_check, name="health-check"),
    # Account
    path("api/account/", include("account.urls")),
    path("api/design-workflow/", include("design_workflow.urls")),
    # Maintenance state (unauthenticated)
    path("api/ws/maintenance/", GetMaintenanceView.as_view(), name="ws-maintenance"),
    # Admin panel (obscured path for security)
    path("gestion-interne-gf62/", admin.site.urls),
]

# Always serve static/media — nginx proxies these to Django
urlpatterns += [
    re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}),
    re_path(r"^static/(?P<path>.*)$", serve, {"document_root": settings.STATIC_ROOT}),
]
