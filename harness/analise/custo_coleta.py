"""Custo da coleta interna (ADR-021, item 6, e ADR-066).

Compara as execuções com e sem coleta interna de uma janela: vazão e
percentis de latência de cada operação na janela de medição, e CPU dos
containers. O critério da ADR-021 é a diferença entre as médias comparada com
a variação entre repetições.

    uv run python -m harness.analise.custo_coleta 20261108-2233
"""

import argparse
import csv
import json
import os
import statistics
import sys
from typing import Any, Dict, List, Optional, Tuple, cast

import numpy as np

from harness.orquestracao import arquitetura

BRUTOS: str = os.path.join(arquitetura.RAIZ, "resultados", "brutos")
PROCESSADOS: str = os.path.join(arquitetura.RAIZ, "resultados", "processados")
PERCENTIS: Tuple[int, ...] = (50, 95, 99)


def _ler_json(caminho: str) -> Dict[str, Any]:
    with open(caminho, encoding="utf-8") as f:
        return cast(Dict[str, Any], json.load(f))


def execucoes_da_janela(janela: str) -> List[str]:
    """Diretórios das execuções que terminaram em ok, na ordem da janela."""
    estado = _ler_json(os.path.join(BRUTOS, "janelas", janela, "estado.json"))
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


def _cpu_nucleos(caminho: str, inicio_ms: int, fim_ms: int) -> Dict[str, float]:
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


def resumir(diretorio: str) -> Dict[str, Any]:
    """Vazão, percentis e CPU de uma execução, só na janela de medição."""
    manifesto = _ler_json(os.path.join(diretorio, "manifesto.json"))
    gerador = _ler_json(os.path.join(diretorio, "gerador.json"))
    inicio_ms = int(gerador["medicao_inicio_ms"])
    fim_ms = int(gerador["medicao_fim_ms"])
    if fim_ms <= inicio_ms:
        raise ValueError(f"{diretorio}: janela de medição vazia")
    duracao_s = (fim_ms - inicio_ms) / 1000
    latencias: Dict[str, List[int]] = {}
    erros = 0
    with open(os.path.join(diretorio, "operacoes.csv"), encoding="utf-8") as f:
        next(f)
        for linha in f:
            ts, operacao, latencia, ok, _cliente, _itens = linha.rstrip("\n").split(",")
            if not inicio_ms * 1000 <= int(ts) < fim_ms * 1000:
                continue
            if ok != "1" or not latencia:
                erros += 1
                continue
            latencias.setdefault(operacao, []).append(int(latencia))
    r: Dict[str, Any] = {
        "execucao": os.path.basename(diretorio),
        "coleta_interna": bool(manifesto.get("coleta_interna", True)),
        "repeticao": int(manifesto["repeticao"]),
        "semente": int(manifesto["semente"]),
        "erros": erros,
    }
    for operacao in sorted(latencias):
        valores = np.asarray(latencias[operacao], dtype=np.int64)
        r[f"{operacao}_por_s"] = valores.size / duracao_s
        for p in PERCENTIS:
            r[f"{operacao}_p{p}_ms"] = float(np.percentile(valores, p)) / 1000
    for rotulo, nucleos in sorted(_cpu_nucleos(os.path.join(diretorio, "metricas-host.csv"),
                                               inicio_ms, fim_ms).items()):
        servico = "gerador" if "-gerador-run-" in rotulo else rotulo.rsplit("-", 2)[-2]
        r[f"cpu_{servico}_nucleos"] = nucleos
    return r


def comparar(resumos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Médias com e sem coleta, desvios entre repetições e diferenças por par de semente."""
    com = sorted((r for r in resumos if r["coleta_interna"]), key=lambda r: r["repeticao"])
    sem = sorted((r for r in resumos if not r["coleta_interna"]), key=lambda r: r["repeticao"])
    if len(com) < 2 or len(sem) < 2:
        raise ValueError("são precisas pelo menos 2 execuções com coleta e 2 sem")
    pares: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    sementes_sem = {r["semente"]: r for r in sem}
    for r in com:
        par: Optional[Dict[str, Any]] = sementes_sem.get(r["semente"])
        if par is not None:
            pares.append((r, par))
    metricas = [k for k, v in com[0].items() if isinstance(v, float)]
    linhas: List[Dict[str, Any]] = []
    for k in metricas:
        vc = [float(r[k]) for r in com if k in r]
        vs = [float(r[k]) for r in sem if k in r]
        if len(vc) < 2 or len(vs) < 2:
            continue
        mc, ms = statistics.mean(vc), statistics.mean(vs)
        dc, ds = statistics.stdev(vc), statistics.stdev(vs)
        dp = ((dc ** 2 + ds ** 2) / 2) ** 0.5
        linhas.append({
            "metrica": k,
            "media_com": mc,
            "media_sem": ms,
            "diferenca_pct": (mc / ms - 1) * 100 if ms else float("nan"),
            "dp_com": dc,
            "dp_sem": ds,
            "dp_entre_repeticoes": dp,
            "diferenca_sobre_dp": abs(mc - ms) / dp if dp else float("nan"),
            "diferencas_pares_pct": " ".join(f"{(float(a[k]) / float(b[k]) - 1) * 100:+.2f}"
                                             for a, b in pares if k in a and k in b and float(b[k])),
        })
    return linhas


def _gravar(caminho: str, linhas: List[Dict[str, Any]]) -> None:
    colunas: List[str] = []
    for linha in linhas:
        colunas += [c for c in linha if c not in colunas]
    with open(caminho, "w", encoding="utf-8", newline="") as f:
        escritor = csv.DictWriter(f, fieldnames=colunas)
        escritor.writeheader()
        escritor.writerows(linhas)


def main() -> None:
    p = argparse.ArgumentParser(description="Custo da coleta interna (ADR-021 e ADR-066)")
    p.add_argument("janela", help="identificador da janela, ex.: 20261108-2233")
    p.add_argument("--saida", default=PROCESSADOS, help="diretório dos CSV de saída")
    args = p.parse_args()
    janela = str(args.janela)
    resumos = [resumir(d) for d in execucoes_da_janela(janela)]
    linhas = comparar(resumos)
    os.makedirs(str(args.saida), exist_ok=True)
    _gravar(os.path.join(str(args.saida), f"custo-coleta-{janela}-execucoes.csv"), resumos)
    _gravar(os.path.join(str(args.saida), f"custo-coleta-{janela}-comparacao.csv"), linhas)
    print(f"{'métrica':26s} {'média com':>12s} {'média sem':>12s} {'dif. %':>7s} "
          f"{'dp entre rep.':>13s} {'|dif|/dp':>8s}  diferenças por par de semente (%)")
    for linha in linhas:
        print(f"{linha['metrica']:26s} {linha['media_com']:12.3f} {linha['media_sem']:12.3f} "
              f"{linha['diferenca_pct']:+7.2f} {linha['dp_entre_repeticoes']:13.3f} "
              f"{linha['diferenca_sobre_dp']:8.2f}  {linha['diferencas_pares_pct']}")


if __name__ == "__main__":
    main()
