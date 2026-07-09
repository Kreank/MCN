"""Vorab-Existenzprüfung von Payload-Fremdschlüsseln (→ klarer ValueError, 422).

Ein unbekannter/ungültiger Fremdschlüssel aus einem Request-Payload würde sonst
erst in der Datenbank als IntegrityError auffallen und als HTTP 500
durchschlagen. Diese Helfer prüfen die Existenz **vor** dem
business_transaction-Block und werfen einen klaren, deutschen ValueError, den die
API-Schicht in 422 übersetzt (Projektregel „Fachfehler = 422, nie 500";
docs/HANDOFF.md Abschnitt 3, db_core/gate_errors.py).

Rennbedingungen bleiben bewusst der Datenbank überlassen — ihre Constraints und
Trigger fangen sie weiterhin ab; hier geht es nur um die saubere Meldung des
Normalfalls. Fehlermeldungen nennen keine Tabellen-/Spaltennamen.
"""
from db_core.models import Party


def ensure_exists(model, obj_id, label):
    """Stellt sicher, dass zu ``obj_id`` eine Zeile in ``model`` existiert.

    ``None`` (optionaler FK) ist erlaubt und wird unverändert durchgereicht.
    Gibt ``obj_id`` zurück, damit der Aufruf inline bleiben kann.
    """
    if obj_id is None:
        return obj_id
    if not model.objects.filter(pk=obj_id).exists():
        raise ValueError(f"{label} {obj_id} existiert nicht")
    return obj_id


def ensure_all_exist(model, ids, label):
    """Prüft eine Menge von IDs mit **einer** Query (kein N+1).

    ``None``-Werte werden ignoriert. Wirft ValueError, sobald mindestens eine ID
    keine Entsprechung hat (mit Auflistung der fehlenden IDs).
    """
    wanted = {i for i in (ids or []) if i is not None}
    if not wanted:
        return
    found = set(model.objects.filter(pk__in=wanted).values_list("pk", flat=True))
    missing = wanted - found
    if missing:
        fehlend = ", ".join(str(m) for m in sorted(missing, key=str))
        raise ValueError(f"{label}: unbekannte ID(s): {fehlend}")


def ensure_party_usable(party_id, label="Partei"):
    """``identity.party`` muss existieren und darf nicht zusammengeführt sein.

    Spiegelt ``identity.assert_parties_not_merged`` (F-06): fachliche Referenzen
    auf zusammengeführte (MERGED) Parties sind unzulässig — die kanonische Party
    ist zu verwenden. ``None`` ist erlaubt (optionaler FK); der Rückgabewert ist
    ``party_id`` für den Inline-Gebrauch.
    """
    if party_id is None:
        return party_id
    status = (
        Party.objects.filter(pk=party_id).values_list("status", flat=True).first()
    )
    if status is None:
        raise ValueError(f"{label} {party_id} existiert nicht")
    if status == "MERGED":
        raise ValueError(
            f"{label} {party_id} ist zusammengeführt; bitte die kanonische "
            "Partei verwenden"
        )
    return party_id
