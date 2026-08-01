"""Rechnungsversand per E-Mail — eine veröffentlichte Rechnung als PDF-Anhang an
den Empfänger senden, protokolliert.

Reine Zustellung: die Rechnung ist bereits veröffentlicht (Snapshot + Prüf-Hash
sind die rechtliche Aufzeichnung, B-30). Es gibt daher **keinen Statuswechsel und
keine GoBD-Berührung** — der Versand ändert nichts am Beleg. Protokolliert wird
er über ``mail.send_mail`` als ``content.communication`` (channel EMAIL,
direction AUSGEHEND, is_commercial=True). Ein Sendefehler lässt die Rechnung
unberührt, wird aber sichtbar (MailSendError → 422 in der API), nie still
verschluckt.

Empfänger-Auflösung spiegelt exakt die des PDF (``beleg_pdf.render_invoice_pdf``):
primärer INVOICE_RECIPIENT, ersatzweise INVOICE_DEBTOR. Fehler werden als
ValueError gemeldet (→ 422); es werden keine personenbezogenen Werte in
Fehlermeldungen aufgenommen.
"""
from decimal import Decimal

from db_core.models import ContactPoint, DunningNotice, Invoice, Quote
from db_core.services import beleg_pdf as beleg_pdf_service
from db_core.services import buchhaltung as buchhaltung_service
from db_core.services import firma as firma_service
from db_core.services.mail import send_mail

# Empfänger wie im PDF: primärer INVOICE_RECIPIENT gewinnt, sonst INVOICE_DEBTOR.
# Reihenfolge = Priorität.
_RECIPIENT_ROLES = ("INVOICE_RECIPIENT", "INVOICE_DEBTOR")
# Schuldner der Rechnung (für den Mahnungsversand): der INVOICE_DEBTOR trägt die
# Zahlungspflicht — er wird angemahnt. Ersatzweise der INVOICE_RECIPIENT.
_DEBTOR_ROLES = ("INVOICE_DEBTOR", "INVOICE_RECIPIENT")


def _pick_party(invoice, roles):
    """Erste passende Beteiligten-Partei nach Rollen-Priorität, sonst None.

    `roles` ist eine nach Priorität geordnete Rollenfolge. Innerhalb einer Rolle
    gewinnt der als primär markierte Beteiligte, sonst irgendeiner der Rolle.
    Erwartet ein Invoice mit vorgeladenen `parties__party`.
    """
    for role in roles:
        chosen = None
        for p in invoice.parties.all():
            if p.role == role:
                chosen = p
                if p.is_primary:
                    break
        if chosen is not None:
            return chosen.party
    return None


def _recipient_party(invoice):
    """Empfängerpartei der Rechnung (Party-Objekt) oder None.

    Spiegelt die Auflösung aus beleg_pdf.render_invoice_pdf: der primäre
    INVOICE_RECIPIENT gewinnt, ersatzweise der INVOICE_DEBTOR.
    """
    return _pick_party(invoice, _RECIPIENT_ROLES)


def _debtor_party(invoice):
    """Schuldnerpartei der Rechnung (Party-Objekt) oder None.

    Für den Mahnungsversand: der INVOICE_DEBTOR gewinnt, ersatzweise der
    INVOICE_RECIPIENT.
    """
    return _pick_party(invoice, _DEBTOR_ROLES)


def primary_email(party_id):
    """Primäre laufende EMAIL einer Partei (sonst irgendeine laufende), sonst None.

    Laufend = valid_until IS NULL. Primäre zuerst, dann die jüngste Gültigkeit.
    """
    points = ContactPoint.objects.filter(
        party_id=party_id, contact_type="EMAIL", valid_until__isnull=True
    ).order_by("-is_primary", "-valid_from", "id")
    for p in points:
        value = (p.value or "").strip()
        if value:
            return value
    return None


def recipient_email(invoice):
    """Vorbelegte Empfänger-E-Mail der Rechnung (für die Dialog-Vorbelegung im UI).

    Erwartet ein Invoice mit vorgeladenen `parties__party`.
    """
    party = _recipient_party(invoice)
    return primary_email(party.id) if party is not None else None


def _body(invoice_number, company_name):
    """Knappe, sachliche Standardnachricht (Firmenname, falls gepflegt)."""
    number = invoice_number or "(ohne Nummer)"
    lines = [
        "Sehr geehrte Damen und Herren,",
        "",
        f"anbei erhalten Sie die Rechnung {number} als PDF-Dokument.",
        "",
        "Für Rückfragen stehen wir Ihnen gerne zur Verfügung.",
        "",
        "Mit freundlichen Grüßen",
    ]
    if company_name:
        lines.append(company_name)
    return "\n".join(lines)


def send_invoice_email(actor, *, invoice_id, to_address=None):
    """Versendet eine veröffentlichte Rechnung als PDF-Anhang an den Empfänger.

    `to_address` überschreibt die ermittelte Empfänger-Adresse (der Nutzer
    bestätigt/korrigiert sie im Dialog); ohne Angabe wird die primäre EMAIL der
    Empfängerpartei genommen. Ist keine Adresse ermittelbar → ValueError (422).

    Fehler:
      - Rechnung unbekannt / nicht VEROEFFENTLICHT → ValueError (422).
      - keine Empfänger-Adresse ermittelbar → ValueError (422).
      - kein Mailkonto / SMTP-/Schlüsselfehler → aus send_mail
        (ValueError bzw. MailSendError/MailKeyError), von der API als 422
        passwortfrei abgebildet.

    Gibt die protokollierte content.communication zurück (aus send_mail).
    """
    invoice = (
        Invoice.objects.filter(id=invoice_id)
        .prefetch_related("parties__party")
        .first()
    )
    if invoice is None:
        raise ValueError("Rechnung nicht gefunden.")
    if invoice.status != "VEROEFFENTLICHT":
        raise ValueError(
            "Nur veröffentlichte Rechnungen können per E-Mail versendet werden."
        )

    party = _recipient_party(invoice)
    address = (to_address or "").strip() or None
    if address is None and party is not None:
        address = primary_email(party.id)
    if not address:
        raise ValueError(
            "Für den Rechnungsempfänger ist keine E-Mail-Adresse hinterlegt. "
            "Bitte eine Adresse angeben oder im Kontakt einen "
            "E-Mail-Kommunikationsweg pflegen."
        )

    # PDF-Ausfertigung (holt die archivierte oder rendert/archiviert on-the-fly).
    pdf = beleg_pdf_service.get_or_archive_invoice_pdf(actor, invoice_id)
    if pdf is None:
        # Bei VEROEFFENTLICHT nicht zu erwarten; defensiv statt 500.
        raise ValueError("Die PDF-Ausfertigung konnte nicht erzeugt werden.")

    profile = firma_service.get_company_profile()
    company_name = (
        profile.company_name if profile and profile.company_name else None
    )
    subject = (
        f"Rechnung {invoice.invoice_number}"
        if invoice.invoice_number
        else "Rechnung"
    )
    body = _body(invoice.invoice_number, company_name)
    filename = beleg_pdf_service._safe_filename(invoice)

    return send_mail(
        actor,
        to_address=address,
        subject=subject,
        body=body,
        attachments=[(filename, pdf, "application/pdf")],
        party_id=party.id if party is not None else None,
        is_commercial=True,
    )


# --- Mahnungsversand -------------------------------------------------------
# Eine bereits ausgestellte dunning_notice (Zahlungserinnerung/Mahnung) wird als
# E-Mail an den Schuldner der Rechnung zugestellt, mit der Rechnung als PDF-Anhang.
# Reine Zustellung — kein Statuswechsel an Rechnung/dunning_notice, keine
# GoBD-Berührung. Betreff/Body richten sich nach dem Label der Mahnstufe
# (Zahlungserinnerung = sachlich-freundlich, Mahnung = bestimmter). Gebühren/
# Zinsen werden NICHT erfunden (B-22: fee/interest_note sind NULL).

def debtor_email(invoice):
    """Vorbelegte Schuldner-E-Mail der Rechnung (für die Dialog-Vorbelegung im UI).

    Erwartet ein Invoice mit vorgeladenen `parties__party`.
    """
    party = _debtor_party(invoice)
    return primary_email(party.id) if party is not None else None


def _open_amount(invoice):
    """Der offene Betrag der Rechnung — aus der EINEN Rechenstelle, sonst None.

    Hier stand einmal eine **zweite** Ableitung (`gross_total − Σ PAYMENT_SIGN·amount`).
    Sie kannte die **Gutschriften nicht**: Nach einer Teilgutschrift sagten Bildschirm,
    Mahnlauf und offene Posten 285,60 € offen, der versendete Mahntext dagegen
    880,60 € — der Kunde wurde über Geld gemahnt, das das Haus selbst erlassen hatte.
    Der Betrag im Brief kommt deshalb aus `buchhaltung.zahlungsspiegel`, genau wie
    jede andere Oberfläche. Es gibt keine zweite Definition von „offen".
    """
    if invoice.gross_total is None:
        return None
    annotiert = buchhaltung_service.mit_zahlungsstand(
        Invoice.objects.filter(id=invoice.id)
    ).first()
    if annotiert is None:
        return None
    return buchhaltung_service.zahlungsspiegel(annotiert)["open_amount"]


def _format_amount(amount, currency):
    """Deutscher Betrag mit Währung (nur für die Textnachricht)."""
    q = amount.quantize(Decimal("0.01"))
    # 1234567.89 -> "1.234.567,89" (Tausenderpunkt, Dezimalkomma).
    s = f"{q:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    return f"{s} {currency}"


def _dunning_is_reminder(label):
    """True für eine (freundliche) Zahlungserinnerung, False für eine (bestimmte)
    Mahnung. Fällt auf den freundlichen Ton zurück, wenn das Label unklar ist."""
    return "mahnung" not in (label or "").lower()


def _dunning_subject(label, invoice_number):
    """Betreff je Stufe: „{Stufen-Label} zu Rechnung {Nummer}"."""
    number = invoice_number or "(ohne Nummer)"
    stufe = label or "Zahlungserinnerung"
    return f"{stufe} zu Rechnung {number}"


def _dunning_body(label, invoice_number, company_name, open_amount, currency):
    """Standard-Body je Stufe (Ton nach Label). Kein Erfinden von Gebühren/Zinsen."""
    number = invoice_number or "(ohne Nummer)"
    betrag_satz = None
    if open_amount is not None and open_amount > 0:
        betrag_satz = (
            f"Der noch offene Betrag beträgt {_format_amount(open_amount, currency)}."
        )

    if _dunning_is_reminder(label):
        lines = ["Sehr geehrte Damen und Herren,", ""]
        lines.append(
            f"zu unserer Rechnung {number} liegt uns bisher kein vollständiger "
            "Zahlungseingang vor."
        )
        if betrag_satz:
            lines.append(betrag_satz)
        lines += [
            "",
            "Sollte sich Ihre Zahlung mit diesem Schreiben überschnitten haben, "
            "betrachten Sie diese Erinnerung bitte als gegenstandslos. Andernfalls "
            "bitten wir Sie, den offenen Betrag zeitnah auszugleichen. Die Rechnung "
            "ist diesem Schreiben als PDF beigefügt.",
            "",
            "Für Rückfragen stehen wir Ihnen gerne zur Verfügung.",
            "",
            "Mit freundlichen Grüßen",
        ]
    else:
        lines = ["Sehr geehrte Damen und Herren,", ""]
        lines.append(
            f"trotz unserer vorangegangenen Zahlungserinnerung ist die Rechnung "
            f"{number} weiterhin offen."
        )
        if betrag_satz:
            lines.append(betrag_satz)
        lines += [
            "",
            "Wir fordern Sie hiermit auf, den offenen Betrag umgehend zu begleichen. "
            "Die Rechnung ist diesem Schreiben als PDF beigefügt.",
            "",
            "Mit freundlichen Grüßen",
        ]

    if company_name:
        lines.append(company_name)
    return "\n".join(lines)


def send_dunning_email(actor, *, dunning_notice_id, to_address=None):
    """Versendet eine ausgestellte Mahnung/Zahlungserinnerung als E-Mail an den
    Schuldner, mit der Rechnung als PDF-Anhang.

    Reine Zustellung — kein Statuswechsel an Rechnung oder dunning_notice, keine
    GoBD-Berührung. Betreff/Body richten sich nach dem Label der Mahnstufe.
    Protokolliert über content.communication (channel EMAIL, direction AUSGEHEND,
    is_commercial=True), verknüpft mit dem Schuldner.

    `to_address` überschreibt die ermittelte Schuldner-Adresse (der Nutzer
    bestätigt/korrigiert sie im Dialog); ohne Angabe wird die primäre EMAIL der
    Schuldnerpartei genommen.

    Fehler:
      - Mahnung unbekannt → LookupError (→ 404 in der API).
      - Rechnung nicht (mehr) veröffentlicht → ValueError (422).
      - keine Empfänger-Adresse ermittelbar → ValueError (422).
      - kein Mailkonto / SMTP-/Schlüsselfehler → aus send_mail
        (ValueError bzw. MailSendError/MailKeyError), von der API als 422
        passwortfrei abgebildet.

    Gibt die protokollierte content.communication zurück (aus send_mail).
    """
    notice = (
        DunningNotice.objects.filter(id=dunning_notice_id)
        .select_related("level")
        .prefetch_related("invoice__parties__party")
        .first()
    )
    if notice is None:
        raise LookupError("Mahnung nicht gefunden.")
    invoice = notice.invoice
    if invoice.status != "VEROEFFENTLICHT":
        raise ValueError(
            "Mahnungen können nur zu veröffentlichten Rechnungen versendet werden."
        )

    party = _debtor_party(invoice)
    address = (to_address or "").strip() or None
    if address is None and party is not None:
        address = primary_email(party.id)
    if not address:
        raise ValueError(
            "Für den Rechnungsschuldner ist keine E-Mail-Adresse hinterlegt. "
            "Bitte eine Adresse angeben oder im Kontakt einen "
            "E-Mail-Kommunikationsweg pflegen."
        )

    # PDF-Ausfertigung der Rechnung (holt die archivierte oder rendert/archiviert).
    pdf = beleg_pdf_service.get_or_archive_invoice_pdf(actor, invoice.id)
    if pdf is None:
        # Bei VEROEFFENTLICHT nicht zu erwarten; defensiv statt 500.
        raise ValueError("Die PDF-Ausfertigung der Rechnung konnte nicht erzeugt werden.")

    profile = firma_service.get_company_profile()
    company_name = (
        profile.company_name if profile and profile.company_name else None
    )
    label = notice.level.label
    subject = _dunning_subject(label, invoice.invoice_number)
    body = _dunning_body(
        label, invoice.invoice_number, company_name,
        _open_amount(invoice), invoice.currency,
    )
    filename = beleg_pdf_service._safe_filename(invoice)

    return send_mail(
        actor,
        to_address=address,
        subject=subject,
        body=body,
        attachments=[(filename, pdf, "application/pdf")],
        party_id=party.id if party is not None else None,
        is_commercial=True,
    )


# --- Angebotsversand -------------------------------------------------------
# Ein Angebot hat KEINE eigenen Beteiligten (kein quote_party). Der Empfänger
# wird best-effort über den optionalen Auftrag abgeleitet (INVOICE_RECIPIENT,
# ersatzweise PRINCIPAL) — dieselbe Auflösung wie im Angebots-PDF
# (beleg_pdf.quote_recipient_party). Ist keiner ermittelbar, muss der Nutzer die
# Adresse im Dialog eintragen (to_address).

def quote_recipient_email(quote):
    """Vorbelegte Empfänger-E-Mail eines Angebots (für die Dialog-Vorbelegung).

    Erwartet ein Quote mit vorgeladenem ``work_order__parties__party``.
    """
    party = beleg_pdf_service.quote_recipient_party(quote)
    return primary_email(party.id) if party is not None else None


def _quote_body(quote_number, company_name):
    """Knappe, sachliche Standardnachricht zum Angebot (Firmenname, falls gepflegt)."""
    number = quote_number or "(ohne Nummer)"
    lines = [
        "Sehr geehrte Damen und Herren,",
        "",
        f"anbei erhalten Sie unser Angebot {number} als PDF-Dokument.",
        "",
        "Wir freuen uns auf Ihre Rückmeldung und stehen für Rückfragen gerne "
        "zur Verfügung.",
        "",
        "Mit freundlichen Grüßen",
    ]
    if company_name:
        lines.append(company_name)
    return "\n".join(lines)


def send_quote_email(actor, *, quote_id, to_address=None):
    """Versendet ein versendetes Angebot als PDF-Anhang an den Empfänger.

    Spiegelbildlich zu send_invoice_email: reine Zustellung — kein Statuswechsel,
    keine GoBD-Berührung (das Angebot ist mit dem Versand bereits festgeschrieben,
    B-30). Protokolliert über content.communication (channel EMAIL, direction
    AUSGEHEND, is_commercial=True).

    `to_address` überschreibt die ermittelte Adresse (der Nutzer bestätigt/
    korrigiert sie im Dialog); ohne Angabe wird die primäre EMAIL der abgeleiteten
    Empfängerpartei genommen.

    Fehler:
      - Angebot unbekannt / nicht VERSENDET → ValueError (422).
      - keine Empfänger-Adresse ermittelbar → ValueError (422).
      - kein Mailkonto / SMTP-/Schlüsselfehler → aus send_mail
        (ValueError bzw. MailSendError/MailKeyError), von der API als 422
        passwortfrei abgebildet.

    Gibt die protokollierte content.communication zurück (aus send_mail).
    """
    quote = (
        Quote.objects.filter(id=quote_id)
        .select_related("work_order")
        .prefetch_related("work_order__parties__party")
        .first()
    )
    if quote is None:
        raise ValueError("Angebot nicht gefunden.")
    if quote.status != "VERSENDET":
        raise ValueError(
            "Nur versendete Angebote können per E-Mail versendet werden."
        )

    party = beleg_pdf_service.quote_recipient_party(quote)
    address = (to_address or "").strip() or None
    if address is None and party is not None:
        address = primary_email(party.id)
    if not address:
        raise ValueError(
            "Für den Angebotsempfänger ist keine E-Mail-Adresse hinterlegt. "
            "Bitte eine Adresse angeben oder im zugehörigen Auftrag/Kontakt einen "
            "E-Mail-Kommunikationsweg pflegen."
        )

    # PDF-Ausfertigung (holt die archivierte oder rendert/archiviert on-the-fly).
    pdf = beleg_pdf_service.get_or_archive_quote_pdf(actor, quote_id)
    if pdf is None:
        # Bei VERSENDET nicht zu erwarten; defensiv statt 500.
        raise ValueError("Die PDF-Ausfertigung konnte nicht erzeugt werden.")

    profile = firma_service.get_company_profile()
    company_name = (
        profile.company_name if profile and profile.company_name else None
    )
    subject = (
        f"Angebot {quote.quote_number}" if quote.quote_number else "Angebot"
    )
    body = _quote_body(quote.quote_number, company_name)
    filename = beleg_pdf_service._safe_quote_filename(quote)

    return send_mail(
        actor,
        to_address=address,
        subject=subject,
        body=body,
        attachments=[(filename, pdf, "application/pdf")],
        party_id=party.id if party is not None else None,
        is_commercial=True,
    )


# --- Freigabelink per E-Mail (fertig verdrahtet, betrieblich noch gesperrt) --
#
# ⚠️ **Es geht hier heute keine Mail raus, und das ist Absicht.**
# `MCN_PUBLIC_LINK_MAIL_ENABLED` (env `MCN_PUBLIC_LINK_MAIL`) steht auf 0. Bis
# der Betrieb ihn bewusst umlegt, verweigert `_freigabe_versand_erlaubt()` den
# Versand mit einer klaren 422-Meldung — BEVOR eine Verbindung aufgebaut oder
# eine Nachricht gebaut wird. Betreff, Text und der ganze Weg dahinter sind
# fertig; es fehlt genau diese eine Freischaltung.
#
# Warum ein eigener Schalter neben `MCN_EMAIL_BACKEND`: Der Backend-Schalter gilt
# für alles (Rechnung, Mahnung, Passwort-Reset). Wer ihn eines Tages auf SMTP
# stellt, um Rechnungen zu versenden, machte sonst zugleich einen fabrikneuen
# Kundenversand an echte Adressen scharf, ohne das je entschieden zu haben.

class FreigabeVersandGesperrt(ValueError):
    """Der Versand des Freigabelinks ist betrieblich nicht freigeschaltet (→ 422)."""


def _freigabe_versand_erlaubt():
    from django.conf import settings

    if not getattr(settings, "MCN_PUBLIC_LINK_MAIL_ENABLED", False):
        raise FreigabeVersandGesperrt(
            "Der E-Mail-Versand des Freigabelinks ist nicht freigeschaltet. "
            "Der Link wurde erzeugt und kann kopiert und selbst verschickt "
            "werden. Freischaltung: MCN_PUBLIC_LINK_MAIL=1 in der Umgebung."
        )


def _freigabe_quote(quote_id):
    quote = (
        Quote.objects.filter(id=quote_id)
        .select_related("work_order")
        .prefetch_related("work_order__parties__party")
        .first()
    )
    if quote is None:
        raise ValueError("Angebot nicht gefunden.")
    if quote.status != "VERSENDET":
        raise ValueError(
            "Nur ein versendetes Angebot kann online zur Freigabe gestellt werden."
        )
    return quote


def freigabe_empfaenger(quote_id, *, to_address=None):
    """Prüft den Versandweg VOR dem Erzeugen des Links und gibt `(quote, party,
    adresse)` zurück.

    Zwei Fehlerfälle, beide 422 und beide ohne Nebenwirkung:

      * **Kein ableitbarer Empfänger.** Ein Angebot hat keine eigenen
        Beteiligten; der Empfänger kommt über den *optionalen* Auftrag
        (INVOICE_RECIPIENT, ersatzweise PRINCIPAL). Ohne Auftrag gibt es keinen —
        dann muss der Nutzer eine Adresse eintragen, sonst liefe der Versand ins
        Leere und niemand erführe es.
      * **Der Versand ist gesperrt** (siehe oben).

    Die Prüfung läuft absichtlich, bevor ein Link entsteht: Ein Link, dessen
    Klartext in einer Fehlerantwort verlorengeht, wäre unbrauchbar und stünde
    trotzdem für 14 Tage in der Datenbank.
    """
    quote = _freigabe_quote(quote_id)
    party = beleg_pdf_service.quote_recipient_party(quote)
    address = (to_address or "").strip() or None
    if address is None and party is not None:
        address = primary_email(party.id)
    if not address:
        raise ValueError(
            "Für dieses Angebot ist kein Empfänger ermittelbar (es hängt an "
            "keinem Auftrag mit Rechnungsempfänger oder dort ist keine "
            "E-Mail-Adresse gepflegt). Bitte eine Adresse angeben oder den "
            "Link kopieren und selbst verschicken."
        )
    _freigabe_versand_erlaubt()
    return quote, party, address


def _freigabe_body(quote, link_url, company_name, gueltig_bis):
    """Sachliche Standardnachricht mit dem Freigabelink (Stil wie `_quote_body`)."""
    number = quote.quote_number or "(ohne Nummer)"
    frist = gueltig_bis.strftime("%d.%m.%Y") if gueltig_bis else None
    lines = [
        "Sehr geehrte Damen und Herren,",
        "",
        f"unser Angebot {number} können Sie online einsehen und direkt "
        "annehmen oder ablehnen:",
        "",
        link_url,
        "",
        "Der Link gehört nur zu diesem Angebot und erlaubt nichts weiter als "
        "diese eine Entscheidung.",
    ]
    if frist:
        lines.append(f"Er ist bis zum {frist} gültig.")
    lines += [
        "",
        "Wenn Sie lieber telefonisch oder schriftlich antworten möchten, "
        "ist uns das ebenso recht.",
        "",
        "Mit freundlichen Grüßen",
    ]
    if company_name:
        lines.append(company_name)
    return "\n".join(lines)


def send_quote_freigabe_email(actor, *, quote_id, link_url, gueltig_bis=None,
                              to_address=None):
    """Versendet den Freigabelink zu einem versendeten Angebot.

    Reine Zustellung, kein Statuswechsel — das Angebot ist mit dem Versand
    bereits festgeschrieben (B-30). Protokolliert über `content.communication`
    wie jeder andere Versand.

    ⚠️ **Der Link steht im Text und landet damit in `content.communication`.**
    Das ist hier vertretbar und anders als beim Passwort-Reset (`api/auth.py`
    protokolliert dessen Link bewusst NICHT): Ein Reset-Token öffnet ein Konto,
    dieser Link erlaubt genau eine Entscheidung an genau einem Angebot, und wer
    das Protokoll lesen darf, darf das Angebot ohnehin sehen. Wem das zu weit
    geht, widerruft den Link — dafür gibt es `revoked_at`.

    Fehler: Angebot unbekannt/nicht VERSENDET → ValueError (422); kein
    Empfänger → ValueError (422); Versand nicht freigeschaltet →
    FreigabeVersandGesperrt (422); SMTP/Schlüssel → aus `send_mail`.
    """
    quote, party, address = freigabe_empfaenger(quote_id, to_address=to_address)
    # Zweite, absichtlich redundante Prüfung unmittelbar vor dem Versand: Der
    # Aufrufer könnte `freigabe_empfaenger` überspringen — diese Zeile ist die
    # letzte Stelle, an der die Sperre noch greift.
    _freigabe_versand_erlaubt()

    profile = firma_service.get_company_profile()
    company_name = (
        profile.company_name if profile and profile.company_name else None
    )
    subject = (
        f"Angebot {quote.quote_number} — Ihre Freigabe"
        if quote.quote_number
        else "Ihr Angebot — Ihre Freigabe"
    )
    body = _freigabe_body(quote, link_url, company_name, gueltig_bis)

    return send_mail(
        actor,
        to_address=address,
        subject=subject,
        body=body,
        party_id=party.id if party is not None else None,
        is_commercial=True,
    )
