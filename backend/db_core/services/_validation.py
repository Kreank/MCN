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
from db_core.models import Building, Party, Unit


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


def ensure_standort(property_id, building_id, unit_id):
    """Gebäude/Einheit müssen zur Liegenschaft passen (sonst FK-Fehler → 500).

    Die **eine** Definition der Standortkonsistenz. Sie bildet die
    zusammengesetzten Fremdschlüssel ab, die `property.technical_asset` (0004)
    und `property.room` (0086) gleichlautend tragen — 0086 nennt die Anlage
    ausdrücklich als Vorbild. Beide Aufrufer teilen sie sich; ein Nachbau je
    Modul wären zwei Wahrheiten über dieselbe DB-Regel.

    (Lag bis zum Anlagen-Review als `raum._pruefe_zuordnung` modulintern. Der
    Unterstrich log, sobald ein zweites Modul sie brauchte.)

    **Gibt `(building_id, unit_id)` zurück** — mit abgeleitetem Gebäude, falls
    nur die Einheit kam (Befund I11). Die Einheit kennt ihr Gebäude; danach zu
    fragen, war eine Bringschuld, die diese Funktion selbst erfüllen kann. Sie
    las den Wert ohnehin schon, um ihn zu vergleichen — und warf dann einen
    Fehler, statt ihn zu setzen. Die Folge war eine dreifach kopierte
    Auswahlkaskade im Frontend (Raum-Editor, Anlagen-Dialog, Plantafel), die
    jeder neue Aufrufer ein weiteres Mal abschreiben müsste.

    Aufrufer, die den Rückgabewert ignorieren, verhalten sich unverändert —
    außer dass „Einheit ohne Gebäude" jetzt durchgeht statt zu scheitern.
    """
    if unit_id is not None and building_id is None:
        # Ableiten statt abweisen: Die Einheit bestimmt ihr Gebäude eindeutig.
        # Die Existenzprüfung übernimmt der Block weiter unten.
        building_id = (
            Unit.objects.filter(pk=unit_id)
            .values_list("building_id", flat=True)
            .first()
        )
        if building_id is None:
            raise ValueError(f"Einheit {unit_id} existiert nicht")
    if building_id is not None:
        b_prop = (
            Building.objects.filter(pk=building_id)
            .values_list("property_id", flat=True)
            .first()
        )
        if b_prop is None:
            raise ValueError(f"Gebäude {building_id} existiert nicht")
        if b_prop != property_id:
            raise ValueError("Das Gebäude gehört nicht zur angegebenen Liegenschaft")
    if unit_id is not None:
        u_build = (
            Unit.objects.filter(pk=unit_id)
            .values_list("building_id", flat=True)
            .first()
        )
        if u_build is None:
            raise ValueError(f"Einheit {unit_id} existiert nicht")
        if u_build != building_id:
            raise ValueError("Die Einheit gehört nicht zum angegebenen Gebäude")
    return building_id, unit_id


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
