"""Zentrale API-Instanz (django-ninja).

OpenAPI-Schema: /api/openapi.json — daraus werden die Clients generiert
(Angular jetzt, Kotlin für die Android-App später). Interaktive Doku: /api/docs.
"""
from ninja import NinjaAPI

api = NinjaAPI(title="MCN API", version="0.1.0")


@api.get("/health", tags=["system"])
def health(request):
    return {"status": "ok"}
