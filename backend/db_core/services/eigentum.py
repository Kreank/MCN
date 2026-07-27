"""Eigentum an einer Einheit — `tenure.ownership_period` + `ownership_interest`.

## Wofür es das gibt

Sascha in seiner Domänenmodell-Skizze (`docs/domaenenmodell-skizze.md`):

> Eine Liegenschaft kann dadurch, dass sie mehrere Eigentümer beherbergen kann,
> auch mehrere Rechnungsadressen besitzen. […] Es kann also sein, dass ich 20
> Rechnungsadressen habe, die ich immer angeben muss, wenn ich Dokumente dazu
> erzeuge.

Genau das konnte das System nicht: Die Tabellen liegen seit Migration 0005 in
der Datenbank — mit Bruchanteilen, exakter Vollständigkeitsprüfung,
Quellenpflicht und Bestätigung — und wurden von **null** Backend-Zeilen benutzt.
Der Reiter „Eigentum" an der Liegenschaft zeigte einen Platzhalter: „sobald die
Lesepfade angebunden sind". Dieses Modul ist der Lesepfad (Arbeitspaket AP5).

## Der Anteil ist ein Bruch, keine Prozentzahl

Drei Erben zu je 1/3 sind dezimal nicht darstellbar. „33,33 % dreimal" ergibt
99,99 %, und ein vollständiger Eigentumsstand wäre nie erreichbar. Die Datenbank
rechnet deshalb über das kleinste gemeinsame Vielfache der Nenner und vergleicht
exakt (OPUS-01); dieser Service prüft mit `Fraction` genauso exakt vor.

## Die drei Vollständigkeitsgrade

`distribution_status` ist kein Schmuck, sondern entscheidet über die Strenge:

* **UNRESOLVED** — man weiß noch nichts. Beteiligte dürfen fehlen.
* **PARTIAL** — man kennt einen von vier Eigentümern. Anteile dürfen lückenhaft
  sein und müssen sich nicht auf 100 % summieren.
* **COMPLETE** — die Aussage „das sind alle, und so gehört es ihnen". Erst hier
  prüft die DB: mindestens eine Beteiligung, keine ohne Anteil, keine
  unbestätigte, SOLE nur allein, Summe **exakt** 100 %.

Der Alltag beginnt bei PARTIAL. Das ist die wichtigste Eigenschaft dieses
Modells: Es zwingt niemanden, etwas zu behaupten, was er nicht weiß.

## Die Prüfung schlägt erst beim COMMIT zu

`trg_ownership_interest_totals` ist ein **DEFERRABLE INITIALLY DEFERRED**
Constraint-Trigger. Er läuft nicht beim INSERT, sondern am Ende der Transaktion —
richtig so, denn sonst wäre ein Stand mit drei Beteiligten nie anlegbar (nach der
ersten Zeile stünde die Summe bei 1/3).

Für den Service heißt das: Ein Anteilsfehler kommt **nicht** an der Stelle
zurück, an der er entstanden ist, sondern beim Verlassen von
`business_transaction` — mit einer Meldung, die keinem Eingabefeld zuzuordnen
ist. Deshalb prüft dieses Modul die Summe **vorher** in Python und wirft einen
lesbaren Fachfehler. Die DB bleibt die Sperre (auch gegen zwei gleichzeitige
Sachbearbeiter), der Service macht sie bedienbar.

## Es gibt kein Löschen — Korrekturen laufen vorwärts

`0009` verbietet DELETE auf beiden Tabellen (F-02). Anders als beim Mieter gibt
es an der Beteiligung auch **kein** `valid_until`: Der Zeitraum hängt am Stand,
nicht am einzelnen Eigentümer. Ein Eigentümerwechsel ist deshalb immer

    alten Stand beenden  →  neuen Stand anlegen

und nicht „Beteiligten austauschen". Wer sich vertippt hat, beendet den Stand
und legt ihn richtig neu an; die falsche Aussage bleibt in der Historie sichtbar.
Das ist gewollt: Wer wem was verkauft hat, ist eine Kette, kein Zustand.
"""
import uuid
from datetime import date
from fractions import Fraction

from django.db.models import Prefetch, Q

from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import OwnershipInterest, OwnershipPeriod, Unit
from db_core.services._validation import ensure_exists, ensure_party_usable

#: Codeliste `tenure.ownership_period.distribution_status` (CHECK aus 0005).
DISTRIBUTION_STATUS = ("COMPLETE", "PARTIAL", "UNRESOLVED")

#: Codeliste `tenure.ownership_period.source_type` (CHECK aus 0005, A-14).
SOURCE_TYPES = (
    "MANAGEMENT_NOTICE",
    "OWNER_LIST",
    "ORDER_STATEMENT",
    "IMPORT",
    "MANUAL",
)

#: Codeliste `tenure.ownership_interest.ownership_type`.
OWNERSHIP_TYPES = ("SOLE", "CO_OWNER")

#: Codeliste `tenure.ownership_interest.confirmation_status`.
CONFIRMATION_STATUS = ("CONFIRMED", "UNCONFIRMED")

#: Einheitstypen ohne Eigentumsstand (A-08). Das Eigentum an
#: Gemeinschaftsflächen folgt der Gemeinschaft.
OHNE_EIGENTUM = ("COMMON_AREA", "TECHNICAL_ROOM")

#: Obergrenze des Nenners (CHECK aus 0005) — Überlaufschutz der LCM-Rechnung.
MAX_NENNER = 1_000_000


class EigentumError(ValueError):
    """Der Eigentums-Vorgang ist fachlich unzulässig (→ 422)."""


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------

def _mit_beteiligten(qs):
    return qs.select_related("unit__building").prefetch_related(
        Prefetch(
            "interests",
            queryset=OwnershipInterest.objects.select_related("owner_party").order_by(
                "-share_numerator", "owner_party__display_name"
            ),
        )
    )


def staende_der_liegenschaft(property_id, *, stichtag=None, historie=False):
    """Alle Eigentumsstände einer Liegenschaft, je Einheit.

    Ohne `historie` nur die am Stichtag geltenden — das ist die Frage, die im
    Alltag gestellt wird („wem gehört das jetzt?"). Mit `historie` die ganze
    Kette, denn wer wem wann verkauft hat, ist der eigentliche Nachweis.
    """
    qs = OwnershipPeriod.objects.filter(unit__property_id=property_id)
    if not historie:
        tag = stichtag or date.today()
        qs = qs.filter(
            Q(valid_from__lte=tag)
            & (Q(valid_until__isnull=True) | Q(valid_until__gt=tag))
        )
    return _mit_beteiligten(qs).order_by(
        "unit__building__building_number", "unit__unit_number", "-valid_from"
    )


def staende_der_einheit(unit_id, *, historie=True):
    """Die Eigentumskette einer Einheit, jüngster Stand zuerst."""
    qs = OwnershipPeriod.objects.filter(unit_id=unit_id)
    if not historie:
        heute = date.today()
        qs = qs.filter(
            Q(valid_from__lte=heute)
            & (Q(valid_until__isnull=True) | Q(valid_until__gt=heute))
        )
    return _mit_beteiligten(qs)


def get_stand(period_id):
    stand = _mit_beteiligten(OwnershipPeriod.objects.filter(id=period_id)).first()
    if stand is None:
        raise EigentumError("Eigentumsstand nicht gefunden.")
    return stand


def eigentuemer_der_liegenschaft(property_id, *, stichtag=None):
    """Die Eigentümer einer Liegenschaft als Parteien, dublettenfrei.

    Der Weg zu Saschas „20 Rechnungsadressen": Wer als Eigentümer einer Einheit
    geführt wird, kommt als Rechnungsempfänger in Frage. Zurückgegeben werden
    Parteien, keine Anteile — für die Auswahl eines Empfängers ist es
    gleichgültig, ob jemandem die Hälfte oder ein Achtel gehört.

    Bewusst über ALLE Vollständigkeitsgrade: Auch ein bloß vermuteter Eigentümer
    in einem PARTIAL-Stand ist jemand, an den man eine Rechnung schreiben kann.
    """
    tag = stichtag or date.today()
    beteiligungen = (
        OwnershipInterest.objects.filter(
            ownership_period__unit__property_id=property_id,
            ownership_period__valid_from__lte=tag,
        )
        .filter(
            Q(ownership_period__valid_until__isnull=True)
            | Q(ownership_period__valid_until__gt=tag)
        )
        .select_related("owner_party")
    )
    gesehen = {}
    for b in beteiligungen:
        gesehen.setdefault(b.owner_party_id, b.owner_party)
    return sorted(gesehen.values(), key=lambda p: (p.display_name or "").lower())


# ---------------------------------------------------------------------------
# Prüfungen
# ---------------------------------------------------------------------------

def _pruefe_anteil(numerator, denominator):
    """Bruchanteil prüfen. Beide leer heißt „Anteil unbekannt" und ist erlaubt."""
    if numerator is None and denominator is None:
        return None, None
    if (numerator is None) != (denominator is None):
        raise EigentumError(
            "Ein Anteil braucht Zähler UND Nenner — oder gar keine Angabe, "
            "wenn die Höhe unbekannt ist."
        )
    try:
        z = int(numerator)
        n = int(denominator)
    except (TypeError, ValueError):
        raise EigentumError("Zähler und Nenner müssen ganze Zahlen sein.")
    if z <= 0 or n <= 0:
        raise EigentumError("Zähler und Nenner müssen größer als null sein.")
    if z > n:
        raise EigentumError(
            f"Ein Anteil kann nicht größer als das Ganze sein ({z}/{n})."
        )
    if n > MAX_NENNER:
        raise EigentumError(
            f"Der Nenner ist auf {MAX_NENNER:,} begrenzt (Rechengrenze der "
            "Anteilsprüfung).".replace(",", ".")
        )
    return z, n


def _pruefe_vollstaendigkeit(status, beteiligungen):
    """Die Regeln eines COMPLETE-Standes — vor dem COMMIT, in lesbaren Worten.

    Dieselben Prüfungen laufen als DEFERRED Constraint-Trigger in der Datenbank
    und sind dort die eigentliche Sperre. Hier entstehen nur die Meldungen, mit
    denen jemand etwas anfangen kann: Der Trigger meldet
    „Anteilssumme 5/6 ist nicht exakt 100 Prozent" beim Verlassen der
    Transaktion, ohne Bezug zu einem Eingabefeld.

    `beteiligungen` ist eine Liste von Dicts mit `share_numerator`,
    `share_denominator`, `ownership_type`, `confirmation_status`.
    """
    if status != "COMPLETE":
        return

    if not beteiligungen:
        raise EigentumError(
            "Ein vollständiger Eigentumsstand braucht mindestens einen "
            "Eigentümer. Solange niemand bekannt ist, passt „ungeklärt“."
        )
    ohne_anteil = [b for b in beteiligungen if b.get("share_numerator") is None]
    if ohne_anteil:
        raise EigentumError(
            f"{len(ohne_anteil)} Eigentümer ohne Anteil. In einem vollständigen "
            "Stand muss jeder Anteil beziffert sein — sonst passt „teilweise "
            "geklärt“."
        )
    unbestaetigt = [
        b for b in beteiligungen if b.get("confirmation_status") != "CONFIRMED"
    ]
    if unbestaetigt:
        raise EigentumError(
            f"{len(unbestaetigt)} Eigentümer sind noch nicht bestätigt. Ein "
            "vollständiger Stand duldet nur belegte Beteiligungen."
        )
    sole = [b for b in beteiligungen if b.get("ownership_type") == "SOLE"]
    if sole and len(beteiligungen) != 1:
        raise EigentumError(
            "Alleineigentum verträgt keinen zweiten Eigentümer. Entweder "
            "„Alleineigentum“ für eine Person oder „Miteigentum“ für alle."
        )

    # Exakt wie die Datenbank: Brüche, keine Dezimalzahlen.
    summe = sum(
        (Fraction(b["share_numerator"], b["share_denominator"]) for b in beteiligungen),
        Fraction(0),
    )
    if summe != 1:
        raise EigentumError(
            f"Die Anteile ergeben {summe.numerator}/{summe.denominator}, "
            "nicht genau 1. Ein vollständiger Stand muss exakt aufgehen "
            "(z. B. 1/3 + 1/3 + 1/3)."
        )


def _pruefe_einheit(unit_id):
    unit = Unit.objects.filter(id=unit_id).first()
    if unit is None:
        raise EigentumError("Einheit nicht gefunden.")
    if unit.unit_type in OHNE_EIGENTUM:
        raise EigentumError(
            "Gemeinschafts- und Technikflächen tragen keinen eigenen "
            "Eigentumsstand — dort folgt das Eigentum der Gemeinschaft."
        )
    return unit


def _pruefe_zeitraum(valid_from, valid_until):
    if valid_from is None:
        raise EigentumError("Der Beginn des Eigentumsstands ist erforderlich.")
    if valid_until is not None and valid_until <= valid_from:
        raise EigentumError("Das Ende muss nach dem Beginn liegen.")


def _pruefe_ueberlappung(unit_id, valid_from, valid_until, ausser=None):
    """Zwei Eigentumsstände derselben Einheit dürfen sich nicht überlappen.

    Die Sperre ist der EXCLUDE-Constraint; hier entsteht die Meldung, die sagt,
    WELCHER Stand im Weg ist.
    """
    qs = OwnershipPeriod.objects.filter(unit_id=unit_id)
    if ausser is not None:
        qs = qs.exclude(id=ausser)
    ende = valid_until
    for anderer in qs:
        a_ende = anderer.valid_until
        # Halboffene Intervalle [von, bis): Überlappung, wenn beide Ränder
        # ineinander ragen. Offenes Ende = unendlich.
        if (ende is None or anderer.valid_from < ende) and (
            a_ende is None or valid_from < a_ende
        ):
            bis = a_ende.isoformat() if a_ende else "offen"
            raise EigentumError(
                f"Für diesen Zeitraum gibt es bereits einen Eigentumsstand "
                f"({anderer.valid_from.isoformat()} bis {bis}). Beenden Sie "
                "ihn zuerst — Eigentum ist eine Kette, keine Parallelwelt."
            )


# ---------------------------------------------------------------------------
# Schreiben
# ---------------------------------------------------------------------------

def create_stand(
    actor_app_user_id,
    *,
    unit_id,
    valid_from,
    source_type,
    source_reference,
    distribution_status="UNRESOLVED",
    valid_until=None,
    eigentuemer=None,
):
    """Legt einen Eigentumsstand samt Beteiligten in EINER Transaktion an.

    Atomar, nicht in zwei Schritten: Ein COMPLETE-Stand ohne Beteiligte ist
    unzulässig (die DB lehnt ihn beim COMMIT ab), ließe sich also gar nicht
    erst anlegen, um danach befüllt zu werden.

    `eigentuemer` ist eine Liste von Dicts mit `party_id` und optional
    `share_numerator`/`share_denominator`, `ownership_type`,
    `confirmation_status`.
    """
    if distribution_status not in DISTRIBUTION_STATUS:
        raise EigentumError(f"Unbekannter Vollständigkeitsgrad: {distribution_status}")
    if source_type not in SOURCE_TYPES:
        raise EigentumError(f"Unbekannte Quellenart: {source_type}")
    referenz = (source_reference or "").strip()
    if not referenz:
        raise EigentumError(
            "Die Quelle ist Pflicht: Woher stammt die Angabe, wem das gehört? "
            "(z. B. „Eigentümerliste der Verwaltung vom 12.03.2026“)"
        )
    _pruefe_einheit(unit_id)
    _pruefe_zeitraum(valid_from, valid_until)
    _pruefe_ueberlappung(unit_id, valid_from, valid_until)

    zeilen = _beteiligtenzeilen(eigentuemer or [])
    _pruefe_vollstaendigkeit(distribution_status, zeilen)

    with as_business_error():
        with business_transaction(actor_app_user_id):
            stand = OwnershipPeriod.objects.create(
                id=uuid.uuid4(),
                unit_id=unit_id,
                distribution_status=distribution_status,
                valid_from=valid_from,
                valid_until=valid_until,
                source_type=source_type,
                source_reference=referenz,
            )
            for z in zeilen:
                OwnershipInterest.objects.create(
                    id=uuid.uuid4(), ownership_period_id=stand.id, **z
                )
    return get_stand(stand.id)


def _beteiligtenzeilen(eigentuemer):
    """Eingabeliste prüfen und in Modellfelder übersetzen."""
    zeilen = []
    gesehen = set()
    for eintrag in eigentuemer:
        party_id = eintrag.get("party_id")
        if not party_id:
            raise EigentumError("Zu jeder Beteiligung gehört ein Kontakt.")
        if party_id in gesehen:
            raise EigentumError(
                "Derselbe Kontakt steht zweimal in der Liste. Ein Eigentümer "
                "hat je Stand genau eine Beteiligung — mit einem Anteil."
            )
        gesehen.add(party_id)
        ensure_party_usable(party_id)

        typ = eintrag.get("ownership_type") or "CO_OWNER"
        if typ not in OWNERSHIP_TYPES:
            raise EigentumError(f"Unbekannte Eigentumsart: {typ}")
        status = eintrag.get("confirmation_status") or "UNCONFIRMED"
        if status not in CONFIRMATION_STATUS:
            raise EigentumError(f"Unbekannter Bestätigungsstand: {status}")

        z, n = _pruefe_anteil(
            eintrag.get("share_numerator"), eintrag.get("share_denominator")
        )
        zeilen.append(
            {
                "owner_party_id": party_id,
                "share_numerator": z,
                "share_denominator": n,
                "ownership_type": typ,
                "confirmation_status": status,
            }
        )
    return zeilen


def update_stand(actor_app_user_id, period_id, felder):
    """Ändert Kopfdaten eines Standes (Quelle, Vollständigkeitsgrad, Ende).

    Die Einheit bleibt unveränderlich — ein Stand gehört zu genau einer Wohnung.
    Wäre er umhängbar, ließe sich die Geschichte einer Einheit an eine andere
    schreiben.
    """
    stand = get_stand(period_id)
    daten = dict(felder)

    if "unit_id" in daten:
        raise EigentumError(
            "Der Eigentumsstand lässt sich nicht auf eine andere Einheit "
            "umhängen. Beenden Sie ihn und legen Sie ihn bei der richtigen an."
        )

    # Unbekannte Feldnamen ZUERST: Ein Tippfehler soll als solcher gemeldet
    # werden und nicht als Zeitraum- oder Anteilsfehler, der ihn nur verdeckt.
    erlaubt = {
        "distribution_status",
        "valid_from",
        "valid_until",
        "source_type",
        "source_reference",
    }
    unbekannt = set(daten) - erlaubt
    if unbekannt:
        raise EigentumError(f"Unbekannte Felder: {', '.join(sorted(unbekannt))}")

    status = daten.get("distribution_status", stand.distribution_status)
    if status not in DISTRIBUTION_STATUS:
        raise EigentumError(f"Unbekannter Vollständigkeitsgrad: {status}")
    if "source_type" in daten and daten["source_type"] not in SOURCE_TYPES:
        raise EigentumError(f"Unbekannte Quellenart: {daten['source_type']}")
    if "source_reference" in daten:
        referenz = (daten["source_reference"] or "").strip()
        if not referenz:
            raise EigentumError("Die Quellenangabe darf nicht leer sein.")
        daten["source_reference"] = referenz

    von = daten.get("valid_from", stand.valid_from)
    bis = daten.get("valid_until", stand.valid_until)
    _pruefe_zeitraum(von, bis)
    _pruefe_ueberlappung(stand.unit_id, von, bis, ausser=stand.id)

    # Der Vollständigkeitsgrad wird an den VORHANDENEN Beteiligten geprüft:
    # Auf COMPLETE hochzustufen ist die Aussage „das sind alle" — und die muss
    # sich jetzt an ihnen messen lassen.
    if status == "COMPLETE":
        _pruefe_vollstaendigkeit(
            status,
            [
                {
                    "share_numerator": i.share_numerator,
                    "share_denominator": i.share_denominator,
                    "ownership_type": i.ownership_type,
                    "confirmation_status": i.confirmation_status,
                }
                for i in stand.interests.all()
            ],
        )

    with as_business_error():
        with business_transaction(actor_app_user_id):
            for feld, wert in daten.items():
                setattr(stand, feld, wert)
            stand.save(update_fields=[*daten.keys(), "updated_at"])
    return get_stand(period_id)


def beenden(actor_app_user_id, period_id, *, valid_until):
    """Beendet einen Eigentumsstand — der Weg für den Eigentümerwechsel.

    Danach legt man den neuen Stand mit `valid_from = valid_until` an; die DB
    lässt beide nebeneinander stehen (halboffene Intervalle).
    """
    if valid_until is None:
        raise EigentumError("Zum Beenden gehört ein Datum.")
    return update_stand(actor_app_user_id, period_id, {"valid_until": valid_until})


def add_eigentuemer(
    actor_app_user_id,
    *,
    period_id,
    party_id,
    share_numerator=None,
    share_denominator=None,
    ownership_type="CO_OWNER",
    confirmation_status="UNCONFIRMED",
):
    """Fügt einem bestehenden Stand einen Eigentümer hinzu.

    Bei einem COMPLETE-Stand wird die Summe **mit** der neuen Beteiligung
    geprüft — sonst liefe der Aufrufer in den DEFERRED-Trigger und bekäme den
    Fehler erst beim Speichern, ohne Bezug zur Eingabe.
    """
    stand = get_stand(period_id)
    neu = _beteiligtenzeilen(
        [
            {
                "party_id": party_id,
                "share_numerator": share_numerator,
                "share_denominator": share_denominator,
                "ownership_type": ownership_type,
                "confirmation_status": confirmation_status,
            }
        ]
    )[0]

    vorhanden = list(stand.interests.all())
    if any(i.owner_party_id == party_id for i in vorhanden):
        raise EigentumError(
            "Dieser Kontakt ist an dem Stand bereits beteiligt. Ändern Sie "
            "seinen Anteil, statt ihn ein zweites Mal einzutragen."
        )
    _pruefe_vollstaendigkeit(
        stand.distribution_status,
        [
            {
                "share_numerator": i.share_numerator,
                "share_denominator": i.share_denominator,
                "ownership_type": i.ownership_type,
                "confirmation_status": i.confirmation_status,
            }
            for i in vorhanden
        ]
        + [neu],
    )

    with as_business_error():
        with business_transaction(actor_app_user_id):
            OwnershipInterest.objects.create(
                id=uuid.uuid4(), ownership_period_id=stand.id, **neu
            )
    return get_stand(period_id)


#: Quellenart der Eigentümer, die aus der Belegungserfassung stammen.
#: „Manuell erfasst" ist hier die Wahrheit — jemand hat es beim Aufnehmen der
#: Belegung gesagt, es liegt keine Eigentümerliste vor.
UEBERNAHME_SOURCE_TYPE = "MANUAL"
UEBERNAHME_REFERENZ = "Aus der Belegungserfassung übernommen"


def plane_uebernahme_aus_belegung(
    *, unit_id, party_id, ab, quelle_zusatz=None
):
    """Bereitet die Übernahme eines Eigentümers **aus der Belegung** vor.

    Saschas Befund beim Testen: *„Bei Belegung kann ich ja auch Eigentümer als
    bewohnt angeben — das sollte beim Reiter Eigentum übernommen werden, wollen
    ja keine doppelte Arbeit."* Genau das macht diese Funktion: Wer beim
    Erfassen der Belegung als Eigentümer benannt wird, steht danach im Reiter
    „Eigentum", ohne dort ein zweites Mal eingetragen zu werden.

    **Geplant, nicht geschrieben.** Alle Prüfungen laufen hier, das Schreiben
    erledigt `wende_uebernahme_an()` **innerhalb der Transaktion der Belegung**.
    Der Grund ist nicht Eleganz: Belegung und Eigentumsstand müssen zusammen
    entstehen oder zusammen scheitern. Ein halb übernommener Eigentümer wäre
    eine Aussage, die niemand getroffen hat.

    Rückgabe: ein Plan-Dict für `wende_uebernahme_an()` — oder ``None``, wenn
    nichts zu tun ist (kein Kontakt übergeben, oder er steht schon drin).

    Die Aussage bleibt bewusst **schwach**: `PARTIAL` („einer ist bekannt, die
    Aufteilung nicht"), ohne Anteil, **unbestätigt**. Eine Nebenbei-Angabe aus
    der Belegungsaufnahme ist kein Grundbuchauszug, und das Modell soll niemanden
    zwingen, etwas zu behaupten, was er nicht weiß.
    """
    if party_id is None:
        return None

    unit = _pruefe_einheit(unit_id)
    ensure_party_usable(party_id, "Eigentümer")

    staende = list(
        OwnershipPeriod.objects.filter(unit_id=unit_id).prefetch_related("interests")
    )
    stand = next(
        (
            s
            for s in staende
            if s.valid_from <= ab and (s.valid_until is None or s.valid_until > ab)
        ),
        None,
    )

    zeile = _beteiligtenzeilen([{"party_id": party_id}])[0]

    if stand is None:
        # Noch kein Stand an diesem Tag: einen offenen anlegen. Ein später
        # beginnender Stand würde mit dem offenen Ende kollidieren — dann meldet
        # die Überlappungsprüfung, WELCHER Stand im Weg ist.
        _pruefe_ueberlappung(unit_id, ab, None)
        referenz = UEBERNAHME_REFERENZ
        if quelle_zusatz:
            referenz = f"{referenz} (Mietvertrag {quelle_zusatz})"
        return {
            "stand": {
                "unit_id": unit_id,
                "distribution_status": "PARTIAL",
                "valid_from": ab,
                "valid_until": None,
                "source_type": UEBERNAHME_SOURCE_TYPE,
                "source_reference": referenz,
            },
            "interest": zeile,
        }

    if any(i.owner_party_id == party_id for i in stand.interests.all()):
        return None  # Steht schon drin — die Übernahme ist wiederholbar.

    if stand.distribution_status == "COMPLETE":
        raise EigentumError(
            f"Für {unit.unit_number} ist das Eigentum bereits als „vollständig "
            "geklärt“ erfasst — dort ist kein Platz für einen weiteren "
            "Eigentümer nebenbei. Bitte im Reiter „Eigentum“ klären: den "
            "bisherigen Stand beenden und den neuen anlegen."
        )

    return {"period_id": stand.id, "interest": zeile}


def wende_uebernahme_an(plan):
    """Schreibt den Plan aus `plane_uebernahme_aus_belegung()`.

    **Nur innerhalb einer bereits offenen `business_transaction` aufrufen** —
    der Aufrufer (die Belegung) hält sie, damit Belegung und Eigentum zusammen
    gültig werden. Diese Funktion prüft nichts mehr; das ist im Plan passiert.
    """
    if not plan:
        return None
    period_id = plan.get("period_id")
    if period_id is None:
        stand = OwnershipPeriod.objects.create(id=uuid.uuid4(), **plan["stand"])
        period_id = stand.id
    OwnershipInterest.objects.create(
        id=uuid.uuid4(), ownership_period_id=period_id, **plan["interest"]
    )
    return period_id


def update_eigentuemer(actor_app_user_id, interest_id, felder):
    """Ändert Anteil, Art oder Bestätigung einer Beteiligung.

    Der Kontakt selbst ist unveränderlich: Ein anderer Eigentümer ist eine
    andere Aussage, kein korrigiertes Feld. Dafür gibt es den neuen Stand.
    """
    beteiligung = (
        OwnershipInterest.objects.select_related("ownership_period")
        .filter(id=interest_id)
        .first()
    )
    if beteiligung is None:
        raise EigentumError("Beteiligung nicht gefunden.")

    daten = dict(felder)
    if "owner_party_id" in daten or "party_id" in daten:
        raise EigentumError(
            "Der Eigentümer einer Beteiligung lässt sich nicht austauschen. "
            "Beenden Sie den Stand und legen Sie ihn richtig neu an."
        )
    if "ownership_type" in daten and daten["ownership_type"] not in OWNERSHIP_TYPES:
        raise EigentumError(f"Unbekannte Eigentumsart: {daten['ownership_type']}")
    if (
        "confirmation_status" in daten
        and daten["confirmation_status"] not in CONFIRMATION_STATUS
    ):
        raise EigentumError(
            f"Unbekannter Bestätigungsstand: {daten['confirmation_status']}"
        )

    if "share_numerator" in daten or "share_denominator" in daten:
        z, n = _pruefe_anteil(
            daten.get("share_numerator", beteiligung.share_numerator),
            daten.get("share_denominator", beteiligung.share_denominator),
        )
        daten["share_numerator"] = z
        daten["share_denominator"] = n

    erlaubt = {
        "share_numerator",
        "share_denominator",
        "ownership_type",
        "confirmation_status",
    }
    unbekannt = set(daten) - erlaubt
    if unbekannt:
        raise EigentumError(f"Unbekannte Felder: {', '.join(sorted(unbekannt))}")

    stand = beteiligung.ownership_period
    nachher = []
    for i in stand.interests.all():
        zeile = {
            "share_numerator": i.share_numerator,
            "share_denominator": i.share_denominator,
            "ownership_type": i.ownership_type,
            "confirmation_status": i.confirmation_status,
        }
        if i.id == beteiligung.id:
            zeile.update({k: v for k, v in daten.items() if k in zeile})
        nachher.append(zeile)
    _pruefe_vollstaendigkeit(stand.distribution_status, nachher)

    with as_business_error():
        with business_transaction(actor_app_user_id):
            for feld, wert in daten.items():
                setattr(beteiligung, feld, wert)
            beteiligung.save(update_fields=[*daten.keys(), "updated_at"])
    return get_stand(stand.id)


def bestaetigen(actor_app_user_id, period_id):
    """Setzt Bestätigungszeitpunkt und -person am Stand.

    Die Bestätigung ist die Aussage „ich habe das geprüft" — deshalb hält die
    DB Zeitpunkt und Person zusammen (CHECK: beide oder keins).
    """
    stand = get_stand(period_id)
    if stand.confirmed_at is not None:
        raise EigentumError("Dieser Eigentumsstand ist bereits bestätigt.")

    from django.utils import timezone

    with as_business_error():
        with business_transaction(actor_app_user_id):
            stand.confirmed_at = timezone.now()
            stand.confirmed_by_user_id = actor_app_user_id
            stand.save(
                update_fields=["confirmed_at", "confirmed_by_user_id", "updated_at"]
            )
    return get_stand(period_id)
