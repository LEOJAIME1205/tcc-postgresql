"""Motor da arquitetura consolidada: uma consulta SQL faz os dois ramos e a fusão (ADR-046)."""

import os
from typing import Any, List, LiteralString

import numpy as np
import psycopg
from pgvector.psycopg import register_vector_async
from psycopg_pool import AsyncConnectionPool

from motor import Resultado


CONSULTA: LiteralString = """
WITH termos AS (
    SELECT CAST(replace(plainto_tsquery('english', %(texto)s)::text, ' & ', ' | ') AS tsquery) AS q
),
lexico AS (
    SELECT id, ROW_NUMBER() OVER (ORDER BY pontuacao DESC, id) AS posicao
    FROM (SELECT p.id, ts_rank_cd(p.lexico, t.q) AS pontuacao
          FROM passagens p, termos t
          WHERE p.lexico @@ t.q
          ORDER BY pontuacao DESC, p.id
          LIMIT 100) AS l
),
vetorial AS (
    SELECT id, ROW_NUMBER() OVER () AS posicao
    FROM (SELECT id FROM passagens ORDER BY embedding <#> %(vetor)s LIMIT 100) AS v
)
SELECT coalesce(l.id, v.id) AS id,
       coalesce(1.0::float8 / (60 + l.posicao), 0) + coalesce(1.0::float8 / (60 + v.posicao), 0)
           AS pontuacao
FROM lexico l FULL OUTER JOIN vetorial v ON l.id = v.id
ORDER BY pontuacao DESC, id
LIMIT 10"""


class Consolidada:
    """Conexão pelas variáveis PGHOST, PGUSER, PGPASSWORD e PGDATABASE.

    Sem comandos preparados (ADR-047): cada consulta é planejada com os valores reais.
    """

    def __init__(self) -> None:
        self.ef = int(os.environ.get("TCC_EF_BUSCA", "100"))
        conexoes = int(os.environ.get("TCC_CONEXOES", "16"))
        self.pool = AsyncConnectionPool("", min_size=conexoes, max_size=conexoes, open=False,
                                        configure=self.configurar,
                                        kwargs={"autocommit": True, "prepare_threshold": None})

    async def configurar(self, conn: psycopg.AsyncConnection[Any]) -> None:
        await register_vector_async(conn)
        await conn.execute("SELECT set_config('hnsw.ef_search', %s, false)", (str(self.ef),))

    async def abrir(self) -> None:
        await self.pool.open(wait=True, timeout=120)

    async def fechar(self) -> None:
        await self.pool.close()

    async def pronto(self) -> bool:
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT to_regclass('passagens') IS NOT NULL")
            linha = await cur.fetchone()
        return linha is not None and bool(linha[0])

    async def buscar(self, texto: str, vetor: List[float]) -> Resultado:
        parametros = {"texto": texto, "vetor": np.array(vetor, dtype=np.float32)}
        async with self.pool.connection() as conn:
            cur = await conn.execute(CONSULTA, parametros)
            linhas = await cur.fetchall()
        return [(int(i), float(p)) for i, p in linhas]
