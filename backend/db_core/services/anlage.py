"""Technische Anlagen — `property.technical_asset` (Therme, Heizung, Aufzug …).

Die Anlage ist das **technische Herz der Liegenschaft**: An ihr hängen die
Prüfungen (`maintenance.inspection.asset_id`), die Aufträge
(`workflow.work_order.asset_id`) und die Vorgänge — und an ihr entscheidet sich,
was der Monteur wissen muss, bevor er losfährt („Heizkörper kalt" ist eine ganz
andere Aufgabe, wenn es eine **zentrale** Anlage ist).

Fachlicher Hintergrund und die DB-Invarianten stehen im Modulkopf von
`db_core/migrations/0101_anlage_stammdaten_und_schutz.py`. Kurz:

* **Echte Spalten, echte CHECKs.** Anlagenart, Status, Versorgung, Hersteller,
  Modell, Seriennummer, Baujahr, Energieträger, Leistung sind Spalten — kein
  Freitext-JSON. Die Ersatzteilsuche von morgen (DATANORM: Vaillant,
  Junkers/Bosch) sucht über Hersteller + Modell; ein JSON-Schlüssel, in den jeder
  alles schreiben darf, trägt das nicht. Die Codelisten stehen als CHECK **in der
  DB** — dieser Service prüft sie nur vor, damit daraus ein 422 wird und kein 500.
* **Kein Löschen.** `status = 'INAKTIV'` legt still; die Zeile bleibt lesbar
  (Aufträge und Berichte von damals zeigen weiter auf sie). Es gibt hier keine
  Löschfunktion — und seit 0101 verbietet es zusätzlich der **No-Delete-Trigger**.
  *Was im Service sitzt, ist umgehbar; erst was im Trigger sitzt, hält.*
* **`power_kw = None` heißt unbekannt, nie 0 kW.** 0 kW hieße „heizt nicht" —
  dieselbe Haltung wie beim fehlenden Einkaufspreis und beim fehlenden U-Wert.
* **`supply_type = 'UNBEKANNT'` ist ein echter Wert.** Nicht erfasst heißt nicht
  „dezentral".

`attributes` bleibt für echte Zusatzfakten ohne eigenes Feld (Anlagenbuchnummer
des Verwalters o. Ä.); dieser Service fasst es nicht an.

Geschrieben wird ausschließlich über `db_core.db_context.business_transaction`.
"""
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import DataError, IntegrityError
from django.db.models import Q

from db_core.db_context import business_transaction
from db_core.models import (
    DueItem,
    Inspection,
    MaintenanceContract,
    MaintenanceContractAsset,
    Property,
    TechnicalAsset,
    WorkOrder,
)
from db_core.services._validation import ensure_exists, ensure_standort

# ---------------------------------------------------------------------------
# Codelisten — deckungsgleich mit den CHECKs aus Migration 0101.
# Die DB ist die Wahrheit; diese Tupel sind die Vorabprüfung (422 statt 500).
# Ein Paritätstest fährt sie gegen die DB.
# ---------------------------------------------------------------------------

# SHK-Anlagenarten des Betriebs (Migration 0112 löste die allgemeine
# Gebäudetechnik-Liste aus 0101 ab). Reihenfolge = Anzeigereihenfolge im UI.
ASSET_TYPES = (
    "THERME_HEIZUNG",
    "THERME_COMBI",
    "ERDWAERMEPUMPE",
    "FERNWAERMESTATION",
    "KESSEL_HEIZUNG",
    "KESSEL_COMBI",
    "HEBEANLAGE",
    "SOLARANLAGE",
    "SONSTIGE",
)

#: Zentral oder dezentral — **die Kernfrage des Slices.**
SUPPLY_TYPES = ("ZENTRAL", "DEZENTRAL", "UNBEKANNT")

ENERGY_SOURCES = (
    "GAS",
    "OEL",
    "FERNWAERME",
    "STROM",
    "PELLET",
    "HOLZ",
    "SOLAR",
    "UMWELTWAERME",
    "SONSTIGE",
)

STATUS = ("AKTIV", "INAKTIV")

#: Freitextspalten. Leerer String wird zu None (= nicht erfasst).
TEXT_FELDER = ("manufacturer", "model", "serial_number", "location_note", "note")

#: Alles, was von außen gesetzt werden darf. `property_id` steht bewusst NICHT
#: drin: Die Liegenschaft kommt aus der Route und ist unveränderlich.
SETZBAR = (
    "name",
    "asset_type",
    "status",
    "supply_type",
    "energy_source",
    "year_built",
    "power_kw",
    "building_id",
    "unit_id",
) + TEXT_FELDER

#: Untergrenze Baujahr (DB-CHECK: 1850–2100). Gebäudetechnik älter als 1850 gibt
#: es nicht; ein Tippfehler („19" statt „1990") soll nicht durchgehen.
BAUJAHR_MIN = 1850

#: numeric(7, 2) mit CHECK > 0.
LEISTUNG_MAX = Decimal("99999.99")


# ---------------------------------------------------------------------------
# Validierung
# ---------------------------------------------------------------------------

def _text(wert, feld, max_len=200):
    if wert is None:
        return None
    if not isinstance(wert, str):
        raise ValueError(f"{feld} muss Text sein.")
    wert = wert.strip()
    if not wert:
        # Leerer Text ist kein Wert (DB-CHECK btrim(...) <> '') — er löscht ihn.
        return None
    if len(wert) > max_len:
        raise ValueError(f"{feld} ist zu lang (höchstens {max_len} Zeichen).")
    return wert


def _codeliste(wert, feld, erlaubt):
    if wert is None:
        return None
    if not isinstance(wert, str) or wert.strip().upper() not in erlaubt:
        raise ValueError(
            f"Ungültiger Wert für {feld}: '{wert}'. Erlaubt: {', '.join(erlaubt)}."
        )
    return wert.strip().upper()


def _baujahr(wert):
    """Baujahr als vierstellige Jahreszahl. Kein „ungefähr" — leer oder echt."""
    if wert is None or wert == "":
        return None
    try:
        jahr = int(wert)
    except (TypeError, ValueError) as exc:
        raise ValueError("year_built muss eine Jahreszahl sein (z. B. 1998).") from exc
    # +1, weil eine Anlage im Dezember mit dem Baujahr des Folgejahres geliefert
    # werden kann. Der Service zieht damit eine ENGERE Grenze als der DB-CHECK
    # (1850–2100) — bewusst: Die DB zieht die äußere Grenze, die nie wandert, der
    # Service die fachliche, die jedes Jahr mitläuft.
    obergrenze = date.today().year + 1
    if not (BAUJAHR_MIN <= jahr <= obergrenze):
        raise ValueError(
            f"year_built muss zwischen {BAUJAHR_MIN} und {obergrenze} liegen."
        )
    return jahr


def _leistung(wert):
    """Nennleistung in kW. **0 ist keine Leistung** — 0 kW hieße „heizt nicht"."""
    if wert is None or wert == "":
        return None
    try:
        zahl = Decimal(str(wert))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("power_kw muss eine Zahl sein (z. B. 24.5).") from exc
    if zahl <= 0:
        raise ValueError(
            "power_kw muss größer als 0 sein; eine unbekannte Leistung bleibt leer."
        )
    if zahl > LEISTUNG_MAX:
        raise ValueError(f"power_kw darf höchstens {LEISTUNG_MAX} kW sein.")
    return zahl.quantize(Decimal("0.01"))


def _pruefe(daten):
    """Rohen (Teil-)Payload prüfen → normalisierte Werte (PATCH-Semantik).

    Unbekannte Schlüssel werden abgewiesen, statt sie stillschweigend zu
    schlucken: Ein Tippfehler im Feldnamen wäre sonst ein Datenverlust, den
    niemand bemerkt.
    """
    unbekannt = sorted(set(daten) - set(SETZBAR))
    if unbekannt:
        raise ValueError(f"Unbekannte Felder: {', '.join(unbekannt)}.")

    werte = {}
    if "name" in daten:
        name = _text(daten["name"], "name")
        if name is None:
            raise ValueError("name darf nicht leer sein.")
        werte["name"] = name

    # NOT NULL in der DB: ein ausdrückliches null ist hier kein „Feld leeren".
    for feld, erlaubt in (
        ("asset_type", ASSET_TYPES),
        ("status", STATUS),
        ("supply_type", SUPPLY_TYPES),
    ):
        if feld in daten:
            wert = _codeliste(daten[feld], feld, erlaubt)
            if wert is None:
                raise ValueError(
                    f"{feld} ist ein Pflichtfeld und darf nicht leer sein."
                )
            werte[feld] = wert

    if "energy_source" in daten:
        werte["energy_source"] = _codeliste(
            daten["energy_source"], "energy_source", ENERGY_SOURCES
        )
    for feld in TEXT_FELDER:
        if feld in daten:
            grenze = 2000 if feld == "note" else 200
            werte[feld] = _text(daten[feld], feld, grenze)
    if "year_built" in daten:
        werte["year_built"] = _baujahr(daten["year_built"])
    if "power_kw" in daten:
        werte["power_kw"] = _leistung(daten["power_kw"])
    for feld in ("building_id", "unit_id"):
        if feld in daten:
            werte[feld] = daten[feld]
    return werte


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------

def list_assets(property_id, mit_inaktiven=False):
    """Anlagen einer Liegenschaft, sortiert (Art, Name)."""
    qs = TechnicalAsset.objects.filter(property_id=property_id)
    if not mit_inaktiven:
        qs = qs.filter(status="AKTIV")
    return list(qs.order_by("asset_type", "name", "id"))


def get_asset(asset_id):
    """Eine Anlage, auch eine stillgelegte (sonst wäre sie nicht reaktivierbar)."""
    return TechnicalAsset.objects.filter(id=asset_id).first()


def _vertraege_der_anlage(asset):
    """Wartungsverträge zu einer Anlage — mit ihrem Bezug (siehe `bezuege`).

    Zwei Abfragen, keine Schleife: erst die Verträge des Objekts, dann in EINEM
    Zug alle Anlagen-Zuordnungen dieser Verträge. Daraus ergibt sich je Vertrag,
    ob er diese Anlage nennt (`ANLAGE`), gar keine nennt (`LIEGENSCHAFT`) oder
    nur andere nennt (fällt raus).
    """
    vertraege = list(
        MaintenanceContract.objects.filter(property_id=asset.property_id)
        .exclude(status="ARCHIVIERT")
        .order_by("next_due_date", "contract_number")
    )
    if not vertraege:
        return []

    hat_zuordnung = set()
    deckt_diese = set()
    for contract_id, asset_id in MaintenanceContractAsset.objects.filter(
        contract_id__in=[v.id for v in vertraege], active=True
    ).values_list("contract_id", "asset_id"):
        hat_zuordnung.add(contract_id)
        if asset_id == asset.id:
            deckt_diese.add(contract_id)

    treffer = []
    for v in vertraege:
        if v.id in deckt_diese:
            treffer.append({"contract": v, "bezug": "ANLAGE"})
        elif v.id not in hat_zuordnung:
            treffer.append({"contract": v, "bezug": "LIEGENSCHAFT"})
    # Verträge MIT ausdrücklichem Bezug zuerst — sie sind die Antwort auf die
    # Frage; die objektweiten sind der Kontext dahinter.
    treffer.sort(key=lambda t: 0 if t["bezug"] == "ANLAGE" else 1)
    return treffer


def bezuege(asset, *, maintenance=False, workflow=False):
    """Was an dieser Anlage hängt — **je Baustein einzeln getort**.

    `maintenance` und `workflow` sind die Rechte des Aufrufers auf die jeweiligen
    **Module**. Fehlt eines, fehlt der **Baustein** (leere Liste, `*_sichtbar =
    False`) — nicht die Antwort. Genau das Muster von `api/dossier.py`.

    Warum das nötig ist (Review-Fund): Diese Funktion liefert Daten aus
    `maintenance` (Verträge, Prüfungen, Fälligkeiten) und `workflow` (Aufträge).
    Sie nur an `property/LESEN` zu hängen hieße: Wer die Liegenschaft sehen darf,
    sieht über die Anlage auch Module, die ihm der zuständige Endpunkt mit 403
    verweigert. Dass heute jede Rolle mit `property` auch `maintenance` hat, ist
    ein **Zufall der Rechtematrix** — und die nächste Matrixzeile macht daraus
    wieder ein Leck. Ein Endpunkt, dessen Dichtheit von einer zufälligen
    Eigenschaft der Rechtematrix abhängt, ist nicht dicht.

    Vier Bezüge, alle vier am Asset:

    * `pruefungen`    — `maintenance.inspection.asset_id`
    * `auftraege`     — `workflow.work_order.asset_id`
    * `faelligkeiten` — offene `maintenance.due_item` über den Prüfungs-Anker
      **und** über die Verträge, die genau diese Anlage abdecken (0135)
    * `wartungsvertraege` — seit 0135 mit echtem Anlagenbezug
      (`maintenance.contract_asset`, n:m). Geliefert werden zwei Sorten, und der
      Unterschied wird **ausgesprochen** statt eingeebnet (`bezug`):

      - `ANLAGE` — der Vertrag nennt diese Anlage ausdrücklich.
      - `LIEGENSCHAFT` — der Vertrag nennt **gar keine** Anlage und gilt damit
        wie eh und je fürs ganze Objekt (Bestandsverträge).

      Ein Vertrag, der ausdrücklich **andere** Anlagen abdeckt, erscheint hier
      **nicht** mehr. Genau der Fehlschluss („irgendein Vertrag gilt für dieses
      Haus, also ist meine Therme versorgt") war der Befund aus dem Praxistest.
    """
    pruefungen = (
        list(
            Inspection.objects.filter(asset_id=asset.id)
            .select_related("inspection_type")
            .order_by("next_due_date", "name")
        )
        if maintenance
        else []
    )
    vertraege = _vertraege_der_anlage(asset) if maintenance else []
    # Wartungs-Fälligkeiten zählen nur, wenn der Vertrag DIESE Anlage nennt —
    # ein objektweiter Vertrag hängt seine Fälligkeit nicht an eine bestimmte
    # Therme, und sie hier zu zeigen hieße wieder, Genauigkeit vorzutäuschen.
    vertrag_ids = [v["contract"].id for v in vertraege if v["bezug"] == "ANLAGE"]
    faelligkeiten = (
        list(
            DueItem.objects.filter(status="OFFEN")
            .filter(
                Q(inspection_id__in=[p.id for p in pruefungen])
                | Q(contract_id__in=vertrag_ids)
            )
            .order_by("due_date")
        )
        if maintenance
        else []
    )
    auftraege = (
        list(WorkOrder.objects.filter(asset_id=asset.id).order_by("-created_at"))
        if workflow
        else []
    )
    return {
        "pruefungen": pruefungen,
        "faelligkeiten": faelligkeiten,
        "wartungsvertraege": vertraege,
        "auftraege": auftraege,
        "maintenance_sichtbar": maintenance,
        "workflow_sichtbar": workflow,
    }


# ---------------------------------------------------------------------------
# Schreiben — es gibt bewusst KEIN `delete_asset` (siehe Modulkopf)
# ---------------------------------------------------------------------------

def _db_fehler(exc):
    """Constraint-Verletzung → klarer Fachfehler (422), nie ein 500.

    Die Vorabprüfungen decken den Normalfall ab; hier landen Rennfälle (die
    Einheit wurde zwischen Prüfung und INSERT verschoben) und alles, was jemals
    an der Vorabprüfung vorbeikommt. Die DB bleibt die letzte Instanz.
    """
    text = str(exc)
    if "technical_asset_power_check" in text:
        return ValueError("power_kw muss größer als 0 sein (0 kW ist keine Leistung).")
    if "technical_asset_year_check" in text:
        return ValueError("year_built liegt außerhalb des zulässigen Bereichs.")
    if "technical_asset_type_check" in text:
        return ValueError("Ungültige Anlagenart.")
    if "technical_asset_status_check" in text:
        return ValueError("Ungültiger Status.")
    if "technical_asset_supply_check" in text:
        return ValueError("Ungültige Versorgungsart.")
    if "technical_asset_energy_check" in text:
        return ValueError("Ungültiger Energieträger.")
    return ValueError(
        "Die Anlage konnte nicht gespeichert werden: Gebäude oder Einheit passen "
        "nicht zur Liegenschaft."
    )


def create_asset(actor_app_user_id, property_id, daten):
    """Legt eine technische Anlage an einer Liegenschaft an.

    `property_id` kommt aus der Route, **nie aus dem Payload** — sonst ließe sich
    eine Anlage an einer fremden Liegenschaft anlegen. Gebäude und Einheit müssen
    zu genau dieser Liegenschaft gehören (die DB erzwingt es über zusammengesetzte
    FKs; hier wird es vorab geprüft, damit daraus ein 422 wird und kein 500).
    """
    ensure_exists(Property, property_id, "Liegenschaft")
    werte = _pruefe(daten or {})
    if not werte.get("name"):
        raise ValueError("name ist ein Pflichtfeld.")
    if not werte.get("asset_type"):
        # Die DB verlangt es seit 0101 (NOT NULL) — und mit gutem Grund: Eine
        # Anlage, deren Art niemand kennt, hilft dem Monteur nicht.
        raise ValueError("asset_type ist ein Pflichtfeld.")
    # Rückgabewert übernehmen: Kam nur die Einheit, leitet `ensure_standort`
    # ihr Gebäude ab (Befund I11) — die DB verlangt es im zusammengesetzten FK.
    b, u = ensure_standort(
        property_id, werte.get("building_id"), werte.get("unit_id")
    )
    if u is not None:
        werte["building_id"] = b

    zeile = {"id": uuid.uuid4(), "property_id": property_id}
    zeile.update(werte)
    zeile.setdefault("status", "AKTIV")
    zeile.setdefault("supply_type", "UNBEKANNT")

    try:
        with business_transaction(actor_app_user_id):
            TechnicalAsset.objects.create(**zeile)
    except (IntegrityError, DataError) as exc:
        raise _db_fehler(exc) from exc
    return get_asset(zeile["id"])


def update_asset(actor_app_user_id, asset_id, daten):
    """Teil-Update (PATCH). Nur übergebene Felder werden geändert.

    Auch der **Statuswechsel** läuft hier durch (`status='INAKTIV'` = stilllegen).
    Die Liegenschaft der Anlage ist **unveränderlich**: Sie steht nicht in
    `SETZBAR` und wird nie überschrieben — eine Anlage wandert nicht in ein
    anderes Objekt, sie wird dort stillgelegt und hier neu erfasst. Sonst zeigten
    die Aufträge von gestern auf eine Anlage, die heute woanders steht.
    """
    asset = get_asset(asset_id)
    if asset is None:
        raise ValueError(f"Anlage {asset_id} existiert nicht")
    werte = _pruefe(daten or {})
    if not werte:
        return asset

    # Zielzustand prüfen, nicht den Payload: Wer nur `unit_id` setzt, muss sich an
    # dem `building_id` messen lassen, das die Anlage bereits trägt.
    ziel_building = werte.get("building_id", asset.building_id)
    ziel_unit = werte.get("unit_id", asset.unit_id)
    ziel_building, _ = ensure_standort(asset.property_id, ziel_building, ziel_unit)
    # Wer eine Einheit setzt, ohne das Gebäude mitzuschicken, bekommt es
    # abgeleitet — sonst schlüge der zusammengesetzte FK zu (Befund I11).
    if ziel_unit is not None and "building_id" not in werte:
        werte["building_id"] = ziel_building

    try:
        with business_transaction(actor_app_user_id):
            TechnicalAsset.objects.filter(id=asset_id).update(**werte)
    except (IntegrityError, DataError) as exc:
        raise _db_fehler(exc) from exc
    return get_asset(asset_id)
