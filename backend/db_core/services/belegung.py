"""Belegung einer Einheit — `tenure.occupancy` + `tenure.occupancy_party`.

## Wofür es das gibt

Der Monteur fährt zur Badenschen Straße 53 und muss in die Wohnung **EG rechts**.
Er braucht **Name und Telefonnummer von Robco**, um einen Termin zu machen und
hineinzukommen. Bis zu diesem Slice ließ sich das nirgends eintragen: Die
Tabellen lagen seit Migration 0005 in der Datenbank und wurden von **null**
Backend-Zeilen benutzt.

## Die Modellierung (verifiziert, nicht angenommen)

**Der Mieter steht in `tenure.occupancy_party`, nicht als Spalte an der
Belegung.** Diese Tabelle gibt es seit 0005 (A-03/A-19), mit Rollen, eigenem
Gültigkeitszeitraum, EXCLUDE gegen Doppelerfassung und deferred
Containment-Trigger. Eine zusätzliche `occupancy.party_id` wäre eine **zweite
Heimat für denselben Fakt** — und könnte weder ein Ehepaar (zwei Vertragsmieter)
noch einen Mitbewohner ohne Vertrag noch einen Mieterwechsel innerhalb des
Belegungszeitraums abbilden. Ausführlich: Modulkopf von Migration 0103.

**Der Mietername gehört NICHT in `contract_reference`.** Das Feld ist eine
Vertragsreferenz (A-17). Ein Name darin wäre ein Kontakt, den niemand anrufen
kann — genau der Fehler, an dem die Vorführung scheitern würde.

## Was die DATENBANK durchsetzt (und dieser Service nur vorprüft)

| Regel | Ort |
|---|---|
| Belegungszeiträume einer Einheit überlappen nie (A-18) | `excl_occupancy` (EXCLUDE) |
| COMMON_AREA/TECHNICAL_ROOM tragen keine Belegung (F-12) | Trigger `forbid_common_area_occupancy` |
| Beteiligtenzeitraum ⊆ Belegungszeitraum | deferred Trigger `check_occupancy_party_range` |
| Dieselbe Person, dieselbe Rolle, überlappend | `excl_occupancy_party_dup` (EXCLUDE) |
| Keine MERGED-Party | Trigger (0009) |
| **Kein Löschen** | Trigger (0009) |

Die Vorprüfungen hier erzeugen die **lesbare Meldung** (422). Sie sind **nicht die
Sperre** — die Sperre steht in der DB und hält auch gegen zwei gleichzeitige
Sachbearbeiter. Deshalb läuft jeder Schreibpfad zusätzlich durch
`gate_errors.as_business_error`: Der EXCLUDE-Verstoß aus der Rennbedingung wird
zum 422, nie zum 500. *Was im Service sitzt, ist umgehbar; erst was im Trigger
sitzt, hält.*

## Es gibt kein Löschen

Eine Belegung wird **beendet** (`valid_until`), ein Mieter **zieht aus**
(`valid_until` an seiner Zeile). Die Historie ist der Punkt: Der
Baustellenbericht von damals zeigt auf die Wohnung, in der damals Musili wohnte.
"""
import uuid
from datetime import date

from django.db.models import Prefetch, Q

from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import Occupancy, OccupancyParty, Unit
from db_core.services import eigentum as eigentum_service
from db_core.services._validation import ensure_party_usable

#: Codeliste `tenure.occupancy.occupancy_type` (CHECK aus 0005).
OCCUPANCY_TYPES = (
    "RENTED",
    "OWNER_OCCUPIED",
    "VACANT",
    "COMMERCIAL_USE",
    "OTHER",
    "UNKNOWN",
)

#: Codeliste `tenure.occupancy_party.role` (CHECK aus 0005).
OCCUPANCY_ROLES = (
    "CONTRACTUAL_TENANT",
    "CO_TENANT",
    "OCCUPANT",
    "OWNER_OCCUPANT",
    "COMMERCIAL_USER",
)

#: Einheitentypen, die **keine** Belegung tragen (Beschluss F-12, Trigger
#: `tenure.forbid_common_area_occupancy`). Hier nur zur Vorprüfung — die DB
#: entscheidet.
UNIT_TYPES_OHNE_BELEGUNG = ("COMMON_AREA", "TECHNICAL_ROOM")


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------

def _aktiv_q(stichtag):
    """`Q`: gilt am Stichtag (halboffenes Intervall `[valid_from, valid_until)`)."""
    return Q(valid_from__lte=stichtag) & (
        Q(valid_until__isnull=True) | Q(valid_until__gt=stichtag)
    )


def belegungen_der_liegenschaft(property_id, *, stichtag=None, historie=False):
    """Alle Belegungen einer Liegenschaft — je Einheit, mit Beteiligten.

    Standardmäßig nur die **am Stichtag geltende** Belegung je Einheit (das ist
    die Frage der Liegenschaftsmappe: „wer wohnt hier?"). Mit `historie=True`
    kommen die beendeten dazu — „wer wohnte hier, als der Schaden entstand?".

    Die Zugehörigkeit zur Liegenschaft läuft über die **Einheit**
    (`occupancy → unit → property`); `occupancy` trägt selbst keine
    `property_id`, und das ist richtig so (der zusammengesetzte FK der Einheit
    garantiert sie bereits — eine zweite Spalte wäre eine zweite Wahrheit).
    """
    stichtag = stichtag or date.today()
    qs = Occupancy.objects.filter(unit__property_id=property_id)
    if not historie:
        qs = qs.filter(_aktiv_q(stichtag))
    return list(
        qs.select_related("unit")
        .prefetch_related(
            Prefetch(
                "parties",
                queryset=OccupancyParty.objects.select_related("party").order_by(
                    "role", "valid_from"
                ),
            )
        )
        .order_by("unit__unit_number", "-valid_from")
    )


def get_belegung(occupancy_id):
    return (
        Occupancy.objects.filter(pk=occupancy_id)
        .select_related("unit")
        .prefetch_related(
            Prefetch(
                "parties",
                queryset=OccupancyParty.objects.select_related("party").order_by(
                    "role", "valid_from"
                ),
            )
        )
        .first()
    )


def property_id_der_belegung(occupancy_id):
    """Die Liegenschaft hinter einer Belegung — für die Objektgrenze (404)."""
    return (
        Occupancy.objects.filter(pk=occupancy_id)
        .values_list("unit__property_id", flat=True)
        .first()
    )


def mieter_zeile(occupancy_party_id):
    """Eine Mieterzeile mit ihrer Belegung — für die Objektgrenze (404).

    Gibt `(occupancy_id, property_id)` zurück oder `None`. Die API braucht beides,
    bevor sie schreibt: die Liegenschaft für die Grenze, die Belegung für die
    Antwort.
    """
    treffer = (
        OccupancyParty.objects.filter(pk=occupancy_party_id)
        .values_list("occupancy_id", "occupancy__unit__property_id")
        .first()
    )
    return treffer


def aktive_mieter(occupancy, stichtag=None):
    """Die am Stichtag geltenden Beteiligten einer (vorgeladenen) Belegung.

    Arbeitet auf dem **Prefetch** (keine neue Query je Belegung — sonst wäre die
    Liegenschaftsmappe ein N+1 über alle Einheiten).
    """
    stichtag = stichtag or date.today()
    return [
        p
        for p in occupancy.parties.all()
        if p.valid_from <= stichtag
        and (p.valid_until is None or p.valid_until > stichtag)
    ]


# ---------------------------------------------------------------------------
# Vorprüfungen
# ---------------------------------------------------------------------------

def _pruefe_zeitraum(valid_from, valid_until, was="Der Zeitraum"):
    if valid_from is None:
        raise ValueError("Ein Gültig-ab-Datum ist erforderlich.")
    if valid_until is not None and valid_until <= valid_from:
        raise ValueError(
            f"{was}: Das Gültig-bis-Datum muss nach dem Gültig-ab-Datum liegen."
        )


def _pruefe_einheit_belegbar(unit_id):
    """F-12: Gemeinschaftsflächen und Technikräume tragen keine Belegung.

    Der **Trigger** entscheidet (und sperrt die Einheit dabei `FOR SHARE` gegen
    einen gleichzeitigen Typwechsel). Diese Vorprüfung erzeugt nur die lesbare
    Meldung statt eines nackten P0001-Durchschlags.
    """
    unit_type = (
        Unit.objects.filter(pk=unit_id).values_list("unit_type", flat=True).first()
    )
    if unit_type is None:
        raise ValueError(f"Einheit {unit_id} existiert nicht")
    if unit_type in UNIT_TYPES_OHNE_BELEGUNG:
        raise ValueError(
            "Gemeinschaftsflächen und Technikräume tragen keine Belegung "
            "(Beschluss F-12). Ein tatsächlich vermieteter Raum ist als eigene "
            "Einheit passenden Typs zu führen (z. B. Lager)."
        )


def _pruefe_ueberlappung(unit_id, valid_from, valid_until, ausser=None):
    """A-18: Belegungszeiträume derselben Einheit überlappen nie.

    Overlap zweier halboffener `daterange`: `ef < nu` UND `nf < eu`
    (dieselbe Formel wie in `property.add_party_role`). Die letzte Instanz bleibt
    `excl_occupancy` in der DB — diese Prüfung fängt nur den Normalfall lesbar ab.
    """
    bestehend = Occupancy.objects.filter(unit_id=unit_id).filter(
        Q(valid_until__isnull=True) | Q(valid_until__gt=valid_from)
    )
    if valid_until is not None:
        bestehend = bestehend.filter(valid_from__lt=valid_until)
    if ausser is not None:
        bestehend = bestehend.exclude(pk=ausser)
    if bestehend.exists():
        raise ValueError(
            "Für diese Einheit besteht in diesem Zeitraum bereits eine Belegung. "
            "Belegungszeiträume dürfen sich nicht überschneiden — die bisherige "
            "Belegung zuerst beenden."
        )


# ---------------------------------------------------------------------------
# Schreiben
# ---------------------------------------------------------------------------

def create_belegung(
    actor_app_user_id,
    *,
    unit_id,
    occupancy_type,
    valid_from,
    valid_until=None,
    contract_reference=None,
    mieter=None,
    eigentuemer_party_id=None,
):
    """Belegung anlegen — optional gleich mit ihren Mietern.

    `mieter` ist eine Liste von Dicts `{"party_id": …, "role": …,
    "valid_from": …?, "valid_until": …?}`. Fehlt der Zeitraum eines Mieters,
    erbt er den der Belegung — der Regelfall („die Wohnung ist ab 01.03. an
    Robco vermietet" ist **eine** Aussage, nicht zwei).

    **Belegung und Mieter entstehen in EINER Transaktion.** Das ist kein Komfort,
    sondern Pflicht: Der Containment-Trigger ist DEFERRED, damit genau das geht.
    Eine Belegung ohne Mieter und ein Mieter ohne Belegung wären zwei
    Halbzustände, die im UI als „Leerstand" gelesen würden.

    **Leerstand** ist der Aufruf ohne `mieter` (Typ `VACANT`) — er bleibt
    ausdrücklich zulässig.

    `eigentuemer_party_id` trägt den Eigentümer **zugleich in den Reiter
    „Eigentum"** ein (Saschas Befund: „wollen ja keine doppelte Arbeit"). Er ist
    kein Beteiligter der Belegung — wer vermietet, wohnt dort gerade nicht — und
    landet deshalb nicht in `occupancy_party`, sondern als Beteiligung an einem
    Eigentumsstand der Einheit. In **derselben** Transaktion: beides gilt
    zusammen oder gar nicht.
    """
    if occupancy_type not in OCCUPANCY_TYPES:
        raise ValueError(
            f"Ungültige Nutzungsart '{occupancy_type}'. "
            f"Erlaubt: {', '.join(OCCUPANCY_TYPES)}."
        )
    _pruefe_zeitraum(valid_from, valid_until, "Die Belegung")
    _pruefe_einheit_belegbar(unit_id)
    _pruefe_ueberlappung(unit_id, valid_from, valid_until)

    zeilen = _mieter_zeilen_vorbereiten(mieter or [], valid_from, valid_until)
    # Geplant VOR der Transaktion (dort laufen die Prüfungen), geschrieben darin.
    eigentum_plan = eigentum_service.plane_uebernahme_aus_belegung(
        unit_id=unit_id,
        party_id=eigentuemer_party_id,
        ab=valid_from,
        quelle_zusatz=_text(contract_reference),
    )

    with as_business_error():
        with business_transaction(actor_app_user_id):
            occupancy = Occupancy.objects.create(
                id=uuid.uuid4(),
                unit_id=unit_id,
                occupancy_type=occupancy_type,
                contract_reference=_text(contract_reference),
                valid_from=valid_from,
                valid_until=valid_until,
            )
            for z in zeilen:
                OccupancyParty.objects.create(
                    id=uuid.uuid4(), occupancy_id=occupancy.id, **z
                )
            eigentum_service.wende_uebernahme_an(eigentum_plan)
    return get_belegung(occupancy.id)


def update_belegung(actor_app_user_id, occupancy_id, felder):
    """Belegung ändern — **inklusive Beenden** (`valid_until` setzen).

    Es gibt kein Löschen (Trigger 0009). Eine irrtümlich erfasste Belegung wird
    beendet; die Historie bleibt lesbar, weil Aufträge und Berichte auf sie
    zeigen.

    Die **Einheit ist nicht änderbar** — eine Belegung, die die Wohnung wechselt,
    ist keine Korrektur, sondern eine andere Belegung (und sie würde still an der
    Überlappungsprüfung der neuen Einheit vorbeilaufen).
    """
    # Mit Beteiligten laden: `_mieter_mitziehen` braucht sie (und ihre Namen für
    # die Meldung) — sonst wäre jede Prüfung eine eigene Query.
    occupancy = get_belegung(occupancy_id)
    if occupancy is None:
        raise ValueError(f"Belegung {occupancy_id} existiert nicht")

    erlaubt = {"occupancy_type", "contract_reference", "valid_from", "valid_until"}
    unbekannt = set(felder) - erlaubt
    if unbekannt:
        raise ValueError(
            "Diese Felder lassen sich an einer Belegung nicht ändern: "
            + ", ".join(sorted(unbekannt))
        )

    neu_typ = felder.get("occupancy_type", occupancy.occupancy_type)
    if neu_typ not in OCCUPANCY_TYPES:
        raise ValueError(
            f"Ungültige Nutzungsart '{neu_typ}'. "
            f"Erlaubt: {', '.join(OCCUPANCY_TYPES)}."
        )
    neu_von = felder.get("valid_from", occupancy.valid_from)
    neu_bis = felder.get("valid_until", occupancy.valid_until)
    _pruefe_zeitraum(neu_von, neu_bis, "Die Belegung")
    _pruefe_ueberlappung(occupancy.unit_id, neu_von, neu_bis, ausser=occupancy.id)

    # Die Mieter müssen INNERHALB der Belegung liegen (deferred Trigger
    # `check_occupancy_children_ranges`). Siehe `_mieter_mitziehen`.
    mitzuziehen = _mieter_mitziehen(occupancy, neu_von, neu_bis)

    occupancy.occupancy_type = neu_typ
    occupancy.valid_from = neu_von
    occupancy.valid_until = neu_bis
    if "contract_reference" in felder:
        occupancy.contract_reference = _text(felder["contract_reference"])

    with as_business_error():
        with business_transaction(actor_app_user_id):
            # Explizite Feldliste: `updated_at` gehört der DB (Trigger).
            occupancy.save(
                update_fields=[
                    "occupancy_type",
                    "contract_reference",
                    "valid_from",
                    "valid_until",
                ]
            )
            # Dieselbe Transaktion — der Containment-Trigger ist DEFERRED und
            # prüft erst beim COMMIT. Genau dafür ist er deferred.
            for zeile in mitzuziehen:
                zeile.save(update_fields=["valid_until"])
    return get_belegung(occupancy.id)


def _mieter_mitziehen(occupancy, neu_von, neu_bis):
    """Beim Beenden einer Belegung enden die **offenen Mietverhältnisse mit**.

    **Der Fall, der diesen Code erzwungen hat** (ein Test hat ihn gefunden, nicht
    das Nachdenken): Robco wohnt in EG rechts, sein Mietverhältnis ist offen
    (`valid_until = NULL`). Er zieht aus, das Büro beendet die Belegung zum 31.03.
    — und die Datenbank weist das ab, weil ein **offener** Beteiligtenzeitraum
    nicht mehr in eine **geschlossene** Belegung passt
    (`assert_occupancy_party_contained`). Der Auszug, also der häufigste Vorgang
    überhaupt, wäre schlicht nicht durchführbar gewesen.

    Die Auflösung ist nicht, den Trigger zu lockern, sondern die Aussage
    auszusprechen: **Endet die Belegung, endet auch, wer in ihr wohnt** — zum
    selben Tag. Wer früher ausgezogen ist, behält sein früheres Datum (nur
    offene und überstehende Zeilen werden gekürzt).

    Zwei Fälle bleiben **Fehler**, weil sie keine ableitbare Auflösung haben:

    * Ein Mieter, der **erst nach** dem neuen Ende einzieht (sein Beginn läge
      hinter seinem Ende — der CHECK verböte es ohnehin). Was hier richtig wäre,
      weiß nur das Büro.
    * Ein Beginn der Belegung, der **hinter** den Einzug eines Mieters geschoben
      wird. Wann jemand eingezogen ist, ist eine Tatsache; sie stillschweigend zu
      verschieben wäre eine Fälschung.
    """
    if neu_von > occupancy.valid_from:
        zu_frueh = [
            z for z in occupancy.parties.all() if z.valid_from < neu_von
        ]
        if zu_frueh:
            raise ValueError(
                "Die Belegung kann nicht später beginnen als ihre Mieter: "
                + ", ".join(
                    f"{z.party.display_name} (ab {z.valid_from:%d.%m.%Y})"
                    for z in zu_frueh
                )
                + ". Zuerst die Mietverhältnisse anpassen."
            )
    if neu_bis is None:
        return []

    mitzuziehen = []
    for z in occupancy.parties.all():
        if z.valid_until is not None and z.valid_until <= neu_bis:
            continue  # Früher ausgezogen — bleibt, wie es war.
        if z.valid_from >= neu_bis:
            raise ValueError(
                f"{z.party.display_name} zieht am {z.valid_from:%d.%m.%Y} ein — "
                f"nach dem Ende der Belegung ({neu_bis:%d.%m.%Y}). "
                "Bitte zuerst dieses Mietverhältnis klären."
            )
        z.valid_until = neu_bis
        mitzuziehen.append(z)
    return mitzuziehen


def add_mieter(
    actor_app_user_id,
    occupancy_id,
    *,
    party_id,
    role,
    valid_from=None,
    valid_until=None,
    eigentuemer_party_id=None,
):
    """Einen Mieter/Nutzer an eine bestehende Belegung setzen.

    Ohne eigenen Zeitraum erbt er den der Belegung. Ein Zeitraum **außerhalb** der
    Belegung weist der deferred Containment-Trigger ab (422) — hier wird er
    vorgeprüft, damit die Meldung den Grund nennt.

    `eigentuemer_party_id` trägt denselben (oder einen anderen) Kontakt zugleich
    als **Eigentümer** der Einheit ein — siehe `create_belegung`.
    """
    occupancy = Occupancy.objects.filter(pk=occupancy_id).first()
    if occupancy is None:
        raise ValueError(f"Belegung {occupancy_id} existiert nicht")

    zeile = _mieter_zeilen_vorbereiten(
        [
            {
                "party_id": party_id,
                "role": role,
                "valid_from": valid_from,
                "valid_until": valid_until,
            }
        ],
        occupancy.valid_from,
        occupancy.valid_until,
    )[0]
    eigentum_plan = eigentum_service.plane_uebernahme_aus_belegung(
        unit_id=occupancy.unit_id,
        party_id=eigentuemer_party_id,
        ab=zeile["valid_from"],
        quelle_zusatz=occupancy.contract_reference,
    )

    with as_business_error():
        with business_transaction(actor_app_user_id):
            OccupancyParty.objects.create(
                id=uuid.uuid4(), occupancy_id=occupancy.id, **zeile
            )
            eigentum_service.wende_uebernahme_an(eigentum_plan)
    return get_belegung(occupancy.id)


def end_mieter(actor_app_user_id, occupancy_party_id, *, valid_until):
    """Ein Mieter zieht aus: `valid_until` setzen. **Kein Löschen** (Trigger 0009).

    Die Zeile bleibt — sie ist die Antwort auf „wer wohnte hier, als der Schaden
    entstand?". Das Enddatum muss nach dem Beginn liegen (CHECK); ein am selben
    Tag wieder zurückgenommener Eintrag ist deshalb nicht spurlos tilgbar, und das
    ist die bewusste Politik des Repos (F-02: Korrekturen laufen vorwärts).
    """
    zeile = OccupancyParty.objects.filter(pk=occupancy_party_id).first()
    if zeile is None:
        raise ValueError(f"Beteiligter {occupancy_party_id} existiert nicht")
    if valid_until is None:
        raise ValueError("Ein Enddatum ist erforderlich.")
    if valid_until <= zeile.valid_from:
        raise ValueError(
            "Das Enddatum muss nach dem Beginn des Mietverhältnisses liegen "
            f"(Beginn: {zeile.valid_from:%d.%m.%Y})."
        )
    occupancy = Occupancy.objects.filter(pk=zeile.occupancy_id).first()
    if occupancy.valid_until is not None and valid_until > occupancy.valid_until:
        raise ValueError(
            "Das Enddatum liegt nach dem Ende der Belegung "
            f"({occupancy.valid_until:%d.%m.%Y}). Ein Mieter kann nicht länger "
            "wohnen, als die Belegung gilt."
        )

    zeile.valid_until = valid_until
    with as_business_error():
        with business_transaction(actor_app_user_id):
            zeile.save(update_fields=["valid_until"])
    return get_belegung(zeile.occupancy_id)


# ---------------------------------------------------------------------------
# Intern
# ---------------------------------------------------------------------------

def _text(wert):
    """Leerer Text ist kein Wert (Repo-Standard)."""
    if wert is None:
        return None
    wert = wert.strip()
    return wert or None


def _mieter_zeilen_vorbereiten(mieter, bel_von, bel_bis):
    """Payload-Zeilen prüfen und auf DB-Felder abbilden (ohne zu schreiben)."""
    zeilen = []
    for m in mieter:
        role = m.get("role")
        if role not in OCCUPANCY_ROLES:
            raise ValueError(
                f"Ungültige Rolle '{role}'. Erlaubt: {', '.join(OCCUPANCY_ROLES)}."
            )
        party_id = m.get("party_id")
        if party_id is None:
            raise ValueError("Ein Mieter braucht einen Kontakt (party_id).")
        # Existenz + kein MERGED (spiegelt trg_occupancy_party_no_merged).
        ensure_party_usable(party_id, "Mieter")

        von = m.get("valid_from") or bel_von
        bis = m.get("valid_until") if m.get("valid_until") is not None else bel_bis
        _pruefe_zeitraum(von, bis, "Das Mietverhältnis")

        # Containment: Beteiligtenzeitraum ⊆ Belegungszeitraum (deferred Trigger).
        if von < bel_von:
            raise ValueError(
                "Das Mietverhältnis kann nicht vor der Belegung beginnen "
                f"(Belegung ab {bel_von:%d.%m.%Y})."
            )
        if bel_bis is not None and (bis is None or bis > bel_bis):
            raise ValueError(
                "Das Mietverhältnis kann nicht nach der Belegung enden "
                f"(Belegung bis {bel_bis:%d.%m.%Y})."
            )
        zeilen.append(
            {
                "party_id": party_id,
                "role": role,
                "valid_from": von,
                "valid_until": bis,
            }
        )
    return zeilen
