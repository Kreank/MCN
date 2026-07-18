#!/bin/sh
# MCN Backup — nächtlicher pg_dump + MinIO-Spiegel + Schlüsselsicherung.
#
# WARUM EIN EIGENER CONTAINER (wie der scheduler): Ein Host-Cron ist ein Schritt,
# den der Betreiber von Hand einrichtet und beim Serverumzug vergisst. Fehlt er,
# läuft das System scheinbar gesund — nur es gibt kein Backup, und das merkt man
# erst, wenn man eines braucht. Als compose-Dienst kommt das Backup mit dem Stack
# hoch, hat dieselben Env-Vars, dieselbe DB und dieselben Logs
# (`docker compose logs backup`) und wird von `restart: unless-stopped` wieder
# angeworfen.
#
# WAS GESICHERT WIRD:
#   1. Postgres — vollständiger `pg_dump` (gzip). DIE Geschäftsdaten.
#   2. MinIO — Spiegel des Beleg-Buckets (Rechnungs-PDFs, Kundenunterschriften).
#      ADDITIV (ohne --remove): im Bucket gelöschte Objekte bleiben im Backup
#      erhalten — sonst propagierte eine versehentliche Löschung in die einzige
#      Kopie. Der Spiegel schützt gegen Plattenverlust; gegen böswillige/
#      versehentliche Änderung eines Objekts ist MinIO-Versioning/Object-Lock die
#      Härtung (offen).
#   3. Schlüssel/.env — OHNE MCN_MAIL_KEY/MCN_CRED_KEY/MCN_SECRET_KEY sind die
#      verschlüsselten Felder (SMTP-Passwort, Geräte-Bearer) und die Sessions nach
#      einem Restore WERTLOS. Ein DB-Dump allein reicht nicht.
#
# WICHTIG (Ausfallsicherheit): Das Zielverzeichnis (MCN_BACKUP_DIR) sollte auf
# einer ANDEREN Platte liegen als pgdata — und zusätzlich off-box gespiegelt
# werden (rsync/S3). Ein Backup auf derselben Platte hilft beim Plattencrash
# nicht. Restore-Anleitung: docs/deployment.md, Abschnitt „Wiederherstellung".
set -eu
# pipefail: sonst ist der Exit-Status von `pg_dump | gzip` der von gzip — ein
# mittendrin abgebrochener pg_dump (Netz weg, Server-OOM, Platte voll) sähe dann
# als sauberes Backup aus. BusyBox ash (alpine /bin/sh) unterstützt pipefail.
set -o pipefail

STUNDE="${MCN_BACKUP_HOUR:-2}"
MINUTE="${MCN_BACKUP_MINUTE:-30}"
export TZ="${MCN_BACKUP_TZ:-Europe/Berlin}"
RETENTION="${MCN_BACKUP_RETENTION_DAYS:-14}"
OUT=/backups

# Führende Nullen strippen, mindestens eine Ziffer behalten. `date +%H` liefert
# nullgepolstert (08, 09) — POSIX-Arithmetik in BusyBox liest das als OKTAL und
# bricht bei 08/09 mit „arithmetic syntax error" ab. `10#`-Präfix kennt BusyBox
# nicht, deshalb hier von Hand.
entnull() {
    v="$1"
    while [ "${#v}" -gt 1 ] && [ "${v#0}" != "${v}" ]; do v="${v#0}"; done
    printf '%s' "${v}"
}

lauf() {
    # HINWEIS: `lauf` wird stets als `lauf || …` gerufen — damit ist `set -e`
    # INNERHALB dieser Funktion inert (POSIX). Die kritischen Pfade prüfen ihren
    # Status deshalb explizit (if/||), nicht implizit über `set -e`.
    umask 077                       # Dump/Spiegel/.env-Kopie nicht welt-/gruppenlesbar
    ts=$(date '+%Y%m%d-%H%M%S')
    echo "[backup] $(date '+%F %T %Z') — Start ${ts}"
    mkdir -p "${OUT}/db" "${OUT}/minio" "${OUT}/env"

    # 1) Postgres-Dump — atomar: erst .partial, dann umbenennen. Mit pipefail (oben)
    #    schlägt die Pipe fehl, sobald pg_dump fehlschlägt; zusätzlich `gzip -t`
    #    (CRC/Vollständigkeit des gzip) und ein Nicht-Leer-Test, bevor die Datei zum
    #    gültigen Backup promoted wird.
    tmp="${OUT}/db/mcn-${ts}.sql.gz.partial"
    final="${OUT}/db/mcn-${ts}.sql.gz"
    if PGPASSWORD="${MCN_DB_PASSWORD}" pg_dump \
            -h "${MCN_DB_HOST}" -p "${MCN_DB_PORT:-5432}" \
            -U "${MCN_DB_USER}" -d "${MCN_DB_NAME}" \
            --no-owner --no-privileges | gzip > "${tmp}"; then
        if [ -s "${tmp}" ] && gzip -t "${tmp}" 2>/dev/null; then
            mv "${tmp}" "${final}"
            echo "[backup] DB-Dump: ${final} ($(du -h "${final}" | cut -f1))"
        else
            rm -f "${tmp}"
            echo "[backup] FEHLER: Dump leer oder unvollständig — verworfen."
            return 1
        fi
    else
        rm -f "${tmp}"
        echo "[backup] FEHLER: pg_dump fehlgeschlagen — kein DB-Backup dieses Laufs."
        return 1
    fi

    # 2) MinIO-Spiegel — ADDITIV (kein --remove): der Spiegel behält auch Objekte,
    #    die im Bucket gelöscht wurden. `--overwrite` deckt eine Neuanlage unter
    #    gleichem Schlüssel ab (bei uuid-/sha-basierten Keys praktisch nie).
    if mc alias set mcnsrc "${MCN_MINIO_ENDPOINT}" \
            "${MCN_MINIO_ACCESS_KEY}" "${MCN_MINIO_SECRET_KEY}" >/dev/null 2>&1; then
        if mc mirror --overwrite \
                "mcnsrc/${MCN_MINIO_BUCKET}" "${OUT}/minio/${MCN_MINIO_BUCKET}"; then
            echo "[backup] MinIO-Spiegel: ${OUT}/minio/${MCN_MINIO_BUCKET}"
        else
            echo "[backup] WARNUNG: MinIO-Spiegel fehlgeschlagen (DB-Dump steht)."
        fi
    else
        echo "[backup] WARNUNG: MinIO-Alias fehlgeschlagen — Spiegel übersprungen."
    fi

    # 3) Schlüssel/Umgebung. Ohne diese Datei ist ein Restore nicht entschlüsselbar.
    #    Nur wenn die .env read-only hereingereicht wurde (Compose-Volume).
    if [ -f /secrets/.env ]; then
        envbak="${OUT}/env/env-${ts}.bak"
        cp /secrets/.env "${envbak}"     # umask 077 → 600
        echo "[backup] Schlüssel/Umgebung gesichert: ${envbak} (chmod 600)"
    else
        echo "[backup] Hinweis: /secrets/.env nicht gemountet — Schlüssel NICHT gesichert."
    fi

    # 4) Aufbewahrung: alte Dumps/Env-Sicherungen wegräumen; verwaiste .partial
    #    (nach hartem Kill) separat. Der MinIO-Spiegel ist additiv und wird nicht
    #    rotiert. `-exec rm` statt `-delete` (portabel über BusyBox find).
    find "${OUT}/db" -name 'mcn-*.sql.gz' -type f -mtime "+${RETENTION}" \
        -exec rm -f {} + 2>/dev/null || true
    find "${OUT}/db" -name 'mcn-*.sql.gz.partial' -type f -mmin +120 \
        -exec rm -f {} + 2>/dev/null || true
    find "${OUT}/env" -name 'env-*.bak' -type f -mtime "+${RETENTION}" \
        -exec rm -f {} + 2>/dev/null || true
    echo "[backup] fertig (Aufbewahrung: ${RETENTION} Tage)."
}

# Sekunden bis zur nächsten Ziel-Uhrzeit — bewusst OHNE `date -d` (BusyBox kennt
# dessen GNU-Syntax nicht). DST-naiv: eine Zeitumstellung verschiebt den Lauf
# zweimal im Jahr um eine Stunde — für ein tägliches Backup belanglos.
schlaf_bis_ziel() {
    h=$(entnull "$(date +%H)")
    m=$(entnull "$(date +%M)")
    s=$(entnull "$(date +%S)")
    zh=$(entnull "${STUNDE}")
    zm=$(entnull "${MINUTE}")
    sec_now=$(( h * 3600 + m * 60 + s ))
    sec_ziel=$(( zh * 3600 + zm * 60 ))
    if [ "${sec_ziel}" -le "${sec_now}" ]; then
        echo $(( 86400 - sec_now + sec_ziel ))
    else
        echo $(( sec_ziel - sec_now ))
    fi
}

echo "[backup] täglicher Lauf um ${STUNDE}:${MINUTE} (${TZ}), Aufbewahrung ${RETENTION} Tage."

# Optionaler Sofortlauf beim Start — praktisch zur Verifikation der Einrichtung.
if [ "${MCN_BACKUP_RUN_ON_START:-0}" = "1" ]; then
    lauf || echo "[backup] WARNUNG: Startlauf fehlgeschlagen."
fi

while true; do
    schlaf=$(schlaf_bis_ziel)
    echo "[backup] nächster Lauf in ${schlaf} s."
    sleep "${schlaf}"
    lauf || echo "[backup] WARNUNG: Lauf fehlgeschlagen — nächster Versuch morgen."
done
