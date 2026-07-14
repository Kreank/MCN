"""Der Quelltext bleibt textuell — sonst ist er nicht reviewbar.

Ein NUL-Byte in einer Quelldatei macht sie für git zu einer **Binärdatei**: Der
Diff verschwindet („Binary files differ"), und in einem Projekt mit
Review-Pflicht ist eine nicht diffbare Quelldatei ein echter Mangel — die
Änderung ist dann nicht mehr prüfbar, sondern nur noch behauptet.

Gefunden wurde das an einem Sentinel-Wert im Frontend (`const OHNE_GESCHOSS`),
der als Map-Schlüssel „garantiert kollisionsfrei" sein sollte. Ein gewöhnlicher,
nicht kollidierender String tut dasselbe — ohne die Datei zu opfern.
"""
import os
from pathlib import Path

# backend/db_core/tests/… → Repo-Wurzel
WURZEL = Path(__file__).resolve().parents[3]
ENDUNGEN = {".py", ".ts", ".html", ".scss", ".css", ".sql", ".json", ".md"}
UEBERSPRUNGEN = {
    "node_modules", ".git", "__pycache__", ".venv", "venv", "dist", ".angular",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".idea", ".vscode",
}


def _quelldateien():
    for ordner, unterordner, dateien in os.walk(WURZEL):
        unterordner[:] = [u for u in unterordner if u not in UEBERSPRUNGEN]
        for name in dateien:
            pfad = Path(ordner) / name
            if pfad.suffix in ENDUNGEN:
                yield pfad


def test_keine_nul_bytes_im_quelltext():
    """Kein NUL-Byte in einer Quelldatei — sonst ist der Diff weg."""
    schuldige = [
        str(p.relative_to(WURZEL))
        for p in _quelldateien()
        if b"\x00" in p.read_bytes()
    ]
    assert not schuldige, (
        "Diese Quelldateien enthalten ein NUL-Byte und gelten git damit als "
        f"BINÄR (nicht diffbar, nicht reviewbar): {schuldige}"
    )
