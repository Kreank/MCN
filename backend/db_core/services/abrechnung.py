"""Abrechnungs-Service: aus Angebot bzw. Bericht + Zeiten wird eine Rechnung.

Deterministisch, ohne KI. Zwei Wege, je nach `work_order.billing_mode`
(Migration 0084):

* **PAUSCHAL** (Default) → `rechnung_aus_angebot`: Die Rechnung ist die
  **Angebotskopie**. Der Kunde hat *diesen* Preis akzeptiert. Erfasste Zeiten und
  Berichtspositionen sind **Nachweis**, kein Rechnungsposten — das Angebot enthält
  die Leistung bereits; sie zusätzlich zu fakturieren hieße, doppelt zu kassieren.
  Das Soll-Ist (0080) bleibt die **interne Nachkalkulation**.
* **PAUSCHAL, zweiter Weg** → `rechnung_aus_nachtrag`: Was **über** das Angebot
  hinaus geleistet wurde, ist mit der Pauschale **nicht** bezahlt. Der Nachtrag
  fakturiert genau die Abweichungen — MEHRVERBRAUCH mit der **Differenzmenge**,
  ZUSATZ mit der **vollen Menge** — und nichts sonst. Er ist zur Angebotskopie
  disjunkt; beide zusammen ergeben den vollen Anspruch. (Herleitung und die Frage,
  warum MINDERVERBRAUCH die Pauschale *nicht* mindert: siehe den Abschnitt
  „Nachtrag" weiter unten.)
* **REGIE** → `rechnung_aus_auftrag`: Die Rechnung entsteht aus dem **Ist**
  (unterzeichnete Berichte + Zeitbuchungen + am Einsatz gebuchtes Material).
  Einen Nachtrag gibt es hier nicht — das Ist ist bereits vollständig fakturiert.

  **Material am Einsatz** (`workflow.material_entry`, abrechenbar seit Migration
  0139) ist die vierte Quelle. Bis dahin war es ein Datensatz ohne Ausgang: Der
  Monteur hatte zwei „Material"-Formulare vor sich, und nur eines führte zu Geld.
  Weil sich dieselbe Sache über **beide** erfassen lässt, prüft
  `_regie_quellenkonflikt_pruefen` die Doppelerfassung an der Artikel-Identität —
  die vier UNIQUE-Indizes können sie per Konstruktion nicht sehen.

## Die drei Invarianten dieses Moduls

**1. Jede Geldzahl rechnet der Server.**
Der VK kommt aus der einen Rechenstelle (`aufschlagsmatrix.vk_vorschlag`), der
Stundensatz aus `pricing.wage_group.hourly_rate`. **Fehlt der EK, ist der VK
`null` = unbekannt — NIE 0.** Und weil eine 0 auch auf anderen Wegen entstehen
kann (Festpreis 0,00 €, VK-Gruppe auf einer 0-Basis, Lohnsatz 0,00 €/h — die
CHECKs erlauben überall `>= 0`), gilt hier **ein Preis ist erst ab > 0 ein
Preis** (`_ist_preis`). Eine Position ohne ermittelbaren Preis wird weder
mit 0,00 € abgerechnet noch stillschweigend weggelassen (eine zu niedrige
Rechnung, die plausibel aussieht, ist der schlimmere Fehler): Der Vorgang
scheitert mit **422 und einer strukturierten Klärungsliste** (`PreisUnbekannt`).

Ein Fehler ohne Ausweg wird irgendwann umgangen — deshalb nimmt **derselbe
Aufruf** explizite Einzelpreise entgegen (`preise`). Ein dort genannter Preis ist
die **bewusste Kalkulationsentscheidung eines Menschen**, kein geratener Wert.
Und er ist **nur zulässig, wo der Server keinen Preis hat**: Ein Preis für eine
Position, deren VK die Matrix kennt, wird **abgelehnt** (422) — sonst ließe sich
die eine Rechenstelle über den Umweg „Preis nennen" stillschweigend unterlaufen.
Gerechnet werden Zeilensumme, Steuer und Gesamt **weiterhin ausschließlich vom
Server**; genannt wird nur der Einzelpreis.

**2. Die Belegposition ist eine eingefrorene Kopie, kein Verweis.**
Die Übernahme kopiert Werte. Eine spätere Preisänderung im Stamm verfälscht die
Rechnung nicht — und beim Angebotsweg gilt zusätzlich: kopiert wird der **im
Angebot vereinbarte** Preis, nicht der heutige Listenpreis.

**Dieses Modul schreibt NIEMALS in `pricing.article`.** Der einzige Weg vom Beleg
in den Stamm ist der eigene, mit `pricing/AENDERN` getorte Vorgang
`POST /pricing/articles/{id}/stammdaten-uebernehmen`. Ein genannter Einzelpreis
gilt für **diesen** Beleg — sonst schriebe ein Abrechnungslauf den Stammdatensatz
um, den alle anderen Belege ebenfalls verwenden.

**3. Dieselbe Leistung kann physisch nicht zweimal abgerechnet werden.**
Die Garantie liegt in der **Datenbank** (`invoicing.billing_link`, vier partielle
UNIQUE-Indizes `WHERE released_at IS NULL`), nicht in diesem Service. Der Service
sperrt die Quellzeilen (`SELECT … FOR UPDATE`) und prüft vor — damit der
Normalfall ein sauberer 422 statt eines IntegrityError-500 wird. Der Index ist
die letzte Instanz, nicht die erste.

## Der fehlende Preis ist SCHON VORHER sichtbar

Der eigentliche Hebel gegen die Sackgasse: `offene_abrechnung` weist den
unbekannten Preis **sofort** aus (`preis_status`), nicht erst beim
Abrechnungslauf. Der Preis ist ja bereits unbekannt, sobald die Position im
Bericht landet — nicht erst, wenn jemand fakturieren will.

## Der Storno löst die Bindung — und nur er

Das ist der Grund für das ganze Bindungs-Design (Begründung im Kopf von
Migration 0084): Eine veröffentlichte Rechnungsposition ist unveränderlich
(B-21); ohne die Freigabe wären stornierte Stunden für immer verbrannt. Eine
**GUTSCHRIFT löst nichts** — sie ist eine Teilkorrektur, die Ursprungsrechnung
besteht weiter und fordert weiterhin Geld. Dieselbe Grenze zieht das Belegmodul
schon bei den Abschlägen (`beleg._gebundene_abschlaege` filtert auf
`_stornierte_belege()`).

Damit daraus keine Falle wird, sperrt das Belegmodul den zweideutigen Fall:
Eine Gutschrift, die den **vollen** Rechnungsbetrag ausschöpft, ist auf einer
gebundenen Rechnung **verboten** (`beleg._vollgutschrift_sperre_pruefen`) — sie
ist ein verkappter Storno und hinterließe eine Rechnung über 0 €, deren Leistung
nie wieder abrechenbar wäre. Teilgutschriften bleiben zulässig: Eine Kulanz heißt
nicht, dass nicht gearbeitet wurde.

## Tore, die scharf bleiben

**B-08** (RECHNUNG erst ab `KAUFMAENNISCH_GEPRUEFT`), Vier-Augen und die
GoBD-Festschreibung sind unberührt: Dieser Service legt Rechnungen im **ENTWURF**
an und geht durch `beleg.create_invoice`/`publish_invoice` wie ein Mensch. Es
gibt keinen Sonderweg an den Triggern vorbei.
"""
import uuid
from collections import OrderedDict
from decimal import InvalidOperation, ROUND_HALF_UP, Decimal

from django.utils import timezone as dj_timezone

from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import (
    Article,
    Assembly,
    AssemblyComponent,
    BillingLink,
    Employee,
    EmploymentContract,
    Invoice,
    InvoiceLine,
    MaterialEntry,
    Quote,
    QuoteLine,
    SiteReport,
    SiteReportLine,
    TaxCode,
    TimeEntry,
    WageGroup,
    WorkOrder,
)
from db_core.services import aufschlagsmatrix as matrix_service
from db_core.services import beleg as beleg_service
from db_core.services import site_report as report_service

# Quellarten der Bindung (Codeliste aus Migration 0084, erweitert um 0139).
BERICHTSPOSITION = "BERICHTSPOSITION"
ZEITBUCHUNG = "ZEITBUCHUNG"
ANGEBOTSPOSITION = "ANGEBOTSPOSITION"
# Am Einsatz gebuchtes Material (`workflow.material_entry`, Migration 0139). Bis
# dahin war es ein Datensatz ohne Ausgang: erfasst, aber nirgends abrechenbar.
MATERIALBUCHUNG = "MATERIALBUCHUNG"

# Klärungseinheiten (das, wofür ein Mensch einen Preis nennen kann). Die
# Zeitbuchung ist NICHT die Klärungseinheit: Abgerechnet wird je **Zeitgruppe**
# (Lohngruppe — oder, wenn keine hinterlegt ist, der Mitarbeiter). Ein Preis je
# einzelner Stempelung wäre weder bedienbar noch fachlich sinnvoll.
QUELLE_BERICHTSPOSITION = "BERICHTSPOSITION"
QUELLE_ZEITGRUPPE = "ZEITGRUPPE"
# Die Materialbuchung IST ihre eigene Klärungseinheit (anders als die
# Zeitbuchung): Sie trägt Bezeichnung, Menge und Einheit und wird als **eine**
# Rechnungsposition abgerechnet — ein Preis je Buchung ist also genau die Einheit,
# in der ein Mensch entscheiden kann.
QUELLE_MATERIALBUCHUNG = "MATERIALBUCHUNG"
# Beim Nachtrag ist die Klärungseinheit die **Abweichungsposition** des Soll-Ist
# (ihr Schlüssel, z. B. `ARTIKEL:<uuid>:stk`) — nicht die einzelne Berichtszeile:
# Der Mehrverbrauch entsteht aus der Summe über alle Berichte des Auftrags.
QUELLE_ABWEICHUNG = "ABWEICHUNG"

# Gründe, aus denen der Server keinen Preis hat (maschinenlesbar fürs UI).
GRUND_EK_FEHLT = "EK_FEHLT"
GRUND_KEINE_VK_REGEL = "KEINE_VK_REGEL"
GRUND_KEINE_HERKUNFT = "KEINE_HERKUNFT"
GRUND_LEISTUNG_UNVOLLSTAENDIG = "LEISTUNG_UNVOLLSTAENDIG"
GRUND_LOHNGRUPPE_FEHLT = "LOHNGRUPPE_FEHLT"
# Die Materialbuchung nennt keinen Artikel — reiner Freitext („Dichtung aus dem
# Fahrzeug"). Das ist der **Bestand**: bis Migration 0139 konnte die Buchung gar
# keinen Artikel tragen. Sie hat damit keine Herkunft, aus der sich ein Preis
# ableiten ließe — und sie verschwindet trotzdem nicht: eigener Grund, eigene
# Klärungszeile, Preis vom Menschen. Bewusst NICHT `KEINE_HERKUNFT`: Der Weg
# heraus ist ein anderer (Artikel zuordnen bzw. Preis nennen, nicht „Artikel im
# Bericht pflegen"), und der Nutzer soll ihn im Text lesen.
GRUND_MATERIAL_OHNE_ARTIKEL = "MATERIAL_OHNE_ARTIKEL"
# Der Server *hat* eine Zahl — aber sie ist 0,00 €. Das ist **kein Preis**,
# sondern eine Lücke (siehe `_ist_preis`). Eigener Grund, damit der Nutzer den
# Unterschied zum fehlenden EK sieht: hier steht eine falsche Zahl im Stamm
# (0,00-EK aus dem Import, Festpreis 0,00, Aufschlag auf eine 0-Basis), dort
# fehlt sie ganz.
GRUND_VK_NULL = "VK_NULL"
GRUND_LOHNSATZ_NULL = "LOHNSATZ_NULL"

# Arten von Preis**vorschlägen**. Ein Vorschlag wird NIE automatisch gesetzt — er
# ist eine Lesehilfe für den Menschen, der entscheidet.
VORSCHLAG_LETZTER_PREIS = "LETZTER_PREIS"
VORSCHLAG_LISTENPREIS = "LISTENPREIS"
VORSCHLAG_LOHNGRUPPE = "LOHNGRUPPE"

PREIS_BEKANNT = "BEKANNT"
PREIS_UNBEKANNT = "UNBEKANNT"

PAUSCHAL = "PAUSCHAL"
REGIE = "REGIE"
BILLING_MODES = (PAUSCHAL, REGIE)

# Nur summenwirksame Angebotspositionen werden zur Rechnung. ALTERNATIV
# (Ausweichvariante) und BEDARF (Eventualposition) waren **Optionen** — sie wurden
# nicht beauftragt und dürfen deshalb auch nicht in Rechnung gestellt werden.
SUMMENWIRKSAM = beleg_service.SUMMENWIRKSAM
TEXT_TYPES = beleg_service.TEXT_TYPES

# DB-Spaltenskalen: quantity numeric(15,3), unit_price/unit_cost numeric(*,2).
_Q_MENGE = Decimal("0.001")
_CENT = Decimal("0.01")
_SEKUNDEN_JE_STUNDE = Decimal(3600)


class AbrechnungError(ValueError):
    """Der Abrechnungsvorgang ist fachlich unzulässig (→ 422)."""


class PreisUnbekannt(AbrechnungError):
    """Für mindestens eine Position steht kein Preis fest (→ 422 mit Klärungsliste).

    `positionen` ist **strukturiert**, nicht bloß Fließtext: Das UI baut daraus
    eine Klärungsmaske (Bezeichnung, Menge, Grund, Preisvorschläge) und schickt
    denselben Aufruf mit `preise` erneut. Ein Fehler ohne Ausweg wird sonst
    irgendwann umgangen — und der Umweg wäre die 0-€-Position.
    """

    def __init__(self, positionen):
        self.positionen = positionen
        namen = "; ".join(p["bezeichnung"] for p in positionen)
        super().__init__(
            "Für folgende Positionen steht kein Preis fest: "
            f"{namen}. Sie werden NICHT mit 0,00 € abgerechnet und auch nicht "
            "weggelassen. Bitte den Einzelpreis nennen oder den Artikel/die "
            "Lohngruppe pflegen."
        )


class EinheitUneindeutig(AbrechnungError):
    """Für denselben Artikel/dieselbe Leistung liegen Mengen in **verschiedenen
    Einheiten** vor (z. B. „Stk" und „Stück") — der Geld-Wächter geht fail-closed
    in die Klärung (→ 422 mit `einheit_uneindeutig`).

    Der Wächter aggregiert die berechnete Menge über die Artikel-/Leistungs-
    IDENTITÄT (nicht über die Einheit), damit „Stk" und „Stück" nicht als zwei
    Posten durchrutschen und dieselben 19 Ventile zweimal fakturiert werden.
    Divergieren die Einheiten aber wirklich, sind die Mengen **nicht summierbar**
    (5 m + 2 Ringe sind nicht 7): Dann wird weder still summiert noch still
    durchgelassen, sondern ein Mensch entscheidet — Einheiten vereinheitlichen oder
    den echten Mehr-Einheiten-Artikel bewusst getrennt abrechnen.

    `konflikte` ist **strukturiert** (Identität, Bezeichnung, die kollidierenden
    Einheiten), damit das UI klar sagen kann, WO das Problem sitzt.
    """

    def __init__(self, konflikte):
        self.konflikte = konflikte
        namen = "; ".join(
            f"{k['bezeichnung']} ({', '.join(k['einheiten'])})" for k in konflikte
        )
        super().__init__(
            "Für folgende Posten liegen die Mengen in verschiedenen Einheiten vor: "
            f"{namen}. Sie werden NICHT abgerechnet, solange unklar ist, ob es "
            "derselbe Posten in unterschiedlicher Schreibweise ist (dann "
            "vereinheitlichen) oder wirklich zwei Einheiten (dann getrennt und "
            "bewusst). Eine stille Summe wäre falsch, ein stiller Durchlass eine "
            "Doppelabrechnung."
        )


def _round2(wert):
    return wert.quantize(_CENT, rounding=ROUND_HALF_UP)


def _ist_preis(wert):
    """Ist das ein Preis — oder eine Null, die nur so aussieht?

    **Die Invariante dieses Moduls in einer Funktion.** „Der Server kennt einen
    Preis" heißt: er kennt einen Preis **größer als 0**. Ein `Decimal("0.00")` ist
    kein günstiger Preis, sondern eine **Lücke**, die durchgerutscht ist:

    * `article_sale_price.fixed_price` erlaubt `>= 0` — ein Festpreis 0,00 ist ein
      Tippfehler, keine Gratisleistung.
    * Die VK-Gruppe rechnet ihre Formel auch auf einer **0-Basis** durch (EK 0,00
      aus einem DATANORM-Import) und liefert sauber 0,00 zurück. Die
      Aufschlagsmatrix nennt denselben 0-EK selbst eine Lücke und gibt dort
      `sale_price = None` — die VK-Gruppe tut es nicht.
    * `wage_group.hourly_rate` erlaubt `>= 0` — ein Satz von 0,00 €/h ist keine
      Verrechnung.

    Fiele eine solche Null durch, stünde die Position mit **0,00 €** auf der
    Rechnung, die Vorschau (`offene_abrechnung`) meldete `BEKANNT` und der
    Klärungsweg wäre zu (`_genannter_preis` lehnt den genannten Preis ab, „weil der
    Server einen Preis kennt"). Die Rechnung sähe plausibel aus und wäre um den
    vollen Positionsbetrag zu niedrig — der teuerste stille Fehler, den dieses
    System machen kann.

    Deshalb: 0 ist **unbekannt**. Wer wirklich nichts berechnen will, nimmt einen
    Rabatt oder eine eigene Position — nicht eine getarnte Null.

    Die Rechenstellen selbst (`aufschlagsmatrix._apply_formula`, die VK-Gruppen)
    werden dafür **nicht** angefasst: Sie bedienen auch Editor und Artikelstamm;
    ihr Verhalten zu ändern ist eine Repo-weite Entscheidung, nicht Teil dieses
    Slices. Der Abrechnungslauf zieht die Grenze lokal — hier, wo Geld entsteht.
    """
    return wert is not None and Decimal(wert) > 0


# ---------------------------------------------------------------------------
# Bindungen lesen
# ---------------------------------------------------------------------------

def _aktive_bindungen(*, quote_line_ids=None, site_report_line_ids=None,
                      time_entry_ids=None, material_entry_ids=None):
    """Die aktiven (nicht gelösten) Bindungen zu den genannten Quellen.

    Gibt **vier** Mengen belegter Quell-IDs zurück (Angebot, Bericht, Zeit,
    Material). **Eine** Query je Quellart; die Auswertung läuft nie je Position
    (kein N+1).
    """
    def _belegt(feld, ids):
        if not ids:
            return set()
        return set(
            BillingLink.objects.filter(
                released_at__isnull=True, **{f"{feld}__in": list(ids)}
            ).values_list(feld, flat=True)
        )

    return (
        _belegt("quote_line_id", quote_line_ids),
        _belegt("site_report_line_id", site_report_line_ids),
        _belegt("time_entry_id", time_entry_ids),
        _belegt("material_entry_id", material_entry_ids),
    )


def _bindungen_am_auftrag(work_order_id, *, source_kinds):
    """Belegnummern der Rechnungen, die den Auftrag über die genannten Quellarten
    **aktiv** binden.

    Der Schlüssel zur Doppelabrechnung über den Moduswechsel (Review-Befund H-2):
    Die drei UNIQUE-Indizes auf `billing_link` sichern jede Quelle **einzeln** —
    sie können per Konstruktion **nicht** sehen, dass die Angebotsposition „10 m
    Rohr" und die Berichtsposition „10 m Rohr" **dieselbe Leistung** sind. Die
    einzige Klammer, die beide Quellen zusammenhält, ist der **Auftrag**. Also
    wird hier über ihn gefragt.
    """
    if not work_order_id:
        return []
    return sorted(
        {
            (nr or "einem Rechnungsentwurf")
            for nr in BillingLink.objects.filter(
                invoice__work_order_id=work_order_id,
                released_at__isnull=True,
                source_kind__in=list(source_kinds),
            ).values_list("invoice__invoice_number", flat=True)
        }
    )


def _IST_QUELLEN(order):
    """Welche Quellarten stehen für „der Auftrag ist über das IST abgerechnet"?

    Der Nachtrag (`rechnung_aus_nachtrag`) bindet **Berichtspositionen** — mit
    derselben `source_kind` wie die Regieabrechnung, denn die Codeliste kennt nur
    drei Werte (CHECK, Migration 0084). Trotzdem sind es zwei grundverschiedene
    Aussagen:

    * **REGIE**: die Berichtsposition ist mit ihrer **vollen Menge** fakturiert —
      daneben noch die Angebotskopie zu stellen, hieße dieselbe Leistung zweimal
      berechnen. Das muss gesperrt bleiben.
    * **PAUSCHAL**: die Bindung stammt aus einem **Nachtrag** und fakturiert
      ausschließlich die **Mehrmenge über dem Soll**. Sie ist zur Angebotskopie
      per Konstruktion **disjunkt** — beide gehören zusammen (Pauschale + Nachtrag),
      und zwar in **beliebiger Reihenfolge**. Bliebe die alte Sperre stehen, wäre
      der Nachtrag ein Einbahnweg: Wer ihn zuerst abrechnet, bekäme die eigentliche
      Pauschalrechnung nie mehr — mit einer Fehlermeldung, die etwas Falsches
      behauptet.

    Unterscheiden lässt sich beides am **Abrechnungsmodus des Auftrags** — und das
    ist hier belastbar, nicht bloß plausibel: `set_billing_mode` **friert den Modus
    ein, sobald eine aktive Bindung besteht**. Ein PAUSCHAL-Auftrag kann deshalb
    keine Regie-Bindung tragen (sie entstünde nur unter REGIE, und der Weg zurück
    ist gesperrt, solange sie aktiv ist). Erst ein **Storno** löst die Bindungen —
    und löst damit zugleich die Sperre auf, ganz richtig.

    **ZEITBUCHUNG zählt immer**: Zeiten fakturiert ausschließlich die Regie.
    **MATERIALBUCHUNG ebenso** (Migration 0139): Am Einsatz gebuchtes Material
    fakturiert ausschließlich `rechnung_aus_auftrag`; der Nachtrag rechnet über
    den Soll-Ist und kennt es nicht. Eine aktive Materialbindung ist deshalb — wie
    eine Zeitbindung — der Beweis, dass das Ist bereits fakturiert wurde.
    """
    if order is not None and order.billing_mode == PAUSCHAL:
        return (ZEITBUCHUNG, MATERIALBUCHUNG)
    return (BERICHTSPOSITION, ZEITBUCHUNG, MATERIALBUCHUNG)


def _schluessel_string(article_id, assembly_id, description, unit):
    """Der Abgleich-Schlüssel als String — deckungsgleich mit `soll_ist`/`abgleich`.

    Muss zeichengenau mit `pos["schluessel"]` aus `site_report.abgleich`
    übereinstimmen (`f"{typ}:{id}:{einheit}"`), sonst finden berechnete Menge und
    Abweichung nicht zusammen.
    """
    key = report_service.abgleich_schluessel(
        article_id, assembly_id, description, unit
    )
    return f"{key[0]}:{key[1]}:{key[2]}"


def _einheit_norm(unit):
    return (unit or "").strip().lower()


def _identitaet(article_id, assembly_id, description):
    """Die **Identität** eines Postens — OHNE die Einheit.

    Der Geld-Wächter aggregiert über die Artikel-/Leistungs-IDENTITÄT, nicht über
    den vollen Abgleich-Schlüssel (Review-Befund, dritter Weg): Die Einheit ist Teil
    des Abgleich-Schlüssels (`ARTIKEL:<uuid>:<einheit>`). Weicht sie zwischen der
    schon fakturierten Menge (etwa „Stk" aus einem Nachtrag) und der neu
    abzurechnenden (etwa „Stück" aus einem Büro-Angebot) ab — **gleicher Artikel** —,
    zerfällt der Posten sonst in zwei Schlüssel und die Mengengrenze sähe die schon
    fakturierte Menge nicht (912 € für 19 Ventile). Die Identität hält beide
    zusammen.

    Freitext ohne Artikel-/Leistungs-UUID hat keine belastbare Identität außer der
    normalisierten Bezeichnung — dann bleibt der Schlüssel bezeichnungsbasiert
    (dieselbe Grenze wie im Abgleich). Zwei WIRKLICH verschiedene Bezeichnungen sind
    verschiedene Posten; das System hat keine Grundlage, sie zu vereinen. Eine
    reine Einheiten-Abweichung derselben Bezeichnung fängt der Divergenz-Check
    trotzdem, weil die Identität die Einheit gerade NICHT enthält.
    """
    if article_id:
        return f"ARTIKEL:{article_id}"
    if assembly_id:
        return f"LEISTUNG:{assembly_id}"
    return f"TEXT:{(description or '').strip().lower()}"


def _billed_je_identitaet(work_order_id):
    """**Aktiv fakturierte Menge je Posten-IDENTITÄT, aufgeschlüsselt nach Einheit.**

    Der Kern der Reparatur (Review-Befunde, drei Doppelabrechnungen). Die
    Nachtragssicherheit darf NICHT am `source_kind` hängen (derselbe Posten ist über
    ANGEBOTSPOSITION UND BERICHTSPOSITION fakturierbar) — und **nicht an der
    Einheit**: „Stk" und „Stück" desselben Artikels sind derselbe Posten.

    Rückgabe: **drei** dicts `{identitaet: {einheit_norm: Decimal}}` — die über
    `ANGEBOTSPOSITION`, über `BERICHTSPOSITION` bzw. über `MATERIALBUCHUNG`
    fakturierte Menge, je Identität **und Einheit**, je **Rechnungsposition genau
    einmal** (mehrere Berichtszeilen binden an EINE Nachtragsposition — die
    Positionsmenge zählt nicht je Bindung mehrfach). Die Aufschlüsselung nach
    Einheit ist der Divergenz-Detektor: Führt eine Identität mehr als eine Einheit,
    sind die Mengen NICHT summierbar (5 m + 2 Ringe sind nicht 7) — dann greift die
    Klärung (fail-closed), nicht eine stille Summe.

    **Warum die Materialbuchung hier auftaucht** (Migration 0139): Dieselbe
    Schraube kann als Berichtsposition UND als Materialbuchung erfasst sein — der
    Monteur hat beide Formulare vor sich. Die vier UNIQUE-Indizes sehen das per
    Konstruktion nicht (verschiedene Quellspalten); die Menge je Identität sieht es.

    `ZEITBUCHUNG` bleibt außen vor: Arbeitszeit ist kein mengenbasierter Posten.
    Maßgeblich ist `invoice_line.quantity` (was auf der Rechnung steht), nicht die
    Quellmenge.
    """
    links = (
        BillingLink.objects.filter(
            invoice__work_order_id=work_order_id,
            released_at__isnull=True,
            invoice_line__isnull=False,
        )
        .exclude(source_kind=ZEITBUCHUNG)
        .select_related(
            "invoice_line", "quote_line", "material_entry",
            "site_report_line", "site_report_line__source_quote_line",
        )
    )
    angebot = {}
    bericht = {}
    material = {}
    gesehen = set()
    for link in links:
        if link.invoice_line_id in gesehen:
            continue
        gesehen.add(link.invoice_line_id)
        menge = link.invoice_line.quantity or Decimal("0.000")
        if link.quote_line_id is not None:
            ql = link.quote_line
            ident = _identitaet(ql.source_article_id, ql.source_assembly_id,
                                ql.description)
            ziel = angebot
            einheit = _einheit_norm(ql.unit)
        elif link.site_report_line_id is not None:
            srl = link.site_report_line
            src = srl.source_quote_line or srl
            ident = _identitaet(src.source_article_id, src.source_assembly_id,
                                src.description)
            ziel = bericht
            einheit = _einheit_norm(src.unit)
        elif link.material_entry_id is not None:
            me = link.material_entry
            # Eine Materialbuchung kennt keine Leistung (Stückliste) — nur Artikel
            # oder Freitext. Dieselbe Identitätsregel wie überall.
            ident = _identitaet(me.source_article_id, None, me.description)
            ziel = material
            einheit = _einheit_norm(me.unit)
        else:  # pragma: no cover — CHECK schließt eine bindungslose Quelle aus
            continue
        je_einheit = ziel.setdefault(ident, {})
        je_einheit[einheit] = je_einheit.get(einheit, Decimal("0.000")) + menge
    return angebot, bericht, material


def _billed_fuer(ident, einheit, angebot_billed, bericht_billed, material_billed):
    """(A, B, einheiten) für eine Identität unter EINER Einheit.

    `A` ist die über die **Angebotskopie** gebundene Menge, `B` die über das
    **Ist** gebundene — und „Ist" heißt seit Migration 0139 *Berichtsposition
    **plus** Materialbuchung*: Beide behaupten dieselbe Sache („dieses Material ist
    verbaut"), also gehören ihre Mengen in denselben Topf. Zwei getrennte Töpfe
    hießen, dieselbe Schraube zweimal für offen zu halten — genau die Lücke, die
    dieser Slice schließt.

    `einheiten` ist die Menge **aller** Einheiten, die für die Identität im Spiel
    sind (gebunden + die geprüfte). Ist sie größer als eins, wurde derselbe Artikel
    in verschiedenen Einheiten geführt — die Mengen sind nicht vergleichbar, und der
    Aufrufer muss in die **Klärung** gehen (fail-closed), statt still zu summieren
    oder still durchzulassen.
    """
    a_units = angebot_billed.get(ident, {})
    b_units = bericht_billed.get(ident, {})
    m_units = material_billed.get(ident, {})
    einheiten = set(a_units) | set(b_units) | set(m_units) | {einheit}
    A = a_units.get(einheit, Decimal("0.000"))
    B = (b_units.get(einheit, Decimal("0.000"))
         + m_units.get(einheit, Decimal("0.000")))
    return A, B, einheiten


def bindungen(invoice_id):
    """Alle Bindungen einer Rechnung (aktive zuerst) — für Mappe und Nachweis."""
    return list(
        BillingLink.objects.filter(invoice_id=invoice_id)
        .select_related("site_report_line__site_report", "time_entry__user",
                        "quote_line", "material_entry")
        .order_by("released_at", "created_at")
    )


# ---------------------------------------------------------------------------
# Preisermittlung — der Server rechnet, nie der Client
# ---------------------------------------------------------------------------

def _artikel_preis(article_id, menge, regelwerk):
    """(VK je Stück, EK je Stück, Grund) eines Artikels.

    **Die einzige Rechenstelle** ist `aufschlagsmatrix.vk_vorschlag`; hier wird
    nichts nachgerechnet. `sale_price = None` heißt **unbekannt, nicht 0** — der
    Aufrufer macht daraus eine Klärung, niemals eine 0-€-Position.

    Der EK kommt als Snapshot in die Position (`unit_cost`, Marge/Nachkalkulation).
    `vk_vorschlag` liefert ihn je `price_unit` (so liegt er im Stamm); die
    Belegposition führt ihn **je Stück** — deshalb die Division. `price_unit` ist
    stets eine Zehnerpotenz, sie ist exakt.
    """
    kopf = matrix_service.vk_vorschlag(article_id, menge, regelwerk=regelwerk)
    if kopf is None:
        return None, None, GRUND_KEINE_HERKUNFT
    ek = kopf.get("ek")
    ek_je_stueck = (
        _round2(Decimal(ek) / Decimal(kopf.get("price_unit") or 1))
        if ek is not None
        else None
    )
    vk = kopf.get("sale_price")
    if _ist_preis(vk):
        return Decimal(vk), ek_je_stueck, None
    if vk is not None:
        # Der Server HAT eine Zahl — sie ist 0,00 €. Das ist kein Preis (siehe
        # `_ist_preis`), sondern eine Lücke, die durchgerutscht ist. Sie bekommt
        # einen **eigenen** Grund: Der Nutzer soll die falsche Zahl im Stamm
        # erkennen, nicht nach einem fehlenden EK suchen.
        return None, ek_je_stueck, GRUND_VK_NULL
    # Warum kein Preis? Das UI soll den Nutzer zur richtigen Stelle schicken:
    # fehlt der Einkaufspreis (Lieferantenbezug/DATANORM) oder fehlt die Regel
    # (Aufschlagsmatrix/VK-Gruppe)?
    grund = GRUND_EK_FEHLT if kopf.get("regel") is not None else GRUND_KEINE_VK_REGEL
    return None, ek_je_stueck, grund


def _leistung_preis(assembly, regelwerk):
    """(VK je Einheit, Grund) einer Leistung (Stückliste). VK None = unbekannt.

    Eine Leistung IST ihre Stückliste (Migration 0033: Material + Lohn). Ihr Preis
    ist deshalb die Summe ihrer Bestandteile, gerechnet mit **denselben** beiden
    Rechenstellen wie überall: `vk_vorschlag` für Material,
    `wage_group.hourly_rate` für Lohn. Hier entsteht **keine zweite Preisregel**.

    Fehlt der Preis eines einzigen Bestandteils, ist der Preis der Leistung
    **unbekannt** (None) — nicht „der Rest". Eine Leistung ohne Bestandteile hat
    keinen ableitbaren Preis.

    Und: Eine Summe von **0,00 €** ist kein Preis, sondern eine Lücke
    (`_ist_preis`) — sie bekommt den eigenen Grund `VK_NULL`. Sonst stünde die
    Leistung mit 0,00 € auf der Rechnung, nur weil ihre Bestandteile allesamt
    0,00 € tragen.
    """
    komponenten = list(
        AssemblyComponent.objects.filter(assembly_id=assembly.id)
        .select_related("wage_group")
        .order_by("position")
    )
    if not komponenten:
        return None, GRUND_LEISTUNG_UNVOLLSTAENDIG
    summe = Decimal("0.00")
    for k in komponenten:
        if k.article_id is not None:
            vk, _ek, _grund = _artikel_preis(
                k.article_id, k.quantity or Decimal(1), regelwerk
            )
            if vk is None:
                return None, GRUND_LEISTUNG_UNVOLLSTAENDIG
            summe += _round2(vk * (k.quantity or Decimal(0)))
        elif k.wage_group_id is not None:
            # Ein Stundensatz von 0,00 € ist kein Satz — dieselbe Grenze wie beim
            # Artikel. Der Lohnanteil der Leistung wäre sonst stillschweigend frei.
            if not _ist_preis(k.wage_group.hourly_rate):
                return None, GRUND_LEISTUNG_UNVOLLSTAENDIG
            stunden = (k.minutes or Decimal(0)) / Decimal(60)
            summe += _round2(k.wage_group.hourly_rate * stunden)
        else:  # pragma: no cover — DB-XOR-CHECK schließt das aus
            return None, GRUND_LEISTUNG_UNVOLLSTAENDIG
    summe = _round2(summe)
    if not _ist_preis(summe):
        return None, GRUND_VK_NULL
    return summe, None


# ---------------------------------------------------------------------------
# Preisvorschläge — Lesehilfe, niemals automatischer Fallback
# ---------------------------------------------------------------------------

def _letzter_berechneter_preis(*, article_id=None, assembly_id=None):
    """Der Einzelpreis, zu dem dieser Artikel/diese Leistung zuletzt berechnet wurde.

    Quelle sind die **eigenen Rechnungen** (`invoicing.invoice_line`), nicht der
    Stamm: „Was haben wir dafür zuletzt genommen?" ist eine belastbare Aussage.
    Kreditbelege (negative Einzelpreise) bleiben außen vor — ein negativer
    Vorschlag wäre Unsinn.
    """
    feld = "source_article_id" if article_id else "source_assembly_id"
    line = (
        InvoiceLine.objects.filter(
            **{feld: article_id or assembly_id},
            unit_price__gt=0,
            invoice__status="VEROEFFENTLICHT",
        )
        .select_related("invoice")
        .order_by("-invoice__invoice_date", "-invoice__published_at")
        .first()
    )
    if line is None:
        return None
    return {
        "art": VORSCHLAG_LETZTER_PREIS,
        "betrag": line.unit_price,
        "quelle": (
            f"zuletzt berechnet in {line.invoice.invoice_number} "
            f"({line.invoice.invoice_date:%d.%m.%Y})"
            if line.invoice.invoice_date
            else f"zuletzt berechnet in {line.invoice.invoice_number}"
        ),
    }


def _vorschlaege_artikel(article_id):
    """Preisvorschläge für einen Artikel — **immer als Vorschlag gekennzeichnet**.

    Kein automatischer Fallback, kein Durchschnitt, keine geratene Zahl. Gibt es
    nichts Vorschlagbares, ist die Liste **leer** — das ist ehrlich.

    Der **Listenpreis** ist ausdrücklich KEIN kalkulierter Verkaufspreis (er ist
    die Aussage des Herstellers); er steht hier nur, weil er dem Menschen die
    Größenordnung nennt.
    """
    vorschlaege = []
    letzter = _letzter_berechneter_preis(article_id=article_id)
    if letzter:
        vorschlaege.append(letzter)
    article = Article.objects.filter(id=article_id).only(
        "list_price", "price_unit"
    ).first()
    if article is not None and article.list_price is not None:
        vorschlaege.append({
            "art": VORSCHLAG_LISTENPREIS,
            "betrag": _round2(
                article.list_price / Decimal(article.price_unit or 1)
            ),
            "quelle": "Listenpreis des Herstellers (kein kalkulierter VK)",
        })
    return vorschlaege


def _vorschlaege_lohn():
    """Vorschläge für eine Zeitgruppe ohne Lohngruppe: die **gepflegten** Sätze.

    Nicht geraten: Das sind die Verrechnungssätze, die der Betrieb selbst angelegt
    hat. Der richtige Weg bleibt, dem Mitarbeiter eine Lohngruppe zuzuweisen —
    der Vorschlag macht die Rechnung nur nicht unmöglich.

    Lohngruppen mit Satz **0,00 €/h** stehen hier nicht: Ein Vorschlag, den die
    Preisklärung selbst wieder ablehnt (`_preise_normalisieren`), wäre eine
    Einladung in die Sackgasse.
    """
    return [
        {
            "art": VORSCHLAG_LOHNGRUPPE,
            "betrag": wg.hourly_rate,
            "quelle": f"Lohngruppe „{wg.name}“ (Verrechnungssatz)",
        }
        for wg in WageGroup.objects.filter(
            status="AKTIV", kind="LOHN", hourly_rate__gt=0
        ).order_by("name")
    ]


# ---------------------------------------------------------------------------
# Quellen des Auftrags
# ---------------------------------------------------------------------------

#: Berichtszustände, die eine Abrechnung tragen (seit Migration 0144).
#: `ENTWURF` fehlt hier bewusst — daran tippt der Monteur womöglich noch.
ABRECHENBARE_BERICHTE = ("ABGESCHLOSSEN", "UNTERZEICHNET")


def _berichtspositionen(work_order_id):
    """Positionen aus den **abgeschlossenen und unterzeichneten** Berichten.

    Bis Migration 0144 zog diese Funktion allein aus `UNTERZEICHNET`, begründet
    mit „ein nicht abgenommener Nachweis ist keine Abrechnungsgrundlage". Die
    Begründung stimmt — nur traf die Umsetzung den Normalfall dieses Betriebs:
    Rund 80 % der Berichte unterschreibt niemand (Sascha, 2026-08-02), fertig
    sind sie trotzdem. Sie mussten als ENTWURF liegenbleiben und fielen damit
    aus der Abrechnung heraus.

    Seit 0144 trennt der dritte Zustand die beiden Fälle: `ABGESCHLOSSEN` heißt
    „fertig, niemand hat unterschrieben" und ist voll abrechenbar, `ENTWURF`
    heißt „in Arbeit" und bleibt draußen. Die Unterschrift ist damit ein
    **Merkmal** — bei Streit über einen Posten weiterhin sichtbar — und kein
    **Tor** mehr.

    Entwürfe werden nicht verschwiegen: Der Aufrufer bekommt sie benannt
    (`_entwurfsberichte`).

    TEXT-Zeilen tragen keine Menge und sind Kommentar, kein Posten.
    """
    return list(
        SiteReportLine.objects.filter(
            site_report__work_order_id=work_order_id,
            site_report__status__in=ABRECHENBARE_BERICHTE,
        )
        .exclude(line_type=report_service.TEXT_TYPE)
        .select_related("site_report")
        .order_by("site_report__report_date", "site_report__created_at",
                  "position_number")
    )


def _entwurfsberichte(work_order_id):
    """Die Berichte, die **nicht** in die Abrechnung eingehen — benannt, nicht
    verschwiegen. Sie sind der häufigste Grund für eine „zu kleine" Rechnung.

    Seit 0144 sind das nur noch die echten Entwürfe: `ABGESCHLOSSEN` fließt ein
    und darf hier nicht mehr auftauchen, sonst meldete die Vorschau eine Lücke,
    die gar keine ist — und der Bearbeiter suchte nach fehlendem Geld, das längst
    in der Rechnung steht.
    """
    return list(
        SiteReport.objects.filter(work_order_id=work_order_id)
        .exclude(status__in=ABRECHENBARE_BERICHTE)
        .order_by("report_date", "created_at")
    )


def _zeitbuchungen(work_order_id):
    """Abrechenbare Zeitbuchungen des Auftrags.

    * `category.is_work_time` — Pausen und andere Nicht-Arbeitszeiten werden nicht
      berechnet (`is_work_time` ist das einzige harte Attribut der Kategorie,
      Migration 0066).
    * `ended_at IS NOT NULL` — eine **laufende** Buchung hat noch keine Dauer.
      Sie zu berechnen hieße, eine Zahl zu erfinden.
    * Der Auftragsbezug läuft über den Einsatz (`time_entry` kennt keinen Auftrag);
      Werkstatt-/Bürozeiten ohne Einsatz gehören zu keiner Baustelle.
    """
    return list(
        TimeEntry.objects.filter(
            service_job__work_order_id=work_order_id,
            category__is_work_time=True,
            ended_at__isnull=False,
        )
        .select_related("category", "user")
        .order_by("started_at")
    )


def _materialbuchungen(work_order_id):
    """Am Einsatz gebuchtes Material des Auftrags (Migration 0139).

    Streng analog zu `_zeitbuchungen`: Der Sammler liefert die **rohen Buchungen**,
    der Preis entsteht danach (`_material_klaerung` → `_artikel_preis` →
    `vk_vorschlag`). Keine zweite Preisregel, keine Rechnung im Sammler.

    * Der Auftragsbezug läuft über den Einsatz (`material_entry` kennt keinen
      Auftrag) — genau wie bei der Zeit. Material an einem **freien Termin** ohne
      Auftrag gehört zu keiner Baustelle und wird hier nicht gefunden.
    * Es gibt **kein Statusfilter** wie „unterzeichnet" beim Bericht: Die
      Materialbuchung ist kein abzunehmender Nachweis, sondern die Aufzeichnung des
      Monteurs — dasselbe Gewicht wie eine Zeitbuchung.
    * Kein `quantity`-Filter: Die DB erzwingt `quantity > 0`.

    **Keine Bestandsführung** (B-26): Hier wird nichts fortgeschrieben, reserviert
    oder abgebucht. Gelesen wird, was jemand aufgeschrieben hat.
    """
    return list(
        MaterialEntry.objects.filter(
            service_job__work_order_id=work_order_id
        )
        .select_related("service_job")
        .order_by("created_at", "id")
    )


def _stunden(sekunden):
    """Sekunden → Stunden auf der DB-Spaltenskala numeric(15,3).

    **Erst summieren, dann umrechnen.** Die Zeiterfassung führt Zeitpunkte, keine
    Dezimalstunden; 20 Minuten sind 0,333… h. Würde je Buchung gerundet, summierten
    sich die Rundungsfehler über einen Monat zu einem sichtbaren Betrag. Deshalb
    wird die Sekundensumme der ganzen Zeitgruppe **einmal** umgerechnet.
    """
    return (Decimal(sekunden) / _SEKUNDEN_JE_STUNDE).quantize(
        _Q_MENGE, rounding=ROUND_HALF_UP
    )


# ---------------------------------------------------------------------------
# Lohngruppe & Zeitgruppen
# ---------------------------------------------------------------------------

def _lohngruppen_kontext(zeiten):
    """Mitarbeiter + Verträge zu den Buchungen (zwei Queries, kein N+1)."""
    users = {t.user_id for t in zeiten}
    employees = {
        e.app_user_id: e
        for e in Employee.objects.filter(app_user_id__in=users).select_related(
            "wage_group"
        )
    }
    contracts = {}
    for c in (
        EmploymentContract.objects.filter(
            employee_id__in=[e.id for e in employees.values()]
        )
        .select_related("wage_group")
        .order_by("-valid_from")
    ):
        contracts.setdefault(c.employee_id, []).append(c)
    return employees, contracts


def _lohngruppe(entry, employees, contracts):
    """Die Lohngruppe eines Mitarbeiters **zum Zeitpunkt der Arbeit**.

    Maßgeblich ist der **Vertrag**, der am Tag der Buchung galt
    (`hr.employment_contract`; die Lohngruppe ist dort nach dem INSERT
    unveränderlich) — nicht die heutige Einstufung. Eine Lohnerhöhung im nächsten
    Jahr darf die Stunden des letzten Jahres nicht teurer machen.

    Trägt der Vertrag keine Lohngruppe (sie ist dort optional), fällt die
    Ermittlung auf die **aktuelle Einstufung am Mitarbeiter** zurück
    (`hr.employee.wage_group`). Auch das ist keine Erfindung, sondern die einzige
    andere gepflegte Aussage des Betriebs.

    Ohne beides: **None** → Klärung mit Grund `LOHNGRUPPE_FEHLT`. Kein Ausweichen
    auf irgendeinen Satz — ein geratener Stundensatz ist eine erfundene Geldzahl.
    """
    employee = employees.get(entry.user_id)
    if employee is None:
        return None
    tag = entry.started_at.date()
    for c in contracts.get(employee.id, []):
        if c.valid_from <= tag and (c.valid_to is None or c.valid_to >= tag):
            if c.wage_group_id is not None:
                return c.wage_group
            break
    return employee.wage_group


def _zeitgruppen(zeiten):
    """Bündelt Zeitbuchungen zu **Zeitgruppen**: Lohngruppe — oder Mitarbeiter.

    Abgerechnet wird eine Sammelposition je Lohngruppe. Hat ein Mitarbeiter keine
    Lohngruppe, ist **er** die Gruppe: Nur so lässt sich für genau seine Stunden
    ein Preis nennen (`preise[<app_user_id>]`), ohne die Stunden der anderen
    mitzuverbiegen.

    Rückgabe: Liste von dicts mit `key` (die Klärungs-/Preis-ID), `wage_group`
    (oder None), `entries`, `sekunden`, `bezeichnung`.
    """
    employees, contracts = _lohngruppen_kontext(zeiten)
    gruppen = OrderedDict()
    for entry in zeiten:
        wg = _lohngruppe(entry, employees, contracts)
        key = wg.id if wg is not None else entry.user_id
        eintrag = gruppen.setdefault(
            key,
            {
                "key": key,
                "wage_group": wg,
                "entries": [],
                "sekunden": 0,
                "bezeichnung": (
                    f"Arbeitszeit {wg.name}" if wg is not None
                    else f"Arbeitszeit {entry.user.display_name}"
                ),
            },
        )
        eintrag["entries"].append(entry)
        eintrag["sekunden"] += int(
            (entry.ended_at - entry.started_at).total_seconds()
        )
    return sorted(gruppen.values(), key=lambda g: g["bezeichnung"])


# ---------------------------------------------------------------------------
# Offene Abrechnung: was ist noch NICHT abgerechnet — und wo fehlt der Preis?
# ---------------------------------------------------------------------------

# Was der Nutzer lesen soll — je Grund. Der Text schickt ihn an die Stelle, an
# der der Preis entsteht; `VK_NULL` sagt ausdrücklich, dass eine **falsche Zahl**
# im Stamm steht (0,00 €) und nicht etwa eine fehlt.
_GRUND_TEXTE = {
    GRUND_EK_FEHLT: (
        "Der Einkaufspreis des Artikels ist unbekannt — der Verkaufspreis lässt "
        "sich daraus nicht ableiten (er ist UNBEKANNT, nicht 0)."
    ),
    GRUND_KEINE_VK_REGEL: (
        "Für diesen Artikel greift keine Aufschlagsregel und keine VK-Gruppe; es "
        "gibt keinen Festpreis."
    ),
    GRUND_VK_NULL: (
        "Der Server errechnet für diesen Artikel 0,00 € — das ist kein Preis, "
        "sondern eine Lücke: Entweder steht ein Einkaufspreis von 0,00 € im Stamm "
        "(typischer Importfehler aus DATANORM/IDS) oder es ist ein Festpreis von "
        "0,00 € hinterlegt. Der Artikel wird NICHT mit 0,00 € abgerechnet. Bitte "
        "den Einkaufs-/Festpreis im Artikelstamm korrigieren — oder hier den "
        "Einzelpreis für DIESEN Beleg nennen."
    ),
    GRUND_LEISTUNG_UNVOLLSTAENDIG: (
        "Der Preis der Leistung ist nicht ermittelbar: mindestens ein Bestandteil "
        "ihrer Stückliste hat keinen Preis (oder sie hat gar keine Bestandteile)."
    ),
    GRUND_KEINE_HERKUNFT: (
        "Die Position verweist weder auf einen Artikel noch auf eine Leistung — es "
        "gibt nichts, woraus der Server einen Preis rechnen könnte."
    ),
    GRUND_MATERIAL_OHNE_ARTIKEL: (
        "Diese Materialbuchung nennt keinen Artikel aus dem Stamm, sondern nur "
        "einen freien Text — daraus lässt sich kein Verkaufspreis ableiten. Die "
        "Buchung wird deshalb NICHT mit 0,00 € abgerechnet und auch nicht "
        "weggelassen: Entweder wird ihr am Einsatz der passende Artikel zugeordnet "
        "(dann rechnet der Server), oder der Einzelpreis wird hier für DIESEN Beleg "
        "genannt."
    ),
}


def _bericht_klaerung(line, regelwerk):
    """(preis, ek, klaerung|None) einer Berichtsposition.

    Die **eine** Stelle, an der entschieden wird, ob der Server einen Preis hat.
    `offene_abrechnung` und `rechnung_aus_auftrag` rufen sie beide auf — liefen sie
    auseinander, zeigte die Vorschau etwas anderes, als der Lauf tut.
    """
    def _klaerung(grund, text, vorschlaege):
        return {
            "quelle_art": QUELLE_BERICHTSPOSITION,
            "quelle_id": line.id,
            "bezeichnung": (
                f"Bericht vom {line.site_report.report_date:%d.%m.%Y}, "
                f"Pos. {line.position_number}: {line.description}"
            ),
            "menge": line.quantity,
            "einheit": line.unit,
            "grund": grund,
            "grund_text": text,
            "vorschlaege": vorschlaege,
        }

    if line.source_article_id is not None:
        vk, ek, grund = _artikel_preis(
            line.source_article_id, line.quantity or Decimal(1), regelwerk
        )
        if vk is not None:
            return vk, ek, None
        return None, ek, _klaerung(
            grund, _GRUND_TEXTE[grund],
            _vorschlaege_artikel(line.source_article_id),
        )

    if line.source_assembly_id is not None:
        assembly = Assembly.objects.filter(id=line.source_assembly_id).first()
        if assembly is None:
            vk, grund = None, GRUND_LEISTUNG_UNVOLLSTAENDIG
        else:
            vk, grund = _leistung_preis(assembly, regelwerk)
        if vk is not None:
            return vk, None, None
        letzter = _letzter_berechneter_preis(assembly_id=line.source_assembly_id)
        text = (
            "Die Bestandteile der Leistung ergeben in Summe 0,00 € — das ist kein "
            "Preis, sondern eine Lücke in der Stückliste (Mengen 0, Preise 0). Die "
            "Leistung wird NICHT gratis abgerechnet."
            if grund == GRUND_VK_NULL
            else _GRUND_TEXTE[GRUND_LEISTUNG_UNVOLLSTAENDIG]
        )
        return None, None, _klaerung(grund, text, [letzter] if letzter else [])

    return None, None, _klaerung(
        GRUND_KEINE_HERKUNFT, _GRUND_TEXTE[GRUND_KEINE_HERKUNFT], []
    )


def _material_klaerung(entry, regelwerk):
    """(preis, ek, klaerung|None) einer Materialbuchung (Migration 0139).

    Dieselbe Rolle wie `_bericht_klaerung`: **die eine Stelle**, an der entschieden
    wird, ob der Server für diese Buchung einen Preis hat. `offene_abrechnung` und
    `rechnung_aus_auftrag` rufen sie beide auf — liefen sie auseinander, zeigte die
    Vorschau etwas anderes, als der Lauf tut.

    Zwei Wege in die Klärung:

    * **Kein Artikel** (Freitext) → `MATERIAL_OHNE_ARTIKEL`. Das ist der Bestand:
      Bis 0139 konnte die Buchung gar keinen Artikel tragen. Sie darf deshalb
      weder mit 0,00 € durchgehen noch stillschweigend fehlen — beides wäre eine zu
      niedrige Rechnung, die plausibel aussieht.
    * **Artikel ohne ermittelbaren VK** → dieselben Gründe wie überall
      (`EK_FEHLT`, `KEINE_VK_REGEL`, `VK_NULL`) samt Vorschlägen aus dem Stamm.
    """
    def _klaerung(grund, text, vorschlaege):
        return {
            "quelle_art": QUELLE_MATERIALBUCHUNG,
            "quelle_id": entry.id,
            "bezeichnung": f"Materialbuchung: {entry.description}",
            "menge": entry.quantity,
            "einheit": entry.unit,
            "grund": grund,
            "grund_text": text,
            "vorschlaege": vorschlaege,
        }

    if entry.source_article_id is None:
        return None, None, _klaerung(
            GRUND_MATERIAL_OHNE_ARTIKEL,
            _GRUND_TEXTE[GRUND_MATERIAL_OHNE_ARTIKEL],
            # Kein Vorschlag: Ohne Artikel gibt es nichts, worauf sich ein
            # „zuletzt berechnet" beziehen könnte. Eine geratene Zahl wäre hier
            # schlimmer als keine.
            [],
        )
    vk, ek, grund = _artikel_preis(
        entry.source_article_id, entry.quantity or Decimal(1), regelwerk
    )
    if vk is not None:
        return vk, ek, None
    return None, ek, _klaerung(
        grund, _GRUND_TEXTE[grund], _vorschlaege_artikel(entry.source_article_id)
    )


def _zeit_klaerung(gruppe):
    """(stundensatz, ek, klaerung|None) einer Zeitgruppe.

    Zwei Wege in die Klärung, und der zweite ist der heimtückische: Die Lohngruppe
    **fehlt** (kein Vertrag, keine Einstufung) — oder sie ist da und trägt einen
    Stundensatz von **0,00 €** (der CHECK erlaubt `>= 0`). Eine 0-€-Arbeitszeit-
    position sähe auf der Rechnung völlig unauffällig aus und verschenkte die
    gesamte Arbeitszeit. Also: 0 ist kein Satz (`_ist_preis`).
    """
    wg = gruppe["wage_group"]
    def _klaerung(grund, text):
        return {
            "quelle_art": QUELLE_ZEITGRUPPE,
            "quelle_id": gruppe["key"],
            "bezeichnung": gruppe["bezeichnung"],
            "menge": _stunden(gruppe["sekunden"]),
            "einheit": "h",
            "grund": grund,
            "grund_text": text,
            "vorschlaege": _vorschlaege_lohn(),
        }

    if wg is None:
        return None, None, _klaerung(
            GRUND_LOHNGRUPPE_FEHLT,
            "Dem Mitarbeiter ist weder im Vertrag noch am Personalsatz eine "
            "Lohngruppe zugewiesen — ohne Lohngruppe gibt es keinen Stundensatz.",
        )
    if not _ist_preis(wg.hourly_rate):
        return None, None, _klaerung(
            GRUND_LOHNSATZ_NULL,
            f"Die Lohngruppe „{wg.name}“ trägt einen Verrechnungssatz von 0,00 €/h "
            "— das ist kein Stundensatz, sondern eine Lücke. Die Arbeitszeit wird "
            "NICHT mit 0,00 € abgerechnet. Bitte den Satz der Lohngruppe pflegen — "
            "oder hier den Stundensatz für DIESEN Beleg nennen.",
        )
    return wg.hourly_rate, wg.cost_rate, None


def offene_abrechnung(work_order_id):
    """Was ist an diesem Auftrag noch **nicht** abgerechnet?

    Fällt aus der Bindung gratis ab und ist für sich schon ein Feature: die
    Antwort auf „Haben wir das schon in Rechnung gestellt?".

    **Der fehlende Preis wird HIER schon sichtbar** (`preis_status`), nicht erst
    beim Abrechnungslauf: Er ist ja bereits unbekannt, sobald die Position im
    Bericht landet. Das ist der eigentliche Hebel gegen die Sackgasse — geklärt
    wird, bevor jemand fakturieren will.

    Bei **PAUSCHAL** sind Berichtspositionen und Zeiten ausdrücklich **kein**
    Rechnungsposten (das Angebot enthält die Leistung bereits) — sie werden
    trotzdem ausgewiesen, aber als **Nachweis**. `abrechenbar` sagt es unmissver-
    ständlich; ohne diese Unterscheidung läse das UI die Liste als „noch zu
    fakturieren" und der Betrieb kassierte doppelt.
    """
    order = WorkOrder.objects.filter(id=work_order_id).first()
    if order is None:
        raise AbrechnungError("Auftrag nicht gefunden.")

    bericht_lines = _berichtspositionen(order.id)
    zeiten = _zeitbuchungen(order.id)
    material = _materialbuchungen(order.id)
    _q, belegte_bl, belegte_te, belegte_me = _aktive_bindungen(
        site_report_line_ids=[l.id for l in bericht_lines],
        time_entry_ids=[t.id for t in zeiten],
        material_entry_ids=[m.id for m in material],
    )
    offene_lines = [l for l in bericht_lines if l.id not in belegte_bl]
    offene_zeiten = [t for t in zeiten if t.id not in belegte_te]
    offenes_material = [m for m in material if m.id not in belegte_me]

    regelwerk = matrix_service.lade_regelwerk()
    positionen = []
    for line in offene_lines:
        preis, _ek, klaerung = _bericht_klaerung(line, regelwerk)
        positionen.append({
            "site_report_line_id": line.id,
            "site_report_id": line.site_report_id,
            "report_date": line.site_report.report_date,
            "position_number": line.position_number,
            "line_type": line.line_type,
            "description": line.description,
            "quantity": line.quantity,
            "unit": line.unit,
            "preis_status": PREIS_BEKANNT if preis is not None else PREIS_UNBEKANNT,
            "einzelpreis": preis,
            "grund": klaerung["grund"] if klaerung else None,
            "grund_text": klaerung["grund_text"] if klaerung else None,
            "vorschlaege": klaerung["vorschlaege"] if klaerung else [],
        })

    gruppen = []
    for gruppe in _zeitgruppen(offene_zeiten):
        satz, _ek, klaerung = _zeit_klaerung(gruppe)
        gruppen.append({
            "quelle_id": gruppe["key"],
            "bezeichnung": gruppe["bezeichnung"],
            "wage_group_id": (
                gruppe["wage_group"].id if gruppe["wage_group"] else None
            ),
            "stunden": _stunden(gruppe["sekunden"]),
            "time_entry_ids": [e.id for e in gruppe["entries"]],
            "preis_status": PREIS_BEKANNT if satz is not None else PREIS_UNBEKANNT,
            "einzelpreis": satz,
            "grund": klaerung["grund"] if klaerung else None,
            "grund_text": klaerung["grund_text"] if klaerung else None,
            "vorschlaege": klaerung["vorschlaege"] if klaerung else [],
        })

    # Material am Einsatz (Migration 0139) — **hier wird der fehlende Preis schon
    # sichtbar**, nicht erst beim Abrechnungslauf. Genau das ist der Hebel: Eine
    # Freitextbuchung ohne Artikel ist bereits unbekannt, sobald sie gebucht wird.
    materialien = []
    for entry in offenes_material:
        preis, _ek, klaerung = _material_klaerung(entry, regelwerk)
        materialien.append({
            "material_entry_id": entry.id,
            "service_job_id": entry.service_job_id,
            "description": entry.description,
            "quantity": entry.quantity,
            "unit": entry.unit,
            "source_article_id": entry.source_article_id,
            "preis_status": PREIS_BEKANNT if preis is not None else PREIS_UNBEKANNT,
            "einzelpreis": preis,
            "grund": klaerung["grund"] if klaerung else None,
            "grund_text": klaerung["grund_text"] if klaerung else None,
            "vorschlaege": klaerung["vorschlaege"] if klaerung else [],
        })

    return {
        "work_order_id": order.id,
        "billing_mode": order.billing_mode,
        # Der Kern der PAUSCHAL-Regel, für das UI unmissverständlich benannt.
        "abrechenbar": order.billing_mode == REGIE,
        "hinweis": (
            "Regieabrechnung: Berichtspositionen, Zeiten und am Einsatz gebuchtes "
            "Material werden fakturiert."
            if order.billing_mode == REGIE
            else "Pauschalabrechnung: Die Rechnung ist die Angebotskopie. "
                 "Berichtspositionen, Zeiten und Materialbuchungen sind Nachweis "
                 "und interne Nachkalkulation — sie werden NICHT zusätzlich "
                 "fakturiert. Was über das Angebot hinaus geleistet wurde, rechnet "
                 "der Nachtrag ab (Reiter „Soll-Ist“) — dort nur die Mehrmenge, nie "
                 "die ganze Position."
        ),
        "berichtspositionen": positionen,
        "zeitgruppen": gruppen,
        "materialbuchungen": materialien,
        # Ehrlichkeit statt Stille: unsignierte Berichte fließen NICHT ein.
        "nicht_unterzeichnete_berichte": [
            {"id": r.id, "report_date": r.report_date, "status": r.status,
             "activity_text": r.activity_text}
            for r in _entwurfsberichte(order.id)
        ],
    }


# ---------------------------------------------------------------------------
# Genannte Preise (die Klärung des Menschen)
# ---------------------------------------------------------------------------

def _preise_normalisieren(preise):
    """{quelle_id → Einzelpreis} auf die DB-Skala bringen und prüfen.

    **0,00 € wird abgelehnt.** Ein leeres Eingabefeld darf nicht stillschweigend
    zur Gratisleistung werden — genau der Fehler, den dieses Modul verhindern
    soll. Kulanz gehört als Rabatt oder als eigene Position auf den Beleg, nicht
    als getarnte Null in die Preisklärung.
    """
    normiert = {}
    for schluessel, wert in (preise or {}).items():
        try:
            preis = Decimal(str(wert)).quantize(_CENT, rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError, TypeError):
            raise AbrechnungError(
                f"Der genannte Preis für {schluessel} ist keine gültige Zahl."
            )
        if not preis.is_finite() or preis <= 0:
            raise AbrechnungError(
                f"Der genannte Preis für {schluessel} muss größer als 0,00 € sein. "
                "Eine 0 wäre eine Gratisleistung — dafür ist ein Rabatt oder eine "
                "eigene Position der richtige Weg."
            )
        normiert[str(schluessel)] = preis
    return normiert


def _genannter_preis(preise, quelle_id, *, bezeichnung, server_preis):
    """Der genannte Preis — nur zulässig, wo der Server **keinen** hat.

    Sonst ließe sich die eine Rechenstelle (`vk_vorschlag`) über den Umweg „Preis
    nennen" stillschweigend unterlaufen: Jede Position bekäme den Preis, den der
    Client gerade schickt, und die Aufschlagsmatrix wäre eine Zierde. Der Preis
    darf **nur** genannt werden, wo der Server keinen kennt.
    """
    genannt = preise.pop(str(quelle_id), None)
    if genannt is None:
        return None
    # `_ist_preis`, nicht `is not None`: Wo der Server nur eine 0 hat, hat er
    # **keinen** Preis — und der genannte Preis muss durchkommen. Sonst wäre der
    # Klärungsweg genau dort zu, wo er am dringendsten gebraucht wird.
    if _ist_preis(server_preis):
        raise AbrechnungError(
            f"Für „{bezeichnung}“ wurde ein Einzelpreis genannt, obwohl der Server "
            f"einen Preis kennt ({server_preis} €). Der Preis wird vom Server "
            "gerechnet; genannt werden darf er nur dort, wo er unbekannt ist."
        )
    return genannt


# ---------------------------------------------------------------------------
# Angebot → Rechnung (PAUSCHAL)
# ---------------------------------------------------------------------------

def _quote_zeilen_kopieren(quote):
    """Die Angebotspositionen als Rechnungspositionen — **wertgleich kopiert**.

    Kopiert wird der **im Angebot vereinbarte** Preis, nicht der heutige
    Listenpreis: Der Kunde hat diesen Preis akzeptiert. Ein Neuberechnen aus dem
    Stamm wäre eine einseitige Preisänderung nach Vertragsschluss.

    ALTERNATIV- und BEDARF-Positionen werden **nicht** übernommen — sie waren
    Optionen und wurden nicht beauftragt. Text- und Zwischensummenzeilen wandern
    mit (sie sind der Lesefluss des Belegs), tragen aber keine Bindung: sie
    rechnen nichts ab.

    Gibt (lines, rubriken, quelle_je_position) zurück; `quelle_je_position` bildet
    die 1-basierte Positionsnummer der NEUEN Rechnung auf die Angebotsposition ab.
    """
    rubriken = sorted(quote.rubriken.all(), key=lambda r: r.position_number)
    rubrik_nummer = {r.id: idx for idx, r in enumerate(rubriken, start=1)}

    lines = []
    quelle = {}
    for ql in sorted(quote.lines.all(), key=lambda l: l.position_number):
        if ql.line_kind != SUMMENWIRKSAM:
            continue
        pos = len(lines) + 1
        row = {
            "line_type": ql.line_type,
            "line_kind": ql.line_kind,
            "description": ql.description,
            "rubrik": rubrik_nummer.get(ql.rubrik_id),
        }
        if ql.line_type in TEXT_TYPES:
            lines.append(row)
            continue
        row.update(
            quantity=ql.quantity,
            unit=ql.unit,
            unit_price=ql.unit_price,
            discount_percent=ql.discount_percent,
            tax_code=ql.tax_code_id,
            # § 35a: der eingefrorene Anteil des Angebots wandert mit. None bleibt
            # None — eine Kopie erfindet keine Bestimmtheit (Leitinvariante 0076).
            labour_net_amount=ql.labour_net_amount,
            # Kalkulations-Snapshot (EK/Aufschlag/Herkunft) mitnehmen: die Marge
            # der Rechnung ist die des Angebots, nicht die von heute.
            unit_cost=ql.unit_cost,
            markup_percent=ql.markup_percent,
            sale_price_group_id=ql.sale_price_group_id,
            source_article_id=ql.source_article_id,
            source_assembly_id=ql.source_assembly_id,
        )
        lines.append(row)
        quelle[pos] = ql.id
    rubriken_out = [
        {"title": r.title, "description": r.description} for r in rubriken
    ]
    return lines, rubriken_out, quelle


def _pauschal_mengengrenze_pruefen(order, lines):
    """Die Angebotskopie darf keine schon per Nachtrag fakturierte Menge doppeln.

    Der Gegenpart zur Nachtragsformel (Review-Befund, KRITISCH 2): Wird zuerst ein
    Nachtrag über einen Posten abgerechnet (BERICHTSPOSITION, etwa als ZUSATZ, weil
    noch kein Angebot zugeordnet war) und **danach** ein Angebot mit demselben Posten
    zugeordnet und fakturiert, stünde die Menge zweimal auf zwei Rechnungen — je für
    sich sauber gebunden, für die drei UNIQUE-Indizes unsichtbar (disjunkte Quellen).

    Die Grenze ist **quantitativ und quellenunabhängig**: Nach dem Buchen dieser
    Angebotszeile (`q`) darf die Gesamtmenge des Postens `max(A + q, Ist)` nicht
    überschreiten. Umgestellt: die schon per Bericht fakturierte Menge `B` muss
    `max(0, Ist − A − q)` einhalten. Ist sie größer, ist der Posten bereits über den
    Nachtrag abgerechnet — der Weg ist, den Nachtrag zu **stornieren** (das löst die
    Bindung), nicht ihn zu übergehen.

    Nur bei PAUSCHAL: Bei REGIE ist die Angebotskopie ohnehin gesperrt, bei einem
    Angebot ohne Auftrag gibt es kein Ist.
    """
    if order is None or order.billing_mode != PAUSCHAL:
        return
    abgleich = report_service.abgleich(order, nur_unterzeichnet=True)
    ist_je = {p["schluessel"]: p["ist"] for p in abgleich["positionen"]}
    angebot_billed, bericht_billed, material_billed = _billed_je_identitaet(order.id)

    # Menge dieses Angebots je (Identität, Einheit) — plus der volle Schlüssel für
    # den Ist-Nachschlag (der Abgleich schlüsselt mit Einheit).
    q_je = {}
    for row in lines:
        if row.get("quantity") is None:      # Text-/Zwischensummenzeile
            continue
        ident = _identitaet(
            row.get("source_article_id"), row.get("source_assembly_id"),
            row.get("description"),
        )
        einheit = _einheit_norm(row.get("unit"))
        rec = q_je.setdefault(
            (ident, einheit),
            {"menge": Decimal("0.000"),
             "bez": row.get("description") or ident,
             "schluessel": _schluessel_string(
                 row.get("source_article_id"), row.get("source_assembly_id"),
                 row.get("description"), row.get("unit"))},
        )
        rec["menge"] += row["quantity"]

    konflikte = []
    for (ident, einheit), rec in q_je.items():
        A, B, einheiten = _billed_fuer(
            ident, einheit, angebot_billed, bericht_billed, material_billed
        )
        if len(einheiten) > 1:
            # Derselbe Artikel steht schon unter einer ANDEREN Einheit in Rechnung
            # (z. B. Nachtrag „Stk", Angebot „Stück"). Ohne diesen Zweig sähe die
            # Mengengrenze die gebundene Menge nicht (verschiedene Einheiten =
            # verschiedene Schlüssel) und buchte 19 obendrauf — 912 € für 19
            # Ventile. Fail-closed: Klärung.
            konflikte.append({
                "identitaet": ident,
                "bezeichnung": rec["bez"],
                "einheiten": sorted(einheiten),
            })
            continue
        ist = ist_je.get(rec["schluessel"], Decimal("0.000"))
        grenze = max(Decimal("0.000"), ist - A - rec["menge"])
        if B > grenze:
            raise AbrechnungError(
                f"„{rec['bez']}“ ist bereits über einen Nachtrag abgerechnet "
                f"({B} bereits fakturiert, geliefert sind {ist}). Die Angebotskopie "
                "stellte dieselbe Menge ein zweites Mal in Rechnung — der Nachtrag "
                "hat sie schon als Mehr-/Zusatzleistung erfasst. Wenn die Menge auf "
                "die Angebotsrechnung gehört, ist der Nachtrag zu stornieren; das "
                "gibt die Leistung wieder frei."
            )
    if konflikte:
        raise EinheitUneindeutig(konflikte)


def rechnung_aus_angebot(
    actor_app_user_id,
    *,
    quote_id,
    invoice_date=None,
    due_date=None,
    payment_term_days=None,
    discount_percent=None,
    discount_days=None,
    show_labour_costs=True,
):
    """Erzeugt eine Rechnung im ENTWURF aus einem Angebot (die Angebotskopie).

    * Positionen **1:1 kopiert** (eingefrorene Werte des Angebots).
    * ALTERNATIV/BEDARF **nicht** übernommen (sie waren Optionen).
    * Je übernommener Betragsposition eine `billing_link` (ANGEBOTSPOSITION).
    * Bereits abgerechnete Angebotspositionen (aktive Bindung) → **422**, mit
      Nennung der Rechnung, in der sie stehen.

    **Hier gibt es keine Preisklärung** — und das ist kein Versehen: Ein Angebot
    ohne Preis kann es nicht geben (`quote_line.unit_price` ist Pflicht für jede
    Betragszeile). Der Preis steht seit dem Angebot fest; er wird kopiert, nicht
    neu ermittelt.

    **Anrechenbare Abschläge werden nicht neu erfunden**: Der bestehende Pfad
    (Migration 0060/0061, `beleg.create_invoice(advance_invoice_ids=…)` bzw.
    `set_invoice_advances`) bleibt zuständig.

    Nur ein Angebot, das **hinausgegangen** ist, ist eine Vereinbarung
    (`site_report.SOLL_AUSGESCHLOSSENE_STATUS` — dieselbe Definition wie beim
    Soll-Ist-Abgleich). Ein Entwurf ist keine.
    """
    quote = (
        Quote.objects.filter(id=quote_id)
        .prefetch_related("lines", "rubriken")
        .first()
    )
    if quote is None:
        raise AbrechnungError("Angebot nicht gefunden.")
    if quote.status in report_service.SOLL_AUSGESCHLOSSENE_STATUS:
        raise AbrechnungError(
            f"Das Angebot steht im Status {quote.status} und ist keine "
            "Vereinbarung — daraus lässt sich keine Rechnung erzeugen."
        )
    # Das Tor ist **beidseitig**. `rechnung_aus_auftrag` sperrt PAUSCHAL; ohne den
    # Gegenweg ließe sich auf einem REGIE-Auftrag zusätzlich die Angebotskopie
    # fakturieren. Die Doppelabrechnungssperre finge das NICHT ab: Die beiden
    # Läufe binden verschiedene Quellen (Angebotsposition vs. Berichtsposition/
    # Zeitbuchung) — dieselbe Leistung stünde auf zwei Rechnungen, jede für sich
    # sauber gebunden.
    order = (
        WorkOrder.objects.filter(id=quote.work_order_id).first()
        if quote.work_order_id
        else None
    )
    if order is not None and order.billing_mode == REGIE:
        raise AbrechnungError(
            f"Der Auftrag ist auf {REGIE} eingestellt: Seine Rechnung entsteht aus "
            "dem Ist (unterzeichnete Berichte und Zeitbuchungen). Würde daneben die "
            "Angebotskopie fakturiert, stünde die Leistung zweimal auf der Rechnung "
            "— einmal als vereinbarte Position, einmal als geleistete. Für die "
            "Angebotskopie die Abrechnungsart des Auftrags auf PAUSCHAL stellen."
        )

    lines, rubriken, quelle = _quote_zeilen_kopieren(quote)
    if not quelle:
        raise AbrechnungError(
            "Das Angebot enthält keine abrechenbare Position (Alternativ-, "
            "Bedarfs- und Textzeilen werden nicht in Rechnung gestellt)."
        )

    with as_business_error():
        # EINE Transaktion: Sperre, Prüfung, Beleganlage und Bindung. Läge die
        # Prüfung davor, könnte ein Nebenläufer dazwischen dieselben Positionen
        # abrechnen — der UNIQUE-Index finge es ab, aber als 500.
        with business_transaction(actor_app_user_id):
            # Die auftragsweite Serialisierung ZUERST: Nachtrag und Angebotskopie
            # sperren sonst verschiedene Quellzeilen und liefen aneinander vorbei
            # (Review-Befund, Nebenläufigkeit). Ab hier sieht dieser Lauf jede
            # gleichzeitige Bindung des anderen Wegs — die Mengengrenze unten prüft
            # gegen den frischen Stand.
            _auftrag_sperren(quote.work_order_id)
            _quellen_sperren(quote_line_ids=list(quelle.values()))
            # Die **quellenübergreifende** Sperre (Review-Befund H-2): Der Auftrag
            # ist bereits über das Ist (Bericht/Zeiten) abgerechnet. Die
            # Angebotskopie stellte dieselbe Leistung ein zweites Mal in Rechnung —
            # und keine der drei UNIQUE-Sperren sähe es, weil die Quellen disjunkt
            # sind. Diese Prüfung hängt NICHT am `billing_mode`: Sie muss auch dann
            # greifen, wenn der Modus nach der Regieabrechnung auf PAUSCHAL
            # zurückgestellt wurde.
            ist_rechnungen = _bindungen_am_auftrag(
                quote.work_order_id, source_kinds=_IST_QUELLEN(order)
            )
            if ist_rechnungen:
                raise AbrechnungError(
                    "Dieser Auftrag ist bereits über das Ist abgerechnet "
                    f"(Berichtspositionen bzw. Zeitbuchungen in "
                    f"{', '.join(ist_rechnungen)}). Die Angebotskopie stellte "
                    "dieselbe Leistung ein zweites Mal in Rechnung — einmal als "
                    "vereinbarte, einmal als geleistete. Wenn die Regierechnung "
                    "falsch war, ist sie zu stornieren; das gibt die Leistungen "
                    "wieder frei."
                )
            belegt, _b, _t, _m = _aktive_bindungen(
                quote_line_ids=list(quelle.values())
            )
            if belegt:
                _schon_abgerechnet(
                    "Angebotsposition(en)",
                    [
                        f"Pos. {ql.position_number} ({ql.description})"
                        for ql in sorted(
                            quote.lines.all(), key=lambda l: l.position_number
                        )
                        if ql.id in belegt
                    ],
                    quote_line_id=belegt,
                )
            # **Die quellenunabhängige Mengengrenze (Review-Befund, KRITISCH 2).**
            # Wurde ein Posten schon per Nachtrag (BERICHTSPOSITION) fakturiert,
            # darf die Angebotskopie nicht dieselbe Menge ein zweites Mal buchen.
            # Der `_bindungen_am_auftrag`-Check oben sieht das NICHT: Bei PAUSCHAL
            # gibt `_IST_QUELLEN` nur ZEITBUCHUNG zurück (Berichtsbindungen sind der
            # legitime Nachtrag). Die Grenze ist quantitativ, nicht boolesch.
            _pauschal_mengengrenze_pruefen(order, lines)
            invoice = beleg_service.create_invoice(
                actor_app_user_id,
                property_id=quote.property_id,
                invoice_type="RECHNUNG",
                project_id=quote.project_id,
                work_order_id=quote.work_order_id,
                # Vorgangsbezug vom Angebot übernehmen (Migration 0113).
                service_case_id=quote.service_case_id,
                invoice_date=invoice_date,
                due_date=due_date,
                payment_term_days=payment_term_days,
                discount_percent=discount_percent,
                discount_days=discount_days,
                show_labour_costs=show_labour_costs,
                rubriken=rubriken,
                lines=lines,
            )
            _bindungen_schreiben(
                invoice,
                {
                    pos: (ANGEBOTSPOSITION, {"quote_line_id": ql_id})
                    for pos, ql_id in quelle.items()
                },
            )
    invoice.refresh_from_db()
    return invoice


def _angebot_bereits_abgerechnet_pruefen(work_order_id):
    """Spiegelbild zur Quersperre in `rechnung_aus_angebot` (Review-Befund H-2).

    Ist der Auftrag bereits über **Angebotspositionen** abgerechnet, wäre die
    Regierechnung dieselbe Leistung ein zweites Mal — die Bindungen sähen es nicht,
    weil sie auf verschiedene Quellen zeigen. Die Prüfung hängt bewusst **nicht**
    am `billing_mode`: Sie muss auch dann greifen, wenn der Modus nach der
    Pauschalabrechnung auf REGIE umgestellt wurde.
    """
    angebots_rechnungen = _bindungen_am_auftrag(
        work_order_id, source_kinds=(ANGEBOTSPOSITION,)
    )
    if angebots_rechnungen:
        raise AbrechnungError(
            "Dieser Auftrag ist bereits über das Angebot abgerechnet "
            f"({', '.join(angebots_rechnungen)}). Berichtspositionen und Zeiten "
            "sind dort Nachweis, kein Rechnungsposten — würden sie zusätzlich "
            "fakturiert, stünde die Leistung zweimal in Rechnung. Wenn die "
            "Angebotsrechnung falsch war, ist sie zu stornieren; das gibt die "
            "Leistungen wieder frei."
        )


def _schon_abgerechnet(was, namen, **quell_filter):
    """422 mit Nennung der Positionen UND der Rechnung, die sie schon abrechnet.

    Ohne die Belegnummer bliebe der Nutzer ratlos zurück („abgerechnet — wo?").
    """
    feld, ids = next(iter(quell_filter.items()))
    nummern = sorted(
        {
            (nr or "einem Rechnungsentwurf")
            for nr in BillingLink.objects.filter(
                released_at__isnull=True, **{f"{feld}__in": list(ids)}
            ).values_list("invoice__invoice_number", flat=True)
        }
    )
    raise AbrechnungError(
        f"{was} sind bereits abgerechnet (in {', '.join(nummern)}): "
        f"{'; '.join(namen)}. Dieselbe Leistung wird nicht zweimal berechnet — "
        "wenn die Rechnung falsch war, ist sie zu stornieren."
    )


def _auftrag_sperren(work_order_id):
    """Sperrt die `work_order`-Zeile bis zum Transaktionsende (`SELECT … FOR UPDATE`).

    **Die Serialisierung über die eine Klammer** (Review-Befund, Nebenläufigkeit).
    Die Doppelabrechnungssperre des Nachtrags rechnet über `_billed_je_schluessel`
    quellenübergreifend — aber die Quellzeilen liegen in **verschiedenen** Tabellen
    (`quote_line` für die Angebotskopie, `site_report_line` für den Nachtrag). Zwei
    gleichzeitige Läufe sperren deshalb verschiedene Zeilen und kämen beide durch:
    eine Doppelabrechnung durch **Timing** statt durch Reihenfolge.

    Der Auftrag ist die einzige Zeile, die **beide** Wege gemeinsam haben. Wer die
    Mengengrenze prüft, sperrt ihn deshalb als **erste** Amtshandlung; der zweite
    Lauf wartet, sieht die Bindung des ersten und wird korrekt abgewiesen (422).
    Dasselbe Muster wie `sign_report` gegen die Berichtspositionen (`FOR SHARE`/
    `FOR UPDATE`) — keine Logik dupliziert, keine Migration, kein zweiter
    Wahrheitsort. Ein Angebot ohne Auftrag (`work_order_id is None`) trägt keine
    auftragsweite Mengengrenze — dann ist nichts zu sperren.
    """
    if work_order_id is None:
        return
    list(
        WorkOrder.objects.filter(id=work_order_id)
        .select_for_update()
        .values_list("id", flat=True)
    )


def _quellen_sperren(*, quote_line_ids=None, site_report_line_ids=None,
                     time_entry_ids=None, material_entry_ids=None):
    """Sperrt die Quellzeilen bis zum Transaktionsende (`SELECT … FOR UPDATE`).

    Damit greifen zwei parallele Rechnungsläufe nicht dieselbe Zeitbuchung: Der
    zweite wartet, sieht danach die committete Bindung des ersten und bekommt
    einen sauberen **422** statt eines IntegrityError (500) aus dem UNIQUE-Index.
    Der Index bleibt trotzdem die **letzte** Instanz — die Sperre ist Komfort,
    keine Garantie.
    """
    if quote_line_ids:
        list(
            QuoteLine.objects.filter(id__in=list(quote_line_ids))
            .select_for_update()
            .values_list("id", flat=True)
        )
    if site_report_line_ids:
        list(
            SiteReportLine.objects.filter(id__in=list(site_report_line_ids))
            .select_for_update()
            .values_list("id", flat=True)
        )
    if time_entry_ids:
        list(
            TimeEntry.objects.filter(id__in=list(time_entry_ids))
            .select_for_update()
            .values_list("id", flat=True)
        )
    if material_entry_ids:
        list(
            MaterialEntry.objects.filter(id__in=list(material_entry_ids))
            .select_for_update()
            .values_list("id", flat=True)
        )


def _bindungen_schreiben(invoice, quellen_je_position):
    """Schreibt je Rechnungsposition ihre Bindung (läuft in der offenen Transaktion).

    `quellen_je_position`: {position_number: (source_kind, {quellspalte: id})}.
    Die Zuordnung läuft über die **Positionsnummer** — der Service hat die Zeilen
    selbst in dieser Reihenfolge erzeugt.
    """
    zeilen = {
        l.position_number: l
        for l in InvoiceLine.objects.filter(invoice_id=invoice.id)
    }
    for pos, (kind, quelle) in quellen_je_position.items():
        line = zeilen.get(pos)
        if line is None:  # pragma: no cover — der Service hat sie gerade erzeugt
            raise AbrechnungError(
                f"Rechnungsposition {pos} konnte nicht gebunden werden."
            )
        BillingLink.objects.create(
            id=uuid.uuid4(),
            invoice_id=invoice.id,
            invoice_line_id=line.id,
            source_kind=kind,
            **quelle,
        )


def _regie_quellenkonflikt_pruefen(order, bericht_lines, material_entries):
    """Dieselbe Sache darf nicht über Bericht UND Materialbuchung fakturiert werden.

    **Der Fall, um den es geht** (Invariante Kap. 2, reproduziert mit 178,50 € auf
    zwei Rechnungen): Der Monteur hat am Einsatz zwei „Material"-Wege vor sich —
    die Berichtsposition und die Materialbuchung. Trägt er dieselbe Schraube in
    beide ein, sind das für die Datenbank **zwei verschiedene Quellen**: Die vier
    partiellen UNIQUE-Indizes sichern jede Quelle einzeln und können per
    Konstruktion nicht sehen, dass es dieselbe Schraube ist. Die einzige Klammer,
    die beide zusammenhält, ist der **Auftrag** — also wird hier über ihn geprüft,
    genau wie bei `_pauschal_mengengrenze_pruefen` und `_nachtrag_kandidaten`.

    **Der Schlüssel ist die Artikel-IDENTITÄT ohne Einheit** (`_identitaet`) — das
    ist der Grund, warum „Stk" und „Stück" hier nicht durchrutschen können: Sie
    ergeben denselben Schlüssel. Divergieren die Einheiten trotzdem, ist das die
    aussagekräftigere Diagnose und wird als `EinheitUneindeutig` gemeldet.

    **Geprüft wird, was fakturiert wird — nicht, was erfasst ist.** Eine Seite
    „zählt", wenn dieser Lauf sie abrechnet ODER wenn sie an diesem Auftrag bereits
    abgerechnet ist (`_billed_je_identitaet`). Damit greift die Sperre auch über
    **zwei Rechnungen hinweg** (Bericht in RE-1, Material in RE-2) und der Storno
    löst sie mit, weil er die Bindungen löst.

    **Warum keine Mengenrechnung, sondern fail-closed?** In der Regie gibt es kein
    unabhängiges Soll, gegen das man rechnen könnte: Beide Quellen *behaupten* das
    Ist. Sagt der Bericht 3 Schrauben und die Buchung 3, waren es 3 (Doppel-
    erfassung) — sagt der Bericht 3 und die Buchung 5, weiß niemand, ob es 5 oder 8
    waren. Das System erfindet die Antwort nicht. Es ist dieselbe Haltung, mit der
    `mit_zeiten` schon heute die doppelt erfasste Arbeitszeit behandelt: „Nur ein
    Mensch kann entscheiden, welche von beiden die Wahrheit ist."
    """
    _a, bericht_billed, material_billed = _billed_je_identitaet(order.id)

    def _leer():
        return {"bezeichnung": None, "einheiten": set(), "posten": []}

    bericht_seite = {}
    material_seite = {}

    for line in bericht_lines:
        if line.quantity is None:      # TEXT-Zeile: kein Posten
            continue
        ident = _identitaet(line.source_article_id, line.source_assembly_id,
                            line.description)
        rec = bericht_seite.setdefault(ident, _leer())
        rec["bezeichnung"] = rec["bezeichnung"] or line.description
        rec["einheiten"].add(_einheit_norm(line.unit))
        rec["posten"].append(
            f"Bericht vom {line.site_report.report_date:%d.%m.%Y}, "
            f"Pos. {line.position_number} ({line.quantity} {line.unit or ''})".strip()
        )
    for ident, je_einheit in bericht_billed.items():
        rec = bericht_seite.setdefault(ident, _leer())
        rec["einheiten"] |= set(je_einheit)
        rec["posten"].append("bereits als Berichtsposition abgerechnet")

    for entry in material_entries:
        ident = _identitaet(entry.source_article_id, None, entry.description)
        rec = material_seite.setdefault(ident, _leer())
        rec["bezeichnung"] = rec["bezeichnung"] or entry.description
        rec["einheiten"].add(_einheit_norm(entry.unit))
        rec["posten"].append(f"Buchung „{entry.description}“ "
                             f"({entry.quantity} {entry.unit})")
    for ident, je_einheit in material_billed.items():
        rec = material_seite.setdefault(ident, _leer())
        rec["einheiten"] |= set(je_einheit)
        rec["posten"].append("bereits als Materialbuchung abgerechnet")

    doppel = []
    konflikte = []
    for ident, b in sorted(bericht_seite.items()):
        m = material_seite.get(ident)
        if m is None:
            continue
        bez = b["bezeichnung"] or m["bezeichnung"] or ident
        einheiten = b["einheiten"] | m["einheiten"]
        if len(einheiten) > 1:
            konflikte.append({
                "identitaet": ident,
                "bezeichnung": bez,
                "einheiten": sorted(einheiten),
            })
        else:
            doppel.append((bez, b["posten"], m["posten"]))

    if doppel:
        # Die eindeutige Doppelerfassung zuerst: Sie ist die häufigere und die mit
        # dem klareren Ausweg. Der Einheiten-Konflikt ist eine Verfeinerung
        # desselben Befunds und kommt danach dran.
        namen = "; ".join(
            f"„{bez}“ ({' + '.join(b_posten)} — UND — {' + '.join(m_posten)})"
            for bez, b_posten, m_posten in doppel
        )
        raise AbrechnungError(
            "Folgendes Material ist an diesem Auftrag ZWEIMAL erfasst — einmal im "
            f"Baustellenbericht und einmal als Materialbuchung am Einsatz: {namen}. "
            "Beide behaupten dieselbe Sache („das ist verbaut worden“); zusammen "
            "fakturiert stünde es zweimal auf der Rechnung. Welche der beiden "
            "Erfassungen die Wahrheit ist, entscheidet ein Mensch, nicht der "
            "Abrechnungslauf: Entweder nur die Berichtspositionen abrechnen "
            "(mit_material = false) oder nur das gebuchte Material "
            "(mit_berichten = false) — oder die doppelte Erfassung am Einsatz "
            "korrigieren, solange das Korrekturfenster (B-28) offen ist. Ist eine "
            "Seite bereits fakturiert, gibt ein Storno sie wieder frei."
        )
    if konflikte:
        raise EinheitUneindeutig(konflikte)


# ---------------------------------------------------------------------------
# Auftrag → Rechnung (REGIE): Bericht + Zeiten + Material
# ---------------------------------------------------------------------------

def rechnung_aus_auftrag(
    actor_app_user_id,
    *,
    work_order_id,
    tax_code,
    preise=None,
    mit_berichten=True,
    mit_zeiten=True,
    mit_material=True,
    invoice_date=None,
    due_date=None,
    payment_term_days=None,
    discount_percent=None,
    discount_days=None,
    show_labour_costs=True,
):
    """Erzeugt eine Rechnung im ENTWURF aus **Bericht + Zeiten + Material** (Regieweg).

    Nur bei `billing_mode = REGIE`. Bei **PAUSCHAL** wäre das eine
    Doppelabrechnung: Das Angebot enthält die Leistung bereits, Zeiten und
    Berichtspositionen sind dort **Nachweis**, kein Rechnungsposten.

    `tax_code` ist **Pflicht und wird nicht geraten**: Welcher Steuersatz gilt
    (19 %, 7 %, § 13b, steuerfrei), ist eine steuerliche Entscheidung des Belegs,
    keine Eigenschaft der Zeitbuchung. Ein erfundener Steuersatz auf einem
    GoBD-festgeschriebenen Beleg wäre ein Fehler mit Finanzamtsfolgen.

    `preise` ({quelle_id → Einzelpreis}) ist die **Klärung des Menschen** für
    Positionen, deren Preis der Server nicht kennt (`PreisUnbekannt.positionen`
    nennt sie strukturiert). Ein Preis für eine Position, die der Server bepreisen
    kann, wird **abgelehnt** — die eine Rechenstelle darf nicht über den Umweg
    „Preis nennen" unterlaufen werden. Summen, Steuer und Gesamt rechnet
    **weiterhin ausschließlich der Server**.

    `mit_berichten` / `mit_zeiten` / `mit_material` sind bewusst schaltbar: Führt
    der Bericht die Arbeitszeit **schon als Position** und läuft daneben die
    Stempeluhr, stünde dieselbe Stunde zweimal auf der Rechnung. Die Bindung
    verhindert das nicht — es sind zwei verschiedene Quellen. Nur ein Mensch kann
    entscheiden, welche von beiden die Wahrheit ist. Dasselbe gilt seit Migration
    0139 für Material: Es lässt sich als **Berichtsposition** und als
    **Materialbuchung** erfassen. Anders als bei der Zeit bleibt es hier aber nicht
    beim Schalter — `_regie_quellenkonflikt_pruefen` **erkennt** die Doppel-
    erfassung an der Artikel-Identität und weist den Lauf ab, statt sie auf die
    Rechnung zu lassen.

    Tor **B-08** bleibt unangetastet: Der Beleg entsteht im ENTWURF; die
    Veröffentlichung verlangt weiterhin den kaufmännisch geprüften Auftrag (DB).
    """
    order = WorkOrder.objects.filter(id=work_order_id).first()
    if order is None:
        raise AbrechnungError("Auftrag nicht gefunden.")
    if order.billing_mode != REGIE:
        raise AbrechnungError(
            f"Der Auftrag ist auf {order.billing_mode} eingestellt: Seine Rechnung "
            "ist die Angebotskopie. Berichtspositionen und Zeiten sind Nachweis "
            "und interne Nachkalkulation — würden sie zusätzlich fakturiert, "
            "stünde die Leistung zweimal auf der Rechnung. Für die Regieabrechnung "
            "die Abrechnungsart des Auftrags auf REGIE stellen."
        )
    _angebot_bereits_abgerechnet_pruefen(order.id)
    if not (mit_berichten or mit_zeiten or mit_material):
        raise AbrechnungError(
            "Weder Berichtspositionen noch Zeiten noch Materialbuchungen sollen "
            "abgerechnet werden — es gibt nichts zu tun."
        )
    if not TaxCode.objects.filter(code=tax_code).exists():
        raise AbrechnungError(f"Unbekannter Steuercode '{tax_code}' (z. B. DE_19).")
    genannte = _preise_normalisieren(preise)

    bericht_lines = _berichtspositionen(order.id) if mit_berichten else []
    zeiten = _zeitbuchungen(order.id) if mit_zeiten else []
    material = _materialbuchungen(order.id) if mit_material else []
    _q, belegte_bl, belegte_te, belegte_me = _aktive_bindungen(
        site_report_line_ids=[l.id for l in bericht_lines],
        time_entry_ids=[t.id for t in zeiten],
        material_entry_ids=[m.id for m in material],
    )
    # Was schon abgerechnet ist, kommt nicht noch einmal — kein Fehler, sondern
    # der Normalfall der zweiten Abrechnungsrunde.
    bericht_lines = [l for l in bericht_lines if l.id not in belegte_bl]
    zeiten = [t for t in zeiten if t.id not in belegte_te]
    material = [m for m in material if m.id not in belegte_me]

    # **Der Geld-Wächter am Auftrag** (vor jeder Preisermittlung, damit der Nutzer
    # den Konflikt sieht statt einer Preisklärung, die ins Leere führt): Dieselbe
    # Sache darf nicht über Bericht UND Materialbuchung fakturiert werden.
    _regie_quellenkonflikt_pruefen(order, bericht_lines, material)

    entwuerfe = _entwurfsberichte(order.id)
    if not bericht_lines and not zeiten and not material:
        raise AbrechnungError(
            "Es gibt nichts abzurechnen: keine offenen Berichtspositionen, keine "
            "offenen Zeitbuchungen und kein offenes Material."
            + (
                f" Achtung: {len(entwuerfe)} Bericht(e) sind noch nicht "
                "unterzeichnet und fließen deshalb nicht ein."
                if entwuerfe
                else ""
            )
        )

    regelwerk = matrix_service.lade_regelwerk()
    klaerungen = []
    lines = []
    quellen = {}            # position_number → (source_kind, {spalte: id})
    zeit_quellen = []       # (position_number, [TimeEntry, …])

    for line in bericht_lines:
        preis, ek, klaerung = _bericht_klaerung(line, regelwerk)
        genannt = _genannter_preis(
            genannte, line.id,
            bezeichnung=f"Bericht Pos. {line.position_number}: {line.description}",
            server_preis=preis,
        )
        if genannt is not None:
            preis, klaerung = genannt, None
        if klaerung is not None:
            klaerungen.append(klaerung)
            continue
        pos = len(lines) + 1
        lines.append({
            "line_type": line.line_type,
            "description": line.description,
            "quantity": line.quantity,
            "unit": line.unit,
            "unit_price": preis,
            "unit_cost": ek,
            "tax_code": tax_code,
            "source_article_id": line.source_article_id,
            "source_assembly_id": line.source_assembly_id,
        })
        quellen[pos] = (BERICHTSPOSITION, {"site_report_line_id": line.id})

    for entry in material:
        preis, ek, klaerung = _material_klaerung(entry, regelwerk)
        genannt = _genannter_preis(
            genannte, entry.id,
            bezeichnung=f"Materialbuchung: {entry.description}",
            server_preis=preis,
        )
        if genannt is not None:
            preis, klaerung = genannt, None
        if klaerung is not None:
            klaerungen.append(klaerung)
            continue
        pos = len(lines) + 1
        lines.append({
            # Material ist § 35a NICHT begünstigt — `_arbeitskosten_anteil` leitet
            # das aus dem Positionstyp ab. Eine Materialbuchung IST Material; ein
            # anderer Typ wäre hier eine Behauptung ohne Grundlage.
            "line_type": "MATERIAL",
            "description": entry.description,
            "quantity": entry.quantity,
            "unit": entry.unit,
            "unit_price": preis,
            "unit_cost": ek,
            "tax_code": tax_code,
            "source_article_id": entry.source_article_id,
        })
        quellen[pos] = (MATERIALBUCHUNG, {"material_entry_id": entry.id})

    for gruppe in _zeitgruppen(zeiten):
        satz, ek, klaerung = _zeit_klaerung(gruppe)
        genannt = _genannter_preis(
            genannte, gruppe["key"],
            bezeichnung=gruppe["bezeichnung"], server_preis=satz,
        )
        if genannt is not None:
            satz, klaerung = genannt, None
        if klaerung is not None:
            klaerungen.append(klaerung)
            continue
        stunden = _stunden(gruppe["sekunden"])
        if stunden <= 0:
            # Weniger als 1,8 Sekunden in der ganzen Gruppe: die DB verlangt
            # quantity > 0. Nicht stillschweigend übergehen — die Buchungen blieben
            # sonst „offen" und niemand wüsste warum.
            raise AbrechnungError(
                f"„{gruppe['bezeichnung']}“: die erfassten Zeiten ergeben gerundet "
                "0,000 Stunden und lassen sich nicht abrechnen."
            )
        pos = len(lines) + 1
        lines.append({
            "line_type": "ARBEITSZEIT",
            "description": gruppe["bezeichnung"],
            "quantity": stunden,
            "unit": "h",
            "unit_price": satz,
            "unit_cost": ek,
            "tax_code": tax_code,
        })
        zeit_quellen.append((pos, gruppe["entries"]))

    if genannte:
        # Ein Preis für eine Quelle, die in diesem Lauf gar nicht vorkommt: Der
        # Aufrufer meint etwas anderes, als er sagt (vielleicht eine längst
        # abgerechnete Position). Nicht stillschweigend verschlucken.
        raise AbrechnungError(
            "Für folgende Quellen wurde ein Preis genannt, die in diesem "
            f"Abrechnungslauf nicht vorkommen: {', '.join(sorted(genannte))}."
        )
    if klaerungen:
        raise PreisUnbekannt(klaerungen)

    with as_business_error():
        with business_transaction(actor_app_user_id):
            # Auftragsweite Serialisierung ZUERST (Review-Befund, Nebenläufigkeit):
            # `_angebot_bereits_abgerechnet_pruefen` prüft quellenübergreifend; ohne
            # die Auftragssperre könnte eine gleichzeitige Angebotsrechnung dazwischen
            # committen, ohne dass die Zeilensperren es sähen (andere Tabelle).
            _auftrag_sperren(order.id)
            quell_bl = [
                q["site_report_line_id"]
                for k, q in quellen.values()
                if k == BERICHTSPOSITION
            ]
            quell_te = [e.id for _p, es in zeit_quellen for e in es]
            quell_me = [
                q["material_entry_id"]
                for k, q in quellen.values()
                if k == MATERIALBUCHUNG
            ]
            _quellen_sperren(
                site_report_line_ids=quell_bl, time_entry_ids=quell_te,
                material_entry_ids=quell_me,
            )
            # Nach der Sperre erneut prüfen: Ein Nebenläufer kann zwischen der
            # Vorauswahl und der Sperre committet haben (READ COMMITTED). Das gilt
            # auch für die Angebotsrechnung desselben Auftrags — sie greift andere
            # Quellen, läuft also nicht gegen dieselbe Zeilensperre.
            _angebot_bereits_abgerechnet_pruefen(order.id)
            # Und ebenso der Quellenkonflikt Bericht↔Material: Ein Nebenläufer kann
            # die Gegenseite committet haben, während dieser Lauf seine Preise
            # ermittelte. Unter der Auftragssperre sieht er jetzt den frischen Stand.
            _regie_quellenkonflikt_pruefen(
                order,
                [l for l in bericht_lines if l.id in set(quell_bl)],
                [m for m in material if m.id in set(quell_me)],
            )
            _q, belegte_bl, belegte_te, belegte_me = _aktive_bindungen(
                site_report_line_ids=quell_bl, time_entry_ids=quell_te,
                material_entry_ids=quell_me,
            )
            if belegte_bl or belegte_te or belegte_me:
                raise AbrechnungError(
                    "Ein Teil der Berichtspositionen, Zeitbuchungen bzw. "
                    "Materialbuchungen wurde soeben von einem anderen Vorgang "
                    "abgerechnet. Bitte den Vorgang wiederholen — die offene "
                    "Abrechnung hat sich geändert."
                )
            invoice = beleg_service.create_invoice(
                actor_app_user_id,
                property_id=order.property_id,
                invoice_type="RECHNUNG",
                project_id=order.project_id,
                work_order_id=order.id,
                # Vorgangsbezug vom Auftrag übernehmen (Migration 0113).
                service_case_id=order.service_case_id,
                invoice_date=invoice_date,
                due_date=due_date,
                payment_term_days=payment_term_days,
                discount_percent=discount_percent,
                discount_days=discount_days,
                show_labour_costs=show_labour_costs,
                lines=lines,
            )
            _bindungen_schreiben(invoice, quellen)
            # Zeitbuchungen: n Buchungen binden an EINE Positionszeile (die
            # Sammelposition der Zeitgruppe). Die Bindung ist trotzdem **je
            # Buchung** — sonst ließe sich eine einzelne Stunde später doch noch
            # ein zweites Mal abrechnen.
            zeilen = {
                l.position_number: l
                for l in InvoiceLine.objects.filter(invoice_id=invoice.id)
            }
            for pos, entries in zeit_quellen:
                for entry in entries:
                    BillingLink.objects.create(
                        id=uuid.uuid4(),
                        invoice_id=invoice.id,
                        invoice_line_id=zeilen[pos].id,
                        source_kind=ZEITBUCHUNG,
                        time_entry_id=entry.id,
                    )
    invoice.refresh_from_db()
    return invoice


# ---------------------------------------------------------------------------
# Nachtrag (PAUSCHAL): die Rechnung aus den ABWEICHUNGEN
# ---------------------------------------------------------------------------
#
# Der Mehrverbrauch war bisher **sichtbar, aber nicht abrechenbar**: Der Soll-Ist
# wies ihn sauber aus, `rechnung_aus_auftrag` ist bei PAUSCHAL gesperrt (zu Recht
# — sie fakturierte die pauschal beauftragte Leistung ein zweites Mal), und das
# Büro tippte die Nachtragsrechnung von Hand ab. Genau die Handarbeit, die dieses
# System beseitigen soll.
#
# **Die fachliche Regel** (Herleitung aus dem Modulkopf, nicht neu erfunden):
#
# * **MEHRVERBRAUCH** → abrechenbar ist **nur die Differenzmenge**. Die Sollmenge
#   ist mit der Pauschale bezahlt; hätte man 19 statt 1 fakturiert, stünden 18
#   Stück doppelt auf der Rechnung.
# * **ZUSATZ** → abrechenbar ist die **volle Menge**. Sie war nie Teil der
#   Pauschale (es gibt kein Soll dafür). Rechnerisch derselbe Satz: `Ist − Soll`,
#   mit Soll = 0.
# * **MINDERVERBRAUCH / ENTFALLEN mindern die Pauschale NICHT.** Pauschal ist der
#   **Preis für das Werk** vereinbart, nicht ein Mengengerüst zum Stückpreis; wer
#   das Werk mit weniger Material schafft, hat es dennoch geschuldet und erbracht.
#   Das ist exakt die Aussage, die schon im Modulkopf steht („Das Soll-Ist bleibt
#   die **interne Nachkalkulation**") — hier wird sie nur zu Ende gedacht. Eine
#   Minderung wäre eine **Preisänderung**, und die trifft ein Mensch, nicht ein
#   Abrechnungslauf (Weg dafür: Rabatt oder Gutschrift).
#
# **Die Bindung ist Pflicht — auch hier.** Jede Nachtragsposition bindet die
# Berichtspositionen, aus denen ihre Mehrmenge stammt (`billing_link`,
# `source_kind = BERICHTSPOSITION`). Ohne sie ließe sich derselbe Mehrverbrauch
# beliebig oft fakturieren; die Sperre säße dann im Service statt in der DB — und
# was im Service sitzt, ist umgehbar. Der **Storno löst die Bindung** (Trigger)
# und macht die Mehrmenge wieder abrechenbar: Sie wurde ja erbracht.
#
# **Warum die Menge NICHT einfach aus den Bindungen EINER Quelle folgt** (die zwei
# Doppelabrechnungen aus dem Review): Eine Berichtsposition trägt die **Ist-Menge**
# (19), fakturiert wird die **Differenz** (1). Es genügt aber NICHT, nur die über
# `BERICHTSPOSITION` fakturierte Menge abzuziehen — dieselbe Sache kann auch über
# die **Angebotskopie** (`ANGEBOTSPOSITION`) schon fakturiert sein. Und das Soll ist
# **volatil**: Wird das Angebot nach der Angebotsrechnung abgelehnt, fällt sein Soll
# auf 0, und der Nachtrag hielte plötzlich die ganzen 19 für offen — obwohl 18 davon
# längst auf der Angebotsrechnung stehen. Deshalb rechnet der Nachtrag gegen die
# **quellenunabhängige** Menge je Schlüssel (`_billed_je_schluessel`):
#
#   pauschal_floor = max(A, Soll)        # A = über ANGEBOTSPOSITION fakturiert
#   offen          = max(0, Ist − pauschal_floor − B)   # B = über BERICHTSPOSITION
#
# `pauschal_floor` ist die Menge, die die Pauschale deckt — sei es bereits
# fakturiert (A) oder erst zugeordnet und noch zu fakturieren (Soll). Der Nachtrag
# rechnet nur, was **darüber** liegt, und zieht ab, was er selbst schon fakturiert
# hat (B). Kommt ein weiterer Bericht dazu, wird nur der Zuwachs abgerechnet; die
# **neuen** Zeilen tragen die Bindung, die alten sind längst gebunden. Die
# Gegenrichtung — die Angebotsrechnung darf nicht in eine schon per Nachtrag
# fakturierte Menge hineinbuchen — steht in `_pauschal_mengengrenze_pruefen`.

def _nachtrag_kandidaten(order):
    """Die abrechenbaren Abweichungen eines Auftrags (MEHRVERBRAUCH + ZUSATZ).

    Rechnet auf **derselben Rechenstelle** wie der Bildschirm
    (`site_report.abgleich`) — aber `nur_unterzeichnet=True`: Ein Entwurfsbericht
    ist ein Nachweis, den niemand abgenommen hat, und keine Abrechnungsgrundlage
    (dieselbe Grenze wie `_berichtspositionen`). Der Nutzer erfährt trotzdem, dass
    es sie gibt (`_entwurfsberichte`) — die häufigste Ursache einer „zu kleinen"
    Nachtragsrechnung.

    Die abgerechnete Menge folgt der **quellenunabhängigen** Formel (Modulkopf):
    `offen = max(0, Ist − max(A, Soll) − B)`. `A`/`B` sind die über
    ANGEBOTSPOSITION bzw. BERICHTSPOSITION je Schlüssel bereits fakturierten Mengen.
    """
    ergebnis = report_service.abgleich(order, nur_unterzeichnet=True)
    angebot_billed, bericht_billed, material_billed = _billed_je_identitaet(order.id)
    kandidaten = []
    for pos in ergebnis["positionen"]:
        if pos["art"] not in (report_service.MEHRVERBRAUCH, report_service.ZUSATZ):
            continue
        lines = pos["lines"]
        # Die noch UNgebundenen Berichtszeilen tragen die neue Bindung; die
        # gebundenen sind per UNIQUE-Index physisch belegt.
        aktive = list(
            BillingLink.objects.filter(
                site_report_line_id__in=[l.id for l in lines],
                released_at__isnull=True,
            ).select_related("invoice")
        )
        gebunden = {l.site_report_line_id for l in aktive}

        # **Identitätsbasiert, nicht schlüsselbasiert** (Review-Befund, dritter Weg):
        # A/B werden über die Artikel-/Leistungs-Identität gesucht, damit „Stk" und
        # „Stück" desselben Artikels nicht als zwei Posten durchrutschen. Führt die
        # Identität aber MEHRERE Einheiten, sind die Mengen nicht vergleichbar → der
        # Kandidat geht in die Klärung (fail-closed), nicht auf einen Beleg.
        ident = _identitaet(
            pos["source_article_id"], pos["source_assembly_id"], pos["bezeichnung"]
        )
        einheit = _einheit_norm(pos["einheit"])
        A, B, einheiten = _billed_fuer(
            ident, einheit, angebot_billed, bericht_billed, material_billed
        )
        einheit_konflikt = len(einheiten) > 1

        # Die Pauschale deckt, was fakturiert IST (A) oder zugeordnet ist und noch
        # fakturiert WIRD (Soll) — je nachdem, was größer ist. Nur darüber rechnet
        # der Nachtrag. Ohne das `max(A, …)` bräche die Doppelabrechnung auf, sobald
        # das Soll nach der Angebotsrechnung fällt (Angebot abgelehnt). Bei
        # Einheiten-Konflikt ist diese Zahl bedeutungslos (A/B liegen unter anderer
        # Einheit) — der Kandidat wird ohnehin geklärt, nicht abgerechnet.
        pauschal_floor = max(A, pos["soll"])
        offen = (pos["ist"] - pauschal_floor - B).quantize(
            _Q_MENGE, rounding=ROUND_HALF_UP
        )
        if offen < 0:
            # Mehr gedeckt/berechnet als heute abrechenbar wäre. Nichts offen.
            # Keine Gutschrift von selbst — die entscheidet ein Mensch.
            offen = Decimal("0.000")
        kandidaten.append({
            "schluessel": pos["schluessel"],
            "art": pos["art"],
            "bezeichnung": pos["bezeichnung"],
            "einheit": pos["einheit"],
            "soll": pos["soll"],
            "ist": pos["ist"],
            "differenz": pos["differenz"],
            # Was der Posten bereits gekostet hat — quellenübergreifend (A+B), damit
            # der Nutzer die Angebotskopie mitsieht, nicht nur frühere Nachträge.
            "bereits_berechnet": A + B,
            "menge": offen,
            "einheit_konflikt": einheit_konflikt,
            "konflikt_einheiten": sorted(einheiten) if einheit_konflikt else [],
            "source_article_id": pos["source_article_id"],
            "source_assembly_id": pos["source_assembly_id"],
            "bindbar": [l for l in lines if l.id not in gebunden],
            "rechnungen": sorted(
                {
                    (link.invoice.invoice_number or "einem Rechnungsentwurf")
                    for link in aktive
                }
            ),
            # Die Positionsart der Rechnungszeile kommt aus dem Bericht (§ 35a:
            # ARBEITSZEIT ist voll begünstigt, MATERIAL nicht — `_arbeitskosten_anteil`
            # leitet daraus ab). Deterministisch die erste Zeile des Schlüssels;
            # Artikel und Einheit sind über den Schlüssel ohnehin identisch.
            "line_type": lines[0].line_type if lines else "MATERIAL",
        })
    return kandidaten, ergebnis


def _nachtrag_klaerung(kandidat, regelwerk):
    """(preis, ek, klaerung|None) einer Abweichungsposition.

    **Dieselbe** Preisermittlung wie überall (`_artikel_preis` / `_leistung_preis`)
    und **derselbe** Klärungsweg (422 mit `PreisUnbekannt`). Kein zweiter Pfad: Der
    Nachtrag darf so wenig mit 0,00 € durchrutschen wie die Regierechnung — er ist
    sogar der gefährlichere Fall, weil ihn niemand gegen ein Angebot prüft.
    """
    def _klaerung(grund, text, vorschlaege):
        return {
            "quelle_art": QUELLE_ABWEICHUNG,
            "quelle_id": kandidat["schluessel"],
            "bezeichnung": kandidat["bezeichnung"],
            "menge": kandidat["menge"],
            "einheit": kandidat["einheit"],
            "grund": grund,
            "grund_text": text,
            "vorschlaege": vorschlaege,
        }

    if kandidat["source_article_id"]:
        vk, ek, grund = _artikel_preis(
            kandidat["source_article_id"], kandidat["menge"] or Decimal(1), regelwerk
        )
        if vk is not None:
            return vk, ek, None
        return None, ek, _klaerung(
            grund, _GRUND_TEXTE[grund],
            _vorschlaege_artikel(kandidat["source_article_id"]),
        )

    if kandidat["source_assembly_id"]:
        assembly = Assembly.objects.filter(id=kandidat["source_assembly_id"]).first()
        if assembly is None:
            vk, grund = None, GRUND_LEISTUNG_UNVOLLSTAENDIG
        else:
            vk, grund = _leistung_preis(assembly, regelwerk)
        if vk is not None:
            return vk, None, None
        letzter = _letzter_berechneter_preis(
            assembly_id=kandidat["source_assembly_id"]
        )
        text = (
            "Die Bestandteile der Leistung ergeben in Summe 0,00 € — das ist kein "
            "Preis, sondern eine Lücke in der Stückliste. Die Leistung wird NICHT "
            "gratis abgerechnet."
            if grund == GRUND_VK_NULL
            else _GRUND_TEXTE[GRUND_LEISTUNG_UNVOLLSTAENDIG]
        )
        return None, None, _klaerung(grund, text, [letzter] if letzter else [])

    # Freitextposition ohne Artikel-/Leistungsbezug (der Monteur hat sie von Hand
    # erfasst). Der Server kann daraus keinen Preis rechnen — aber der Mensch kann
    # ihn nennen. Keine Sackgasse, keine 0 €.
    return None, None, _klaerung(
        GRUND_KEINE_HERKUNFT, _GRUND_TEXTE[GRUND_KEINE_HERKUNFT], []
    )


_NACHTRAG_ZUSATZ = {
    report_service.MEHRVERBRAUCH: "Mehrmenge",
    report_service.ZUSATZ: "Zusatzleistung",
}


def _nachtrag_bezeichnung(kandidat):
    """Die Bezeichnung der Rechnungsposition — mit benanntem Grund.

    Der Kunde soll auf dem Beleg **sehen**, warum die Position da ist: nicht „18
    Thermostatventile" (die sind mit der Pauschale bezahlt), sondern „1
    Thermostatventil (Mehrmenge gegenüber Angebot)". Eine Nachtragsrechnung, die
    aussieht wie die Pauschalrechnung, ist die nächste Rückfrage.
    """
    return f"{kandidat['bezeichnung']} ({_NACHTRAG_ZUSATZ[kandidat['art']]} gegenüber Angebot)"


def nachtrag_vorschau(work_order_id):
    """Was lässt sich an diesem PAUSCHAL-Auftrag als **Nachtrag** abrechnen?

    Die ehrliche Antwort — auch wenn sie „nichts" lautet: Ein Knopf, der nichts
    tut und nicht sagt warum, ist schlimmer als kein Knopf.

    * `positionen` — MEHRVERBRAUCH (nur die Differenz) und ZUSATZ (volle Menge),
      jeweils **abzüglich dessen, was schon fakturiert ist**. Mit Preis und
      Zeilenbetrag; ein **unbekannter Preis wird als unbekannt ausgewiesen**, nie
      als 0,00 € (`preis_status`, wie in `offene_abrechnung`).
    * `bereits_abgerechnet` — Abweichungen, die es gibt, deren Mehrmenge aber
      bereits in einer Rechnung steht (mit Belegnummer). Sonst stünde der Nutzer
      vor einer leeren Liste und hielte den Soll-Ist für kaputt.
    * `nicht_unterzeichnete_berichte` — ihre Mengen fließen **nicht** ein.
    """
    order = WorkOrder.objects.filter(id=work_order_id).first()
    if order is None:
        raise AbrechnungError("Auftrag nicht gefunden.")

    kandidaten, _abgleich = _nachtrag_kandidaten(order)
    regelwerk = matrix_service.lade_regelwerk()

    positionen = []
    bereits_abgerechnet = []
    einheit_konflikte = []
    summe = Decimal("0.00")
    preise_unbekannt = False
    for k in kandidaten:
        if k.get("einheit_konflikt"):
            # Fail-closed sichtbar machen, BEVOR jemand fakturiert: derselbe Artikel
            # steht schon unter einer anderen Einheit in Rechnung — nicht summierbar,
            # nicht abrechenbar, bis ein Mensch die Einheiten vereinheitlicht.
            einheit_konflikte.append({
                "schluessel": k["schluessel"],
                "bezeichnung": k["bezeichnung"],
                "einheiten": k["konflikt_einheiten"],
            })
            continue
        if k["menge"] <= 0:
            bereits_abgerechnet.append({
                "schluessel": k["schluessel"],
                "art": k["art"],
                "bezeichnung": k["bezeichnung"],
                "einheit": k["einheit"],
                "menge": k["differenz"],
                "rechnungen": k["rechnungen"],
            })
            continue
        preis, _ek, klaerung = _nachtrag_klaerung(k, regelwerk)
        if preis is None:
            preise_unbekannt = True
        else:
            summe += _round2(preis * k["menge"])
        positionen.append({
            "schluessel": k["schluessel"],
            "art": k["art"],
            "bezeichnung": k["bezeichnung"],
            "einheit": k["einheit"],
            "soll": k["soll"],
            "ist": k["ist"],
            "menge": k["menge"],
            "bereits_berechnet": k["bereits_berechnet"],
            "preis_status": PREIS_BEKANNT if preis is not None else PREIS_UNBEKANNT,
            "einzelpreis": preis,
            "betrag": _round2(preis * k["menge"]) if preis is not None else None,
            "grund": klaerung["grund"] if klaerung else None,
            "grund_text": klaerung["grund_text"] if klaerung else None,
            "vorschlaege": klaerung["vorschlaege"] if klaerung else [],
        })

    return {
        "work_order_id": order.id,
        "billing_mode": order.billing_mode,
        # Der Nachtrag ist der PAUSCHAL-Fall. Bei REGIE wird ohnehin das ganze Ist
        # fakturiert (`rechnung_aus_auftrag`) — ein Nachtrag daneben wäre eine
        # Doppelabrechnung.
        "abrechenbar": order.billing_mode == PAUSCHAL,
        "hinweis": (
            "Nachtrag: Abgerechnet werden ausschließlich die Abweichungen — "
            "Mehrverbrauch nur mit der Differenzmenge, Zusatzleistungen mit der "
            "vollen Menge. Minderverbrauch und entfallene Positionen mindern die "
            "Pauschale nicht: Pauschal ist der Preis für das Werk vereinbart, "
            "nicht ein Mengengerüst."
            if order.billing_mode == PAUSCHAL
            else "Regieabrechnung: Das gesamte Ist wird fakturiert (Berichts-"
                 "positionen und Zeiten). Einen Nachtrag gibt es hier nicht — er "
                 "rechnete dieselbe Leistung ein zweites Mal ab."
        ),
        "positionen": positionen,
        "bereits_abgerechnet": bereits_abgerechnet,
        # Fail-closed: derselbe Artikel in verschiedenen Einheiten — nicht
        # abrechenbar, bis ein Mensch entscheidet. Ehrlich ausgewiesen, nicht still.
        "einheit_konflikte": einheit_konflikte,
        "summe": summe,
        # Ehrlich: Die Summe ist nur die der bepreisbaren Positionen.
        "preise_unbekannt": preise_unbekannt,
        "nicht_unterzeichnete_berichte": [
            {"id": r.id, "report_date": r.report_date, "status": r.status,
             "activity_text": r.activity_text}
            for r in _entwurfsberichte(order.id)
        ],
    }


def rechnung_aus_nachtrag(
    actor_app_user_id,
    *,
    work_order_id,
    tax_code,
    preise=None,
    invoice_date=None,
    due_date=None,
    payment_term_days=None,
    discount_percent=None,
    discount_days=None,
    show_labour_costs=True,
):
    """Erzeugt eine Rechnung (ENTWURF) über die **Abweichungen** eines
    PAUSCHAL-Auftrags.

    Nur MEHRVERBRAUCH (Differenzmenge) und ZUSATZ (volle Menge), nur aus
    **unterzeichneten** Berichten, nur was noch nicht fakturiert ist. Jede Position
    bindet ihre Berichtszeilen (`billing_link`) — die Doppelabrechnung ist damit
    **physisch** gesperrt, nicht bloß im Service geprüft.

    `tax_code` ist **Pflicht und wird nicht geraten** (wie bei `rechnung_aus_auftrag`):
    Welcher Steuersatz gilt, ist eine steuerliche Entscheidung des Belegs. Den
    Steuersatz der Angebotsposition zu übernehmen wäre eine plausible Vermutung —
    und eine Vermutung auf einem GoBD-festgeschriebenen Beleg ist genau das, was
    dieses Modul nirgends tut.

    `preise` ist der **bestehende** Klärungsweg (`PreisUnbekannt`): genannt werden
    darf ein Preis nur, wo der Server keinen hat. Eine Nachtragsposition mit
    0,00 € gibt es nicht.
    """
    order = WorkOrder.objects.filter(id=work_order_id).first()
    if order is None:
        raise AbrechnungError("Auftrag nicht gefunden.")
    if order.billing_mode != PAUSCHAL:
        raise AbrechnungError(
            f"Der Auftrag ist auf {order.billing_mode} eingestellt: Dort wird das "
            "gesamte Ist fakturiert (Berichtspositionen und Zeiten). Ein Nachtrag "
            "daneben stellte dieselbe Mehrmenge ein zweites Mal in Rechnung. Der "
            "Nachtrag ist der PAUSCHAL-Fall."
        )
    # Zeiten und am Einsatz gebuchtes Material fakturiert ausschließlich die
    # Regie. Solche Bindungen an einem PAUSCHAL-Auftrag kann es nach
    # `set_billing_mode` nicht geben — geprüft wird es trotzdem: Die Sperre soll
    # halten, auch wenn jemand den Modus künftig auf einem anderen Weg setzt.
    # (Genau dieser Fehler — eine Sperre, die an einem nie gesetzten Zustand hing —
    # kostete in Welle 5 einen zweiten Anlauf.)
    regie = _bindungen_am_auftrag(
        order.id, source_kinds=(ZEITBUCHUNG, MATERIALBUCHUNG)
    )
    if regie:
        raise AbrechnungError(
            f"Dieser Auftrag ist bereits über Zeit-/Materialbuchungen abgerechnet "
            f"({', '.join(regie)}) — das ist eine Regieabrechnung über das gesamte "
            "Ist. Ein Nachtrag daneben fakturierte die Mehrmenge ein zweites Mal."
        )
    if not TaxCode.objects.filter(code=tax_code).exists():
        raise AbrechnungError(f"Unbekannter Steuercode '{tax_code}' (z. B. DE_19).")
    genannte = _preise_normalisieren(preise)

    # **Die Mengenentscheidung fällt UNTER der Auftragssperre** (Review-Befund,
    # Nebenläufigkeit): `_billed_je_schluessel` rechnet quellenübergreifend, aber
    # die Quellzeilen liegen in verschiedenen Tabellen. Nur wenn Prüfen UND
    # Schreiben unter der `work_order`-Sperre stehen, serialisieren zwei
    # gleichzeitige Läufe. Deshalb liegt die ganze Ermittlung — inkl. der
    # Preisklärung — jetzt in der Transaktion; ein 422/PreisUnbekannt rollt sie
    # ohne Schaden zurück (es wurde nichts geschrieben).
    with as_business_error():
        with business_transaction(actor_app_user_id):
            _auftrag_sperren(order.id)

            regie = _bindungen_am_auftrag(
                order.id, source_kinds=(ZEITBUCHUNG, MATERIALBUCHUNG)
            )
            if regie:
                raise AbrechnungError(
                    f"Dieser Auftrag ist bereits über Zeit-/Materialbuchungen "
                    f"abgerechnet ({', '.join(regie)}) — das ist eine "
                    "Regieabrechnung über das gesamte Ist. Ein Nachtrag daneben "
                    "fakturierte die Mehrmenge ein zweites Mal."
                )

            kandidaten, _abgleich = _nachtrag_kandidaten(order)
            offene = [k for k in kandidaten if k["menge"] > 0]
            if not offene:
                entwuerfe = _entwurfsberichte(order.id)
                schon = [k for k in kandidaten if k["menge"] <= 0]
                raise AbrechnungError(
                    "Es gibt nichts nachzutragen: "
                    + (
                        "Alle Abweichungen sind bereits abgerechnet."
                        if schon
                        else "Der Soll-Ist-Abgleich weist keinen Mehrverbrauch und "
                             "keine Zusatzleistung aus. (Minderverbrauch und "
                             "entfallene Positionen mindern die Pauschale nicht.)"
                    )
                    + (
                        f" Achtung: {len(entwuerfe)} Bericht(e) sind noch nicht "
                        "unterzeichnet und fließen deshalb nicht ein."
                        if entwuerfe
                        else ""
                    )
                )

            regelwerk = matrix_service.lade_regelwerk()
            klaerungen = []
            einheit_konflikte = []
            lines = []
            quellen = []            # (position_number, [SiteReportLine, …])
            for k in offene:
                if k["einheit_konflikt"]:
                    # Fail-closed (Review-Befund, dritter Weg): derselbe Artikel
                    # steht schon unter einer ANDEREN Einheit in Rechnung. Nicht
                    # summierbar, nicht durchlassbar — ein Mensch entscheidet.
                    einheit_konflikte.append({
                        "identitaet": _identitaet(
                            k["source_article_id"], k["source_assembly_id"],
                            k["bezeichnung"]),
                        "bezeichnung": k["bezeichnung"],
                        "einheiten": k["konflikt_einheiten"],
                    })
                    continue
                preis, ek, klaerung = _nachtrag_klaerung(k, regelwerk)
                genannt = _genannter_preis(
                    genannte, k["schluessel"],
                    bezeichnung=k["bezeichnung"], server_preis=preis,
                )
                if genannt is not None:
                    preis, klaerung = genannt, None
                if klaerung is not None:
                    klaerungen.append(klaerung)
                    continue
                if not k["bindbar"]:
                    # Es gäbe etwas abzurechnen, aber keine freie Berichtszeile, an
                    # die sich die Bindung hängen ließe. Ohne Bindung wäre die
                    # Position **beliebig oft** fakturierbar — die Sperre hätte ein
                    # Loch, das niemand sieht. Also: kein Beleg. (Erreichbar, wenn
                    # das Soll nachträglich fällt — Angebot abgelehnt — und die
                    # Differenz wächst, alle Zeilen aber schon gebunden sind.)
                    raise AbrechnungError(
                        f"„{k['bezeichnung']}“: Es wären {k['menge']} {k['einheit']} "
                        "nachzutragen, aber alle zugehörigen Berichtspositionen sind "
                        "bereits abgerechnet "
                        f"({', '.join(k['rechnungen']) or 'in einer Rechnung'}) — "
                        "eine Rechnungsposition ohne Abrechnungsbindung wird nicht "
                        "erzeugt, sie ließe sich beliebig oft wiederholen. Wenn die "
                        "bestehende Rechnung falsch ist, ist sie zu stornieren — das "
                        "gibt die Leistungen wieder frei."
                    )
                pos = len(lines) + 1
                lines.append({
                    "line_type": k["line_type"],
                    "description": _nachtrag_bezeichnung(k),
                    "quantity": k["menge"],
                    "unit": k["einheit"],
                    "unit_price": preis,
                    "unit_cost": ek,
                    "tax_code": tax_code,
                    "source_article_id": k["source_article_id"],
                    "source_assembly_id": k["source_assembly_id"],
                })
                quellen.append((pos, k["bindbar"]))

            # Der Einheiten-Konflikt hat Vorrang: Er ist fail-closed und lässt sich
            # nicht durch einen genannten Preis auflösen (die Einheit muss erst
            # vereinheitlicht werden). Zuerst prüfen, sonst meldete der genannte-
            # Preis-Check unten spurios einen „Preis für unbekannte Quelle".
            if einheit_konflikte:
                raise EinheitUneindeutig(einheit_konflikte)

            if genannte:
                raise AbrechnungError(
                    "Für folgende Abweichungen wurde ein Preis genannt, die in "
                    f"diesem Nachtrag nicht vorkommen: {', '.join(sorted(genannte))}."
                )
            if klaerungen:
                raise PreisUnbekannt(klaerungen)

            quell_ids = [l.id for _p, ls in quellen for l in ls]
            _quellen_sperren(site_report_line_ids=quell_ids)
            # Zweitprüfung derselben Quellzeilen (UNIQUE-Index als letzte Instanz →
            # sauberer 422 statt 500). Die auftragsweite Serialisierung liegt schon
            # in `_auftrag_sperren` oben.
            _q, belegte, _t, _m = _aktive_bindungen(
                site_report_line_ids=quell_ids
            )
            if belegte:
                raise AbrechnungError(
                    "Ein Teil der Abweichungen wurde soeben von einem anderen "
                    "Vorgang abgerechnet. Bitte den Nachtrag erneut aufrufen — die "
                    "abrechenbaren Mengen haben sich geändert."
                )
            invoice = beleg_service.create_invoice(
                actor_app_user_id,
                property_id=order.property_id,
                invoice_type="RECHNUNG",
                project_id=order.project_id,
                work_order_id=order.id,
                # Vorgangsbezug vom Auftrag übernehmen (Migration 0113).
                service_case_id=order.service_case_id,
                invoice_date=invoice_date,
                due_date=due_date,
                payment_term_days=payment_term_days,
                discount_percent=discount_percent,
                discount_days=discount_days,
                show_labour_costs=show_labour_costs,
                lines=lines,
            )
            zeilen = {
                l.position_number: l
                for l in InvoiceLine.objects.filter(invoice_id=invoice.id)
            }
            # n Berichtszeilen binden an EINE Rechnungsposition (die Mehrmenge ist
            # die Summe über alle Berichte). Die Bindung bleibt trotzdem **je
            # Berichtszeile** — sonst ließe sich eine einzelne Zeile später doch
            # noch ein zweites Mal greifen.
            for pos, report_lines in quellen:
                for line in report_lines:
                    BillingLink.objects.create(
                        id=uuid.uuid4(),
                        invoice_id=invoice.id,
                        invoice_line_id=zeilen[pos].id,
                        source_kind=BERICHTSPOSITION,
                        site_report_line_id=line.id,
                    )
    invoice.refresh_from_db()
    return invoice


# ---------------------------------------------------------------------------
# Notbremse: die Bindungen eines ENTWURFS lösen
# ---------------------------------------------------------------------------

def bindungen_loesen(actor_app_user_id, *, invoice_id, reason):
    """Löst die Bindungen eines Rechnungs-**ENTWURFS** und entfernt die gebundenen
    Positionen aus ihm.

    Der Weg aus einem verunglückten Entwurf. Eine gebundene Rechnung lässt ihren
    Positionssatz nicht ersetzen (Trigger `protect_billed_invoice_lines`) — sonst
    verschwände die Doppelabrechnungssperre mit einem Klick auf „Speichern".

    **Beides gehört zusammen und passiert in EINER Transaktion:**

    1. Bindungen lösen (`released_at`, `released_reason`, `invoice_line_id := NULL`)
       → die Quellen sind wieder abrechenbar.
    2. Die gebundenen Positionen aus dem Entwurf **entfernen**
       → er stellt sie nicht länger in Rechnung.

    Ohne Schritt 2 wäre die Sperre löchrig: Der Entwurf führte die Leistung
    weiterhin, und dieselbe Stunde ließe sich parallel ein zweites Mal abrechnen.
    Mit Schritt 2 bleibt sie lückenlos — die Quelle ist frei, weil sie **nirgends
    mehr** berechnet wird.

    Nur im ENTWURF. Eine veröffentlichte Rechnung wird nicht „entbunden", sondern
    **storniert** (das löst die Bindungen per DB-Trigger und ist GoBD-konform).
    """
    grund = (reason or "").strip()
    if not grund:
        raise AbrechnungError(
            "Das Lösen der Abrechnungsbindung ist begründungspflichtig."
        )
    invoice = Invoice.objects.filter(id=invoice_id).first()
    if invoice is None:
        raise AbrechnungError("Rechnung nicht gefunden.")
    if invoice.status != "ENTWURF":
        raise AbrechnungError(
            "Die Rechnung ist veröffentlicht. Eine abgerechnete Leistung wird "
            "nicht entbunden, sondern durch ein STORNO wieder frei — das löst die "
            "Bindungen und ist GoBD-konform."
        )

    with as_business_error():
        with business_transaction(actor_app_user_id):
            aktiv = list(
                BillingLink.objects.select_for_update().filter(
                    invoice_id=invoice.id, released_at__isnull=True
                )
            )
            if not aktiv:
                raise AbrechnungError(
                    "Diese Rechnung trägt keine aktive Abrechnungsbindung."
                )
            line_ids = {l.invoice_line_id for l in aktiv if l.invoice_line_id}
            BillingLink.objects.filter(id__in=[l.id for l in aktiv]).update(
                released_at=dj_timezone.now(),
                released_reason=grund,
                # Die Position wird gleich entfernt — der Verweis darf nicht ins
                # Leere zeigen (FK). Die gelöste Bindung bleibt als Nachweis stehen.
                invoice_line_id=None,
            )
            # Jetzt trägt die Rechnung keine aktive Bindung mehr → der Trigger
            # `protect_billed_invoice_lines` lässt das DELETE durch.
            InvoiceLine.objects.filter(
                invoice_id=invoice.id, id__in=line_ids
            ).delete()
            _summen_neu(invoice)
    invoice.refresh_from_db()
    return invoice


def _summen_neu(invoice):
    """Kopfsummen aus den verbliebenen Positionen neu ableiten.

    Dieselbe Rechenstelle wie der Editor (`beleg._totals`) — die Summe je
    Steuergruppe gerundet, exakt wie der DB-CHECK sie beim Veröffentlichen prüft.
    Zwei Summenformeln im selben System wären ein Cent-Bug mit Ansage.
    """
    prepared = [
        {
            "line_type": l.line_type,
            "line_kind": l.line_kind,
            "net_amount": l.net_amount,
            "tax_code_id": l.tax_code_id,
            "tax_rate_percent": l.tax_rate_percent,
        }
        for l in InvoiceLine.objects.filter(invoice_id=invoice.id)
    ]
    net, tax, gross = beleg_service._totals(prepared)
    Invoice.objects.filter(id=invoice.id).update(
        net_total=net, tax_total=tax, gross_total=gross
    )


# ---------------------------------------------------------------------------
# Abrechnungsart am Auftrag
# ---------------------------------------------------------------------------

def _wirksame_rechnungen(work_order_id):
    """Belegnummern der veröffentlichten Rechnungen des Auftrags, die **gelten**.

    Nicht mitgezählt werden Kreditbelege selbst und jede Rechnung, zu der ein
    veröffentlichter **STORNO** existiert: Sie ist aufgehoben, ihre Bindungen sind
    gelöst, die Leistung ist wieder frei — und damit ist der Auftrag wieder offen
    für eine andere Abrechnungsart. Eine **GUTSCHRIFT** hebt die Rechnung dagegen
    NICHT auf (dieselbe Grenze wie in `beleg._korrigierte_belege` bzw. am Trigger
    `release_billing_links_on_cancel`): Die Rechnung fordert weiterhin Geld.
    """
    storniert = set(
        Invoice.objects.filter(
            invoice_type="STORNO", status="VEROEFFENTLICHT"
        ).values_list("reference_invoice_id", flat=True)
    )
    return sorted(
        (nr or "ohne Nummer")
        for pk, nr in Invoice.objects.filter(
            work_order_id=work_order_id, status="VEROEFFENTLICHT"
        )
        .exclude(invoice_type__in=("STORNO", "GUTSCHRIFT"))
        .values_list("id", "invoice_number")
        if pk not in storniert
    )


def set_billing_mode(actor_app_user_id, *, work_order_id, billing_mode):
    """Setzt `work_order.billing_mode` (PAUSCHAL | REGIE).

    Nicht mehr änderbar, sobald der Auftrag ABGERECHNET oder STORNIERT ist: Die
    Abrechnungsart ist dann Geschichte, und sie umzuschreiben änderte rückwirkend
    die Aussage darüber, wie abgerechnet wurde.

    **Und — der eigentliche Schutz (Review-Befund H-2):** Der Statusvergleich oben
    ist wirkungslos, denn **nichts im System setzt einen Auftrag je auf
    ABGERECHNET** (auch `beleg.publish_invoice` nicht; der Auftrag bleibt
    KAUFMAENNISCH_GEPRUEFT). Der Wechsel wird deshalb an dem festgemacht, was
    tatsächlich passiert ist:

    * Der Auftrag trägt **aktive Abrechnungsbindungen** (egal ob Entwurf oder
      veröffentlichte Rechnung), **oder**
    * es existiert eine **wirksame veröffentlichte Rechnung** zum Auftrag
      (`_wirksame_rechnungen` — eine stornierte zählt nicht).

    Warum das zwingend ist: PAUSCHAL → Rechnung aus Angebot → veröffentlichen →
    Moduswechsel auf REGIE → Rechnung aus Bericht/Zeiten → veröffentlichen. Beide
    Rechnungen sind für sich sauber gebunden, die Quellen sind **disjunkt** — die
    drei UNIQUE-Indizes auf `billing_link` können die Doppelabrechnung per
    Konstruktion nicht sehen. Der Moduswechsel ist das Tor, an dem sie entsteht.
    Der Weg zum Ziel heißt: **erst stornieren, dann umstellen** — der Storno löst
    die Bindungen und gibt die Leistungen frei.

    Die Sperre ist **doppelt gelegt**: `rechnung_aus_angebot` und
    `rechnung_aus_auftrag` prüfen zusätzlich und modus-unabhängig, ob der Auftrag
    schon über die jeweils andere Quelle abgerechnet ist
    (`_angebot_bereits_abgerechnet_pruefen` und die Quersperre im Angebotsweg).
    Dort zählt es wirklich — hier wird der Fehler nur früh und verständlich
    abgefangen.
    """
    if billing_mode not in BILLING_MODES:
        raise AbrechnungError(
            f"Ungültige Abrechnungsart '{billing_mode}'. "
            f"Erlaubt: {', '.join(BILLING_MODES)}."
        )
    order = WorkOrder.objects.filter(id=work_order_id).first()
    if order is None:
        raise AbrechnungError("Auftrag nicht gefunden.")
    if order.status in ("ABGERECHNET", "STORNIERT"):
        raise AbrechnungError(
            f"Der Auftrag ist {order.status} — die Abrechnungsart lässt sich nicht "
            "mehr ändern."
        )
    if order.billing_mode == billing_mode:
        return order
    gebunden = BillingLink.objects.filter(
        invoice__work_order_id=order.id, released_at__isnull=True
    ).exists()
    rechnungen = _wirksame_rechnungen(order.id)
    if gebunden or rechnungen:
        woran = (
            f"Es bestehen bereits Rechnungen zu diesem Auftrag "
            f"({', '.join(rechnungen)})."
            if rechnungen
            else "Zu diesem Auftrag sind Leistungen bereits in einem "
            "Rechnungsentwurf gebunden."
        )
        weg = (
            "Stornieren Sie die Rechnung(en) — das löst die Bindungen — und stellen "
            "Sie die Abrechnungsart danach um."
            if rechnungen
            else "Lösen Sie die Bindungen des Entwurfs (oder verwerfen Sie ihn) und "
            "stellen Sie die Abrechnungsart danach um."
        )
        raise AbrechnungError(
            f"Die Abrechnungsart lässt sich nicht mehr ändern: {woran} Die "
            "Abrechnungsart entscheidet, WORAUS die Rechnung entsteht (Angebot "
            "oder Bericht/Zeiten). Wird sie nach der Abrechnung umgestellt, ließe "
            f"sich dieselbe Leistung über die jeweils andere Quelle ein zweites Mal "
            f"fakturieren. {weg}"
        )
    with as_business_error():
        with business_transaction(actor_app_user_id):
            order.billing_mode = billing_mode
            order.save(update_fields=["billing_mode", "updated_at"])
    order.refresh_from_db()
    return order
