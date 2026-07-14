# MCN Frontend + Reverse Proxy.
#
# Das Frontend bekommt bewusst KEINEN Laufzeit-Container: ein Angular-Build ist
# nach `ng build` eine Handvoll statischer Dateien. Node im Betrieb laufen zu
# lassen, wäre ein Prozess, ein Angriffsziel und ein Speicherverbrauch ohne
# jeden Gegenwert. Der Build passiert hier (Stufe 1) und wandert ins nginx-Image
# (Stufe 2).
#
# Build-Kontext ist die REPO-WURZEL:
#   docker build -f deploy/nginx.Dockerfile -t mcn-nginx .

# ---------------------------------------------------------------------------
# 1. Angular bauen
# ---------------------------------------------------------------------------
FROM node:24-alpine AS build

WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
# `npm ci` statt `npm install`: die package-lock.json ist bindend.
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npm run build -- --configuration production

# Der application-Builder legt den Browser-Build unter dist/<projekt>/browser ab.
# Wandert der Pfad, soll der BUILD scheitern und nicht ein leeres nginx-Image
# entstehen, das erst beim ersten Aufruf als 404 auffällt.
RUN test -f /app/dist/mcn-frontend/browser/index.html

# ---------------------------------------------------------------------------
# 2. nginx
# ---------------------------------------------------------------------------
FROM nginx:1.27-alpine

COPY --from=build /app/dist/mcn-frontend/browser /usr/share/nginx/html

# Die mitgelieferte Beispielkonfiguration weg — unser Entrypoint schreibt die
# echte nach /etc/nginx/conf.d/default.conf.
RUN rm -f /etc/nginx/conf.d/default.conf

COPY deploy/nginx/app.conf.template /etc/nginx/mcn/app.conf.template
# Das offizielle nginx-Image führt beim Start alles unter /docker-entrypoint.d/
# aus, bevor nginx startet.
COPY deploy/nginx/10-mcn-config.sh /docker-entrypoint.d/10-mcn-config.sh
RUN chmod +x /docker-entrypoint.d/10-mcn-config.sh

EXPOSE 80 443
