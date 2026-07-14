#!/bin/sh
# Erzeugt /etc/nginx/conf.d/default.conf beim Containerstart.
#
# Zwei Zustände, ein Image:
#
#   OHNE Zertifikat  — die Anwendung läuft über HTTP (Port 80). Das ist der
#                      Bootstrap-Zustand: certbot braucht einen erreichbaren
#                      HTTP-Server für die ACME-Challenge, bevor es ein
#                      Zertifikat ausstellen kann. Der LOGIN funktioniert in
#                      diesem Zustand ABSICHTLICH NICHT: Session- und
#                      CSRF-Cookie tragen ohne MCN_DEBUG das Secure-Flag, der
#                      Browser sendet sie über HTTP nicht mit. Das ist kein
#                      Fehler, sondern der Schutz. Erst Zertifikat holen.
#
#   MIT Zertifikat   — HTTP leitet auf HTTPS um (die ACME-Challenge bleibt über
#                      HTTP erreichbar, sonst scheitert jede Erneuerung), die
#                      Anwendung läuft über HTTPS (Port 443).
#
# Der Wechsel passiert von selbst beim nächsten Start des Containers.
set -eu

DOMAIN="${MCN_DOMAIN:-localhost}"
CERT_DIR="/etc/letsencrypt/live/${DOMAIN}"
APP_CONF="/etc/nginx/mcn/app.conf"
OUT="/etc/nginx/conf.d/default.conf"

# ---------------------------------------------------------------------------
# Zugriffsregeln für /admin/
# ---------------------------------------------------------------------------
# MCN_ADMIN_ALLOW_IPS: kommaseparierte Adressen/Netze. Alles andere: deny.
# Ist die Liste leer, ist /admin/ für JEDEN gesperrt (fail-closed) — auch für den
# Betreiber. Das ist Absicht: eine vergessene Konfiguration darf das Admin nicht
# öffnen, sondern muss es schließen.
GUARD=""
if [ -n "${MCN_ADMIN_ALLOW_IPS:-}" ]; then
    OLD_IFS="${IFS}"
    IFS=','
    for netz in ${MCN_ADMIN_ALLOW_IPS}; do
        netz=$(echo "${netz}" | tr -d ' ')
        [ -n "${netz}" ] || continue
        GUARD="${GUARD}    allow ${netz};\n"
    done
    IFS="${OLD_IFS}"
fi
GUARD="${GUARD}    deny all;\n"

# Optional zusätzlich Basic-Auth. MCN_ADMIN_BASIC_AUTH enthält eine oder mehrere
# htpasswd-Zeilen (`benutzer:$apr1$...`), erzeugt mit
#   docker run --rm httpd:2.4-alpine htpasswd -nbB admin '<passwort>'
# Zwei Schlösser an einer Tür: die IP-Liste hilft nicht, wenn jemand hinter
# derselben IP sitzt (Büro-NAT, geteilter Server).
if [ -n "${MCN_ADMIN_BASIC_AUTH:-}" ]; then
    printf '%b\n' "${MCN_ADMIN_BASIC_AUTH}" > /etc/nginx/admin.htpasswd
    # Dieses Skript läuft als root, die nginx-WORKER laufen als `nginx`. Mit 0600
    # könnten sie die Datei nicht lesen — nginx antwortet dann mit **500** statt
    # mit 401, und /admin/ wäre auf eine Art „gesperrt", die wie ein Serverfehler
    # aussieht. (Genau so ist es bei der Verifikation passiert.) Deshalb: Gruppe
    # nginx darf lesen, sonst niemand.
    chown root:nginx /etc/nginx/admin.htpasswd 2>/dev/null && chmod 640 /etc/nginx/admin.htpasswd \
        || chmod 644 /etc/nginx/admin.htpasswd
    GUARD="${GUARD}    auth_basic \"MCN Verwaltung\";\n"
    GUARD="${GUARD}    auth_basic_user_file /etc/nginx/admin.htpasswd;\n"
    echo "[nginx] /admin/: IP-Allowlist + Basic-Auth aktiv."
else
    echo "[nginx] /admin/: nur IP-Allowlist (MCN_ADMIN_BASIC_AUTH nicht gesetzt)."
fi

# awk statt sed: die Guard-Zeilen enthalten '$apr1$'-Hashes und Schrägstriche,
# an denen sich ein sed-Ersetzungsmuster verschluckt.
awk -v guard="$(printf '%b' "${GUARD}" | sed 's/[[:space:]]*$//')" \
    '{ if ($0 == "__ADMIN_GUARD__") print guard; else print }' \
    /etc/nginx/mcn/app.conf.template > "${APP_CONF}"

# ---------------------------------------------------------------------------
# server-Blöcke
# ---------------------------------------------------------------------------
# default_server: der Stack ist der einzige Dienst auf dem Port; jeder Host-Header
# landet hier. Ohne das wäre er beim Zugriff über die reine IP nicht erreichbar.
if [ -f "${CERT_DIR}/fullchain.pem" ] && [ -f "${CERT_DIR}/privkey.pem" ]; then
    echo "[nginx] Zertifikat für ${DOMAIN} gefunden — HTTPS aktiv, HTTP leitet um."
    cat > "${OUT}" <<EOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name ${DOMAIN};

    # Muss über HTTP erreichbar bleiben, sonst schlägt jede Erneuerung fehl.
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    # Healthcheck (docker). Bewusst auf Port 80 und VOR der Umleitung: der
    # Prüfer im Container hat kein TLS-fähiges wget.
    location = /nginx-health {
        access_log off;
        add_header Content-Type text/plain always;
        return 200 "ok\n";
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl default_server;
    listen [::]:443 ssl default_server;
    http2 on;
    server_name ${DOMAIN};

    ssl_certificate     ${CERT_DIR}/fullchain.pem;
    ssl_certificate_key ${CERT_DIR}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;

    # HSTS: erst einschalten, wenn das Zertifikat steht (sonst sperrt man sich
    # selbst aus, falls TLS später ausfällt).
    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "same-origin" always;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    include ${APP_CONF};
}
EOF
else
    echo "[nginx] KEIN Zertifikat unter ${CERT_DIR} — HTTP-Bootstrap."
    echo "[nginx] HINWEIS: In diesem Zustand ist der LOGIN nicht möglich"
    echo "[nginx]          (Secure-Cookies). Zuerst 'docker compose run --rm certbot ...'"
    cat > "${OUT}" <<EOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name ${DOMAIN};

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location = /nginx-health {
        access_log off;
        add_header Content-Type text/plain always;
        return 200 "ok\n";
    }

    add_header X-Content-Type-Options "nosniff" always;

    include ${APP_CONF};
}
EOF
fi
