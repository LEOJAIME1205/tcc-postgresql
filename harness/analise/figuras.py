"""Figuras do artigo (Etapa 4.3 do plano; ADR-070, itens 4 e 5).

Lê os CSV de resultados/processados/ e grava cada figura em PDF (para o LaTeX do template da
SBC) e em PNG (para conferência) em resultados/processados/figuras/.

- Execuções curtas e carga mista: cada repetição é um ponto, e a linha liga as medianas das
  repetições de cada nível (ADR-070, item 5). Pontos vazados marcam execuções que entram nas
  tabelas com ressalva: no C, o gerador como gargalo (vazão como limite inferior, revisão da
  ADR-065); na mista, a execução que não sustentou a taxa, fora da mediana e dos testes
  (ADR-068, item 3).
- Longa duração: as séries por minuto entram como estão, sem suavização nem remoção de pontos
  (ADR-070, item 4); o controle com fio aparece ao lado das execuções no gráfico do SSD.

Cor e marcador seguem a arquitetura em todas as figuras: o lado PostgreSQL em azul com
círculo, a poliglota, a ingênua e o InfluxDB em laranja com quadrado, a outbox em verde-água
com triângulo e o controle com fio em cinza. As três cores passam no validador de paleta
(separação para daltonismo em todos os pares); o marcador repete a identidade para quem
imprime em tons de cinza.

    uv run --group analise python -m harness.analise.figuras
"""
# Os stubs do matplotlib deixam o **kwargs dos métodos sem tipo, e o modo estrito acusaria cada
# chamada; só essa regra fica desligada, só neste arquivo.
# pyright: reportUnknownMemberType=false

import argparse
import math
import os
import statistics
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple, cast

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.axes import Axes  # noqa: E402
from matplotlib.figure import Figure, SubFigure  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.ticker import FuncFormatter, LogLocator, NullLocator  # noqa: E402

from harness.analise.comum import PROCESSADOS, Linha, ler_execucoes, numero  # noqa: E402
from harness.analise.mista import medianas_da_base, validas  # noqa: E402

SAIDA: str = os.path.join(PROCESSADOS, "figuras")
LONGA: str = os.path.join(PROCESSADOS, "longa")
LARGURA: float = 6.3  # polegadas: a largura do texto no template da SBC (~16 cm)

FUNDO = "#ffffff"
TINTA = "#0b0b0b"
TINTA_2 = "#52514e"
APAGADA = "#898781"
GRADE = "#e1e0d9"
EIXO = "#c3c2b7"


@dataclass(frozen=True)
class Estilo:
    rotulo: str
    cor: str
    marcador: str


AZUL, LARANJA, VERDE_AGUA = "#2a78d6", "#eb6834", "#1baf7a"
ESTILOS: Dict[str, Estilo] = {
    "consolidada": Estilo("Consolidada", AZUL, "o"),
    "timescaledb": Estilo("TimescaleDB", AZUL, "o"),
    "poliglota": Estilo("Poliglota", LARANJA, "s"),
    "ingenua": Estilo("Ingênua", LARANJA, "s"),
    "influxdb": Estilo("InfluxDB", LARANJA, "s"),
    "outbox": Estilo("Outbox", VERDE_AGUA, "^"),
    "fio": Estilo("Controle com fio", APAGADA, "D"),
}

TIPOS_C: Tuple[str, ...] = (
    "single-groupby-1-1-1", "single-groupby-1-1-12", "single-groupby-1-8-1",
    "single-groupby-5-1-1", "single-groupby-5-1-12", "single-groupby-5-8-1",
    "cpu-max-all-1", "cpu-max-all-8", "double-groupby-1", "double-groupby-5",
    "double-groupby-all", "high-cpu-all", "high-cpu-1", "lastpoint", "groupby-orderby-limit",
)

PASSO_REPETICAO: float = 0.045  # afastamento horizontal entre repetições, em unidades de posição
PASSO_ARQUITETURA: float = 0.30  # afastamento entre arquiteturas no mesmo nível


def estilo_global() -> None:
    plt.rcParams.update({
        "font.family": "sans-serif", "font.sans-serif": ["DejaVu Sans"], "font.size": 7.5,
        "axes.facecolor": FUNDO, "figure.facecolor": FUNDO, "savefig.facecolor": FUNDO,
        "axes.edgecolor": EIXO, "axes.linewidth": 0.6, "axes.labelcolor": TINTA_2,
        "axes.titlesize": 8, "axes.titlecolor": TINTA, "axes.titlelocation": "left",
        "axes.titlepad": 6, "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "axes.grid.axis": "y", "axes.axisbelow": True,
        "grid.color": GRADE, "grid.linewidth": 0.5, "grid.linestyle": "-",
        "xtick.color": EIXO, "ytick.color": EIXO, "xtick.labelcolor": TINTA_2,
        "ytick.labelcolor": TINTA_2, "xtick.major.size": 2.5, "ytick.major.size": 0,
        "xtick.major.width": 0.6, "legend.frameon": False, "legend.fontsize": 7.5,
        "lines.solid_capstyle": "round", "lines.solid_joinstyle": "round",
        "pdf.fonttype": 42, "ps.fonttype": 42, "savefig.dpi": 200,
    })


def gravar(figura: Figure, nome: str, saida: str) -> None:
    os.makedirs(saida, exist_ok=True)
    for extensao in ("pdf", "png"):
        figura.savefig(os.path.join(saida, f"{nome}.{extensao}"), bbox_inches="tight",
                       pad_inches=0.03)
    plt.close(figura)
    print(f"{nome}: pdf e png em {os.path.relpath(saida, PROCESSADOS)}")


def legenda(figura: Figure, arquiteturas: Sequence[str], titulo_vazado: Optional[str] = None) -> None:
    """Legenda única no alto da figura, numa linha; o vazado ganha uma entrada própria."""
    alcas: List[Line2D] = [Line2D([], [], color=ESTILOS[a].cor, marker=ESTILOS[a].marcador,
                                  markersize=5, linewidth=1.5, label=ESTILOS[a].rotulo)
                           for a in arquiteturas]
    if titulo_vazado:
        alcas.append(Line2D([], [], color=APAGADA, marker="o", markersize=5, linewidth=0,
                            markerfacecolor=FUNDO, markeredgewidth=0.9, label=titulo_vazado))
    figura.legend(handles=alcas, loc="lower left", bbox_to_anchor=(0.0, 1.0), ncol=len(alcas),
                  handlelength=1.8, columnspacing=1.6, borderaxespad=0.2)


def formato_numero(v: float, _pos: object = None) -> str:
    """Rótulos de eixo com separador brasileiro e sufixos mil e mi."""
    if v == 0:
        return "0"
    if abs(v) >= 1e6:
        return f"{v / 1e6:g} mi".replace(".", ",")
    if abs(v) >= 1e3:
        return f"{v / 1e3:g} mil".replace(".", ",")
    if abs(v) >= 1:
        return f"{v:g}".replace(".", ",")
    return f"{v:.2g}".replace(".", ",")


def formato_razao(v: float, _pos: object = None) -> str:
    return f"{v:.2f}".replace(".", ",")


def subdivisoes_log(minimo: float, maximo: float) -> Tuple[float, ...]:
    """Marcas por década conforme a faixa: as potências de 10 bastam em faixas largas; numa
    faixa estreita, sem nenhuma potência dentro, entram 2, 3 e 5 de cada década."""
    decadas = math.log10(maximo / minimo) if minimo > 0 and maximo > minimo else 0.0
    if decadas >= 2.5:
        return (1.0,)
    if decadas >= 0.8:
        return (1.0, 2.0, 5.0)
    return (1.0, 1.5, 2.0, 3.0, 5.0, 7.0)


def eixo_log(ax: Axes) -> None:
    """Escala log no eixo y, chamada depois dos dados: as marcas dependem da faixa."""
    ax.set_yscale("log")
    ax.autoscale_view()
    minimo, maximo = ax.get_ylim()
    ax.yaxis.set_major_locator(LogLocator(base=10, subs=subdivisoes_log(minimo, maximo)))
    ax.yaxis.set_major_formatter(FuncFormatter(formato_numero))
    ax.yaxis.set_minor_locator(NullLocator())


# --- pontos por nível ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Ponto:
    posicao: float
    valor: float
    repeticao: int
    vazado: bool
    na_mediana: bool


def pontos(linhas: Sequence[Linha], arquitetura: str, coluna: str,
           posicao: Callable[[Linha], float],
           vazado: Callable[[Linha], bool] = lambda _l: False,
           na_mediana: Callable[[Linha], bool] = lambda _l: True) -> List[Ponto]:
    saida: List[Ponto] = []
    for linha in linhas:
        if linha["arquitetura"] != arquitetura:
            continue
        v = numero(linha, coluna)
        if v is None:
            continue
        saida.append(Ponto(posicao(linha), v, int(linha["repeticao"]), vazado(linha),
                           na_mediana(linha)))
    return saida


def medianas(serie: Sequence[Ponto]) -> List[Tuple[float, float]]:
    posicoes = sorted({p.posicao for p in serie})
    saida: List[Tuple[float, float]] = []
    for x in posicoes:
        valores = [p.valor for p in serie if p.posicao == x and p.na_mediana]
        if valores:
            saida.append((x, statistics.median(valores)))
    return saida


def deslocamento(indice: int, total: int, repeticao: int = 3) -> float:
    """Afastamento horizontal da arquitetura e da repetição (1 a 5, centrada na 3)."""
    return (indice - (total - 1) / 2) * PASSO_ARQUITETURA + (repeticao - 3) * PASSO_REPETICAO


def desenhar_niveis(ax: Axes, series: Dict[str, List[Ponto]]) -> Dict[str, Tuple[float, float]]:
    """Pontos de cada repetição e linha das medianas; devolve o fim de cada linha."""
    fins: Dict[str, Tuple[float, float]] = {}
    for i, (arquitetura, serie) in enumerate(series.items()):
        e = ESTILOS[arquitetura]
        meio = deslocamento(i, len(series))
        linha = medianas(serie)
        if linha:
            ax.plot([x + meio for x, _ in linha], [y for _, y in linha], color=e.cor,
                    linewidth=1.5, zorder=2)
            fins[arquitetura] = (linha[-1][0] + meio, linha[-1][1])
        for p in serie:
            x = p.posicao + deslocamento(i, len(series), p.repeticao)
            ax.plot(x, p.valor, linestyle="none", marker=e.marcador, markersize=4,
                    markerfacecolor=FUNDO if p.vazado else e.cor,
                    markeredgecolor=e.cor if p.vazado else FUNDO,
                    markeredgewidth=0.9 if p.vazado else 0.5, zorder=3)
    return fins


def rotulos_diretos(ax: Axes, fins: Dict[str, Tuple[float, float]], minimo: float = 0.08) -> bool:
    """Nome de cada série ao fim da linha, em tinta neutra; desiste se dois ficam a menos de
    `minimo` da altura do eixo (a legenda continua valendo)."""
    if not fins:
        return False
    ax.autoscale_view()
    alturas: List[Tuple[float, str]] = []
    for arquitetura, (x, y) in fins.items():
        _, ya = ax.transAxes.inverted().transform(ax.transData.transform((x, y)))
        alturas.append((float(ya), arquitetura))
    alturas.sort()
    if any(b[0] - a[0] < minimo for a, b in zip(alturas, alturas[1:])):
        return False
    for arquitetura, (x, y) in fins.items():
        ax.annotate(ESTILOS[arquitetura].rotulo, (x, y), xytext=(6, 0),
                    textcoords="offset points", va="center", ha="left", color=TINTA_2,
                    fontsize=7)
    return True


def eixo_niveis(ax: Axes, niveis: Sequence[float], rotulos: Sequence[str], titulo: str,
                folga_direita: float = 0.5) -> None:
    ax.set_xticks(list(niveis), list(rotulos))
    ax.set_xlim(min(niveis) - 0.5, max(niveis) + folga_direita)
    ax.set_xlabel(titulo)


def potencias_de_2(linhas: Sequence[Linha], coluna: str = "nivel") -> List[int]:
    return sorted({int(float(l[coluna])) for l in linhas})


# --- figuras das execuções curtas -------------------------------------------------------------

def _log2(linha: Linha) -> float:
    return math.log2(float(linha["nivel"]))


def figura_a(curtas: Sequence[Linha], saida: str) -> None:
    linhas = [l for l in curtas if l["eixo"] == "a"]
    arquiteturas = ("consolidada", "poliglota")
    niveis = potencias_de_2(linhas)
    figura, eixos = plt.subplots(1, 3, figsize=(LARGURA, 2.1))
    paineis = (("busca_hibrida_por_s", "(a) Vazão (buscas/s)"),
               ("busca_hibrida_p50_ms", "(b) Latência p50 (ms)"),
               ("busca_hibrida_p99_ms", "(c) Latência p99 (ms)"))
    for k, (ax, (coluna, titulo)) in enumerate(zip(list(eixos), paineis)):
        series = {a: pontos(linhas, a, coluna, _log2) for a in arquiteturas}
        fins = desenhar_niveis(ax, series)
        eixo_log(ax)
        eixo_niveis(ax, [math.log2(n) for n in niveis], [str(n) for n in niveis], "Clientes",
                    folga_direita=2.0 if k == 0 else 0.5)
        ax.set_title(titulo)
        if k == 0:
            rotulos_diretos(ax, fins)
    legenda(figura, arquiteturas)
    figura.tight_layout(w_pad=1.5)
    gravar(figura, "a-busca", saida)


def figura_b_vazao(curtas: Sequence[Linha], saida: str) -> None:
    linhas = [l for l in curtas if l["eixo"] == "b" and l["grupo"] == "b-vazao"]
    arquiteturas = ("consolidada", "ingenua", "outbox")
    niveis = potencias_de_2(linhas)
    figura, eixos = plt.subplots(1, 2, figsize=(LARGURA, 2.2))
    paineis = (("consome_por_s", "(a) Consumo (mensagens/s)"),
               ("cpu_sut_nucleos", "(b) CPU do sistema sob teste (núcleos)"))
    for ax, (coluna, titulo) in zip(list(eixos), paineis):
        series = {a: pontos(linhas, a, coluna, _log2) for a in arquiteturas}
        fins = desenhar_niveis(ax, series)
        ax.yaxis.set_major_formatter(FuncFormatter(formato_numero))
        ax.set_ylim(bottom=0)
        eixo_niveis(ax, [math.log2(n) for n in niveis], [str(n) for n in niveis],
                    "Produtores e consumidores", folga_direita=1.6)
        ax.set_title(titulo)
        rotulos_diretos(ax, fins)
    legenda(figura, arquiteturas)
    figura.tight_layout(w_pad=1.5)
    gravar(figura, "b-vazao", saida)


def figura_b_latencia(curtas: Sequence[Linha], saida: str) -> None:
    linhas = [l for l in curtas if l["eixo"] == "b" and l["grupo"] == "b-latencia"]
    arquiteturas = ("consolidada", "ingenua", "outbox")
    taxas = sorted({int(float(l["nivel"])) for l in linhas})
    indice = {t: float(i) for i, t in enumerate(taxas)}
    figura, eixos = plt.subplots(1, 2, figsize=(LARGURA, 2.2))
    paineis = (("fim_a_fim_p50_ms", "(a) Fim a fim p50 (ms)"),
               ("fim_a_fim_p99_ms", "(b) Fim a fim p99 (ms)"))
    for k, (ax, (coluna, titulo)) in enumerate(zip(list(eixos), paineis)):
        series = {a: pontos(linhas, a, coluna, lambda l: indice[int(float(l["nivel"]))])
                  for a in arquiteturas}
        fins = desenhar_niveis(ax, series)
        eixo_log(ax)
        eixo_niveis(ax, list(indice.values()), [formato_numero(t) for t in taxas],
                    "Taxa oferecida (mensagens/s)", folga_direita=1.3 if k == 0 else 0.5)
        ax.set_title(titulo)
        if k == 0:
            rotulos_diretos(ax, fins)
    legenda(figura, arquiteturas)
    figura.tight_layout(w_pad=1.5)
    gravar(figura, "b-latencia", saida)


def figura_c_carga(curtas: Sequence[Linha], saida: str) -> None:
    linhas = [l for l in curtas if l["eixo"] == "c"]
    arquiteturas = ("timescaledb", "influxdb")
    niveis = potencias_de_2(linhas)

    def gargalo(linha: Linha) -> bool:
        return numero(linha, "gerador_gargalo") == 1.0

    figura, eixos = plt.subplots(1, 3, figsize=(LARGURA, 2.1))
    paineis = (("vazao_por_s", "(a) Vazão (métricas/s)"),
               ("cpu_sut_s_por_milhao", "(b) CPU (núcleos·s por milhão)"),
               ("disco_mib_por_milhao", "(c) Gravado no SSD (MiB por milhão)"))
    vazados = False
    for k, (ax, (coluna, titulo)) in enumerate(zip(list(eixos), paineis)):
        series = {a: pontos(linhas, a, coluna, _log2, vazado=gargalo) for a in arquiteturas}
        vazados = vazados or any(p.vazado for s in series.values() for p in s)
        fins = desenhar_niveis(ax, series)
        ax.yaxis.set_major_formatter(FuncFormatter(formato_numero))
        ax.set_ylim(bottom=0)
        eixo_niveis(ax, [math.log2(n) for n in niveis], [str(n) for n in niveis],
                    "Trabalhadores", folga_direita=2.2 if k == 0 else 0.5)
        ax.set_title(titulo)
        if k == 0:
            rotulos_diretos(ax, fins)
    legenda(figura, arquiteturas, "Gerador como gargalo (limite inferior)" if vazados else None)
    figura.tight_layout(w_pad=1.5)
    gravar(figura, "c-carga", saida)


def figura_c_consultas(curtas: Sequence[Linha], saida: str) -> None:
    """Média de cada tipo de consulta nas 20 execuções de cada lado (4 níveis × 5 repetições)."""
    linhas = [l for l in curtas if l["eixo"] == "c"]
    arquiteturas = ("timescaledb", "influxdb")
    figura, ax = plt.subplots(figsize=(LARGURA, 4.2))
    for i, arquitetura in enumerate(arquiteturas):
        e = ESTILOS[arquitetura]
        for linha_y, tipo in enumerate(TIPOS_C):
            valores = [v for l in linhas if l["arquitetura"] == arquitetura
                       and (v := numero(l, f"consulta_{tipo}_media_ms")) is not None]
            y = linha_y + (i - 0.5) * 0.34
            ax.plot(valores, [y] * len(valores), linestyle="none", marker=e.marcador,
                    markersize=3.2, markerfacecolor=e.cor, markeredgecolor=FUNDO,
                    markeredgewidth=0.4, alpha=0.55, zorder=3)
            if valores:
                ax.plot([statistics.median(valores)] * 2, [y - 0.15, y + 0.15], color=e.cor,
                        linewidth=2.0, zorder=4)
    ax.set_xscale("log")
    ax.xaxis.set_major_formatter(FuncFormatter(formato_numero))
    ax.xaxis.set_minor_locator(NullLocator())
    ax.grid(axis="x")
    ax.grid(axis="y", visible=False)
    ax.set_yticks(range(len(TIPOS_C)), list(TIPOS_C))
    ax.set_ylim(len(TIPOS_C) - 0.5, -0.5)
    ax.tick_params(axis="y", length=0)
    ax.set_xlabel("Tempo médio da consulta na execução (ms, escala log); traço: mediana")
    legenda(figura, arquiteturas)
    figura.tight_layout()
    gravar(figura, "c-consultas", saida)


def figura_mista(mista: Sequence[Linha], saida: str) -> None:
    """Latência da busca sob escrita, relativa à mediana da mesma arquitetura sem escrita."""
    arquiteturas = ("consolidada", "poliglota")
    base = medianas_da_base(validas(mista))
    taxas = sorted({int(l["taxa"]) for l in mista})
    indice = {t: float(i) for i, t in enumerate(taxas)}

    def invalida(linha: Linha) -> bool:
        return numero(linha, "valida") != 1.0

    figura, eixos = plt.subplots(1, 2, figsize=(LARGURA, 2.3), sharey=True)
    vazados = any(invalida(l) for l in mista)
    for ax, p, titulo in zip(list(eixos), ("p50", "p99"),
                             ("(a) Busca híbrida p50", "(b) Busca híbrida p99")):
        series: Dict[str, List[Ponto]] = {}
        for a in arquiteturas:
            referencia = base[(a, p)]
            series[a] = [Ponto(pt.posicao, pt.valor / referencia, pt.repeticao, pt.vazado,
                               pt.na_mediana)
                         for pt in pontos(mista, a, f"busca_hibrida_{p}_ms",
                                          lambda l: indice[int(l["taxa"])], vazado=invalida,
                                          na_mediana=lambda l: not invalida(l))]
        ax.axhline(1.0, color=EIXO, linewidth=0.8, zorder=1)
        fins = desenhar_niveis(ax, series)
        eixo_niveis(ax, list(indice.values()),
                    ["sem escrita" if t == 0 else formato_numero(t) for t in taxas],
                    "Escrita concorrente (métricas/s)", folga_direita=1.3)
        ax.yaxis.set_major_formatter(FuncFormatter(formato_razao))
        ax.set_title(titulo)
        rotulos_diretos(ax, fins)
    eixos[0].set_ylabel("Latência ÷ mediana sem escrita")
    legenda(figura, arquiteturas, "Não sustentou a taxa (fora da mediana)" if vazados else None)
    figura.tight_layout(w_pad=1.5)
    gravar(figura, "m-degradacao", saida)


# --- figuras da longa duração -----------------------------------------------------------------

def pasta_longa(eixo: str, arquitetura: str) -> Optional[str]:
    for nome in sorted(os.listdir(LONGA)):
        partes = nome.split("_")
        if len(partes) > 2 and partes[1] == eixo and partes[2] == arquitetura:
            return os.path.join(LONGA, nome)
        if eixo == "controle" and nome.endswith("_controle_fio"):
            return os.path.join(LONGA, nome)
    return None


def serie_minutos(pasta: str, coluna: str) -> Tuple[List[float], List[float]]:
    horas: List[float] = []
    valores: List[float] = []
    for linha in ler_execucoes(os.path.join(pasta, "minutos.csv")):
        v = numero(linha, coluna)
        if v is not None:
            horas.append(int(linha["minuto"]) / 60)
            valores.append(v)
    return horas, valores


def eixo_horas(ax: Axes, rotulo: bool = True) -> None:
    ax.set_xlim(0, 12)
    ax.set_xticks(range(0, 13, 2))
    if rotulo:
        ax.set_xlabel("Horas de medição")


def figura_longa_b(saida: str) -> None:
    arquiteturas = ("consolidada", "outbox")
    pastas = {a: pasta_longa("b", a) for a in arquiteturas}
    figura, eixos = plt.subplots(3, 1, figsize=(LARGURA, 4.6), sharex=True)
    paineis = (("profundidade", "(a) Profundidade da fila (mensagens)"),
               ("fim_a_fim_p50_ms", "(b) Fim a fim p50 (ms)"),
               ("fim_a_fim_p99_ms", "(c) Fim a fim p99 (ms)"))
    for ax, (coluna, titulo) in zip(list(eixos), paineis):
        for a in arquiteturas:
            pasta = pastas[a]
            if pasta is None:
                continue
            x, y = serie_minutos(pasta, coluna)
            ax.plot(x, y, color=ESTILOS[a].cor, linewidth=0.8)
        eixo_log(ax)
        ax.set_title(titulo)
        eixo_horas(ax, rotulo=ax is eixos[-1])
    legenda(figura, arquiteturas)
    figura.tight_layout(h_pad=0.8)
    gravar(figura, "longa-b", saida)


def figura_longa_consultas(saida: str) -> None:
    """Tempo médio de cada lote periódico de consultas ao longo das 12 h, por tipo."""
    arquiteturas = ("timescaledb", "influxdb")
    lotes: Dict[str, List[Linha]] = {}
    for a in arquiteturas:
        pasta = pasta_longa("c", a)
        lotes[a] = ler_execucoes(os.path.join(pasta, "lotes.csv")) if pasta else []
    colunas = 3
    linhas_grade = math.ceil(len(TIPOS_C) / colunas)
    figura, eixos = plt.subplots(linhas_grade, colunas, figsize=(LARGURA, 7.6), sharex=True)
    planos: List[Axes] = [ax for fila in eixos for ax in fila]
    for ax, tipo in zip(planos, TIPOS_C):
        for a in arquiteturas:
            serie = [(float(l["hora"]), v) for l in lotes[a]
                     if l["tipo"] == tipo and l["periodico"] == "True"
                     and (v := numero(l, "media_ms")) is not None]
            e = ESTILOS[a]
            ax.plot([h for h, _ in serie], [v for _, v in serie], color=e.cor, linewidth=1.0,
                    marker=e.marcador, markersize=2.6, markeredgecolor=FUNDO,
                    markeredgewidth=0.3)
        eixo_log(ax)
        ax.set_title(tipo, fontsize=7, loc="left")
        eixo_horas(ax, rotulo=False)
    for ax in planos[len(TIPOS_C):]:
        ax.set_visible(False)
    for ax in planos[-colunas:]:
        ax.set_xlabel("Horas")
    figura.supylabel("Tempo médio do lote (ms, escala log)", fontsize=7.5, color=TINTA_2)
    legenda(figura, arquiteturas)
    figura.tight_layout(h_pad=0.6, w_pad=0.8)
    gravar(figura, "longa-c-consultas", saida)


def figura_longa_ssd(saida: str) -> None:
    """Escrita e latência por escrita no SSD, minuto a minuto, nas quatro longas e no controle."""
    execucoes = (("c", "timescaledb", "C · TimescaleDB"), ("c", "influxdb", "C · InfluxDB"),
                 ("b", "consolidada", "B · Consolidada"), ("b", "outbox", "B · Outbox"),
                 ("controle", "fio", "Controle com fio"))
    figura = plt.figure(figsize=(LARGURA, 6.6), layout="constrained")
    colunas = cast(Sequence[SubFigure], figura.subfigures(1, 2, wspace=0.04))
    for subfigura, coluna, titulo in zip(colunas,
                                         ("disco_escrita_mib_s", "disco_ms_por_escrita"),
                                         ("(a) Escrita no SSD (MiB/s)",
                                          "(b) Tempo por escrita (ms)")):
        subfigura.suptitle(titulo, x=0.02, ha="left", fontsize=8, color=TINTA)
        eixos: List[Axes] = list(cast(Sequence[Axes], subfigura.subplots(
            len(execucoes), 1, sharex=True, sharey=True)))
        for ax, (eixo, arquitetura, nome) in zip(eixos, execucoes):
            pasta = pasta_longa(eixo, arquitetura)
            if pasta is None:
                continue
            x, y = serie_minutos(pasta, coluna)
            ax.plot(x, y, color=ESTILOS[arquitetura].cor, linewidth=0.7)
            ax.set_ylim(bottom=0)
            ax.set_title(nome, fontsize=7, loc="left", pad=3)
            eixo_horas(ax, rotulo=ax is eixos[-1])
    gravar(figura, "longa-ssd", saida)


def main() -> None:
    analisador = argparse.ArgumentParser(description=__doc__,
                                         formatter_class=argparse.RawDescriptionHelpFormatter)
    analisador.add_argument("--saida", default=SAIDA, help="diretório das figuras")
    saida = str(analisador.parse_args().saida)
    estilo_global()
    curtas = ler_execucoes(os.path.join(PROCESSADOS, "curtas-execucoes.csv"))
    mista = ler_execucoes(os.path.join(PROCESSADOS, "mista-execucoes.csv"))
    figura_a(curtas, saida)
    figura_b_vazao(curtas, saida)
    figura_b_latencia(curtas, saida)
    figura_c_carga(curtas, saida)
    figura_c_consultas(curtas, saida)
    figura_mista(mista, saida)
    figura_longa_b(saida)
    figura_longa_consultas(saida)
    figura_longa_ssd(saida)


if __name__ == "__main__":
    main()
