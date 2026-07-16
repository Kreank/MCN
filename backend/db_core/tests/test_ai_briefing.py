"""Leitstand-Tagesbriefing (db_core.ai.leitstand_briefing).

Deckt die drei Ebenen ab, aus denen das Briefing besteht:

* **Reine Funktionen** (ohne DB): `_bereinige` (Säubern der Modellantwort),
  `_fallback` (Briefing aus Zählwerten) und `_nachrichten` (Vertrauensgrenze).
* **Sammlung** gegen echte DB: Aufgaben überfällig-zuerst, mit Zählwerten/Kappung.
* **Erzeugen + Cache + Provenienz**: LLM-Pfad vs. Fallback, jeder Lauf als
  `ai_run` protokolliert, Cache/TTL/refresh.
"""
import uuid
from datetime import date, timedelta

import pytest
from django.utils import timezone

from db_core.ai import leitstand_briefing as lb
from db_core.ai.llm import FakeBackend, LlmError
from db_core.models import AiRun
from db_core.services import aufgabe as aufgabe_service


@pytest.fixture(autouse=True)
def _cache_leeren():
    """Der Modul-Cache ist prozessweit — vor jedem Test verwerfen."""
    lb.cache_leeren()
    yield
    lb.cache_leeren()


# ---------------------------------------------------------------------------
# Reine Funktionen
# ---------------------------------------------------------------------------

def test_bereinige_akzeptiert_gueltiges_briefing():
    roh = {
        "schlagzeile": "  Das steht an  ",
        "punkte": [
            {"text": " 3 überfällige Aufgaben ", "bereich": "aufgaben",
             "dringlichkeit": "ueberfaellig"},
        ],
    }
    out = lb._bereinige(roh, mit_angebote=True)
    assert out["schlagzeile"] == "Das steht an"
    assert out["punkte"] == [
        {"text": "3 überfällige Aufgaben", "bereich": "aufgaben",
         "dringlichkeit": "ueberfaellig"}
    ]


def test_bereinige_wirft_ungueltige_punkte_weg_und_deckelt():
    roh = {
        "schlagzeile": "x",
        "punkte": (
            [{"text": "ok", "bereich": "aufgaben", "dringlichkeit": "quatsch"}]  # dring→info
            + [{"text": "", "bereich": "aufgaben", "dringlichkeit": "info"}]      # leerer Text raus
            + [{"text": "y", "bereich": "unbekannt", "dringlichkeit": "info"}]    # Bereich raus
            + [{"text": f"p{i}", "bereich": "wartung", "dringlichkeit": "bald"}
               for i in range(10)]                                               # Kappung
        ),
    }
    out = lb._bereinige(roh, mit_angebote=True)
    assert len(out["punkte"]) == lb.MAX_PUNKTE
    assert out["punkte"][0] == {"text": "ok", "bereich": "aufgaben", "dringlichkeit": "info"}


def test_bereinige_verwirft_angebote_ohne_recht():
    roh = {"schlagzeile": "x", "punkte": [
        {"text": "a", "bereich": "angebote", "dringlichkeit": "info"},
        {"text": "b", "bereich": "aufgaben", "dringlichkeit": "info"},
    ]}
    out = lb._bereinige(roh, mit_angebote=False)
    assert [p["bereich"] for p in out["punkte"]] == ["aufgaben"]


@pytest.mark.parametrize("data", [None, {}, {"schlagzeile": "x"},
                                  {"schlagzeile": "", "punkte": []},
                                  {"schlagzeile": "x", "punkte": "nope"},
                                  {"echo": "..."}])
def test_bereinige_unbrauchbar_ist_none(data):
    assert lb._bereinige(data, mit_angebote=True) is None


def test_fallback_aus_zaehlwerten():
    kontext = {
        "aufgaben": {"offen": 5, "ueberfaellig": 2, "liste": []},
        "vorgaenge": {"neu_48h": 3, "liste": []},
        "wartung": {"faellig_14t": 1, "liste": [{"ueberfaellig": True}]},
        "angebote": {"versendet_offen": 4, "liste": [{"abgelaufen": False}]},
    }
    out = lb._fallback(kontext, mit_angebote=True)
    # Zwei Aufgaben-Punkte: überfällige zuerst, dann der offene Rest.
    aufgaben_punkte = [p for p in out["punkte"] if p["bereich"] == "aufgaben"]
    assert aufgaben_punkte[0]["dringlichkeit"] == "ueberfaellig"
    assert aufgaben_punkte[1]["dringlichkeit"] == "info"
    einmalig = {p["bereich"]: p["dringlichkeit"]
                for p in out["punkte"] if p["bereich"] != "aufgaben"}
    assert einmalig["vorgaenge"] == "bald"
    assert einmalig["wartung"] == "ueberfaellig"       # eine überfällige Fälligkeit
    assert einmalig["angebote"] == "info"
    assert out["schlagzeile"] == "Das steht heute an."


def test_fallback_leer_ist_ruhiger_tag():
    kontext = {
        "aufgaben": {"offen": 0, "ueberfaellig": 0, "liste": []},
        "vorgaenge": {"neu_48h": 0, "liste": []},
        "wartung": {"faellig_14t": 0, "liste": []},
    }
    out = lb._fallback(kontext, mit_angebote=False)
    assert out["punkte"] == []
    assert "Ruhiger Tag" in out["schlagzeile"]


def test_nachrichten_trennt_daten_von_anweisung():
    msgs = lb._nachrichten({"aufgaben": {"offen": 1}})
    assert msgs[0].role == "system"
    assert "DATEN" in msgs[0].content and "KEINE" in msgs[0].content
    assert msgs[1].content.startswith("<daten>") and msgs[1].content.endswith("</daten>")


# ---------------------------------------------------------------------------
# Sammlung gegen echte DB
# ---------------------------------------------------------------------------

def test_sammle_kontext_aufgaben_ueberfaellig_zuerst(app_user):
    heute = date.today()
    aufgabe_service.create_task(app_user.id, title="Zukunft", due_date=heute + timedelta(days=5))
    aufgabe_service.create_task(app_user.id, title="Überfällig", due_date=heute - timedelta(days=3))
    aufgabe_service.create_task(app_user.id, title="Ohne Datum")

    kontext, sources = lb._sammle_kontext(
        mit_angebote=False, heute=heute, jetzt=timezone.now()
    )
    a = kontext["aufgaben"]
    assert a["offen"] == 3
    assert a["ueberfaellig"] == 1
    # Reihenfolge: überfällig (frühestes Datum) zuerst, ohne Datum zuletzt.
    assert a["liste"][0]["titel"] == "Überfällig"
    assert a["liste"][0]["ueberfaellig"] is True
    assert a["liste"][-1]["titel"] == "Ohne Datum"
    # Ohne Angebots-Recht taucht der Angebote-Block gar nicht erst auf.
    assert "angebote" not in kontext
    assert all(s["type"] == "task" for s in sources)


def test_sammle_kontext_kappt_auf_max(app_user):
    heute = date.today()
    for i in range(lb.MAX_AUFGABEN + 5):
        aufgabe_service.create_task(app_user.id, title=f"A{i}", due_date=heute)
    kontext, _ = lb._sammle_kontext(mit_angebote=False, heute=heute, jetzt=timezone.now())
    assert kontext["aufgaben"]["offen"] == lb.MAX_AUFGABEN + 5      # voller Zählwert
    assert len(kontext["aufgaben"]["liste"]) == lb.MAX_AUFGABEN     # Liste gedeckelt


# ---------------------------------------------------------------------------
# Erzeugen + Provenienz + Cache
# ---------------------------------------------------------------------------

_SYNTH_KONTEXT = {
    "aufgaben": {"offen": 2, "ueberfaellig": 1, "liste": [{"ueberfaellig": True}]},
    "vorgaenge": {"neu_48h": 0, "liste": []},
    "wartung": {"faellig_14t": 0, "liste": []},
}
_SYNTH_SOURCES = [{"type": "task", "id": str(uuid.uuid4())}]


def _fake_sammeln(monkeypatch):
    monkeypatch.setattr(
        lb, "_sammle_kontext",
        lambda **k: (dict(_SYNTH_KONTEXT), list(_SYNTH_SOURCES)),
    )


def test_hole_briefing_ki_pfad_protokolliert_lauf(app_user, monkeypatch):
    _fake_sammeln(monkeypatch)
    fake = FakeBackend(responses=[{
        "schlagzeile": "Heute wichtig",
        "punkte": [{"text": "1 überfällige Aufgabe", "bereich": "aufgaben",
                    "dringlichkeit": "ueberfaellig"}],
    }], model_name="qwen3.5-9b", model_version="q4")

    out = lb.hole_briefing(app_user.id, mit_angebote=False, backend=fake)

    assert out["ki_generiert"] is True
    assert out["modell"] == "qwen3.5-9b"
    assert out["schlagzeile"] == "Heute wichtig"
    assert out["punkte"][0]["bereich"] == "aufgaben"
    # Der Lauf ist in ai.ai_run protokolliert — mit Quellen und Ausgang OK.
    lauf = AiRun.objects.get(workflow_name=lb.WORKFLOW_NAME)
    assert lauf.result_status == "OK"
    assert lauf.sources == _SYNTH_SOURCES
    assert lauf.tools_used == ["llm"]


def test_hole_briefing_fallback_bei_unbrauchbarer_antwort(app_user, monkeypatch):
    _fake_sammeln(monkeypatch)
    # FakeBackend ohne Skript → Echo ({"echo": ...}) passt nicht ins Schema.
    out = lb.hole_briefing(app_user.id, mit_angebote=False, backend=FakeBackend())

    assert out["ki_generiert"] is False
    assert out["modell"] is None
    # Fallback aus den Zählwerten: eine überfällige Aufgabe.
    assert out["punkte"][0] == {"text": "1 überfällige Aufgabe(n).",
                                "bereich": "aufgaben", "dringlichkeit": "ueberfaellig"}
    # Auch der Fehlversuch ist ein Lauf (hier ohne Transportfehler → OK).
    assert AiRun.objects.filter(workflow_name=lb.WORKFLOW_NAME).count() == 1


def test_hole_briefing_fallback_bei_llm_fehler(app_user, monkeypatch):
    _fake_sammeln(monkeypatch)

    class KaputtesBackend(FakeBackend):
        def generate(self, *a, **k):
            raise LlmError("Endpoint nicht erreichbar.")

    out = lb.hole_briefing(app_user.id, mit_angebote=False, backend=KaputtesBackend())
    assert out["ki_generiert"] is False
    # Der Executor hat den Lauf trotzdem abgeschlossen — als FEHLER.
    lauf = AiRun.objects.get(workflow_name=lb.WORKFLOW_NAME)
    assert lauf.result_status == "FEHLER"


def test_hole_briefing_fallback_bei_kaputtem_profil(app_user, monkeypatch):
    # get_backend() ist fail-closed (fehlkonfiguriertes Profil → LlmError). Das
    # darf keine 500 werfen, sondern muss aufs Fallback gehen — hier startet gar
    # kein Lauf (ai_run wird nie erreicht).
    _fake_sammeln(monkeypatch)
    monkeypatch.setattr(
        lb, "get_backend",
        lambda *a, **k: (_ for _ in ()).throw(LlmError("Profil nicht konfiguriert.")),
    )
    out = lb.hole_briefing(app_user.id, mit_angebote=False)  # ohne injiziertes backend
    assert out["ki_generiert"] is False
    assert AiRun.objects.filter(workflow_name=lb.WORKFLOW_NAME).count() == 0


def test_cache_und_refresh(app_user, monkeypatch):
    _fake_sammeln(monkeypatch)
    fake = FakeBackend()  # Echo → Fallback, egal

    erst = lb.hole_briefing(app_user.id, mit_angebote=False, backend=fake)
    wieder = lb.hole_briefing(app_user.id, mit_angebote=False, backend=fake)
    assert wieder["stand"] == erst["stand"]                 # aus dem Cache, nicht neu
    assert AiRun.objects.filter(workflow_name=lb.WORKFLOW_NAME).count() == 1

    spaeter = timezone.now() + timedelta(seconds=1)
    frisch = lb.hole_briefing(
        app_user.id, mit_angebote=False, backend=fake, refresh=True, jetzt=spaeter
    )
    assert frisch["stand"] == spaeter                       # refresh umgeht den Cache
    assert AiRun.objects.filter(workflow_name=lb.WORKFLOW_NAME).count() == 2


def test_cache_getrennt_nach_angebots_recht(app_user, monkeypatch):
    _fake_sammeln(monkeypatch)
    fake = FakeBackend()
    lb.hole_briefing(app_user.id, mit_angebote=False, backend=fake)
    lb.hole_briefing(app_user.id, mit_angebote=True, backend=fake)
    # Zwei Cache-Schlüssel (mit/ohne Angebote) → zwei Läufe.
    assert AiRun.objects.filter(workflow_name=lb.WORKFLOW_NAME).count() == 2
