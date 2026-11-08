# Gerador de carga do Eixo A (ADR-048): mesma base, uv e lock da imagem do serviço de busca.
FROM python:3.14.4-slim-trixie@sha256:2ca02f32b4d9d893863367ce07ec1972819f476dd38d8612f2a9cb6a41cbb727
COPY --from=ghcr.io/astral-sh/uv:0.12.24@sha256:3af4716e991d6956a41e573eab705d0ee08500cd829ed30293eb8472f372c65a /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /opt/projeto
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-cache

# Os dados entram montados em /app/eixo-a-busca/dados, somente leitura.
WORKDIR /app
COPY eixo-a-busca/cargas/gerador.py eixo-a-busca/cargas/
COPY eixo-a-busca/consolidada/carga.py eixo-a-busca/consolidada/
COPY eixo-a-busca/poliglota/carga.py eixo-a-busca/poliglota/
ENTRYPOINT ["/opt/venv/bin/python", "/app/eixo-a-busca/cargas/gerador.py"]
