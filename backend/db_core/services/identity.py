"""Identity-Service: Parties (Personen/Organisationen) anlegen und die
Kontaktmappe verdrahten — Ansprechpartner (party_relationship), Adressen
(address/party_address) und Kommunikationswege (contact_point).

Alle Writes laufen ausschließlich über db_core.db_context.business_transaction,
damit die DB-Trigger app.current_user_id sehen (Audit) und die
Typkonsistenz-Trigger greifen. Transaktionen bleiben kurz: Party + Subtyp in
einer Transaktion, keine weitere Arbeit darin.

Fremdschlüssel und Codelisten werden VOR dem Schreiben geprüft (→ ValueError,
den die API in 422 übersetzt). Die DB-Constraints (Exclusion über den
Gültigkeitszeitraum, CHECK, no-merged-Trigger) bleiben die letzte Instanz:
ihre Verletzung wird hier eingefangen und ebenfalls als ValueError gemeldet,
niemals als 500. DSGVO: es werden keine personenbezogenen Werte (Adressen,
Kontaktwege) in Fehlermeldungen aufgenommen.
"""
import datetime
import re
import uuid

from django.db import IntegrityError, transaction
from django.db.models import Count

from db_core.db_context import business_transaction, run_business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import (
    AcquisitionSource,
    Address,
    ContactPoint,
    Organization,
    Party,
    PartyAddress,
    PartyRelationship,
    Person,
    ServiceCase,
)
from db_core.services import objektsicht
from db_core.services._validation import ensure_party_usable

# Beschlossene Codelisten (Migration 0003).
ADDRESS_TYPES = ("BUSINESS", "POSTAL", "BILLING", "PRIVATE")
CONTACT_POINT_TYPES = ("EMAIL", "PHONE", "MOBILE", "FAX", "PORTAL")

# Beschlossene Codeliste der Organisationstypen (A-02, Migration 0002).
ORGANIZATION_TYPES = (
    "PROPERTY_MANAGEMENT",
    "WEG",
    "COMPANY",
    "AUTHORITY",
    "INSURER",
    "OTHER",
)


def personenname(first_name, last_name):
    """Anzeigename aus Vor- und Nachname — **die** eine Stelle dafür.

    Der Vorname ist seit Migration 0125 optional (Befund B1). Ohne ihn ist der
    Anzeigename schlicht der Nachname.

    Warum das eine öffentliche Funktion ist und kein f-String: Genau diese
    Verkettung stand an sieben weiteren Orten im Backend (Mitarbeiterliste,
    Zeiterfassung, Auswertungen, Suche), jeweils als
    `f"{p.first_name} {p.last_name}"`. Mit einem NULL-Vornamen erzeugt das den
    **literalen Text „None Özdemir"** — und das abschließende `.strip()`, das
    mehrere dieser Stellen tragen, hilft dagegen nicht. Ein Review hat das
    gefunden, nachdem der Vorname optional wurde. Wer künftig einen
    Personennamen zusammensetzt, ruft diese Funktion.
    """
    return " ".join(
        teil.strip() for teil in (first_name, last_name) if teil and teil.strip()
    )


def create_person(
    actor_app_user_id,
    first_name,
    last_name,
    salutation=None,
    title=None,
    birth_date=None,
):
    """Legt identity.party (PERSON) + identity.person in einer Transaktion an.

    Gibt die angelegte Party zurück. Der Anzeigename entsteht aus Vor- und
    Nachname; die Subtyp-Trigger stellen die Typkonsistenz sicher.
    """
    # display_name trägt den DB-CHECK btrim(...) <> '' (0002). Leere Namen vorab
    # als klaren 422 abweisen, statt sie als DB-IntegrityError (500) enden zu
    # lassen — analog zu create_property/create_organization.
    #
    # Der VORNAME ist seit Migration 0125 optional (Befund B1): Der Anrufer
    # nennt ihn oft nicht, und ein erfundenes „X" ist schlechter als gar
    # keiner. Ein Leerstring wird zu None normalisiert — die DB verbietet ihn
    # (person_first_name_nicht_leer), und „erhoben und leer" soll es nicht
    # geben. Der NACHNAME bleibt Pflicht (B3): ohne ihn gäbe es keinen
    # Anzeigenamen und keinen identifizierbaren Kontakt.
    first_name = (first_name or "").strip() or None
    if not last_name or not last_name.strip():
        raise ValueError("Der Nachname darf nicht leer sein.")
    display_name = personenname(first_name, last_name)
    with business_transaction(actor_app_user_id):
        # PK explizit: die Models tragen keinen Model-Default, ein von Django
        # eingesetztes NULL würde den DB-Default gen_random_uuid() aushebeln.
        party = Party.objects.create(
            id=uuid.uuid4(),
            party_type="PERSON",
            display_name=display_name,
            status="ACTIVE",
            version=1,
        )
        Person.objects.create(
            party=party,
            first_name=first_name,
            last_name=last_name,
            salutation=salutation,
            title=title,
            birth_date=birth_date,
        )
    return party


def create_organization(
    actor_app_user_id,
    legal_name,
    organization_type,
    display_name=None,
    legal_form=None,
    registration_number=None,
    tax_number=None,
    vat_id=None,
):
    """Legt identity.party (ORGANIZATION) + identity.organization an.

    organization_type wird gegen die beschlossene Codeliste geprüft; der
    Anzeigename fällt auf den Rechtsnamen zurück, wenn keiner angegeben ist.
    """
    if organization_type not in ORGANIZATION_TYPES:
        raise ValueError(
            f"Ungültiger organization_type '{organization_type}'. "
            f"Erlaubt: {', '.join(ORGANIZATION_TYPES)}."
        )
    # Beide strippen: `update_organization` vergleicht später
    # `party.display_name == org.legal_name`, um zu erkennen, ob der
    # Anzeigename abgeleitet war. Ein ungetrimmt gespeicherter Rechtsname
    # ließe diesen Vergleich still scheitern, und der Anzeigename folgte einer
    # Umfirmierung dann nicht.
    legal_name = (legal_name or "").strip()
    name = (display_name or legal_name).strip()
    with business_transaction(actor_app_user_id):
        party = Party.objects.create(
            id=uuid.uuid4(),
            party_type="ORGANIZATION",
            display_name=name,
            status="ACTIVE",
            version=1,
        )
        Organization.objects.create(
            party=party,
            organization_type=organization_type,
            legal_name=legal_name,
            legal_form=legal_form,
            registration_number=registration_number,
            tax_number=tax_number,
            vat_id=vat_id,
        )
    return party


def set_party_acquisition_source(actor_app_user_id, *, party_id, source_id):
    """Setzt/ändert den Akquisekanal eines Kontakts (`source_id=None` löst ihn wieder).

    Prüft, dass der Kanal existiert (sonst 422 statt FK-500). Gibt die Party zurück.
    """
    party = Party.objects.filter(id=party_id).first()
    if party is None:
        raise ValueError("Kontakt nicht gefunden.")
    if source_id is not None and not AcquisitionSource.objects.filter(id=source_id).exists():
        raise ValueError("Akquisekanal nicht gefunden.")
    party.acquisition_source_id = source_id
    with business_transaction(actor_app_user_id):
        party.save(update_fields=["acquisition_source_id", "updated_at"])
    party.refresh_from_db()
    return party


def set_party_note(actor_app_user_id, *, party_id, note):
    """Setzt/leert das freie Notizfeld eines Kontakts (identity.party.note).

    Freitext im Stammdaten-Tab (Hero-Angleichung Kontakte-3). Leerer/blanker Text
    wird zu NULL normalisiert, damit „gelöscht" und „nie gesetzt" gleich aussehen.
    Additive Spalte, kein No-Update-Trigger; Updates werden auditiert. Gibt die
    Party zurück.
    """
    party = Party.objects.filter(id=party_id).first()
    if party is None:
        raise ValueError("Kontakt nicht gefunden.")
    party.note = note.strip() if note and note.strip() else None
    with business_transaction(actor_app_user_id):
        party.save(update_fields=["note", "updated_at"])
    party.refresh_from_db()
    return party


# ---------------------------------------------------------------------------
# Ansprechpartner — party_relationship (CONTACT_PERSON_FOR)
# Konvention: from_party (Person) ist Ansprechpartner FÜR to_party (Organisation).
# ---------------------------------------------------------------------------

def list_contact_persons(organization_party_id, *, include_ended=False):
    """Ansprechpartner einer Organisation (aktive Beziehungen zuerst).

    Standardmäßig nur laufende Zuordnungen (valid_until IS NULL); mit
    include_ended auch beendete (für eine spätere Historie).
    """
    qs = (
        PartyRelationship.objects.filter(
            to_party_id=organization_party_id,
            relationship_type="CONTACT_PERSON_FOR",
        )
        .select_related("from_party", "from_party__person")
    )
    if not include_ended:
        qs = qs.filter(valid_until__isnull=True)
    return list(qs.order_by("-valid_until", "from_party__display_name", "id"))


def add_contact_person(
    actor_app_user_id,
    organization_party_id,
    *,
    person_party_id=None,
    new_person=None,
    valid_from=None,
):
    """Ordnet einer Organisation eine Person als Ansprechpartner zu.

    Entweder person_party_id (vorhandene Person) ODER new_person (dict mit
    first_name/last_name/…) — genau eines. Eine neue Person wird im selben
    Vorgang angelegt. Gibt die angelegte party_relationship zurück.
    """
    if (person_party_id is None) == (new_person is None):
        raise ValueError(
            "Genau eine Quelle angeben: bestehende Person ODER neue Person."
        )

    # Ziel muss eine (nicht zusammengeführte) Organisation sein.
    org = (
        Party.objects.filter(pk=organization_party_id)
        .values_list("party_type", "status")
        .first()
    )
    if org is None:
        raise ValueError(f"Kontakt {organization_party_id} existiert nicht")
    if org[1] == "MERGED":
        raise ValueError(
            f"Kontakt {organization_party_id} ist zusammengeführt; bitte die "
            "kanonische Partei verwenden"
        )
    if org[0] != "ORGANIZATION":
        raise ValueError(
            "Ansprechpartner können nur einer Organisation zugeordnet werden."
        )

    valid_from = valid_from or datetime.date.today()

    if person_party_id is not None:
        ensure_party_usable(person_party_id, label="Person")
        ptype = (
            Party.objects.filter(pk=person_party_id)
            .values_list("party_type", flat=True)
            .first()
        )
        if ptype != "PERSON":
            raise ValueError("Als Ansprechpartner ist nur eine Person zulässig.")
        if person_party_id == organization_party_id:
            raise ValueError("Eine Partei kann nicht ihr eigener Ansprechpartner sein.")

    try:
        with as_business_error(), business_transaction(actor_app_user_id):
            if person_party_id is None:
                person_party = create_person(actor_app_user_id, **new_person)
                # create_person öffnet eine eigene, bereits geschlossene
                # Transaktion; hier läuft weiter der äußere Kontext.
                target_person_id = person_party.id
            else:
                target_person_id = person_party_id

            relationship = PartyRelationship.objects.create(
                id=uuid.uuid4(),
                from_party_id=target_person_id,
                to_party_id=organization_party_id,
                relationship_type="CONTACT_PERSON_FOR",
                valid_from=valid_from,
            )
    except IntegrityError as exc:
        if "excl_party_relationship_dup" in str(exc):
            raise ValueError(
                "Diese Person ist im angegebenen Zeitraum bereits als "
                "Ansprechpartner zugeordnet."
            ) from exc
        raise
    return relationship


def remove_contact_person(actor_app_user_id, relationship_id):
    """Beendet eine Ansprechpartner-Zuordnung (valid_until = heute).

    Kein Löschen (trg_party_relationship_no_delete); die Beziehung wird zeitlich
    beendet und der Vorgang auditiert. Wurde sie am selben Tag angelegt, endet
    sie am Folgetag (der CHECK verlangt valid_until > valid_from).
    """
    rel = PartyRelationship.objects.filter(
        pk=relationship_id, relationship_type="CONTACT_PERSON_FOR"
    ).first()
    if rel is None:
        raise ValueError("Ansprechpartner-Zuordnung nicht gefunden.")
    if rel.valid_until is not None:
        raise ValueError("Diese Zuordnung ist bereits beendet.")
    ende = datetime.date.today()
    if ende <= rel.valid_from:
        ende = rel.valid_from + datetime.timedelta(days=1)
    with business_transaction(actor_app_user_id):
        PartyRelationship.objects.filter(pk=rel.id).update(valid_until=ende)
    rel.valid_until = ende
    return rel


def contact_person_case_counts(person_party_ids, *, scope="ALLE", actor_id=None):
    """Anzahl der Vorgänge, die die jeweilige Person **gemeldet** hat.

    `{person_party_id: anzahl}` — in **einer** Aggregat-Query für beliebig viele
    Personen (N+1-frei). Personen ohne gemeldeten Vorgang fehlen im Ergebnis (der
    Aufrufer setzt sie auf 0).

    **Die fachliche Kante** ist `workflow.service_case.reported_by_party_id` (der
    Melder eines Vorgangs). Eine direkte Person→**Projekt**-Kante gibt es im Schema
    nicht: `workflow.project` trägt keinen Party-FK, Projekte hängen über
    `project_property` an Liegenschaften. Deshalb wird hier bewusst die
    Vorgangs-Melderrolle gezählt, nicht ein konstruierter Projektbezug. Die
    Zuordnung trifft die Person als Party — unabhängig davon, ob sie zufällig
    Ansprechpartner der Organisation ist, deren Mappe gerade offen ist; die
    Beschriftung im Frontend spricht deshalb von „gemeldeten Vorgängen".

    Scope 'EIGENE' (Objektsicht, Monteur): nur Vorgänge an **meinen** Objekten —
    sonst verriete die bloße Zahl Aktivität an fremden Objekten (fail-closed: ohne
    Akteur leere Menge).
    """
    ids = {p for p in (person_party_ids or []) if p is not None}
    if not ids:
        return {}
    qs = ServiceCase.objects.filter(reported_by_party_id__in=ids)
    if scope == "EIGENE":
        if actor_id is None:
            return {}
        qs = qs.filter(property_id__in=objektsicht.eigene_property_ids(actor_id))
    return {
        row["reported_by_party_id"]: row["n"]
        for row in qs.values("reported_by_party_id").annotate(n=Count("id"))
    }


# ---------------------------------------------------------------------------
# Namen ändern (Befund H4) — Heirat, Umfirmierung
# ---------------------------------------------------------------------------

def update_person(actor_app_user_id, party_id, daten):
    """Teil-Update (PATCH) der Personendaten.

    Heirat, Namensangleichung, Tippfehler — bis Migration 0126 gab es dafür
    **keinen Pfad**: kein Endpunkt, kein Service, und ohne Audit-Trigger auch
    keinen Nachweis (Befund H4). Die DSGVO trägt als Gegenargument nicht,
    im Gegenteil: Art. 16 verlangt das Recht auf Berichtigung (H6).

    Entscheidend ist das Mitziehen von `party.display_name`: Er ist der Name in
    jeder Liste, jeder Suche und jedem Beleg. Bliebe er stehen, hieße die
    Person in ihrer Mappe „Müller" und überall sonst weiter „Meyer" — schlimmer
    als gar keine Änderung, weil der Widerspruch nicht auffällt.
    """
    person = Person.objects.filter(party_id=party_id).first()
    if person is None:
        raise ValueError("Person nicht gefunden.")

    daten = daten or {}
    werte = {}
    if "first_name" in daten:
        # Optional seit 0125; Leerstring wird zu NULL (DB-CHECK).
        werte["first_name"] = _text_oder_none(daten["first_name"])
    if "last_name" in daten:
        wert = daten["last_name"]
        if not wert or not str(wert).strip():
            raise ValueError("Der Nachname ist ein Pflichtfeld und darf nicht leer sein.")
        werte["last_name"] = str(wert).strip()
    for feld in ("salutation", "title"):
        if feld in daten:
            werte[feld] = _text_oder_none(daten[feld])
    if "birth_date" in daten:
        werte["birth_date"] = daten["birth_date"]

    if not werte:
        return Party.objects.get(pk=party_id)

    with business_transaction(actor_app_user_id):
        Person.objects.filter(party_id=party_id).update(**werte)
        # Gegen den ZIELZUSTAND rechnen: Wer nur den Nachnamen sendet, behält
        # den gespeicherten Vornamen im Anzeigenamen.
        neu = Person.objects.get(party_id=party_id)
        Party.objects.filter(pk=party_id).update(
            display_name=personenname(neu.first_name, neu.last_name)
        )
    return Party.objects.get(pk=party_id)


def update_organization(actor_app_user_id, party_id, daten):
    """Teil-Update (PATCH) der Organisationsdaten — Umfirmierung.

    Der Anzeigename ist der Knackpunkt. `identity.organization` hat **keine**
    Spalte `display_name` — der Anzeigename lebt allein an `identity.party`,
    und beim Anlegen ist er entweder ausdrücklich gesetzt oder aus dem
    Rechtsnamen abgeleitet (`create_organization`). Danach lässt sich beides
    nicht mehr unterscheiden: Es ist dasselbe Feld.

    Daraus die Regel, die hier gilt:

    * `display_name` ausdrücklich gesendet → er gewinnt (leer = zurück auf den
      Rechtsnamen).
    * sonst: Der Anzeigename folgt einem neuen Rechtsnamen **nur dann**, wenn
      er vorher exakt der alte Rechtsname war — also erkennbar abgeleitet.
      Ein bewusst abweichender Anzeigename („Wolff Sanitär") überlebt eine
      Umfirmierung.
    """
    org = Organization.objects.filter(party_id=party_id).first()
    if org is None:
        raise ValueError("Organisation nicht gefunden.")

    daten = daten or {}
    anzeige_gesendet = "display_name" in daten
    anzeige_wert = _text_oder_none(daten.get("display_name"))
    werte = {}
    if "legal_name" in daten:
        wert = daten["legal_name"]
        if not wert or not str(wert).strip():
            raise ValueError("Der Firmenname ist ein Pflichtfeld und darf nicht leer sein.")
        werte["legal_name"] = str(wert).strip()
    if "organization_type" in daten:
        if daten["organization_type"] not in ORGANIZATION_TYPES:
            raise ValueError(
                f"Ungültiger Organisationstyp '{daten['organization_type']}'. "
                f"Erlaubt: {', '.join(ORGANIZATION_TYPES)}."
            )
        werte["organization_type"] = daten["organization_type"]
    # `display_name` ist bewusst NICHT dabei — die Spalte gibt es an
    # `identity.organization` nicht, der Anzeigename gehört der Party.
    for feld in ("legal_form", "registration_number", "tax_number", "vat_id"):
        if feld in daten:
            werte[feld] = _text_oder_none(daten[feld])

    if not werte and not anzeige_gesendet:
        return Party.objects.get(pk=party_id)

    party = Party.objects.get(pk=party_id)
    neuer_rechtsname = werte.get("legal_name", org.legal_name)
    if anzeige_gesendet:
        neuer_anzeigename = anzeige_wert or neuer_rechtsname
    elif party.display_name == org.legal_name:
        # War abgeleitet — folgt mit.
        neuer_anzeigename = neuer_rechtsname
    else:
        # War bewusst abweichend — bleibt.
        neuer_anzeigename = party.display_name

    with business_transaction(actor_app_user_id):
        if werte:
            Organization.objects.filter(party_id=party_id).update(**werte)
        if neuer_anzeigename != party.display_name:
            Party.objects.filter(pk=party_id).update(display_name=neuer_anzeigename)
    return Party.objects.get(pk=party_id)


# ---------------------------------------------------------------------------
# Kontakt in einem Rutsch (Befunde F1/F3)
# ---------------------------------------------------------------------------

def kontakt_durchstich(
    actor_app_user_id,
    *,
    anlegen,
    phone=None,
    email=None,
    adresse=None,
):
    """Kontakt + Kommunikationswege + Adresse in EINER Transaktion.

    Der Anlage-Dialog kannte bisher nur Namensfelder (Befund F1). Telefon und
    Adresse waren danach in **zwei verschiedenen Reitern** der Kontaktmappe
    nachzutragen — und weil nach dem Anlegen nicht einmal dorthin navigiert
    wurde (F2), musste der Kontakt vorher in der Liste wiedergefunden werden.
    Für einen einzigen zusammenhängenden Vorgang.

    Dass das fachlich zulässig ist, war nie die Frage: `quick-intake` und
    `/planung/anruf` legen Person und Kontaktwege längst atomar an (F3). Was
    beiden fehlt, ist die **Adresse am Kontakt** (F4) — die entsteht dort nur an
    der Liegenschaft. Hier ist sie dabei.

    `anlegen` ist ein Aufruf ohne Argumente, der die Party erzeugt und
    zurückgibt (`create_person` oder `create_organization`, jeweils vorbelegt).
    So trägt diese Funktion die Reihenfolge und die Transaktionsklammer, ohne
    Personen- und Organisationsfelder doppelt zu kennen.

    **Alles oder nichts:** Die service-internen `business_transaction`-Aufrufe
    werden hier zu Savepoints. Scheitert die Adresse, entsteht keine Person ohne
    sie — und das wäre sonst eine Waise, die der No-Delete-Schutz nicht mehr
    entfernen könnte (dasselbe Argument wie bei `quick_intake`).
    """

    def _durchstich():
        party = anlegen()
        if phone and phone.strip():
            add_contact_point(
                actor_app_user_id,
                party.id,
                contact_type="PHONE",
                value=phone,
                is_primary=True,
            )
        if email and email.strip():
            add_contact_point(
                actor_app_user_id,
                party.id,
                contact_type="EMAIL",
                value=email,
                is_primary=True,
            )
        if adresse:
            add_address(actor_app_user_id, party.id, **adresse)
        return party

    return run_business_transaction(actor_app_user_id, _durchstich)


# ---------------------------------------------------------------------------
# Adressen — address + party_address
# ---------------------------------------------------------------------------

def list_addresses(party_id, *, include_ended=False):
    """Adresszuordnungen eines Kontakts inkl. der Adressdaten."""
    qs = PartyAddress.objects.filter(party_id=party_id).select_related("address")
    if not include_ended:
        qs = qs.filter(valid_until__isnull=True)
    return list(qs.order_by("-is_primary", "address_type", "-valid_from", "id"))


def _party_address_anlegen(
    party_id, address_id, *, address_type, is_primary, valid_from, label
):
    """Die Zuordnungszeile selbst — ohne eigene Transaktion.

    Getrennt gehalten, weil das Zuordnen einer BESTEHENDEN Adresse (der von
    Befund G3 vermisste Weg) genau hier ansetzt: `identity.address` ist ein
    gemeinsamer Topf und append-only, `party_address.address_id` und
    `property.address_id` dürfen dieselbe Zeile referenzieren. Es fehlt nur der
    Aufrufer — siehe die Begründung in `api/projekt.py` (quick-intake), warum
    das NICHT automatisch geschehen darf.
    """
    return PartyAddress.objects.create(
        id=uuid.uuid4(),
        party_id=party_id,
        address_id=address_id,
        address_type=address_type,
        is_primary=is_primary,
        valid_from=valid_from,
        label=(label.strip() if label and label.strip() else None),
    )


def _pruefe_adressfelder(street, postal_code, city, country_code):
    """Spiegelt die DB-CHECKs auf `identity.address` (0003).

    Vorab prüfen, sonst enden Fehleingaben als roher IntegrityError — also 500
    statt Meldung. Dieselbe Politik wie bei den Namen in `create_person`.
    Die Regeln sind absichtlich **identisch** zur DB, nicht strenger: sonst
    lehnte die Anwendung Werte ab, die die Datenbank annähme.
    """
    for feldname, wert in (
        ("Die Straße", street), ("Die PLZ", postal_code), ("Der Ort", city),
    ):
        if not wert or not str(wert).strip():
            raise ValueError(f"{feldname} darf nicht leer sein.")
    if not re.fullmatch(r"[A-Z]{2}", str(country_code or "")):
        raise ValueError(
            f"Ungültiges Länderkürzel '{country_code}'. "
            "Erwartet werden zwei Großbuchstaben (z. B. DE)."
        )


def _adress_dublette(exc):
    """Der Exclusion-Constraint als Fachfehler statt als 500.

    Gibt `None` zurück, wenn es NICHT die Dublette war — die Aufrufstelle
    reicht dann blank weiter. `raise exc from exc` wäre der bequemere Weg,
    setzte aber `__cause__` auf die Exception selbst und unterdrückte damit
    die echte Ursachenkette in der Ausgabe.
    """
    if "excl_party_address_primary" in str(exc):
        return ValueError(
            "Für diesen Kontakt existiert im angegebenen Zeitraum bereits "
            "eine primäre Adresse dieses Typs."
        )
    return None


def add_address(
    actor_app_user_id,
    party_id,
    *,
    address_type,
    street,
    postal_code,
    city,
    house_number=None,
    address_addition=None,
    country_code="DE",
    is_primary=True,
    valid_from=None,
    label=None,
):
    """Legt eine Adresse an und ordnet sie dem Kontakt mit Typ zu.

    Die zeitliche Exklusivität je (Party, Typ) für primäre Adressen erzwingt der
    DB-Constraint excl_party_address_primary; seine Verletzung wird als
    ValueError (→ 422) gemeldet. `label` ist ein optionaler freier Titel der
    Zuordnung (z. B. „Baustelle Nord", Hero-Angleichung Kontakte-6) — leer wird
    zu NULL normalisiert. Gibt die angelegte party_address zurück.
    """
    if address_type not in ADDRESS_TYPES:
        raise ValueError(
            f"Ungültiger Adresstyp '{address_type}'. "
            f"Erlaubt: {', '.join(ADDRESS_TYPES)}."
        )
    _pruefe_adressfelder(street, postal_code, city, country_code)
    ensure_party_usable(party_id, label="Kontakt")
    valid_from = valid_from or datetime.date.today()

    try:
        with business_transaction(actor_app_user_id):
            address = Address.objects.create(
                id=uuid.uuid4(),
                street=street,
                house_number=house_number,
                address_addition=address_addition,
                postal_code=postal_code,
                city=city,
                country_code=country_code,
            )
            link = _party_address_anlegen(
                party_id,
                address.id,
                address_type=address_type,
                is_primary=is_primary,
                valid_from=valid_from,
                label=label,
            )
    except IntegrityError as exc:
        fachlich = _adress_dublette(exc)
        if fachlich is None:
            raise
        raise fachlich from exc
    return link


def update_party_address(actor_app_user_id, party_address_id, daten):
    """Teil-Update (PATCH) einer Adress**zuordnung** — Typ, Primär, Titel.

    Der Adress**inhalt** bleibt außen vor: `identity.address` ist append-only
    (Trigger `trg_address_immutable`, 0003, Befund H1). Wer die Straße
    korrigieren will, nimmt `ersetze_party_address` — das ist kein Umweg,
    sondern der vorgesehene Weg.
    """
    zuordnung = PartyAddress.objects.filter(pk=party_address_id).first()
    if zuordnung is None:
        raise ValueError("Adresszuordnung nicht gefunden.")
    if zuordnung.valid_until is not None:
        raise ValueError(
            "Diese Adresszuordnung ist beendet und kann nicht mehr geändert "
            "werden. Lege bei Bedarf eine neue an."
        )

    daten = daten or {}
    werte = {}
    if "address_type" in daten:
        if daten["address_type"] not in ADDRESS_TYPES:
            raise ValueError(
                f"Ungültiger Adresstyp '{daten['address_type']}'. "
                f"Erlaubt: {', '.join(ADDRESS_TYPES)}."
            )
        werte["address_type"] = daten["address_type"]
    if "is_primary" in daten:
        werte["is_primary"] = bool(daten["is_primary"])
    if "label" in daten:
        werte["label"] = _text_oder_none(daten["label"])

    if not werte:
        return zuordnung

    try:
        with business_transaction(actor_app_user_id):
            PartyAddress.objects.filter(pk=zuordnung.id).update(**werte)
    except IntegrityError as exc:
        fachlich = _adress_dublette(exc)
        if fachlich is None:
            raise
        raise fachlich from exc
    return PartyAddress.objects.get(pk=zuordnung.id)


def ersetze_party_address(
    actor_app_user_id,
    party_address_id,
    *,
    street,
    postal_code,
    city,
    house_number=None,
    address_addition=None,
    country_code="DE",
):
    """Adressinhalt korrigieren: **neue** Adresszeile anlegen und umhängen.

    Das ist der von Befund H1 vorgezeichnete Weg. `identity.address` ist
    append-only — eine Korrektur ändert die Zeile nicht, sie ersetzt sie. Typ,
    Primär-Kennzeichen, Titel und Gültigkeitsbeginn der Zuordnung bleiben
    stehen; nur `address_id` zeigt danach woanders hin.

    Bewusst **ohne** client-gelieferte `address_id`: Ein Endpunkt, der eine
    beliebige bestehende Adresse annimmt, ließe eine fremde Anschrift
    unterschieben — genau der Fehler, der in AP1 an `building.address_id`
    aufgefallen ist. Hier entsteht die Zeile serverseitig aus den Feldern.

    Die alte Adresszeile bleibt: Andere Zuordnungen oder Liegenschaften können
    auf sie zeigen, und Belege haben sie womöglich schon geschnappschusst.
    """
    zuordnung = PartyAddress.objects.filter(pk=party_address_id).first()
    if zuordnung is None:
        raise ValueError("Adresszuordnung nicht gefunden.")
    if zuordnung.valid_until is not None:
        raise ValueError("Diese Adresszuordnung ist beendet.")
    _pruefe_adressfelder(street, postal_code, city, country_code)

    with business_transaction(actor_app_user_id):
        neu = Address.objects.create(
            id=uuid.uuid4(),
            street=street.strip(),
            house_number=house_number,
            address_addition=address_addition,
            postal_code=postal_code.strip(),
            city=city.strip(),
            country_code=country_code,
        )
        PartyAddress.objects.filter(pk=zuordnung.id).update(address_id=neu.id)
    return PartyAddress.objects.select_related("address").get(pk=zuordnung.id)


def end_party_address(actor_app_user_id, party_address_id, *, valid_until=None):
    """Beendet eine Adresszuordnung zeitlich — kein Löschen (Trigger 0126).

    Der Umzug: Die Adresse GALT und gilt nicht mehr. Die Zeile bleibt lesbar,
    weil Aufträge und Belege aus der Zeit auf sie zeigen.

    Dieselbe Klemme wie beim Kommunikationsweg: `CHECK (valid_until >
    valid_from)` verbietet ein Ende am Anlagetag. Eine noch heute
    zurückgenommene Zuordnung wird deshalb auf morgen datiert — sie spurlos zu
    tilgen ist die bewusste Politik des Repos nicht (F-02: Korrekturen laufen
    vorwärts).
    """
    zuordnung = PartyAddress.objects.filter(pk=party_address_id).first()
    if zuordnung is None:
        raise ValueError("Adresszuordnung nicht gefunden.")
    if zuordnung.valid_until is not None:
        raise ValueError("Diese Adresszuordnung ist bereits beendet.")

    ende = valid_until or datetime.date.today()
    # `CHECK (valid_until > valid_from)` verbietet ein Ende am Anlagetag.
    am_selben_tag = ende <= zuordnung.valid_from
    if am_selben_tag:
        ende = zuordnung.valid_from + datetime.timedelta(days=1)

    werte = {"valid_until": ende}
    # Der Fehlgriff am selben Tag — und die Falle, die ein Review gefunden hat:
    #
    # `excl_party_address_primary` schließt sich über `daterange(valid_from,
    # valid_until)` WHERE is_primary. Die beendete Zeile belegt danach
    # [heute, morgen) — und das überlappt die neue [heute, ) weiterhin. Wer
    # eine falsche Primäradresse anlegt, beendet und die richtige eintragen
    # will, liefe also in ein 422 mit der irreführenden Meldung, es gebe
    # bereits eine primäre Adresse (die es nur noch als Leiche gibt).
    #
    # Deshalb verliert eine am selben Tag zurückgenommene Zuordnung auch ihr
    # Primär-Kennzeichen: Eine Primäradresse, die keinen einzigen Tag galt,
    # war nie eine — sie darf den Platz für die echte nicht blockieren. Die
    # Zeile selbst bleibt (F-02: Korrekturen laufen vorwärts, nichts wird
    # spurlos getilgt), sie ist nur nicht mehr die primäre.
    if am_selben_tag and zuordnung.is_primary:
        werte["is_primary"] = False

    with business_transaction(actor_app_user_id):
        PartyAddress.objects.filter(pk=zuordnung.id).update(**werte)
    return PartyAddress.objects.get(pk=zuordnung.id)


# ---------------------------------------------------------------------------
# Kommunikationswege — contact_point
# ---------------------------------------------------------------------------

def list_contact_points(party_id, *, include_ended=False):
    """Kommunikationswege eines Kontakts (primäre zuerst)."""
    qs = ContactPoint.objects.filter(party_id=party_id)
    if not include_ended:
        qs = qs.filter(valid_until__isnull=True)
    return list(qs.order_by("-is_primary", "contact_type", "-valid_from", "id"))


def contact_points_bulk(party_ids, *, include_ended=False):
    """Kommunikationswege **vieler** Kontakte in EINER Query.

    `{party_id: [ContactPoint, …]}`, je Party in derselben Reihenfolge wie
    `list_contact_points` (primäre zuerst). Für Listen, die je Zeile einen
    Kontakt zeigen — sechs Mieter an sechs Einheiten wären sonst sechs Queries,
    und die Liegenschaftsmappe der WEG wächst mit der Zahl der Wohnungen.
    """
    ids = {p for p in (party_ids or []) if p is not None}
    if not ids:
        return {}
    qs = ContactPoint.objects.filter(party_id__in=ids)
    if not include_ended:
        qs = qs.filter(valid_until__isnull=True)
    treffer = {}
    for cp in qs.order_by("-is_primary", "contact_type", "-valid_from", "id"):
        treffer.setdefault(cp.party_id, []).append(cp)
    return treffer


def add_contact_point(
    actor_app_user_id,
    party_id,
    *,
    contact_type,
    value,
    label=None,
    is_primary=False,
    valid_from=None,
):
    """Legt einen Kommunikationsweg (Tel/Mobil/E-Mail/Fax/Portal) an.

    Die Exklusivität je (Party, Typ) für primäre Wege erzwingt
    excl_contact_point_primary; Verletzung → ValueError (422).
    """
    if contact_type not in CONTACT_POINT_TYPES:
        raise ValueError(
            f"Ungültiger Kontakttyp '{contact_type}'. "
            f"Erlaubt: {', '.join(CONTACT_POINT_TYPES)}."
        )
    if not value or not value.strip():
        raise ValueError("Der Wert des Kommunikationswegs darf nicht leer sein.")
    ensure_party_usable(party_id, label="Kontakt")
    valid_from = valid_from or datetime.date.today()

    try:
        with business_transaction(actor_app_user_id):
            point = ContactPoint.objects.create(
                id=uuid.uuid4(),
                party_id=party_id,
                contact_type=contact_type,
                value=value.strip(),
                label=(label.strip() if label and label.strip() else None),
                is_primary=is_primary,
                valid_from=valid_from,
            )
    except IntegrityError as exc:
        if "excl_contact_point_primary" in str(exc):
            raise ValueError(
                "Für diesen Kontakt existiert im angegebenen Zeitraum bereits "
                "ein primärer Kommunikationsweg dieses Typs."
            ) from exc
        raise
    return point


def update_contact_point(actor_app_user_id, contact_point_id, daten):
    """Teil-Update (PATCH) eines Kommunikationswegs — die **Korrektur**.

    Der Unterschied zum Beenden (`deactivate_contact_point`) ist fachlich, nicht
    technisch, und er entscheidet, welcher Weg richtig ist:

    * **Korrektur** — die Nummer war von Anfang an falsch getippt. Es gab nie
      einen Zeitraum, in dem sie galt; ihre „Historie" ist Rauschen. Hier.
    * **Wechsel** — die alte Nummer galt und gilt nicht mehr. Dann beenden und
      eine neue anlegen, damit erkennbar bleibt, unter welcher Nummer man den
      Kontakt im Mai erreicht hat.

    Bis Migration 0126 war die Unterscheidung müßig, weil es KEINEN der beiden
    Wege für den Wert selbst gab (Befund H2) — und weil ohne Audit-Trigger
    niemand hätte nachvollziehen können, dass korrigiert wurde. Beides steht
    jetzt.

    Ein beendeter Weg ist nicht mehr korrigierbar: Er ist Geschichte.
    """
    point = ContactPoint.objects.filter(pk=contact_point_id).first()
    if point is None:
        raise ValueError("Kommunikationsweg nicht gefunden.")
    if point.valid_until is not None:
        raise ValueError(
            "Dieser Kommunikationsweg ist beendet und kann nicht mehr geändert "
            "werden. Lege bei Bedarf einen neuen an."
        )

    daten = daten or {}
    werte = {}
    if "contact_type" in daten:
        if daten["contact_type"] not in CONTACT_POINT_TYPES:
            raise ValueError(
                f"Ungültiger Typ '{daten['contact_type']}'. "
                f"Erlaubt: {', '.join(CONTACT_POINT_TYPES)}."
            )
        werte["contact_type"] = daten["contact_type"]
    if "value" in daten:
        wert = daten["value"]
        if not wert or not str(wert).strip():
            raise ValueError("Der Wert darf nicht leer sein.")
        werte["value"] = str(wert).strip()
    if "label" in daten:
        werte["label"] = _text_oder_none(daten["label"])
    if "is_primary" in daten:
        werte["is_primary"] = bool(daten["is_primary"])

    if not werte:
        return point

    try:
        with business_transaction(actor_app_user_id):
            ContactPoint.objects.filter(pk=point.id).update(**werte)
    except IntegrityError as exc:
        if "excl_contact_point_primary" in str(exc):
            raise ValueError(
                "Für diesen Kontakt existiert im angegebenen Zeitraum bereits "
                "ein primärer Kommunikationsweg dieses Typs."
            ) from exc
        raise
    return ContactPoint.objects.get(pk=point.id)


def _text_oder_none(wert):
    """Leerstring wie „nicht gesetzt" behandeln (NULL-fähige Textspalten)."""
    if wert is None:
        return None
    text = str(wert).strip()
    return text or None


def deactivate_contact_point(actor_app_user_id, contact_point_id):
    """Beendet einen Kommunikationsweg zeitlich (valid_until = heute).

    Seit Migration 0126 trägt `contact_point` das No-Delete-Verbot; beendet
    statt gelöscht wird er wie die übrigen zeitabhängigen Zuordnungen ohnehin
    schon immer (Historie).
    """
    point = ContactPoint.objects.filter(pk=contact_point_id).first()
    if point is None:
        raise ValueError("Kommunikationsweg nicht gefunden.")
    if point.valid_until is not None:
        raise ValueError("Dieser Kommunikationsweg ist bereits beendet.")

    ende = datetime.date.today()
    am_selben_tag = ende <= point.valid_from
    if am_selben_tag:
        ende = point.valid_from + datetime.timedelta(days=1)

    werte = {"valid_until": ende}
    # Gleiche Falle wie bei der Adresse (siehe `end_party_address`):
    # `excl_contact_point_primary` schließt sich über den Gültigkeitszeitraum,
    # und [heute, morgen) überlappt [heute, ) weiterhin. Ein primärer
    # Kommunikationsweg, der keinen Tag galt, blockierte sonst den Platz für
    # den richtigen.
    if am_selben_tag and point.is_primary:
        werte["is_primary"] = False

    with business_transaction(actor_app_user_id):
        ContactPoint.objects.filter(pk=point.id).update(**werte)
    return ContactPoint.objects.get(pk=point.id)
