"""seed_szenario — der Datenbestand für die Vorführung (SHK-Betrieb, Berlin).

**Nicht zu verwechseln mit `seed_demo`.** `seed_demo` ist Entwicklerfutter: es
berührt jeden Codepfad einmal und wirkt in einer Vorführung wie fremder
Beispielkram. Dieser Seed baut die Welt des Users nach — die WEG Badensche
Straße 53 mit ihren sechs Mietern, das EFH Peter Borm, die eigene Belegschaft —
und führt darin die sechs Szenarien aus `docs/demo-szenario.md` durch:

    A  Wartungsvertrag Zentralheizung (Fälligkeiten-Engine)
    B  Legionellenprüfung (Prüffrist, TrinkwV)
    C  Havarie Rohrbruch (ganze Kette bis Regie-Abrechnung)
    D  Thermostatventile (Pauschal-Angebot → Soll-Ist mit MEHRVERBRAUCH)
    E  Badsanierung (Angebot → Abschlag → Schlussrechnung mit Anrechnung, § 35a)
    F  Heizungsstörung (Notdienst, Rechnung bleibt offen → Mahnwesen)

Alle fachlichen Writes laufen über die Service-Schicht und damit durch
`business_transaction`, die Statusautomaten und die DB-Trigger. Es gibt hier
**keinen Seed-Sonderweg** an den Toren vorbei: Was der reguläre Weg nicht
hergibt, wird gemeldet, nicht erzwungen.

**Zeitbezug:** alles relativ zu `betriebs_datum()` (Europa/Berlin), nie gegen ein
hartkodiertes Datum und nie gegen das UTC-Datum. Der Datenbestand sieht damit an
jedem Vorführungstag plausibel aus.

**Idempotenz:** jeder Block prüft seinen Anker (Name/Titel) und überspringt sich.
Ein zweiter Lauf legt nichts doppelt an; ein nach Teilerfolg abgebrochener Lauf
heilt sich auf Blockebene. Gelöscht wird nie (GoBD).

**Passwörter:** ohne DEBUG setzt Django-seitig niemand ein Passwort — die Chefs
stünden vor einem Login, durch das sie nicht kommen. Deshalb `--mit-passwoertern`
(oder `MCN_SZENARIO_PASSWOERTER=1`): setzt die Demo-Passwörter **bewusst** und
sagt es laut auf stdout. Kein Passwort landet je in einer Datei.

Für den Vorführungsserver gibt es zusätzlich den eigenständigen Befehl
`demo_passwoerter_setzen` (Deployment-Slice) — er setzt die Passwörter aller
Seed-Konten nachträglich und hängt an zwei bewussten Schaltern
(`MCN_DEMO_INSTANZ=1` + `MCN_DEMO_PASSWORD`). Beide Wege sind idempotent und
schließen sich nicht aus: Wer den Seed direkt mit `--mit-passwoertern` fährt,
braucht den zweiten Schritt nicht.

**Objektspeicher:** Die Kundenunterschrift unter dem Baustellenbericht (Szenario
C/D) geht durch MinIO. Steht er nicht, bleibt der Bericht im ENTWURF **und
Szenario C wird NICHT fakturiert** — eine Regie-Rechnung aus einem unsignierten
Bericht enthielte kein Material und wäre still zu niedrig. Der Seed sagt das und
holt beides beim nächsten Lauf nach (er ist an dieser Stelle wiederaufsetzbar).
"""
import math
import os
import struct
import uuid
import zlib
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from db_core.betriebszeit import BETRIEBS_TZ, betriebs_datum
from db_core.db_context import business_transaction
from db_core.models import (
    AppointmentCategory, AppUser, Article, ArticleSalePrice, Assembly,
    Employee, Inspection, InspectionType, Invoice, MaintenanceContract,
    ManagementMandate, Occupancy, Party, Property, Quote, Resource,
    SalePriceGroup, SiteReport, Task, TechnicalAsset, Unit, UserRole, WageGroup,
    WorkOrder,
)
from db_core.services import abrechnung as abrechnung_service
from db_core.services import anlage as anlage_service
from db_core.services import artikel as artikel_service
from db_core.services import aufgabe as aufgabe_service
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import belegung as belegung_service
from db_core.services import buchhaltung as buchhaltung_service
from db_core.services import einsatz as einsatz_service
from db_core.services import faelligkeit as faelligkeit_service
from db_core.services import firma as firma_service
from db_core.services import identity as identity_service
from db_core.services import mitarbeiter as mitarbeiter_service
from db_core.services import planung as planung_service
from db_core.services import projekt as projekt_service
from db_core.services import property as property_service
from db_core.services import pruefung as pruefung_service
from db_core.services import rechte_pflege as rechte_service
from db_core.services import site_report as report_service
from db_core.services import verwaltung as verwaltung_service
from db_core.services import vier_augen as vier_augen_service
from db_core.services import wartung as wartung_service

# ---------------------------------------------------------------------------
# Feste UUIDs — eigener Nummernraum, kollidiert NICHT mit seed_demo (…01xx).
# ---------------------------------------------------------------------------
SEED_UUID = {
    # Der Akteur des Seeds ist ein eigener technischer Benutzer — NICHT Patrick.
    # Grund (Befund, kein Umweg): `rechte_pflege.assign_role` verbietet die
    # Selbstzuweisung von Rollen. Wäre Patrick der Akteur, könnte der Seed ihm
    # seine eigene ADMINISTRATION-Rolle nicht über den Service geben — die erste
    # Rollenzuweisung eines Systems hat über den regulären Weg keinen Akteur.
    # Ein neutraler Einrichtungsbenutzer löst das, ohne die Sperre aufzuweichen;
    # im Audit steht dann ehrlich „MCN Einrichtung (Seed)" als Urheber.
    "system": uuid.UUID("00000000-0000-4000-8000-000000000200"),
    "patrick": uuid.UUID("00000000-0000-4000-8000-000000000201"),
    "robin": uuid.UUID("00000000-0000-4000-8000-000000000202"),
    "tina": uuid.UUID("00000000-0000-4000-8000-000000000203"),
    "sascha": uuid.UUID("00000000-0000-4000-8000-000000000204"),
    "murat": uuid.UUID("00000000-0000-4000-8000-000000000205"),
    "julian": uuid.UUID("00000000-0000-4000-8000-000000000206"),
    "rojhat": uuid.UUID("00000000-0000-4000-8000-000000000207"),
}

MAIL_DOMAIN = "mitra-sanitaer.de"

# Wegwerf-Demo-Passwort. NIE in eine Datei schreiben — nur stdout.
DEMO_PASSWORD = os.environ.get("MCN_SZENARIO_PASSWORD", "mcn-demo-passwort-2026")

# Die Belegschaft (docs/demo-szenario.md, Abschnitt 5).
BELEGSCHAFT = [
    {"key": "patrick", "vorname": "Patrick", "nachname": "van Dalen",
     "anrede": "Herr", "rolle": "ADMINISTRATION", "seit": date(2016, 4, 1),
     "lohngruppe": "Meister", "urlaub": Decimal("30")},
    {"key": "robin", "vorname": "Robin", "nachname": "Paul",
     "anrede": "Herr", "rolle": "BUCHHALTUNG", "seit": date(2017, 1, 2),
     "lohngruppe": None, "urlaub": Decimal("30")},
    {"key": "tina", "vorname": "Tina", "nachname": "Radtke",
     "anrede": "Frau", "rolle": "DISPOSITION", "seit": date(2019, 8, 1),
     "lohngruppe": None, "urlaub": Decimal("30")},
    {"key": "sascha", "vorname": "Sascha", "nachname": "Richter",
     "anrede": "Herr", "rolle": "DISPOSITION", "seit": date(2021, 2, 1),
     "lohngruppe": None, "urlaub": Decimal("30")},
    {"key": "murat", "vorname": "Murat", "nachname": "Emektar",
     "anrede": "Herr", "rolle": "MONTEUR", "seit": date(2018, 5, 2),
     "lohngruppe": "Monteur", "urlaub": Decimal("30")},
    {"key": "julian", "vorname": "Julian", "nachname": "Hoffmann",
     "anrede": "Herr", "rolle": "MONTEUR", "seit": date(2022, 9, 1),
     "lohngruppe": "Monteur", "urlaub": Decimal("28")},
    {"key": "rojhat", "vorname": "Rojhat", "nachname": "Beyaz",
     "anrede": "Herr", "rolle": "MONTEUR", "seit": date(2023, 3, 1),
     "lohngruppe": "Monteur", "urlaub": Decimal("28")},
]

VOLLZEIT = {
    "hours_monday": Decimal("8"), "hours_tuesday": Decimal("8"),
    "hours_wednesday": Decimal("8"), "hours_thursday": Decimal("8"),
    "hours_friday": Decimal("8"),
}

# Die sechs Mieter (Abschnitt 1). Der Mietername gehört NICHT in
# `contract_reference` — er ist eine eigene identity.party mit Telefon und
# E-Mail, verbunden über tenure.occupancy_party (Rolle CONTRACTUAL_TENANT).
#
# Rufnummern aus dem von der Bundesnetzagentur für Fiktion reservierten Block
# (030 23125 xxx), E-Mails auf example.com: erfundene Adressen echter Dritter
# gibt es hier nicht.
MIETER = [
    ("EG links", "Picolino", "+49 30 23125 101", "picolino@example.com", 2019),
    ("EG rechts", "Robco", "+49 30 23125 102", "robco@example.com", 2021),
    ("1. OG links", "Musili", "+49 30 23125 103", "musili@example.com", 2017),
    ("1. OG rechts", "Ruboni", "+49 30 23125 104", "ruboni@example.com", 2022),
    ("2. OG links", "Lufnik", "+49 30 23125 105", "lufnik@example.com", 2015),
    ("2. OG rechts", "Kutzi", "+49 30 23125 106", "kutzi@example.com", 2023),
]

WEG_NAME = "WEG Badensche Straße 53"
WEG_OBJEKT = "Badensche Straße 53"
EFH_OBJEKT = "EFH Peter Borm"
STEGOS = "Stegos Immobilien GmbH"

# Artikelstamm (SHK). Listenpreis + VK-Gruppe → der VK ist ohne EK rechenbar;
# damit läuft die Regie-Abrechnung ohne Preisklärung durch.
WAGE_GROUPS = [
    {"name": "Monteur", "kind": "LOHN", "hourly_rate": "58.00", "cost_rate": "42.00"},
    {"name": "Meister", "kind": "LOHN", "hourly_rate": "72.00", "cost_rate": "55.00"},
    {"name": "Notdienst (Zuschlag 50 %)", "kind": "LOHN",
     "hourly_rate": "87.00", "cost_rate": "63.00"},
]

ARTIKEL = [
    {"article_number": "SHK-1001", "description": "Thermostatventil-Oberteil Danfoss RA-N 15",
     "unit": "Stk", "line_type": "MATERIAL", "list_price": "24.50",
     "manufacturer_name": "Danfoss", "product_group": "Heizung / Armaturen"},
    {"article_number": "SHK-1002", "description": "Thermostatkopf Danfoss RAW-K",
     "unit": "Stk", "line_type": "MATERIAL", "list_price": "18.90",
     "manufacturer_name": "Danfoss", "product_group": "Heizung / Armaturen"},
    {"article_number": "SHK-1010", "description": "Kupferrohr 15 x 1,0 mm, halbhart",
     "unit": "m", "line_type": "MATERIAL", "list_price": "9.80",
     "manufacturer_name": "Wieland", "product_group": "Rohr / Kupfer"},
    {"article_number": "SHK-1011", "description": "Pressfitting Kupfer 15 mm (Muffe)",
     "unit": "Stk", "line_type": "MATERIAL", "list_price": "4.60",
     "manufacturer_name": "Viega", "product_group": "Rohr / Fittings"},
    {"article_number": "SHK-1012", "description": "Rohrschelle 15 mm mit Schallschutzeinlage",
     "unit": "Stk", "line_type": "MATERIAL", "list_price": "2.40",
     "product_group": "Befestigung"},
    {"article_number": "SHK-1020", "description": "Zündelektrode Vaillant ecoTEC (Ersatzteil)",
     "unit": "Stk", "line_type": "MATERIAL", "list_price": "42.00",
     "manufacturer_name": "Vaillant", "product_group": "Ersatzteile / Therme"},
    {"article_number": "SHK-1030", "description": "Waschtisch-Set Villeroy & Boch O.novo 60 cm",
     "unit": "Stk", "line_type": "MATERIAL", "list_price": "289.00",
     "manufacturer_name": "Villeroy & Boch", "product_group": "Sanitär / Keramik"},
    {"article_number": "SHK-1031", "description": "Dusch-Set Grohe Grohtherm 800 (Thermostat)",
     "unit": "Stk", "line_type": "MATERIAL", "list_price": "319.00",
     "manufacturer_name": "Grohe", "product_group": "Sanitär / Armaturen"},
    {"article_number": "SHK-2001", "description": "An- und Abfahrt Servicefahrzeug",
     "unit": "Fahrt", "line_type": "FAHRT", "list_price": "45.00"},
    {"article_number": "SHK-2002", "description": "Notdienstpauschale (außerhalb der Geschäftszeit)",
     "unit": "Einsatz", "line_type": "ZUSCHLAG", "list_price": "95.00"},
]

# ---------------------------------------------------------------------------
# Unterschrift (PNG) — ohne Fremdbibliothek erzeugt.
# ---------------------------------------------------------------------------


def _unterschrift_png(seed=0):
    """Ein handschriftlich anmutender Schriftzug als 8-Bit-Graustufen-PNG.

    Der Baustellenbericht wird vom Kunden auf dem Tablet unterschrieben; für den
    Seed wird die Kurve gerechnet statt eine Bilddatei ins Repo zu legen.
    """
    w, h = 420, 140
    px = [[255] * w for _ in range(h)]
    for x in range(30, w - 30):
        t = (x - 30) / (w - 60)
        y = (
            70
            + 30 * math.sin(t * 11 + seed)
            + 12 * math.sin(t * 27 + seed * 2)
            - 24 * t
        )
        for dy in (-2, -1, 0, 1):
            yy = int(y) + dy
            if 0 <= yy < h:
                px[yy][x] = 0
    raw = b"".join(b"\x00" + bytes(row) for row in px)

    def chunk(typ, data):
        return (
            struct.pack(">I", len(data))
            + typ
            + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


class Command(BaseCommand):
    help = (
        "Legt den Vorführungs-Datenbestand an (WEG Badensche Straße 53, EFH Peter "
        "Borm, eigene Belegschaft, sechs Szenarien). Idempotent; löscht nie."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--mit-passwoertern",
            action="store_true",
            default=os.environ.get("MCN_SZENARIO_PASSWOERTER") == "1",
            help=(
                "Setzt die Demo-Passwörter der Logins AUCH ohne DEBUG "
                "(Vorführungsserver). Das Passwort steht in MCN_SZENARIO_PASSWORD "
                "und wird nur auf stdout ausgegeben, nie in eine Datei geschrieben."
            ),
        )

    # -- Ausgabe -----------------------------------------------------------
    def _ok(self, text):
        self.angelegt += 1
        self.stdout.write(f"  + {text}")

    def _skip(self, text):
        self.uebersprungen += 1
        self.stdout.write(self.style.HTTP_NOT_MODIFIED(f"  = {text} (vorhanden)"))

    def _warn(self, text):
        self.befunde.append(text)
        self.stdout.write(self.style.WARNING(f"  ! {text}"))

    def _kapitel(self, text):
        self.stdout.write(self.style.MIGRATE_HEADING(f"\n{text}"))

    # -- Einstieg ----------------------------------------------------------
    def handle(self, *args, **options):
        self.angelegt = 0
        self.uebersprungen = 0
        self.befunde = []
        self.mit_passwoertern = options["mit_passwoertern"]

        self.heute = betriebs_datum()   # Betriebszeitzone, NICHT UTC
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"seed_szenario — Betriebsdatum {self.heute:%d.%m.%Y} "
                f"(Europe/Berlin), DEBUG={settings.DEBUG}"
            )
        )

        actor = self._app_user("system", "MCN Einrichtung (Seed)")

        self._firmenprofil(actor)
        self._rolle_buchhaltung(actor)
        self._stammdaten_planung(actor)
        self._artikelstamm(actor)
        self._kontakte(actor)
        self._liegenschaften(actor)
        self._belegschaft(actor)

        self._szenario_a(actor)
        self._szenario_b(actor)
        self._szenario_c(actor)
        self._szenario_d(actor)
        self._szenario_e(actor)
        self._szenario_f(actor)
        self._faelligkeiten(actor)
        self._logins()

        self.stdout.write(
            self.style.SUCCESS(
                f"\nseed_szenario fertig: {self.angelegt} angelegt, "
                f"{self.uebersprungen} übersprungen."
            )
        )
        if self.befunde:
            self.stdout.write(self.style.WARNING("\nBefunde (nicht erreichte Zustände):"))
            for b in self.befunde:
                self.stdout.write(self.style.WARNING(f"  ! {b}"))

    # =====================================================================
    # Grundlagen
    # =====================================================================
    def _app_user(self, key, display_name):
        """security.app_user idempotent über feste UUID (Bootstrapping)."""
        with transaction.atomic():
            user, _ = AppUser.objects.get_or_create(
                id=SEED_UUID[key],
                defaults={"display_name": display_name, "status": "ACTIVE",
                          "version": 1},
            )
        return user

    def _firmenprofil(self, actor):
        self._kapitel("Firmenprofil")
        if firma_service.get_company_profile() is not None:
            self._skip("Firmenprofil")
            return
        firma_service.update_company_profile(
            actor.id,
            company_name="Mitra Sanitär GmbH",
            legal_form="GmbH",
            street="Kaiserin-Augusta-Allee 29",
            postal_code="10553",
            city="Berlin",
            state_code="BE",
            phone="+49 30 23125 100",
            email=f"info@{MAIL_DOMAIN}",
            web=f"https://{MAIL_DOMAIN}",
            tax_number="29/123/45678",
            vat_id="DE811234567",
            commercial_register="HRB 123456 B, AG Charlottenburg",
            bank_name="Berliner Sparkasse",
            iban="DE02100500000054540402",
            bic="BELADEBEXXX",
            managing_director="Patrick van Dalen",
            managing_director_title="Geschäftsführer",
            datev_consultant_number="12345",
            datev_client_number="1001",
            datev_chart_of_accounts="SKR03",
            datev_account_length=4,
            datev_fiscal_year_start_month=1,
        )
        self._ok("Firmenprofil: Mitra Sanitär GmbH, Berlin")

    def _rolle_buchhaltung(self, actor):
        """Die Rolle BUCHHALTUNG für Robin Paul.

        **Befund gegenüber `docs/demo-szenario.md`:** Die Rolle existiert bereits
        — Migration 0026 liefert sieben Rollen aus (nicht vier; `seed_demo`
        *benutzt* nur vier). Ihre Startmatrix trifft die Vorgabe fast exakt:
        billing/invoicing/pricing/accounting schreibend, identity/property/
        workflow/content lesend, **security gar nicht** — also keine
        Rechteverwaltung und keine Benutzerverwaltung.

        Zwei Dinge fehlen für die Vorführung; sie werden hier als **Daten**
        nachgezogen (Rechtematrix ist Stammdatenpflege, keine Migration):

        * `content` ANLEGEN/AENDERN — „Dokumente schreibend" (Belege ablegen,
          Anhänge pflegen).
        * `security` LESEN/ANLEGEN — damit Robin einen **Vier-Augen-Antrag
          stellen** kann (Storno/Gutschrift). **FREIGEBEN bleibt aus**: Er
          beantragt, Patrick entscheidet. Genau das ist die Vorführung.
        * `management`/`tenure` LESEN — die Buchhaltung muss sehen, wer verwaltet
          und wer Mieter ist, um Belege richtig zu adressieren.
        """
        self._kapitel("Rolle BUCHHALTUNG (Rechtematrix als Daten)")
        gewuenscht = [
            ("content", "ANLEGEN"), ("content", "AENDERN"),
            ("security", "LESEN"), ("security", "ANLEGEN"),
            ("management", "LESEN"), ("tenure", "LESEN"),
        ]
        for modul, aktion in gewuenscht:
            row = next(
                (
                    r for r in rechte_service.permission_rows()
                    if r.role_id == "BUCHHALTUNG" and r.module == modul
                    and r.action == aktion
                ),
                None,
            )
            if row is not None and row.allowed:
                self._skip(f"BUCHHALTUNG {modul}/{aktion}")
                continue
            rechte_service.set_permission(
                actor.id, role_code="BUCHHALTUNG", module=modul, action=aktion,
                allowed=True, row_scope="ALLE",
            )
            self._ok(f"Recht gesetzt: BUCHHALTUNG {modul}/{aktion}")
        # Gegenprobe: die Rechteverwaltung bleibt zu.
        verboten = [
            r for r in rechte_service.permission_rows()
            if r.role_id == "BUCHHALTUNG" and r.module == "security"
            and r.action in ("AENDERN", "FREIGEBEN") and r.allowed
        ]
        if verboten:
            self._warn(
                "BUCHHALTUNG hat security/AENDERN oder security/FREIGEBEN — die "
                "Rollentrennung der Vorführung ist damit hinfällig."
            )

    def _stammdaten_planung(self, actor):
        self._kapitel("Planungs-Stammdaten")
        for idx, (name, token) in enumerate([
            ("Wartung", "SAGE"),
            ("Störung / Notdienst", "ORANGE"),
            ("Umsetzung", "NAVY"),
            ("Vor-Ort-Termin", "TEAL"),
            ("Begehung", "PLUM"),
            ("Büro", "AMBER"),
        ]):
            if AppointmentCategory.objects.filter(name=name).exists():
                self.uebersprungen += 1
                continue
            planung_service.create_category(
                actor.id, name=name, color_token=token, sort_order=idx
            )
            self._ok(f"Terminkategorie: {name}")
        for name, typ in [
            ("VW Crafter (B-MC 1234)", "FAHRZEUG"),
            ("Ford Transit (B-MC 5678)", "FAHRZEUG"),
            ("Pressmaschine Viega Picus", "GERAET"),
            ("Spülkompressor", "GERAET"),
        ]:
            if Resource.objects.filter(name=name).exists():
                self.uebersprungen += 1
                continue
            planung_service.create_resource(actor.id, name=name, resource_type=typ)
            self._ok(f"Betriebsmittel: {name}")

    def _artikelstamm(self, actor):
        self._kapitel("Artikelstamm")
        for wg in WAGE_GROUPS:
            if WageGroup.objects.filter(name=wg["name"]).exists():
                self.uebersprungen += 1
                continue
            artikel_service.create_wage_group(actor.id, **wg)
            self._ok(f"Lohngruppe: {wg['name']}")

        for art in ARTIKEL:
            if Article.objects.filter(article_number=art["article_number"]).exists():
                self.uebersprungen += 1
                continue
            a = artikel_service.create_article(actor.id, **art)
            self._ok(f"Artikel {a.article_number} — {a.description}")

        # Eine Leistung (Stückliste): reiner LOHN. Bewusst OHNE das Ventil als
        # Komponente — im Angebot D stehen Ventil und Thermostatkopf als eigene
        # Materialpositionen daneben. Läge das Ventil zusätzlich in der Leistung,
        # zählte der Soll-Ist-Abgleich (und damit der Nachtrag) es doppelt, und der
        # server­errechnete VK der Leistung enthielte das Material ein zweites Mal.
        # „Tauschen je Heizkörper" ist hier die Montagearbeit; das Material ist
        # separat kalkuliert (das übliche SHK-Modell).
        if not Assembly.objects.filter(assembly_number="LEI-3001").exists():
            monteur = WageGroup.objects.get(name="Monteur")
            asm = artikel_service.create_assembly(
                actor.id,
                assembly_number="LEI-3001",
                name="Thermostatventil tauschen (Montage je Heizkörper)",
                unit="Stk",
                description=(
                    "Heizkörper absperren, Altventil demontieren, "
                    "Thermostatventil-Oberteil setzen, Thermostatkopf montieren, "
                    "Anlage entlüften."
                ),
                components=[
                    {"wage_group_id": monteur.id, "minutes": "25.00"},
                ],
            )
            self._ok(f"Leistung {asm.assembly_number} — {asm.name}")

        # VK-Kalkulation: ohne sie hat kein Artikel einen Verkaufspreis, und die
        # Regie-Abrechnung landete in der Preisklärung statt in einer Rechnung.
        gruppen = {}
        for name, prozent in [
            ("Aufschlag 45 % (Material)", Decimal("45.000")),
            ("Aufschlag 30 % (Sonstiges)", Decimal("30.000")),
        ]:
            grp = SalePriceGroup.objects.filter(name=name).first()
            if grp is None:
                grp = artikel_service.create_sale_price_group(
                    actor.id, name=name, calc_basis="LISTENPREIS",
                    operator="AUFSCHLAG", percent_change=prozent,
                )
                self._ok(f"VK-Gruppe: {name}")
            gruppen[name] = grp
        for art in Article.objects.filter(status="AKTIV", list_price__isnull=False):
            if ArticleSalePrice.objects.filter(article_id=art.id).exists():
                continue
            grp = gruppen[
                "Aufschlag 45 % (Material)" if art.line_type == "MATERIAL"
                else "Aufschlag 30 % (Sonstiges)"
            ]
            artikel_service.set_article_sale_price(
                actor.id, article_id=art.id, label="Standard",
                sale_price_group_id=grp.id, is_standard=True,
            )
            self.angelegt += 1

    # =====================================================================
    # Kontakte, Liegenschaften, Belegung, Verwaltung, Anlagen
    # =====================================================================
    def _person(self, actor, vorname, nachname, anrede=None, telefon=None,
                mail=None, mobil=None):
        name = f"{vorname} {nachname}"
        party = Party.objects.filter(display_name=name).first()
        if party is None:
            party = identity_service.create_person(
                actor.id, first_name=vorname, last_name=nachname, salutation=anrede
            )
            self._ok(f"Person: {name}")
        if not identity_service.list_contact_points(party.id):
            for typ, wert in (("PHONE", telefon), ("MOBILE", mobil), ("EMAIL", mail)):
                if wert:
                    identity_service.add_contact_point(
                        actor.id, party.id, contact_type=typ, value=wert,
                        is_primary=True, valid_from=self.heute - timedelta(days=365),
                    )
        return party

    def _kontakte(self, actor):
        self._kapitel("Kontakte")
        # Verwaltung — Daten aus docs/demo-szenario.md (echte Domäne, so gewollt).
        stegos = Party.objects.filter(display_name=STEGOS).first()
        if stegos is None:
            stegos = identity_service.create_organization(
                actor.id, legal_name="Stegos Immobilien GmbH",
                organization_type="PROPERTY_MANAGEMENT", legal_form="GmbH",
            )
            identity_service.add_address(
                actor.id, stegos.id, address_type="BUSINESS",
                street="Klingsorstraße", house_number="7",
                postal_code="12167", city="Berlin",
                valid_from=self.heute - timedelta(days=1500),
            )
            identity_service.add_contact_point(
                actor.id, stegos.id, contact_type="EMAIL", value="info@stegos.net",
                is_primary=True, valid_from=self.heute - timedelta(days=1500),
            )
            identity_service.add_contact_point(
                actor.id, stegos.id, contact_type="PHONE", value="030 79085327",
                is_primary=True, valid_from=self.heute - timedelta(days=1500),
            )
            self._ok(f"Organisation: {STEGOS} (Verwaltung)")
        else:
            self._skip(STEGOS)

        # Der AUFTRAGGEBER ist die WEG — nicht die Verwaltung.
        weg = Party.objects.filter(display_name=WEG_NAME).first()
        if weg is None:
            weg = identity_service.create_organization(
                actor.id, legal_name="WEG Badensche Straße 53, 10825 Berlin",
                organization_type="WEG", display_name=WEG_NAME,
            )
            identity_service.add_address(
                actor.id, weg.id, address_type="BILLING",
                street="Badensche Straße", house_number="53",
                postal_code="10825", city="Berlin",
                valid_from=self.heute - timedelta(days=1500),
            )
            self._ok(f"Organisation: {WEG_NAME} (Auftraggeber)")
        else:
            self._skip(WEG_NAME)

        # Der EFH-Eigentümer.
        borm = self._person(
            actor, "Peter", "Borm", anrede="Herr",
            mobil="017662147248", mail="sascha-richter@homtail.de",
        )
        if not identity_service.list_addresses(borm.id):
            identity_service.add_address(
                actor.id, borm.id, address_type="BILLING",
                street="Ringelnatzstraße", house_number="22",
                postal_code="12437", city="Berlin",
                valid_from=self.heute - timedelta(days=900),
            )

        # Die sechs Mieter: echte Kontakte mit Telefon und E-Mail.
        for _einheit, name, tel, mail, _seit in MIETER:
            self._person(actor, "Familie", name, telefon=tel, mail=mail)

    def _liegenschaften(self, actor):
        self._kapitel("Liegenschaften")
        weg_party = Party.objects.get(display_name=WEG_NAME)
        stegos = Party.objects.get(display_name=STEGOS)
        borm = Party.objects.get(display_name="Peter Borm")

        # --- A: die WEG -------------------------------------------------
        weg_obj = Property.objects.filter(name=WEG_OBJEKT).first()
        if weg_obj is None:
            weg_obj = property_service.create_property(
                actor.id, name=WEG_OBJEKT, property_type="WEG",
                street="Badensche Straße", house_number="53",
                postal_code="10825", city="Berlin",
            )
            self._ok(f"Liegenschaft {weg_obj.property_number} — {WEG_OBJEKT}")
            haus = property_service.add_building(
                actor.id, property_id=weg_obj.id, building_number="1",
                name="Vorderhaus",
            )
            for einheit, *_rest in MIETER:
                property_service.add_unit(
                    actor.id, building_id=haus.id, property_id=weg_obj.id,
                    unit_type="APARTMENT", unit_number=einheit,
                )
            property_service.add_unit(
                actor.id, building_id=haus.id, property_id=weg_obj.id,
                unit_type="TECHNICAL_ROOM", unit_number="Heizungskeller",
            )
            self._ok("6 Wohnungen + Heizungskeller angelegt")
            property_service.add_party_role(
                actor.id, property_id=weg_obj.id, party_id=weg_party.id,
                role="COMMUNITY_OF_OWNERS",
                valid_from=self.heute - timedelta(days=1500),
            )
            self._ok(f"Rolle COMMUNITY_OF_OWNERS: {WEG_NAME}")
        else:
            self._skip(f"Liegenschaft {WEG_OBJEKT}")

        # Die Verwaltung ist KEINE Beteiligtenrolle — sie läuft über ein MANDAT.
        if not ManagementMandate.objects.filter(property_id=weg_obj.id).exists():
            verwaltung_service.create_mandat(
                actor.id,
                property_id=weg_obj.id,
                management_party_id=stegos.id,      # verwaltet
                principal_party_id=weg_party.id,    # beauftragt und zahlt
                default_contact_party_id=stegos.id,  # ist der Ansprechpartner
                mandate_type="WEG_MANAGEMENT",
                scope_type="ENTIRE_PROPERTY",
                valid_from=self.heute - timedelta(days=1200),
                contract_reference="Verwaltervertrag vom 01.01. (Eigentümerbeschluss)",
            )
            self._ok(f"Mandat WEG_MANAGEMENT: {STEGOS} verwaltet für {WEG_NAME}")
        else:
            self._skip("Verwaltungsmandat")

        # Belegung: sechs Wohnungen, sechs Mieter (CONTRACTUAL_TENANT).
        for einheit, name, _tel, _mail, seit in MIETER:
            unit = Unit.objects.filter(
                property_id=weg_obj.id, unit_number=einheit
            ).first()
            if unit is None or Occupancy.objects.filter(unit_id=unit.id).exists():
                self.uebersprungen += 1
                continue
            mieter = Party.objects.get(display_name=f"Familie {name}")
            belegung_service.create_belegung(
                actor.id,
                unit_id=unit.id,
                occupancy_type="RENTED",
                valid_from=date(seit, 1, 1),
                contract_reference=f"MV-{seit}-{einheit.replace(' ', '').replace('.', '')}",
                mieter=[{"party_id": mieter.id, "role": "CONTRACTUAL_TENANT"}],
            )
            self._ok(f"Belegung {einheit}: Familie {name} (Mieter seit {seit})")

        # Die technischen Anlagen — der fachliche Kern des Demo-Arguments.
        keller = Unit.objects.filter(
            property_id=weg_obj.id, unit_number="Heizungskeller"
        ).first()
        if not TechnicalAsset.objects.filter(property_id=weg_obj.id).exists():
            anlage_service.create_asset(actor.id, weg_obj.id, {
                "name": "Zentralheizung Badensche Straße 53",
                "asset_type": "HEIZUNG",
                "supply_type": "ZENTRAL",     # <- die Kernfrage des Monteurs
                "energy_source": "GAS",
                "manufacturer": "Viessmann",
                "model": "Vitocrossal 300 CU3A",
                "year_built": 2015,
                "power_kw": Decimal("80.00"),
                "building_id": keller.building_id if keller else None,
                "unit_id": keller.id if keller else None,
                "location_note": "Heizungskeller, Zugang über den Hof (Schlüssel Nr. 4).",
                "note": "Zentralanlage — versorgt alle sechs Einheiten. KEINE Etagenthermen.",
            })
            self._ok("Anlage: Zentralheizung (ZENTRAL, Viessmann, Bj. 2015, 80 kW)")
            anlage_service.create_asset(actor.id, weg_obj.id, {
                "name": "Trinkwassererwärmer (Speicher 400 l)",
                "asset_type": "TRINKWASSER",
                "supply_type": "ZENTRAL",
                "energy_source": "GAS",
                "manufacturer": "Viessmann",
                "model": "Vitocell 100-V",
                "year_built": 2015,
                "building_id": keller.building_id if keller else None,
                "unit_id": keller.id if keller else None,
                "location_note": "Heizungskeller, neben dem Kessel.",
                "note": "Zentrale Warmwasserbereitung → Legionellenprüfung (TrinkwV).",
            })
            self._ok("Anlage: Trinkwassererwärmer (Grundlage der Prüffrist)")

        # --- B: das EFH --------------------------------------------------
        efh = Property.objects.filter(name=EFH_OBJEKT).first()
        if efh is None:
            efh = property_service.create_property(
                actor.id, name=EFH_OBJEKT, property_type="EINFAMILIENHAUS",
                street="Ringelnatzstraße", house_number="22",
                postal_code="12437", city="Berlin",
            )
            self._ok(f"Liegenschaft {efh.property_number} — {EFH_OBJEKT}")
            haus = property_service.add_building(
                actor.id, property_id=efh.id, building_number="1",
            )
            property_service.add_unit(
                actor.id, building_id=haus.id, property_id=efh.id,
                unit_type="APARTMENT", unit_number="Einfamilienhaus",
            )
            property_service.add_party_role(
                actor.id, property_id=efh.id, party_id=borm.id,
                role="PROPERTY_OWNER", valid_from=self.heute - timedelta(days=900),
            )
            self._ok("Rolle PROPERTY_OWNER: Peter Borm")
        else:
            self._skip(f"Liegenschaft {EFH_OBJEKT}")

        efh_unit = Unit.objects.filter(property_id=efh.id).first()
        if efh_unit is not None and not Occupancy.objects.filter(
            unit_id=efh_unit.id
        ).exists():
            belegung_service.create_belegung(
                actor.id, unit_id=efh_unit.id, occupancy_type="OWNER_OCCUPIED",
                valid_from=self.heute - timedelta(days=900),
                mieter=[{"party_id": borm.id, "role": "OWNER_OCCUPANT"}],
            )
            self._ok("Belegung EFH: Peter Borm (selbst genutzt)")

        if not TechnicalAsset.objects.filter(property_id=efh.id).exists():
            anlage_service.create_asset(actor.id, efh.id, {
                "name": "Gastherme Bad/OG",
                "asset_type": "THERME",
                "supply_type": "DEZENTRAL",   # <- der Gegensatz zur Zentralanlage
                "energy_source": "GAS",
                "manufacturer": "Vaillant",
                "model": "ecoTEC plus VC 20 CS/1-5",
                "year_built": 2019,
                "power_kw": Decimal("20.00"),
                "building_id": efh_unit.building_id if efh_unit else None,
                "unit_id": efh_unit.id if efh_unit else None,
                "location_note": "Hauswirtschaftsraum EG, rechts neben der Tür.",
                "note": "Etagentherme (dezentral) — kein Kessel im Keller.",
            })
            self._ok("Anlage: Gastherme (DEZENTRAL, Vaillant, Bj. 2019, 20 kW)")

    # =====================================================================
    # Belegschaft
    # =====================================================================
    def _belegschaft(self, actor):
        self._kapitel("Belegschaft")
        for emp in BELEGSCHAFT:
            name = f"{emp['vorname']} {emp['nachname']}"
            person = self._person(
                actor, emp["vorname"], emp["nachname"], anrede=emp["anrede"],
                mail=self._login_mail(emp),
            )
            if Employee.objects.filter(party_id=person.id).exists():
                self._skip(f"Mitarbeiter {name}")
                continue
            account = self._app_user(emp["key"], f"{name} (Login)")
            wg = (
                WageGroup.objects.filter(name=emp["lohngruppe"]).first()
                if emp["lohngruppe"] else None
            )
            employee = mitarbeiter_service.create_employee(
                actor.id, app_user_id=account.id, party_id=person.id,
                hired_on=emp["seit"],
            )
            mitarbeiter_service.create_contract(
                actor.id, employee_id=employee.id, valid_from=emp["seit"],
                hours=VOLLZEIT, vacation_days_per_year=emp["urlaub"],
                wage_group_id=wg.id if wg else None,
            )
            mitarbeiter_service.set_vacation_budget(
                actor.id, employee_id=employee.id, year=self.heute.year,
                entitlement_days=emp["urlaub"],
            )
            self._ok(
                f"Mitarbeiter {employee.employee_number} — {name} "
                f"({emp['rolle']}{', ' + emp['lohngruppe'] if emp['lohngruppe'] else ''})"
            )

    def _login_mail(self, emp):
        umlaute = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
        vor = emp["vorname"].lower().translate(umlaute)
        nach = emp["nachname"].lower().translate(umlaute).replace(" ", "")
        return f"{vor}.{nach}@{MAIL_DOMAIN}"

    def _logins(self):
        """Login-Konten + Rollenzuordnung. Passwörter nur bewusst."""
        self._kapitel("Logins und Rollen")
        User = get_user_model()
        setze_passwort = settings.DEBUG or self.mit_passwoertern
        if setze_passwort:
            self.stdout.write(
                self.style.WARNING(
                    f"  ! Die Demo-Logins bekommen das Passwort {DEMO_PASSWORD!r} "
                    f"(aus MCN_SZENARIO_PASSWORD). Das ist ein Vorführungs-Zugang "
                    f"— NICHT für den Produktivbetrieb, und in KEINE Datei "
                    f"schreiben."
                )
            )
        elif not settings.DEBUG:
            self.stdout.write(
                self.style.WARNING(
                    "  ! Ohne DEBUG und ohne --mit-passwoertern bekommen die Logins "
                    "KEIN nutzbares Passwort (unusable_password) — niemand kommt "
                    "durch den Login. Für die Vorführung: "
                    "manage.py seed_szenario --mit-passwoertern"
                )
            )

        for emp in BELEGSCHAFT:
            email = self._login_mail(emp)
            app_user_id = SEED_UUID[emp["key"]]
            ist_admin = emp["rolle"] == "ADMINISTRATION"
            user = User.objects.filter(email__iexact=email).first()
            if user is None:
                user = User(
                    username=email, email=email, app_user_id=app_user_id,
                    is_staff=ist_admin, is_superuser=ist_admin,
                )
                if setze_passwort:
                    user.set_password(DEMO_PASSWORD)
                else:
                    user.set_unusable_password()
                user.save()
                self._ok(f"Login {email} ({emp['rolle']})")
            elif setze_passwort and not user.has_usable_password():
                # Heilt den Fall „ohne DEBUG angelegt, danach mit Flag nachgezogen".
                user.set_password(DEMO_PASSWORD)
                user.save(update_fields=["password"])
                self._ok(f"Login {email}: Passwort nachgetragen")
            else:
                self._skip(f"Login {email}")

            if self._rolle_zuweisen(app_user_id, emp["rolle"]):
                self._ok(f"Rolle {emp['rolle']} → {email}")
            else:
                self.uebersprungen += 1

    def _rolle_zuweisen(self, app_user_id, role_code):
        aktiv = (
            UserRole.objects.filter(
                user_id=app_user_id, role_id=role_code, valid_from__lte=self.heute
            )
            .filter(Q(valid_until__isnull=True) | Q(valid_until__gt=self.heute))
            .exists()
        )
        if aktiv:
            return False
        rechte_service.assign_role(
            SEED_UUID["system"], user_id=app_user_id, role_code=role_code,
            valid_from=self.heute,
        )
        return True

    # =====================================================================
    # Hilfen für die Szenarien
    # =====================================================================
    def _zeit(self, tag, stunde, minute=0):
        """Ein Zeitpunkt in Betriebszeit (Europe/Berlin) — nie in UTC rechnen."""
        return datetime(tag.year, tag.month, tag.day, stunde, minute,
                        tzinfo=BETRIEBS_TZ)

    def _monteur(self, key):
        return AppUser.objects.get(id=SEED_UUID[key])

    def _auftrag_aufsetzen(self, actor, *, prop, title, auftraggeber,
                           empfaenger=None, scope="COMMON_PROPERTY",
                           evidence=None, bis="IN_AUSFUEHRUNG", **kwargs):
        """Auftrag anlegen und über die echten Tore hochschalten.

        `empfaenger` trennt „wer zahlt" (INVOICE_DEBTOR) von „wer den Beleg
        bekommt" (INVOICE_RECIPIENT) — bei der WEG ist das der springende Punkt.
        """
        order = auftrag_service.create_work_order(
            actor.id, property_id=prop.id, title=title, **kwargs
        )
        auftrag_service.set_order_evidence(
            actor.id, work_order_id=order.id,
            reference=evidence or "Telefonische Beauftragung",
        )
        auftrag_service.confirm_responsibility(
            actor.id, work_order_id=order.id, scope=scope
        )
        auftrag_service.add_work_order_party(
            actor.id, work_order_id=order.id, party_id=auftraggeber.id,
            role="PRINCIPAL", is_primary=True, source="OWNERSHIP",
        )
        auftrag_service.add_work_order_party(
            actor.id, work_order_id=order.id, party_id=auftraggeber.id,
            role="INVOICE_DEBTOR", is_primary=True, source="BILLING_INSTRUCTION",
        )
        auftrag_service.add_work_order_party(
            actor.id, work_order_id=order.id,
            party_id=(empfaenger or auftraggeber).id,
            role="INVOICE_RECIPIENT", is_primary=True,
            source="MANDATE" if empfaenger else "BILLING_INSTRUCTION",
        )
        kette = ["FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG",
                 "TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"]
        for status in kette[: kette.index(bis) + 1]:
            auftrag_service.advance_status(
                actor.id, work_order_id=order.id, to_status=status
            )
        order.refresh_from_db()
        return order

    def _signieren(self, actor, bericht, *, name, seed):
        """Besiegelt einen Bericht mit der Kundenunterschrift — best effort.

        Die Unterschrift landet im Objektspeicher (MinIO); ist er nicht
        erreichbar, bleibt der Bericht im ENTWURF. Das wird **gemeldet**, nicht
        umgangen: `sign_report` ist der einzige Weg zu UNTERZEICHNET, und ein per
        UPDATE gesetzter Status wäre eine Lüge (kein Unterschriftsbild, kein
        Siegel). Ein späterer Lauf holt es nach — deshalb ist dieser Schritt
        wiederholbar.
        """
        bericht.refresh_from_db()
        if bericht.status == "UNTERZEICHNET":
            return True
        try:
            report_service.sign_report(
                actor.id, report_id=bericht.id, signed_by_name=name,
                signature_png=_unterschrift_png(seed=seed),
            )
        except Exception as exc:
            self._warn(
                f"Bericht {bericht.id} NICHT unterzeichnet: {exc} — er bleibt im "
                f"ENTWURF. Ursache ist fast immer der Objektspeicher (MinIO, "
                f"MCN_MINIO_*). MinIO starten und `seed_szenario` erneut laufen "
                f"lassen: Der Seed holt die Unterschrift (und die davon abhängige "
                f"Abrechnung) nach."
            )
            return False
        bericht.refresh_from_db()
        self._ok(f"Bericht UNTERZEICHNET ({name}) — ab jetzt versiegelt")
        return True

    def _beleg_beteiligte(self, actor, invoice, *, schuldner, empfaenger=None):
        beleg_service.add_invoice_party(
            actor.id, invoice_id=invoice.id, party_id=schuldner.id,
            role="INVOICE_DEBTOR", is_primary=True,
        )
        beleg_service.add_invoice_party(
            actor.id, invoice_id=invoice.id,
            party_id=(empfaenger or schuldner).id,
            role="INVOICE_RECIPIENT", is_primary=True,
        )

    # =====================================================================
    # A) Wartungsvertrag Zentralheizung — die Fälligkeit, die den Auftrag macht
    # =====================================================================
    def _szenario_a(self, actor):
        self._kapitel("Szenario A — Wartungsvertrag Zentralheizung")
        weg_obj = Property.objects.get(name=WEG_OBJEKT)
        weg_party = Party.objects.get(display_name=WEG_NAME)
        name = "Wartungsvertrag Zentralheizung (jährlich)"
        if MaintenanceContract.objects.filter(name=name).exists():
            self._skip(name)
            return
        vertrag = wartung_service.create_contract(
            actor.id, property_id=weg_obj.id, name=name,
            # Fällig in 8 Tagen — „nächste Woche", relativ zum Vorführungstag.
            start_date=self.heute + timedelta(days=8),
            interval_kind="JAEHRLICH",
            due_action="AUFTRAG",          # die Fälligkeit erzeugt den Auftrag
            party_id=weg_party.id,
            lead_time_days=30,             # Vorlauf 30 Tage → jetzt sichtbar
            notes=(
                "Jahreswartung der Zentralheizung (Viessmann Vitocrossal 300) "
                "inkl. Abgasmessung und Sicherheitsprüfung. Vereinbart mit der "
                "WEG, Ansprechpartner ist die Verwaltung Stegos."
            ),
        )
        self._ok(
            f"Wartungsvertrag {vertrag.contract_number} — nächste Fälligkeit "
            f"{vertrag.next_due_date:%d.%m.%Y} (Aktion: AUFTRAG)"
        )
        self.stdout.write(
            "    Der Vertrag wird bewusst NICHT ausgelöst: In der Vorführung "
            "klickt der Chef die Fälligkeit an, und das System erzeugt den Auftrag."
        )

    # =====================================================================
    # B) Legionellenprüfung — die Frist, die man vergisst
    # =====================================================================
    def _szenario_b(self, actor):
        self._kapitel("Szenario B — Legionellenprüfung (TrinkwV)")
        weg_obj = Property.objects.get(name=WEG_OBJEKT)
        speicher = TechnicalAsset.objects.filter(
            property_id=weg_obj.id, asset_type="TRINKWASSER"
        ).first()
        art = InspectionType.objects.filter(
            name="Trinkwasser: Legionellenprüfung"
        ).first()
        if art is None:
            self._warn(
                "Prüfart 'Trinkwasser: Legionellenprüfung' fehlt — Szenario B "
                "nicht aufgebaut."
            )
            return
        if Inspection.objects.filter(
            property_id=weg_obj.id, inspection_type_id=art.id
        ).exists():
            self._skip("Prüffrist Legionellen")
        else:
            p = pruefung_service.create_inspection(
                actor.id, inspection_type_id=art.id, property_id=weg_obj.id,
                asset_id=speicher.id if speicher else None,
                # Die letzte Probenahme liegt drei Jahre + 6 Tage zurück: die
                # Frist ist SEIT SECHS TAGEN abgelaufen. Genau der Fall, der teuer
                # wird — und den kein Kalender im Büro gefangen hat.
                start_date=self.heute - timedelta(days=6),
                interval_kind="TAGE",
                interval_days=1095,        # 3-Jahres-Frist (vermietetes Objekt)
                lead_time_days=60,
                responsibility="Zugelassenes Untersuchungslabor / Probenehmer",
                notes=(
                    "Zentrale Trinkwassererwärmung, vermietetes Objekt → "
                    "3-Jahres-Frist. Intervall und Pflicht prüft der Betrieb "
                    "selbst; das Produkt gibt keine Rechtsauskunft."
                ),
            )
            self._ok(
                f"Prüffrist: {p.name} — fällig {p.next_due_date:%d.%m.%Y} "
                f"(ÜBERFÄLLIG)"
            )

        # Eine zweite, harmlose Frist — damit die Liste nicht nur rot ist.
        art2 = InspectionType.objects.filter(
            name="Schornsteinfeger / Feuerstättenschau"
        ).first()
        if art2 is not None and not Inspection.objects.filter(
            property_id=weg_obj.id, inspection_type_id=art2.id
        ).exists():
            p2 = pruefung_service.create_inspection(
                actor.id, inspection_type_id=art2.id, property_id=weg_obj.id,
                start_date=self.heute + timedelta(days=21),
                notes="Termin laut Feuerstättenbescheid. Keine Rechtsauskunft.",
            )
            self._ok(f"Prüffrist: {p2.name} — fällig {p2.next_due_date:%d.%m.%Y}")

    # =====================================================================
    # C) Havarie Rohrbruch — die ganze Kette, Abrechnung in REGIE
    # =====================================================================
    def _szenario_c(self, actor):
        self._kapitel("Szenario C — Havarie Rohrbruch 1. OG links (Musili)")
        titel = "Havarie: Rohrbruch Steigleitung 1. OG links"
        weg_obj = Property.objects.get(name=WEG_OBJEKT)
        weg_party = Party.objects.get(display_name=WEG_NAME)
        stegos = Party.objects.get(display_name=STEGOS)
        musili = Party.objects.get(display_name="Familie Musili")
        vorgestern = self.heute - timedelta(days=2)

        # Dieses Szenario ist in drei wiederaufsetzbaren Stufen gebaut
        # (Baustelle → Unterschrift → Abrechnung). Grund: Die Regie-Abrechnung
        # rechnet **nur unterzeichnete** Berichte ab (`_berichtspositionen`) — ohne
        # Objektspeicher gäbe es sonst eine Rechnung, der das ganze MATERIAL fehlt.
        order = WorkOrder.objects.filter(title=titel).first()
        if order is not None:
            self._skip(f"{titel} (Baustelle)")
            bericht = SiteReport.objects.filter(work_order_id=order.id).first()
            self._szenario_c_abrechnen(
                actor, order, bericht, weg_party=weg_party, stegos=stegos
            )
            return

        # 1) Der Anruf → Vorgang (Schnellerfassung).
        vorgang = projekt_service.create_service_case(
            actor.id, property_id=weg_obj.id,
            subject="Wasseraustritt 1. OG links — Rohrbruch Steigleitung",
            description=(
                "Anruf Frau Musili, 1. OG links: Wasser tritt aus der Wand hinter "
                "dem Bad aus, läuft ins Treppenhaus. Hauptabsperrung durch den "
                "Hausmeister geschlossen."
            ),
            reported_by_party_id=musili.id,
            priority="NOTFALL",
        )
        self._ok(f"Vorgang {vorgang.case_number} — Rohrbruch gemeldet (NOTFALL)")

        # 2) Der Auftrag. Auftraggeber ist die WEG (Steigleitung =
        #    Gemeinschaftseigentum), den Beleg bekommt die Verwaltung.
        order = self._auftrag_aufsetzen(
            actor, prop=weg_obj, title=titel,
            auftraggeber=weg_party, empfaenger=stegos,
            scope="COMMON_PROPERTY",
            evidence="Notfallbeauftragung durch die Verwaltung Stegos (telefonisch)",
            service_case_id=vorgang.id,
            description=(
                "Steigleitung Kaltwasser im Bereich 1. OG links gebrochen. "
                "Leitung freilegen, schadhaften Abschnitt erneuern, Wand schließen."
            ),
            priority="NOTFALL", is_emergency=True,
            bis="IN_AUSFUEHRUNG",
        )
        abrechnung_service.set_billing_mode(
            actor.id, work_order_id=order.id, billing_mode="REGIE"
        )
        self._ok(f"Auftrag {order.order_number} — REGIE (Zeit + Material)")

        # 3) Der Monteur auf der Plantafel.
        kat = AppointmentCategory.objects.filter(name="Störung / Notdienst").first()
        job = einsatz_service.create_service_job(
            actor.id, work_order_id=order.id,
            scheduled_start=self._zeit(vorgestern, 7, 30),
            scheduled_end=self._zeit(vorgestern, 13, 0),
            on_site_contact_party_id=musili.id,
            access_instructions=(
                "Frau Musili ist zu Hause. Hauptabsperrung im Heizungskeller "
                "(Schlüssel Nr. 4, Zugang über den Hof)."
            ),
            appointment_category_id=kat.id if kat else None,
        )
        murat = self._monteur("murat")
        einsatz_service.assign_user(
            actor.id, service_job_id=job.id, assignee_user_id=murat.id, role="LEAD"
        )
        # Die Zeiten werden VOR dem Abschluss gestempelt: nach Einsatzabschluss
        # verlangt B-28 eine Begründung (Korrekturfenster), nach kaufmännischer
        # Prüfung ist die Erfassung ganz gesperrt.
        for status in ("GEPLANT", "BESTAETIGT", "UNTERWEGS", "VOR_ORT"):
            einsatz_service.advance_status(
                actor.id, service_job_id=job.id, to_status=status
            )
        self._ok(f"Einsatz {job.job_number} — Murat Emektar, {vorgestern:%d.%m.}")

        # 4) Zeit stempeln (auf den Monteur, nicht auf den Seed-Akteur).
        einsatz_service.log_time(
            actor.id, service_job_id=job.id, user_id=murat.id,
            time_type="FAHRTZEIT",
            started_at=self._zeit(vorgestern, 7, 30),
            ended_at=self._zeit(vorgestern, 8, 0),
        )
        einsatz_service.log_time(
            actor.id, service_job_id=job.id, user_id=murat.id,
            time_type="ARBEITSZEIT",
            started_at=self._zeit(vorgestern, 8, 0),
            ended_at=self._zeit(vorgestern, 12, 30),
            note="Wand geöffnet, Steigleitung auf 6 m erneuert, Dichtheitsprobe.",
        )
        einsatz_service.advance_status(
            actor.id, service_job_id=job.id, to_status="ABGESCHLOSSEN"
        )
        self._ok("Zeiten gestempelt: 0,5 h Fahrt + 4,5 h Arbeit (Murat)")

        # 5) Der Baustellenbericht — mit Positionen (Mengen, NIEMALS Preise).
        bericht = report_service.create_report(
            actor.id, work_order_id=order.id, service_job_id=job.id,
            report_date=vorgestern,
            activity_text=(
                "Vorwand im Bad 1. OG links geöffnet. Kaltwasser-Steigleitung "
                "(Kupfer 15 mm) auf 6 m Länge erneuert, 8 Pressverbindungen "
                "gesetzt, mit Schallschutzschellen befestigt. Dichtheitsprobe "
                "bestanden, Anlage gespült und in Betrieb genommen. Wandöffnung "
                "provisorisch verschlossen."
            ),
            weather="trocken, 19 °C",
            hours_worked=Decimal("4.50"),
            remarks=(
                "Der Trockenbau (Wand schließen, verputzen) ist NICHT Bestandteil "
                "dieses Einsatzes und wird gesondert beauftragt."
            ),
        )
        rohr = Article.objects.get(article_number="SHK-1010")
        fitting = Article.objects.get(article_number="SHK-1011")
        schelle = Article.objects.get(article_number="SHK-1012")
        report_service.set_report_lines(
            actor.id, report_id=bericht.id,
            lines=[
                {"line_type": "MATERIAL", "source_article_id": rohr.id,
                 "quantity": Decimal("6.000")},
                {"line_type": "MATERIAL", "source_article_id": fitting.id,
                 "quantity": Decimal("8.000")},
                {"line_type": "MATERIAL", "source_article_id": schelle.id,
                 "quantity": Decimal("4.000")},
                {"line_type": "TEXT",
                 "description": "Wandöffnung ca. 0,4 m² — Verschluss durch den "
                                "Trockenbauer der WEG."},
            ],
        )
        self._ok("Baustellenbericht: 3 Materialpositionen (Mengen, keine Preise)")

        self._szenario_c_abrechnen(
            actor, order, bericht, weg_party=weg_party, stegos=stegos
        )

    def _szenario_c_abrechnen(self, actor, order, bericht, *, weg_party, stegos):
        """Unterschrift → Regie-Rechnung → Zahlung → Vier-Augen-Antrag.

        Wiederaufsetzbar: Solange der Bericht nicht unterzeichnet ist, wird
        **nicht** fakturiert. Eine Regie-Rechnung ohne die Berichtspositionen wäre
        um das gesamte Material zu niedrig — und sähe trotzdem plausibel aus. Das
        ist genau der Fehler, den dieses Produkt verhindern soll; er darf im Seed
        erst recht nicht entstehen.
        """
        if bericht is None:
            self._warn("Szenario C: kein Baustellenbericht gefunden — Abbruch.")
            return
        # 6) Die Unterschrift der Mieterin — besiegelt den Bericht unwiderruflich.
        if not self._signieren(actor, bericht, name="Familie Musili", seed=1):
            self._warn(
                "Szenario C: Die Regie-Rechnung wurde NICHT erzeugt, weil der "
                "Bericht unsigniert ist. Sie enthielte nur die Zeiten und KEIN "
                "Material (`abrechnung._berichtspositionen` rechnet ausschließlich "
                "unterzeichnete Berichte ab) — eine plausibel aussehende, viel zu "
                "niedrige Rechnung."
            )
            return
        if Invoice.objects.filter(work_order_id=order.id).exists():
            self._skip("Szenario C: Regie-Rechnung")
            return

        # 7) Abrechnung in REGIE: Berichtspositionen + Zeitbuchungen.
        order.refresh_from_db()
        kette = ["TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"]
        for status in kette[kette.index(order.status) + 1:] if order.status in kette else kette:
            auftrag_service.advance_status(
                actor.id, work_order_id=order.id, to_status=status
            )
        try:
            rechnung = abrechnung_service.rechnung_aus_auftrag(
                actor.id, work_order_id=order.id, tax_code="DE_19",
                invoice_date=self.heute - timedelta(days=1),
                payment_term_days=14,
            )
        except abrechnung_service.PreisUnbekannt as exc:
            self._warn(
                "Regie-Abrechnung (C) hängt in der Preisklärung: "
                f"{[p.get('bezeichnung') for p in exc.positionen]}. Rechnung "
                "NICHT erzeugt — der Seed rät keine Preise."
            )
            return
        self._beleg_beteiligte(
            actor, rechnung, schuldner=weg_party, empfaenger=stegos
        )
        beleg_service.publish_invoice(actor.id, invoice_id=rechnung.id)
        rechnung.refresh_from_db()
        self._ok(
            f"Rechnung {rechnung.invoice_number} (REGIE: Material + Zeit) — "
            f"{rechnung.gross_total} EUR, Schuldner WEG, Beleg an Stegos"
        )

        # Bezahlt — die Havarie ist durch.
        buchhaltung_service.record_payment(
            actor.id, invoice_id=rechnung.id, amount=rechnung.gross_total,
            paid_at=self.heute - timedelta(days=1), payment_type="ZAHLUNG",
        )
        self._ok("Zahlungseingang verbucht (vollständig)")

        # 8) Vier-Augen: Robin BEANTRAGT eine Kulanz-Gutschrift, Patrick
        #    entscheidet — in der Vorführung, live. Der Antrag bleibt offen.
        lohn_pos = next(
            (
                l.position_number for l in rechnung.lines.all()
                if l.line_type == "ARBEITSZEIT"
            ),
            None,
        ) or max(l.position_number for l in rechnung.lines.all())
        robin = self._monteur("robin")
        vier_augen_service.request_approval(
            robin.id,
            action_code="RECHNUNGSKORREKTUR",
            payload={"operation": "GUTSCHRIFT", "positions": [lohn_pos]},
            target_table="invoicing.invoice",
            target_id=rechnung.id,
            reason=(
                "Kulanz gegenüber der WEG: Die Arbeitszeit des Notdiensts wird "
                "gutgeschrieben (Zusage von Herrn van Dalen am Telefon). "
                "Bitte um Freigabe."
            ),
        )
        self._ok(
            "Vier-Augen: Robin Paul (Buchhaltung) beantragt eine Gutschrift — "
            "OFFEN, Patrick van Dalen entscheidet"
        )

    # =====================================================================
    # D) Thermostatventile — Pauschal-Angebot, Soll-Ist mit MEHRVERBRAUCH
    # =====================================================================
    def _szenario_d(self, actor):
        self._kapitel("Szenario D — Thermostatventile (Soll-Ist)")
        titel = "Thermostatventile tauschen — alle 6 Einheiten"
        vorhanden = WorkOrder.objects.filter(title=titel).first()
        if vorhanden is not None:
            self._skip(titel)
            # Wiederaufsetzen: Unterschrift + Abrechnung nachholen, falls der
            # Objektspeicher beim ersten Lauf nicht stand. Der Nachtrag rechnet nur
            # aus dem UNTERZEICHNETEN Bericht — ohne Unterschrift gäbe es keine
            # Mehrmenge und `rechnung_aus_nachtrag` fände „nichts nachzutragen".
            self._szenario_d_abrechnen(actor, vorhanden)
            return
        weg_obj = Property.objects.get(name=WEG_OBJEKT)
        weg_party = Party.objects.get(display_name=WEG_NAME)
        stegos = Party.objects.get(display_name=STEGOS)
        heizung = TechnicalAsset.objects.filter(
            property_id=weg_obj.id, asset_type="HEIZUNG"
        ).first()
        gestern = self.heute - timedelta(days=1)

        order = self._auftrag_aufsetzen(
            actor, prop=weg_obj, title=titel,
            auftraggeber=weg_party, empfaenger=stegos,
            scope="COMMON_PROPERTY",
            evidence="Beschluss der Eigentümerversammlung (TOP 6)",
            description=(
                "Thermostatventile in allen sechs Wohnungen erneuern "
                "(hydraulischer Abgleich der Zentralheizung)."
            ),
            asset_id=heizung.id if heizung else None,
            bis="IN_AUSFUEHRUNG",
        )
        # billing_mode bleibt PAUSCHAL: Die Rechnung IST die Angebotskopie.
        self._ok(f"Auftrag {order.order_number} — PAUSCHAL")

        ventil = Article.objects.get(article_number="SHK-1001")
        kopf = Article.objects.get(article_number="SHK-1002")
        leistung = Assembly.objects.get(assembly_number="LEI-3001")

        # Angebot: 18 Heizkörper (3 je Wohnung), pauschal.
        angebot = beleg_service.create_quote(
            actor.id, property_id=weg_obj.id,
            title="Thermostatventile Badensche Straße 53 (6 Einheiten)",
            work_order_id=order.id,          # <- macht das Angebot zum SOLL
            quote_date=self.heute - timedelta(days=25),
            valid_until_date=self.heute + timedelta(days=5),
            lines=[
                {"line_type": "TEXT",
                 "description": "Erneuerung der Thermostatventile in allen sechs "
                                "Wohnungen, 3 Heizkörper je Wohnung."},
                {"line_type": "MATERIAL",
                 "description": ventil.description,
                 "source_article_id": ventil.id,
                 "quantity": "18", "unit": "Stk", "unit_price": "35.53",
                 "unit_cost": "24.50", "tax_code": "DE_19"},
                {"line_type": "MATERIAL",
                 "description": kopf.description,
                 "source_article_id": kopf.id,
                 "quantity": "18", "unit": "Stk", "unit_price": "27.41",
                 "unit_cost": "18.90", "tax_code": "DE_19"},
                {"line_type": "ARBEITSZEIT",
                 "description": leistung.name,
                 "source_assembly_id": leistung.id,
                 "quantity": "18", "unit": "Stk", "unit_price": "24.17",
                 "tax_code": "DE_19"},
                {"line_type": "FAHRT",
                 "description": "An- und Abfahrt Servicefahrzeug",
                 "quantity": "2", "unit": "Fahrt", "unit_price": "45.00",
                 "tax_code": "DE_19"},
            ],
        )
        beleg_service.send_quote(actor.id, quote_id=angebot.id)
        angebot.refresh_from_db()
        self._ok(
            f"Angebot {angebot.quote_number} versendet — {angebot.gross_total} EUR "
            f"(Soll: 18 Ventile)"
        )
        self.stdout.write(
            "    Hinweis: Das Angebot bleibt VERSENDET. Einen Produktpfad "
            "'Angebot annehmen' (→ ANGENOMMEN) gibt es heute nicht (Befund)."
        )

        # Ausführung: der Monteur findet in einer Wohnung EINEN Heizkörper mehr.
        kat = AppointmentCategory.objects.filter(name="Umsetzung").first()
        job = einsatz_service.create_service_job(
            actor.id, work_order_id=order.id,
            scheduled_start=self._zeit(gestern, 8, 0),
            scheduled_end=self._zeit(gestern, 16, 0),
            access_instructions="Mieter sind informiert; Reihenfolge EG → 2. OG.",
            appointment_category_id=kat.id if kat else None,
        )
        julian = self._monteur("julian")
        einsatz_service.assign_user(
            actor.id, service_job_id=job.id, assignee_user_id=julian.id, role="LEAD"
        )
        for status in ("GEPLANT", "BESTAETIGT", "UNTERWEGS", "VOR_ORT"):
            einsatz_service.advance_status(
                actor.id, service_job_id=job.id, to_status=status
            )
        einsatz_service.log_time(
            actor.id, service_job_id=job.id, user_id=julian.id,
            time_type="ARBEITSZEIT",
            started_at=self._zeit(gestern, 8, 0),
            ended_at=self._zeit(gestern, 16, 0),
            note="Alle Wohnungen abgearbeitet, Anlage entlüftet.",
        )
        einsatz_service.advance_status(
            actor.id, service_job_id=job.id, to_status="ABGESCHLOSSEN"
        )
        self._ok(f"Einsatz {job.job_number} — Julian Hoffmann, {gestern:%d.%m.}")

        bericht = report_service.create_report(
            actor.id, work_order_id=order.id, service_job_id=job.id,
            report_date=gestern,
            activity_text=(
                "Thermostatventile in allen sechs Wohnungen erneuert. In der "
                "Wohnung 2. OG rechts (Familie Kutzi) hängt ein zusätzlicher "
                "Heizkörper im Bad, der im Angebot nicht enthalten war — er wurde "
                "mit erneuert."
            ),
            hours_worked=Decimal("8.00"),
        )
        # Der Bericht startet MIT DEM ANGEBOT als Soll — der Monteur korrigiert
        # nur die Abweichung.
        report_service.vorbelegen_aus_angebot(
            actor.id, report_id=bericht.id, quote_id=angebot.id
        )
        vorbelegt = list(report_service.list_report_lines(bericht.id))
        self._ok(
            f"Bericht aus Angebot vorbelegt: {len(vorbelegt)} Positionen "
            f"(Ist = Soll)"
        )

        # Die Ist-Korrektur: ein Heizkörper mehr → 19 statt 18.
        neue_zeilen = []
        for l in vorbelegt:
            menge = l.quantity
            notiz = l.note
            if l.source_article_id in (ventil.id, kopf.id) or (
                l.source_assembly_id == leistung.id
            ):
                menge = (l.quantity or Decimal("0")) + Decimal("1.000")
                notiz = "Zusätzlicher Heizkörper im Bad 2. OG rechts (Kutzi)."
            neue_zeilen.append({
                "line_type": l.line_type,
                "description": l.description,
                "unit": l.unit,
                "quantity": menge,
                "note": notiz,
                "source_article_id": l.source_article_id,
                "source_assembly_id": l.source_assembly_id,
                "source_quote_line_id": l.source_quote_line_id,
            })
        report_service.set_report_lines(
            actor.id, report_id=bericht.id, lines=neue_zeilen
        )
        self._ok("Ist-Menge korrigiert: 19 statt 18 (ein Heizkörper mehr)")

        self._szenario_d_abrechnen(actor, order)

    def _szenario_d_abrechnen(self, actor, order):
        """Unterschrift → Pauschalrechnung → Nachtrag (gebunden). Wiederaufsetzbar.

        Der Nachtrag läuft über `rechnung_aus_nachtrag`, NICHT über eine
        Handbuchung: nur dieser Weg legt die `billing_link` auf die Berichtszeilen
        und macht die Mehrmenge damit **physisch** nicht erneut abrechenbar. Eine
        handgebuchte Rechnung sähe für den Menschen gleich aus, ließe den
        Mehrverbrauch aus Produktsicht aber „noch offen" — ein Klick auf „Nachtrag
        abrechnen" in der Vorführung buchte ihn ein zweites Mal. Genau die
        Doppelabrechnung, die das System verhindert.
        """
        weg_party = Party.objects.get(display_name=WEG_NAME)
        stegos = Party.objects.get(display_name=STEGOS)
        angebot = Quote.objects.filter(work_order_id=order.id).order_by(
            "created_at"
        ).first()
        ventil = Article.objects.get(article_number="SHK-1001")
        kopf = Article.objects.get(article_number="SHK-1002")
        leistung = Assembly.objects.get(assembly_number="LEI-3001")

        bericht = SiteReport.objects.filter(work_order_id=order.id).first()
        if bericht is not None and bericht.status == "ENTWURF":
            self._signieren(
                actor, bericht,
                name="Stegos Immobilien GmbH (Hausverwaltung)", seed=2,
            )
        bericht.refresh_from_db() if bericht else None

        # Soll-Ist zur Anzeige (auch wenn noch nicht signiert — dann vorläufig).
        abgleich = report_service.soll_ist(order.id)
        mehr = [
            z for z in abgleich["positionen"]
            if z["art"] == report_service.MEHRVERBRAUCH
        ]
        if mehr:
            # Anzeige, kein Anlegen: nicht mitzählen (sonst „1 angelegt" auf jedem
            # sonst idempotenten Wiederholungslauf).
            self.stdout.write(
                "    Soll-Ist: MEHRVERBRAUCH bei "
                + ", ".join(f"{z['bezeichnung']} (+{z['differenz']})" for z in mehr)
            )
        else:
            self._warn(
                "Soll-Ist zeigt KEINEN Mehrverbrauch — Szenario D trägt nicht."
            )

        # Ohne unterzeichneten Bericht rechnet der Nachtrag nichts ab (Ist = 0 aus
        # Abrechnungssicht). Dann keine Belege erzeugen — der nächste Lauf mit
        # Objektspeicher holt es nach.
        if bericht is None or bericht.status != "UNTERZEICHNET":
            self._warn(
                "Szenario D: Der Bericht ist nicht unterzeichnet — Pauschal- und "
                "Nachtragsrechnung wurden NICHT erzeugt. Ursache ist fast immer der "
                "Objektspeicher (MinIO). `seed_szenario` mit erreichbarem MinIO "
                "erneut laufen lassen: die Abrechnung wird nachgeholt."
            )
            return

        # Pauschalrechnung (Angebotskopie). Idempotent über den Angebotsbezug.
        kette = ["TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"]
        for status in (
            kette[kette.index(order.status) + 1:]
            if order.status in kette else kette
        ):
            auftrag_service.advance_status(
                actor.id, work_order_id=order.id, to_status=status
            )
            order.refresh_from_db()
        pauschal = Invoice.objects.filter(
            work_order_id=order.id, invoice_type="RECHNUNG"
        ).filter(billing_links__source_kind="ANGEBOTSPOSITION").first()
        if pauschal is None:
            pauschal = abrechnung_service.rechnung_aus_angebot(
                actor.id, quote_id=angebot.id,
                invoice_date=self.heute, payment_term_days=14,
                discount_percent=Decimal("2.00"), discount_days=7,
            )
            self._beleg_beteiligte(actor, pauschal, schuldner=weg_party,
                                   empfaenger=stegos)
            beleg_service.publish_invoice(actor.id, invoice_id=pauschal.id)
            pauschal.refresh_from_db()
            self._ok(
                f"Rechnung {pauschal.invoice_number} (Pauschale aus Angebot) — "
                f"{pauschal.gross_total} EUR, 2 % Skonto binnen 7 Tagen"
            )
        else:
            self._skip(f"Pauschalrechnung {pauschal.invoice_number}")

        # Der Nachtrag über die Abweichung — gebunden über rechnung_aus_nachtrag.
        vorschau = abrechnung_service.nachtrag_vorschau(order.id)
        if not vorschau["positionen"]:
            self._skip("Szenario D: Nachtragsrechnung (nichts mehr offen)")
            return

        # Die Preise stehen im Stamm (VK-Gruppen), der Server bepreist die Mehrmenge
        # selbst. Fehlt einer Position der Server-VK, verlangt der Nachtrag die
        # Klärung (PreisUnbekannt) — dann reichen wir denselben Preis wie in der
        # Angebotszeile über `preise=` nach (ein genannter Preis ist nur zulässig,
        # wo der Server keinen hat).
        preis_fallback = {
            str(ventil.id): "35.53",
            str(kopf.id): "27.41",
            str(leistung.id): "24.17",
        }
        try:
            nachtrag = abrechnung_service.rechnung_aus_nachtrag(
                actor.id, work_order_id=order.id, tax_code="DE_19",
                invoice_date=self.heute, payment_term_days=14,
            )
        except abrechnung_service.PreisUnbekannt as exc:
            preise = {}
            for pos in exc.positionen:
                quelle_id = pos["quelle_id"]
                treffer = next(
                    (p for ref, p in preis_fallback.items() if ref in quelle_id),
                    None,
                )
                if treffer is None:
                    self._warn(
                        f"Szenario D: Für die Abweichung „{pos['bezeichnung']}“ "
                        f"steht kein Preis fest ({pos['grund']}) und kein Fallback "
                        f"greift — Nachtrag NICHT erzeugt."
                    )
                    return
                preise[quelle_id] = treffer
            nachtrag = abrechnung_service.rechnung_aus_nachtrag(
                actor.id, work_order_id=order.id, tax_code="DE_19",
                preise=preise, invoice_date=self.heute, payment_term_days=14,
            )
            self._ok("Nachtrag: Preise über die Klärung nachgereicht (preise=)")
        self._beleg_beteiligte(actor, nachtrag, schuldner=weg_party,
                               empfaenger=stegos)
        beleg_service.publish_invoice(actor.id, invoice_id=nachtrag.id)
        nachtrag.refresh_from_db()
        gebunden = abrechnung_service.bindungen(nachtrag.id)
        self._ok(
            f"Nachtragsrechnung {nachtrag.invoice_number} — "
            f"{nachtrag.gross_total} EUR, {len(gebunden)} Berichtsposition(en) "
            f"gebunden (Mehrmenge physisch gesperrt)"
        )
        # Gegenprobe: ein zweiter Nachtragslauf darf NICHTS mehr finden.
        nach = abrechnung_service.nachtrag_vorschau(order.id)
        if nach["positionen"]:
            self._warn(
                "Szenario D: Nach dem Nachtrag meldet die Vorschau noch offene "
                f"Positionen ({len(nach['positionen'])}) — die Bindung greift "
                "nicht. Das wäre eine Doppelabrechnungslücke."
            )
        else:
            self._ok(
                "Gegenprobe: die Nachtrags-Vorschau ist jetzt leer — nichts mehr "
                "offen, kein zweites Mal abrechenbar"
            )

    # =====================================================================
    # E) Badsanierung — Abschlag, Schlussrechnung mit Anrechnung, § 35a
    # =====================================================================
    def _szenario_e(self, actor):
        self._kapitel("Szenario E — Badsanierung Peter Borm")
        titel = "Badsanierung Erdgeschoss (komplett)"
        if WorkOrder.objects.filter(title=titel).exists():
            self._skip(titel)
            return
        efh = Property.objects.get(name=EFH_OBJEKT)
        borm = Party.objects.get(display_name="Peter Borm")

        order = self._auftrag_aufsetzen(
            actor, prop=efh, title=titel, auftraggeber=borm,
            scope="PRIVATE_UNIT",
            evidence="Auftragsbestätigung des Eigentümers vom Angebot",
            description=(
                "Bad im Erdgeschoss komplett erneuern: Rückbau, Rohinstallation, "
                "Fliesen, Sanitärobjekte, Duschabtrennung."
            ),
            bis="FREIGEGEBEN",   # AR/TR sind ab FREIGEGEBEN stellbar (B-08)
        )
        self._ok(f"Auftrag {order.order_number} — Badsanierung (FREIGEGEBEN)")

        waschtisch = Article.objects.get(article_number="SHK-1030")
        dusche = Article.objects.get(article_number="SHK-1031")

        # Das Angebot — der Kernprozess beginnt hier.
        angebot = beleg_service.create_quote(
            actor.id, property_id=efh.id,
            title="Badsanierung Ringelnatzstraße 22",
            work_order_id=order.id,
            quote_date=self.heute - timedelta(days=40),
            valid_until_date=self.heute + timedelta(days=20),
            lines=[
                {"line_type": "TEXT",
                 "description": "Komplettsanierung des Bades im Erdgeschoss "
                                "(ca. 8 m²), inkl. Entsorgung."},
                {"line_type": "ARBEITSZEIT",
                 "description": "Rückbau und Entsorgung Altbad",
                 "quantity": "16", "unit": "h", "unit_price": "58.00",
                 "tax_code": "DE_19"},
                {"line_type": "ARBEITSZEIT",
                 "description": "Rohinstallation Wasser und Abwasser",
                 "quantity": "24", "unit": "h", "unit_price": "58.00",
                 "tax_code": "DE_19"},
                {"line_type": "ARBEITSZEIT",
                 "description": "Fliesen- und Montagearbeiten",
                 "quantity": "32", "unit": "h", "unit_price": "58.00",
                 "tax_code": "DE_19"},
                {"line_type": "MATERIAL",
                 "description": waschtisch.description,
                 "source_article_id": waschtisch.id,
                 "quantity": "1", "unit": "Stk", "unit_price": "419.05",
                 "unit_cost": "289.00", "tax_code": "DE_19"},
                {"line_type": "MATERIAL",
                 "description": dusche.description,
                 "source_article_id": dusche.id,
                 "quantity": "1", "unit": "Stk", "unit_price": "462.55",
                 "unit_cost": "319.00", "tax_code": "DE_19"},
                {"line_type": "MATERIAL",
                 "description": "Fliesen, Abdichtung, Kleinmaterial",
                 "quantity": "1", "unit": "psch", "unit_price": "2400.00",
                 "tax_code": "DE_19"},
                {"line_type": "MATERIAL",
                 "description": "Duschabtrennung Glas, Maßanfertigung",
                 "quantity": "1", "unit": "Stk", "unit_price": "980.00",
                 "tax_code": "DE_19", "line_kind": "ALTERNATIV"},
            ],
        )
        beleg_service.send_quote(actor.id, quote_id=angebot.id)
        angebot.refresh_from_db()
        self._ok(
            f"Angebot {angebot.quote_number} versendet — {angebot.gross_total} EUR "
            f"(inkl. Alternativposition)"
        )

        # Der Abschlag: 40 % der Angebotssumme. Der Arbeitskostenanteil MUSS
        # ausdrücklich stehen — eine PAUSCHALE-Zeile ohne ihn machte den
        # § 35a-Ausweis der ganzen Rechnung unbestimmbar (der Kunde verlöre 20 %
        # Steuerbonus auf den Lohnanteil).
        lohn_netto = Decimal("4176.00")     # 72 h x 58,00 EUR
        netto_gesamt = Decimal("7461.60")   # Summe der NORMAL-Positionen
        ab_netto = (netto_gesamt * Decimal("0.40")).quantize(Decimal("0.01"))
        ab_lohn = (lohn_netto * Decimal("0.40")).quantize(Decimal("0.01"))
        abschlag = beleg_service.create_invoice(
            actor.id, property_id=efh.id, invoice_type="ABSCHLAGSRECHNUNG",
            work_order_id=order.id,
            invoice_date=self.heute - timedelta(days=30),
            due_date=self.heute - timedelta(days=16),
            payment_term_days=14,
            show_labour_costs=True,          # Privatkunde → § 35a ausweisen
            lines=[
                {"line_type": "PAUSCHALE",
                 "description": (
                     f"1. Abschlag (40 %) gemäß Angebot {angebot.quote_number} — "
                     f"Rückbau und Rohinstallation"
                 ),
                 "quantity": "1", "unit": "psch",
                 "unit_price": str(ab_netto),
                 "labour_net_amount": str(ab_lohn),
                 "tax_code": "DE_19"},
            ],
        )
        self._beleg_beteiligte(actor, abschlag, schuldner=borm)
        beleg_service.publish_invoice(actor.id, invoice_id=abschlag.id)
        abschlag.refresh_from_db()
        buchhaltung_service.record_payment(
            actor.id, invoice_id=abschlag.id, amount=abschlag.gross_total,
            paid_at=self.heute - timedelta(days=18), payment_type="ZAHLUNG",
        )
        self._ok(
            f"Abschlagsrechnung {abschlag.invoice_number} — "
            f"{abschlag.gross_total} EUR, bezahlt"
        )

        # Ausführung abschließen, dann die Schlussrechnung MIT Anrechnung.
        for status in ("IN_PLANUNG", "IN_AUSFUEHRUNG", "TECHNISCH_ABGESCHLOSSEN",
                       "KAUFMAENNISCH_GEPRUEFT"):
            auftrag_service.advance_status(
                actor.id, work_order_id=order.id, to_status=status
            )
        schluss = beleg_service.create_invoice(
            actor.id, property_id=efh.id, invoice_type="SCHLUSSRECHNUNG",
            work_order_id=order.id,
            invoice_date=self.heute, payment_term_days=14,
            show_labour_costs=True,
            advance_invoice_ids=[abschlag.id],   # → negative Anrechnungspositionen
            lines=[
                {"line_type": "ARBEITSZEIT",
                 "description": "Rückbau und Entsorgung Altbad",
                 "quantity": "16", "unit": "h", "unit_price": "58.00",
                 "tax_code": "DE_19"},
                {"line_type": "ARBEITSZEIT",
                 "description": "Rohinstallation Wasser und Abwasser",
                 "quantity": "24", "unit": "h", "unit_price": "58.00",
                 "tax_code": "DE_19"},
                {"line_type": "ARBEITSZEIT",
                 "description": "Fliesen- und Montagearbeiten",
                 "quantity": "32", "unit": "h", "unit_price": "58.00",
                 "tax_code": "DE_19"},
                {"line_type": "MATERIAL", "description": waschtisch.description,
                 "source_article_id": waschtisch.id,
                 "quantity": "1", "unit": "Stk", "unit_price": "419.05",
                 "unit_cost": "289.00", "tax_code": "DE_19"},
                {"line_type": "MATERIAL", "description": dusche.description,
                 "source_article_id": dusche.id,
                 "quantity": "1", "unit": "Stk", "unit_price": "462.55",
                 "unit_cost": "319.00", "tax_code": "DE_19"},
                {"line_type": "MATERIAL",
                 "description": "Fliesen, Abdichtung, Kleinmaterial",
                 "quantity": "1", "unit": "psch", "unit_price": "2400.00",
                 "tax_code": "DE_19"},
            ],
        )
        self._beleg_beteiligte(actor, schluss, schuldner=borm)
        beleg_service.publish_invoice(actor.id, invoice_id=schluss.id)
        schluss.refresh_from_db()
        lohn = beleg_service.arbeitskosten(schluss)
        self._ok(
            f"Schlussrechnung {schluss.invoice_number} — Zahlbetrag "
            f"{schluss.gross_total} EUR (Anrechnung des Abschlags ist enthalten)"
        )
        if lohn["bestimmbar"]:
            self._ok(
                f"§ 35a-Ausweis: {lohn['gross_amount']} EUR Arbeitskosten "
                f"(brutto) — der Steuerbonus des Privatkunden"
            )
        else:
            self._warn(
                f"§ 35a-Ausweis der Schlussrechnung ist NICHT bestimmbar "
                f"(Grund {lohn['grund']}, offen: {lohn['offen']})."
            )
        self.stdout.write(
            "    ZUGFeRD/Factur-X entsteht beim Abruf "
            "(GET /invoicing/invoices/{id}/zugferd.pdf) — im Seed ist nichts "
            "vorzuhalten."
        )

    # =====================================================================
    # F) Heizungsstörung — Notdienst, Rechnung bleibt offen
    # =====================================================================
    def _szenario_f(self, actor):
        self._kapitel("Szenario F — Heizungsstörung (Notdienst, offener Posten)")
        titel = "Störung: Therme ohne Warmwasser (Notdienst)"
        if WorkOrder.objects.filter(title=titel).exists():
            self._skip(titel)
            return
        efh = Property.objects.get(name=EFH_OBJEKT)
        borm = Party.objects.get(display_name="Peter Borm")
        therme = TechnicalAsset.objects.filter(
            property_id=efh.id, asset_type="THERME"
        ).first()
        # Der Einsatz liegt fünf Wochen zurück; die Rechnung ist seit drei
        # Wochen fällig und wurde nie bezahlt.
        einsatztag = self.heute - timedelta(days=35)

        order = self._auftrag_aufsetzen(
            actor, prop=efh, title=titel, auftraggeber=borm,
            scope="PRIVATE_UNIT",
            evidence="Notdienst-Anruf des Eigentümers (Samstag, 19:40 Uhr)",
            description=(
                "Vaillant ecoTEC geht auf Störung F28 (keine Zündung). "
                "Kein Warmwasser, keine Heizung."
            ),
            asset_id=therme.id if therme else None,
            priority="NOTFALL", is_emergency=True,
            bis="IN_AUSFUEHRUNG",
        )
        kat = AppointmentCategory.objects.filter(name="Störung / Notdienst").first()
        job = einsatz_service.create_service_job(
            actor.id, work_order_id=order.id,
            scheduled_start=self._zeit(einsatztag, 20, 0),
            scheduled_end=self._zeit(einsatztag, 21, 30),
            on_site_contact_party_id=borm.id,
            access_instructions="Therme im Hauswirtschaftsraum EG (dezentral).",
            appointment_category_id=kat.id if kat else None,
        )
        rojhat = self._monteur("rojhat")
        einsatz_service.assign_user(
            actor.id, service_job_id=job.id, assignee_user_id=rojhat.id, role="LEAD"
        )
        for status in ("GEPLANT", "BESTAETIGT", "UNTERWEGS", "VOR_ORT"):
            einsatz_service.advance_status(
                actor.id, service_job_id=job.id, to_status=status
            )
        einsatz_service.log_time(
            actor.id, service_job_id=job.id, user_id=rojhat.id,
            time_type="ARBEITSZEIT",
            started_at=self._zeit(einsatztag, 20, 0),
            ended_at=self._zeit(einsatztag, 21, 30),
            note="Zündelektrode verrußt und gerissen — getauscht, Therme läuft.",
        )
        einsatz_service.advance_status(
            actor.id, service_job_id=job.id, to_status="ABGESCHLOSSEN"
        )
        self._ok(
            f"Einsatz {job.job_number} — Rojhat Beyaz, {einsatztag:%d.%m.} 20:00 "
            f"(Notdienst)"
        )

        elektrode = Article.objects.get(article_number="SHK-1020")
        for status in ("TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"):
            auftrag_service.advance_status(
                actor.id, work_order_id=order.id, to_status=status
            )
        rechnung = beleg_service.create_invoice(
            actor.id, property_id=efh.id, invoice_type="RECHNUNG",
            work_order_id=order.id,
            invoice_date=einsatztag + timedelta(days=1),
            due_date=self.heute - timedelta(days=21),   # überfällig seit 3 Wochen
            payment_term_days=14,
            show_labour_costs=True,
            lines=[
                {"line_type": "ARBEITSZEIT",
                 "description": "Störungsbehebung Gastherme (Notdienst)",
                 "quantity": "1.5", "unit": "h", "unit_price": "58.00",
                 "tax_code": "DE_19"},
                {"line_type": "ZUSCHLAG",
                 "description": "Notdienstpauschale (außerhalb der Geschäftszeit)",
                 "quantity": "1", "unit": "Einsatz", "unit_price": "95.00",
                 "labour_net_amount": "95.00",   # reiner Lohnzuschlag → § 35a
                 "tax_code": "DE_19"},
                {"line_type": "MATERIAL", "description": elektrode.description,
                 "source_article_id": elektrode.id,
                 "quantity": "1", "unit": "Stk", "unit_price": "60.90",
                 "unit_cost": "42.00", "tax_code": "DE_19"},
                {"line_type": "FAHRT",
                 "description": "An- und Abfahrt Servicefahrzeug",
                 "quantity": "1", "unit": "Fahrt", "unit_price": "45.00",
                 "tax_code": "DE_19"},
            ],
        )
        self._beleg_beteiligte(actor, rechnung, schuldner=borm)
        beleg_service.publish_invoice(actor.id, invoice_id=rechnung.id)
        rechnung.refresh_from_db()
        self._ok(
            f"Rechnung {rechnung.invoice_number} — {rechnung.gross_total} EUR, "
            f"fällig am {rechnung.due_date:%d.%m.%Y}, UNBEZAHLT "
            f"(überfällig seit 21 Tagen)"
        )
        self.stdout.write(
            "    Es wurde bewusst KEINE Mahnung erzeugt: Der Mahnlauf "
            "(/buchhaltung/mahnlauf) wird in der Vorführung live gefahren."
        )

    # =====================================================================
    # Fälligkeiten erzeugen (Engine) + Aufgaben
    # =====================================================================
    def _faelligkeiten(self, actor):
        self._kapitel("Fälligkeiten-Engine")
        ergebnis = faelligkeit_service.generiere(actor.id, stichtag=self.heute)
        neu = {k: len(v) for k, v in ergebnis.items() if v}
        if neu:
            self.angelegt += sum(neu.values())
            self.stdout.write(
                "  + Fälligkeiten erzeugt: "
                + ", ".join(f"{k} {n}" for k, n in neu.items())
            )
        else:
            self._skip("Fälligkeiten (bereits erzeugt)")
        offen = faelligkeit_service.liste(status="OFFEN", stichtag=self.heute)
        for item in offen:
            zustand = "ÜBERFÄLLIG" if item.due_date < self.heute else "offen"
            self.stdout.write(
                f"    · {item.kind}: {item.title} — {item.due_date:%d.%m.%Y} "
                f"({zustand})"
            )

        self._kapitel("Aufgaben")
        for titel, beschreibung in [
            (
                "Legionellenprüfung beauftragen (Labor)",
                "Frist ist abgelaufen. Probenehmer beauftragen und Termin mit "
                "der Verwaltung Stegos abstimmen.",
            ),
            (
                "Zahlungserinnerung Peter Borm",
                "Rechnung Notdienst ist seit drei Wochen fällig. Vor dem Mahnlauf "
                "kurz anrufen.",
            ),
        ]:
            if Task.objects.filter(title=titel).exists():
                self.uebersprungen += 1
                continue
            aufgabe_service.create_task(
                actor.id, title=titel, description=beschreibung,
                due_date=self.heute + timedelta(days=2),
            )
            self._ok(f"Aufgabe: {titel}")
