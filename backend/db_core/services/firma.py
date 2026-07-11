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
import hashlib
import re
import uuid
from pathlib import PurePosixPath

from db_core import storage as storage_module
from db_core.db_context import business_transaction
from db_core.models import Branch, CompanyProfile, DunningLevel, File, Trade
from db_core.services import vier_augen

# --- Firmenprofil (Singleton) ----------------------------------------------

# Pflegbare Profilfelder (Whitelist gegen willkürliche Attribut-Injektion).
_PROFILE_FIELDS = (
    "company_name", "legal_form", "street", "postal_code", "city", "country",
    "state_code", "phone", "email", "web", "tax_number", "vat_id",
    "commercial_register", "bank_name", "iban", "bic", "managing_director",
    "managing_director_title", "default_language", "logo_file_id",
)

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

    profile = get_company_profile()
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
    # Spiegelt den DB-CHECK trade_code_check; sonst 500 statt 422.
    if not re.fullmatch(r"[A-Z0-9_]{2,}", code):
        raise ValueError(
            "Gewerk-Code darf nur A–Z, 0–9 und _ enthalten (mindestens 2 Zeichen)."
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
