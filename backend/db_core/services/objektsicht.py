"""**Die eine** Definition von „meine Objekte" (row_scope EIGENE, Modul `property`).

## Warum es diese Datei gibt

Ein Mieter meldet „Heizkörper kalt". Zwei Tage vorher hat bei einem anderen Mieter
desselben Hauses ein Heizkörper geleckt und musste getauscht werden; im Objekt
steht außerdem eine Zentralanlage. Genau das muss der Monteur wissen, bevor er
losfährt — und genau das sah er bis zu diesem Slice nicht: Die Rolle MONTEUR hatte
Rechte nur auf `workflow` und `content` und sah ausschließlich **ihre eigenen
Einsätze**. Liegenschaft, Beteiligte, Objekthistorie: 403.

Seit der Rechte-Migration `0099` darf der Monteur das Objekt sehen, an dem er
arbeitet — und **nur** dieses. Diese Zeilenbegrenzung ist eine fachliche Regel,
und sie steht **an genau einer Stelle: hier**. Würde sie an zwei Stellen
ausformuliert, liefen die beiden Formulierungen auseinander — und die eine, die
großzügiger ist, wäre das Datenleck.

## Die Regel (wörtlich)

> Eine Liegenschaft ist „meine", wenn ich (`security.app_user`) über
> `workflow.job_assignment.assignee_id` einem `workflow.service_job` zugewiesen
> bin, dessen `property_id` sie ist — bzw., wenn `service_job.property_id` NULL
> ist, dessen `work_order.property_id` sie ist.

**Beide Nullbarkeiten sind real** und keine Vorsichtsmaßnahme:

  * Der **freie Termin** (Migration 0062) hat keinen Auftrag; seine Liegenschaft —
    falls gepflegt — steht direkt am Einsatz.
  * Ein **auftragsgebundener** Einsatz trägt oft keine eigene `property_id`; sie
    kommt dann aus dem Auftrag (die DB sichert über einen zusammengesetzten FK ab,
    dass beide, wenn gesetzt, identisch sind).

Dasselbe Coalesce steht in `api/planung.py::_property_ref` und in
`db_core/services/suche.py` — dort für die Anzeige, hier für die **Grenze**.

## Das Zeitfenster: KEINES (Entscheidung des Users)

Wer je einen Einsatz auf einem Objekt hatte, sieht dieses Objekt **dauerhaft**.
Kein „letzte 90 Tage", kein „nur solange der Auftrag offen ist". Begründung: Der
Wert der Objektkenntnis ist gerade der **Rückblick** („was war hier vorher?"); ein
Fenster, das ihn nach n Tagen abschneidet, nähme dem Slice seinen Zweck. Ein
Ex-Mitarbeiter verliert den Zugang nicht über das Fenster, sondern über seine
Rollenzuordnung (`security.user_role.valid_until`) und sein Login.

## Was NICHT hierüber läuft

* **Einsätze** (`api/planung.py`): Die Sicht des Monteurs auf Einsätze hängt
  weiterhin allein an der **Zuweisung**, nie am Objekt — sonst würde ein freier
  Termin (Begehung, Beratung) für jeden „öffentlich", der einmal am Objekt war.
  Über das Objekt (Dossier, Objekthistorie) sieht er die Einsätze der Kollegen
  **lesend**; disponieren und bearbeiten kann er weiterhin nur die eigenen.
* **Die RECHNUNG.** Sie ist die **eine** Ausnahme von „er darf alles sehen" und hat
  hier bewusst **keine** Funktion: Es gibt kein `eigene_rechnungen`. Seit Migration
  0102 trägt MONTEUR `invoicing/LESEN` (EIGENE) — die Rechnung bleibt trotzdem
  gesperrt, und zwar dort, wo sie gelesen wird: Jeder Rechnungs-Endpunkt benutzt
  `api/permissions.require`, das bei Scope EIGENE **403** wirft (fail-closed). Wer
  hier eine Rechnungs-Begrenzung einbaut, hebt genau diese Sperre auf.
* **Der PREIS.** Was der Monteur am Angebot sieht, begrenzt nicht diese Datei,
  sondern das **Schema**: `api/beleg.py::QuoteMengenDetailOut` führt kein Geldfeld.
  Die Zeilenbegrenzung steht hier (`eigene_angebote`), die Feldbegrenzung dort —
  beide zusammen, keine ersetzt die andere.
"""
from django.db.models import Q
from django.db.models.functions import Coalesce

from db_core.models import Party, Project, Property, Quote, ServiceJob


def eigene_property_ids(actor_id):
    """Die `property_id`s, an denen der Akteur je einen Einsatz hatte.

    Gibt ein **Values-Queryset** zurück (eine Spalte, `objekt_id`) — es ist als
    Subquery gedacht (`…filter(property_id__in=eigene_property_ids(actor))`) und
    wird nie selbst ausgewertet. Ohne Akteur: leere Menge (fail-closed) — ein Konto
    ohne `app_user` hat keine „eigenen" Zeilen, also gar keine.
    """
    qs = (
        ServiceJob.objects.filter(assignments__assignee_id=actor_id)
        if actor_id is not None
        else ServiceJob.objects.none()
    )
    return (
        qs.annotate(
            # Der freie Termin trägt die Liegenschaft selbst; der
            # auftragsgebundene Einsatz oft nur über seinen Auftrag.
            objekt_id=Coalesce("property_id", "work_order__property_id")
        )
        .filter(objekt_id__isnull=False)
        .values("objekt_id")
    )


def ist_eigenes_objekt(actor_id, property_id):
    """Ist DIESE Liegenschaft meine? (die Detail-/Schreibgrenze)"""
    if actor_id is None or property_id is None:
        return False
    return eigene_property_ids(actor_id).filter(objekt_id=property_id).exists()


def objekt_q(actor_id, pfad="property_id"):
    """`Q`-Ausdruck: „das Objekt hinter `pfad` ist meins".

    `pfad` ist der ORM-Pfad zur Liegenschaft der zu filternden Zeile — z. B.
    `"property_id"` (Auftrag, Vorgang), `"id"` (die Liegenschaft selbst) oder
    `"property_links__property_id"` (Projekt).
    """
    return Q(**{f"{pfad}__in": eigene_property_ids(actor_id)})


def begrenzen(qs, scope, actor_id, pfad="property_id"):
    """Bei Scope 'EIGENE' auf meine Objekte begrenzen; bei 'ALLE' unverändert.

    **Der einzige Weg, wie ein Endpunkt den Scope umsetzen darf.** Ein
    `require_scoped` ohne diesen Aufruf ist ein stiller Datenleak.
    """
    if scope != "EIGENE":
        return qs
    return qs.filter(objekt_q(actor_id, pfad))


def eigene_properties(actor_id):
    """Die Liegenschaften selbst (Queryset) — für Listen und Dossiers."""
    return Property.objects.filter(id__in=eigene_property_ids(actor_id))


def eigene_projekte(actor_id):
    """Projekte an meinen Objekten (über `workflow.project_property`)."""
    return Project.objects.filter(
        property_links__property_id__in=eigene_property_ids(actor_id)
    ).distinct()


def ist_eigenes_projekt(actor_id, project_id):
    if actor_id is None or project_id is None:
        return False
    return eigene_projekte(actor_id).filter(id=project_id).exists()


def due_item_q(actor_id):
    """`Q` auf `maintenance.due_item`: „diese Fälligkeit hängt an einem meiner Objekte".

    **Warum das nicht `objekt_q(actor, "property_id")` sein darf:** `due_item.property_id`
    ist als **einzige** der vier Wartungs-Entitäten **nullable**. Contract, Inspection
    und Warranty tragen die Liegenschaft NOT NULL — die Fälligkeit dagegen kann sie
    leer lassen und hängt dann nur an ihrem **Anker** (genau einer von contract |
    inspection | warranty, DB-CHECK), der seinerseits eine Liegenschaft trägt.

    Ein reiner `property_id__in`-Filter wäre für solche Zeilen zwar **fail-closed**
    (SQL: `NULL IN (…)` ist niemals wahr) — er würde dem Monteur aber die Fälligkeit
    an seinem eigenen Objekt vorenthalten, nur weil die denormalisierte Spalte leer
    blieb. Deshalb die Auflösung über den Anker.
    """
    objekte = eigene_property_ids(actor_id)
    return (
        Q(property_id__in=objekte)
        | Q(contract__property_id__in=objekte)
        | Q(inspection__property_id__in=objekte)
        | Q(warranty__property_id__in=objekte)
    )


def eigene_party_q(actor_id):
    """`Q` auf `identity.party`: „dieser Kontakt hängt an einem meiner Objekte".

    Acht Wege — mehr kennt das Schema nicht, und mehr darf der Monteur nicht sehen:

    | Weg | Tabelle |
    |---|---|
    | Beteiligter der Liegenschaft (Eigentümer, WEG, Hausmeister) | `property.property_party_role` |
    | Beteiligter eines Auftrags an meinem Objekt | `workflow.work_order_party` |
    | Melder eines Vorgangs an meinem Objekt | `workflow.service_case.reported_by_party` |
    | Ansprechpartner vor Ort an einem Einsatz meines Objekts | `workflow.service_job.on_site_contact_party` |
    | **Mieter/Nutzer einer Einheit meines Objekts** | `tenure.occupancy_party` |
    | **Verwalter meines Objekts** | `management.management_mandate.management_party` |
    | **Auftraggeber des Mandats** (die WEG) | `management.management_mandate.principal_party` |
    | **Standardkontakt und Zuständige des Mandats** | `…mandate.default_contact_party`, `management.management_responsibility` |

    Die vier neuen Wege kamen mit dem Belegungs-/Verwaltungs-Slice (0103) dazu, und
    sie sind **der Grund für ihn**: Der Monteur fährt zur Badenschen Straße und muss
    in die Wohnung EG rechts. Ohne Namen und Telefonnummer des Mieters kommt er
    nicht hinein; ohne den Verwalter weiß er nicht, wen er anruft, wenn niemand
    aufmacht. Genau das stand vorher hinter 403 — obwohl der Docstring dieser Datei
    es schon versprach.

    **Die Grenze bleibt das Objekt.** Der Mieter einer fremden Liegenschaft ist für
    ihn nicht vorhanden — auch nicht über die Suche, auch nicht über eine geratene
    ID (`api/objektgrenze.guard_party` → 404). Alles andere (Lieferanten,
    Rechnungsschuldner fremder Belege, der Kontaktbestand des Betriebs) bleibt
    unsichtbar. Der Monteur braucht die Nummer des Mieters, den er anruft — nicht
    das Adressbuch der Firma.

    **Die Belegung hängt über die Einheit am Objekt**, nicht direkt: `occupancy` →
    `unit` → `property`. Ein `occupancy.property_id` gibt es nicht (und soll es
    nicht geben — es wäre eine zweite, verwaisungsfähige Wahrheit über dieselbe
    Zugehörigkeit, die der zusammengesetzte FK der Einheit bereits garantiert).
    """
    objekte = eigene_property_ids(actor_id)
    return (
        Q(property_roles__property_id__in=objekte)
        | Q(work_order_roles__work_order__property_id__in=objekte)
        | Q(reported_cases__property_id__in=objekte)
        | Q(on_site_service_jobs__property_id__in=objekte)
        | Q(on_site_service_jobs__work_order__property_id__in=objekte)
        # Belegung (0103): der Mieter der Einheit — über die Einheit ans Objekt.
        | Q(occupancy_roles__occupancy__unit__property_id__in=objekte)
        # Verwaltung (0103): Verwalter, Auftraggeber, Standardkontakt, Zuständige.
        | Q(mandates_as_manager__property_id__in=objekte)
        | Q(mandates_as_principal__property_id__in=objekte)
        | Q(mandates_as_contact__property_id__in=objekte)
        | Q(mandate_responsibilities__mandate__property_id__in=objekte)
    )


def eigene_parties(actor_id):
    """Die Kontakte an meinen Objekten (Queryset, dublettenfrei)."""
    if actor_id is None:
        return Party.objects.none()
    return Party.objects.filter(eigene_party_q(actor_id)).distinct()


def ist_eigene_party(actor_id, party_id):
    if actor_id is None or party_id is None:
        return False
    return eigene_parties(actor_id).filter(id=party_id).exists()


# ---------------------------------------------------------------------------
# Angebote (Migration 0102) — die Zeilenbegrenzung. NICHT die Feldbegrenzung.
# ---------------------------------------------------------------------------

# Welche Angebote der Objektsicht überhaupt gezeigt werden.
#
# **Nur VERSENDET und ANGENOMMEN** — die beiden Status, in denen das Angebot eine
# Aussage nach außen ist:
#
#   * ENTWURF / INTERN_GEPRUEFT / FREIGEGEBEN sind **Bürokram**: Der Beleginhalt ist
#     dort noch änderbar (`beleg.QUOTE_EDITIERBAR`; erst der Versand friert ihn ein).
#     Ein Monteur, der eine Kalkulation von heute Vormittag als „das ist beauftragt"
#     liest, baut das Falsche ein — und die Position, die die Chefin nachmittags
#     streicht, hat er dann schon montiert.
#   * ABGELEHNT / ABGELAUFEN / ERSETZT sind ausdrücklich **nicht (mehr) das Soll**.
#     Sie anzuzeigen wäre die gefährlichste Variante: eine verbindlich aussehende
#     Mengenliste, die niemand bestellt hat. Das ERSETZT-Angebot hat einen
#     Nachfolger — und der ist, sobald er versendet ist, sichtbar.
#
# Der Bericht bezieht sein Soll über `work_order` aus genau denselben Belegen; die
# Grenze läuft also nicht gegen den Soll-Ist-Abgleich, sie deckt sich mit ihm.
ANGEBOT_STATUS_EIGENE = ("VERSENDET", "ANGENOMMEN")


def eigene_angebote(actor_id):
    """Die Angebote, die die Objektsicht sehen darf (Queryset).

    Zwei Bedingungen, beide notwendig: die Liegenschaft ist meine **und** der Beleg
    ist versendet/angenommen (`ANGEBOT_STATUS_EIGENE`). Ohne Akteur: leere Menge.

    **Das ist die Zeilengrenze, nicht die Feldgrenze.** Welche Felder ein Angebot
    preisgibt, entscheidet das preisfreie Schema in `api/beleg.py` — ein Aufrufer,
    der von hier zieht und `QuoteDetailOut` serialisiert, liefert dem Monteur die
    Marge.
    """
    if actor_id is None:
        return Quote.objects.none()
    return Quote.objects.filter(
        property_id__in=eigene_property_ids(actor_id),
        status__in=ANGEBOT_STATUS_EIGENE,
    )


def angebote_begrenzen(qs, scope, actor_id):
    """Bei Scope 'ALLE' unverändert; sonst auf sichtbare Angebote meiner Objekte.

    **Fail-closed als Default** (anders formuliert als `begrenzen` weiter oben, und
    das mit Absicht): Nur der ausdrücklich benannte Scope 'ALLE' gibt die volle Menge
    heraus; jeder andere Wert — auch ein künftiger dritter, den jemand in die Matrix
    schreibt — landet in der Objektgrenze statt in „alles herausgeben". An dieser
    Funktion hängt der Preis; ein Default, der im Zweifel öffnet, wäre hier die
    teuerste Bequemlichkeit des Hauses.
    """
    if scope == "ALLE":
        return qs
    if actor_id is None:
        return qs.none()
    return qs.filter(
        property_id__in=eigene_property_ids(actor_id),
        status__in=ANGEBOT_STATUS_EIGENE,
    )


def ist_eigenes_angebot(actor_id, quote_id):
    """Darf DIESES Angebot gelesen werden? (Detailgrenze — fremd/Entwurf → 404)"""
    if actor_id is None or quote_id is None:
        return False
    return eigene_angebote(actor_id).filter(id=quote_id).exists()
