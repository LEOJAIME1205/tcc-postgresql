"""Carrega as passagens da NQ no OpenSearch e os vetores no Qdrant (ADR-044 e ADR-045)."""

import argparse
import hashlib
import json
import os
import sys
import time
from typing import Any, Callable, Dict, Iterator, cast

import numpy as np
import numpy.typing as npt
from opensearchpy import OpenSearch
from opensearchpy.helpers import streaming_bulk
from qdrant_client import QdrantClient
from qdrant_client.models import CollectionStatus, Distance, HnswConfigDiff, VectorParams

DADOS: str = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dados")
DIMENSAO: int = 384
AVISO: int = 250_000
NOME: str = "passagens"
LOTE_OPENSEARCH_BYTES: int = 10 * 2**20
LOTE_QDRANT: int = 256
FLUXOS_QDRANT: int = 4
INTERVALO_S: float = 2.0
CONFIRMACOES: int = 3

# refresh desligado só durante a carga (ADR-045); volta ao padrão ao fim.
INDICE: Dict[str, Any] = {
    "settings": {"index": {"number_of_shards": 1, "number_of_replicas": 0,
                           "refresh_interval": "-1"}},
    "mappings": {"properties": {"texto": {"type": "text", "analyzer": "english"}}},
}


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


def acoes() -> Iterator[Dict[str, Any]]:
    """Um documento por passagem, na ordem do corpus: a linha n é a passagem docN (ADR-041)."""
    with open(os.path.join(DADOS, "nq", "corpus.jsonl"), encoding="utf-8") as f:
        for n, linha in enumerate(f):
            doc = registro(linha)
            if doc.get("_id") != f"doc{n}":
                raise ValueError(f"linha {n + 1}: esperado doc{n}, encontrado {doc.get('_id')!r}")
            texto = f"{campo(doc, 'title')} {campo(doc, 'text')}".strip()
            yield {"_index": NOME, "_id": str(n), "_source": {"texto": texto}}


def esperar(pronto: Callable[[], bool]) -> float:
    """Espera a condição valer em CONFIRMACOES consultas seguidas, a INTERVALO_S uma da outra.

    Devolve o instante da primeira consulta da sequência: o tempo de confirmação não conta.
    """
    seguidas = 0
    desde = time.monotonic()
    while True:
        agora = time.monotonic()
        if pronto():
            if seguidas == 0:
                desde = agora
            seguidas += 1
            if seguidas == CONFIRMACOES:
                return desde
        else:
            seguidas = 0
        time.sleep(INTERVALO_S)


def opensearch(cliente: OpenSearch, total: int, duracoes: Dict[str, float]) -> Dict[str, Any]:
    cliente.indices.create(index=NOME, body=INDICE)
    inicio = time.monotonic()
    n = 0
    for _ in streaming_bulk(cliente, acoes(), chunk_size=total,
                            max_chunk_bytes=LOTE_OPENSEARCH_BYTES):
        n += 1
        if n % AVISO == 0:
            print(f"  opensearch: {n:,} documentos ({n / (time.monotonic() - inicio):,.0f}/s)",
                  flush=True)
    duracoes["opensearch_envio"] = round(time.monotonic() - inicio, 1)
    print(f"opensearch_envio: {duracoes['opensearch_envio']} s", flush=True)

    inicio = time.monotonic()
    cliente.indices.put_settings(index=NOME, body={"index": {"refresh_interval": None}})
    cliente.indices.refresh(index=NOME)
    pronto = esperar(lambda: cliente.indices.stats(index=NOME, metric="merge")
                     ["indices"][NOME]["total"]["merges"]["current"] == 0)
    duracoes["opensearch_pronto"] = round(pronto - inicio, 1)
    print(f"opensearch_pronto: {duracoes['opensearch_pronto']} s", flush=True)

    contagem = cast(int, cliente.count(index=NOME)["count"])
    if contagem != total:
        raise RuntimeError(f"OpenSearch com {contagem} documentos, esperados {total}")
    estatisticas = cliente.indices.stats(index=NOME, metric="store,segments")
    primarios = estatisticas["indices"][NOME]["primaries"]
    return {"versao": cliente.info()["version"]["number"], "documentos": contagem,
            "bytes": primarios["store"]["size_in_bytes"],
            "segmentos": primarios["segments"]["count"]}


def qdrant(cliente: QdrantClient, vetores: npt.NDArray[np.float32],
           duracoes: Dict[str, float]) -> Dict[str, Any]:
    total = vetores.shape[0]
    cliente.create_collection(NOME, vectors_config=VectorParams(size=DIMENSAO, distance=Distance.COSINE),
                              hnsw_config=HnswConfigDiff(m=16, ef_construct=100))
    inicio = time.monotonic()
    cliente.upload_collection(NOME, vectors=vetores, ids=range(total), batch_size=LOTE_QDRANT,
                              parallel=FLUXOS_QDRANT, wait=True)
    duracoes["qdrant_envio"] = round(time.monotonic() - inicio, 1)
    print(f"qdrant_envio: {duracoes['qdrant_envio']} s", flush=True)

    inicio = time.monotonic()
    pronto = esperar(lambda: cliente.get_collection(NOME).status == CollectionStatus.GREEN)
    duracoes["qdrant_pronto"] = round(pronto - inicio, 1)
    print(f"qdrant_pronto: {duracoes['qdrant_pronto']} s", flush=True)

    info = cliente.get_collection(NOME)
    if info.points_count != total:
        raise RuntimeError(f"Qdrant com {info.points_count} pontos, esperados {total}")
    return {"versao": cliente.info().version, "pontos": info.points_count,
            "vetores_indexados": info.indexed_vectors_count, "segmentos": info.segments_count}


def main() -> int:
    parser = argparse.ArgumentParser(description="Carga do Eixo A no OpenSearch e no Qdrant")
    parser.add_argument("--registro", required=True, help="arquivo JSON com tempos e tamanhos")
    args = parser.parse_args()
    caminho_registro = cast(str, args.registro)

    caminho_vetores = os.path.join(DADOS, "passagens.npy")
    vetores: npt.NDArray[np.float32] = np.load(caminho_vetores, mmap_mode="r")
    if vetores.ndim != 2 or vetores.shape[1] != DIMENSAO or vetores.dtype != np.float32:
        print(f"{caminho_vetores}: formato inesperado {vetores.shape} {vetores.dtype}",
              file=sys.stderr)
        return 1

    busca = OpenSearch(hosts=[{"host": os.environ.get("OPENSEARCH_HOST", "opensearch"),
                               "port": 9200}], timeout=120)
    vetorial = QdrantClient(host=os.environ.get("QDRANT_HOST", "qdrant"), prefer_grpc=True,
                            timeout=120)
    if busca.indices.exists(index=NOME) or vetorial.collection_exists(NOME):
        print("Índice ou coleção já existe: a carga parte de motores vazios", file=sys.stderr)
        return 1

    duracoes: Dict[str, float] = {}
    saida: Dict[str, Any] = {
        "opensearch": opensearch(busca, vetores.shape[0], duracoes),
        "qdrant": qdrant(vetorial, vetores, duracoes),
        "duracoes_s": duracoes,
        "passagens_npy_sha256": sha256(caminho_vetores),
        "concluido_em": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    with open(caminho_registro, "w", encoding="utf-8") as f:
        json.dump(saida, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"{vetores.shape[0]:,} passagens nos dois motores")
    return 0


if __name__ == "__main__":
    sys.exit(main())
