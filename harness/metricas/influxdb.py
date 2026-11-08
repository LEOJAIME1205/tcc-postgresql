"""Coletor de métricas internas do InfluxDB 1.x (ADR-064)."""

import argparse
import subprocess
from typing import Any, Dict, List, Tuple, cast

from coleta import Leitor, Metrica, ler_json, rodar

DIRETORIO_DADOS: str = "/var/lib/influxdb"
# (grupo do /debug/vars, campo, métrica); os grupos com rótulo (um por shard) somam as
# instâncias. Nomes conferidos no /debug/vars do InfluxDB 1.13.1 em 08/11/2026.
CAMPOS: Tuple[Tuple[str, str, str], ...] = (
    ("write", "pointReq", "influx_pontos_escritos_total"),
    ("write", "writeError", "influx_erros_escrita_total"),
    ("write", "writeDrop", "influx_pontos_descartados_total"),
    ("httpd", "writeReq", "influx_requisicoes_escrita_total"),
    ("httpd", "queryReq", "influx_consultas_total"),
    ("httpd", "queryReqDurationNs", "influx_consulta_ns_total"),
    ("tsm1_cache", "memBytes", "influx_cache_bytes"),
    ("tsm1_cache", "WALCompactionTimeMs", "influx_cache_compactacao_ms_total"),
    ("tsm1_engine", "cacheCompactions", "influx_compactacoes_cache_total"),
    ("tsm1_engine", "tsmLevel1Compactions", "influx_compactacoes_nivel1_total"),
    ("tsm1_engine", "tsmLevel2Compactions", "influx_compactacoes_nivel2_total"),
    ("tsm1_engine", "tsmLevel3Compactions", "influx_compactacoes_nivel3_total"),
    ("tsm1_engine", "tsmOptimizeCompactions", "influx_compactacoes_otimizacao_total"),
    ("tsm1_engine", "tsmFullCompactions", "influx_compactacoes_completas_total"),
    ("tsm1_engine", "cacheCompactionsActive", "influx_compactacoes_ativas"),
    ("tsm1_engine", "tsmLevel1CompactionsActive", "influx_compactacoes_ativas"),
    ("tsm1_engine", "tsmLevel2CompactionsActive", "influx_compactacoes_ativas"),
    ("tsm1_engine", "tsmLevel3CompactionsActive", "influx_compactacoes_ativas"),
    ("tsm1_engine", "tsmOptimizeCompactionsActive", "influx_compactacoes_ativas"),
    ("tsm1_engine", "tsmFullCompactionsActive", "influx_compactacoes_ativas"),
    ("tsm1_filestore", "diskBytes", "influx_tsm_bytes"),
    ("localStore", "pointsWritten", "influx_pontos_gravados_total"),
    ("queryExecutor", "queriesExecuted", "influx_consultas_executadas_total"),
    ("queryExecutor", "queryDurationNs", "influx_consulta_execucao_ns_total"),
    ("runtime", "HeapInUse", "influx_heap_bytes"),
    ("runtime", "Sys", "influx_memoria_go_bytes"),
)


def ler_estatisticas(base: str) -> List[Metrica]:
    """Escrita, consultas, cache do TSM, compactações, séries e memória, a cada 10 s."""
    variaveis = cast(Dict[str, Any], ler_json(base + "/debug/vars"))
    somas: Dict[str, int] = {}
    series: List[Metrica] = []
    for chave, grupo in variaveis.items():
        if not isinstance(grupo, dict):
            continue
        conteudo = cast(Dict[str, Any], grupo)
        nome = str(conteudo.get("name", chave))
        valores = cast(Dict[str, Any], conteudo.get("values", {}))
        for grupo_campo, campo, metrica in CAMPOS:
            if nome == grupo_campo and isinstance(valores.get(campo), (int, float)):
                somas[metrica] = somas.get(metrica, 0) + int(valores[campo])
        if nome == "database":
            rotulos = cast(Dict[str, str], conteudo.get("tags", {}))
            if isinstance(valores.get("numSeries"), (int, float)):
                series.append(("influxdb", "influx_series", rotulos.get("database", ""),
                               int(valores["numSeries"])))
    return [("influxdb", metrica, "", valor) for metrica, valor in somas.items()] + series


def tamanhos(container: str) -> Leitor:
    """Tamanho do diretório de dados, lido dentro do container a cada 60 s."""

    def ler(_base: str) -> List[Metrica]:
        try:
            r = subprocess.run(["docker", "exec", container, "du", "-sb", DIRETORIO_DADOS],
                               capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired as erro:
            raise ValueError(f"du sem resposta em {erro.timeout} s") from erro
        if r.returncode != 0:
            raise ValueError(f"du terminou com código {r.returncode}: {r.stderr.strip()}")
        return [("influxdb", "influx_dados_bytes", "", int(r.stdout.split()[0]))]

    return ler


def main() -> None:
    p = argparse.ArgumentParser(description="Coletor de métricas internas do InfluxDB 1.x")
    p.add_argument("saida", help="arquivo metricas-<serviço>.csv a criar; não pode existir")
    p.add_argument("--url", required=True, help="ex.: http://172.18.0.2:8086")
    p.add_argument("--container", required=True, help="container do InfluxDB, para o tamanho dos dados")
    args = p.parse_args()
    rodar(str(args.saida), str(args.url).rstrip("/"), ler_estatisticas, tamanhos(str(args.container)))


if __name__ == "__main__":
    main()
