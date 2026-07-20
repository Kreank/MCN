"""Anruf-Durchstich: Kunde → Auftrag → Termin in einem Schritt.

Der Alltagsfall im Handwerksbetrieb: Das Telefon klingelt, der Kunde schildert
sein Problem, man vereinbart einen Termin und legt auf. Fachlich entstehen dabei
drei Entitäten (Kontakt, Auftrag, Einsatz) — im Kopf des Disponenten aber nur
eine einzige Sache: „Termin für Herrn Müller".

Dieser Service bildet genau das ab. `quick_intake` (api/projekt.py) macht schon
Person + Liegenschaft + **Vorgang** atomar, endet aber im Eingangskorb: Der
Vorgang ist die *optionale* Vorstufe, nicht der Arbeitsauftrag. Wer am Telefon
bereits einen Termin vereinbart, braucht keinen Eingangskorb, sondern einen
freigegebenen Auftrag mit Termin.

## Warum bis FREIGEGEBEN

Ein Auftrag entsteht laut Trigger zwingend als ENTWURF. Der Einsatz ließe sich
darauf zwar planen (`trg_service_job_execution_gate` greift erst beim Übergang
auf UNTERWEGS), aber der Monteur stünde am Termintag vor verschlossener Tür:
Losfahren darf er nur bei einem Auftrag in FREIGEGEBEN/IN_PLANUNG/IN_AUSFUEHRUNG.
Ein Durchstich, der planbare, aber nicht ausführbare Termine erzeugt, wäre
schlimmer als gar keiner.

`workflow.recheck_work_order_gates` verlangt für FREIGEGEBEN dreierlei:
Beauftragungsnachweis in Textform, bestätigten Verantwortungsbereich und einen
PRINCIPAL. Alle drei werden hier **erfüllt, nicht umgangen** — das Telefonat
*ist* der Nachweis (A-26 verlangt Textform, nicht Schriftform), der Anrufer *ist*
der Auftraggeber. Der Weg durch die Tore ist derselbe wie beim manuellen
Anlegen; er wird nur nicht über vier Bildschirme verteilt.

## Der zweite Ausgang: vorlegen statt freigeben

Freigeben setzt voraus, dass die Disposition die Beauftragung fachlich
entscheiden *kann*. Beim Wasserrohrbruch trifft das zu; bei „der Kunde will sein
Bad komplett saniert haben" nicht — ob der Betrieb so einen Auftrag überhaupt
annimmt, entscheidet die technische Leitung, nicht das Telefon.

Ohne einen zweiten Ausgang hätte die Disposition an dieser Stelle nur die Wahl
zwischen Freigeben (entscheidet etwas, das ihr nicht zusteht) und Liegenlassen
(der Anruf verpufft). `vorlegen=True` führt den Auftrag deshalb nach
FREIGABE_AUSSTEHEND statt nach FREIGEGEBEN — erfasst, aber bewusst nicht
entschieden.

Der Status ist kein neuer: Die Übergangstabelle kennt ENTWURF →
FREIGABE_AUSSTEHEND und verlangt dafür kein FREIGEBEN. Die DB-Tore feuern erst
ab FREIGEGEBEN, also ist der Verantwortungsbereich auf diesem Weg noch nicht
Pflicht — wer den Auftrag fachlich nicht beurteilen kann, kann meist auch die
Zuordnung Sonder-/Gemeinschaftseigentum nicht treffen. Der Entscheider ergänzt
beides und läuft beim Freigeben durch dieselben Tore wie jeder andere.

Nachweis und PRINCIPAL werden trotzdem gesetzt: Beide sind aus dem Telefonat
bekannt, und sie später nachzutragen hieße, den Entscheider Arbeit machen zu
lassen, die schon getan war.
"""

import uuid
from datetime import date

from db_core.db_context import run_business_transaction
from db_core.services import auftrag as auftrag_service
from db_core.services import identity as identity_service
from db_core.services import planung as planung_service
from db_core.services import property as property_service

# Liegenschaftstypen, bei denen der Verantwortungsbereich eindeutig ist: Beim
# Einfamilienhaus gibt es kein Gemeinschaftseigentum, an dem er sich reiben
# könnte. Bei allen anderen Typen (WEG, MFH …) ist die Zuordnung eine fachliche
# Entscheidung — die verlangen wir explizit, statt sie zu raten. Eine falsche
# Annahme hier landet später in der Rechnung beim falschen Kostenträger.
EINDEUTIGER_SCOPE = {"EINFAMILIENHAUS": "PRIVATE_UNIT"}


def _nachweis_aus_telefonat(anrufer_name, angenommen_am=None):
    """Formuliert den Beauftragungsnachweis (A-26) aus dem Telefonat.

    Textform genügt; entscheidend ist, dass nachvollziehbar bleibt, wer wann
    beauftragt hat. Ohne Namen bleibt der Eintrag trotzdem gültig (die DB prüft
    nur auf nicht-leer), verliert aber an Aussagekraft — deshalb reichen die
    Aufrufer den Namen durch.
    """
    tag = (angenommen_am or date.today()).strftime("%d.%m.%Y")
    if anrufer_name and anrufer_name.strip():
        return f"Telefonisch beauftragt am {tag} durch {anrufer_name.strip()}"
    return f"Telefonisch beauftragt am {tag}"


def _ableiten_liegenschaftsname(name, street, house_number, city):
    """property.name ist Pflicht; am Telefon fragt niemand nach einem
    Objektnamen, also aus der Adresse ableiten (Straße Hausnr, Ort)."""
    if name and name.strip():
        return name.strip()
    strasse = " ".join(
        teil.strip() for teil in (street, house_number) if teil and teil.strip()
    )
    stadt = city.strip() if city else ""
    abgeleitet = ", ".join(teil for teil in (strasse, stadt) if teil)
    return abgeleitet or (street or "").strip()


def _personenname(salutation, first_name, last_name):
    return " ".join(
        teil.strip()
        for teil in (salutation, first_name, last_name)
        if teil and teil.strip()
    )


def anruf_durchstich(
    actor_app_user_id,
    *,
    # --- Anrufer ---
    existing_party_id=None,
    salutation=None,
    first_name=None,
    last_name=None,
    phone=None,
    email=None,
    anrufer_anzeigename=None,
    # --- Liegenschaft ---
    existing_property_id=None,
    property_type=None,
    property_name=None,
    street=None,
    house_number=None,
    postal_code=None,
    city=None,
    # --- Auftrag ---
    title,
    description=None,
    priority="NORMAL",
    is_emergency=False,
    responsibility_scope=None,
    order_evidence_reference=None,
    trade_id=None,
    vorlegen=False,
    vorlage_frage=None,
    # --- Termin ---
    scheduled_start=None,
    scheduled_end=None,
    building_id=None,
    unit_id=None,
    assignee_ids=(),
    resource_ids=(),
    access_instructions=None,
    appointment_category_id=None,
):
    """Legt Kontakt (optional), Liegenschaft (optional), Auftrag und Termin an.

    Gibt `(party_id, property_id, work_order, service_job)` zurück.

    Alles läuft in EINER Transaktion; die service-internen
    `business_transaction`-Aufrufe werden zu Savepoints. Scheitert ein
    Teilschritt — etwa ein DB-Tor am Transaktionsende —, rollt der gesamte
    Durchstich zurück. Das ist hier wichtiger als anderswo: Aufträge und Einsätze
    verbrauchen GoBD-Belegnummern, und der No-Delete-Schutz lässt Waisen
    hinterher nicht mehr entfernen.

    Ohne `scheduled_start` landet der Termin bewusst im **Rückstand** (Status
    UNGEPLANT) — der Kunde will einen Termin „nächste Woche irgendwann", die
    Disposition legt ihn später ins Raster. Der Auftrag wird trotzdem
    freigegeben; die Freigabe hängt nicht am Termin.

    Mit `vorlegen=True` endet der Durchstich stattdessen in FREIGABE_AUSSTEHEND
    und `vorlage_frage` wird Pflicht (Begründung des Übergangs, sichtbar im
    Statusverlauf). Ein Termin darf auch dann schon entstehen — geplant werden
    kann er, nur losfahren darf der Monteur nicht: `trg_service_job_execution_gate`
    verlangt für UNTERWEGS einen Auftrag ab FREIGEGEBEN. Genau das ist gewollt,
    wenn am Telefon ein Wunschtermin fällt, die Annahme aber noch offen ist.
    """
    if not title or not title.strip():
        raise ValueError("Für den Auftrag ist ein Titel Pflicht.")

    if existing_party_id is None and not (
        first_name and first_name.strip() and last_name and last_name.strip()
    ):
        raise ValueError("Für einen neuen Kontakt sind Vor- und Nachname Pflicht.")

    if existing_property_id is None:
        if not (
            street
            and street.strip()
            and postal_code
            and postal_code.strip()
            and city
            and city.strip()
        ):
            raise ValueError(
                "Für eine neue Liegenschaft sind Straße, PLZ und Ort Pflicht."
            )
        if not property_type:
            raise ValueError(
                "Für eine neue Liegenschaft ist der Objekttyp Pflicht."
            )
        typ_fuer_scope = property_type
    else:
        # Bei einer bestehenden Liegenschaft kennen wir den Typ erst nach dem
        # Laden. Den Scope daraus abzuleiten würde einen zusätzlichen Query vor
        # der Transaktion kosten und wäre trotzdem nur geraten — hier bleibt es
        # bei der expliziten Angabe, sofern der Aufrufer keine macht.
        typ_fuer_scope = None

    scope = responsibility_scope or EINDEUTIGER_SCOPE.get(typ_fuer_scope)
    # Im Notfall lässt recheck_work_order_gates die Freigabe ohne bestätigte
    # Verantwortung zu (A-23, Gefahrenabwehr): Bei Wasserrohrbruch um 23 Uhr wird
    # nicht erst geklärt, ob das Rohr Gemeinschafts- oder Sondereigentum ist.
    # Diese Ausnahme hier zu ignorieren hieße, ausgerechnet den dringendsten Fall
    # an einer Formalie scheitern zu lassen. Ist der Bereich bekannt, wird er
    # trotzdem gesetzt — die Ausnahme erlaubt das Weglassen, sie verbietet die
    # Angabe nicht.
    # Beim Vorlegen entfällt die Pflicht: Der Auftrag geht nach
    # FREIGABE_AUSSTEHEND, und dort feuert recheck_work_order_gates noch nicht.
    # Den Bereich hier trotzdem zu verlangen hieße, den Vorlege-Weg genau in dem
    # Fall zu versperren, für den es ihn gibt — wer die Beauftragung fachlich
    # nicht beurteilen kann, kann sie meist auch nicht zuordnen.
    if scope is None and not is_emergency and not vorlegen:
        raise ValueError(
            "Der Verantwortungsbereich lässt sich nicht ableiten und ist für die "
            "Freigabe Pflicht (Sondereigentum, Gemeinschaftseigentum oder gemischt)."
        )

    # Die Frage ist beim Vorlegen der eigentliche Inhalt: Ein Auftrag, der ohne
    # sie in der Entscheider-Liste landet, zwingt den Entscheider, den Fall aus
    # Titel und Beschreibung zu rekonstruieren — dann hätte die Disposition ihn
    # auch gleich liegenlassen können.
    if vorlegen and not (vorlage_frage and vorlage_frage.strip()):
        raise ValueError(
            "Zum Vorlegen gehört die Frage an den Entscheider — was soll "
            "entschieden werden?"
        )

    anzeigename = anrufer_anzeigename or _personenname(
        salutation, first_name, last_name
    )
    nachweis = order_evidence_reference or _nachweis_aus_telefonat(anzeigename)

    def _durchstich():
        if existing_party_id is None:
            party = identity_service.create_person(
                actor_app_user_id,
                first_name,
                last_name,
                salutation=salutation,
            )
            if phone and phone.strip():
                identity_service.add_contact_point(
                    actor_app_user_id,
                    party.id,
                    contact_type="PHONE",
                    value=phone,
                    is_primary=True,
                )
            if email and email.strip():
                identity_service.add_contact_point(
                    actor_app_user_id,
                    party.id,
                    contact_type="EMAIL",
                    value=email,
                    is_primary=True,
                )
            party_id = party.id
        else:
            # Keine neuen Kontaktwege am fremden Kontakt: Wer anruft, ändert
            # damit nicht seine hinterlegte Nummer. Die Verwendbarkeit prüft
            # add_work_order_party (ensure_party_usable → 422).
            party_id = existing_party_id

        if existing_property_id is None:
            prop = property_service.create_property(
                actor_app_user_id,
                name=_ableiten_liegenschaftsname(
                    property_name, street, house_number, city
                ),
                property_type=property_type,
                street=street,
                house_number=house_number,
                postal_code=postal_code,
                city=city,
            )
            # Bei einer NEUEN Liegenschaft ist der Anrufer deren Eigentümer —
            # dieselbe Annahme wie in quick_intake. Bei einer bestehenden gilt
            # sie bewusst nicht: Wer ein bereits erfasstes Objekt meldet, ist
            # deshalb noch lange nicht sein Eigentümer (Mieter, Verwalter).
            property_service.add_party_role(
                actor_app_user_id,
                property_id=prop.id,
                party_id=party_id,
                role="PROPERTY_OWNER",
                valid_from=date.today(),
            )
            property_id = prop.id
        else:
            property_id = existing_property_id

        # `trade_id` nur hier setzen: Der Einsatz erbt das Gewerk vom Auftrag,
        # wenn ihm keines mitgegeben wird (einsatz.py). Es doppelt zu reichen
        # brächte nur die Möglichkeit, dass beide auseinanderlaufen.
        order = auftrag_service.create_work_order(
            actor_app_user_id,
            property_id=property_id,
            title=title,
            description=description,
            priority=priority,
            is_emergency=is_emergency,
            trade_id=trade_id,
        )

        # Reihenfolge ist nicht beliebig: Die drei Tore aus
        # recheck_work_order_gates müssen ALLE erfüllt sein, bevor der
        # Statuswechsel kommt. Der Trigger ist DEFERRABLE und prüft erst am
        # Transaktionsende — die Reihenfolge innerhalb der Transaktion wäre ihm
        # also egal. Sie steht hier trotzdem so, weil ein späterer Leser sonst
        # annehmen müsste, ENTWURF → FREIGEGEBEN ginge ohne Vorbedingungen.
        auftrag_service.add_work_order_party(
            actor_app_user_id,
            work_order_id=order.id,
            party_id=party_id,
            role="PRINCIPAL",
            is_primary=True,
            source="MANUAL",
        )
        auftrag_service.set_order_evidence(
            actor_app_user_id, work_order_id=order.id, reference=nachweis
        )
        if scope is not None:
            auftrag_service.confirm_responsibility(
                actor_app_user_id, work_order_id=order.id, scope=scope
            )
        # Die Frage wandert als `reason` in den Statusverlauf statt in ein
        # eigenes Feld: Sie gehört zu genau diesem Übergang („warum liegt das
        # hier?"), und der Statusverlauf steht in der Detailansicht ohnehin schon
        # da, wo der Entscheider hinschaut. Ein separates Feld hätte eine
        # Migration gekostet und dieselbe Information an einen zweiten Ort gelegt.
        if vorlegen:
            order = auftrag_service.advance_status(
                actor_app_user_id,
                work_order_id=order.id,
                to_status="FREIGABE_AUSSTEHEND",
                reason=vorlage_frage.strip(),
            )
        else:
            order = auftrag_service.advance_status(
                actor_app_user_id, work_order_id=order.id, to_status="FREIGEGEBEN"
            )

        job = planung_service.create_termin(
            actor_app_user_id,
            work_order_id=order.id,
            property_id=property_id,
            building_id=building_id,
            unit_id=unit_id,
            scheduled_start=scheduled_start,
            scheduled_end=scheduled_end,
            on_site_contact_party_id=party_id,
            access_instructions=access_instructions,
            appointment_category_id=appointment_category_id,
            assignee_ids=assignee_ids,
            resource_ids=resource_ids,
        )
        return party_id, property_id, order, job

    return run_business_transaction(actor_app_user_id, _durchstich)
