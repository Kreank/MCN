"""Der anmeldefreie Kundenweg „Angebot online annehmen" — und die interne Seite,
die die Links erzeugt.

Diese Datei ist der Nachweis für die Zusagen aus `api/oeffentlich.py`:

  * **Kein Orakel.** Unbekanntes, abgelaufenes und widerrufenes Token liefern
    eine **bytegleiche** Antwort. Ein bereits eingelöstes Token gehört bewusst
    nicht dazu: Sein Inhaber hat den Besitz nachgewiesen und sieht den Ausgang —
    ihn stattdessen auf „ungültiger Link" laufen zu lassen, ließe ihn an seiner
    eigenen Zusage zweifeln. Entscheiden kann er trotzdem nur einmal.
  * **Kein EK, keine Marge.** Die öffentliche Antwort führt Preise (der Kunde
    soll ja entscheiden), aber `unit_cost` und `markup_percent` sind über das
    Schema nicht erreichbar. Der Negativtest prüft gegen die **konkreten Werte**
    und hat eine Gegenprobe, dass sie am Beleg wirklich stehen — sonst bestünde
    er auch, wenn niemand sie je gesetzt hätte.
  * **CSRF am POST**, Drosselung je IP, Einmal-Einlösung, und ein Angebot in
    falschem Status ist nicht entscheidbar.
  * **Es geht keine Mail raus.** Der Versand ist betrieblich gesperrt; die Sperre
    greift, BEVOR eine Nachricht entsteht.
"""
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.test import Client, override_settings

from db_core.db_context import business_transaction
from db_core.models import Notification, PublicLink, Quote, QuoteLine
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import identity as identity_service
from db_core.services import oeffentlicher_link as link_service
from db_core.services import property as property_service

from .conftest import logged_in_client, make_role_user

OEFFENTLICH = "/api/oeffentlich/angebot/{}"
ENTSCHEIDUNG = "/api/oeffentlich/angebot/{}/entscheidung"
LINK_NEU = "/api/invoicing/quotes/{}/freigabelink"
LINK_LISTE = "/api/invoicing/quotes/{}/freigabelinks"
LINK_WEG = "/api/invoicing/freigabelinks/{}"

# Zwei unverwechselbare Zahlen, die NUR im EK bzw. im Aufschlag vorkommen. Damit
# ist der Negativtest eine echte Suche im Antworttext und nicht bloß ein Blick
# auf Feldnamen.
EK = Decimal("13.37")
AUFSCHLAG = Decimal("77.7700")

# Freie Adressen je Test — die Drossel zählt pro IP, und ein Test soll den
# nächsten nicht aussperren.
PROXY = override_settings(MCN_TRUST_PROXY_IP=True)


def _oeffentlich(methode, url, ip="198.51.100.7", csrf=False, **kwargs):
    client = Client(enforce_csrf_checks=csrf)
    return getattr(client, methode)(url, HTTP_X_REAL_IP=ip, **kwargs)


def _angebot(app_user, *, mit_auftrag=True, mit_email=True, versenden=True):
    """Ein Angebot mit EK/Aufschlag an den Positionen, optional mit Empfänger."""
    obj = property_service.create_property(
        app_user.id, name="Freigabe-Objekt", property_type="WEG",
        street="Musterweg 3", postal_code="10115", city="Berlin",
    )
    kunde = identity_service.create_person(
        app_user.id, first_name="Konrad", last_name="Kunde"
    )
    if mit_email:
        identity_service.add_contact_point(
            app_user.id, kunde.id, contact_type="EMAIL",
            value="konrad.kunde@example.test", is_primary=True,
        )
    order = None
    if mit_auftrag:
        order = auftrag_service.create_work_order(
            app_user.id, property_id=obj.id, title="Heizung erneuern"
        )
        auftrag_service.add_work_order_party(
            app_user.id, work_order_id=order.id, party_id=kunde.id,
            role="INVOICE_RECIPIENT", is_primary=True,
        )
    quote = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Heizungstausch",
        lines=[{
            "line_type": "MATERIAL", "description": "Brennwerttherme",
            "quantity": 1, "unit": "Stk", "unit_price": "2400.00",
            "tax_code": "DE_19", "unit_cost": str(EK),
            "markup_percent": str(AUFSCHLAG),
        }],
    )
    if order is not None:
        with business_transaction(app_user.id):
            Quote.objects.filter(id=quote.id).update(work_order_id=order.id)
    if versenden:
        quote = beleg_service.send_quote(app_user.id, quote_id=quote.id)
    quote.refresh_from_db()
    return quote


def _link(app_user, quote, tage=14):
    return link_service.link_erzeugen(
        app_user.id,
        purpose=link_service.PURPOSE_ANGEBOT_FREIGABE,
        target_type=link_service.ZIEL_ANGEBOT,
        target_id=quote.id,
        gueltig_bis=datetime.now(dt_timezone.utc) + timedelta(days=tage),
    )


def _abgelaufener_link(app_user, quote, klartext):
    """Wie ein Link nach Fristablauf aussieht. Direkt per INSERT, weil
    `expires_at` nach dem Anlegen unveränderlich ist (Guard-Trigger)."""
    return PublicLink.objects.create(
        id=uuid.uuid4(),
        purpose=link_service.PURPOSE_ANGEBOT_FREIGABE,
        target_type=link_service.ZIEL_ANGEBOT,
        target_id=quote.id,
        token_hash=link_service._hash(klartext),
        expires_at=datetime.now(dt_timezone.utc) - timedelta(minutes=1),
        created_by_id=app_user.id,
        use_count=0,
        version=1,
    )


# ---------------------------------------------------------------------------
# Öffentliches Lesen
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@PROXY
def test_kunde_sieht_sein_angebot(app_user):
    quote = _angebot(app_user)
    _, token = _link(app_user, quote)

    r = _oeffentlich("get", OEFFENTLICH.format(token))
    assert r.status_code == 200, r.content
    daten = r.json()
    assert daten["quote_number"] == quote.quote_number
    assert daten["title"] == "Heizungstausch"
    assert daten["entscheidbar"] is True
    assert daten["ausgang"] is None
    assert daten["positionen"][0]["description"] == "Brennwerttherme"
    assert daten["positionen"][0]["unit_price"] == "2400.00"
    assert daten["gross_total"] is not None
    # Das csrftoken-Cookie muss gesetzt sein, sonst kann der Kunde nicht POSTen.
    assert "csrftoken" in r.cookies


@pytest.mark.django_db
@PROXY
def test_oeffentliche_antwort_traegt_weder_ek_noch_marge(app_user):
    """Der Kunde sieht Preise — aber nie den Einkauf und nie den Aufschlag.

    Geprüft wird gegen die konkreten Werte im Rohtext, nicht gegen Feldnamen:
    Eine umbenannte Spalte fiele sonst durch. Die Gegenprobe stellt sicher, dass
    die Werte am Beleg überhaupt existieren — ohne sie bewiese der Test nichts.
    """
    quote = _angebot(app_user)
    zeile = QuoteLine.objects.get(quote_id=quote.id)
    # Gegenprobe: Der Beleg FÜHRT die Werte.
    assert zeile.unit_cost == EK
    assert zeile.markup_percent == AUFSCHLAG

    _, token = _link(app_user, quote)
    r = _oeffentlich("get", OEFFENTLICH.format(token))
    roh = r.content.decode("utf-8")

    assert "13.37" not in roh
    assert "77.77" not in roh
    assert "unit_cost" not in roh
    assert "markup_percent" not in roh
    # Auch nicht auf dem Umweg über verschachtelte Strukturen.
    assert "labour_net_amount" not in roh
    assert "tax_code" not in roh
    # Gegenprobe zur Gegenprobe: Der VK, den der Kunde sehen SOLL, ist da.
    assert "2400.00" in roh


@pytest.mark.django_db
@PROXY
def test_ungueltige_token_antworten_alle_gleich(app_user):
    """Drei Gründe, EINE Antwort — bytegleich, nicht nur „ähnlich".

    Unbekannt, abgelaufen und widerrufen sind die Menge, in der Raten überhaupt
    etwas brächte. Ein eingelöster Token gehört bewusst nicht dazu (eigener Test
    darunter): Sein Inhaber hat den Besitz nachgewiesen.
    """
    quote = _angebot(app_user)

    # (1) unbekannt
    antworten = [_oeffentlich("get", OEFFENTLICH.format("Z" * 43))]

    # (2) abgelaufen
    _abgelaufener_link(app_user, quote, "Y" * 43)
    antworten.append(_oeffentlich("get", OEFFENTLICH.format("Y" * 43)))

    # (3) widerrufen
    zeile, token_w = _link(app_user, quote)
    link_service.link_widerrufen(app_user.id, zeile.id)
    antworten.append(_oeffentlich("get", OEFFENTLICH.format(token_w)))

    codes = {r.status_code for r in antworten}
    koerper = {r.content for r in antworten}
    assert codes == {404}, codes
    assert len(koerper) == 1, koerper
    # Der Wortlaut darf nichts über den Grund verraten.
    einziger = antworten[0].json()["detail"].lower()
    for verraeter in ("abgelaufen", "widerrufen", "verwendet", "unbekannt"):
        assert verraeter not in einziger


@pytest.mark.django_db
@PROXY
def test_eingeloester_link_zeigt_den_ausgang_statt_ins_leere_zu_laufen(app_user):
    """Nach der Zusage darf der Kunde die Seite neu laden und sein Ergebnis sehen.

    Die Gegenprobe steht daneben: Handeln kann er nicht mehr.
    """
    quote = _angebot(app_user)
    zeile, token = _link(app_user, quote)

    erste = _oeffentlich(
        "post", ENTSCHEIDUNG.format(token),
        data={"entscheidung": "ANGENOMMEN"}, content_type="application/json",
    )
    assert erste.status_code == 200

    # Neuladen — derselbe Link, jetzt eingelöst.
    r = _oeffentlich("get", OEFFENTLICH.format(token))
    assert r.status_code == 200, r.content
    daten = r.json()
    assert daten["entscheidbar"] is False
    assert daten["ausgang"] == "ANGENOMMEN"
    assert daten["ausgang_am"] is not None
    # Der Beleg selbst ist weiterhin da — der Kunde soll nachlesen können.
    assert daten["positionen"][0]["description"] == "Brennwerttherme"
    zeile.refresh_from_db()
    assert zeile.used_at is not None


@pytest.mark.django_db
@PROXY
def test_abgelaufener_link_zeigt_den_ausgang_NICHT_mehr(app_user):
    """Die Lesbarkeit endet an der Frist, nicht an der Einlösung."""
    quote = _angebot(app_user)
    zeile = _abgelaufener_link(app_user, quote, "W" * 43)
    with business_transaction(app_user.id):
        PublicLink.objects.filter(id=zeile.id).update(
            used_at=datetime.now(dt_timezone.utc) - timedelta(minutes=2),
            use_count=1,
        )
    assert _oeffentlich("get", OEFFENTLICH.format("W" * 43)).status_code == 404


@pytest.mark.django_db
@PROXY
def test_verschwundenes_ziel_ist_dieselbe_antwort(app_user):
    """Zeigt der Link (weiche Referenz!) ins Leere, ist das für den Kunden
    derselbe Zustand — kein Sonderfehler, der die Existenz von Objekten verrät."""
    zeile, token = link_service.link_erzeugen(
        app_user.id,
        purpose=link_service.PURPOSE_ANGEBOT_FREIGABE,
        target_type=link_service.ZIEL_ANGEBOT,
        target_id=uuid.uuid4(),
        gueltig_bis=datetime.now(dt_timezone.utc) + timedelta(days=1),
    )
    r = _oeffentlich("get", OEFFENTLICH.format(token))
    assert r.status_code == 404


@pytest.mark.django_db
@PROXY
def test_intern_entschiedenes_angebot_zeigt_den_ausgang(app_user):
    """Hat der Betrieb bereits entschieden, während der Link noch frisch ist,
    zeigt die Seite den Ausgang — statt eine zweite Entscheidung anzubieten."""
    quote = _angebot(app_user)
    _, token = _link(app_user, quote)
    beleg_service.set_quote_status(
        app_user.id, quote_id=quote.id, to_status="ANGENOMMEN"
    )

    r = _oeffentlich("get", OEFFENTLICH.format(token))
    assert r.status_code == 200
    assert r.json()["entscheidbar"] is False
    assert r.json()["ausgang"] == "ANGENOMMEN"

    r2 = _oeffentlich(
        "post", ENTSCHEIDUNG.format(token),
        data={"entscheidung": "ABGELEHNT"}, content_type="application/json",
    )
    assert r2.status_code == 422
    assert "abgeschlossen" in r2.json()["detail"]


# ---------------------------------------------------------------------------
# Die Entscheidung
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@PROXY
def test_annahme_setzt_den_status_wirklich(app_user):
    quote = _angebot(app_user)
    zeile, token = _link(app_user, quote)

    r = _oeffentlich(
        "post", ENTSCHEIDUNG.format(token),
        data={"entscheidung": "ANGENOMMEN"}, content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["ausgang"] == "ANGENOMMEN"

    quote.refresh_from_db()
    assert quote.status == "ANGENOMMEN"
    zeile.refresh_from_db()
    assert zeile.used_at is not None
    assert zeile.use_count == 1


@pytest.mark.django_db
@PROXY
def test_ablehnung_geht_ebenso(app_user):
    quote = _angebot(app_user)
    _, token = _link(app_user, quote)
    r = _oeffentlich(
        "post", ENTSCHEIDUNG.format(token),
        data={"entscheidung": "ABGELEHNT"}, content_type="application/json",
    )
    assert r.status_code == 200
    quote.refresh_from_db()
    assert quote.status == "ABGELEHNT"


@pytest.mark.django_db
@PROXY
def test_der_kunde_darf_nicht_ablaufen_lassen(app_user):
    """ABGELAUFEN ist eine Feststellung des Betriebs, keine Kundenentscheidung."""
    quote = _angebot(app_user)
    _, token = _link(app_user, quote)
    r = _oeffentlich(
        "post", ENTSCHEIDUNG.format(token),
        data={"entscheidung": "ABGELAUFEN"}, content_type="application/json",
    )
    assert r.status_code == 422
    quote.refresh_from_db()
    assert quote.status == "VERSENDET"


@pytest.mark.django_db
@PROXY
def test_zweite_entscheidung_prallt_ab(app_user):
    """Genau EINE Erklärung je Angebot — auch wenn der Link lesbar bleibt.

    Gehalten wird das an zwei Stellen: `single_use` in der Datenbank und der
    Belegstatus im Service. Der zweite Versuch bekommt einen ehrlichen Grund —
    er hat den Besitz des Tokens ja bereits nachgewiesen, hier ist nichts mehr
    zu verschweigen.
    """
    quote = _angebot(app_user)
    zeile, token = _link(app_user, quote)
    erste = _oeffentlich(
        "post", ENTSCHEIDUNG.format(token),
        data={"entscheidung": "ANGENOMMEN"}, content_type="application/json",
    )
    assert erste.status_code == 200

    zweite = _oeffentlich(
        "post", ENTSCHEIDUNG.format(token),
        data={"entscheidung": "ABGELEHNT"}, content_type="application/json",
    )
    assert zweite.status_code == 422
    assert "abgeschlossen" in zweite.json()["detail"]
    quote.refresh_from_db()
    assert quote.status == "ANGENOMMEN"
    # Und der Link ist genau einmal verbraucht worden.
    zeile.refresh_from_db()
    assert zeile.use_count == 1


@pytest.mark.django_db
@PROXY
def test_ohne_csrf_kein_schreiben(app_user):
    quote = _angebot(app_user)
    _, token = _link(app_user, quote)
    r = _oeffentlich(
        "post", ENTSCHEIDUNG.format(token), csrf=True,
        data={"entscheidung": "ANGENOMMEN"}, content_type="application/json",
    )
    assert r.status_code == 403
    quote.refresh_from_db()
    assert quote.status == "VERSENDET"


@pytest.mark.django_db
@PROXY
def test_entscheidung_geht_auf_das_konto_des_systemakteurs(app_user):
    """Der Audit-Trail nennt den Automaten, nicht einen zufälligen Menschen —
    und das Statusprotokoll nennt den Link, über den entschieden wurde."""
    from db_core.models import StatusChange

    quote = _angebot(app_user)
    zeile, token = _link(app_user, quote)
    _oeffentlich(
        "post", ENTSCHEIDUNG.format(token),
        data={"entscheidung": "ANGENOMMEN"}, content_type="application/json",
    )

    eintrag = StatusChange.objects.filter(
        entity="quote", entity_id=quote.id, to_status="ANGENOMMEN"
    ).first()
    assert eintrag is not None
    assert eintrag.changed_by_id == link_service.SYSTEMAKTEUR_ID
    assert str(zeile.id) in (eintrag.reason or "")


@pytest.mark.django_db
@PROXY
def test_entscheidung_benachrichtigt_die_zustaendigen(app_user):
    """Empfänger ist, wer `invoicing/VERSENDEN` trägt — also das Angebot ohnehin
    sehen darf. Ein Konto ohne dieses Recht bekommt nichts."""
    _, buchhaltung = make_role_user("BUCHHALTUNG")   # darf VERSENDEN
    _, monteur = make_role_user("MONTEUR")           # darf es nicht

    quote = _angebot(app_user)
    _, token = _link(app_user, quote)
    _oeffentlich(
        "post", ENTSCHEIDUNG.format(token),
        data={"entscheidung": "ANGENOMMEN"}, content_type="application/json",
    )

    meldung = Notification.objects.filter(
        recipient_id=buchhaltung.id, kind="ANGEBOT_ANGENOMMEN"
    ).first()
    assert meldung is not None
    assert quote.quote_number in meldung.title
    assert meldung.target_type == "invoicing.quote"
    assert meldung.target_id == quote.id
    assert not Notification.objects.filter(recipient_id=monteur.id).exists()


@pytest.mark.django_db
@PROXY
@override_settings(MCN_PUBLIC_LINK_IP_THRESHOLD=3)
def test_drosselung_greift_nach_n_versuchen(app_user):
    """Ohne Bremse ist der Link ein Rateautomat. Gezählt werden Fehlschläge."""
    for i in range(3):
        r = _oeffentlich("get", OEFFENTLICH.format("Q" * 43), ip="203.0.113.9")
        assert r.status_code == 404, i
    r = _oeffentlich("get", OEFFENTLICH.format("Q" * 43), ip="203.0.113.9")
    assert r.status_code == 429
    assert "später" in r.json()["detail"]


@pytest.mark.django_db
@PROXY
@override_settings(MCN_PUBLIC_LINK_IP_THRESHOLD=3)
def test_drosselung_zaehlt_keine_erfolgreichen_aufrufe(app_user):
    """Ein Kunde, der seine Seite fünfmal lädt, sperrt sich nicht selbst aus."""
    quote = _angebot(app_user)
    _, token = _link(app_user, quote)
    for _ in range(5):
        assert _oeffentlich(
            "get", OEFFENTLICH.format(token), ip="203.0.113.11"
        ).status_code == 200


@pytest.mark.django_db
@PROXY
@override_settings(MCN_PUBLIC_LINK_IP_THRESHOLD=3)
def test_drosselung_haengt_an_der_ip(app_user):
    for _ in range(4):
        _oeffentlich("get", OEFFENTLICH.format("R" * 43), ip="203.0.113.20")
    frisch = _oeffentlich("get", OEFFENTLICH.format("R" * 43), ip="203.0.113.21")
    assert frisch.status_code == 404


# ---------------------------------------------------------------------------
# Intern: Link erzeugen, auflisten, widerrufen
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_link_erzeugen_gibt_die_url_genau_einmal(app_user):
    quote = _angebot(app_user)
    client = logged_in_client("ADMINISTRATION")
    r = client.post(
        LINK_NEU.format(quote.id), data={"gueltig_tage": 7},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    url = r.json()["url"]
    assert "/angebot/" in url
    token = url.rsplit("/", 1)[-1]

    # Die Liste kennt den Klartext nicht mehr.
    liste = client.get(LINK_LISTE.format(quote.id))
    assert liste.status_code == 200
    assert len(liste.json()) == 1
    assert "url" not in liste.json()[0]
    assert token not in liste.content.decode("utf-8")
    assert liste.json()[0]["offen"] is True


@pytest.mark.django_db
def test_link_nur_fuer_versendete_angebote(app_user):
    quote = _angebot(app_user, versenden=False)
    client = logged_in_client("ADMINISTRATION")
    r = client.post(
        LINK_NEU.format(quote.id), data={}, content_type="application/json"
    )
    assert r.status_code == 422
    assert "versendetes" in r.json()["detail"]
    assert not PublicLink.objects.filter(target_id=quote.id).exists()


@pytest.mark.django_db
def test_link_erzeugen_verlangt_das_versendenrecht(app_user):
    """`invoicing/VERSENDEN` — wer ein Angebot hinausgeben darf, darf auch den
    Weg dafür erzeugen. Ein reines Änderungsrecht genügt nicht."""
    quote = _angebot(app_user)
    # NUR_LESEN trägt LESEN, aber kein VERSENDEN.
    client = logged_in_client("NUR_LESEN")
    r = client.post(
        LINK_NEU.format(quote.id), data={}, content_type="application/json"
    )
    assert r.status_code == 403
    assert not PublicLink.objects.filter(target_id=quote.id).exists()


@pytest.mark.django_db
@PROXY
def test_widerruf_macht_den_link_sofort_unbrauchbar(app_user):
    quote = _angebot(app_user)
    client = logged_in_client("ADMINISTRATION")
    r = client.post(
        LINK_NEU.format(quote.id), data={}, content_type="application/json"
    )
    token = r.json()["url"].rsplit("/", 1)[-1]
    link_id = r.json()["id"]

    assert _oeffentlich("get", OEFFENTLICH.format(token)).status_code == 200
    weg = client.delete(LINK_WEG.format(link_id))
    assert weg.status_code == 200
    assert _oeffentlich("get", OEFFENTLICH.format(token)).status_code == 404
    # Widerrufen heißt stilllegen, nicht löschen.
    assert PublicLink.objects.filter(id=link_id).exists()


@pytest.mark.django_db
def test_widerruf_unbekannter_link_ist_404(app_user):
    client = logged_in_client("ADMINISTRATION")
    assert client.delete(LINK_WEG.format(uuid.uuid4())).status_code == 404


# ---------------------------------------------------------------------------
# Mailversand: fertig verdrahtet, betrieblich gesperrt
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_mailversand_ist_gesperrt(app_user):
    """⚠️ Der Kern der Sicherung: Solange `MCN_PUBLIC_LINK_MAIL` nicht auf 1
    steht, geht nichts raus — und zwar BEVOR ein Link entsteht oder eine
    Nachricht gebaut wird."""
    quote = _angebot(app_user)
    client = logged_in_client("ADMINISTRATION")
    with patch("db_core.services.beleg_versand.send_mail") as gesendet:
        r = client.post(
            LINK_NEU.format(quote.id), data={"per_mail": True},
            content_type="application/json",
        )
    assert r.status_code == 422
    assert "nicht freigeschaltet" in r.json()["detail"]
    gesendet.assert_not_called()
    # Kein halbfertiger Link bleibt zurück.
    assert not PublicLink.objects.filter(target_id=quote.id).exists()


@pytest.mark.django_db
def test_kein_empfaenger_scheitert_mit_422(app_user):
    """Ohne Auftrag gibt es keinen ableitbaren Empfänger. Der Versuch muss sauber
    scheitern, statt ins Leere zu laufen — und darf keinen Link hinterlassen."""
    quote = _angebot(app_user, mit_auftrag=False)
    client = logged_in_client("ADMINISTRATION")
    r = client.post(
        LINK_NEU.format(quote.id), data={"per_mail": True},
        content_type="application/json",
    )
    assert r.status_code == 422
    assert "Empfänger" in r.json()["detail"]
    assert not PublicLink.objects.filter(target_id=quote.id).exists()


@pytest.mark.django_db
def test_link_ohne_mail_geht_auch_ohne_empfaenger(app_user):
    """Der Regelfall heute: Link erzeugen, kopieren, selbst verschicken."""
    quote = _angebot(app_user, mit_auftrag=False)
    client = logged_in_client("ADMINISTRATION")
    r = client.post(
        LINK_NEU.format(quote.id), data={"per_mail": False},
        content_type="application/json",
    )
    assert r.status_code == 201
    assert r.json()["mail_versandt"] is False


@pytest.mark.django_db
@override_settings(MCN_PUBLIC_LINK_MAIL_ENABLED=True)
def test_freigeschaltet_wuerde_die_richtige_mail_entstehen(app_user):
    """Nachweis, dass **nur** der Schalter fehlt — ohne echten Transportweg.

    `send_mail` ist ersetzt: Es gibt weder SMTP-Verbindung noch Postausgang.
    Geprüft wird, was der Versandpfad übergäbe: die abgeleitete Kundenadresse,
    ein Betreff mit der Angebotsnummer und der Klartext-Link im Text.
    """
    quote = _angebot(app_user)
    client = logged_in_client("ADMINISTRATION")
    with patch(
        "db_core.services.beleg_versand.send_mail", return_value=MagicMock()
    ) as gesendet:
        r = client.post(
            LINK_NEU.format(quote.id), data={"per_mail": True},
            content_type="application/json",
        )
    assert r.status_code == 201, r.content
    assert r.json()["mail_versandt"] is True
    gesendet.assert_called_once()
    kwargs = gesendet.call_args.kwargs
    assert kwargs["to_address"] == "konrad.kunde@example.test"
    assert quote.quote_number in kwargs["subject"]
    token = r.json()["url"].rsplit("/", 1)[-1]
    assert token in kwargs["body"]
    assert kwargs["is_commercial"] is True
