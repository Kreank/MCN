"""Zentrale API-Instanz (django-ninja).

OpenAPI-Schema: /api/openapi.json — daraus werden die Clients generiert
(Angular jetzt, Kotlin für die Android-App später). Interaktive Doku: /api/docs.

**Seit dem Auth-Slice ist die gesamte API anmeldepflichtig.** Das `auth`-Argument
der NinjaAPI-Instanz gilt für jeden Endpunkt, der nicht ausdrücklich `auth=None`
setzt — bisher trugen nur die Schreib-Endpunkte `auth=django_auth`, die
Lese-Endpunkte waren in der Dev-Phase offen. Damit fällt auch die zuletzt
verbliebene DSGVO-Lücke (`GET /hr/employees/{id}` mit Krankheitshistorie).

Ausgenommen (`auth=None`): `/health` und die vier Endpunkte unter `/auth`, die
erreichbar sein müssen, bevor eine Sitzung besteht.
"""
from ninja import NinjaAPI
from ninja.security import django_auth

from api.artikel import router as artikel_router
from api.aufgabe import router as aufgabe_router
from api.auftrag import router as auftrag_router
from api.auth import router as auth_router
from api.auswertungen import router as auswertungen_router
from api.beleg import router as beleg_router
from api.buchhaltung import router as buchhaltung_router
from api.firma import router as firma_router
from api.identity import router as identity_router
from api.maintenance import router as maintenance_router
from api.mitarbeiter import router as mitarbeiter_router
from api.planung import router as planung_router
from api.projekt import router as projekt_router
from api.property import router as property_router

# Cookie-basierte Session-Auth für die ganze API; django-ninja aktiviert damit
# zugleich den CSRF-Schutz für unsichere Methoden.
api = NinjaAPI(title="MCN API", version="0.1.0", auth=django_auth)


@api.get("/health", tags=["system"], auth=None)
def health(request):
    return {"status": "ok"}


api.add_router("/auth", auth_router, tags=["auth"])
api.add_router("/identity", identity_router, tags=["identity"])
api.add_router("/property", property_router, tags=["property"])
api.add_router("/workflow", projekt_router, tags=["workflow"])
api.add_router("/workflow", aufgabe_router, tags=["workflow"])
api.add_router("/workflow", auftrag_router, tags=["workflow"])
api.add_router("/planung", planung_router, tags=["planung"])
api.add_router("/invoicing", beleg_router, tags=["invoicing"])
api.add_router("/buchhaltung", buchhaltung_router, tags=["buchhaltung"])
api.add_router("/maintenance", maintenance_router, tags=["maintenance"])
api.add_router("/hr", mitarbeiter_router, tags=["hr"])
api.add_router("/pricing", artikel_router, tags=["pricing"])
api.add_router("/auswertungen", auswertungen_router, tags=["auswertungen"])
api.add_router("/company", firma_router, tags=["company"])
