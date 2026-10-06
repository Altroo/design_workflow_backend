from django.conf import settings
from django.views.decorators.cache import cache_control
from django.views.static import serve


@cache_control(private=True, max_age=31536000, immutable=True)
def serve_card_thumbnail(request, path):
    # Only UUID-named thumbnails use this route. Replacing a cover changes its URL.
    return serve(request, path, document_root=settings.MEDIA_ROOT)
