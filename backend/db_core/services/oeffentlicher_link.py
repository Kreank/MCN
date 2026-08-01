"""Öffentliche Links — der Unterbau für alles, was ohne Anmeldung erreichbar ist.

Ein öffentlicher Link ist ein **Bearer-Geheimnis mit genau einer erlaubten
Handlung an genau einem Objekt**. Die Mechanik ist für jeden Verbraucher
dieselbe und wohnt deshalb an einer Stelle (`security.public_link`, Migration
0141): Klartext genau einmal ausgeben, in der Datenbank nur den SHA-256-Hash,
Ablauf serverseitig prüfen, Widerruf über `revoked_at`, Einlösung unter
Zeilensperre mit Replay-Schutz.

**Vorbilder im Haus, bewusst zusammengeführt:** `pricing.punchout_session`
(Einmal-Token, Einlösung unter `select_for_update`) und `security.device_token`
(Klartext verlässt den Server genau einmal, Widerruf statt Löschung).

## Die vier Regeln, an denen hier nichts gelockert wird

1. **Kein Orakel — für die Fälle, in denen Raten etwas brächte.** Unbekannt,
   abgelaufen und widerrufen führen zu *demselben* Ergebnis (`None`) über
   *denselben* Weg (eine indizierte Abfrage auf den Hash). Wer aus der Antwort
   ableiten kann, dass ein Token „mal gültig war", hat einen Teilerfolg beim
   Raten.
2. **Ein eingelöster Link bleibt lesbar, bis er abläuft.** Er ist ausdrücklich
   *nicht* Teil von Regel 1: Wer eingelöst hat, hat den Besitz bereits
   nachgewiesen — ihm den Ausgang zu verweigern verrät niemandem etwas und lässt
   nur den Kunden glauben, seine Zusage sei fehlgeschlagen, weil ein Neuladen
   „ungültiger Link" zeigt. Ob er noch *handeln* darf, entscheidet nicht der
   Link, sondern der Verbraucher (beim Angebot: der Belegstatus).
3. **Der Klartext existiert einmal.** `link_erzeugen` gibt ihn zurück; danach
   kennt ihn nur noch der Empfänger. Ein Datenbank-Leck liefert keine nutzbaren
   Links.
4. **Einlösen gehört in die Transaktion der Handlung.** `einloesen` öffnet
   deshalb *keine* eigene Transaktion — es läuft in der des Aufrufers, wie
   `vier_augen.claim()`. Scheitert die Fachaktion, ist der Link nicht verbraucht.

## Einmalig oder mehrfach — eine Eigenschaft des Links, kein Ermessen

Die Angebotsfreigabe ist genau **eine** Erklärung; die als Nächstes aufsetzende
Kunden-Terminbuchung ist bewusst mehrfach nutzbar (absagen, umbuchen). Beides
muss derselbe Unterbau tragen, also steht die Unterscheidung als Spalte an der
Zeile (`single_use`) — dort und nur dort kann die **Datenbank** sie durchsetzen
(`CHECK (NOT single_use OR use_count <= 1)`), und der Guard-Trigger friert sie
ein. Sie im Verbraucher zu lassen hieße, sie beim zweiten Verbraucher erneut
richtig treffen zu müssen; die zweite Kopie ist erfahrungsgemäß die ohne Prüfung.

Welchen Wert ein Zweck bekommt, steht in `_EINMALIG_JE_ZWECK` — **nicht** im
Aufrufer. Fail-closed: ein Zweck, der dort fehlt, ist einmalig.

## Drosselung

Wiederverwendet wird die vorhandene DB-Mechanik aus `login_schutz.py`
(`security.login_register_failure` / `login_is_locked`): Die SQL-Funktionen sind
über ihren `bucket_key` generisch, es ist kein Login-Spezifikum daran. Eine
zweite Drossel zu bauen hieße, dieselbe Zähl-, Fenster- und Sperrlogik ein
zweites Mal richtig zu treffen — und die zweite Kopie ist erfahrungsgemäß die
ohne Fensterprüfung. Eigen sind hier nur der Namensraum des Schlüssels
(`plink:…`) und die Schwellen (ein Kundenlink wird ein-, nicht dreißigmal
geöffnet).

Gezählt werden **Fehlschläge**, nicht Aufrufe: Ein Kunde, der seine Angebotsseite
fünfmal neu lädt, soll sich nicht aussperren; ein Angreifer, der Token durchrät,
soll es. Gespeichert wird nur der Zählerschlüssel — keine IP-Historie über das
Nötige hinaus (`security.login_throttle` trägt bewusst keinen Schutzstandard,
sie ist transienter Zustand, Migration 0116).
"""
import hashlib
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.db import connection

from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import AppUser, PublicLink

#: Feste Kennung des technischen Akteurs aus Migration 0141. Er schreibt alles,
#: was aus einem öffentlichen Link entsteht — nie ein zufälliger Mensch.
SYSTEMAKTEUR_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")

#: Geschlossenes Vokabular; jede weitere Art kostet eine Migration (CHECK).
PURPOSE_ANGEBOT_FREIGABE = "ANGEBOT_FREIGABE"

#: Weiche Ziel-Kennung (wie `notify.notification.target_type`).
ZIEL_ANGEBOT = "invoicing.quote"

#: Welche Zwecke genau EINE Einlösung erlauben (siehe Modulkopf). Der Wert landet
#: als `single_use` an der Zeile; kein Aufrufer setzt ihn selbst. **Fail-closed:**
#: Ein hier nicht eingetragener Zweck ist einmalig — wer einen mehrfach nutzbaren
#: Link braucht, muss das hier bewusst hinschreiben.
_EINMALIG_JE_ZWECK = {
    PURPOSE_ANGEBOT_FREIGABE: True,
}

#: Obergrenze der Gültigkeit. Ein Link, der ein Jahr lebt, ist ein Dauerzugang.
MAX_GUELTIGKEIT = timedelta(days=90)

#: `secrets.token_urlsafe(32)` liefert 43 Zeichen aus diesem Alphabet. Die
#: Vorprüfung hält offensichtlichen Unsinn (SQL, Pfade, Riesen-Strings) von der
#: Datenbank fern — sie ersetzt keine Prüfung, sie spart eine Abfrage.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$")


class LinkError(ValueError):
    """Der Link-Vorgang ist fachlich unzulässig (→ 422)."""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now():
    return datetime.now(dt_timezone.utc)


def _cfg(name: str, default: int) -> int:
    return int(getattr(settings, name, default))


# --- Der Systemakteur -------------------------------------------------------

def systemakteur() -> AppUser:
    """Der technische Akteur, der öffentliche Schreibvorgänge ausführt.

    Fail-closed: Fehlt die Zeile oder ist sie kein Systemakteur, wird nichts
    geschrieben. Ein Rückfall auf „irgendeinen aktiven Account" wäre genau die
    Falschzuschreibung im Audit-Trail, die Migration 0141 beseitigt.
    """
    actor = AppUser.objects.filter(
        id=SYSTEMAKTEUR_ID, status="ACTIVE", is_system=True
    ).first()
    if actor is None:
        raise LinkError(
            "Der Systemakteur für die Online-Selbstbedienung fehlt oder ist "
            "deaktiviert (security.app_user, Migration 0141)."
        )
    return actor


# --- Erzeugen, Auflösen, Widerrufen, Einlösen -------------------------------

def link_erzeugen(actor_app_user_id, *, purpose, target_type, target_id,
                  gueltig_bis):
    """Legt einen Link an und gibt `(zeile, klartext)` zurück.

    **Der Klartext steht ausschließlich in diesem Rückgabewert.** Er wird nicht
    protokolliert, nicht in `content.communication` geschrieben und ist danach
    nicht mehr abrufbar — in der Datenbank liegt nur sein SHA-256-Hash.
    """
    if gueltig_bis is None:
        raise LinkError("Ein öffentlicher Link braucht ein Ablaufdatum.")
    jetzt = _now()
    if gueltig_bis <= jetzt:
        raise LinkError("Das Ablaufdatum muss in der Zukunft liegen.")
    if gueltig_bis > jetzt + MAX_GUELTIGKEIT:
        raise LinkError(
            f"Ein öffentlicher Link gilt höchstens {MAX_GUELTIGKEIT.days} Tage."
        )

    token = secrets.token_urlsafe(32)
    with as_business_error():
        with business_transaction(actor_app_user_id):
            zeile = PublicLink.objects.create(
                id=uuid.uuid4(),
                purpose=purpose,
                target_type=target_type,
                target_id=target_id,
                token_hash=_hash(token),
                expires_at=gueltig_bis,
                # Aus dem Zweck abgeleitet, nicht vom Aufrufer gesetzt — sonst
                # wäre die Einmaligkeit eine Frage der Aufrufstelle.
                single_use=_EINMALIG_JE_ZWECK.get(purpose, True),
                created_by_id=actor_app_user_id,
                use_count=0,
                version=1,
            )
    zeile.refresh_from_db()
    return zeile, token


def link_aufloesen(klartext, *, purpose):
    """Die noch **erreichbare** Link-Zeile zum Klartext-Token — oder `None`.

    `None` bedeutet **immer dasselbe**: unbekannt, falscher Zweck, abgelaufen
    oder widerrufen. Der Aufrufer darf diese Fälle nicht unterscheiden und tut es
    auch nicht (siehe `api/oeffentlich.py`).

    Eine **bereits eingelöste** Zeile kommt bewusst zurück (Regel 2 im
    Modulkopf): Der Inhaber hat den Besitz nachgewiesen und darf den Ausgang
    sehen. Ob er noch handeln darf, ist eine Frage an den Verbraucher, nicht an
    den Link — `einloesen` sagt bei `single_use` ein zweites Mal Nein.

    Der Vergleich läuft über den Hash und zusätzlich über
    `secrets.compare_digest` — der Datenbankvergleich ist der Treffer, der
    Konstantzeit-Vergleich die Zusicherung, dass in unserem Code kein
    zeichenweiser Abbruch stattfindet.
    """
    if not klartext or not _TOKEN_RE.match(klartext):
        return None
    erwartet = _hash(klartext)
    zeile = PublicLink.objects.filter(token_hash=erwartet, purpose=purpose).first()
    if zeile is None:
        return None
    if not secrets.compare_digest(zeile.token_hash, erwartet):
        return None
    if zeile.revoked_at is not None:
        return None
    if zeile.expires_at <= _now():
        return None
    return zeile


def link_widerrufen(actor_app_user_id, link_id, *, purpose=None) -> bool:
    """Zieht einen Link zurück. Idempotent: ein bereits widerrufener bleibt es.

    `purpose` ist die **Zuständigkeitsgrenze** und sollte immer gesetzt werden:
    Die Endpunkte hängen an einem Modulrecht (`invoicing/VERSENDEN` beim
    Angebot), die Tabelle aber trägt die Links aller Bereiche. Ohne diese Angabe
    könnte, wer Angebotslinks widerrufen darf, auch fremde (z. B. künftige
    Terminbuchungs-) Links stilllegen.

    Rückgabe: ob es die Zeile gibt (nicht, ob sich etwas geändert hat) — sonst
    wäre der zweite Klick ein 404.
    """
    qs = PublicLink.objects.filter(id=link_id)
    if purpose is not None:
        qs = qs.filter(purpose=purpose)
    with as_business_error():
        with business_transaction(actor_app_user_id):
            qs.filter(revoked_at__isnull=True).update(revoked_at=_now())
            return qs.exists()


def einloesen(link_id):
    """Verbraucht einen Link — **in der Transaktion des Aufrufers**.

    Öffnet bewusst keine eigene Transaktion: Die Einlösung gehört untrennbar zu
    der Handlung, die sie erlaubt (Muster `vier_augen.claim()`). Scheitert die
    Handlung fachlich, rollt das Verbrauchen mit zurück.

    Die maßgeblichen Prüfungen laufen hier ein zweites Mal — unter
    `select_for_update`. Zwei gleichzeitige Klicks auf denselben Link lesen sonst
    beide einen unverbrauchten Zustand (READ COMMITTED) und lösen zweimal aus.

    Bei `single_use` ist die zweite Einlösung ein Fehler; bei einem mehrfach
    nutzbaren Link rückt `used_at` vor und `use_count` zählt weiter. Der
    Schreibvorgang trägt die gelesene `use_count` als Bedingung mit — auch wenn
    die Zeilensperre das schon ausschließt, ist das die Zusicherung, die nicht
    davon abhängt, dass jeder künftige Aufrufer die Sperre nimmt.
    """
    zeile = PublicLink.objects.select_for_update().filter(id=link_id).first()
    if zeile is None:
        raise LinkError("Der Link ist nicht mehr gültig.")
    if zeile.revoked_at is not None:
        raise LinkError("Der Link ist nicht mehr gültig.")
    if zeile.expires_at <= _now():
        raise LinkError("Der Link ist nicht mehr gültig.")
    if zeile.single_use and zeile.used_at is not None:
        raise LinkError("Dieser Link wurde bereits verwendet.")
    getroffen = PublicLink.objects.filter(
        id=link_id, use_count=zeile.use_count
    ).update(used_at=_now(), use_count=zeile.use_count + 1)
    if not getroffen:
        # Kann unter der Sperre nicht auftreten; bleibt als letzte Instanz stehen.
        raise LinkError("Dieser Link wurde bereits verwendet.")
    zeile.refresh_from_db()
    return zeile


def links_zum_ziel(*, purpose, target_type, target_id):
    """Alle Links auf ein Ziel, neueste zuerst (für die Liste im Leitstand)."""
    return list(
        PublicLink.objects.select_related("created_by")
        .filter(purpose=purpose, target_type=target_type, target_id=target_id)
        .order_by("-created_at", "id")
    )


def ist_offen(zeile, *, jetzt=None) -> bool:
    """Ist dieser Link noch einlösbar? (Anzeige-Hilfe, keine Autorisierung.)

    Ein mehrfach nutzbarer Link bleibt offen, auch wenn er schon benutzt wurde —
    „offen" heißt hier „es geht damit noch etwas", nicht „unberührt".
    """
    jetzt = jetzt or _now()
    return (
        zeile.revoked_at is None
        and not (zeile.single_use and zeile.used_at is not None)
        and zeile.expires_at > jetzt
    )


# --- Drosselung (DB-Mechanik aus login_schutz.py wiederverwendet) -----------

def _bucket(ip: str) -> str:
    return f"plink:ip:{ip or 'unbekannt'}"


def gesperrt(ip: str) -> bool:
    """Ist diese IP wegen zu vieler Fehlversuche gesperrt?"""
    with connection.cursor() as cur:
        cur.execute("SELECT security.login_is_locked(%s)", [[_bucket(ip)]])
        row = cur.fetchone()
    return bool(row and row[0] is not None)


def fehlversuch(ip: str) -> None:
    """Verbucht einen Fehlversuch (unbekanntes/abgelaufenes Token) auf der IP.

    Läuft ohne `business_transaction` — der Aufrufer ist nicht authentifiziert,
    es gibt keinen `app.current_user_id`. Dieselbe Begründung wie in
    `login_schutz.py`; die Zähllogik selbst ist atomar (UPSERT in der
    SQL-Funktion).
    """
    with connection.cursor() as cur:
        cur.execute(
            "SELECT security.login_register_failure(%s, %s, %s, %s)",
            [
                _bucket(ip),
                _cfg("MCN_PUBLIC_LINK_IP_THRESHOLD", 10),
                _cfg("MCN_PUBLIC_LINK_WINDOW_SECONDS", 900),
                _cfg("MCN_PUBLIC_LINK_LOCKOUT_SECONDS", 900),
            ],
        )
