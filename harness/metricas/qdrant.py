"""Coletor de métricas internas do Qdrant (ADR-049)."""

import argparse
import re
from typing import Any, Dict, List, Tuple, cast

from coleta import Metrica, ler_json, ler_texto, rodar

LINHA: re.Pattern[str] = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})?\s+(\S+)$")
ROTULO: re.Pattern[str] = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')
REQUISICOES: Dict[str, str] = {
    "grpc_responses_duration_seconds_count": "qd_requisicoes_total",
    "grpc_responses_duration_seconds_sum": "qd_requisicao_s_total",
    "rest_responses_duration_seconds_count": "qd_requisicoes_total",
    "rest_responses_duration_seconds_sum": "qd_requisicao_s_total",
}
MEMORIA: Dict[str, str] = {
    "memory_allocated_bytes": "qd_memoria_alocada_bytes",
    "memory_resident_bytes": "qd_memoria_residente_bytes",
}


def prometheus(texto: str) -> List[Tuple[str, Dict[str, str], float]]:
    """Nome, rótulos e valor de cada amostra do formato de texto do Prometheus."""
    amostras: List[Tuple[str, Dict[str, str], float]] = []
    for linha in texto.splitlines():
        achado = LINHA.match(linha.strip())
        if achado is None or linha.startswith("#"):
            continue
        rotulos = dict(ROTULO.findall(achado.group(2) or ""))
        amostras.append((achado.group(1), rotulos, float(achado.group(3))))
    return amostras


def ler_estatisticas(base: str) -> List[Metrica]:
    """Requisições e tempo acumulado por rota, e memória, a cada 10 s."""
    linhas: List[Metrica] = []
    for nome, rotulos, valor in prometheus(ler_texto(base + "/metrics")):
        if nome in REQUISICOES:
            partes = [nome.split("_", 1)[0], rotulos.get("method", ""),
                      rotulos.get("endpoint", ""), rotulos.get("status", "")]
            rota = " ".join(p for p in partes if p)
            metrica = REQUISICOES[nome]
            linhas.append(("qdrant", metrica, rota,
                           int(valor) if metrica == "qd_requisicoes_total" else valor))
        elif nome in MEMORIA:
            linhas.append(("qdrant", MEMORIA[nome], "", int(valor)))
    return linhas


def ler_tamanhos(base: str) -> List[Metrica]:
    """Pontos, vetores indexados e segmentos de cada coleção, a cada 60 s."""
    linhas: List[Metrica] = []
    colecoes = cast(List[Dict[str, Any]], ler_json(base + "/collections")["result"]["collections"])
    for colecao in colecoes:
        nome = str(colecao["name"])
        info = cast(Dict[str, Any], ler_json(f"{base}/collections/{nome}")["result"])
        for campo, metrica in (("points_count", "qd_pontos"),
                               ("indexed_vectors_count", "qd_vetores_indexados"),
                               ("segments_count", "qd_segmentos")):
            if info.get(campo) is not None:
                linhas.append(("qdrant", metrica, nome, int(info[campo])))
    return linhas


def main() -> None:
    p = argparse.ArgumentParser(description="Coletor de métricas internas do Qdrant")
    p.add_argument("saida", help="arquivo metricas-<serviço>.csv a criar; não pode existir")
    p.add_argument("--url", required=True, help="ex.: http://172.18.0.2:6333 (porta REST)")
    args = p.parse_args()
    rodar(str(args.saida), str(args.url).rstrip("/"), ler_estatisticas, ler_tamanhos)


if __name__ == "__main__":
    main()
