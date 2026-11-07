"""Motor da arquitetura poliglota: OpenSearch e Qdrant em paralelo, fusão no serviço (ADR-046)."""

import asyncio
import os
from typing import List

from opensearchpy import AsyncOpenSearch
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import CollectionStatus, SearchParams

from motor import POR_RAMO, Resultado, fundir

NOME: str = "passagens"


class Poliglota:
    """Endereços pelas variáveis OPENSEARCH_HOST e QDRANT_HOST.

    O cliente do OpenSearch abre até TCC_CONEXOES conexões por processo, a mesma regra do pool
    do PostgreSQL; o do Qdrant usa um canal gRPC, que multiplexa as requisições.
    """

    def __init__(self) -> None:
        self.ef = int(os.environ.get("TCC_EF_BUSCA", "100"))
        conexoes = int(os.environ.get("TCC_CONEXOES", "16"))
        self.lexical = AsyncOpenSearch(
            hosts=[{"host": os.environ.get("OPENSEARCH_HOST", "opensearch"), "port": 9200}],
            maxsize=conexoes, timeout=60)
        self.vetorial = AsyncQdrantClient(host=os.environ.get("QDRANT_HOST", "qdrant"),
                                          prefer_grpc=True, timeout=60)

    async def abrir(self) -> None:
        pass

    async def fechar(self) -> None:
        await self.lexical.close()
        await self.vetorial.close()

    async def pronto(self) -> bool:
        saude = await self.lexical.cluster.health(index=NOME)
        colecao = await self.vetorial.get_collection(NOME)
        return saude["status"] == "green" and colecao.status == CollectionStatus.GREEN

    async def ramo_lexico(self, texto: str) -> List[int]:
        corpo = {"query": {"match": {"texto": texto}}, "size": POR_RAMO, "_source": False}
        resposta = await self.lexical.search(index=NOME, body=corpo)
        return [int(h["_id"]) for h in resposta["hits"]["hits"]]

    async def ramo_vetorial(self, vetor: List[float]) -> List[int]:
        resposta = await self.vetorial.query_points(NOME, query=vetor, limit=POR_RAMO,
                                                    search_params=SearchParams(hnsw_ef=self.ef))
        return [int(p.id) for p in resposta.points]

    async def buscar(self, texto: str, vetor: List[float]) -> Resultado:
        lexico, vetorial = await asyncio.gather(self.ramo_lexico(texto), self.ramo_vetorial(vetor))
        return fundir(lexico, vetorial)
