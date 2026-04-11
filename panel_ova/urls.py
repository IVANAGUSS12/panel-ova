from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
import os

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include(('accounts.urls', 'accounts'), namespace='accounts')),
    path('', include(('core.urls', 'core'), namespace='core')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    # Include debug toolbar only when explicitly enabled and installed.
    if os.getenv('ENABLE_DEBUG_TOOLBAR', '0') == '1' and 'debug_toolbar' in settings.INSTALLED_APPS:
        import debug_toolbar
        urlpatterns = [path('__debug__/', include(debug_toolbar.urls)),] + urlpatterns

    # Include Silk only if installed in INSTALLED_APPS (it can be present
    # without being active in production). Import only when configured.
    if 'silk' in settings.INSTALLED_APPS:
        urlpatterns = [path('silk/', include('silk.urls', namespace='silk')),] + urlpatterns
