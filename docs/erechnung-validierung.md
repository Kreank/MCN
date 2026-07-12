# E-Rechnung extern validieren (veraPDF + Mustang)

Die E-Rechnung (`backend/db_core/services/erechnung.py`) erzeugt ein Hybrid-PDF:
PDF/A-3B-Sichtbild plus eingebettetes CII-XML (EN16931). Beide Zusicherungen —
**PDF/A-3B** und **EN16931** — sind nur belegt, wenn sie mit den Referenz-
Validatoren geprüft wurden. Unsere eigenen Tests prüfen unsere eigene Logik; sie
können ein systematisches Missverständnis nicht aufdecken.

Der Test `backend/db_core/tests/test_erechnung_konformitaet.py` fährt beide
Validatoren gegen sechs Belegformen (Skonto, kein Skonto, zwei Steuersätze,
Schlussrechnung mit negativen Anrechnungspositionen, Storno mit negativen Summen,
Rechnung mit Logo/Alphakanal). **Ohne installierte Validatoren wird er sauber
übersprungen** — die Suite bleibt auf einem Rechner ohne Java grün.

Die Validator-Artefakte gehören **nicht ins Repo** (Binaries, > 90 MB).

## 1. Java

Eine JRE/JDK ≥ 11 wird von beiden Werkzeugen gebraucht.

```powershell
winget install --id EclipseAdoptium.Temurin.21.JDK --source winget
```

Danach `java -version` (ggf. neue Shell öffnen).

## 2. veraPDF (PDF/A)

Offizielle Referenzimplementierung der PDF Association (verapdf.org).

```bash
curl -sL -o verapdf-installer.zip https://software.verapdf.org/releases/verapdf-installer.zip
unzip -q verapdf-installer.zip           # → verapdf-greenfield-<version>/
cd verapdf-greenfield-*
java -jar verapdf-izpack-installer-*.jar auto-install.xml   # headless
```

`auto-install.xml` (Installationspfad anpassen):

```xml
<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<AutomatedInstallation langpack="eng">
    <com.izforge.izpack.panels.htmlhello.HTMLHelloPanel id="welcome"/>
    <com.izforge.izpack.panels.target.TargetPanel id="install_dir">
        <installpath>C:\tools\verapdf</installpath>
    </com.izforge.izpack.panels.target.TargetPanel>
    <com.izforge.izpack.panels.packs.PacksPanel id="sdk_pack_select">
        <pack index="0" name="veraPDF GUI" selected="true"/>
        <pack index="1" name="veraPDF Mac and *nix Scripts" selected="true"/>
        <pack index="2" name="veraPDF Validation model" selected="false"/>
        <pack index="3" name="veraPDF Documentation" selected="false"/>
        <pack index="4" name="veraPDF Sample Plugins" selected="false"/>
    </com.izforge.izpack.panels.packs.PacksPanel>
    <com.izforge.izpack.panels.install.InstallPanel id="install"/>
    <com.izforge.izpack.panels.finish.FinishPanel id="finish"/>
</AutomatedInstallation>
```

Manueller Aufruf:

```bash
C:/tools/verapdf/verapdf.bat --flavour 3b --format text beleg.pdf
```

## 3. Mustang (ZUGFeRD/Factur-X: XSD + EN16931-Schematron)

Referenz-CLI des ZUGFeRD-Projekts (github.com/ZUGFeRD/mustangproject). Prüft das
Hybrid-PDF komplett: PDF/A (eigener veraPDF-Lauf), XMP-Extension, XSD **und** die
EN16931-Schematron-Regeln (BR-*, BR-CO-*).

```bash
curl -sL -O https://github.com/ZUGFeRD/mustangproject/releases/download/core-2.24.0/Mustang-CLI-2.24.0.jar
java -jar Mustang-CLI-2.24.0.jar --action validate --source beleg.pdf
```

Der Bericht ist XML; entscheidend ist `<summary status="valid">`.

## 4. Testlauf

```bash
cd backend
export MCN_VERAPDF="C:/tools/verapdf/verapdf.bat"
export MCN_MUSTANG_JAR="C:/tools/Mustang-CLI-2.24.0.jar"
uv run pytest -q db_core/tests/test_erechnung_konformitaet.py
```

Ohne die beiden Variablen (oder ohne Java) meldet der Test `skipped` mit klarem
Grund — genauso wie `test_storage_minio_e2e.py` ohne MinIO.
