"""Coletor de métricas internas do OpenSearch (ADR-049)."""

import argparse
from typing import Any, Dict, List, cast

from coleta import Metrica, ler_json, rodar

ESTATISTICAS: str = "/_nodes/stats/indices,jvm"
INDICES: str = "/_cat/indices?format=json&bytes=b&h=index,docs.count,pri.store.size"


def ler_estatisticas(base: str) -> List[Metrica]:
    """Busca, indexação, heap, coletas de lixo, cache e segmentos, a cada 10 s."""
    nos = cast(Dict[str, Any], ler_json(base + ESTATISTICAS)["nodes"])
    linhas: List[Metrica] = []
    for no in nos.values():
        indices = cast(Dict[str, Any], no["indices"])
        jvm = cast(Dict[str, Any], no["jvm"])
        linhas.extend([
            ("opensearch", "os_buscas_total", "", int(indices["search"]["query_total"])),
            ("opensearch", "os_busca_ms_total", "", int(indices["search"]["query_time_in_millis"])),
            ("opensearch", "os_indexacoes_total", "", int(indices["indexing"]["index_total"])),
            ("opensearch", "os_indexacao_ms_total", "",
             int(indices["indexing"]["index_time_in_millis"])),
            ("opensearch", "os_cache_consultas_bytes", "",
             int(indices["query_cache"]["memory_size_in_bytes"])),
            ("opensearch", "os_segmentos", "", int(indices["segments"]["count"])),
            ("opensearch", "os_heap_usado_bytes", "", int(jvm["mem"]["heap_used_in_bytes"])),
        ])
        for coletor, gc in cast(Dict[str, Any], jvm["gc"]["collectors"]).items():
            linhas.append(("opensearch", "os_gc_total", coletor, int(gc["collection_count"])))
            linhas.append(("opensearch", "os_gc_ms_total", coletor,
                           int(gc["collection_time_in_millis"])))
    return linhas


def ler_tamanhos(base: str) -> List[Metrica]:
    """Tamanho e documentos de cada índice, a cada 60 s."""
    linhas: List[Metrica] = []
    for indice in cast(List[Dict[str, Any]], ler_json(base + INDICES)):
        nome = str(indice["index"])
        if indice.get("pri.store.size") is not None:
            linhas.append(("opensearch", "os_indice_bytes", nome, int(indice["pri.store.size"])))
        if indice.get("docs.count") is not None:
            linhas.append(("opensearch", "os_documentos", nome, int(indice["docs.count"])))
    return linhas


def main() -> None:
    p = argparse.ArgumentParser(description="Coletor de métricas internas do OpenSearch")
    p.add_argument("saida", help="arquivo metricas-<serviço>.csv a criar; não pode existir")
    p.add_argument("--url", required=True, help="ex.: http://172.18.0.3:9200")
    args = p.parse_args()
    rodar(str(args.saida), str(args.url).rstrip("/"), ler_estatisticas, ler_tamanhos)


if __name__ == "__main__":
    main()
