"""Anmeldefreie Kundenwege — heute: „Angebot online annehmen".

Zwei Router in einer Datei, weil sie zwei Seiten derselben Sache sind:

  * `router` (`/api/oeffentlich/…`, **`auth=None`**) — was der Kunde ohne Konto
    aufruft. Autorisierung ist ausschließlich das Token in der URL.
  * `intern_router` (`/api/invoicing/…`, Sitzung + Recht) — womit der Betrieb
    solche Links erzeugt, auflistet und widerruft.

## Die Regeln der öffentlichen Antwort

**Positivliste, nicht Filter.** `OeffentlichePositionOut` erbt nichts von
`QuoteLineOut` und zählt jedes Feld einzeln auf. Eine Vererbung „QuoteLineOut
ohne unit_cost" wäre eine Feldliste, die man beim nächsten neuen Betragsfeld
stillschweigend vergisst — genau die Begründung, mit der schon `QuoteMengenOut`
(Migration 0102) additiv gebaut wurde. Der Kunde **muss** Preise sehen, er soll
ja etwas annehmen; `unit_cost` und `markup_percent` (Einkauf und Aufschlag)
sind hier strukturell nicht erreichbar, nicht bloß weggelassen.

**Ein Wortlaut für alles Ungültige.** Unbekanntes, abgelaufenes und widerrufenes
Token liefern dieselbe 404 mit demselben Satz — byteweise dieselbe Antwort. Der
Aufrufer erfährt nie, ob er „fast" richtig lag; Vorbild ist `_ZU_VIELE` in
`api/auth.py`, das aus demselben Grund keine Restdauer nennt. Der Weg dorthin ist
in allen drei Fällen derselbe (eine indizierte Abfrage auf den Hash), also gibt
es auch kein Timing-Orakel.

**Ein eingelöster Link bleibt lesbar und zeigt den Ausgang.** Er gehört
ausdrücklich **nicht** in die Gruppe oben: Wer eingelöst hat, hat den Besitz
nachgewiesen. Ihm den Ausgang zu verweigern verrät niemandem etwas — es lässt nur
den Kunden glauben, seine Zusage sei fehlgeschlagen, weil ein Neuladen
„ungültiger Link" zeigt. Bis `expires_at` liefert GET deshalb weiter den Beleg,
mit `entscheidbar = false`, dem `ausgang` und dem Zeitpunkt (`ausgang_am`).
Genau dieselbe Antwort entsteht, wenn der Betrieb **intern** entschieden hat,
während der Link noch frisch ist. Eine zweite Entscheidung prallt in beiden
Fällen ab (422) — der Ausgang ist am Beleg, nicht am Link, und der Statusautomat
lässt VERSENDET → ANGENOMMEN genau einmal zu.

**Der Unterbau trägt auch mehrfach nutzbare Links** (`security.public_link.
single_use`), weil die als Nächstes aufsetzende Terminbuchung Absage und
Umbuchung über denselben Link erlaubt. Für das Angebot bleibt es bei genau einer
Entscheidung — und die wird an **zwei** Stellen gehalten: `single_use` in der
Datenbank (CHECK) und der Belegstatus im Service.

**Der Link kann genau eine Sache.** Kein anderer Beleg, keine Adresse, kein
Kontakt, keine Position — der Zielbezug steht in der Link-Zeile und wird nie aus
dem Request übernommen.

**Der Schreibweg ist der reguläre.** `beleg.set_quote_status` mit dem
Systemakteur, in einer `business_transaction`. Keine eigene SQL-Abkürzung: Der
Automat geht durch dieselbe Tür wie ein Mensch (CLAUDE.md), inklusive
Statusautomat, Einfrier-Trigger, Abrechnungs-Guard und Audit.

⚠️ **Bewusst in Kauf genommen:** Das Token steht in der URL und landet damit im
nginx-Zugriffsprotokoll und potenziell im `Referer` weitergeklickter Links. Das
teilt es mit dem bereits bestehenden Punchout-Rückgabe-Endpunkt. Gegenmittel
sind die kurze Gültigkeit, die Einmal-Einlösung und der Widerruf — nicht die
Hoffnung, dass niemand mitliest.
"""
import logging
from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID

from django.conf import settings
from django.middleware.csrf import get_token
from ninja import Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status

from api.auth import _require_csrf
from api.permissions import require
from db_core.db_context import run_business_transaction
from db_core.models import CompanyProfile, Quote, StatusChange
from db_core.services import beleg as beleg_service
from db_core.services import beleg_versand as versand_service
from db_core.services import benachrichtigung as benachrichtigung_service
from db_core.services import login_schutz
from db_core.services import oeffentlicher_link as link_service
from db_core.services import rechte as rechte_service

logger = logging.getLogger(__name__)

router = Router()
intern_router = Router()

# EINE Meldung für unbekannt / abgelaufen / widerrufen / bereits verwendet.
# Sie darf nie aufgetrennt werden — die Unterscheidung wäre das Orakel.
_UNGUELTIG = (
    "Dieser Link ist nicht (mehr) gültig. Bitte wenden Sie sich an uns, "
    "wenn Sie ihn noch benötigen."
)

# Drosselung, ohne die Restdauer zu nennen (kein Timing-Orakel), Vorbild
# `_ZU_VIELE` in api/auth.py.
_ZU_VIELE = (
    "Zu viele Versuche. Bitte versuchen Sie es später noch einmal."
)

#: Ausgänge, die der Kunde über den Link setzen darf. ABGELAUFEN ist bewusst
#: nicht dabei: Das ist eine Feststellung des Betriebs, keine Entscheidung des
#: Kunden. ERSETZT verlangt ein Nachfolgeangebot (DB-CHECK) und ist kein
#: Statuswechsel — siehe `beleg.set_quote_status`.
_ERLAUBTE_ENTSCHEIDUNGEN = ("ANGENOMMEN", "ABGELEHNT")

_MELDUNG = {
    "ANGENOMMEN": "Vielen Dank — Ihre Zusage ist bei uns eingegangen.",
    "ABGELEHNT": "Ihre Rückmeldung ist bei uns eingegangen.",
}

_NOTIFY_KIND = {
    "ANGENOMMEN": "ANGEBOT_ANGENOMMEN",
    "ABGELEHNT": "ANGEBOT_ABGELEHNT",
}


# ---------------------------------------------------------------------------
# Schemata — ausdrücklich aufgezählt, nichts abgeleitet
# ---------------------------------------------------------------------------

class OeffentlichePositionOut(Schema):
    """Eine Angebotsposition, wie der Kunde sie auf dem Beleg sieht.

    Enthalten ist, was auch auf dem PDF steht. **Nicht** enthalten und über
    dieses Schema auch nicht erreichbar: `unit_cost` (Einkaufspreis),
    `markup_percent` (Aufschlag), `sale_price_group_id`, `source_article_id`,
    `source_assembly_id`, `labour_net_amount`, `tax_code`. Die ersten beiden
    sind die eigentliche Gefahr — sie stehen nicht einmal auf dem Kundenbeleg.
    """
    position_number: int
    line_type: str
    # NORMAL | ALTERNATIV | BEDARF. Bleibt drin und ist wichtig: Eine
    # Alternativ-/Bedarfsposition zählt nicht in die Summe, und der Kunde soll
    # sehen, worüber er entscheidet.
    line_kind: str = "NORMAL"
    rubrik: int | None = None
    description: str
    quantity: Decimal | None = None
    unit: str | None = None
    unit_price: Decimal | None = None
    discount_percent: Decimal | None = None
    tax_rate_percent: Decimal | None = None
    net_amount: Decimal | None = None


class OeffentlicheRubrikOut(Schema):
    position_number: int
    title: str
    description: str | None = None


class OeffentlicherAusstellerOut(Schema):
    """Der Briefkopf: nur, was ohnehin auf jedem Geschäftsbrief steht.

    Kein IBAN/BIC, keine Steuer-/Registernummern, keine DATEV-Konten — die
    stehen im Firmenprofil daneben und haben auf einer öffentlichen Seite nichts
    zu suchen.
    """
    company_name: str
    street: str | None = None
    postal_code: str | None = None
    city: str | None = None
    phone: str | None = None
    email: str | None = None
    web: str | None = None


class OeffentlichesAngebotOut(Schema):
    quote_number: str | None = None
    title: str
    status: str
    quote_date: date | None = None
    valid_until_date: date | None = None
    currency: str
    net_total: Decimal | None = None
    tax_total: Decimal | None = None
    gross_total: Decimal | None = None
    # Anschreiben-Freitext des Belegkopfs (ab VERSENDET eingefroren).
    cover_letter: str | None = None
    # Wofür das Angebot gilt — als fertiger Text, nicht als Objekt-Fremdschlüssel.
    objekt: str | None = None
    aussteller: OeffentlicherAusstellerOut | None = None
    rubriken: list[OeffentlicheRubrikOut] = []
    positionen: list[OeffentlichePositionOut] = []
    # Darf der Kunde jetzt entscheiden? False, sobald der Ausgang feststeht —
    # dann steht er in `ausgang`, mit dem Zeitpunkt in `ausgang_am`.
    entscheidbar: bool
    ausgang: str | None = None
    ausgang_am: datetime | None = None
    link_gueltig_bis: datetime


class EntscheidungIn(Schema):
    entscheidung: Literal["ANGENOMMEN", "ABGELEHNT"]


class EntscheidungOut(Schema):
    ausgang: str
    meldung: str
    quote_number: str | None = None
    title: str


# ---------------------------------------------------------------------------
# Gemeinsame Wege
# ---------------------------------------------------------------------------

def _drossel_pruefen(request):
    """Erst die Bremse, dann alles andere — sonst ist der Link ein Rateautomat."""
    ip = login_schutz.client_ip(request)
    if link_service.gesperrt(ip):
        raise HttpError(429, _ZU_VIELE)
    return ip


def _link_oder_404(token, ip):
    """Die gültige Link-Zeile — oder EINE einheitliche 404.

    Der Fehlversuch wird nur hier verbucht: an genau der Stelle, an der ein
    Token nicht aufgeht. Ein Kunde, der seine Seite mehrfach lädt, zählt nicht.
    """
    link = link_service.link_aufloesen(
        token, purpose=link_service.PURPOSE_ANGEBOT_FREIGABE
    )
    if link is None:
        link_service.fehlversuch(ip)
        raise HttpError(404, _UNGUELTIG)
    return link


def _quote_oder_404(link):
    # Der Zielbezug kommt AUSSCHLIESSLICH aus der Link-Zeile — nie aus dem
    # Request. `target_type` wird mitgeprüft: `purpose` und Zieltabelle gehören
    # zusammen, und wenn eine künftige Anlagestelle das einmal auseinanderlaufen
    # lässt, soll hier nichts geladen werden statt irgendetwas.
    if link.target_type != link_service.ZIEL_ANGEBOT:
        raise HttpError(404, _UNGUELTIG)
    quote = (
        Quote.objects.filter(id=link.target_id)
        .select_related("property__address")
        .prefetch_related("lines", "rubriken")
        .first()
    )
    if quote is None:
        # Das Ziel ist verschwunden (weiche Referenz, kein FK). Für den Kunden
        # ist das derselbe Zustand wie ein ungültiger Link.
        raise HttpError(404, _UNGUELTIG)
    return quote


def _objekt_text(quote):
    obj = quote.property
    if obj is None:
        return None
    teile = [obj.name] if getattr(obj, "name", None) else []
    adresse = getattr(obj, "address", None)
    if adresse is not None:
        zeile = " ".join(
            t for t in [
                adresse.street,
                " ".join(t for t in [adresse.postal_code, adresse.city] if t),
            ] if t
        ).strip()
        if zeile:
            teile.append(zeile)
    return ", ".join(teile) or None


def _aussteller():
    profil = CompanyProfile.objects.first()
    if profil is None or not profil.company_name:
        return None
    return OeffentlicherAusstellerOut(
        company_name=profil.company_name,
        street=profil.street,
        postal_code=profil.postal_code,
        city=profil.city,
        phone=profil.phone,
        email=profil.email,
        web=profil.web,
    )


def _angebot_out(quote, link):
    nummern = {r.id: r.position_number for r in quote.rubriken.all()}
    positionen = [
        OeffentlichePositionOut(
            position_number=l.position_number,
            line_type=l.line_type,
            line_kind=l.line_kind,
            rubrik=nummern.get(l.rubrik_id),
            description=l.description,
            quantity=l.quantity,
            unit=l.unit,
            unit_price=l.unit_price,
            discount_percent=l.discount_percent,
            tax_rate_percent=l.tax_rate_percent,
            net_amount=l.net_amount,
        )
        for l in sorted(quote.lines.all(), key=lambda x: x.position_number)
    ]
    rubriken = [
        OeffentlicheRubrikOut(
            position_number=r.position_number, title=r.title, description=r.description
        )
        for r in sorted(quote.rubriken.all(), key=lambda r: r.position_number)
    ]
    entscheidbar = quote.status == "VERSENDET"
    return OeffentlichesAngebotOut(
        quote_number=quote.quote_number,
        title=quote.title,
        status=quote.status,
        quote_date=quote.quote_date,
        valid_until_date=quote.valid_until_date,
        currency=quote.currency,
        net_total=quote.net_total,
        tax_total=quote.tax_total,
        gross_total=quote.gross_total,
        cover_letter=quote.cover_letter,
        objekt=_objekt_text(quote),
        aussteller=_aussteller(),
        rubriken=rubriken,
        positionen=positionen,
        entscheidbar=entscheidbar,
        ausgang=None if entscheidbar else quote.status,
        ausgang_am=None if entscheidbar else _ausgang_am(quote),
        link_gueltig_bis=link.expires_at,
    )


def _ausgang_am(quote):
    """Wann der Ausgang festgehalten wurde — aus dem Statusverlauf.

    Der Beleg selbst führt kein Entscheidungsdatum; `workflow.status_change` ist
    die einzige Quelle dafür und wird ausschließlich von den Statusautomat-
    Triggern befüllt. Herausgegeben wird genau **ein** Zeitstempel, nicht der
    Verlauf: Wer wann was intern getan hat, geht den Kunden nichts an.
    """
    return (
        StatusChange.objects.filter(
            entity="quote", entity_id=quote.id, to_status=quote.status
        )
        .order_by("-occurred_at")
        .values_list("occurred_at", flat=True)
        .first()
    )


# ---------------------------------------------------------------------------
# Öffentlich (auth=None)
# ---------------------------------------------------------------------------

@router.get("/angebot/{token}", response=OeffentlichesAngebotOut, auth=None)
def oeffentliches_angebot(request, token: str):
    """Das Angebot hinter einem Freigabelink. Setzt zugleich das csrftoken-Cookie.

    `auth=None`: Der Kunde hat kein Konto. Autorisierung ist das Token — in der
    Datenbank nur als SHA-256-Hash. Kein Modul-Recht, deshalb steht der Pfad mit
    Begründung in `api/tests/test_endpoint_schutz.py::WHITELIST`.

    Der Aufruf ist **nebenwirkungsfrei**: Er verbraucht den Link nicht und
    schreibt nichts (ein GET, der schreibt, wäre schon deshalb falsch, weil ihn
    jeder Vorschau-Bot des Mailprogramms auslöst).

    Ein **bereits eingelöster** Link führt hierher zurück, solange er nicht
    abgelaufen ist: Der Kunde sieht seinen Beleg und den Ausgang, nur eben ohne
    Handlungsmöglichkeit (`entscheidbar = false`). Abgelaufen und widerrufen
    dagegen sind vom unbekannten Token ununterscheidbar.
    """
    ip = _drossel_pruefen(request)
    link = _link_oder_404(token, ip)
    quote = _quote_oder_404(link)
    # Wie /api/auth/csrf: erzeugt den Token und markiert das Cookie; die
    # CsrfViewMiddleware setzt es auf der fertigen Antwort. Ohne das könnte der
    # Kunde den folgenden POST nicht absetzen.
    get_token(request)
    return _angebot_out(quote, link)


@router.post("/angebot/{token}/entscheidung", response=EntscheidungOut, auth=None)
def angebot_entscheiden(request, token: str, payload: EntscheidungIn):
    """Der Kunde nimmt das Angebot an oder lehnt es ab.

    CSRF wird nachgeholt (`_require_csrf`, Muster aus `api/auth.py`): Bei
    `auth=None` prüft django-ninja nicht selbst, und ohne Prüfung könnte eine
    fremde Seite die Entscheidung im Namen des Kunden auslösen, sobald sie den
    Link kennt.

    Reihenfolge in EINER Transaktion, mit dem Systemakteur:
      1. Link einlösen (unter Zeilensperre, Replay-Schutz)
      2. `beleg.set_quote_status` — der reguläre Weg durch Statusautomat und
         Trigger, ohne Abkürzung
      3. Benachrichtigung an die Zuständigen

    Scheitert 2., rollt 1. mit zurück: Der Link bleibt gültig und der Kunde kann
    es erneut versuchen. Umgekehrt wäre er sein Recht los, ohne dass etwas
    geschehen ist.
    """
    _require_csrf(request)
    ip = _drossel_pruefen(request)
    link = _link_oder_404(token, ip)
    quote = _quote_oder_404(link)

    if payload.entscheidung not in _ERLAUBTE_ENTSCHEIDUNGEN:
        # Das Literal-Schema fängt das bereits; hier steht die Regel trotzdem,
        # damit sie nicht allein von der Typannotation abhängt.
        raise HttpError(422, "Unzulässige Entscheidung.")

    # **Die Einmaligkeit der Erklärung hängt am BELEG, nicht am Link.** Der
    # Statusautomat lässt VERSENDET → ANGENOMMEN/ABGELEHNT genau einmal zu; ein
    # eingelöster Link kommt bis hierher (er darf ja den Ausgang zeigen) und
    # prallt an dieser Prüfung ab. Dass hier ein Klartext-Grund steht statt der
    # neutralen 404, ist kein Orakel: Wer bis hierher kommt, hat den Besitz des
    # Tokens bereits nachgewiesen.
    if quote.status != "VERSENDET":
        raise HttpError(
            422,
            "Dieses Angebot kann nicht mehr online entschieden werden — es ist "
            "bereits abgeschlossen. Bitte wenden Sie sich an uns.",
        )

    actor = link_service.systemakteur()
    grund = (
        f"Online-Entscheidung des Kunden über Freigabelink {link.id} "
        f"(security.public_link)"
    )
    def _tun():
        link_service.einloesen(link.id)
        beleg_service.set_quote_status(
            actor.id,
            quote_id=quote.id,
            to_status=payload.entscheidung,
            reason=grund,
        )
        _benachrichtigen(actor, quote, payload.entscheidung)

    try:
        # `run_business_transaction` statt `business_transaction`: `set_quote_status`
        # sperrt den Auftrag (`select_for_update`), und die Rechnungswege sperren
        # dieselbe Zeile — ein Deadlock (40P01) ist ein wiederholbarer Fehler, kein
        # Ergebnis (Retry-Pflicht aus db/README.md). Der Wiederholungslauf ist
        # unbedenklich: Der erste Versuch rollt vollständig zurück, also ist auch
        # der Link wieder unverbraucht.
        run_business_transaction(actor.id, _tun)
    except link_service.LinkError:
        # Ein Wettlauf zweier Klicks: Zwischen der Prüfung oben und der
        # Zeilensperre hat ein zweiter Aufruf den Link verbraucht (oder der
        # Betrieb hat ihn in derselben Sekunde widerrufen). Kein Orakel-Fall —
        # der Besitz ist nachgewiesen —, also eine ehrliche 409 statt der
        # neutralen 404: Der Kunde soll die Seite neu laden und den Ausgang
        # sehen, nicht glauben, sein Link sei kaputt.
        raise HttpError(
            409,
            "Zu diesem Angebot ist gerade schon eine Rückmeldung eingegangen. "
            "Bitte laden Sie die Seite neu.",
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))

    quote.refresh_from_db()
    return EntscheidungOut(
        ausgang=quote.status,
        meldung=_MELDUNG[payload.entscheidung],
        quote_number=quote.quote_number,
        title=quote.title,
    )


def _benachrichtigen(actor, quote, entscheidung):
    """Meldet die Kundenentscheidung an die Zuständigen.

    Empfängerkreis ist genau, wer `invoicing`/`VERSENDEN` mit Scope ALLE trägt —
    dieselbe Matrix, aus der die API ihre 403 zieht. Damit steht in der Glocke
    nichts, was der Empfänger am Ziel nicht ohnehin sähe (`docs/INVARIANTEN.md`,
    Abschnitt 5): Wer ein Angebot versenden darf, sieht Nummer, Titel und Status.

    Inhalt bewusst knapp: Nummer, Titel, Ausgang. Keine Beträge — die stehen im
    Beleg, und die Meldung ist der Weg dorthin, nicht sein Ersatz.
    """
    nummer = quote.quote_number or "(ohne Nummer)"
    wort = "angenommen" if entscheidung == "ANGENOMMEN" else "abgelehnt"
    benachrichtigung_service.viele_benachrichtigen(
        rechte_service.empfaenger_mit_recht("invoicing", "VERSENDEN"),
        kind=_NOTIFY_KIND[entscheidung],
        title=f"Angebot {nummer} {wort}",
        body=f"„{quote.title}“ — der Kunde hat online {wort}.",
        target_type=link_service.ZIEL_ANGEBOT,
        target_id=quote.id,
        ausgeloest_von=actor.id,
    )


# ---------------------------------------------------------------------------
# Intern (Sitzung + Recht) — Links erzeugen, auflisten, widerrufen
# ---------------------------------------------------------------------------

class FreigabelinkIn(Schema):
    # Gültigkeit in Tagen; None = Betriebsvorgabe (MCN_PUBLIC_LINK_TTL_DAYS).
    gueltig_tage: int | None = None
    # Den Link zusätzlich per E-Mail an den Angebotsempfänger schicken.
    # ⚠️ Betrieblich gesperrt (MCN_PUBLIC_LINK_MAIL) — solange das so ist,
    # scheitert der Aufruf mit 422, BEVOR ein Link entsteht.
    per_mail: bool = False
    to_address: str | None = None


class FreigabelinkOut(Schema):
    id: UUID
    expires_at: datetime
    created_at: datetime
    used_at: datetime | None = None
    revoked_at: datetime | None = None
    offen: bool
    created_by: str | None = None


class FreigabelinkNeuOut(FreigabelinkOut):
    """Die EINZIGE Antwort, in der die vollständige URL vorkommt.

    Danach steht in der Datenbank nur noch der SHA-256-Hash — die URL ist nicht
    wiederherstellbar. Das UI muss das deutlich sagen, sonst schließt jemand den
    Dialog und wundert sich.
    """
    url: str
    mail_versandt: bool = False


def _link_out(zeile):
    return FreigabelinkOut(
        id=zeile.id,
        expires_at=zeile.expires_at,
        created_at=zeile.created_at,
        used_at=zeile.used_at,
        revoked_at=zeile.revoked_at,
        offen=link_service.ist_offen(zeile),
        created_by=zeile.created_by.display_name if zeile.created_by_id else None,
    )


def _url(token: str) -> str:
    base = settings.MCN_FRONTEND_BASE_URL.rstrip("/")
    return f"{base}/angebot/{token}"


@intern_router.post(
    "/quotes/{quote_id}/freigabelink",
    response={201: FreigabelinkNeuOut},
)
def freigabelink_erzeugen(request, quote_id: UUID, payload: FreigabelinkIn):
    """Erzeugt einen Freigabelink zu einem versendeten Angebot.

    **Recht `invoicing`/`VERSENDEN`** — und nicht `AENDERN`: Der Link ist ein
    Zustellweg für genau denselben Beleg, den dieses Recht ohnehin an den Kunden
    hinausgeben darf (`POST /quotes/{id}/send-email` hängt am selben Recht). Wer
    ein Angebot verschicken darf, darf auch den Weg dafür erzeugen; wer es nur
    ändern darf, soll es deswegen nicht nach außen tragen können.

    Die Klartext-URL steht **ausschließlich** in dieser Antwort.

    Reihenfolge mit `per_mail=true`: Erst wird geprüft, ob es überhaupt einen
    Empfänger gibt und ob der Versand freigeschaltet ist — beides scheitert mit
    422, BEVOR ein Link entsteht. Ein Link, dessen Klartext in einer
    Fehlerantwort verlorengeht, wäre unbrauchbar und stünde trotzdem in der
    Datenbank.
    """
    actor, _ = require(request, "invoicing", "VERSENDEN")

    quote = Quote.objects.filter(id=quote_id).first()
    if quote is None:
        raise HttpError(404, "Angebot nicht gefunden.")
    if quote.status != "VERSENDET":
        raise HttpError(
            422,
            "Nur ein versendetes Angebot kann online zur Freigabe gestellt "
            f"werden (Status: {quote.status}).",
        )

    tage = payload.gueltig_tage or int(
        getattr(settings, "MCN_PUBLIC_LINK_TTL_DAYS", 14)
    )
    if tage < 1:
        raise HttpError(422, "Die Gültigkeit muss mindestens einen Tag betragen.")

    if payload.per_mail:
        try:
            versand_service.freigabe_empfaenger(
                quote_id, to_address=payload.to_address
            )
        except ValueError as exc:
            raise HttpError(422, str(exc))

    gueltig_bis = datetime.now(dt_timezone.utc) + timedelta(days=tage)
    try:
        zeile, klartext = link_service.link_erzeugen(
            actor,
            purpose=link_service.PURPOSE_ANGEBOT_FREIGABE,
            target_type=link_service.ZIEL_ANGEBOT,
            target_id=quote_id,
            gueltig_bis=gueltig_bis,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))

    url = _url(klartext)
    mail_versandt = False
    if payload.per_mail:
        try:
            versand_service.send_quote_freigabe_email(
                actor,
                quote_id=quote_id,
                link_url=url,
                gueltig_bis=zeile.expires_at,
                to_address=payload.to_address,
            )
            mail_versandt = True
        except ValueError as exc:
            # Der Versand ist gescheitert, NACHDEM der Link entstanden ist (die
            # Vorprüfung oben deckt die erwartbaren Fälle ab; hier bleibt der
            # SMTP-Ausfall). Der Klartext geht mit der Fehlerantwort verloren —
            # ein Link, den niemand mehr kennt, wäre eine offene Tür ohne
            # Aufsicht. Deshalb wird er sofort widerrufen und der Nutzer erzeugt
            # nach der Störung einen neuen.
            link_service.link_widerrufen(
                actor, zeile.id, purpose=link_service.PURPOSE_ANGEBOT_FREIGABE
            )
            logger.warning("Freigabelink erzeugt, Versand fehlgeschlagen — widerrufen.")
            raise HttpError(422, str(exc))

    daten = _link_out(zeile)
    return Status(
        201, FreigabelinkNeuOut(**daten.dict(), url=url, mail_versandt=mail_versandt)
    )


@intern_router.get(
    "/quotes/{quote_id}/freigabelinks", response=list[FreigabelinkOut]
)
def freigabelinks_liste(request, quote_id: UUID):
    """Welche Freigabelinks gibt es zu diesem Angebot? Ohne Klartext, versteht sich.

    Recht `invoicing`/`VERSENDEN` — dieselbe Begründung wie beim Erzeugen: Die
    Liste sagt, wem gerade ein Zustellweg offensteht, und das gehört in dieselbe
    Hand wie das Erzeugen und Widerrufen.
    """
    require(request, "invoicing", "VERSENDEN")
    zeilen = link_service.links_zum_ziel(
        purpose=link_service.PURPOSE_ANGEBOT_FREIGABE,
        target_type=link_service.ZIEL_ANGEBOT,
        target_id=quote_id,
    )
    return [_link_out(z) for z in zeilen]


@intern_router.delete("/freigabelinks/{link_id}")
def freigabelink_widerrufen(request, link_id: UUID):
    """Zieht einen Freigabelink zurück (`revoked_at`). Idempotent.

    Gelöscht wird nichts — die Tabelle trägt No-Delete, und ein widerrufener Link
    soll nachweisbar bleiben.

    **Der Zweck ist die Zuständigkeitsgrenze.** `security.public_link` trägt die
    Links aller Bereiche, dieses Recht deckt aber nur die Belege ab. Ohne den
    `purpose`-Filter könnte, wer Angebotslinks widerrufen darf, auch fremde
    Links stilllegen — beim nächsten Verbraucher (Terminbuchung) wäre das ein
    Rechteübertritt, den niemand mehr bemerkt.
    """
    actor, _ = require(request, "invoicing", "VERSENDEN")
    if not link_service.link_widerrufen(
        actor, link_id, purpose=link_service.PURPOSE_ANGEBOT_FREIGABE
    ):
        raise HttpError(404, "Freigabelink nicht gefunden.")
    return {"detail": "Freigabelink widerrufen."}
