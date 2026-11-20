"""Análise da carga mista (ADR-068, item 6; ADR-070).

Duas etapas, como em harness/analise/curtas.py:

- resumir: uma linha por execução da janela em mista-execucoes.csv. Traz a vazão e os
  percentis da busca na medição, a validade da ingestão (ADR-068, item 3), a CPU e a memória
  de cada container, a leitura e a escrita no SSD e, na consolidada, as tuplas, o autovacuum,
  o WAL e os checkpoints do banco benchmark.
- comparar: grava mista-configuracoes.csv (mediana, mínimo e máximo por arquitetura e taxa,
  ADR-070, item 5) e mista-comparacoes.csv, com os dois testes da ADR-068, item 6:
  - dentro de cada arquitetura: cada taxa contra a configuração sem escrita, no p50 e no p99
    da busca. O primeiro grupo é a taxa: o Â12 é a probabilidade de uma execução com escrita
    ter latência maior que uma sem escrita, e a razão é a mediana com escrita dividida pela
    mediana sem escrita. Holm sobre as 6 comparações da arquitetura;
  - entre arquiteturas: a degradação de cada execução é a latência dela dividida pela mediana
    da mesma arquitetura sem escrita. Em cada taxa, no p50 e no p99, a consolidada contra a
    poliglota. Holm sobre as 6 comparações.
  O pareamento por bloco (mesma repetição, ADR-032) entra só como descrição, pela menor e pela
  maior razão dentro dos blocos.

Uma execução com ingestão que não sustentou a taxa a menos de 1% na medição, ou que não
cobriu a medição, fica fora dos testes e da mediana da base, e é contada como inválida
(ADR-068, item 3; ADR-070, item 2).

    uv run python -m harness.analise.mista resumir [<janela>]
    uv run python -m harness.analise.mista comparar
"""

import argparse
import os
import statistics
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, cast

import numpy as np

from harness.analise.calibracao import CACHE_PSLC_GIB, PERCENTIS, ler_operacoes
from harness.analise.comum import (PROCESSADOS, Linha, cpu_nucleos, disco_bytes_por_s,
                                   disco_gravado_bytes, execucoes_da_janela, gravar_csv,
                                   ler_execucoes, ler_json, numero, series_por_rotulo,
                                   servico_do_container)
from harness.analise.estatistica import ALFA, comparar, holm, menor_p_possivel

JANELA: str = "20261115-1319"
PREFIXO: str = "mista"
OPERACAO: str = "busca_hibrida"
TAXAS: Tuple[int, ...] = (250_000, 500_000, 1_000_000)
TESTADAS: Tuple[str, ...] = ("p50", "p99")
DEGRADACOES: Tuple[str, ...] = ("p50", "p95", "p99")
ARQUITETURAS: Tuple[str, str] = ("consolidada", "poliglota")
IDENTIFICACAO: Tuple[str, ...] = ("execucao", "janela", "arquitetura", "taxa", "repeticao")
BOOLEANAS: Tuple[str, ...] = ("valida", "taxa_sustentada", "ingestao_cobriu_medicao",
                              "acima_do_cache_pslc")
METRICAS_BENCHMARK: Tuple[str, ...] = ("pg_tuplas_mortas", "pg_tuplas_vivas", "pg_autovacuum_total",
                                       "pg_autoanalyze_total", "pg_wal_bytes_total",
                                       "pg_checkpoints_total")
GIB: int = 2**30
MIB: int = 2**20


# --- resumir --------------------------------------------------------------------------------

def _memoria(r: Dict[str, Any], host: str, inicio_ms: int, fim_ms: int) -> None:
    """Pico da memória de cada container na medição e média do cache de arquivos dele, que o
    cgroup conta no limite (ADR-003)."""
    series = series_por_rotulo(host, ("cont_mem_bytes", "cont_mem_arquivo_bytes"), inicio_ms, fim_ms)
    for (metrica, rotulo), serie in sorted(series.items()):
        servico = servico_do_container(rotulo)
        valores = [v for _, v in serie]
        if metrica == "cont_mem_bytes":
            r[f"mem_{servico}_max_gib"] = max(valores) / GIB
        else:
            r[f"mem_arquivo_{servico}_media_gib"] = statistics.fmean(valores) / GIB


def _benchmark(r: Dict[str, Any], caminho: str, inicio_ms: int, fim_ms: int) -> None:
    """Banco benchmark da consolidada na medição: tuplas no fim, autovacuum, autoanalyze e
    checkpoints no trecho e WAL por segundo (o WAL é do servidor inteiro)."""
    series = series_por_rotulo(caminho, METRICAS_BENCHMARK, inicio_ms, fim_ms)

    def ultimo(metrica: str) -> float:
        return sum(s[-1][1] for (m, _), s in series.items() if m == metrica)

    def no_trecho(metrica: str) -> float:
        return sum(s[-1][1] - s[0][1] for (m, _), s in series.items() if m == metrica)

    r["benchmark_tuplas_mortas_fim"] = ultimo("pg_tuplas_mortas")
    r["benchmark_tuplas_vivas_fim"] = ultimo("pg_tuplas_vivas")
    r["benchmark_autovacuum"] = no_trecho("pg_autovacuum_total")
    r["benchmark_autoanalyze"] = no_trecho("pg_autoanalyze_total")
    r["checkpoints"] = no_trecho("pg_checkpoints_total")
    wal = [s for (m, _), s in series.items() if m == "pg_wal_bytes_total"]
    if wal and len(wal[0]) >= 2 and wal[0][-1][0] > wal[0][0][0]:
        (t0, v0), (t1, v1) = wal[0][0], wal[0][-1]
        r["wal_mib_s"] = (v1 - v0) / ((t1 - t0) / 1000) / MIB


def resumir_execucao(diretorio: str) -> Dict[str, Any]:
    manifesto = ler_json(os.path.join(diretorio, "manifesto.json"))
    gerador = ler_json(os.path.join(diretorio, "gerador.json"))
    inicio_ms, fim_ms = int(gerador["medicao_inicio_ms"]), int(gerador["medicao_fim_ms"])
    if fim_ms <= inicio_ms:
        raise ValueError(f"{diretorio}: janela de medição vazia")
    duracao_s = (fim_ms - inicio_ms) / 1000
    taxa = int(manifesto["taxa"]) if manifesto.get("taxa") is not None else 0
    r: Dict[str, Any] = {
        "execucao": os.path.basename(diretorio),
        "arquitetura": str(manifesto["arquitetura"]),
        "taxa": taxa,
        "repeticao": int(manifesto["repeticao"]),
        "medicao_s": duracao_s,
    }
    ops = ler_operacoes(os.path.join(diretorio, "operacoes.csv"), inicio_ms, fim_ms)
    r["erros"] = ops.erros
    if OPERACAO in ops.latencias_us:
        valores = np.frombuffer(ops.latencias_us[OPERACAO], dtype=np.int64)
        r[f"{OPERACAO}_por_s"] = valores.size / duracao_s
        for p in PERCENTIS:
            r[f"{OPERACAO}_p{p}_ms"] = float(np.percentile(valores, p)) / 1000

    mista = cast(Dict[str, Any], gerador.get("mista") or {})
    if taxa:
        obtida = mista.get("taxa_na_medicao_metricas_s")
        ingestao = cast(Dict[str, Any], mista.get("ingestao") or {})
        r["taxa_obtida_por_s"] = obtida
        r["desvio_taxa_pct"] = (float(obtida) / taxa - 1) * 100 if obtida is not None else None
        r["taxa_sustentada"] = bool(mista.get("taxa_sustentada"))
        r["ingestao_cobriu_medicao"] = bool(mista.get("ingestao_cobriu_medicao"))
        r["maior_atraso_ingestao_s"] = ingestao.get("maior_atraso_s")
        r["valida"] = r["taxa_sustentada"] and r["ingestao_cobriu_medicao"]
    else:
        r["valida"] = True

    host = os.path.join(diretorio, "metricas-host.csv")
    sut = 0.0
    for rotulo, nucleos in sorted(cpu_nucleos(host, inicio_ms, fim_ms).items()):
        servico = servico_do_container(rotulo)
        r[f"cpu_{servico}_nucleos"] = nucleos
        if servico != "gerador":
            sut += nucleos
    r["cpu_sut_nucleos"] = sut
    _memoria(r, host, inicio_ms, fim_ms)
    for sentido, metrica in (("escrita", "disco_escrita_bytes_total"),
                             ("leitura", "disco_leitura_bytes_total")):
        por_s = disco_bytes_por_s(host, inicio_ms, fim_ms, metrica)
        r[f"disco_{sentido}_mib_s"] = por_s / MIB if por_s is not None else None
    gravado = disco_gravado_bytes(host)
    r["disco_gravado_gib"] = gravado / GIB if gravado is not None else None
    r["acima_do_cache_pslc"] = gravado is not None and gravado / GIB > CACHE_PSLC_GIB
    benchmark = os.path.join(diretorio, "metricas-postgres-benchmark.csv")
    if os.path.exists(benchmark):
        _benchmark(r, benchmark, inicio_ms, fim_ms)
    return r


# --- comparar -------------------------------------------------------------------------------

Valor = Callable[[Linha], Optional[float]]


def validas(linhas: Sequence[Linha]) -> List[Linha]:
    """Execuções que entram nos testes e na mediana da base (ADR-068, item 3)."""
    saida: List[Linha] = []
    for linha in linhas:
        if numero(linha, "valida") == 1.0:
            saida.append(linha)
        else:
            print(f"aviso: {linha['execucao']} não sustentou a taxa ou não cobriu a medição; "
                  f"fica fora dos testes (ADR-070, item 2)", file=sys.stderr)
    return saida


def medianas_da_base(linhas: Sequence[Linha]) -> Dict[Tuple[str, str], float]:
    """Mediana de cada percentil da busca nas execuções sem escrita de cada arquitetura."""
    base: Dict[Tuple[str, str], float] = {}
    for arquitetura in ARQUITETURAS:
        for p in DEGRADACOES:
            valores = list(_por_bloco(linhas, arquitetura, 0, latencia(p)).values())
            if valores:
                base[(arquitetura, p)] = statistics.median(valores)
    return base


def latencia(p: str) -> Valor:
    def valor(linha: Linha) -> Optional[float]:
        return numero(linha, f"{OPERACAO}_{p}_ms")
    return valor


def degradacao(p: str, base: Dict[Tuple[str, str], float]) -> Valor:
    """Latência da execução dividida pela mediana da mesma arquitetura sem escrita."""
    def valor(linha: Linha) -> Optional[float]:
        v = numero(linha, f"{OPERACAO}_{p}_ms")
        referencia = base.get((linha["arquitetura"], p))
        return v / referencia if v is not None and referencia else None
    return valor


def _por_bloco(linhas: Sequence[Linha], arquitetura: str, taxa: int, valor: Valor) -> Dict[str, float]:
    saida: Dict[str, float] = {}
    for linha in linhas:
        if linha["arquitetura"] == arquitetura and int(linha["taxa"]) == taxa:
            v = valor(linha)
            if v is not None:
                saida[linha["repeticao"]] = v
    return saida


def _comparacao(teste: str, arquitetura: str, taxa: int, metrica: str, grupo1: str, grupo2: str,
                va: Dict[str, float], vb: Dict[str, float]) -> Optional[Dict[str, Any]]:
    if not va or not vb:
        print(f"aviso: {teste} {arquitetura} {taxa} {metrica}: grupo sem valores; comparação de fora",
              file=sys.stderr)
        return None
    c = comparar(list(va.values()), list(vb.values()))
    razoes = [va[k] / vb[k] for k in va if k in vb and vb[k] != 0]
    return {
        "teste": teste, "arquitetura": arquitetura, "taxa": taxa, "metrica": metrica,
        "grupo1": grupo1, "grupo2": grupo2, "n1": c.n1, "n2": c.n2,
        "mediana1": c.mediana1, "min1": min(va.values()), "max1": max(va.values()),
        "mediana2": c.mediana2, "min2": min(vb.values()), "max2": max(vb.values()),
        "razao_medianas": c.razao_medianas, "a12": c.a12, "u": c.u, "p": c.p,
        "razao_bloco_min": min(razoes) if razoes else None,
        "razao_bloco_max": max(razoes) if razoes else None, "blocos": len(razoes),
    }


def _holm(familia: List[Dict[str, Any]]) -> None:
    for linha, ajustado in zip(familia, holm([float(l["p"]) for l in familia])):
        linha["familia"] = len(familia)
        linha["p_holm"] = ajustado
        linha["significativo"] = ajustado <= ALFA
        linha["alcancavel"] = menor_p_possivel(int(linha["n1"]), int(linha["n2"])) * len(familia) <= ALFA


def comparacoes(linhas: Sequence[Linha]) -> List[Dict[str, Any]]:
    """Os dois testes da ADR-068, item 6, só com as execuções válidas."""
    boas = validas(linhas)
    base = medianas_da_base(boas)
    saida: List[Dict[str, Any]] = []
    for arquitetura in ARQUITETURAS:
        familia: List[Dict[str, Any]] = []
        for taxa in TAXAS:
            for p in TESTADAS:
                linha = _comparacao("taxa_contra_base", arquitetura, taxa, f"{OPERACAO}_{p}_ms",
                                    f"{arquitetura}_{taxa}", f"{arquitetura}_sem_escrita",
                                    _por_bloco(boas, arquitetura, taxa, latencia(p)),
                                    _por_bloco(boas, arquitetura, 0, latencia(p)))
                if linha is not None:
                    familia.append(linha)
        _holm(familia)
        saida += familia
    familia = []
    for taxa in TAXAS:
        for p in TESTADAS:
            valor = degradacao(p, base)
            linha = _comparacao("entre_arquiteturas", "consolidada_poliglota", taxa, f"degradacao_{p}",
                                f"consolidada_{taxa}", f"poliglota_{taxa}",
                                _por_bloco(boas, "consolidada", taxa, valor),
                                _por_bloco(boas, "poliglota", taxa, valor))
            if linha is not None:
                familia.append(linha)
    _holm(familia)
    return saida + familia


def coluna(nome: str) -> Valor:
    def valor(linha: Linha) -> Optional[float]:
        return numero(linha, nome)
    return valor


def configuracoes(linhas: Sequence[Linha]) -> List[Dict[str, Any]]:
    """Mediana, mínimo e máximo de cada coluna numérica e das degradações, por arquitetura e
    taxa, só com as execuções válidas; as booleanas são contadas em todas."""
    base = medianas_da_base([l for l in linhas if numero(l, "valida") == 1.0])
    colunas: List[str] = []
    for linha in linhas:
        colunas += [k for k in linha
                    if k not in colunas and k not in IDENTIFICACAO and k not in BOOLEANAS]
    extratores: List[Tuple[str, Valor]] = [(f"degradacao_{p}", degradacao(p, base)) for p in DEGRADACOES]
    extratores += [(nome, coluna(nome)) for nome in colunas]
    saida: List[Dict[str, Any]] = []
    for arquitetura in ARQUITETURAS:
        for taxa in (0, *TAXAS):
            todas = [l for l in linhas if l["arquitetura"] == arquitetura and int(l["taxa"]) == taxa]
            if not todas:
                continue
            boas = [l for l in todas if numero(l, "valida") == 1.0]
            c: Dict[str, Any] = {"arquitetura": arquitetura, "taxa": taxa, "n": len(boas),
                                 "invalidas": len(todas) - len(boas)}
            for nome, valor in extratores:
                valores = [v for v in (valor(l) for l in boas) if v is not None]
                if valores:
                    c[f"{nome}_mediana"] = statistics.median(valores)
                    c[f"{nome}_min"] = min(valores)
                    c[f"{nome}_max"] = max(valores)
            for marca in BOOLEANAS:
                c[marca] = sum(1 for l in todas if numero(l, marca) == 1.0)
            saida.append(c)
    return saida


def _faixa(c: Dict[str, Any], i: str) -> str:
    return f"{float(c['mediana' + i]):.3g} [{float(c['min' + i]):.3g}–{float(c['max' + i]):.3g}]"


def imprimir(comparadas: Sequence[Dict[str, Any]]) -> None:
    anterior: Optional[Tuple[str, str]] = None
    for c in comparadas:
        chave = (str(c["teste"]), str(c["arquitetura"]))
        if chave != anterior:
            print(f"\n== {chave[0]}, {chave[1]} (família de {c['familia']})")
            print(f"{'taxa':>9s} {'métrica':22s} {'grupo 1 [mín–máx]':>26s} {'grupo 2 [mín–máx]':>26s} "
                  f"{'razão':>7s} {'Â12':>5s} {'p':>8s} {'p Holm':>8s} {'signif.':>7s}")
            anterior = chave
        print(f"{int(c['taxa']):9d} {str(c['metrica']):22s} {_faixa(c, '1'):>26s} {_faixa(c, '2'):>26s} "
              f"{float(c['razao_medianas']):7.3f} {float(c['a12']):5.2f} {float(c['p']):8.2g} "
              f"{float(c['p_holm']):8.2g} {'sim' if c['significativo'] else 'não':>7s}"
              + ("" if c["alcancavel"] else "  (inalcançável)"))


def main() -> None:
    p = argparse.ArgumentParser(description="Análise da carga mista (ADR-068, item 6)")
    p.add_argument("--saida", default=PROCESSADOS, help="diretório dos CSV")
    etapas = p.add_subparsers(dest="etapa", required=True)
    r = etapas.add_parser("resumir", help="lê os brutos e grava mista-execucoes.csv")
    r.add_argument("janela", nargs="?", default=JANELA, help="janela da carga mista")
    etapas.add_parser("comparar", help="lê mista-execucoes.csv e grava as comparações")
    args = p.parse_args()
    saida = str(args.saida)
    execucoes_csv = os.path.join(saida, f"{PREFIXO}-execucoes.csv")
    if str(args.etapa) == "resumir":
        janela = str(args.janela)
        resumos: List[Dict[str, Any]] = []
        for diretorio in execucoes_da_janela(janela):
            print(f"lendo {os.path.basename(diretorio)}", flush=True)
            resumo = resumir_execucao(diretorio)
            resumo["janela"] = janela
            resumos.append(resumo)
        os.makedirs(saida, exist_ok=True)
        gravar_csv(execucoes_csv, resumos)
        print(f"{len(resumos)} execuções em {execucoes_csv}")
        return
    linhas = ler_execucoes(execucoes_csv)
    comparadas = comparacoes(linhas)
    gravar_csv(os.path.join(saida, f"{PREFIXO}-configuracoes.csv"), configuracoes(linhas))
    gravar_csv(os.path.join(saida, f"{PREFIXO}-comparacoes.csv"), comparadas)
    imprimir(comparadas)


if __name__ == "__main__":
    main()
