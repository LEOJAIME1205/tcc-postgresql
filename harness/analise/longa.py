"""Análise da longa duração (ADR-069, item 6, e o complemento dele; ADR-004, item 3; ADR-070).

Descritiva, sem teste. Duas etapas, como em harness/analise/curtas.py:

- resumir: para cada execução longa (e para o controle com fio, se for passado), grava em
  resultados/processados/longa/<execução>/:
  - execucao.json: identificação, estado e janela de medição;
  - minutos.csv: uma linha por minuto da medição, contado desde o início dela, com as séries
    derivadas. No B, enfileiramento, consumo, profundidade e latência fim a fim (lidos do
    operacoes.csv inteiro). No C, a taxa obtida e o atraso da ingestão. Em todos, CPU e
    memória de cada container e leitura, escrita, latência por escrita e temperatura do SSD;
  - internas.csv: a última amostra de cada minuto de toda métrica interna (PostgreSQL,
    RabbitMQ, TimescaleDB, InfluxDB), em formato longo;
  - no C, lotes.csv (os lotes de consultas com o minuto e a hora) e bateria-final.csv.
- derivar: lê essas pastas e grava, para todas as colunas de minutos.csv:
  - longa-horas.csv: mediana, mínimo e máximo dos valores por minuto em cada hora;
  - longa-deriva.csv: inclinação por hora (Theil-Sen, a principal, e mínimos quadrados, como
    referência) e a primeira hora contra a última, pela mediana dos 60 minutos de cada uma;
  - longa-lotes.csv, no C: por tipo de consulta, a inclinação por hora da média de cada lote
    periódico e o primeiro lote contra o último periódico;
  - longa-bateria-final.csv, no C: a média de cada tipo na bateria final e, se
    curtas-execucoes.csv existir, a razão para a mediana das médias das curtas da mesma
    arquitetura.

Taxas por minuto saem da diferença entre a última amostra do minuto e a do minuto anterior,
dividida pelo tempo entre as duas. O minuto -1 guarda a última amostra antes da medição, para a
taxa do primeiro minuto. A atribuição de uma queda ao disco (ADR-004, item 3) é feita caso a
caso sobre as séries do SSD da execução e do controle; não há limiar automático.

    uv run python -m harness.analise.longa resumir [<janela ou diretório> ...]
    uv run python -m harness.analise.longa derivar
"""

import argparse
import csv
import json
import math
import os
import statistics
import sys
from array import array
from typing import Any, Dict, List, Optional, Sequence, Tuple, cast

import numpy as np

from harness.analise.comum import (BRUTOS, PROCESSADOS, Linha, execucoes_da_janela, gravar_csv,
                                   ler_execucoes, ler_json, numero, servico_do_container)

JANELA: str = "20261116-0943"
SUBPASTA: str = "longa"
MINUTO_MS: int = 60_000
MINUTOS_POR_HORA: int = 60
GIB: int = 2**30
MIB: int = 2**20
# Arquivos de métricas tratados à parte; os outros metricas-*.csv vão para internas.csv.
FORA_DAS_INTERNAS: Tuple[str, ...] = ("metricas-host.csv", "metricas-gerador.csv", "metricas-lotes.csv")

Amostra = Tuple[int, float]
SeriePorMinuto = Dict[int, Amostra]
Ultimas = Dict[Tuple[str, str], SeriePorMinuto]


# --- leitura ----------------------------------------------------------------------------------

def ultimas_por_minuto(caminho: str, inicio_ms: int, fim_ms: int) -> Ultimas:
    """Última amostra (ts_ms, valor) de cada (métrica, rótulo) em cada minuto da medição, de um
    CSV de métricas do harness; o minuto -1 é a última amostra antes de inicio_ms."""
    saida: Ultimas = {}
    with open(caminho, encoding="utf-8", newline="") as f:
        leitor = csv.reader(f)
        cabecalho = next(leitor)
        if cabecalho[:5] != ["ts_ms", "origem", "metrica", "rotulo", "valor"]:
            raise ValueError(f"{caminho}: cabeçalho inesperado {cabecalho}")
        for campos in leitor:
            if len(campos) < 5 or not campos[4]:
                continue
            ts = int(campos[0])
            if ts >= fim_ms:
                continue
            minuto = (ts - inicio_ms) // MINUTO_MS if ts >= inicio_ms else -1
            serie = saida.setdefault((campos[2], campos[3]), {})
            anterior = serie.get(minuto)
            if anterior is None or ts >= anterior[0]:
                serie[minuto] = (ts, float(campos[4]))
    return saida


def taxa(serie: SeriePorMinuto, minuto: int) -> Optional[float]:
    """Taxa por segundo no minuto, entre a última amostra dele e a do minuto anterior."""
    atual, anterior = serie.get(minuto), serie.get(minuto - 1)
    if atual is None or anterior is None or atual[0] <= anterior[0]:
        return None
    return (atual[1] - anterior[1]) / ((atual[0] - anterior[0]) / 1000)


def ultimo(serie: SeriePorMinuto, minuto: int) -> Optional[float]:
    amostra = serie.get(minuto)
    return amostra[1] if amostra is not None else None


def _soma(valores: Sequence[Optional[float]]) -> Optional[float]:
    presentes = [v for v in valores if v is not None]
    return sum(presentes) if presentes else None


class OperacoesPorMinuto:
    """Contagens por minuto da medição e latências fim a fim de cada minuto, mais as operações
    bem-sucedidas antes da medição (aquecimento), para a profundidade da fila."""

    def __init__(self, minutos: int) -> None:
        self.contagens: Dict[str, List[int]] = {}
        self.erros: List[int] = [0] * minutos
        self.fim_a_fim_us: List[array[int]] = [array("q") for _ in range(minutos)]
        self.antes: Dict[str, int] = {}
        self.minutos = minutos

    def contar(self, operacao: str) -> List[int]:
        contagem = self.contagens.get(operacao)
        if contagem is None:
            contagem = self.contagens[operacao] = [0] * self.minutos
        return contagem


def operacoes_por_minuto(caminho: str, inicio_ms: int, fim_ms: int) -> OperacoesPorMinuto:
    """Lê o operacoes.csv do B linha a linha; com ~360 milhões de linhas, leva minutos. Os nomes
    locais evitam buscas de atributo no laço."""
    minutos = math.ceil((fim_ms - inicio_ms) / MINUTO_MS)
    ops = OperacoesPorMinuto(minutos)
    contar, antes, erros, fim_a_fim = ops.contar, ops.antes, ops.erros, ops.fim_a_fim_us
    inicio_us, fim_us, minuto_us = inicio_ms * 1000, fim_ms * 1000, MINUTO_MS * 1000
    with open(caminho, encoding="utf-8") as f:
        cabecalho = next(f).rstrip("\n").split(",")
        if cabecalho[:4] != ["ts_us", "operacao", "latencia_us", "ok"]:
            raise ValueError(f"{caminho}: cabeçalho inesperado {cabecalho}")
        for linha in f:
            campos = linha.split(",", 4)
            ts = int(campos[0])
            if ts >= fim_us:
                continue
            operacao = campos[1]
            ok = campos[3] == "1" and campos[2] != ""
            if ts < inicio_us:
                if ok:
                    antes[operacao] = antes.get(operacao, 0) + 1
                continue
            minuto = (ts - inicio_us) // minuto_us
            if not ok:
                erros[minuto] += 1
                continue
            contar(operacao)[minuto] += 1
            if operacao == "fim_a_fim":
                fim_a_fim[minuto].append(int(campos[2]))
    return ops


# --- resumir ----------------------------------------------------------------------------------

def info_da_execucao(diretorio: str) -> Dict[str, Any]:
    """Identificação e janela de medição; no controle com fio, a janela é a do fio, limitada à
    duração planejada: os ~0,4 s de partida e saída do processo virariam um minuto parcial."""
    manifesto = ler_json(os.path.join(diretorio, "manifesto.json"))
    info: Dict[str, Any] = {"execucao": os.path.basename(diretorio), "diretorio": diretorio,
                            "estado": manifesto.get("estado")}
    if "fio_inicio_ms" in manifesto:
        inicio = int(manifesto["fio_inicio_ms"])
        fim = int(manifesto["fio_fim_ms"])
        if manifesto.get("duracao_s") is not None:
            fim = min(fim, inicio + int(manifesto["duracao_s"]) * 1000)
        info.update(eixo="controle", arquitetura="fio", inicio_ms=inicio, fim_ms=fim,
                    taxa=manifesto.get("taxa_mib_s"))
        return info
    gerador = ler_json(os.path.join(diretorio, "gerador.json"))
    parametros = cast(Dict[str, Any], gerador.get("parametros") or {})
    planejada = cast(Dict[str, Any], manifesto.get("duracao_planejada_s") or {})
    info.update(eixo=str(manifesto["eixo"]), arquitetura=str(manifesto["arquitetura"]),
                inicio_ms=int(gerador["medicao_inicio_ms"]), fim_ms=int(gerador["medicao_fim_ms"]),
                taxa=manifesto.get("taxa"), medicao_planejada_s=planejada.get("medicao"),
                lote_intervalo_s=parametros.get("lote_intervalo_s"))
    return info


def _host(linhas: List[Dict[str, Any]], ultimas: Ultimas) -> None:
    """CPU e memória de cada container e o SSD, minuto a minuto."""
    for m, r in enumerate(linhas):
        sut: List[Optional[float]] = []
        escrita: List[Optional[float]] = []
        leitura: List[Optional[float]] = []
        escritas: List[Optional[float]] = []
        escrita_ms: List[Optional[float]] = []
        temperaturas: List[float] = []
        for (metrica, rotulo), serie in sorted(ultimas.items()):
            if metrica == "cont_cpu_us_total":
                servico = servico_do_container(rotulo)
                por_s = taxa(serie, m)
                r[f"cpu_{servico}_nucleos"] = por_s / 1e6 if por_s is not None else None
                if servico != "gerador":
                    sut.append(r[f"cpu_{servico}_nucleos"])
            elif metrica in ("cont_mem_bytes", "cont_mem_arquivo_bytes"):
                valor = ultimo(serie, m)
                nome = "mem" if metrica == "cont_mem_bytes" else "mem_arquivo"
                r[f"{nome}_{servico_do_container(rotulo)}_gib"] = valor / GIB if valor is not None else None
            elif metrica == "cont_oom_total":
                r[f"oom_{servico_do_container(rotulo)}"] = ultimo(serie, m)
            elif metrica == "disco_escrita_bytes_total":
                escrita.append(taxa(serie, m))
            elif metrica == "disco_leitura_bytes_total":
                leitura.append(taxa(serie, m))
            elif metrica == "disco_escritas_total":
                escritas.append(taxa(serie, m))
            elif metrica == "disco_escrita_ms_total":
                escrita_ms.append(taxa(serie, m))
            elif metrica == "nvme_temp_c":
                valor = ultimo(serie, m)
                if valor is not None:
                    temperaturas.append(valor)
        r["cpu_sut_nucleos"] = _soma(sut)
        por_s = _soma(escrita)
        r["disco_escrita_mib_s"] = por_s / MIB if por_s is not None else None
        por_s = _soma(leitura)
        r["disco_leitura_mib_s"] = por_s / MIB if por_s is not None else None
        n, ms = _soma(escritas), _soma(escrita_ms)
        r["disco_ms_por_escrita"] = ms / n if n and ms is not None else None
        r["nvme_temp_c"] = max(temperaturas) if temperaturas else None


def _fila(linhas: List[Dict[str, Any]], ops: OperacoesPorMinuto, medicao_s: float) -> None:
    """Vazões, profundidade e latência fim a fim do B, minuto a minuto; o último minuto pode
    ser parcial, e a vazão dele é dividida pela duração real."""
    enfileiradas = ops.antes.get("enfileira", 0)
    consumidas = ops.antes.get("consome", 0)
    enfileira, consome = ops.contar("enfileira"), ops.contar("consome")
    for m, r in enumerate(linhas):
        segundos = min(60.0, medicao_s - 60 * m)
        enfileiradas += enfileira[m]
        consumidas += consome[m]
        r["enfileira_por_s"] = enfileira[m] / segundos
        r["consome_por_s"] = consome[m] / segundos
        r["profundidade"] = enfileiradas - consumidas
        r["erros"] = ops.erros[m]
        latencias = np.frombuffer(ops.fim_a_fim_us[m], dtype=np.int64)
        if latencias.size:
            p50, p99 = np.percentile(latencias, [50, 99])
            r["fim_a_fim_p50_ms"] = float(p50) / 1000
            r["fim_a_fim_p99_ms"] = float(p99) / 1000
            r["fim_a_fim_max_ms"] = float(latencias.max()) / 1000


def _ingestao(linhas: List[Dict[str, Any]], ultimas: Ultimas) -> None:
    """Taxa obtida e atraso da ingestão do C (pontos devidos e ainda não enviados no fim do
    minuto), minuto a minuto."""
    metricas = ultimas.get(("tsbs_metricas_total", ""), {})
    devidos = ultimas.get(("alimentador_pontos_devidos_total", ""), {})
    enviados = ultimas.get(("alimentador_pontos_enviados_total", ""), {})
    for m, r in enumerate(linhas):
        r["metricas_por_s"] = taxa(metricas, m)
        d, e = ultimo(devidos, m), ultimo(enviados, m)
        r["atraso_pontos"] = d - e if d is not None and e is not None else None


def _gravar_internas(caminho: str, diretorio: str, inicio_ms: int, fim_ms: int) -> None:
    with open(caminho, "w", encoding="utf-8", newline="") as saida:
        escritor = csv.writer(saida)
        escritor.writerow(["arquivo", "minuto", "metrica", "rotulo", "ts_ms", "valor"])
        for nome in sorted(os.listdir(diretorio)):
            if not (nome.startswith("metricas-") and nome.endswith(".csv")) or nome in FORA_DAS_INTERNAS:
                continue
            arquivo = nome[len("metricas-"):-len(".csv")]
            for (metrica, rotulo), serie in sorted(ultimas_por_minuto(
                    os.path.join(diretorio, nome), inicio_ms, fim_ms).items()):
                for minuto, (ts, valor) in sorted(serie.items()):
                    if minuto >= 0:
                        escritor.writerow([arquivo, minuto, metrica, rotulo, ts, repr(valor)])


def _consultas_c(pasta: str, diretorio: str, info: Dict[str, Any]) -> None:
    """Lotes de consultas com minuto, hora e se é periódico; bateria final do resumo."""
    inicio_ms = int(info["inicio_ms"])
    planejada, intervalo = info.get("medicao_planejada_s"), info.get("lote_intervalo_s")
    periodicos = int(planejada) // int(intervalo) if planejada and intervalo else None
    lotes: List[Dict[str, Any]] = []
    caminho_lotes = os.path.join(diretorio, "metricas-lotes.csv")
    if os.path.exists(caminho_lotes):
        with open(caminho_lotes, encoding="utf-8", newline="") as f:
            for linha in csv.DictReader(f):
                ts = int(linha["ts_ms"])
                lotes.append({**linha, "minuto": (ts - inicio_ms) // MINUTO_MS,
                              "hora": (ts - inicio_ms) / (MINUTOS_POR_HORA * MINUTO_MS),
                              "periodico": periodicos is not None and int(linha["lote"]) <= periodicos})
        gravar_csv(os.path.join(pasta, "lotes.csv"), lotes)
    caminho_resumo = os.path.join(diretorio, "resumo.json")
    if os.path.exists(caminho_resumo):
        por_tipo: Dict[str, Dict[str, Any]] = {}
        for valor in cast(List[Dict[str, Any]], ler_json(caminho_resumo).get("valores", [])):
            if valor.get("metrica") == "consulta_latencia_us":
                tipo, estatistica = str(valor["rotulo"]).rsplit(":", 1)
                por_tipo.setdefault(tipo, {"tipo": tipo})[f"{estatistica}_ms"] = float(valor["valor"]) / 1000
        gravar_csv(os.path.join(pasta, "bateria-final.csv"), [por_tipo[t] for t in sorted(por_tipo)])


def resumir_execucao(diretorio: str, saida: str) -> None:
    info = info_da_execucao(diretorio)
    inicio_ms, fim_ms = int(info["inicio_ms"]), int(info["fim_ms"])
    if fim_ms <= inicio_ms:
        raise ValueError(f"{diretorio}: janela de medição vazia")
    pasta = os.path.join(saida, SUBPASTA, str(info["execucao"]))
    os.makedirs(pasta, exist_ok=True)
    linhas: List[Dict[str, Any]] = [{"minuto": m} for m in range(math.ceil((fim_ms - inicio_ms) / MINUTO_MS))]
    if info["eixo"] == "b":
        print("  operacoes.csv", flush=True)
        _fila(linhas, operacoes_por_minuto(os.path.join(diretorio, "operacoes.csv"), inicio_ms, fim_ms),
              (fim_ms - inicio_ms) / 1000)
    if info["eixo"] == "c":
        _ingestao(linhas, ultimas_por_minuto(os.path.join(diretorio, "metricas-gerador.csv"),
                                             inicio_ms, fim_ms))
        _consultas_c(pasta, diretorio, info)
    print("  metricas-host.csv", flush=True)
    _host(linhas, ultimas_por_minuto(os.path.join(diretorio, "metricas-host.csv"), inicio_ms, fim_ms))
    gravar_csv(os.path.join(pasta, "minutos.csv"), linhas)
    print("  métricas internas", flush=True)
    _gravar_internas(os.path.join(pasta, "internas.csv"), diretorio, inicio_ms, fim_ms)
    with open(os.path.join(pasta, "execucao.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)


# --- derivar ----------------------------------------------------------------------------------

def theil_sen(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    """Mediana das inclinações entre todos os pares de pontos com x diferente."""
    if len(x) < 2:
        return None
    xs, ys = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    i, j = np.triu_indices(xs.size, 1)
    dx = xs[j] - xs[i]
    validos = dx != 0
    if not validos.any():
        return None
    return float(np.median((ys[j] - ys[i])[validos] / dx[validos]))


def minimos_quadrados(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    if len(x) < 2 or len(set(x)) < 2:
        return None
    return float(np.polyfit(np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64), 1)[0])


def _razao(depois: Optional[float], antes: Optional[float]) -> Optional[float]:
    return depois / antes if depois is not None and antes else None


def deriva(minutos: Sequence[Linha], coluna: str, total_min: int) -> Optional[Dict[str, Any]]:
    """Inclinação por hora e primeira hora contra a última de uma coluna de minutos.csv."""
    pontos = [(int(l["minuto"]), v) for l in minutos for v in [numero(l, coluna)] if v is not None]
    if len(pontos) < 2:
        return None
    x = [m / MINUTOS_POR_HORA for m, _ in pontos]
    y = [v for _, v in pontos]
    primeira = [v for m, v in pontos if m < MINUTOS_POR_HORA]
    ultima = [v for m, v in pontos if m >= total_min - MINUTOS_POR_HORA]
    mediana_primeira = statistics.median(primeira) if primeira else None
    mediana_ultima = statistics.median(ultima) if ultima else None
    return {"serie": coluna, "minutos": len(pontos),
            "inclinacao_theil_sen_por_h": theil_sen(x, y),
            "inclinacao_mq_por_h": minimos_quadrados(x, y),
            "primeira_hora_mediana": mediana_primeira, "ultima_hora_mediana": mediana_ultima,
            "razao_ultima_primeira": _razao(mediana_ultima, mediana_primeira)}


def horas(minutos: Sequence[Linha], coluna: str) -> List[Dict[str, Any]]:
    """Mediana, mínimo e máximo dos valores por minuto de cada hora."""
    por_hora: Dict[int, List[float]] = {}
    for linha in minutos:
        valor = numero(linha, coluna)
        if valor is not None:
            por_hora.setdefault(int(linha["minuto"]) // MINUTOS_POR_HORA + 1, []).append(valor)
    return [{"serie": coluna, "hora": h, "minutos": len(v), "mediana": statistics.median(v),
             "min": min(v), "max": max(v)} for h, v in sorted(por_hora.items())]


def deriva_dos_lotes(lotes: Sequence[Linha]) -> List[Dict[str, Any]]:
    """Por tipo de consulta, a média de cada lote periódico contra a hora do lote (ADR-061: a
    média por tipo), e o primeiro lote periódico contra o último."""
    por_tipo: Dict[str, List[Tuple[float, float]]] = {}
    for lote in lotes:
        media = numero(lote, "media_ms")
        if numero(lote, "periodico") == 1.0 and media is not None:
            por_tipo.setdefault(lote["tipo"], []).append((float(lote["hora"]), media))
    saida: List[Dict[str, Any]] = []
    for tipo, pontos in sorted(por_tipo.items()):
        pontos.sort()
        x, y = [h for h, _ in pontos], [v for _, v in pontos]
        saida.append({"tipo": tipo, "lotes": len(pontos),
                      "inclinacao_theil_sen_ms_por_h": theil_sen(x, y),
                      "inclinacao_mq_ms_por_h": minimos_quadrados(x, y),
                      "primeiro_lote_media_ms": y[0], "ultimo_lote_media_ms": y[-1],
                      "razao_ultimo_primeiro": _razao(y[-1], y[0])})
    return saida


def medias_das_curtas(caminho: str) -> Dict[Tuple[str, str], float]:
    """Mediana das médias de cada tipo de consulta nas execuções curtas do C, por arquitetura."""
    valores: Dict[Tuple[str, str], List[float]] = {}
    for linha in ler_execucoes(caminho):
        if linha.get("grupo") != "c":
            continue
        for coluna in linha:
            if coluna.startswith("consulta_") and coluna.endswith("_media_ms"):
                v = numero(linha, coluna)
                if v is not None:
                    tipo = coluna[len("consulta_"):-len("_media_ms")]
                    valores.setdefault((linha["arquitetura"], tipo), []).append(v)
    return {chave: statistics.median(v) for chave, v in valores.items()}


def derivar(saida: str) -> None:
    raiz = os.path.join(saida, SUBPASTA)
    curtas = os.path.join(saida, "curtas-execucoes.csv")
    medianas_curtas = medias_das_curtas(curtas) if os.path.exists(curtas) else {}
    todas_horas: List[Dict[str, Any]] = []
    todas_derivas: List[Dict[str, Any]] = []
    todos_lotes: List[Dict[str, Any]] = []
    bateria: List[Dict[str, Any]] = []
    for nome in sorted(os.listdir(raiz)):
        pasta = os.path.join(raiz, nome)
        if not os.path.exists(os.path.join(pasta, "execucao.json")):
            continue
        info = ler_json(os.path.join(pasta, "execucao.json"))
        ident = {"execucao": nome, "eixo": info["eixo"], "arquitetura": info["arquitetura"],
                 "estado": info["estado"]}
        minutos = ler_execucoes(os.path.join(pasta, "minutos.csv"))
        colunas = [c for c in minutos[0] if c != "minuto"] if minutos else []
        for coluna in colunas:
            d = deriva(minutos, coluna, len(minutos))
            if d is not None:
                todas_derivas.append({**ident, **d})
            todas_horas += [{**ident, **h} for h in horas(minutos, coluna)]
        if os.path.exists(os.path.join(pasta, "lotes.csv")):
            todos_lotes += [{**ident, **d} for d in deriva_dos_lotes(ler_execucoes(os.path.join(pasta, "lotes.csv")))]
        if os.path.exists(os.path.join(pasta, "bateria-final.csv")):
            for linha in ler_execucoes(os.path.join(pasta, "bateria-final.csv")):
                media = numero(linha, "media_ms")
                curta = medianas_curtas.get((str(info["arquitetura"]), linha["tipo"]))
                bateria.append({**ident, "tipo": linha["tipo"], "media_ms": media,
                                "curtas_mediana_das_medias_ms": curta,
                                "razao_longa_curtas": _razao(media, curta)})
        print(f"{nome}: {len(minutos)} minutos, estado {info['estado']}")
    gravar_csv(os.path.join(saida, "longa-horas.csv"), todas_horas)
    gravar_csv(os.path.join(saida, "longa-deriva.csv"), todas_derivas)
    if todos_lotes:
        gravar_csv(os.path.join(saida, "longa-lotes.csv"), todos_lotes)
    if bateria:
        gravar_csv(os.path.join(saida, "longa-bateria-final.csv"), bateria)


def _alvos(argumentos: Sequence[str]) -> List[str]:
    """Janelas viram as execuções ok delas; diretórios entram como estão (controle com fio, ou a
    execução abortada da B consolidada, para a comparação descritiva da ADR-033)."""
    diretorios: List[str] = []
    for alvo in argumentos:
        if os.path.isdir(alvo):
            diretorios.append(os.path.abspath(alvo))
        elif os.path.isdir(os.path.join(BRUTOS, "janelas", alvo)):
            diretorios += execucoes_da_janela(alvo)
        else:
            raise SystemExit(f"{alvo}: nem diretório nem janela")
    return diretorios


def main() -> None:
    p = argparse.ArgumentParser(description="Análise da longa duração (ADR-069, item 6)")
    p.add_argument("--saida", default=PROCESSADOS, help="diretório dos resultados processados")
    etapas = p.add_subparsers(dest="etapa", required=True)
    r = etapas.add_parser("resumir", help="séries por minuto de cada execução")
    r.add_argument("alvos", nargs="*", default=[JANELA], help="janelas ou diretórios de execução")
    etapas.add_parser("derivar", help="horas, deriva e consultas a partir das séries")
    args = p.parse_args()
    saida = str(args.saida)
    if str(args.etapa) == "resumir":
        for diretorio in _alvos(cast(List[str], args.alvos)):
            print(f"lendo {os.path.basename(diretorio)}", flush=True)
            resumir_execucao(diretorio, saida)
        return
    if not os.path.isdir(os.path.join(saida, SUBPASTA)):
        print(f"nada em {os.path.join(saida, SUBPASTA)}; rode resumir antes", file=sys.stderr)
        raise SystemExit(1)
    derivar(saida)


if __name__ == "__main__":
    main()
