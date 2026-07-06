"""ASGI-Einstiegspunkt — für KI-Streaming (SSE) unter uvicorn betreiben:

    uv run uvicorn config.asgi:application --reload
"""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_asgi_application()
