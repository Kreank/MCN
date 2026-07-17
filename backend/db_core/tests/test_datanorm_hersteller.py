"""Tests der HERSTELLER-DATANORM-Variante (Vaillant, Bosch/Junkers).

Anders als der Großhändler-Export (B&O) tragen die Herstellerkataloge

  * den PREIS IM A-SATZ (Feld nach der Einheit, Cent; Preiskennzeichen 1 = Liste),
    nicht in einem separaten P-Satz, und
  * den RABATT in einer separaten .RAB-Datei (Rabattgruppe am Artikel → Prozent),
    nicht im P-Satz.

Zusätzlich sind die Dateinamen im ZIP tolerant (datanorm.001 / .002, Groß/Klein,
Mehr-Member-ZIPs mit .RAB/.WRG). Die Sätze stammen wörtlich aus den vorliegenden
Dateien (Vaillant Preisliste_ET, PPT-Handelsware + RAB, Bosch aend/ers .002).

Der B&O-Weg (P-Satz gewinnt) muss unberührt bleiben — die letzte Testgruppe weist
das über einen Trockenlauf mit --preise nach.
"""
import io
import zipfile
from decimal import Decimal

import pytest
from django.core.management import call_command

from db_core.management.commands.datanorm_import import (
    _artikel_member,
    _rabattindex,
    _zeilen,
)
from db_core.services import datanorm


# --- R-Satz (Rabatttabelle .RAB) --------------------------------------------

def test_parse_rabattgruppe_ppt():
    # Echter Satz aus datanorm-rab-ppt01062023: 2000 = 20,00 %
    r = datanorm.parse_rabattgruppe("R;;PP21;1;2000;Speicher/Heizungszubehör;")
    assert r.gruppe == "PP21"
    assert r.kennzeichen == 1
    assert r.wert == 2000


def test_parse_rabattgruppe_bosch_null_prozent():
    r = datanorm.parse_rabattgruppe("R;;100;1;0;Condens 9800i W-Speichergeräte;")
    assert r.gruppe == "100"
    assert r.wert == 0


def test_kein_rabattsatz():
    with pytest.raises(datanorm.DatanormFehler):
        datanorm.parse_rabattgruppe("A;N;X1;00;Ding;;1;0;ST;100;;;;")


# --- A-Satz-Preis (Herstellerkatalog) ---------------------------------------

def _a(zeile):
    return datanorm.parse_artikel(zeile)


def test_a_satz_liste_ohne_rabatt_setzt_listenpreis():
    """Vaillant ET 000661: 21000 Cent = 210,00 €, Preiskennzeichen 1 (Liste)."""
    a = _a("A;N;000661;00;VA Si.Gruppe mit Druckm.;VGH u.w.;1;0;Stck;21000;RC;30; ;")
    ek, lp = datanorm.preis_aus_artikel(a, rabatt=None)
    assert lp == Decimal("210.0000")     # Bruttopreis aus dem A-Satz
    assert ek is None                    # ohne .RAB kein EK — nicht raten


def test_a_satz_liste_mit_rabattgruppe_ergibt_ek():
    """RC = 30,00 % → EK = 210,00 × 0,70 = 147,00 €."""
    a = _a("A;N;000661;00;VA Si.Gruppe;VGH u.w.;1;0;Stck;21000;RC;30; ;")
    rg = datanorm.Rabattgruppe("RC", 1, 3000)
    ek, lp = datanorm.preis_aus_artikel(a, rabatt=rg)
    assert lp == Decimal("210.0000")
    assert ek == Decimal("147.0000")


def test_a_satz_ppt_handelsware_20_prozent():
    """PPT-Handelsware VIGO: 12300 Cent = 123,00 €, PP21 = 20 % → EK 98,40 €."""
    a = _a("A;N;0010037772;30;Volumenimpulsgeber VIGO 0,3 - 40;0,3 - 40 l/min;1;0;St;12300;PP21; ;T1;")
    rg = datanorm.Rabattgruppe("PP21", 1, 2000)
    ek, lp = datanorm.preis_aus_artikel(a, rabatt=rg)
    assert lp == Decimal("123.0000")
    assert ek == Decimal("98.4000")


def test_a_satz_preiseinheit_wird_angewandt():
    """Preiseinheit 2 (je 100) bleibt auch im A-Satz-Weg wirksam."""
    a = _a("A;N;X;00;Kleinteil;;1;2;ST;1290;RC; ;;")
    ek, lp = datanorm.preis_aus_artikel(a, rabatt=datanorm.Rabattgruppe("RC", 1, 4000))
    assert lp == Decimal("0.1290")
    assert ek == Decimal("0.0774")       # dieselbe Falle wie beim P-Satz


def test_a_satz_nettopreis_pkz2_ist_ek():
    a = _a("A;N;X;00;Ding;;2;0;ST;500;;;;")
    ek, lp = datanorm.preis_aus_artikel(a, rabatt=None)
    assert ek == Decimal("5.0000")
    assert lp is None


def test_a_satz_werkspreis_pkz3_bleibt_ek_unbekannt():
    a = _a("A;N;X;00;Ding;;3;0;ST;5000;;;;")
    ek, lp = datanorm.preis_aus_artikel(a, rabatt=None)
    assert ek is None
    assert lp == Decimal("50.0000")


def test_a_satz_bosch_002_wird_gelesen():
    """Bosch aend .002: 2111 Cent = 21,11 €, Rabattgruppe '400', Preiskennzeichen 1."""
    a = _a("A;A;10000946;50;BOSCH Ersatzteil TTNR: 10000946;Einlegeblende 22/4;1;0;ST;2111;400;ET; ;")
    ek, lp = datanorm.preis_aus_artikel(a, rabatt=None)
    assert lp == Decimal("21.1100")
    assert a.rabattgruppe == "400"


# --- Datei-/Member-Toleranz --------------------------------------------------

def test_member_002_wird_akzeptiert():
    assert _artikel_member(["datanorm.002"]) == "datanorm.002"
    assert _artikel_member(["DATANORM.002"]) == "DATANORM.002"


def test_member_mehr_member_waehlt_artikeldatei():
    """Bosch ers-ZIP: DATANORM.001 + .RAB + .WRG → die .001 ist der Artikelstamm."""
    got = _artikel_member(["DATANORM.001", "DATANORM.RAB", "DATANORM.WRG"])
    assert got == "DATANORM.001"


def test_member_ignoriert_verzeichniseintrag():
    """Grundausstattung-ZIP packt eine Ordnerebene mit ein."""
    got = _artikel_member(["Grund/", "Grund/DATANORM.001"])
    assert got == "Grund/DATANORM.001"


def test_member_bo_einzeldatei_bleibt():
    assert _artikel_member(["datanorm.001"]) == "datanorm.001"


# --- Streaming aus echten ZIP-Layouts ---------------------------------------

def _zip(tmp_path, name, members):
    """members: dict dateiname -> textinhalt."""
    pfad = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(pfad, "w") as z:
        for dateiname, inhalt in members.items():
            z.writestr(dateiname, inhalt.encode(datanorm.ENCODING))
    return str(pfad)


def test_zeilen_liest_002_member(tmp_path):
    inhalt = (
        "V 220526BOSCH Datanorm 4.0                                       04EUR\n"
        "A;N;10000946;50;BOSCH Ersatzteil;Einlegeblende;1;0;ST;2111;400;ET; ;\n"
    )
    pfad = _zip(tmp_path, "aend", {"datanorm.002": inhalt})
    zeilen = list(_zeilen(pfad))
    assert zeilen[0].startswith("V ")
    assert zeilen[1].startswith("A;N;10000946")


def test_zeilen_mehr_member_liest_artikeldatei(tmp_path):
    pfad = _zip(
        tmp_path, "ers",
        {
            "DATANORM.001": "A;N;1;50;Artikel;;1;0;ST;100;100;DL; ;\n",
            "DATANORM.RAB": "R;;100;1;0;Gruppe;\n",
            "DATANORM.WRG": "S;;DL;Dienstleistung;;;\n",
        },
    )
    zeilen = list(_zeilen(pfad))
    assert len(zeilen) == 1
    assert zeilen[0].startswith("A;N;1;")


# --- Rabattindex -------------------------------------------------------------

def test_rabattindex_aus_separater_rab_zip(tmp_path):
    rab = (
        "V 300523PPT GmbH                                                 04EUR\n"
        "R;;PP21;1;2000;Speicher;\n"
        "R;;PP11;1;1500;Abgas;\n"
    )
    pfad = _zip(tmp_path, "rab", {"DATANORM.RAB": rab})
    tabelle = _rabattindex([pfad])
    assert set(tabelle) == {"PP21", "PP11"}
    assert tabelle["PP21"].wert == 2000
    assert tabelle["PP11"].wert == 1500


def test_rabattindex_aus_stamm_zip_bundle(tmp_path):
    """Bosch bündelt die .RAB im selben ZIP wie den Artikelstamm."""
    pfad = _zip(
        tmp_path, "ers",
        {
            "DATANORM.001": "A;N;1;50;Artikel;;1;0;ST;100;100;DL; ;\n",
            "DATANORM.RAB": "R;;100;1;500;Condens;\n",
        },
    )
    tabelle = _rabattindex([None, pfad])
    assert tabelle["100"].wert == 500


# --- Trockenlauf: A-Satz-Preis erscheint (Integration) ----------------------

def _dryrun(*args):
    out = io.StringIO()
    call_command("datanorm_import", *args, "--dry-run", stdout=out)
    return out.getvalue()


def test_dryrun_hersteller_listenpreis_ohne_rabatt(tmp_path):
    stamm = (
        "V 090626Preisliste_ET_DE                                         04EUR\n"
        "A;N;000661;00;VA Si.Gruppe mit Druckm.;VGH u.w.;1;0;Stck;21000;RC;30; ;\n"
        "B;N;000661; ; ; ;0;0;0;4024074002537; ;3065;0;0; ; ;\n"
    )
    pfad = _zip(tmp_path, "et", {"DATANORM.001": stamm})
    text = _dryrun("--stamm", pfad, "--namespace", "vaillant", "--limit", "5")
    assert "210,0000" in text            # Listenpreis aus dem A-Satz
    assert "A-Satz Liste" in text        # Herkunft ausgewiesen
    assert "kein Preissatz" not in text  # nicht mehr leer


def test_dryrun_hersteller_ek_mit_rabatt(tmp_path):
    stamm = (
        "V 090626Preisliste_ET_DE                                         04EUR\n"
        "A;N;000661;00;VA Si.Gruppe;VGH u.w.;1;0;Stck;21000;RC;30; ;\n"
    )
    rab = (
        "V 300523PPT                                                      04EUR\n"
        "R;;RC;1;3000;Zubehör;\n"
    )
    stamm_zip = _zip(tmp_path, "et", {"DATANORM.001": stamm})
    rab_zip = _zip(tmp_path, "rab", {"DATANORM.RAB": rab})
    text = _dryrun(
        "--stamm", stamm_zip, "--rabatt", rab_zip,
        "--namespace", "vaillant", "--limit", "5",
    )
    assert "210,0000" in text            # Listenpreis
    assert "147,0000" in text            # EK = 210 × 0,70
    assert "Rabattgruppe RC" in text


def test_dryrun_bosch_002_toleranz(tmp_path):
    stamm = (
        "V 220526BOSCH Datanorm 4.0                                       04EUR\n"
        "A;N;10000946;50;BOSCH Ersatzteil;Einlegeblende;1;0;ST;2111;400;ET; ;\n"
    )
    pfad = _zip(tmp_path, "aend", {"datanorm.002": stamm})   # .002, klein
    text = _dryrun("--stamm", pfad, "--namespace", "bosch", "--limit", "5")
    assert "21,1100" in text             # 2111 Cent, aus .002 gelesen
    assert "A-Satz Liste" in text


# --- B&O-Regression: der P-Satz gewinnt weiter ------------------------------

def test_dryrun_bo_p_satz_bleibt_massgeblich(tmp_path):
    """Liegt --preise vor, wird der P-Satz genutzt, NICHT der A-Satz-Preis."""
    stamm = (
        "V 020726DATANORM Artikelstamm Testhaendler                       04EUR\n"
        "A;N;ART1;50;Erster Artikel;;1;0;ST;1000;RG01; ; ;\n"
    )
    preise = (
        "V 030726DATANORM Preispflege Testhaendler                        04EUR\n"
        "P;A;ART1;1;1000;1;3300;;;;;\n"
    )
    stamm_zip = _zip(tmp_path, "datanorm", {"datanorm.001": stamm})
    preis_zip = _zip(tmp_path, "datpreis", {"datpreis.001": preise})
    text = _dryrun(
        "--stamm", stamm_zip, "--preise", preis_zip,
        "--namespace", "bo", "--limit", "5",
    )
    # P-Satz-Herkunft, nicht die A-Satz-Formulierung:
    assert "Liste - 33.00 % Rabatt" in text
    assert "A-Satz" not in text
    assert "6,7000" in text              # EK = 10,00 × 0,67 aus dem P-Satz
