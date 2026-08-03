"""Firmeneinstellungen-Service: Firmenprofil, Niederlassungen, Gewerke und die
Pflege der Mahnstufen.

Alle Writes laufen über business_transaction (Benutzerkontext/Audit). Die
Fachtabellen liegen im Schema `company` (0023) bzw. `invoicing.dunning_level`
(0025). Kein Löschen — Niederlassungen/Gewerke werden deaktiviert.

Mahnstufen-Lücken (B-22): Der DB-Trigger `invoicing.check_dunning_notice`
erzwingt je Rechnung eine lückenlos aufsteigende Stufenfolge. Das `active`-Flag
ist eine Konfig-Ebene. Damit die Konfiguration immer ausführbar bleibt, erzwingt
`update_dunning_level`, dass die aktiven Stufen einen lückenlosen Präfix {1..k}
bilden — eine mittlere Stufe zu deaktivieren, während eine höhere aktiv bleibt,
wird verboten (sonst wäre ein Sprung 1 -> 3 nötig, den der Trigger nie zulässt).
"""
import calendar
import hashlib
import re
import uuid
from datetime import time
from pathlib import PurePosixPath

from db_core import storage as storage_module
from db_core.db_context import business_transaction
from db_core.models import (
    AcquisitionSource,
    Branch,
    CompanyProfile,
    DunningLevel,
    File,
    Invoice,
    MailAccount,
    Party,
    Project,
    Property,
    Quote,
    Trade,
)
from db_core.services import vier_augen

# --- Firmenprofil (Singleton) ----------------------------------------------

# Pflegbare Profilfelder (Whitelist gegen willkürliche Attribut-Injektion).
_PROFILE_FIELDS = (
    "company_name", "legal_form", "street", "postal_code", "city", "country",
    "state_code", "phone", "email", "web", "tax_number", "vat_id",
    "commercial_register", "bank_name", "iban", "bic", "managing_director",
    "managing_director_title", "default_language", "logo_file_id",
    # DATEV-Export-Konfiguration (0051)
    "datev_consultant_number", "datev_client_number", "datev_chart_of_accounts",
    "datev_account_length", "datev_fiscal_year_start_month",
    "datev_debtor_account", "datev_revenue_account_full",
    "datev_revenue_account_reduced", "datev_revenue_account_free",
    "datev_revenue_account_reverse",
    # Abschlags-Kontierung (0063)
    "datev_advance_mode", "datev_advance_account_full",
    "datev_advance_account_reduced", "datev_advance_account_free",
    "datev_advance_account_reverse",
    # Resturlaubs-Verfall (0072). NULL/NULL = kein Verfall (Default).
    "vacation_carryover_expiry_month", "vacation_carryover_expiry_day",
    # Arbeitszeitfenster (0148). Grundlage der Auslastung auf der Plantafel.
    "work_start", "work_end", "break_minutes",
)

# Konto-Override-Felder: reine Ziffernfolgen (Sach-/Personenkonten). NULL = der
# Service verwendet den SKR-Standard.
_DATEV_ACCOUNT_FIELDS = (
    "datev_debtor_account", "datev_revenue_account_full",
    "datev_revenue_account_reduced", "datev_revenue_account_free",
    "datev_revenue_account_reverse",
    "datev_advance_account_full", "datev_advance_account_reduced",
    "datev_advance_account_free", "datev_advance_account_reverse",
)

# Abschlags-Buchungsmodus (0063): ERLOES = Teilleistung (Default, Bestands-
# verhalten), ANZAHLUNG = Verbindlichkeitskonto „Erhaltene Anzahlungen".
DATEV_ADVANCE_MODES = ("ERLOES", "ANZAHLUNG")

# Bankdaten sind Vier-Augen-pflichtig (security.four_eyes_action 'BANKDATEN'):
# eine ÄNDERUNG an einem bestehenden Profil wird nicht direkt geschrieben,
# sondern als Freigabeantrag angelegt; erst die Genehmigung wendet sie an.
_BANK_FIELDS = ("bank_name", "iban", "bic")


def get_company_profile():
    """Das Firmenprofil oder None, wenn noch keins gepflegt ist (Singleton)."""
    return CompanyProfile.objects.first()


def _clean(value):
    """Leerstrings zu None normalisieren, Strings trimmen."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _validate_datev(values):
    """Prüft die DATEV-Konfigurationsfelder (spiegelt die DB-CHECKs + Fachregeln).

    Nur gesetzte Felder werden geprüft; alle sind einzeln nullbar. Ohne diese
    Vorabprüfung schlüge z. B. ein SKR-Tippfehler oder eine nichtnumerische
    Beraternummer erst als DataError/500 durch statt als klare 422-Meldung.
    `values` ist bereits _clean-normalisiert (leer → None). Der Kontenrahmen wird
    hier großgeschrieben (in place), damit 'skr03' zu 'SKR03' wird.
    """
    if values.get("datev_chart_of_accounts"):
        skr = values["datev_chart_of_accounts"].upper()
        if skr not in ("SKR03", "SKR04"):
            raise ValueError("Kontenrahmen muss SKR03 oder SKR04 sein.")
        values["datev_chart_of_accounts"] = skr

    # datev_advance_mode ist NOT NULL (DB-Default 'ERLOES'): ein geleertes Feld
    # bedeutet „unverändert", NIE NULL — sonst 500 statt 422.
    if "datev_advance_mode" in values:
        if values["datev_advance_mode"] is None:
            del values["datev_advance_mode"]
        else:
            modus = values["datev_advance_mode"].upper()
            if modus not in DATEV_ADVANCE_MODES:
                raise ValueError(
                    "Buchung der Abschlagsrechnungen muss ERLOES oder ANZAHLUNG sein."
                )
            values["datev_advance_mode"] = modus

    berater = values.get("datev_consultant_number")
    if berater is not None:
        if not re.fullmatch(r"[0-9]{4,7}", berater) or not (1001 <= int(berater) <= 9_999_999):
            raise ValueError("Beraternummer muss zwischen 1001 und 9999999 liegen.")
    mandant = values.get("datev_client_number")
    if mandant is not None:
        if not re.fullmatch(r"[0-9]{1,5}", mandant) or not (1 <= int(mandant) <= 99_999):
            raise ValueError("Mandantennummer muss zwischen 1 und 99999 liegen.")

    length = values.get("datev_account_length")
    if length is not None and not (4 <= int(length) <= 8):
        raise ValueError("Sachkontenlänge muss zwischen 4 und 8 liegen.")
    month = values.get("datev_fiscal_year_start_month")
    if month is not None and not (1 <= int(month) <= 12):
        raise ValueError("Wirtschaftsjahresbeginn muss ein Monat (1–12) sein.")

    for feld in _DATEV_ACCOUNT_FIELDS:
        konto = values.get(feld)
        if konto is not None and not re.fullmatch(r"[0-9]{3,9}", konto):
            raise ValueError(
                "Kontonummern dürfen nur aus Ziffern bestehen (3–9 Stellen)."
            )


def _validate_urlaubsverfall(values, profile):
    """Verfallstag des Resturlaubs-Übertrags: beide Felder oder keins (DB-CHECK).

    Geprüft wird der **Ergebniszustand** (Bestand + Änderung), nicht nur das, was
    gesendet wurde: Wer allein den Monat leert, hinterließe sonst einen Tag ohne
    Monat — der DB-CHECK schlüge als 500 durch statt als klare 422.

    NULL/NULL ist der Default und heißt **kein Verfall**. Das ist kein Versäumnis,
    sondern die Entscheidung: Es wird nichts weggerechnet, was der Betrieb nicht
    ausdrücklich eingestellt hat (Begründung im Kopf von Migration 0072).
    """
    monat_key = "vacation_carryover_expiry_month"
    tag_key = "vacation_carryover_expiry_day"
    if monat_key not in values and tag_key not in values:
        return
    monat = values[monat_key] if monat_key in values else (
        getattr(profile, monat_key, None) if profile else None
    )
    tag = values[tag_key] if tag_key in values else (
        getattr(profile, tag_key, None) if profile else None
    )
    if (monat is None) != (tag is None):
        raise ValueError(
            "Der Verfallstag des Resturlaubs braucht Tag UND Monat — oder keins "
            "von beidem (kein Verfall)."
        )
    if monat is None:
        return
    monat, tag = int(monat), int(tag)
    if not 1 <= monat <= 12:
        raise ValueError("Der Verfallsmonat muss zwischen 1 und 12 liegen.")
    letzter = calendar.monthrange(2024, monat)[1]  # Schaltjahr: 29.02. erlaubt
    if not 1 <= tag <= letzter:
        raise ValueError(
            f"Der Verfallstag muss zwischen 1 und {letzter} liegen (Monat {monat})."
        )
    values[monat_key] = monat
    values[tag_key] = tag


def _validate_arbeitszeit(values, profile):
    """Arbeitszeitfenster: Beginn vor Feierabend, Pause passt hinein (DB-CHECKs).

    Wie beim Urlaubsverfall wird der **Ergebniszustand** geprüft, nicht nur das
    Gesendete: Wer allein den Feierabend vorzieht, könnte ihn sonst vor den
    bestehenden Arbeitsbeginn schieben, und der CHECK schlüge als 500 durch.

    Die Felder sind NOT NULL mit Default — ein geleertes Feld heißt hier also
    „unverändert", nie NULL (dieselbe Regel wie bei `datev_advance_mode`).
    """
    keys = ("work_start", "work_end", "break_minutes")
    if not any(k in values for k in keys):
        return
    for k in keys:
        if k in values and values[k] is None:
            del values[k]
    if not any(k in values for k in keys):
        return
    # Pydantic nimmt „08:00:00+02:00" klaglos als *aware* time entgegen. Der
    # Vergleich gegen den naiven Bestandswert wuerfe dann TypeError — und der
    # kaeme als 500 heraus, in einem Endpunkt, dessen ganzer Zweck 422 ist.
    # Der Betrieb hat EINE Ortszeit; ein Zeitzonenversatz an der Stechuhr wäre
    # ohnehin bedeutungslos.
    for k in ("work_start", "work_end"):
        if k in values and getattr(values[k], "tzinfo", None) is not None:
            raise ValueError(
                "Arbeitsbeginn und Feierabend sind Ortszeiten — ohne Zeitzonenangabe."
            )

    def jetzt(k, vorgabe):
        if k in values:
            return values[k]
        return getattr(profile, k, vorgabe) if profile else vorgabe

    von, bis = jetzt("work_start", time(7, 0)), jetzt("work_end", time(16, 0))
    if von >= bis:
        raise ValueError("Der Arbeitsbeginn muss vor dem Feierabend liegen.")
    pause = int(jetzt("break_minutes", 60))
    spanne = (bis.hour * 60 + bis.minute) - (von.hour * 60 + von.minute)
    if pause < 0:
        raise ValueError("Die Pause kann nicht negativ sein.")
    if pause >= spanne:
        raise ValueError(
            f"Die Pause ({pause} Min.) muss kürzer sein als der Arbeitstag "
            f"({spanne} Min.) — sonst bliebe keine Arbeitszeit übrig."
        )
    if "break_minutes" in values:
        values["break_minutes"] = pause


def _abschlagsmodus_wechsel_pruefen(profile, neuer_modus):
    """Der DATEV-Abschlagsmodus darf nur an einem SAUBEREN SCHNITT wechseln.

    Der Modus wirkt zum Zeitpunkt des **Exports**, nicht des Belegs. Wird
    umgestellt, während veröffentlichte Abschläge noch auf ihre Schlussrechnung
    warten, löste die spätere Schlussrechnung eine Anzahlung auf, die nie als
    Anzahlung gebucht wurde (bzw. umgekehrt) — auf dem Anzahlungskonto bliebe ein
    Saldo stehen, den niemand mehr zuordnen kann. Ein bloßer UI-Warnhinweis ist
    dafür die schwächste Form: der Server kennt die Bedingung, also setzt er sie
    durch (→ 422).

    Erlaubt bleibt jedes Speichern OHNE Moduswechsel (auch mit unverändert
    mitgesendetem Modus) — sonst wäre das Firmenprofil nicht mehr pflegbar,
    solange ein Abschlag offen ist.
    """
    if neuer_modus is None or neuer_modus == profile.datev_advance_mode:
        return
    # Import lokal: `beleg` zieht die halbe Belegwelt nach (Zirkelbezug vermeiden).
    from db_core.services import beleg as beleg_service

    offen = beleg_service.offene_abschlaege_gesamt()
    if not offen:
        return
    nummern = ", ".join(i.invoice_number or "ENTWURF" for i in offen[:5])
    if len(offen) > 5:
        nummern += f" … (+{len(offen) - 5})"
    raise ValueError(
        f"Die Buchung der Abschlagsrechnungen lässt sich derzeit nicht umstellen: "
        f"{len(offen)} veröffentlichte Abschlags-/Teilrechnung(en) warten noch auf "
        f"ihre Schlussrechnung ({nummern}). Deren Schlussrechnung würde sonst eine "
        "Anzahlung auflösen, die nie als Anzahlung gebucht wurde — auf dem "
        "Anzahlungskonto bliebe ein Saldo stehen. Stellen Sie erst die offenen "
        "Schlussrechnungen, dann den Modus um."
    )


def update_company_profile(actor_app_user_id, **fields):
    """Legt das Firmenprofil an oder aktualisiert es (Upsert des Singletons).

    Gibt `(profile, pending_approval)` zurück: `pending_approval` ist der
    Freigabeantrag (security.approval_request), falls die Änderung Bankdaten
    (IBAN/BIC/Bankname) eines BESTEHENDEN Profils betrifft — diese werden NICHT
    direkt geschrieben, sondern in einen Vier-Augen-Antrag (BANKDATEN) gelegt und
    erst durch dessen Genehmigung angewandt. Alle übrigen Felder werden sofort
    übernommen. Beim erstmaligen Anlegen gibt es noch keine schützenswerten
    Bestandsdaten; dort werden Bankdaten direkt gesetzt (Bootstrapping).

    Nur Felder aus der Whitelist werden übernommen. `company_name` ist beim
    Anlegen Pflicht und darf nicht leer werden.
    """
    unknown = set(fields) - set(_PROFILE_FIELDS)
    if unknown:
        raise ValueError(f"Unbekannte Profilfelder: {', '.join(sorted(unknown))}")

    values = {k: _clean(v) for k, v in fields.items()}
    # Zwei-Zeichen-Codes normalisieren (Land groß, Sprache klein).
    if values.get("country"):
        values["country"] = values["country"].upper()
    if values.get("default_language"):
        values["default_language"] = values["default_language"].lower()
    # country/default_language sind NOT NULL: ein geleertes Feld bedeutet
    # „unverändert" (bzw. DB-Default beim Anlegen), NIE NULL — sonst 500 statt 422.
    for nn in ("country", "default_language"):
        if nn in values and values[nn] is None:
            del values[nn]
    if values.get("country") and not re.fullmatch(r"[A-Z]{2}", values["country"]):
        raise ValueError("Land muss ein zweistelliges ISO-Kürzel sein (z. B. DE).")
    if values.get("default_language") and not re.fullmatch(r"[a-z]{2}", values["default_language"]):
        raise ValueError("Sprache muss ein zweistelliges Kürzel sein (z. B. de).")

    _validate_datev(values)

    profile = get_company_profile()
    _validate_urlaubsverfall(values, profile)
    _validate_arbeitszeit(values, profile)
    if profile is None:
        if not values.get("company_name"):
            raise ValueError("Firmenname ist erforderlich.")
        with business_transaction(actor_app_user_id):
            profile = CompanyProfile.objects.create(
                id=uuid.uuid4(), is_singleton=True,
                **{k: values.get(k) for k in _PROFILE_FIELDS if k in values},
            )
        return profile, None

    if "company_name" in values and not values["company_name"]:
        raise ValueError("Firmenname darf nicht leer sein.")

    _abschlagsmodus_wechsel_pruefen(profile, values.get("datev_advance_mode"))

    # Bankdaten heraustrennen: nur die tatsächlich geänderten Felder lösen einen
    # Vier-Augen-Antrag aus (unveränderte Werte nicht).
    bank_changes = {
        k: values[k]
        for k in _BANK_FIELDS
        if k in values and values[k] != getattr(profile, k)
    }
    direct = {k: v for k, v in values.items() if k not in bank_changes}

    if direct:
        for key, val in direct.items():
            setattr(profile, key, val)
        with business_transaction(actor_app_user_id):
            profile.save(update_fields=list(direct) + ["updated_at"])
        profile.refresh_from_db()

    pending = None
    if bank_changes:
        pending = vier_augen.request_approval(
            actor_app_user_id,
            action_code="BANKDATEN",
            payload=bank_changes,
            target_table="company.company_profile",
            target_id=profile.id,
            reason="Änderung der Firmen-Bankverbindung",
        )
    return profile, pending


# --- Firmenlogo (content.file, referenziert über company_profile.logo_file_id) ---
# Das Logo erscheint im Kopf der Beleg-PDFs. Es liegt als ganz normale
# content.file im Objektspeicher (dieselbe Infrastruktur wie die Datei-Ablage:
# Storage + File-Model + SHA-256-Dedup), wird aber NICHT über content.file_link
# angehängt — es gibt keine company-Zielspalte —, sondern direkt über
# company_profile.logo_file_id referenziert. Entfernen = logo_file_id auf NULL;
# die Datei selbst bleibt (content.file ist unveränderlich, GoBD).

# Nur Rasterformate, die fpdf2 in ein PDF einbetten kann: PNG und JPEG. Kein
# SVG (kann Skripte ausführen), kein PDF, kein GIF/WebP.
LOGO_MAX_BYTES = 2 * 1024 * 1024  # 2 MB


class LogoFehler(ValueError):
    """Der Logo-Upload ist fachlich unzulässig (→ 422)."""


def _logo_mime(inhalt):
    """Bildtyp aus den Magic Bytes (nur fpdf2-einbettbare Raster: PNG/JPEG).

    Der vom Client gemeldete Content-Type wird bewusst nicht geglaubt (wie in der
    Datei-Ablage): der Typ ergibt sich aus dem Inhalt. Alles außer PNG/JPEG wird
    abgelehnt — SVG kann Skripte ausführen, und fpdf2 bettet ohnehin nur Raster
    ein.
    """
    if inhalt[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if inhalt[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    raise LogoFehler(
        "Nur PNG- oder JPEG-Bilder sind als Logo zulässig "
        "(kein SVG, PDF oder anderes Format)."
    )


def _logo_dateiname(dateiname):
    """Anzeigename des Logos (nur Basisname, ohne Pfadanteile, begrenzt)."""
    name = (dateiname or "").strip().replace("\\", "/")
    basis = PurePosixPath(name).name.replace("\x00", "")
    return basis[:255] or "firmenlogo"


def set_company_logo(actor_app_user_id, *, dateiname, inhalt):
    """Legt das Firmenlogo als content.file ab und setzt logo_file_id.

    Nur PNG/JPEG (fpdf2-einbettbar) und höchstens 2 MB; sonst LogoFehler (→ 422).
    Ein bereits vorhandenes Logo wird ersetzt (logo_file_id zeigt auf die neue
    Datei; die alte content.file bleibt bestehen — unveränderlich/GoBD). Gibt das
    aktualisierte Firmenprofil zurück.

    Das Profil muss existieren: das Logo hängt an ihm. Ohne Profil zuerst die
    Firmendaten anlegen.
    """
    profile = get_company_profile()
    if profile is None:
        raise LogoFehler(
            "Es ist noch kein Firmenprofil gepflegt. Bitte zuerst die "
            "Firmendaten hinterlegen, dann das Logo hochladen."
        )
    if not inhalt:
        raise LogoFehler("Die Bilddatei ist leer.")
    if len(inhalt) > LOGO_MAX_BYTES:
        raise LogoFehler(
            f"Das Logo ist zu groß ({len(inhalt) / 1_048_576:.1f} MB). "
            f"Erlaubt sind {LOGO_MAX_BYTES // 1_048_576} MB."
        )
    mime = _logo_mime(inhalt)

    # Denselben Inhalt (SHA-256) nicht erneut ablegen — nur referenzieren.
    digest = hashlib.sha256(inhalt).hexdigest()
    vorhanden = File.objects.filter(sha256=digest, size_bytes=len(inhalt)).first()
    if vorhanden is None:
        storage_key = f"logo/{uuid.uuid4()}"
        try:
            storage_module.get_storage().put_object(
                storage_key, inhalt, content_type=mime
            )
        except storage_module.StorageError as exc:
            raise LogoFehler(f"Das Logo konnte nicht gespeichert werden: {exc}")
        with business_transaction(actor_app_user_id):
            datei = File.objects.create(
                id=uuid.uuid4(),
                storage_key=storage_key,
                original_filename=_logo_dateiname(dateiname),
                mime_type=mime,
                size_bytes=len(inhalt),
                sha256=digest,
                media_metadata={},
                uploaded_by_id=actor_app_user_id,
            )
    else:
        datei = vorhanden

    with business_transaction(actor_app_user_id):
        profile.logo_file_id = datei.id
        profile.save(update_fields=["logo_file_id", "updated_at"])
    profile.refresh_from_db()
    return profile


def remove_company_logo(actor_app_user_id):
    """Entfernt das Firmenlogo (logo_file_id → NULL); die Datei selbst bleibt.

    Idempotent: ist kein Logo gesetzt, passiert nichts. Gibt das Firmenprofil
    zurück.
    """
    profile = get_company_profile()
    if profile is None:
        raise LogoFehler("Es ist noch kein Firmenprofil gepflegt.")
    if profile.logo_file_id is not None:
        with business_transaction(actor_app_user_id):
            profile.logo_file_id = None
            profile.save(update_fields=["logo_file_id", "updated_at"])
        profile.refresh_from_db()
    return profile


def company_logo_inhalt():
    """(File, Bytes) des Firmenlogos aus dem Objektspeicher.

    Wirft LogoFehler, wenn kein Logo gesetzt ist, der Steckbrief fehlt oder der
    Objektspeicher das Objekt gerade nicht liefert (die API übersetzt das in 404).
    """
    profile = get_company_profile()
    if profile is None or profile.logo_file_id is None:
        raise LogoFehler("Es ist kein Firmenlogo gesetzt.")
    datei = File.objects.filter(id=profile.logo_file_id).first()
    if datei is None:
        raise LogoFehler("Der Logo-Datensatz fehlt.")
    try:
        return datei, storage_module.get_storage().get_object(datei.storage_key)
    except storage_module.StorageError as exc:
        raise LogoFehler(f"Das Logo ist derzeit nicht abrufbar: {exc}")


# --- Niederlassungen --------------------------------------------------------

def list_branches(*, include_inactive=True):
    qs = Branch.objects.all()
    if not include_inactive:
        qs = qs.filter(active=True)
    return qs.order_by("name", "id")


def create_branch(actor_app_user_id, *, name, **fields):
    name = _clean(name)
    if not name:
        raise ValueError("Name der Niederlassung ist erforderlich.")
    allowed = ("street", "postal_code", "city", "country", "phone", "email")
    vals = {k: _clean(fields.get(k)) for k in allowed if k in fields}
    if vals.get("country"):
        vals["country"] = vals["country"].upper()
        _assert_country(vals["country"])
    else:
        vals.pop("country", None)  # NOT NULL → DB-Default 'DE'
    with business_transaction(actor_app_user_id):
        branch = Branch.objects.create(id=uuid.uuid4(), name=name, **vals)
    return branch


def update_branch(actor_app_user_id, *, branch_id, **fields):
    branch = Branch.objects.filter(id=branch_id).first()
    if branch is None:
        raise ValueError("Niederlassung nicht gefunden.")
    allowed = ("name", "street", "postal_code", "city", "country", "phone",
               "email", "active")
    unknown = set(fields) - set(allowed)
    if unknown:
        raise ValueError(f"Unbekannte Felder: {', '.join(sorted(unknown))}")
    changed = []
    for key in allowed:
        if key not in fields:
            continue
        val = fields[key]
        if key == "active":
            val = bool(val)
        else:
            val = _clean(val)
            if key == "name" and not val:
                raise ValueError("Name der Niederlassung darf nicht leer sein.")
            if key == "country":
                if not val:
                    continue  # NOT NULL: leeres Land bleibt unverändert
                val = val.upper()
                _assert_country(val)
        setattr(branch, key, val)
        changed.append(key)
    if changed:
        with business_transaction(actor_app_user_id):
            branch.save(update_fields=changed + ["updated_at"])
        branch.refresh_from_db()
    return branch


# --- Gewerk-Katalog ---------------------------------------------------------

def list_trades(*, include_inactive=True):
    qs = Trade.objects.all()
    if not include_inactive:
        qs = qs.filter(active=True)
    return qs.order_by("sort_order", "label", "id")


def _assert_country(value):
    """Spiegelt den DB-CHECK. Ohne das schlüge ein 'USA' als DataError durch (500)."""
    if value and not re.fullmatch(r"[A-Z]{2}", value):
        raise ValueError("Land muss ein zweistelliges ISO-Kürzel sein (z. B. DE).")


def create_trade(actor_app_user_id, *, code, label, sort_order=0):
    code = (_clean(code) or "").upper()
    label = _clean(label)
    if not code:
        raise ValueError("Gewerk-Code ist erforderlich.")
    # Spiegelt den DB-CHECK trade_code_check; sonst 500 statt 422. Der Code ist
    # zugleich das Kürzel in der Auftragsnummer (AU-HZG-26-0142) — deshalb muss
    # er mit einem Buchstaben beginnen, sonst wäre er dort nicht mehr vom
    # Jahresteil zu unterscheiden (Migration 0120).
    if not re.fullmatch(r"[A-Z][A-Z0-9_]+", code):
        raise ValueError(
            "Gewerk-Code muss mit einem Buchstaben beginnen und darf nur A–Z, "
            "0–9 und _ enthalten (mindestens 2 Zeichen)."
        )
    if not label:
        raise ValueError("Gewerk-Bezeichnung ist erforderlich.")
    if Trade.objects.filter(code=code).exists():
        raise ValueError(f"Gewerk-Code '{code}' ist bereits vergeben.")
    with business_transaction(actor_app_user_id):
        trade = Trade.objects.create(
            id=uuid.uuid4(), code=code, label=label, sort_order=sort_order or 0,
        )
    return trade


def update_trade(actor_app_user_id, *, trade_id, **fields):
    trade = Trade.objects.filter(id=trade_id).first()
    if trade is None:
        raise ValueError("Gewerk nicht gefunden.")
    allowed = ("label", "active", "sort_order")
    unknown = set(fields) - set(allowed)
    if unknown:
        raise ValueError(f"Unbekannte Felder: {', '.join(sorted(unknown))}")
    changed = []
    for key in allowed:
        if key not in fields:
            continue
        val = fields[key]
        if key == "active":
            val = bool(val)
        elif key == "label":
            val = _clean(val)
            if not val:
                raise ValueError("Gewerk-Bezeichnung darf nicht leer sein.")
        elif key == "sort_order":
            val = int(val or 0)
        setattr(trade, key, val)
        changed.append(key)
    if changed:
        with business_transaction(actor_app_user_id):
            trade.save(update_fields=changed + ["updated_at"])
        trade.refresh_from_db()
    return trade


# --- Akquisekanäle / Quellen (company.acquisition_source, 0049) --------------

def list_acquisition_sources(*, include_inactive=True):
    qs = AcquisitionSource.objects.all()
    if not include_inactive:
        qs = qs.filter(active=True)
    return qs.order_by("sort_order", "label", "id")


def create_acquisition_source(actor_app_user_id, *, code, label, sort_order=0):
    code = (_clean(code) or "").upper()
    label = _clean(label)
    if not code:
        raise ValueError("Kanal-Code ist erforderlich.")
    # Spiegelt den DB-CHECK (^[A-Z0-9_]{2,}$); sonst 500 statt 422.
    if not re.fullmatch(r"[A-Z0-9_]{2,}", code):
        raise ValueError(
            "Kanal-Code darf nur A–Z, 0–9 und _ enthalten (mindestens 2 Zeichen)."
        )
    if not label:
        raise ValueError("Kanal-Bezeichnung ist erforderlich.")
    if AcquisitionSource.objects.filter(code=code).exists():
        raise ValueError(f"Kanal-Code '{code}' ist bereits vergeben.")
    with business_transaction(actor_app_user_id):
        source = AcquisitionSource.objects.create(
            id=uuid.uuid4(), code=code, label=label, sort_order=sort_order or 0,
        )
    return source


def update_acquisition_source(actor_app_user_id, *, source_id, **fields):
    source = AcquisitionSource.objects.filter(id=source_id).first()
    if source is None:
        raise ValueError("Akquisekanal nicht gefunden.")
    allowed = ("label", "active", "sort_order")
    unknown = set(fields) - set(allowed)
    if unknown:
        raise ValueError(f"Unbekannte Felder: {', '.join(sorted(unknown))}")
    changed = []
    for key in allowed:
        if key not in fields:
            continue
        val = fields[key]
        if key == "active":
            val = bool(val)
        elif key == "label":
            val = _clean(val)
            if not val:
                raise ValueError("Kanal-Bezeichnung darf nicht leer sein.")
        elif key == "sort_order":
            val = int(val or 0)
        setattr(source, key, val)
        changed.append(key)
    if changed:
        with business_transaction(actor_app_user_id):
            source.save(update_fields=changed + ["updated_at"])
        source.refresh_from_db()
    return source


# --- Mahnstufen -------------------------------------------------------------

def list_dunning_levels():
    """Alle Mahnstufen aufsteigend (inkl. deaktivierter)."""
    return DunningLevel.objects.order_by("level")


def _assert_active_prefix(projected):
    """Erzwingt: die aktiven Stufen bilden einen lückenlosen Präfix {1..k}.

    `projected` ist ein dict {level: active}. Leere aktive Menge (Mahnwesen aus)
    ist erlaubt.
    """
    active = sorted(lvl for lvl, is_active in projected.items() if is_active)
    if active and active != list(range(1, len(active) + 1)):
        raise ValueError(
            "Mahnstufen müssen lückenlos ab Stufe 1 aktiv sein "
            f"(aktiv wäre: {', '.join(map(str, active))}). Deaktivieren Sie "
            "zuerst die höheren Stufen, bevor eine mittlere Stufe deaktiviert wird."
        )


def update_dunning_level(actor_app_user_id, *, level, label=None,
                         days_after_due=None, active=None):
    """Pflegt Bezeichnung, Frist und Aktivierung einer Mahnstufe.

    `fee`/`interest_note` werden bewusst NICHT verändert (STB-Vorbehalt B-22).
    Die Aktivierung wird gegen die Präfix-Regel geprüft (siehe Modul-Docstring).
    """
    row = DunningLevel.objects.filter(level=level).first()
    if row is None:
        raise ValueError(f"Mahnstufe {level} existiert nicht.")

    changed = []
    if label is not None:
        label = _clean(label)
        if not label:
            raise ValueError("Bezeichnung der Mahnstufe darf nicht leer sein.")
        row.label = label
        changed.append("label")
    if days_after_due is not None:
        days = int(days_after_due)
        if days < 0:
            raise ValueError("Tage nach Fälligkeit dürfen nicht negativ sein.")
        row.days_after_due = days
        changed.append("days_after_due")
    if active is not None:
        active = bool(active)
        # Präfix-Regel gegen den projizierten Gesamtzustand prüfen.
        projected = {
            lv.level: (active if lv.level == level else lv.active)
            for lv in DunningLevel.objects.all()
        }
        _assert_active_prefix(projected)
        row.active = active
        changed.append("active")

    if changed:
        with business_transaction(actor_app_user_id):
            row.save(update_fields=changed + ["updated_at"])
        row.refresh_from_db()
    return row


# --- Onboarding / Erste Schritte -------------------------------------------

def onboarding_status():
    """Setup-Fortschritt eines frischen Mandanten als Flags (rein lesend).

    Jedes Flag ist ein „ist mindestens einmal passiert"-Boolean — kein Zählen,
    keine fremden Daten, nur ob der jeweilige Meilenstein erreicht ist. Dient der
    Erste-Schritte-Checkliste auf der Übersicht; die eigentliche Rechteprüfung je
    Zielbereich erfolgt dort beim Navigieren.
    """
    profile = get_company_profile()
    return {
        "firmenprofil": bool(profile and profile.company_name),
        "logo": bool(profile and profile.logo_file_id),
        "bankdaten": bool(profile and profile.iban),
        "mailkonto": MailAccount.objects.filter(active=True).exists(),
        "kontakt": Party.objects.exists(),
        "liegenschaft": Property.objects.exists(),
        "projekt": Project.objects.exists(),
        "beleg": Quote.objects.exists() or Invoice.objects.exists(),
    }
