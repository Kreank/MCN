"""E-Rechnung: ZUGFeRD/Factur-X (Hybrid-PDF = PDF/A-3B + eingebettetes CII-XML).

Seit 2025 gilt in Deutschland die E-Rechnungspflicht im B2B. Der Default dieses
Systems ist **ZUGFeRD/Factur-X**: ein PDF, das für den Menschen lesbar ist und
im Anhang das maschinenlesbare CII-XML (UN/CEFACT Cross Industry Invoice) trägt.
XRechnung (reines XML, B2G/Leitweg-ID) ist bewusst NICHT Teil dieses Slices.

Aufbau
------
1. ``build_cii_xml(invoice)`` erzeugt das CII-XML im Profil **EN16931**
   („Comfort"). Grundlage ist ein BT-Code-Dictionary nach EN 16931, das die
   Bibliothek ``factur-x`` (PyPI, BSD, offizielle Referenzimplementierung des
   Factur-X-Konsortiums) in CII-XML übersetzt und **gegen die offizielle XSD
   validiert** (``check_xsd=True``). Wir bauen kein XML von Hand — die XSD-
   Prüfung der Bibliothek ist die härtere Zusicherung.
2. ``render_zugferd_pdf(invoice)`` rendert das Sichtbild über **dasselbe Layout**
   wie das normale Beleg-PDF (``beleg_pdf.render_invoice_document``), nur mit
   ``enforce_compliance="PDF/A-3B"``: fpdf2 setzt dann sRGB-OutputIntent + XMP
   und verweigert nicht-eingebettete Schriften (deshalb die eingebettete
   DejaVu-TTF). Anschließend bettet ``factur-x`` das XML als ``factur-x.xml``
   mit ``AFRelationship=Alternative`` ein und schreibt die ZUGFeRD-XMP-Extension.
3. ``get_or_archive_zugferd_pdf`` archiviert die Ausfertigung beim ersten Abruf
   GoBD-fest (eigene ``link_category='E_RECHNUNG'``, eigener partieller
   UNIQUE-Index aus Migration 0059) — exakt der Ablauf des Beleg-PDF.

Datenquelle: **ausschließlich der eingefrorene ``billing_snapshot``**
(``beleg.beleg_stammdaten``). Nur für Altbelege, die vor der Snapshot-Härtung
(``SNAPSHOT_VERSION = 2``) veröffentlicht wurden, greift dort ein ehrlicher
Live-Fallback — ein nachträgliches Umschreiben des Snapshots wäre eine Änderung
am festgeschriebenen Beleg (B-30) und ist ausgeschlossen.

Ehrliche Grenzen (siehe auch der Slice-Bericht):
- Validiert wird gegen die **XSD**. Die **Schematron**-Regeln (BR-*/BR-CO-*) des
  EN16931-Profils prüft die Bibliothek nur mit einem externen Saxon-Server; der
  läuft hier nicht. Arithmetik und Pflichtfelder sichern stattdessen eigene
  Tests ab (Steuergruppen cent-genau gegen die Kopfsummen).
- **PDF/A-3B ist nicht mit veraPDF gegengeprüft.** Wir verlassen uns auf fpdf2s
  ``enforce_compliance`` (OutputIntent, XMP, eingebettete Schriften) und
  factur-x' XMP-Extension. Das ist der Stand der Technik dieser Bibliotheken,
  aber keine unabhängig zertifizierte Konformitätsaussage.
"""
import logging
from collections import OrderedDict
from decimal import ROUND_HALF_UP, Decimal

from db_core.models import Invoice, TaxCode
from db_core.services import beleg_pdf
from db_core.services.beleg import (
    FINAL_TYPE,
    SUMMENWIRKSAM,
    TEXT_TYPES,
    anrechnungen,
    anzeige_menge_preis,
    beleg_stammdaten,
    beteiligter,
    zahlungsbedingungen,
)

log = logging.getLogger(__name__)

try:  # Harte Dependency (pyproject: factur-x). Der Guard gibt nur eine klare
    from facturx import generate_cii_xml, generate_from_binary  # Fehlermeldung.
except ImportError as exc:  # pragma: no cover - Umgebungsfehler
    raise ImportError(
        "Das Paket 'factur-x' fehlt (uv sync). Es erzeugt das CII-XML und "
        "bettet es ZUGFeRD-konform ins PDF ein."
    ) from exc

E_RECHNUNG_CATEGORY = "E_RECHNUNG"
_STORAGE_PREFIX = "belege/erechnung"
_CENT = Decimal("0.01")

# Profil. EN16931 („Comfort") ist erreichbar: alle Pflichtfelder des Profils sind
# aus dem Snapshot bedienbar (Verkäufer/Käufer mit Anschrift, Positionen mit
# Menge/Einheit/Preis/Steuersatz, Steueraufteilung, Summen). BASIC wäre nur nötig,
# wenn Positionsdaten fehlten — das ist bei uns nie der Fall, jede Rechnung hat
# Positionen. Deshalb kein Fallback-Profil: lieber ein ehrlicher Fehler als ein
# stiller Profil-Downgrade, den der Empfänger nicht erwartet.
PROFIL = "en16931"

# --- UNTDID 1001 (Belegart) -------------------------------------------------
# 380 = Commercial invoice, 381 = Credit note, 384 = Corrected invoice.
#
# ABSCHLAGS-/TEIL-/SCHLUSSRECHNUNG bleiben bewusst 380: es sind echte, zahlbare
# Rechnungen über eine (Teil-)Leistung. 386 („Prepayment invoice") wäre die Art
# für eine reine VORAUSzahlungsanforderung ohne erbrachte Leistung — das ist die
# Abschlagsrechnung nach VOB/BGB gerade nicht: sie rechnet bereits erbrachte
# Leistung ab, ist offener Posten und mahnbar.
#
# ANRECHNUNG DER ABSCHLÄGE IN DER SCHLUSSRECHNUNG → NEGATIVE POSITIONEN,
# ausdrücklich NICHT BT-113 (TotalPrepaidAmount). Zwei Gründe:
#
# 1. **BT-113 ist der GEZAHLTE Betrag** („Paid amount"), nicht der berechnete.
#    Unsere Abschlagsrechnung ist ein eigener offener Posten und kann zum
#    Zeitpunkt der Schlussrechnung unbezahlt sein — die Anrechnung erfolgt
#    trotzdem (§ 14 Abs. 5 UStG). Stünde sie in BT-113, behauptete der Beleg eine
#    Zahlung, die es nicht gab.
# 2. **BT-113 mindert nur den Zahlbetrag, nicht die Steuer.** Der Beleg wiese
#    dann die volle USt der Gesamtleistung aus, obwohl die Abschläge ihre USt
#    bereits ausgewiesen (und abgeführt) haben — der Empfänger zöge die Vorsteuer
#    doppelt. § 14 Abs. 5 UStG verlangt, die Teilentgelte UND die darauf
#    entfallenden Steuerbeträge abzusetzen. Genau das leisten negative Positionen
#    je Steuersatz: BG-23 (Steueraufteilung) trägt den geminderten Betrag, BT-110
#    ist die tatsächlich geschuldete Steuer, BT-112 = BT-115 = Zahlbetrag.
#
# Damit sind Datenbank, PDF und XML cent- und vorzeichengleich. Die angerechneten
# Belege stehen zusätzlich als BG-3 (Bezug auf vorausgegangene Rechnungen) im XML.
#
# **GUTSCHRIFT UND STORNO → beide 384 (corrected invoice).** Das ist eine bewusste
# Festlegung, kein Versehen:
#
# Bei einer Belegart 381 (credit note) erwartet der Empfänger POSITIVE Beträge —
# das Vorzeichen steckt bereits in der Belegart. Unsere Kreditbelege tragen
# (aus der GoBD-Modellierung, `beleg._negated_lines`) NEGATIVE Beträge. Die
# Kombination „381 + negativ" ist zwar XSD-valide, aber die eine Variante, die ein
# Empfänger doppelt negieren kann — aus der Gutschrift würde eine Forderung.
#
# 384 („corrected invoice") ist dagegen genau das, was beide Belege sind: die
# Korrektur eines konkreten Ursprungsbelegs (reference_invoice_id, im XML als
# BG-3 mitgeführt). Bei 384 sind negative Beträge die übliche und eindeutige
# Lesart. Damit bleiben XML, PDF und Datenbank cent- UND vorzeichengleich, statt
# an einer Stelle stillschweigend umzudrehen.
_UNTDID_1001 = {
    "RECHNUNG": "380",
    "ABSCHLAGSRECHNUNG": "380",
    "TEILRECHNUNG": "380",
    "SCHLUSSRECHNUNG": "380",
    "GUTSCHRIFT": "384",
    "STORNO": "384",
}

# --- UN/CEFACT Rec. 20 (Mengeneinheiten) ------------------------------------
# `invoice_line.unit` ist ein Freitextfeld (der Anwender tippt „Stk", „Std", „m²").
# Unbekannte Einheiten fallen auf C62 („one", Stück/Einheit) zurück und lassen den
# Beleg NICHT scheitern — eine E-Rechnung darf nicht daran hängen, dass jemand
# „Rolle" statt „Stk" geschrieben hat. C62 ist der in EN16931 übliche Auffangcode.
_EINHEIT_FALLBACK = "C62"
_UNIT_CODES = {
    # Stück
    "stk": "H87", "stck": "H87", "stück": "H87", "stueck": "H87", "st": "H87",
    "x": "H87", "pcs": "H87", "piece": "H87",
    # Zeit
    "std": "HUR", "stunde": "HUR", "stunden": "HUR", "h": "HUR", "hr": "HUR",
    "min": "MIN", "minute": "MIN", "minuten": "MIN",
    "tag": "DAY", "tage": "DAY", "d": "DAY",
    "woche": "WEE", "wochen": "WEE",
    "monat": "MON", "monate": "MON",
    # Länge / Fläche / Volumen
    "m": "MTR", "meter": "MTR", "lfm": "MTR", "lfdm": "MTR", "lfd.m": "MTR",
    "mm": "MMT", "cm": "CMT", "km": "KMT",
    "m2": "MTK", "m²": "MTK", "qm": "MTK",
    "m3": "MTQ", "m³": "MTQ", "cbm": "MTQ",
    # Masse
    "kg": "KGM", "g": "GRM", "t": "TNE", "to": "TNE", "tonne": "TNE",
    # Volumen (flüssig)
    "l": "LTR", "ltr": "LTR", "liter": "LTR", "ml": "MLT",
    # Sonstiges
    "set": "SET", "satz": "SET", "paar": "PR",
    # „pauschal" hat keinen eigenen Rec-20-Code — bewusst C62 statt eines
    # geratenen Codes, den der Empfänger nicht auflösen kann.
    "psch": _EINHEIT_FALLBACK, "pausch": _EINHEIT_FALLBACK,
    "pauschal": _EINHEIT_FALLBACK, "pauschale": _EINHEIT_FALLBACK,
    "pa": _EINHEIT_FALLBACK,
}

# --- UNTDID 5305 (Steuerkategorie) ------------------------------------------
# S  = Standardsatz, E = steuerbefreit, AE = Steuerschuldnerschaft des
# Leistungsempfängers (Reverse Charge, §13b UStG). Kategorien E und AE verlangen
# einen Befreiungsgrund (BT-120) — den liefert `tax_code.mandatory_text`.
_TAX_CATEGORY = {
    "DE_19": "S",
    "DE_7": "S",
    "DE_0": "E",
    "DE_13B": "AE",
}
_TAX_CATEGORY_FALLBACK_SATZ = "S"

# SEPA-Überweisung (UNTDID 4461). Nur gesetzt, wenn eine IBAN gepflegt ist —
# ohne Bankverbindung gibt es keine sinnvolle Zahlungsart-Angabe.
_PAYMENT_MEANS_SEPA = "58"


class ERechnungError(ValueError):
    """Die E-Rechnung ist aus der Datenlage nicht erzeugbar (→ 422).

    Bewusst eine ValueError-Unterklasse: die API übersetzt ValueError ohnehin in
    422 mit dem Klartext. Ein fehlendes Firmenprofil oder ein Beleg ohne
    Empfänger ist ein Pflegefehler, kein Serverfehler.
    """


def _dec(value):
    return Decimal(str(value))


def _betrag(value):
    """Betrag als String mit zwei Nachkommastellen (CII erwartet Dezimaltext)."""
    return f"{_dec(value).quantize(_CENT, rounding=ROUND_HALF_UP)}"


def _menge(value):
    """Menge als String (numeric(15,3) — Skala aus der DB übernehmen)."""
    return f"{_dec(value).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)}"


def _prozent(value):
    return f"{_dec(value).quantize(_CENT, rounding=ROUND_HALF_UP)}"


def einheit_code(unit):
    """Freitext-Einheit → UN/CEFACT Rec. 20. Unbekannt ⇒ C62 (kein Fehler)."""
    if not unit:
        return _EINHEIT_FALLBACK
    schluessel = str(unit).strip().lower().rstrip(".")
    return _UNIT_CODES.get(schluessel, _EINHEIT_FALLBACK)


def belegart_code(invoice_type):
    """Belegart → UNTDID 1001. Unbekannter Typ ⇒ 380 (Rechnung)."""
    return _UNTDID_1001.get(invoice_type, "380")


def _steuerkategorie(tax_code):
    return _TAX_CATEGORY.get(tax_code, _TAX_CATEGORY_FALLBACK_SATZ)


def _land(adresse, default="DE"):
    if not adresse:
        return default
    return (adresse.get("country_code") or default).upper()


def _strasse(adresse):
    if not adresse:
        return None
    teile = [adresse.get("street"), adresse.get("house_number")]
    zeile = " ".join(t for t in teile if t)
    return zeile or None


def _verkaeufer_felder(issuer):
    """BG-4 (Verkäufer). Ohne Firmenprofil gibt es keine gültige E-Rechnung."""
    if not issuer or not (issuer.get("company_name") or "").strip():
        # Kein „Beleg neu ausstellen" — ein veröffentlichter Beleg ist
        # unveränderlich. Sobald das Firmenprofil gepflegt ist, zieht
        # `beleg_stammdaten` es je Feld nach und der Abruf funktioniert.
        raise ERechnungError(
            "Für die E-Rechnung fehlt das Firmenprofil (Aussteller). "
            "Unter Einstellungen › Firmenprofil pflegen, dann die E-Rechnung "
            "erneut abrufen."
        )
    felder = {
        "BT-27": issuer["company_name"],
        "BT-40": (issuer.get("country") or "DE").upper(),
    }
    for bt, wert in (
        ("BT-35", issuer.get("street")),
        ("BT-37", issuer.get("city")),
        ("BT-38", issuer.get("postal_code")),
        ("BT-31", issuer.get("vat_id")),       # USt-IdNr.
        ("BT-32", issuer.get("tax_number")),   # Steuernummer (Fiskal-ID)
        ("BT-42", issuer.get("phone")),
        ("BT-43", issuer.get("email")),
    ):
        if wert:
            felder[bt] = wert
    return felder


def _kaeufer_felder(stamm):
    """BG-7 (Käufer): der Rechnungsempfänger, ersatzweise der Rechnungsschuldner.

    Der Schuldner ist die Fallback-Quelle, weil die DB genau ihn verlangt (A-27);
    ein separater Empfänger ist optional.
    """
    empfaenger = beteiligter(stamm, "INVOICE_RECIPIENT") or beteiligter(
        stamm, "INVOICE_DEBTOR"
    )
    snapshot = (empfaenger or {}).get("snapshot") or {}
    name = (snapshot.get("display_name") or "").strip()
    if not name:
        raise ERechnungError(
            "Für die E-Rechnung fehlt der Rechnungsempfänger (Name)."
        )
    adresse = snapshot.get("address")
    felder = {"BT-44": name, "BT-55": _land(adresse)}
    for bt, wert in (
        ("BT-50", _strasse(adresse)),
        ("BT-52", (adresse or {}).get("city")),
        ("BT-53", (adresse or {}).get("postal_code")),
        ("BT-48", snapshot.get("vat_id")),
    ):
        if wert:
            felder[bt] = wert
    return felder


def _lieferort_felder(stamm, kaeufer):
    """BG-13 ShipToTradeParty = Leistungsort (die Liegenschaft).

    Die Liegenschaft ist am Beleg Pflicht und der Ort, an dem die Leistung
    erbracht wurde — inhaltlich die richtige Angabe. Sie ist außerdem technisch
    nötig: die CII-XSD verlangt ein nicht-leeres ApplicableHeaderTradeDelivery.
    Ein Lieferdatum (BT-72) setzen wir bewusst NICHT — die Rechnung führt keines,
    und ein erfundenes Datum wäre eine falsche Tatsachenbehauptung.
    """
    delivery = stamm.get("delivery") or {}
    adresse = delivery.get("address")
    name = (delivery.get("name") or delivery.get("property_number") or "").strip()
    if not name:
        # Notnagel: ohne Liegenschaftsdaten den Käufer als Lieferziel führen —
        # sonst wäre das XML nicht XSD-valide. Kommt praktisch nicht vor
        # (invoice.property_id ist NOT NULL).
        return {"BT-70": kaeufer["BT-44"], "BT-80": kaeufer["BT-55"]}
    felder = {"BT-70": name, "BT-80": _land(adresse)}
    for bt, wert in (
        ("BT-75", _strasse(adresse)),
        ("BT-77", (adresse or {}).get("city")),
        ("BT-78", (adresse or {}).get("postal_code")),
    ):
        if wert:
            felder[bt] = wert
    return felder


def _zahlungsfelder(issuer):
    """BG-16 (Zahlungsanweisung): SEPA-Überweisung auf die Firmen-IBAN."""
    iban = (issuer.get("iban") or "").strip() if issuer else ""
    if not iban:
        return {}
    felder = {
        "BT-81": _PAYMENT_MEANS_SEPA,
        "BT-84": iban.replace(" ", ""),
        "BT-85": issuer.get("company_name"),
    }
    if issuer.get("bic"):
        felder["BT-86"] = issuer["bic"].strip()
    return felder


def _positionen(invoice):
    """BG-25 (Positionen) + die Steuergruppen (BG-23) aus den NORMAL-Positionen.

    Nicht summenwirksame Positionen (ALTERNATIV = Ausweichvariante, BEDARF =
    Eventualposition) und reine Text-/Zwischensummenzeilen gehören NICHT ins XML:
    sie wurden nie berechnet. Stünden sie drin, läse der Empfänger Beträge, die
    keine Forderung sind — und die Steuergruppen passten nicht mehr zu den
    Kopfsummen.

    Vorzeichen bei Gutschrift/Storno: die Umlegung auf die Menge (BR-27: kein
    negativer Nettoeinzelpreis) macht `beleg.anzeige_menge_preis` — dieselbe
    Funktion, die das PDF benutzt. So kann das Sichtbild nicht von den Daten
    abweichen. Der Positionsbetrag (BT-131) bleibt exakt der gespeicherte
    (negative) net_amount; XML, PDF und Datenbank sind cent- und
    vorzeichengleich.
    """
    zeilen = []
    gruppen = OrderedDict()  # (tax_code, rate) -> Netto-Summe
    nummer = 0
    for line in sorted(invoice.lines.all(), key=lambda l: l.position_number):
        if line.line_type in TEXT_TYPES or line.line_kind != SUMMENWIRKSAM:
            continue
        nummer += 1
        roh_menge, roh_preis = anzeige_menge_preis(line)
        menge = _dec(roh_menge)
        preis = _dec(roh_preis)
        netto = _dec(line.net_amount)
        satz = _dec(line.tax_rate_percent)
        kategorie = _steuerkategorie(line.tax_code_id)

        zeile = {
            "BT-126": str(nummer),
            "BT-153": line.description,
            "BT-129": _menge(menge),
            "BT-130": einheit_code(line.unit),
            "BT-131": _betrag(netto),
            "BT-146": _betrag(preis),
            "BT-151": kategorie,
            "BT-152": _prozent(satz),
        }
        # Positionsrabatt als Positions-Nachlass (BG-27): so steht im XML derselbe
        # Einzelpreis wie in der PDF-Spalte „Einzelpreis", und der Nachlass ist als
        # solcher erkennbar (statt still in einen Mischpreis gerechnet zu werden).
        #
        # Kreditbeleg mit Rabatt: Basis und Nachlass sind dann NEGATIV (Basis −100,
        # Nachlass −10, Betrag −90). Das ist konsistent und geht auf
        # (BT-131 = Menge × Preis − Nachlass); ein Nachlass mit positivem Betrag auf
        # negativer Basis wäre die Alternative gewesen und würde die Rechnung
        # zerreißen. EN16931 kennt für BT-136 keine Vorzeichenregel (nur BR-27/28
        # für die PREISE, und die sind hier positiv).
        if line.discount_percent:
            basis = (menge * preis).quantize(_CENT, rounding=ROUND_HALF_UP)
            nachlass = basis - netto
            if nachlass:
                zeile["BG-27"] = [
                    {
                        "BT-136": _betrag(nachlass),
                        "BT-137": _betrag(basis),
                        "BT-138": _prozent(line.discount_percent),
                        "BT-139": "Positionsrabatt",
                        "BT-140": "95",  # UNTDID 5189: Discount
                    }
                ]
        zeilen.append(zeile)
        schluessel = (line.tax_code_id, satz, kategorie)
        gruppen[schluessel] = gruppen.get(schluessel, Decimal("0.00")) + netto

    if not zeilen:
        raise ERechnungError(
            "Der Beleg enthält keine summenwirksame Position — daraus lässt sich "
            "keine E-Rechnung erzeugen."
        )
    return zeilen, gruppen


def _steuergruppen(invoice, gruppen):
    """BG-23 (Steueraufteilung) und die Gegenprobe gegen die Kopfsummen.

    Die Steuer wird je Gruppe gerundet — exakt wie `beleg._prepare_lines` und der
    DB-CHECK `assert_invoice_totals` es tun. Weicht die Summe von der
    gespeicherten Kopfsteuer ab, wird KEIN XML ausgeliefert: ein
    E-Rechnungs-Empfänger bucht die XML-Werte, ein Cent Abweichung zum PDF wäre
    ein echter Buchungsfehler.
    """
    befreiungsgruende = {
        tc.code: tc.mandatory_text
        for tc in TaxCode.objects.filter(
            code__in={code for code, _rate, _kat in gruppen}
        )
    }
    bg23 = []
    steuer_summe = Decimal("0.00")
    netto_summe = Decimal("0.00")
    for (code, satz, kategorie), netto in gruppen.items():
        steuer = (netto * satz / Decimal(100)).quantize(
            _CENT, rounding=ROUND_HALF_UP
        )
        steuer_summe += steuer
        netto_summe += netto
        eintrag = {
            "BT-116": _betrag(netto),
            "BT-117": _betrag(steuer),
            "BT-118": kategorie,
            "BT-119": _prozent(satz),
        }
        if kategorie in ("E", "AE"):
            # BR-E-10/BR-AE-10: befreite Umsätze brauchen einen Befreiungsgrund.
            eintrag["BT-120"] = (
                befreiungsgruende.get(code)
                or "Steuerbefreit / Steuerschuldnerschaft des Leistungsempfängers."
            )
        bg23.append(eintrag)

    if netto_summe != _dec(invoice.net_total) or steuer_summe != _dec(
        invoice.tax_total
    ):
        raise ERechnungError(
            "Die Steueraufteilung des Belegs stimmt nicht mit seinen Kopfsummen "
            f"überein (Netto {netto_summe} vs. {invoice.net_total}, Steuer "
            f"{steuer_summe} vs. {invoice.tax_total}). Der Beleg ist inkonsistent; "
            "eine E-Rechnung wird dafür nicht ausgestellt."
        )
    return bg23


def _zahlungsbedingungen_bt20(invoice):
    """BT-20 (Zahlungsbedingungen): Klartext + maschinenlesbare Skonto-Zeile.

    EN16931 hat für Skonto **kein eigenes Feld** — BT-20 ist reiner Text. Damit
    wäre der Skonto-Slice maschinell wirkungslos: der Empfänger könnte den Abzug
    nicht automatisch buchen. ZUGFeRD/Factur-X löst das mit einer festgelegten
    Konvention IM SELBEN Feld, die Empfängersysteme parsen:

        #SKONTO#TAGE=10#PROZENT=2.00#BASISBETRAG=285.60#

    Der Klartext steht in der ersten Zeile (den liest der Mensch, und er ist
    wörtlich derselbe wie im PDF), die Konventionszeile darunter (die liest die
    Software). Ohne Skonto bleibt es beim Klartext.
    """
    klartext = beleg_pdf.zahlungsbedingungen_text(invoice)
    if not klartext:
        return None
    zb = zahlungsbedingungen(invoice)
    if not zb:
        return klartext
    maschine = (
        f"#SKONTO#TAGE={zb['discount_days']}"
        f"#PROZENT={_prozent(zb['discount_percent'])}"
        f"#BASISBETRAG={_betrag(invoice.gross_total)}#"
    )
    return f"{klartext}\n{maschine}"


def _bt_dict(invoice):
    """Das vollständige EN16931-BT-Dictionary des Belegs."""
    if not invoice.invoice_number:
        raise ERechnungError("Der Beleg trägt keine Belegnummer.")
    if not invoice.invoice_date:
        raise ERechnungError("Der Beleg trägt kein Belegdatum.")

    stamm = beleg_stammdaten(invoice)
    issuer = stamm["issuer"]
    kaeufer = _kaeufer_felder(stamm)
    zeilen, gruppen = _positionen(invoice)
    bg23 = _steuergruppen(invoice, gruppen)

    waehrung = invoice.currency or "EUR"
    daten = {
        "BT-1": invoice.invoice_number,
        "BT-2": invoice.invoice_date,
        "BT-3": belegart_code(invoice.invoice_type),
        "BT-5": waehrung,
        **_verkaeufer_felder(issuer),
        **kaeufer,
        **_lieferort_felder(stamm, kaeufer),
        **_zahlungsfelder(issuer or {}),
        "BG-23": bg23,
        "BG-25": zeilen,
        # Summen (BG-22). Beleg-Nachlässe/-Zuschläge auf Kopfebene gibt es im
        # Datenmodell nicht (Rabatte hängen an der Position) — daher BT-106 = BT-109.
        "BT-106": _betrag(invoice.net_total),
        "BT-109": _betrag(invoice.net_total),
        "BT-110": _betrag(invoice.tax_total),
        "BT-110-1": waehrung,
        "BT-112": _betrag(invoice.gross_total),
        "BT-115": _betrag(invoice.gross_total),
    }
    if invoice.due_date:
        daten["BT-9"] = invoice.due_date
    bedingungstext = _zahlungsbedingungen_bt20(invoice)
    if bedingungstext:
        daten["BT-20"] = bedingungstext
    # BG-3: Bezug auf vorausgegangene Belege (BT-25/BT-26), Kardinalität 0..n.
    # Kreditbeleg → der korrigierte Ursprungsbeleg; Schlussrechnung → jede
    # angerechnete Abschlags-/Teilrechnung. Ohne diesen Bezug müsste der
    # Empfänger den Abzug aus dem Positionstext raten.
    bg3 = []
    if invoice.invoice_type in ("GUTSCHRIFT", "STORNO") and invoice.reference_invoice_id:
        ref = (
            Invoice.objects.filter(id=invoice.reference_invoice_id)
            .only("invoice_number", "invoice_date")
            .first()
        )
        if ref and ref.invoice_number:
            eintrag = {"BT-25": ref.invoice_number}
            if ref.invoice_date:
                eintrag["BT-26"] = ref.invoice_date
            bg3.append(eintrag)
    if invoice.invoice_type == FINAL_TYPE:
        for posten in anrechnungen(invoice):
            if not posten["invoice_number"]:
                continue
            eintrag = {"BT-25": posten["invoice_number"]}
            if posten["invoice_date"]:
                eintrag["BT-26"] = posten["invoice_date"]
            bg3.append(eintrag)
    if bg3:
        daten["BG-3"] = bg3
    return daten


def build_cii_xml(invoice):
    """CII-XML (EN16931) einer veröffentlichten Rechnung als Bytes.

    Validiert gegen die offizielle Factur-X-XSD (``check_xsd=True``). Schlägt die
    Validierung fehl, fliegt der Fehler durch — wir liefern kein „fast valides"
    XML aus.
    """
    daten = _bt_dict(invoice)
    return generate_cii_xml(
        daten,
        level=PROFIL,
        check_xsd=True,
        # Schematron braucht einen externen Saxon-Server (nicht vorhanden);
        # ausdrücklich abschalten statt still scheitern zu lassen.
        check_schematron=False,
    )


def render_zugferd_pdf(invoice):
    """Hybrid-PDF (PDF/A-3B + eingebettetes CII-XML) einer Rechnung als Bytes."""
    xml = build_cii_xml(invoice)
    basis_pdf = beleg_pdf.render_invoice_document(invoice, compliance="PDF/A-3B")
    return generate_from_binary(
        basis_pdf,
        xml,
        flavor="factur-x",
        level=PROFIL,
        check_xsd=False,  # gerade erzeugt und geprüft — kein Doppelcheck
        afrelationship="Alternative",  # ZUGFeRD: XML und PDF sind dasselbe Dokument
        # PDF/A verlangt einen UNKOMPRIMIERTEN XMP-Metadatenstrom. factur-x
        # komprimiert per Default — das bräche die Konformität still.
        xmp_compression=False,
    )


# --- Abruf + GoBD-Archivierung ---------------------------------------------

def _archived_key(invoice_id):
    return beleg_pdf.archived_key_for(
        "invoice_id", invoice_id, category=E_RECHNUNG_CATEGORY
    )


def _register(actor_app_user_id, invoice_id, *, storage_key, original_filename,
              sha256, size_bytes):
    return beleg_pdf.insert_file_and_link(
        actor_app_user_id, "invoice_id", invoice_id,
        storage_key=storage_key, original_filename=original_filename,
        sha256=sha256, size_bytes=size_bytes, category=E_RECHNUNG_CATEGORY,
    )


def _render(invoice_id):
    """render_fn für die Archivierung: None ⇒ 404 (kein veröffentlichter Beleg)."""
    invoice = beleg_pdf.load_invoice_for_render(invoice_id)
    if invoice is None:
        return None
    return render_zugferd_pdf(invoice)


def _filename(invoice_id):
    inv = Invoice.objects.filter(id=invoice_id).only("id", "invoice_number").first()
    roh = (inv.invoice_number if inv else None) or str(invoice_id)
    sicher = "".join(ch for ch in roh if ch.isalnum() or ch in "-_")
    return f"{sicher or 'beleg'}-zugferd.pdf"


def get_or_archive_zugferd_pdf(actor_app_user_id, invoice_id):
    """Liefert die (archivierte) ZUGFeRD-Ausfertigung einer Rechnung.

    Eigene Ausfertigung mit eigener Kategorie (``E_RECHNUNG``) neben dem normalen
    Beleg-PDF: es sind zwei verschiedene Dokumente (verschiedene Bytes, anderer
    PDF-Standard, XML-Anhang) und beide sind aufbewahrungspflichtig. Ablauf,
    Wettlauf-Behandlung und Degradation bei fehlendem Objektspeicher: identisch
    zum Beleg-PDF (siehe beleg_pdf.get_or_archive_pdf).

    None ⇒ Rechnung unbekannt oder nicht veröffentlicht (Endpunkt → 404).
    """
    return beleg_pdf.get_or_archive_pdf(
        actor_app_user_id, invoice_id,
        storage_prefix=_STORAGE_PREFIX,
        render_fn=_render,
        key_lookup=_archived_key,
        register_fn=_register,
        filename_fn=_filename,
    )


def build_cii_xml_for(invoice_id):
    """CII-XML einer veröffentlichten Rechnung (Debug-/Prüfansicht).

    None ⇒ Rechnung unbekannt oder nicht veröffentlicht (Endpunkt → 404). Das XML
    wird bewusst NICHT archiviert: die aufbewahrungspflichtige Ausfertigung ist
    das Hybrid-PDF, das dieses XML bereits enthält.
    """
    invoice = beleg_pdf.load_invoice_for_render(invoice_id)
    if invoice is None:
        return None
    return build_cii_xml(invoice)
