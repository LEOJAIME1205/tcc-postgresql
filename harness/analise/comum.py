"""Leituras comuns às análises: execuções de uma janela, CPU dos containers, volume gravado
no SSD e o instante em que ele passa de um limite, e CSV de saída."""

import csv
import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple, cast

from harness.orquestracao import arquitetura

BRUTOS: str = os.path.join(arquitetura.RAIZ, "resultados", "brutos")
PROCESSADOS: str = os.path.join(arquitetura.RAIZ, "resultados", "processados")


def ler_json(caminho: str) -> Dict[str, Any]:
    with open(caminho, encoding="utf-8") as f:
        return cast(Dict[str, Any], json.load(f))


def execucoes_da_janela(janela: str) -> List[str]:
    """Diretórios das execuções que terminaram em ok, na ordem da janela."""
    estado = ler_json(os.path.join(BRUTOS, "janelas", janela, "estado.json"))
    diretorios: List[str] = []
    for item in cast(List[Dict[str, Any]], estado.get("execucoes", [])):
        tentativas = cast(List[str], item.get("tentativas", []))
        if item.get("estado") != "ok" or not tentativas:
            print(f"aviso: {item.get('execucao')} não terminou em ok; fica de fora", file=sys.stderr)
            continue
        identificador = tentativas[-1]
        eixo = identificador.split("_")[1]
        diretorios.append(os.path.join(BRUTOS, eixo, identificador))
    return diretorios


def servico_do_container(rotulo: str) -> str:
    """Nome do serviço a partir do nome do container (tcc-b-outbox-relay-1 → relay)."""
    return "gerador" if "-gerador-run-" in rotulo else rotulo.rsplit("-", 2)[-2]


def cpu_nucleos(caminho: str, inicio_ms: int, fim_ms: int) -> Dict[str, float]:
    """Núcleos médios de cada container entre inicio_ms e fim_ms (cont_cpu_us_total)."""
    series: Dict[str, List[Tuple[int, int]]] = {}
    with open(caminho, encoding="utf-8") as f:
        for linha in csv.DictReader(f):
            if linha["metrica"] != "cont_cpu_us_total":
                continue
            ts = int(linha["ts_ms"])
            if inicio_ms <= ts <= fim_ms:
                series.setdefault(linha["rotulo"], []).append((ts, int(linha["valor"])))
    nucleos: Dict[str, float] = {}
    for rotulo, serie in series.items():
        if len(serie) >= 2 and serie[-1][0] > serie[0][0]:
            (t0, v0), (t1, v1) = serie[0], serie[-1]
            nucleos[rotulo] = (v1 - v0) / ((t1 - t0) * 1000)
    return nucleos


def disco_gravado_bytes(caminho: str) -> Optional[int]:
    """Bytes gravados no SSD durante a coleta inteira da execução (disco_escrita_bytes_total).
    O coletor do host só registra o disco do experimento, achado pelo modelo."""
    series: Dict[str, Tuple[int, int]] = {}
    with open(caminho, encoding="utf-8") as f:
        for linha in csv.DictReader(f):
            if linha["metrica"] != "disco_escrita_bytes_total":
                continue
            valor = int(linha["valor"])
            anterior = series.get(linha["rotulo"])
            series[linha["rotulo"]] = (valor if anterior is None else anterior[0], valor)
    if not series:
        return None
    return sum(ultimo - primeiro for primeiro, ultimo in series.values())


def disco_bytes_por_s(caminho: str, inicio_ms: int, fim_ms: int,
                      metrica: str = "disco_escrita_bytes_total") -> Optional[float]:
    """Bytes gravados (ou lidos, com disco_leitura_bytes_total) por segundo no SSD entre
    inicio_ms e fim_ms, pela primeira e pela última amostra de cada dispositivo dentro do
    trecho, como em cpu_nucleos."""
    series: Dict[str, List[Tuple[int, int]]] = {}
    with open(caminho, encoding="utf-8") as f:
        for linha in csv.DictReader(f):
            if linha["metrica"] != metrica:
                continue
            ts = int(linha["ts_ms"])
            if inicio_ms <= ts <= fim_ms:
                series.setdefault(linha["rotulo"], []).append((ts, int(linha["valor"])))
    taxas: List[float] = []
    for serie in series.values():
        if len(serie) >= 2 and serie[-1][0] > serie[0][0]:
            (t0, v0), (t1, v1) = serie[0], serie[-1]
            taxas.append((v1 - v0) / ((t1 - t0) / 1000))
    return sum(taxas) if taxas else None


def instante_do_volume(caminho: str, limite_bytes: float) -> Optional[int]:
    """Primeiro ts_ms em que o volume gravado no SSD, contado desde o início da coleta,
    chega a limite_bytes; None se não chegar."""
    inicio: Dict[str, int] = {}
    ultimo: Dict[str, int] = {}
    with open(caminho, encoding="utf-8") as f:
        for linha in csv.DictReader(f):
            if linha["metrica"] != "disco_escrita_bytes_total":
                continue
            valor = int(linha["valor"])
            inicio.setdefault(linha["rotulo"], valor)
            ultimo[linha["rotulo"]] = valor
            if sum(ultimo[r] - inicio[r] for r in ultimo) >= limite_bytes:
                return int(linha["ts_ms"])
    return None


def series_por_rotulo(caminho: str, metricas: Sequence[str], inicio_ms: int,
                      fim_ms: int) -> Dict[Tuple[str, str], List[Tuple[int, float]]]:
    """Amostras entre inicio_ms e fim_ms de cada (métrica, rótulo) pedida, em ordem de tempo,
    de qualquer CSV de métricas do harness (ts_ms, origem, metrica, rotulo, valor)."""
    series: Dict[Tuple[str, str], List[Tuple[int, float]]] = {}
    with open(caminho, encoding="utf-8") as f:
        for linha in csv.DictReader(f):
            if linha["metrica"] not in metricas:
                continue
            ts = int(linha["ts_ms"])
            if inicio_ms <= ts <= fim_ms:
                series.setdefault((linha["metrica"], linha["rotulo"]), []).append(
                    (ts, float(linha["valor"])))
    for serie in series.values():
        serie.sort()
    return series


# Linha de um CSV de resumo lido de volta: tudo é texto.
Linha = Dict[str, str]


def ler_execucoes(caminho: str) -> List[Linha]:
    with open(caminho, encoding="utf-8", newline="") as f:
        return cast(List[Linha], list(csv.DictReader(f)))


def numero(linha: Linha, coluna: str) -> Optional[float]:
    """Valor numérico de uma coluna de CSV gravado por gravar_csv; vazio vira None e booleano
    vira 0 ou 1."""
    texto = linha.get(coluna) or ""
    if texto in ("", "None"):
        return None
    if texto in ("True", "False"):
        return float(texto == "True")
    return float(texto)


def gravar_csv(caminho: str, linhas: List[Dict[str, Any]]) -> None:
    """Grava as linhas com a união das colunas, na ordem em que aparecem."""
    colunas: List[str] = []
    for linha in linhas:
        colunas += [c for c in linha if c not in colunas]
    with open(caminho, "w", encoding="utf-8", newline="") as f:
        escritor = csv.DictWriter(f, fieldnames=colunas)
        escritor.writeheader()
        escritor.writerows(linhas)
