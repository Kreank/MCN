"""IDS-Connect Punchout-Session — HTTP-Roundtrip des itek-2.5-Warenkorbverfahrens.

Der Ablauf:

1. **Start** (`start_session`): MCN legt eine kurzlebige Session mit einem
   Einmal-Token an, baut das Punchout-Formular (über `anbindung.build_punchout`)
   und übergibt dem Shop eine `hookurl`, die das Token trägt. Bei `action='WKS'`
   wird der aktuelle Angebots-Warenkorb als itek-XML mitgegeben.
2. **Rückgabe** (`receive_cart`): Der Shop POSTet den fertigen Warenkorb an die
   hookurl. Der (unauthentifizierte) Endpunkt reicht Token + XML hierher; die
   Session wird über den Token-**Hash** gefunden, geprüft (offen, nicht abgelaufen)
   und auf EINGELOEST gesetzt, der Warenkorb gespeichert.
3. **Vorschau** (`session_preview`): Das Frontend pollt die Session; sobald sie
   eingelöst ist, werden die Positionen geparst und gegen den Artikelstamm
   aufgelöst (`ids_warenkorb.resolve_positions`) — der Anwender übernimmt sie dann
   im Angebots-Editor.

Sicherheit: In der DB liegt nur der SHA-256-Hash des Tokens (`token_hash`); der
Klartext existiert ausschließlich in der an den Shop übergebenen hookurl. Ein
abgelaufenes oder bereits eingelöstes Token wird abgewiesen (Replay-Schutz; der
DB-Trigger `protect_punchout_session` verhindert zusätzlich das Zurücksetzen).
"""
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone

from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import PunchoutSession, Quote, SupplierConnection
from db_core.services import anbindung as anbindung_service
from db_core.services import ids_warenkorb

# Gültigkeitsdauer einer Punchout-Session. Der Handwerker stellt in dieser Zeit
# den Warenkorb im Shop zusammen; danach ist das Token wertlos.
PUNCHOUT_TTL = timedelta(hours=2)


class PunchoutError(ValueError):
    """Der Punchout-Vorgang ist fachlich unzulässig (→ 422)."""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now():
    return datetime.now(dt_timezone.utc)


def start_session(actor_app_user_id, *, connection_id, hook_base, action="WKE",
                  quote_id=None, positions=None):
    """Startet eine Punchout-Session und gibt `(session, punchout)` zurück.

    `hook_base` ist die absolute Basis-URL des Rückgabe-Endpunkts OHNE Token
    (z. B. ``https://host/api/pricing/warenkorb-return/``); das Token wird angehängt.
    `positions` (Liste `ids_warenkorb.CartPosition`) füllt bei `action='WKS'` den
    Ausgangs-Warenkorb. Wirft PunchoutError (→ 422) bei fehlender URL/Zugangsdaten
    oder ungültiger Aktion (aus `build_punchout`), bzw. wenn die Anbindung/das
    Angebot nicht existiert.
    """
    conn = SupplierConnection.objects.filter(id=connection_id).first()
    if conn is None:
        raise PunchoutError("Anbindung nicht gefunden.")
    if quote_id is not None and not Quote.objects.filter(id=quote_id).exists():
        raise PunchoutError("Angebot nicht gefunden.")

    action = (action or "WKE").upper()
    cart_xml = None
    if action == "WKS":
        # Auch ein leerer Warenkorb ist zulässig (mit leerem Korb in den Shop).
        cart_xml = ids_warenkorb.build_cart_xml(positions or []).decode("utf-8")

    token = secrets.token_urlsafe(32)
    hook_url = f"{hook_base}{token}"
    try:
        punchout = anbindung_service.build_punchout(
            connection_id, hook_url=hook_url, action=action, warenkorb_xml=cart_xml
        )
    except ValueError as exc:
        raise PunchoutError(str(exc))

    with as_business_error():
        with business_transaction(actor_app_user_id):
            session = PunchoutSession.objects.create(
                id=uuid.uuid4(),
                connection_id=connection_id,
                quote_id=quote_id,
                token_hash=_hash(token),
                action=action,
                status="OFFEN",
                created_by_id=actor_app_user_id,
                expires_at=_now() + PUNCHOUT_TTL,
                version=1,
            )
    session.refresh_from_db()
    return session, punchout


def receive_cart(token: str, xml) -> PunchoutSession:
    """Löst eine Session über ihr Token ein und speichert den zurückgegebenen
    Warenkorb.

    `xml` sind die Roh-Bytes (oder ein String) des vom Shop gelieferten
    Warenkorbs. Der Aufruf ist **unauthentifiziert** — die Autorisierung ist das
    (gehashte) Token. Wirft PunchoutError bei unbekanntem/abgelaufenem/bereits
    eingelöstem Token oder ungültigem XML. Der Ersteller der Session ist der
    Akteur für den Schreibvorgang (der Rückruf hat kein eigenes Konto).
    """
    if not token:
        raise PunchoutError("Kein Token übergeben.")
    token_hash = _hash(token)
    # Vorab-Lesung (ungelockt): nur, um früh zu 404-en und den Akteur (Ersteller)
    # für den Audit-Kontext der Transaktion zu kennen. Die maßgeblichen Prüfungen
    # laufen gleich noch einmal unter Zeilensperre.
    vorab = PunchoutSession.objects.filter(token_hash=token_hash).first()
    if vorab is None:
        raise PunchoutError("Unbekanntes oder ungültiges Token.")

    raw = xml.encode("utf-8") if isinstance(xml, str) else xml
    # Validieren (defusedxml-gehärtet) — ungültiges XML wird nicht gespeichert.
    ids_warenkorb.parse_returned_cart(raw)
    # Bijektiv über latin-1 ablegen: erhält die Originalbytes samt Kodierung
    # verlustfrei (die Vorschau reencodet identisch), ohne Umlaute zu verstümmeln.
    gespeichert = raw.decode("latin-1")

    with as_business_error():
        with business_transaction(vorab.created_by_id):
            # Zeile sperren und Status/Ablauf UNTER der Sperre erneut prüfen: so
            # kann eine zweite, gleichzeitige Rückgabe desselben Tokens die erste
            # nicht überschreiben (Einmal-Einlösung, Muster wie vier_augen).
            session = PunchoutSession.objects.select_for_update().get(id=vorab.id)
            if session.status != "OFFEN":
                raise PunchoutError("Diese Punchout-Session wurde bereits eingelöst.")
            if session.expires_at <= _now():
                raise PunchoutError("Diese Punchout-Session ist abgelaufen.")
            session.returned_cart_xml = gespeichert
            session.redeemed_at = _now()
            session.status = "EINGELOEST"
            session.save(update_fields=[
                "returned_cart_xml", "redeemed_at", "status", "updated_at",
            ])
    session.refresh_from_db()
    return session


def get_session(session_id):
    return (
        PunchoutSession.objects.filter(id=session_id)
        .select_related("connection")
        .first()
    )


def session_preview(session_id):
    """Status der Session + (falls eingelöst) die aufgelösten Positionen.

    Gibt `(session, resolved)` zurück; `resolved` ist `[]`, solange die Session
    noch offen ist. Gibt `(None, [])`, wenn es die Session nicht gibt.
    """
    session = get_session(session_id)
    if session is None:
        return None, []
    if session.status != "EINGELOEST" or not session.returned_cart_xml:
        return session, []
    positions = ids_warenkorb.parse_returned_cart(
        session.returned_cart_xml.encode("latin-1"),
        net_price_semantics=session.connection.net_price_semantics,
    )
    resolved = ids_warenkorb.resolve_positions(
        session.connection.source_namespace, positions
    )
    return session, resolved
