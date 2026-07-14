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

from api.anlage import router as anlage_router
from api.artikel import router as artikel_router
from api.aufgabe import router as aufgabe_router
from api.auftrag import router as auftrag_router
from api.auth import router as auth_router
from api.auswertungen import router as auswertungen_router
from api.beleg import router as beleg_router
from api.belegerfassung import router as belegerfassung_router
from api.belegung import router as belegung_router
from api.dateien import router as dateien_router
from api.dossier import router as dossier_router
from api.buchhaltung import router as buchhaltung_router
from api.firma import router as firma_router
from api.identity import router as identity_router
from api.lieferant import router as lieferant_router
from api.lohngruppe import router as lohngruppe_router
from api.mail import router as mail_router
from api.maintenance import router as maintenance_router
from api.mitarbeiter import router as mitarbeiter_router
from api.planung import router as planung_router
from api.projekt import router as projekt_router
from api.property import router as property_router
from api.qualifikation import router as qualifikation_router
from api.raum import router as raum_router
from api.security import router as security_router
from api.site_report import router as site_report_router
from api.suche import router as suche_router
from api.verwaltung import router as verwaltung_router
from api.zeiterfassung import hr_router as zeit_stammdaten_router
from api.zeiterfassung import router as zeiterfassung_router

# Cookie-basierte Session-Auth für die ganze API; django-ninja aktiviert damit
# zugleich den CSRF-Schutz für unsichere Methoden.
api = NinjaAPI(title="MCN API", version="0.1.0", auth=django_auth)


@api.get("/health", tags=["system"], auth=None)
def health(request):
    return {"status": "ok"}


api.add_router("/auth", auth_router, tags=["auth"])
api.add_router("/identity", identity_router, tags=["identity"])
api.add_router("/property", property_router, tags=["property"])
# Raumaufmaß (0086): derselbe Präfix, dasselbe Recht — der Raum ist
# Objektstammdatum, kein Vorgangswert.
api.add_router("/property", raum_router, tags=["property"])
# Technische Anlagen (0004): ebenfalls Objektstammdatum, ebenfalls Recht `property`.
api.add_router("/property", anlage_router, tags=["property"])
# Belegung (0005) und Verwaltung (0006) — eigene Präfixe, eigene Rechtemodule
# (`tenure` / `management`). Sie hängen NICHT an `property`: Wer Räume und Anlagen
# pflegen darf, darf damit noch lange keine Mietverhältnisse ändern und keine
# Verwaltungsverträge schließen. Die Matrix trennt das seit 0026 — bis zu diesem
# Slice benutzte nur niemand die Trennung.
api.add_router("/tenure", belegung_router, tags=["tenure"])
api.add_router("/management", verwaltung_router, tags=["management"])
api.add_router("/workflow", projekt_router, tags=["workflow"])
api.add_router("/workflow", aufgabe_router, tags=["workflow"])
api.add_router("/workflow", auftrag_router, tags=["workflow"])
api.add_router("/workflow", site_report_router, tags=["workflow"])
api.add_router("/planung", planung_router, tags=["planung"])
# Qualifikationen + Zuweisungs-Vorlagen (0078). Liegt unter /planung, weil es
# Planungswerkzeuge sind — die NACHWEISE am Mitarbeiter hängen darin aber am
# `hr`-Recht (Personalakte), nicht an `workflow`.
api.add_router("/planung", qualifikation_router, tags=["planung"])
api.add_router("/invoicing", beleg_router, tags=["invoicing"])
api.add_router("/buchhaltung", buchhaltung_router, tags=["buchhaltung"])
api.add_router("/maintenance", maintenance_router, tags=["maintenance"])
api.add_router("/hr", mitarbeiter_router, tags=["hr"])
api.add_router("/hr", zeit_stammdaten_router, tags=["hr"])
api.add_router("/zeiterfassung", zeiterfassung_router, tags=["zeiterfassung"])
api.add_router("/pricing", artikel_router, tags=["pricing"])
api.add_router("/pricing", lohngruppe_router, tags=["pricing"])
api.add_router("/pricing", lieferant_router, tags=["pricing"])
api.add_router("/auswertungen", auswertungen_router, tags=["auswertungen"])
api.add_router("/company", firma_router, tags=["company"])
api.add_router("/company", mail_router, tags=["company"])
api.add_router("/security", security_router, tags=["security"])
api.add_router("/accounting", belegerfassung_router, tags=["accounting"])
api.add_router("/content", dateien_router, tags=["content"])
# Entitäts-Dossiers: EIN Aufruf je Entität, deterministisch und rechtegefiltert.
# Bewusst ein eigener Präfix (kein Modul der Rechtematrix): Ein Dossier bündelt
# mehrere Module — der KERN hängt am Modul der Entität, jeder weitere Baustein an
# seinem eigenen (siehe api/dossier.py).
api.add_router("/dossier", dossier_router, tags=["dossier"])
# Globale Suche: EIN Endpunkt über alle Entitäten, jede Kategorie an ihrem eigenen
# Modul getort (kein eigenes Recht — siehe api/suche.py).
api.add_router("/suche", suche_router, tags=["suche"])
