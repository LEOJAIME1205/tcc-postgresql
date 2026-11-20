"""Análise das execuções curtas (ADR-067, item 7; ADR-061, ADR-065, item 4; ADR-070).

Duas etapas:

- resumir: lê os brutos das quatro janelas e grava uma linha por execução em
  curtas-execucoes.csv, com o resumo da calibração (harness/analise/calibracao.py) mais a
  repetição, o nível, a eficiência da carga do C e a média de cada tipo de consulta do C. É a
  etapa pesada: lê inteiros os operacoes.csv do A e do B.
- comparar: lê esse CSV e grava curtas-configuracoes.csv, com mediana, mínimo e máximo de
  cada métrica por configuração (ADR-070, item 5), e curtas-comparacoes.csv, com o
  Mann-Whitney exato e bilateral, a correção de Holm, o Â12 e a razão das medianas
  (harness/analise/estatistica.py).

Métricas testadas e famílias de Holm (complemento da ADR-067, item 3):
- A: vazão, p50 e p99 da busca híbrida; família de 3 em cada nível;
- B, vazão: consumo por segundo, contra a ingênua e contra a outbox; família de 2 por nível;
- B, latência: p50 e p99 fim a fim, contra as duas; família de 4 por taxa;
- C, carga: vazão da carga inteira (ADR-065, item 2), núcleos-segundo do sistema sob teste
  por milhão de métricas e MiB gravados no SSD por milhão de métricas, na carga (ADR-065,
  item 4); família de 3 por nível;
- C, consultas: média de cada tipo em cada execução (ADR-061), 20 contra 20, com os 4 níveis
  juntos (ADR-067, item 3); família dos 15 tipos.
As outras colunas são descritivas.

O primeiro grupo de toda comparação é a arquitetura consolidada: o Â12 é a probabilidade de
uma execução dela ter valor maior que uma da poliglota, e a razão é a mediana dela dividida
pela da poliglota. A coluna a12_consolidada_melhor traduz o Â12 para o sentido da métrica. O
pareamento por bloco (mesma repetição e, nas consultas, mesmo nível; ADR-032) entra só como
descrição, pela menor e pela maior razão entre as arquiteturas no mesmo bloco.

Entram todas as execuções que terminaram em ok (ADR-070); execuções acima de 60 GiB gravados
e com o gerador como gargalo ficam marcadas e são contadas por configuração.

    uv run python -m harness.analise.curtas resumir [<janela> ...]
    uv run python -m harness.analise.curtas comparar
"""

import argparse
import os
import statistics
import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, cast

from harness.analise.calibracao import resumir as resumir_calibracao
from harness.analise.comum import (PROCESSADOS, Linha, disco_bytes_por_s, execucoes_da_janela,
                                   gravar_csv, ler_execucoes, ler_json, numero)
from harness.analise.estatistica import ALFA, comparar, holm, menor_p_possivel

JANELAS: Tuple[str, ...] = ("20261111-0350", "20261112-0004", "20261113-1656", "20261114-1321")
PREFIXO: str = "curtas"
GRUPO_CONSULTAS: str = "c-consultas"


@dataclass(frozen=True)
class Metrica:
    coluna: str
    maior_melhor: bool


@dataclass(frozen=True)
class Grupo:
    nome: str
    consolidada: str
    poliglotas: Tuple[str, ...]
    testadas: Tuple[Metrica, ...]
    descritivas: Tuple[str, ...]


_GRUPOS: Tuple[Grupo, ...] = (
    Grupo("a", "consolidada", ("poliglota",),
          (Metrica("busca_hibrida_por_s", True), Metrica("busca_hibrida_p50_ms", False),
           Metrica("busca_hibrida_p99_ms", False)),
          ("busca_hibrida_p95_ms", "erros", "variacao_minutos_pct", "cpu_sut_nucleos",
           "cpu_gerador_nucleos", "disco_gravado_gib")),
    Grupo("b-vazao", "consolidada", ("ingenua", "outbox"),
          (Metrica("consome_por_s", True),),
          ("enfileira_por_s", "crescimento_pct", "fim_a_fim_p50_ms", "fim_a_fim_p99_ms", "erros",
           "retiradas_vazias", "variacao_minutos_pct", "cpu_sut_nucleos", "cpu_gerador_nucleos",
           "disco_gravado_gib")),
    Grupo("b-latencia", "consolidada", ("ingenua", "outbox"),
          (Metrica("fim_a_fim_p50_ms", False), Metrica("fim_a_fim_p99_ms", False)),
          ("fim_a_fim_p95_ms", "enfileira_por_s", "desvio_taxa_pct", "agenda_atraso_max_ms",
           "crescimento_pct", "erros", "retiradas_vazias", "cpu_sut_nucleos",
           "cpu_gerador_nucleos", "disco_gravado_gib")),
    Grupo("c", "timescaledb", ("influxdb",),
          (Metrica("vazao_por_s", True), Metrica("cpu_sut_s_por_milhao", False),
           Metrica("disco_mib_por_milhao", False)),
          ("vazao_antes_do_cache_por_s", "carga_s", "variacao_minutos_pct", "cpu_sut_nucleos",
           "cpu_gerador_antes_do_cache_nucleos", "disco_gravado_gib")),
)
GRUPOS: Dict[str, Grupo] = {g.nome: g for g in _GRUPOS}


# --- resumir --------------------------------------------------------------------------------

def _grupo_e_nivel(eixo: str, concorrencia: int, taxa: Optional[int]) -> Tuple[str, int]:
    if eixo == "b":
        return ("b-latencia", taxa) if taxa is not None else ("b-vazao", concorrencia)
    return eixo, concorrencia


def _carga_c(r: Dict[str, Any], diretorio: str, inicio_ms: int, fim_ms: int,
             gerador: Dict[str, Any]) -> None:
    """Eficiência da carga (ADR-065, item 4) e média de cada tipo de consulta (ADR-061)."""
    vazao = float(r["vazao_por_s"])
    bytes_s = disco_bytes_por_s(os.path.join(diretorio, "metricas-host.csv"), inicio_ms, fim_ms)
    if vazao > 0:
        r["cpu_sut_s_por_milhao"] = float(r["cpu_sut_nucleos"]) / vazao * 1e6
        r["disco_mib_por_milhao"] = bytes_s / vazao * 1e6 / 2**20 if bytes_s is not None else None
    parametros = cast(Dict[str, Any], gerador.get("parametros", {}))
    esperadas = parametros.get("consultas_por_tipo")
    resumo = ler_json(os.path.join(diretorio, "resumo.json"))
    for valor in cast(List[Dict[str, Any]], resumo.get("valores", [])):
        metrica, rotulo = str(valor.get("metrica")), str(valor.get("rotulo"))
        if metrica == "consulta_latencia_us" and rotulo.endswith(":media"):
            r[f"consulta_{rotulo[:-len(':media')]}_media_ms"] = float(valor["valor"]) / 1000
        elif metrica == "consulta_contagem" and esperadas is not None and float(valor["valor"]) != float(esperadas):
            print(f"aviso: {os.path.basename(diretorio)}: {rotulo} com {valor['valor']} consultas, "
                  f"esperadas {esperadas}", file=sys.stderr)


def resumir_execucao(diretorio: str) -> Optional[Dict[str, Any]]:
    """Resumo da calibração mais o que a comparação das curtas precisa."""
    manifesto = ler_json(os.path.join(diretorio, "manifesto.json"))
    if manifesto.get("cenario") != "curta" or manifesto.get("espera") == "aviso":
        print(f"aviso: {os.path.basename(diretorio)} não é execução curta; fica de fora", file=sys.stderr)
        return None
    gerador = ler_json(os.path.join(diretorio, "gerador.json"))
    r = resumir_calibracao(diretorio)
    eixo = str(manifesto["eixo"])
    taxa = int(manifesto["taxa"]) if manifesto.get("taxa") is not None else None
    grupo, nivel = _grupo_e_nivel(eixo, int(manifesto["concorrencia"]), taxa)
    r.update(grupo=grupo, nivel=nivel, arquitetura=str(manifesto["arquitetura"]),
             repeticao=int(manifesto["repeticao"]), taxa=taxa)
    if eixo == "b":
        r["retiradas_vazias"] = gerador.get("retiradas_vazias")
        agenda = cast(Optional[Dict[str, Any]], gerador.get("agenda"))
        if taxa is not None:
            r["desvio_taxa_pct"] = (float(r.get("enfileira_por_s", 0.0)) / taxa - 1) * 100
            r["agenda_atraso_max_ms"] = agenda.get("atraso_max_ms") if agenda else None
    if eixo == "c":
        _carga_c(r, diretorio, int(gerador["medicao_inicio_ms"]), int(gerador["medicao_fim_ms"]),
                 gerador)
    return r


# --- comparar -------------------------------------------------------------------------------

def _contar(execucoes: Sequence[Linha], coluna: str, valor: float) -> int:
    return sum(1 for e in execucoes if numero(e, coluna) == valor)


def configuracoes(linhas: Sequence[Linha]) -> List[Dict[str, Any]]:
    """Mediana, mínimo e máximo de cada métrica por grupo, arquitetura e nível (ADR-070)."""
    por_config: Dict[Tuple[str, str, int], List[Linha]] = {}
    for linha in linhas:
        por_config.setdefault((linha["grupo"], linha["arquitetura"], int(linha["nivel"])), []).append(linha)
    saida: List[Dict[str, Any]] = []
    for (nome, arquitetura, nivel), execucoes in sorted(por_config.items()):
        grupo = GRUPOS[nome]
        c: Dict[str, Any] = {"grupo": nome, "arquitetura": arquitetura, "nivel": nivel,
                             "n": len(execucoes)}
        for coluna in [*(m.coluna for m in grupo.testadas), *grupo.descritivas]:
            valores = [v for v in (numero(e, coluna) for e in execucoes) if v is not None]
            if valores:
                c[f"{coluna}_mediana"] = statistics.median(valores)
                c[f"{coluna}_min"] = min(valores)
                c[f"{coluna}_max"] = max(valores)
        c["acima_do_cache_pslc"] = _contar(execucoes, "acima_do_cache_pslc", 1.0)
        c["gerador_gargalo"] = _contar(execucoes, "gerador_gargalo", 1.0)
        if nome.startswith("b"):
            c["fila_crescendo"] = _contar(execucoes, "sustentavel", 0.0)
        saida.append(c)
    return saida


def _comparacao(grupo: str, nivel: str, metrica: Metrica, consolidada: str, poliglota: str,
                a: Sequence[Linha], b: Sequence[Linha],
                bloco: Callable[[Linha], str]) -> Optional[Dict[str, Any]]:
    """Uma linha de curtas-comparacoes.csv, ainda sem a correção de Holm."""
    va: Dict[str, float] = {}
    vb: Dict[str, float] = {}
    for linhas, destino in ((a, va), (b, vb)):
        for linha in linhas:
            valor = numero(linha, metrica.coluna)
            if valor is not None:
                destino[bloco(linha)] = valor
    if not va or not vb:
        print(f"aviso: {grupo} {nivel} {metrica.coluna}: sem valores de {consolidada} ou de "
              f"{poliglota}; comparação de fora", file=sys.stderr)
        return None
    c = comparar(list(va.values()), list(vb.values()))
    razoes = [va[k] / vb[k] for k in va if k in vb and vb[k] != 0]
    if c.mediana1 == c.mediana2:
        melhor = "empate"
    else:
        melhor = consolidada if (c.mediana1 > c.mediana2) == metrica.maior_melhor else poliglota
    return {
        "grupo": grupo, "nivel": nivel, "metrica": metrica.coluna,
        "maior_melhor": metrica.maior_melhor, "consolidada": consolidada, "poliglota": poliglota,
        "n_consolidada": c.n1, "n_poliglota": c.n2,
        "mediana_consolidada": c.mediana1, "min_consolidada": min(va.values()),
        "max_consolidada": max(va.values()),
        "mediana_poliglota": c.mediana2, "min_poliglota": min(vb.values()),
        "max_poliglota": max(vb.values()),
        "razao_medianas": c.razao_medianas, "a12": c.a12,
        "a12_consolidada_melhor": c.a12 if metrica.maior_melhor else 1 - c.a12,
        "u": c.u, "p": c.p,
        "razao_bloco_min": min(razoes) if razoes else None,
        "razao_bloco_max": max(razoes) if razoes else None,
        "blocos": len(razoes), "melhor_mediana": melhor,
    }


def _holm(familia: List[Dict[str, Any]]) -> None:
    """p ajustado de Holm dentro da família; alcancavel diz se o teste, sem empates, chegaria
    a ALFA com esse tamanho de amostra e de família."""
    for linha, ajustado in zip(familia, holm([float(l["p"]) for l in familia])):
        linha["familia"] = len(familia)
        linha["p_holm"] = ajustado
        linha["significativo"] = ajustado <= ALFA
        linha["alcancavel"] = (menor_p_possivel(int(linha["n_consolidada"]), int(linha["n_poliglota"]))
                               * len(familia) <= ALFA)


def _por_repeticao(linha: Linha) -> str:
    return linha["repeticao"]


def _por_nivel_e_repeticao(linha: Linha) -> str:
    return f"{linha['nivel']}/{linha['repeticao']}"


def comparacoes(linhas: Sequence[Linha]) -> List[Dict[str, Any]]:
    """Consolidada contra cada poliglota, por nível e métrica, com Holm por família."""
    def execucoes(grupo: str, arquitetura: str, nivel: Optional[int]) -> List[Linha]:
        return [l for l in linhas if l["grupo"] == grupo and l["arquitetura"] == arquitetura
                and (nivel is None or int(l["nivel"]) == nivel)]

    saida: List[Dict[str, Any]] = []
    for grupo in _GRUPOS:
        for nivel in sorted({int(l["nivel"]) for l in linhas if l["grupo"] == grupo.nome}):
            familia: List[Dict[str, Any]] = []
            a = execucoes(grupo.nome, grupo.consolidada, nivel)
            for poliglota in grupo.poliglotas:
                b = execucoes(grupo.nome, poliglota, nivel)
                for metrica in grupo.testadas:
                    linha = _comparacao(grupo.nome, str(nivel), metrica, grupo.consolidada,
                                        poliglota, a, b, _por_repeticao)
                    if linha is not None:
                        familia.append(linha)
            _holm(familia)
            saida += familia
    c = GRUPOS["c"]
    a = execucoes("c", c.consolidada, None)
    b = execucoes("c", c.poliglotas[0], None)
    colunas = sorted({k for l in [*a, *b] for k in l
                      if k.startswith("consulta_") and k.endswith("_media_ms") and l[k]})
    familia = []
    for coluna in colunas:
        linha = _comparacao(GRUPO_CONSULTAS, "todos", Metrica(coluna, False), c.consolidada,
                            c.poliglotas[0], a, b, _por_nivel_e_repeticao)
        if linha is not None:
            familia.append(linha)
    _holm(familia)
    return saida + familia


def _fmt(valor: Any) -> str:
    if valor is None or valor == "":
        return "-"
    if isinstance(valor, bool):
        return "sim" if valor else "não"
    if isinstance(valor, float):
        absoluto = abs(valor)
        casas = 0 if absoluto >= 1000 else 1 if absoluto >= 10 else 2 if absoluto >= 0.1 else 4
        return f"{valor:,.{casas}f}".replace(",", " ")
    return str(valor)


def imprimir(comparadas: Sequence[Dict[str, Any]]) -> None:
    anterior: Optional[Tuple[str, str]] = None
    for c in comparadas:
        chave = (str(c["grupo"]), str(c["nivel"]))
        if chave != anterior:
            print(f"\n== {chave[0]}, nível {chave[1]} (família de {c['familia']})")
            print(f"{'métrica':34s} {'poliglota':10s} {'consolidada [mín–máx]':>30s} "
                  f"{'poliglota [mín–máx]':>30s} {'razão':>7s} {'Â12':>5s} {'p':>8s} {'p Holm':>8s} "
                  f"{'signif.':>7s} {'melhor':>12s}")
            anterior = chave
        cons = (f"{_fmt(c['mediana_consolidada'])} [{_fmt(c['min_consolidada'])}–"
                f"{_fmt(c['max_consolidada'])}]")
        pol = f"{_fmt(c['mediana_poliglota'])} [{_fmt(c['min_poliglota'])}–{_fmt(c['max_poliglota'])}]"
        print(f"{str(c['metrica']):34s} {str(c['poliglota']):10s} {cons:>30s} {pol:>30s} "
              f"{float(c['razao_medianas']):7.3f} {float(c['a12']):5.2f} {float(c['p']):8.2g} "
              f"{float(c['p_holm']):8.2g} {_fmt(c['significativo']):>7s} {str(c['melhor_mediana']):>12s}"
              + ("" if c["alcancavel"] else "  (inalcançável)"))


def main() -> None:
    p = argparse.ArgumentParser(description="Análise das execuções curtas (ADR-067, item 7)")
    p.add_argument("--saida", default=PROCESSADOS, help="diretório dos CSV")
    etapas = p.add_subparsers(dest="etapa", required=True)
    r = etapas.add_parser("resumir", help="lê os brutos e grava curtas-execucoes.csv")
    r.add_argument("janelas", nargs="*", default=list(JANELAS), help="janelas das curtas")
    etapas.add_parser("comparar", help="lê curtas-execucoes.csv e grava as comparações")
    args = p.parse_args()
    saida = str(args.saida)
    execucoes_csv = os.path.join(saida, f"{PREFIXO}-execucoes.csv")
    if str(args.etapa) == "resumir":
        resumos: List[Dict[str, Any]] = []
        for janela in cast(List[str], args.janelas):
            for diretorio in execucoes_da_janela(str(janela)):
                print(f"lendo {os.path.basename(diretorio)}", flush=True)
                resumo = resumir_execucao(diretorio)
                if resumo is not None:
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
