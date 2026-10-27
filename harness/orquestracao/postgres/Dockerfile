FROM postgres:18.6-trixie@sha256:edb0c08dc33befd0262ab3b5cf1c4f3f8cfd8b09e88c05b45adee29d6761ddb6

ARG CA_CERTIFICATES_VERSAO=20250419+deb13u1
ARG PGVECTOR_VERSAO=0.8.7-1.pgdg13+1
ARG TIMESCALEDB_VERSAO=2.31.0~debian13-1806
ARG TIMESCALEDB_TOOLS_VERSAO=0.19.0~debian13

ADD --checksum=sha256:eb53bd3dc31e06fbf1ed769139f12b1fffaf1958a96c9b4f9727c19aada83321 \
    --chmod=644 \
    https://packagecloud.io/timescale/timescaledb/gpgkey \
    /usr/local/share/keyrings/timescaledb.gpg.asc

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends "ca-certificates=${CA_CERTIFICATES_VERSAO}"; \
    echo "deb [ signed-by=/usr/local/share/keyrings/timescaledb.gpg.asc ] https://packagecloud.io/timescale/timescaledb/debian/ trixie main" \
        > /etc/apt/sources.list.d/timescaledb.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        "postgresql-18-pgvector=${PGVECTOR_VERSAO}" \
        "timescaledb-2-postgresql-18=${TIMESCALEDB_VERSAO}" \
        "timescaledb-2-loader-postgresql-18=${TIMESCALEDB_VERSAO}" \
        "timescaledb-tools=${TIMESCALEDB_TOOLS_VERSAO}"; \
    rm -rf /var/lib/apt/lists/*