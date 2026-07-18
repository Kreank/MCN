# Backup-Container: pg_dump (aus postgres:16-alpine, versionsgleich zum Server) +
# mc (MinIO-Client) für den Bucket-Spiegel. Bewusst schlank; ein eigenes Image,
# weil das Backend-Image weder pg_dump noch mc trägt.
FROM postgres:16-alpine

# mc — GEPINNT auf dieselbe Version wie der minio-init-Dienst (offizielle Quelle
# dl.min.io). Kein "latest": reproduzierbar und nachvollziehbar. Server mit
# arm64-CPU müssen linux-amd64 → linux-arm64 ändern.
ARG MC_VERSION=RELEASE.2025-08-13T08-35-41Z
RUN apk add --no-cache ca-certificates curl \
 && curl -fsSL "https://dl.min.io/client/mc/release/linux-amd64/archive/mc.${MC_VERSION}" \
      -o /usr/local/bin/mc \
 && chmod +x /usr/local/bin/mc \
 && apk del curl \
 && mc --version

# Build-Kontext ist die Repo-Wurzel (siehe docker-compose.yml), daher deploy/…
COPY deploy/backup-entrypoint.sh /usr/local/bin/backup-entrypoint.sh
RUN chmod +x /usr/local/bin/backup-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/backup-entrypoint.sh"]
