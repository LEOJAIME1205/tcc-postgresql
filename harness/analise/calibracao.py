"""Análise da calibração (ADR-067, itens 6 e 7).

Resume cada execução das rampas de concorrência e aplica os critérios fixados
antes das rampas:

- saturação (A e C): o menor nível em que dobrar a concorrência aumenta a vazão
  em menos de 10%;
- vazão sustentável (B): a maior vazão de consumo num nível em que a fila cresce
  no máximo 1% do que foi enfileirado na medição (ADR-054, item 5);
- relay como gargalo: vazão da outbox de pelo menos 90% da vazão isolada (ADR-053);
- gerador como gargalo: acima de 1,7 núcleo no A e no B, e de 85% das 4 CPUs
  lógicas no C (ADR-065), com a CPU do gerador medida na parte da medição anterior
  aos primeiros 60 GiB gravados no SSD (revisão da ADR-065, item 3); nas execuções
  que não chegam a esse volume, é a medição inteira;
- durações: abaixo da saturação, a vazão do primeiro e do último minuto da
  medição difere em menos de 5%.

Diagnósticos das durações, que não entram nos critérios:
- o último minuto do aquecimento comparado com a média da medição (A e B);
- no A, o ruído esperado da diferença entre dois minutos só pelo sorteio das
  consultas, num sistema estacionário: sqrt(2) · CV das latências / sqrt(n), com n
  as consultas do primeiro minuto. É a aproximação de processo de renovação para
  clientes em laço fechado.

Marcação do cache pSLC (revisão da ADR-067, item 3): execuções que gravaram mais de
60 GiB no SSD durante a coleta. Não sai da análise nem muda os critérios. No C, a vazão
antes desse volume é reportada como diagnóstico; a vazão da execução continua sendo a
da carga inteira (ADR-065, item 2, e ADR-004).

    uv run python -m harness.analise.calibracao <janela> [<janela> ...]
        [--relay resultados/relay/vazao-isolada.json]
"""

import argparse
import csv
import os
from array import array
from typing import Any, Dict, List, Optional, Tuple, cast

import numpy as np

from harness.analise.comum import (PROCESSADOS, cpu_nucleos, disco_gravado_bytes,
                                   execucoes_da_janela, gravar_csv, instante_do_volume,
                                   ler_json, servico_do_container)

PERCENTIS: Tuple[int, ...] = (50, 95, 99)
MINUTO_MS: int = 60_000
GANHO_MIN_SATURACAO: float = 0.10
CRESCIMENTO_MAX_PCT: float = 1.0
FRACAO_RELAY_GARGALO: float = 0.90
CPU_GERADOR_MAX: Dict[str, float] = {"a": 1.7, "b": 1.7, "c": 3.4}
VARIACAO_MAX_PCT: float = 5.0
OPERACAO_PRINCIPAL: Dict[str, str] = {"a": "busca_hibrida", "b": "consome"}
# Abaixo dos ≈62 GiB de cache pSLC medidos com o disco vazio (ambiente/README.md).
CACHE_PSLC_GIB: float = 60.0
# Trecho mínimo para avaliar a CPU do gerador quando o volume chega cedo ao limiar.
AVALIACAO_MIN_MS: int = 10_000


class Operacoes:
    """Operações bem-sucedidas da janela de medição, por tipo."""

    def __init__(self) -> None:
        self.latencias_us: Dict[str, array[int]] = {}
        self.primeiro_minuto: Dict[str, int] = {}
        self.ultimo_minuto: Dict[str, int] = {}
        self.fim_do_aquecimento: Dict[str, int] = {}
        self.erros: int = 0


def ler_operacoes(caminho: str, inicio_ms: int, fim_ms: int) -> Operacoes:
    """Lê o operacoes.csv guardando só a janela de medição, em arrays de int64, e
    contando as operações do último minuto do aquecimento."""
    ops = Operacoes()
    inicio_us, fim_us = inicio_ms * 1000, fim_ms * 1000
    fim_primeiro_us = inicio_us + MINUTO_MS * 1000
    inicio_ultimo_us = fim_us - MINUTO_MS * 1000
    inicio_aquecimento_us = inicio_us - MINUTO_MS * 1000
    with open(caminho, encoding="utf-8") as f:
        cabecalho = next(f).rstrip("\n").split(",")
        if cabecalho[:4] != ["ts_us", "operacao", "latencia_us", "ok"]:
            raise ValueError(f"{caminho}: cabeçalho inesperado {cabecalho}")
        for linha in f:
            campos = linha.split(",", 4)
            ts = int(campos[0])
            if inicio_aquecimento_us <= ts < inicio_us and campos[3] == "1":
                operacao = campos[1]
                ops.fim_do_aquecimento[operacao] = ops.fim_do_aquecimento.get(operacao, 0) + 1
            if not inicio_us <= ts < fim_us:
                continue
            if campos[3] != "1" or not campos[2]:
                ops.erros += 1
                continue
            operacao = campos[1]
            destino = ops.latencias_us.get(operacao)
            if destino is None:
                destino = array("q")
                ops.latencias_us[operacao] = destino
            destino.append(int(campos[2]))
            if ts < fim_primeiro_us:
                ops.primeiro_minuto[operacao] = ops.primeiro_minuto.get(operacao, 0) + 1
            if ts >= inicio_ultimo_us:
                ops.ultimo_minuto[operacao] = ops.ultimo_minuto.get(operacao, 0) + 1
    return ops


def _variacao_pct(primeiro: float, ultimo: float) -> Optional[float]:
    return (ultimo / primeiro - 1) * 100 if primeiro > 0 else None


def _resumir_operacoes(r: Dict[str, Any], eixo: str, ops: Operacoes, duracao_s: float) -> None:
    """Vazão e percentis de cada operação; no B, o crescimento da fila na medição."""
    r["erros"] = ops.erros
    for operacao in sorted(ops.latencias_us):
        valores = np.frombuffer(ops.latencias_us[operacao], dtype=np.int64)
        r[f"{operacao}_por_s"] = valores.size / duracao_s
        for p in PERCENTIS:
            r[f"{operacao}_p{p}_ms"] = float(np.percentile(valores, p)) / 1000
    principal = OPERACAO_PRINCIPAL[eixo]
    r["vazao_por_s"] = r.get(f"{principal}_por_s", 0.0)
    primeiro = ops.primeiro_minuto.get(principal, 0) * 1000 / MINUTO_MS
    ultimo = ops.ultimo_minuto.get(principal, 0) * 1000 / MINUTO_MS
    r["primeiro_minuto_por_s"] = primeiro
    r["ultimo_minuto_por_s"] = ultimo
    r["variacao_minutos_pct"] = _variacao_pct(primeiro, ultimo)
    aquecimento = ops.fim_do_aquecimento.get(principal, 0) * 1000 / MINUTO_MS
    r["fim_do_aquecimento_por_s"] = aquecimento
    r["aquecimento_vs_medicao_pct"] = _variacao_pct(float(r["vazao_por_s"]), aquecimento)
    if eixo == "a" and principal in ops.latencias_us:
        latencias = np.frombuffer(ops.latencias_us[principal], dtype=np.int64).astype(np.float64)
        n_primeiro = ops.primeiro_minuto.get(principal, 0)
        cv = float(latencias.std() / latencias.mean()) if latencias.size and latencias.mean() > 0 else None
        r["cv_latencia"] = cv
        r["ruido_minutos_pp"] = (float(np.sqrt(2) * cv / np.sqrt(n_primeiro) * 100)
                                 if cv is not None and n_primeiro > 0 else None)
    if eixo == "b":
        enfileirados = len(ops.latencias_us.get("enfileira", array("q")))
        consumidos = len(ops.latencias_us.get("consome", array("q")))
        r["enfileirados"] = enfileirados
        r["consumidos"] = consumidos
        r["crescimento_fila"] = enfileirados - consumidos
        r["crescimento_pct"] = (enfileirados - consumidos) / enfileirados * 100 if enfileirados else None
        r["sustentavel"] = enfileirados > 0 and (enfileirados - consumidos) <= enfileirados * CRESCIMENTO_MAX_PCT / 100


def _resumir_carga_c(r: Dict[str, Any], diretorio: str, inicio_ms: int, fim_ms: int,
                     fim_antes_do_cache_ms: int) -> None:
    """Vazão da carga do Eixo C pela série por segundo do TSBS (ADR-065, item 2) e, como
    diagnóstico, a vazão até fim_antes_do_cache_ms."""
    serie: List[Tuple[int, int]] = []
    with open(os.path.join(diretorio, "metricas-gerador.csv"), encoding="utf-8") as f:
        for linha in csv.DictReader(f):
            if linha["metrica"] == "tsbs_metricas_total":
                serie.append((int(linha["ts_ms"]), int(float(linha["valor"]))))
    serie.sort()
    na_medicao = [(t, v) for t, v in serie if inicio_ms <= t <= fim_ms]
    if len(na_medicao) < 2:
        raise ValueError(f"{diretorio}: série da carga com menos de 2 pontos na medição")

    def total_em(instante_ms: int) -> int:
        anteriores = [v for t, v in serie if t <= instante_ms]
        return anteriores[-1] if anteriores else 0

    (t0, v0), (t1, v1) = na_medicao[0], na_medicao[-1]
    r["vazao_por_s"] = (v1 - v0) / ((t1 - t0) / 1000) if t1 > t0 else 0.0
    antes = [(t, v) for t, v in na_medicao if t <= fim_antes_do_cache_ms]
    r["vazao_antes_do_cache_por_s"] = ((antes[-1][1] - v0) / ((antes[-1][0] - t0) / 1000)
                                       if len(antes) >= 2 and antes[-1][0] > t0 else None)
    if t1 - t0 >= 2 * MINUTO_MS:
        primeiro = (total_em(t0 + MINUTO_MS) - v0) * 1000 / MINUTO_MS
        ultimo = (v1 - total_em(t1 - MINUTO_MS)) * 1000 / MINUTO_MS
        r["primeiro_minuto_por_s"] = primeiro
        r["ultimo_minuto_por_s"] = ultimo
        r["variacao_minutos_pct"] = _variacao_pct(primeiro, ultimo)
    else:
        r["variacao_minutos_pct"] = None
    resumo = ler_json(os.path.join(diretorio, "resumo.json"))
    for valor in cast(List[Dict[str, Any]], resumo.get("valores", [])):
        if valor.get("metrica") == "carga_s":
            r["carga_s"] = float(valor["valor"])


def resumir(diretorio: str) -> Dict[str, Any]:
    """Uma linha por execução, com vazão, latências, fila e CPU na janela de medição."""
    manifesto = ler_json(os.path.join(diretorio, "manifesto.json"))
    gerador = ler_json(os.path.join(diretorio, "gerador.json"))
    eixo = str(manifesto["eixo"])
    inicio_ms = int(gerador["medicao_inicio_ms"])
    fim_ms = int(gerador["medicao_fim_ms"])
    if fim_ms <= inicio_ms:
        raise ValueError(f"{diretorio}: janela de medição vazia")
    espera = manifesto.get("espera")
    serie = str(manifesto["arquitetura"]) + ("_aviso" if espera == "aviso" else "")
    r: Dict[str, Any] = {
        "execucao": os.path.basename(diretorio),
        "eixo": eixo,
        "serie": serie,
        "concorrencia": int(manifesto["concorrencia"]),
        "medicao_s": (fim_ms - inicio_ms) / 1000,
    }
    host = os.path.join(diretorio, "metricas-host.csv")
    # Fim do trecho em que a CPU do gerador é avaliada: os primeiros 60 GiB gravados
    # (revisão da ADR-065, item 3), com pelo menos AVALIACAO_MIN_MS de medição.
    limiar_ms = instante_do_volume(host, CACHE_PSLC_GIB * 2**30)
    fim_avaliacao_ms = (fim_ms if limiar_ms is None
                        else min(fim_ms, max(limiar_ms, inicio_ms + AVALIACAO_MIN_MS)))
    r["avaliacao_gerador_s"] = (fim_avaliacao_ms - inicio_ms) / 1000
    if eixo == "c":
        _resumir_carga_c(r, diretorio, inicio_ms, fim_ms, fim_avaliacao_ms)
    else:
        ops = ler_operacoes(os.path.join(diretorio, "operacoes.csv"), inicio_ms, fim_ms)
        _resumir_operacoes(r, eixo, ops, (fim_ms - inicio_ms) / 1000)
    for sufixo, fim_trecho in (("nucleos", fim_ms), ("antes_do_cache_nucleos", fim_avaliacao_ms)):
        sut = 0.0
        for rotulo, nucleos in sorted(cpu_nucleos(host, inicio_ms, fim_trecho).items()):
            servico = servico_do_container(rotulo)
            r[f"cpu_{servico}_{sufixo}"] = nucleos
            if servico != "gerador":
                sut += nucleos
        r[f"cpu_sut_{sufixo}"] = sut
    r["gerador_gargalo"] = (float(r.get("cpu_gerador_antes_do_cache_nucleos", 0.0))
                            > CPU_GERADOR_MAX[eixo])
    gravado = disco_gravado_bytes(host)
    r["disco_gravado_gib"] = gravado / 2**30 if gravado is not None else None
    r["acima_do_cache_pslc"] = gravado is not None and gravado / 2**30 > CACHE_PSLC_GIB
    return r


def nivel_de_saturacao(linhas: List[Dict[str, Any]], chave: str) -> Optional[int]:
    """Menor nível em que dobrar a concorrência aumenta a vazão em menos de 10%."""
    por_nivel = {int(r["concorrencia"]): float(r[chave]) for r in linhas if r.get(chave) is not None}
    for nivel in sorted(por_nivel):
        dobro = por_nivel.get(2 * nivel)
        if dobro is not None and por_nivel[nivel] > 0 and dobro / por_nivel[nivel] - 1 < GANHO_MIN_SATURACAO:
            return nivel
    return None


def avaliar(resumos: List[Dict[str, Any]], vazao_relay: Optional[float]) -> List[Dict[str, Any]]:
    """Critérios da ADR-067 por série (eixo e arquitetura, com _aviso na espera por aviso)."""
    series: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for r in resumos:
        series.setdefault((str(r["eixo"]), str(r["serie"])), []).append(r)
    criterios: List[Dict[str, Any]] = []
    for (eixo, serie), linhas in sorted(series.items()):
        linhas.sort(key=lambda r: int(r["concorrencia"]))
        saturacao = nivel_de_saturacao(linhas, "vazao_por_s")
        c: Dict[str, Any] = {
            "eixo": eixo,
            "serie": serie,
            "niveis": " ".join(str(r["concorrencia"]) for r in linhas),
            "saturacao": saturacao,
            "vazao_maxima_por_s": max(float(r["vazao_por_s"]) for r in linhas),
        }
        if eixo == "b":
            sustentaveis = [r for r in linhas if r.get("sustentavel")]
            melhor = max(sustentaveis, key=lambda r: float(r["vazao_por_s"])) if sustentaveis else None
            c["vazao_sustentavel_por_s"] = float(melhor["vazao_por_s"]) if melhor else None
            c["nivel_sustentavel"] = int(melhor["concorrencia"]) if melhor else None
            c["niveis_com_fila_crescendo"] = " ".join(
                str(r["concorrencia"]) for r in linhas if not r.get("sustentavel"))
            if serie == "outbox" and vazao_relay:
                for r in linhas:
                    r["fracao_relay"] = float(r["vazao_por_s"]) / vazao_relay
                c["niveis_relay_gargalo"] = " ".join(
                    str(r["concorrencia"]) for r in linhas
                    if float(r["fracao_relay"]) >= FRACAO_RELAY_GARGALO)
        c["niveis_gerador_gargalo"] = " ".join(str(r["concorrencia"]) for r in linhas if r["gerador_gargalo"])
        c["niveis_acima_do_cache_pslc"] = " ".join(
            str(r["concorrencia"]) for r in linhas if r["acima_do_cache_pslc"])
        abaixo = [r for r in linhas if saturacao is None or int(r["concorrencia"]) < saturacao]
        c["niveis_instaveis"] = " ".join(
            str(r["concorrencia"]) for r in abaixo
            if r.get("variacao_minutos_pct") is not None
            and abs(float(r["variacao_minutos_pct"])) >= VARIACAO_MAX_PCT)
        criterios.append(c)
    return criterios


def _fmt(valor: Any, casas: int = 1) -> str:
    if valor is None or valor == "":
        return "-"
    if isinstance(valor, bool):
        return "sim" if valor else "não"
    if isinstance(valor, float):
        return f"{valor:,.{casas}f}".replace(",", " ")
    return str(valor)


def _disco(r: Dict[str, Any]) -> str:
    """Volume gravado no SSD, com * quando passa do limiar do cache pSLC."""
    return _fmt(r.get("disco_gravado_gib")) + ("*" if r.get("acima_do_cache_pslc") else "")


def imprimir(resumos: List[Dict[str, Any]], criterios: List[Dict[str, Any]]) -> None:
    for c in criterios:
        eixo, serie = str(c["eixo"]), str(c["serie"])
        print(f"\n== Eixo {eixo.upper()}, {serie}")
        linhas = sorted((r for r in resumos if r["eixo"] == eixo and r["serie"] == serie),
                        key=lambda r: int(r["concorrencia"]))
        if eixo == "b":
            print(f"{'nível':>5s} {'enfileira/s':>12s} {'consome/s':>11s} {'fila %':>7s} "
                  f"{'e2e p50 ms':>11s} {'e2e p99 ms':>11s} {'1º-últ. min %':>13s} "
                  f"{'CPU ger.':>8s} {'CPU SUT':>8s} {'relay':>6s} {'disco GiB':>9s}")
            for r in linhas:
                print(f"{r['concorrencia']:5d} {_fmt(r.get('enfileira_por_s'), 0):>12s} "
                      f"{_fmt(r.get('consome_por_s'), 0):>11s} {_fmt(r.get('crescimento_pct'), 2):>7s} "
                      f"{_fmt(r.get('fim_a_fim_p50_ms')):>11s} {_fmt(r.get('fim_a_fim_p99_ms')):>11s} "
                      f"{_fmt(r.get('variacao_minutos_pct'), 2):>13s} "
                      f"{_fmt(r.get('cpu_gerador_nucleos'), 2):>8s} {_fmt(r.get('cpu_sut_nucleos'), 2):>8s} "
                      f"{_fmt(r.get('fracao_relay'), 2):>6s} {_disco(r):>9s}")
        elif eixo == "c":
            print(f"{'nível':>5s} {'métricas/s':>12s} {'carga s':>8s} {'1º-últ. min %':>13s} "
                  f"{'CPU ger.':>8s} {'CPU SUT':>8s} {'disco GiB':>9s} "
                  f"{'antes de 60 GiB:':>16s} {'métricas/s':>12s} {'CPU ger.':>8s} {'CPU SUT':>8s}")
            for r in linhas:
                print(f"{r['concorrencia']:5d} {_fmt(r.get('vazao_por_s'), 0):>12s} "
                      f"{_fmt(r.get('carga_s')):>8s} {_fmt(r.get('variacao_minutos_pct'), 2):>13s} "
                      f"{_fmt(r.get('cpu_gerador_nucleos'), 2):>8s} {_fmt(r.get('cpu_sut_nucleos'), 2):>8s} "
                      f"{_disco(r):>9s} {_fmt(r.get('avaliacao_gerador_s'), 0) + ' s':>16s} "
                      f"{_fmt(r.get('vazao_antes_do_cache_por_s'), 0):>12s} "
                      f"{_fmt(r.get('cpu_gerador_antes_do_cache_nucleos'), 2):>8s} "
                      f"{_fmt(r.get('cpu_sut_antes_do_cache_nucleos'), 2):>8s}")
        else:
            print(f"{'nível':>5s} {'consultas/s':>12s} {'p50 ms':>9s} {'p95 ms':>9s} {'p99 ms':>9s} "
                  f"{'1º-últ. min %':>13s} {'ruído pp':>8s} {'aquec. %':>8s} {'CPU ger.':>8s} {'CPU SUT':>8s} "
                  f"{'disco GiB':>9s}")
            for r in linhas:
                print(f"{r['concorrencia']:5d} {_fmt(r.get('vazao_por_s'), 1):>12s} "
                      f"{_fmt(r.get('busca_hibrida_p50_ms')):>9s} {_fmt(r.get('busca_hibrida_p95_ms')):>9s} "
                      f"{_fmt(r.get('busca_hibrida_p99_ms')):>9s} {_fmt(r.get('variacao_minutos_pct'), 2):>13s} "
                      f"{_fmt(r.get('ruido_minutos_pp'), 2):>8s} {_fmt(r.get('aquecimento_vs_medicao_pct'), 2):>8s} "
                      f"{_fmt(r.get('cpu_gerador_nucleos'), 2):>8s} {_fmt(r.get('cpu_sut_nucleos'), 2):>8s} "
                      f"{_disco(r):>9s}")
        print(f"   saturação: {_fmt(c['saturacao'])}"
              + ("" if eixo != "b" else
                 f" · vazão sustentável: {_fmt(c.get('vazao_sustentavel_por_s'), 0)}/s"
                 f" no nível {_fmt(c.get('nivel_sustentavel'))}"
                 f" · fila crescendo em: {_fmt(c.get('niveis_com_fila_crescendo'))}")
              + (f" · relay como gargalo em: {_fmt(c.get('niveis_relay_gargalo'))}"
                 if "niveis_relay_gargalo" in c else ""))
        print(f"   gerador como gargalo em: {_fmt(c['niveis_gerador_gargalo'])}"
              f" · instáveis abaixo da saturação: {_fmt(c['niveis_instaveis'])}"
              f" · acima de {CACHE_PSLC_GIB:.0f} GiB gravados (*): {_fmt(c['niveis_acima_do_cache_pslc'])}")


def main() -> None:
    p = argparse.ArgumentParser(description="Análise da calibração (ADR-067)")
    p.add_argument("janelas", nargs="+", help="identificadores das janelas das rampas")
    p.add_argument("--relay", default=None, help="registro da vazão isolada do relay (ADR-053)")
    p.add_argument("--nome", default="calibracao", help="prefixo dos CSV de saída")
    p.add_argument("--saida", default=PROCESSADOS, help="diretório dos CSV de saída")
    args = p.parse_args()
    vazao_relay: Optional[float] = None
    if args.relay is not None:
        registro = ler_json(str(args.relay))
        regime = registro.get("vazao_regime_por_s")
        if regime is None:
            raise SystemExit(f"{args.relay}: sem vazao_regime_por_s")
        vazao_relay = float(regime)
    resumos: List[Dict[str, Any]] = []
    for janela in [str(j) for j in args.janelas]:
        for diretorio in execucoes_da_janela(janela):
            print(f"lendo {os.path.basename(diretorio)}", flush=True)
            r = resumir(diretorio)
            r["janela"] = janela
            resumos.append(r)
    criterios = avaliar(resumos, vazao_relay)
    os.makedirs(str(args.saida), exist_ok=True)
    gravar_csv(os.path.join(str(args.saida), f"{args.nome}-execucoes.csv"), resumos)
    gravar_csv(os.path.join(str(args.saida), f"{args.nome}-criterios.csv"), criterios)
    imprimir(resumos, criterios)


if __name__ == "__main__":
    main()
