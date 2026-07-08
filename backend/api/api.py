"""Zentrale API-Instanz (django-ninja).

OpenAPI-Schema: /api/openapi.json — daraus werden die Clients generiert
(Angular jetzt, Kotlin für die Android-App später). Interaktive Doku: /api/docs.
"""
from ninja import NinjaAPI

from api.artikel import router as artikel_router
from api.aufgabe import router as aufgabe_router
from api.beleg import router as beleg_router
from api.identity import router as identity_router
from api.projekt import router as projekt_router
from api.property import router as property_router

# django-ninja aktiviert den CSRF-Schutz automatisch, sobald ein Endpoint
# Cookie-basierte Auth (django_auth) nutzt — kein globaler Schalter nötig.
api = NinjaAPI(title="MCN API", version="0.1.0")


@api.get("/health", tags=["system"])
def health(request):
    return {"status": "ok"}


api.add_router("/identity", identity_router, tags=["identity"])
api.add_router("/property", property_router, tags=["property"])
api.add_router("/workflow", projekt_router, tags=["workflow"])
api.add_router("/workflow", aufgabe_router, tags=["workflow"])
api.add_router("/invoicing", beleg_router, tags=["invoicing"])
api.add_router("/pricing", artikel_router, tags=["pricing"])
