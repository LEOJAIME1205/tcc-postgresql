# Gerador de carga do Eixo C: TSBS compilado do código-fonte (ADR-059) e o adaptador em Python
# que cumpre o contrato do harness, com a mesma base, uv e lock das imagens dos outros eixos.
FROM golang:1.27.2-trixie@sha256:47667934874913a449b96faa7ef4777fa285d9ec29126ee80ed61c45b7cccf0e AS tsbs
WORKDIR /src
RUN git clone https://github.com/timescale/tsbs.git . \
 && git checkout 8323e59c74027b108f4ad5ec5d3e498b0101a02e
COPY eixo-c-temporal/cargas/tsbs/pr255.patch /tmp/pr255.patch
RUN git apply /tmp/pr255.patch
# O go.sum não tem a entrada de uma dependência indireta; -mod=mod a acrescenta sem mudar o go.mod.
RUN CGO_ENABLED=0 GOFLAGS=-mod=mod go build -trimpath -o /out/ \
      ./cmd/tsbs_generate_data ./cmd/tsbs_generate_queries \
      ./cmd/tsbs_load_timescaledb ./cmd/tsbs_load_influx \
      ./cmd/tsbs_run_queries_timescaledb ./cmd/tsbs_run_queries_influx \
 && git diff --stat > /out/alteracoes.txt

FROM python:3.14.4-slim-trixie@sha256:2ca02f32b4d9d893863367ce07ec1972819f476dd38d8612f2a9cb6a41cbb727
COPY --from=ghcr.io/astral-sh/uv:0.12.24@sha256:3af4716e991d6956a41e573eab705d0ee08500cd829ed30293eb8472f372c65a /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /opt/projeto
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-cache

COPY --from=tsbs /out/ /opt/tsbs/
ENV PATH=/opt/tsbs:$PATH
# Os dados gerados entram montados em /app/eixo-c-temporal/dados.
WORKDIR /app
COPY eixo-c-temporal/cargas/gerador.py eixo-c-temporal/cargas/
ENTRYPOINT ["/opt/venv/bin/python", "/app/eixo-c-temporal/cargas/gerador.py"]
