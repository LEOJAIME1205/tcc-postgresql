"""Carrega as passagens da NQ e seus vetores no PostgreSQL e constrói os índices."""

import argparse
import hashlib
import json
import os
import sys
import time
from typing import Any, Dict, List, LiteralString, Tuple, cast

import numpy as np
import numpy.typing as npt
import psycopg
from pgvector.psycopg import register_vector

DADOS: str = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dados")
DIMENSAO: int = 384
AVISO: int = 250_000

TABELA: LiteralString = """
CREATE TABLE passagens (
    id        integer NOT NULL,
    titulo    text NOT NULL,
    texto     text NOT NULL,
    lexico    tsvector GENERATED ALWAYS AS (to_tsvector('english', titulo || ' ' || texto)) STORED,
    embedding vector(384) NOT NULL
)"""
COPIA: LiteralString = "COPY passagens (id, titulo, texto, embedding) FROM STDIN (FORMAT BINARY)"

WAL_CARGA: LiteralString = "ALTER SYSTEM SET max_wal_size = '16GB'"
WAL_PADRAO: LiteralString = "ALTER SYSTEM RESET max_wal_size"
CHECKPOINTS: LiteralString = "SELECT num_timed, num_requested FROM pg_stat_checkpointer"


CONSTRUCAO: Tuple[LiteralString, ...] = (
    "SET maintenance_work_mem = '8GB'",
    "SET max_parallel_maintenance_workers = 11",
    "SET max_parallel_workers = 12",
)
INDICES: Tuple[Tuple[str, LiteralString], ...] = (
    ("chave_primaria", "ALTER TABLE passagens ADD PRIMARY KEY (id)"),
    ("indice_lexico", "CREATE INDEX passagens_lexico ON passagens USING gin (lexico)"),
    ("indice_vetorial", "CREATE INDEX passagens_embedding ON passagens "
                        "USING hnsw (embedding vector_ip_ops) WITH (m = 16, ef_construction = 100)"),
)
TAMANHOS: LiteralString = """
SELECT relname::text, pg_relation_size(oid) FROM pg_class
WHERE relname IN ('passagens', 'passagens_pkey', 'passagens_lexico', 'passagens_embedding')
UNION ALL
SELECT 'passagens_toast', pg_total_relation_size(reltoastrelid) FROM pg_class
WHERE relname = 'passagens'
UNION ALL
SELECT 'total', pg_total_relation_size('passagens')"""
PARAMETROS: LiteralString = """
SELECT name, current_setting(name) FROM pg_settings
WHERE name IN ('shared_buffers', 'effective_cache_size', 'work_mem', 'max_worker_processes',
               'maintenance_work_mem', 'max_parallel_maintenance_workers', 'max_parallel_workers', 'max_wal_size')
ORDER BY name"""


def registro(linha: str) -> Dict[str, Any]:
    dados = json.loads(linha)
    if not isinstance(dados, dict):
        raise ValueError(f"linha não é um objeto JSON: {linha[:80]!r}")
    return cast(Dict[str, Any], dados)


def campo(doc: Dict[str, Any], nome: str) -> str:
    valor = doc.get(nome)
    if not isinstance(valor, str):
        raise ValueError(f"campo {nome!r} ausente ou não textual em {doc.get('_id')!r}")
    return valor


def sha256(caminho: str) -> str:
    with open(caminho, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def copiar(conn: psycopg.Connection[Any], vetores: npt.NDArray[np.float32]) -> int:
    """COPY binário, linha a linha, na ordem do corpus: a linha n é a passagem docN (ADR-041)."""
    n = 0
    inicio = time.monotonic()
    with (open(os.path.join(DADOS, "nq", "corpus.jsonl"), encoding="utf-8") as f,
          conn.cursor() as cur, cur.copy(COPIA) as copia):
        copia.set_types(["integer", "text", "text", "vector"])
        for linha in f:
            doc = registro(linha)
            if doc.get("_id") != f"doc{n}":
                raise ValueError(f"linha {n + 1}: esperado doc{n}, encontrado {doc.get('_id')!r}")
            copia.write_row((n, campo(doc, "title"), campo(doc, "text"), vetores[n]))
            n += 1
            if n % AVISO == 0:
                print(f"  {n:,} linhas ({n / (time.monotonic() - inicio):,.0f}/s)", flush=True)
    return n


def executar(conn: psycopg.Connection[Any], nome: str, comando: LiteralString,
             duracoes: Dict[str, float]) -> None:
    inicio = time.monotonic()
    conn.execute(comando)
    duracoes[nome] = round(time.monotonic() - inicio, 1)
    print(f"{nome}: {duracoes[nome]} s", flush=True)


def carregar(conn: psycopg.Connection[Any], vetores: npt.NDArray[np.float32],
             duracoes: Dict[str, float]) -> List[Tuple[Any, ...]]:
    """COPY, índices e VACUUM ANALYZE; devolve os parâmetros que valeram na construção."""
    inicio = time.monotonic()
    linhas = copiar(conn, vetores)
    duracoes["copia"] = round(time.monotonic() - inicio, 1)
    print(f"copia: {duracoes['copia']} s", flush=True)
    if linhas != vetores.shape[0]:
        raise RuntimeError(f"{linhas} linhas no corpus, {vetores.shape[0]} vetores")

    for comando in CONSTRUCAO:
        conn.execute(comando)
    parametros: List[Tuple[Any, ...]] = conn.execute(PARAMETROS).fetchall()
    for nome, comando in INDICES:
        executar(conn, nome, comando, duracoes)
    executar(conn, "vacuum_analyze", "VACUUM ANALYZE passagens", duracoes)
    return parametros


def main() -> int:
    parser = argparse.ArgumentParser(description="Carga do Eixo A no PostgreSQL")
    parser.add_argument("--registro", required=True, help="arquivo JSON com tempos e tamanhos")
    args = parser.parse_args()
    caminho_registro = cast(str, args.registro)

    caminho_vetores = os.path.join(DADOS, "passagens.npy")
    vetores: npt.NDArray[np.float32] = np.load(caminho_vetores, mmap_mode="r")
    if vetores.ndim != 2 or vetores.shape[1] != DIMENSAO or vetores.dtype != np.float32:
        print(f"{caminho_vetores}: formato inesperado {vetores.shape} {vetores.dtype}",
              file=sys.stderr)
        return 1

    with psycopg.connect(autocommit=True) as conn:
        conn.add_notice_handler(lambda d: print(f"  servidor: {d.message_primary}", flush=True))
        existe = conn.execute("SELECT to_regclass('passagens') IS NOT NULL").fetchone()
        if existe is None or existe[0]:
            print("A tabela passagens já existe: a carga parte de um banco vazio", file=sys.stderr)
            return 1
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute(TABELA)
        register_vector(conn)

        duracoes: Dict[str, float] = {}
        antes = conn.execute(CHECKPOINTS).fetchone()
        conn.execute(WAL_CARGA)
        conn.execute("SELECT pg_reload_conf()")
        try:
            parametros = carregar(conn, vetores, duracoes)
        finally:
            conn.execute(WAL_PADRAO)
            conn.execute("SELECT pg_reload_conf()")
        depois = conn.execute(CHECKPOINTS).fetchone()

        linhas = vetores.shape[0]


        contagem = conn.execute("SELECT count(*) FROM passagens").fetchone()
        if contagem is None or contagem[0] != linhas:
            print(f"Contagem final {contagem}, esperada {linhas}", file=sys.stderr)
            return 1
        tamanhos = {str(nome): int(b) for nome, b in conn.execute(TAMANHOS).fetchall()}
        versao = conn.execute("SELECT version()").fetchone()
        pgvector = conn.execute(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()

    saida: Dict[str, Any] = {
        "linhas": linhas, "duracoes_s": duracoes, "tamanhos_bytes": tamanhos,
        "checkpoints": {"por_tempo": depois[0] - antes[0], "pedidos": depois[1] - antes[1]}
                       if antes and depois else None,
        "parametros_construcao": {str(nome): str(valor) for nome, valor in parametros},
        "postgres": versao[0] if versao else None, "pgvector": pgvector[0] if pgvector else None,
        "passagens_npy_sha256": sha256(caminho_vetores),
        "concluido_em": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    with open(caminho_registro, "w", encoding="utf-8") as f:
        json.dump(saida, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"{linhas:,} passagens; total {tamanhos['total'] / 2**30:.2f} GiB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
