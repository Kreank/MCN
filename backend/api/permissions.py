"""Rechte-Durchsetzung für die API-Schicht.

Bis zu diesem Slice prüfte jeder Router nur, ob dem Login-Konto ein
`security.app_user` zugeordnet ist (`_actor_id`). Die Rechtematrix aus
Migration 0026 lag ungenutzt herum. Hier wird sie ausgewertet.

Zwei Ebenen, die nicht verwechselt werden dürfen:

  * **Darf dieser Benutzer die Aktion?** → `require*(request, module, action)`.
    Antwort 403. Das ist diese Datei.
  * **Ist die Aktion fachlich zulässig?** (Statusautomat, Freigabetore, GoBD)
    → Service + DB-Trigger. Antwort 422. Daran ändert sich nichts.

Ein Recht ersetzt nie ein Tor: wer FREIGEBEN darf, darf trotzdem keinen
Auftrag freigeben, dem die Vorbedingungen fehlen.

Drei Torfunktionen, je nach dem, was der Endpunkt mit dem row_scope
('ALLE'|'EIGENE') tut — **fail-closed als Grundhaltung**:

  * `require`  — für Endpunkte, die den Scope NICHT auswerten. Ist der effektive
    Scope 'EIGENE', gibt es **403**: die Ansicht kann die Zeilenbegrenzung nicht
    umsetzen, also wird der Zugriff verweigert statt stillschweigend alle Zeilen
    (also fremde) preiszugeben. 'EIGENE' wird NIE zu 'ALLE' aufgeweitet.
  * `require_scoped` — für Endpunkte, die den Scope **tatsächlich auswerten**.
    Wirft NICHT bei 'EIGENE'; der Aufrufer MUSS dann selbst auf eigene Zeilen
    filtern (sonst ist die Begrenzung unwirksam).
  * `require_create` — für ANLEGEN-Endpunkte. 'EIGENE' ist dort bedeutungslos:
    der Erzeuger ist per Definition der Akteur, es gibt keine fremde Zeile zu
    schützen. Gibt nur die actor_id zurück.

Der Regelfall ist `require`. `require_scoped`/`require_create` sind die bewusst
gesetzten Ausnahmen an genau den Stellen, an denen 'EIGENE' fachlich definiert
ist (Monteur sieht/bearbeitet eigene Aufgaben und Einsätze).
"""
from ninja.errors import HttpError

from db_core.services import rechte as rechte_service


def actor_id(request):
    """app_user_id des angemeldeten Kontos, sonst 403 mit klarer Meldung."""
    actor = getattr(request.user, "app_user_id", None)
    if actor is None:
        raise HttpError(
            403,
            "Dem Login-Konto ist kein security.app_user zugeordnet; "
            "fachliche Vorgänge sind damit nicht möglich.",
        )
    return actor


def _resolve(request, module, action):
    """Prüft, dass das Recht existiert, und gibt (actor_id, row_scope) zurück.

    403, wenn dem Konto kein app_user zugeordnet ist oder das Recht fehlt. Der
    row_scope wird hier NICHT ausgewertet — das entscheidet die Torfunktion.

    Die Rechte werden pro Request einmal geladen und am Request-Objekt gecacht —
    ein Endpunkt prüft oft mehrfach (Lesen + Schreiben), und die Matrix ist
    Stammdaten.
    """
    actor = actor_id(request)

    cache = getattr(request, "_mcn_permissions", None)
    if cache is None:
        cache = rechte_service.effective_permissions(actor)
        request._mcn_permissions = cache

    scope = cache.get((module, action))
    if scope is None:
        raise HttpError(
            403,
            f"Keine Berechtigung: {action} im Modul {module}. "
            "Wenden Sie sich an die Administration.",
        )
    return actor, scope


def require(request, module, action):
    """Prüft das Recht und gibt (actor_id, row_scope) zurück — fail-closed.

    403, wenn das Recht fehlt ODER der effektive Scope 'EIGENE' ist: dieser
    Endpunkt wertet den Scope nicht aus und darf deshalb keine Zeilen liefern,
    die dem Akteur nicht gehören. Der Rückgabewert bleibt (actor, scope), damit
    bestehende Aufrufer (`actor, _ = require(...)`) unverändert funktionieren.
    """
    actor, scope = _resolve(request, module, action)
    if scope == "EIGENE":
        raise HttpError(
            403,
            "Ihre Rolle erlaubt nur den Zugriff auf eigene Datensätze; "
            "diese Ansicht unterstützt das noch nicht.",
        )
    return actor, scope


def require_scoped(request, module, action):
    """Wie `require`, aber wirft NICHT bei 'EIGENE'. Gibt (actor_id, row_scope).

    Nur für Endpunkte verwenden, die den Scope tatsächlich auswerten. Wer das
    nutzt, MUSS bei `scope == 'EIGENE'` auf die eigenen Zeilen des Akteurs
    filtern (bzw. bei Detail-/Schreibzugriff auf fremde Zeilen mit 404
    antworten) — sonst ist die Zeilenbegrenzung wirkungslos.
    """
    return _resolve(request, module, action)


def require_create(request, module, action):
    """Für ANLEGEN-Endpunkte OHNE setzbares Owner-Feld: prüft das Recht, gibt actor_id.

    Der Scope wird hier nicht ausgewertet. Das ist nur dann korrekt, wenn die
    erzeugte Zeile **kein Feld trägt, mit dem der Erzeuger sie jemand anderem
    zuordnen kann**. Sonst könnte ein Konto mit Scope 'EIGENE' Zeilen außerhalb
    seines eigenen Sichtfelds erzeugen (z. B. eine Aufgabe auf die Liste eines
    Kollegen legen) — ein Review hat genau das für `workflow.task` nachgewiesen.

    Trägt die Zeile ein Owner-Feld (`assigned_to`, `responsible`, …), nimm
    `require_scoped` und erzwinge bei 'EIGENE' den Akteur als Eigentümer.
    Beispiel: `api/aufgabe.py::create_task`.
    """
    actor, _ = _resolve(request, module, action)
    return actor


def check(request, module, action):
    """Wie `require` (fail-closed), aber ohne Ausnahme: row_scope oder None.

    **Achtung — 'EIGENE' liefert hier `None`**, weil `require` dahinter sitzt.
    Für einen Baustein, der die Objektgrenze selbst zieht, ist das zu scharf: Er
    verweigert dem Monteur Daten an **seinem eigenen** Objekt, obwohl der
    zuständige Endpunkt sie ihm liefert. Solche Bausteine nehmen
    `check_scoped` (unten) und werten den Scope aus.
    """
    try:
        _, scope = require(request, module, action)
    except HttpError:
        return None
    return scope


def check_scoped(request, module, action):
    """Wie `require_scoped`, aber ohne Ausnahme: row_scope oder None.

    Das weiche Gegenstück für **Bausteine** einer zusammengesetzten Antwort
    (Kopfzeile, Anlagenliste, Gebäudeansicht): Fehlt das Recht ganz, fehlt der
    Baustein (`None`). Steht es auf `'EIGENE'`, kommt der Scope zurück — und der
    Aufrufer **muss** die Objektgrenze selbst ziehen
    (`objektsicht.ist_eigenes_objekt`), sonst ist sie wirkungslos.

    Warum es das braucht (Review-Fund): Mit `check` bekam der Monteur an
    **seinem** Objekt „Belegung ist für Sie nicht sichtbar" zu lesen, während
    ihm `GET /tenure/properties/{id}/belegung` dieselben Mieter samt Telefon
    lieferte. Die Auskunft war nicht nur unvollständig, sondern mit falscher
    Begründung unvollständig — und genau dafür hat MONTEUR seit Migration 0103
    `tenure/LESEN` mit Scope EIGENE („er braucht Name und Telefonnummer").
    """
    try:
        return _resolve(request, module, action)[1]
    except HttpError:
        return None
