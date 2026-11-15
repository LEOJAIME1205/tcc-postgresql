"""Gerador da carga mista (ADR-068): a busca do Eixo A sob a ingestão do Eixo C em taxa fixa.

    gerador.py preparar [--saida DIR]
    gerador.py executar --saida DIR --concorrencia N --aquecimento-s A --medicao-s M --semente S
                        [--taxa R]

`preparar` cria o banco `benchmark` vazio no sistema de ingestão da arquitetura de
TCC_ARQUITETURA, como no Eixo C: o TimescaleDB no PostgreSQL da consolidada e o InfluxDB na
poliglota. Os dados da busca vêm da cópia inicial do Eixo A (`executar preparar m <arquitetura>
--copiar-de a`).

`executar` inicia a ingestão de R métricas/s (o gerador do Eixo C, subcomando `ingerir`), espera
a ingestão começar e mais 10 s, e roda o gerador do Eixo A com N clientes, A segundos de
aquecimento e M de medição. No fim da busca, para a ingestão. Sem --taxa, roda só a busca, que é
a configuração de base. Grava o operacoes.csv e o gerador.json do A, este com o bloco `mista`, e
o metricas-ingestao.csv e o ingestao.json da ingestão.
"""

import argparse
import csv
import json
import os
import signal
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple, cast

APP: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GERADOR_A: str = os.path.join(APP, "eixo-a-busca", "cargas", "gerador.py")
GERADOR_C: str = os.path.join(APP, "eixo-c-temporal", "cargas", "gerador.py")
INGESTAO: Dict[str, str] = {"consolidada": "timescaledb", "poliglota": "influxdb"}
VERSAO: str = "1"
TRABALHADORES: int = 4  # do tsbs_load (ADR-068, item 3)
ANTECEDENCIA_S: float = 10.0  # a ingestão começa 10 s antes do aquecimento da busca
ESPERA_INICIO_S: float = 300.0  # prazo para o primeiro relatório da ingestão
PARADA_S: float = 300.0  # prazo para a ingestão parar depois do SIGTERM
FOLGA_S: int = 3600  # limite de segurança da ingestão, além do aquecimento e da medição
TOLERANCIA_TAXA: float = 0.01  # validade: taxa obtida na medição a menos de 1% da planejada


def avisar(mensagem: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {mensagem}", file=sys.stderr, flush=True)


def ler_json(caminho: str) -> Dict[str, Any]:
    with open(caminho, encoding="utf-8") as f:
        return cast(Dict[str, Any], json.load(f))


def gravar_json(caminho: str, dados: Dict[str, Any]) -> None:
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dados, f, indent=2, ensure_ascii=False)
        f.write("\n")


def arquitetura_do_ambiente() -> str:
    arquitetura = os.environ.get("TCC_ARQUITETURA", "")
    if arquitetura not in INGESTAO:
        raise SystemExit(f"TCC_ARQUITETURA precisa ser {' ou '.join(INGESTAO)}, não {arquitetura!r}")
    return arquitetura


def ambiente_ingestao(arquitetura: str) -> Dict[str, str]:
    """O ambiente do container, com o sistema de ingestão no lugar da arquitetura da busca."""
    return {**os.environ, "TCC_ARQUITETURA": INGESTAO[arquitetura]}


def preparar(saida: str) -> int:
    """Banco benchmark vazio no sistema de ingestão, pelo preparo do Eixo C."""
    arquitetura = arquitetura_do_ambiente()
    return subprocess.run([sys.executable, GERADOR_C, "preparar", "--saida", saida],
                          env=ambiente_ingestao(arquitetura)).returncode


def serie_carregada(metricas: str) -> List[Tuple[int, int]]:
    """(ts_ms, métricas carregadas) de cada relatório de 1 s do tsbs_load."""
    serie: List[Tuple[int, int]] = []
    with open(metricas, encoding="utf-8") as f:
        for linha in csv.DictReader(f):
            if linha["metrica"] == "tsbs_metricas_total":
                serie.append((int(linha["ts_ms"]), int(linha["valor"])))
    return serie


def taxa_no_trecho(serie: List[Tuple[int, int]], inicio_ms: int, fim_ms: int) -> Optional[float]:
    """Métricas/s carregadas entre o último relatório até inicio_ms e o último até fim_ms."""
    antes = [p for p in serie if p[0] <= inicio_ms]
    ate_o_fim = [p for p in serie if p[0] <= fim_ms]
    if not antes or not ate_o_fim or ate_o_fim[-1][0] <= antes[-1][0]:
        return None
    (t0, v0), (t1, v1) = antes[-1], ate_o_fim[-1]
    return (v1 - v0) / ((t1 - t0) / 1000)


def esperar_inicio(metricas: str, processo: "subprocess.Popen[bytes]") -> bool:
    """Espera o primeiro relatório da ingestão; False se ela terminar ou o prazo passar."""
    prazo = time.monotonic() + ESPERA_INICIO_S
    while time.monotonic() < prazo:
        if processo.poll() is not None:
            return False
        if os.path.exists(metricas) and serie_carregada(metricas):
            return True
        time.sleep(1.0)
    return False


def parar_ingestao(processo: "subprocess.Popen[bytes]") -> int:
    """SIGTERM e espera; se não parar no prazo, encerra."""
    if processo.poll() is None:
        processo.send_signal(signal.SIGTERM)
    try:
        return processo.wait(timeout=PARADA_S)
    except subprocess.TimeoutExpired:
        avisar(f"a ingestão não parou em {PARADA_S:.0f} s; encerrada")
        processo.kill()
        return processo.wait()


def executar(saida: str, concorrencia: int, aquecimento_s: int, medicao_s: int, semente: int,
             taxa: Optional[int]) -> int:
    arquitetura = arquitetura_do_ambiente()
    ferramenta = os.path.join(saida, "ferramenta")
    os.makedirs(ferramenta, exist_ok=True)
    metricas = os.path.join(saida, "metricas-ingestao.csv")
    ingestao: Optional[subprocess.Popen[bytes]] = None
    if taxa is not None:
        avisar(f"ingestão de {taxa} métricas/s em {INGESTAO[arquitetura]}")
        with open(os.path.join(ferramenta, "ingestao.log"), "wb") as log:
            ingestao = subprocess.Popen(
                [sys.executable, GERADOR_C, "ingerir", "--saida", saida, "--taxa", str(taxa),
                 "--trabalhadores", str(TRABALHADORES),
                 "--limite-s", str(aquecimento_s + medicao_s + FOLGA_S)],
                env=ambiente_ingestao(arquitetura), stdout=log, stderr=subprocess.STDOUT)
        if not esperar_inicio(metricas, ingestao):
            avisar("a ingestão não começou; veja ferramenta/ingestao.log")
            parar_ingestao(ingestao)
            return 1
        time.sleep(ANTECEDENCIA_S)

    avisar(f"busca com {concorrencia} cliente(s)")
    busca = subprocess.run([sys.executable, GERADOR_A, "executar", "--saida", saida,
                            "--concorrencia", str(concorrencia), "--aquecimento-s", str(aquecimento_s),
                            "--medicao-s", str(medicao_s), "--semente", str(semente)]).returncode
    codigo_ingestao = parar_ingestao(ingestao) if ingestao is not None else 0

    caminho = os.path.join(saida, "gerador.json")
    if not os.path.exists(caminho):
        avisar("o gerador da busca não gravou o gerador.json")
        return busca or 1
    g = ler_json(caminho)
    mista: Dict[str, Any] = {"taxa_metricas_s": taxa, "antecedencia_s": ANTECEDENCIA_S,
                             "sistema_ingestao": INGESTAO[arquitetura] if taxa is not None else None,
                             "trabalhadores_ingestao": TRABALHADORES if taxa is not None else None}
    versao_ingestao = ""
    registro_ingestao = os.path.join(saida, "ingestao.json")
    if taxa is not None and os.path.exists(registro_ingestao) and os.path.exists(metricas):
        ing = ler_json(registro_ingestao)
        versao_ingestao = f"; ingestão: tcc-gerador-temporal {ing.get('versao')}"
        inicio_ms, fim_ms = int(g["medicao_inicio_ms"]), int(g["medicao_fim_ms"])
        obtida = taxa_no_trecho(serie_carregada(metricas), inicio_ms, fim_ms)
        mista["taxa_na_medicao_metricas_s"] = obtida
        mista["taxa_sustentada"] = obtida is not None and abs(obtida / taxa - 1) <= TOLERANCIA_TAXA
        mista["ingestao_cobriu_medicao"] = (int(ing["inicio_ms"]) <= inicio_ms
                                            and int(ing["fim_ms"]) >= fim_ms)
        mista["ingestao"] = ing
        if not mista["taxa_sustentada"]:
            avisar(f"taxa não sustentada na medição: {obtida} de {taxa} métricas/s")
    g["ferramenta"] = "tcc-gerador-mista"
    g["versao"] = f"{VERSAO} (busca: tcc-gerador-busca {g.get('versao')}{versao_ingestao})"
    parametros = cast(Dict[str, Any], g.get("parametros") or {})
    g["parametros"] = {**parametros, "taxa_metricas_s": taxa}
    g["mista"] = mista
    gravar_json(caminho, g)
    if busca != 0:
        return busca
    if codigo_ingestao != 0:
        avisar(f"a ingestão terminou com código {codigo_ingestao}; veja ferramenta/ingestao.log")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Gerador da carga mista (ADR-068)")
    sub = parser.add_subparsers(dest="comando", required=True)
    pp = sub.add_parser("preparar", help="cria o banco benchmark vazio no sistema de ingestão")
    pp.add_argument("--saida", default="/saida")
    pe = sub.add_parser("executar", help="busca com N clientes sob ingestão de R métricas/s")
    pe.add_argument("--saida", required=True)
    pe.add_argument("--concorrencia", type=int, required=True)
    pe.add_argument("--aquecimento-s", type=int, required=True)
    pe.add_argument("--medicao-s", type=int, required=True)
    pe.add_argument("--semente", type=int, required=True)
    pe.add_argument("--taxa", type=int, default=None, help="métricas/s da ingestão; sem ela, só a busca")
    args = parser.parse_args()

    if cast(str, args.comando) == "preparar":
        return preparar(cast(str, args.saida))
    return executar(cast(str, args.saida), cast(int, args.concorrencia), cast(int, args.aquecimento_s),
                    cast(int, args.medicao_s), cast(int, args.semente), cast(Optional[int], args.taxa))


if __name__ == "__main__":
    sys.exit(main())
