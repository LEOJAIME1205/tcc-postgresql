"""Leitura do plano de uma janela e sorteio da ordem das execuções."""

import random
import re
import tomllib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple, cast

REPOUSO_S: int = 1200  # repouso antes de cada execução (ADR-004)
ARQUITETURAS: Dict[str, Tuple[str, ...]] = {
    "a": ("consolidada", "poliglota"),
    "b": ("consolidada", "ingenua", "outbox"),
    "c": ("timescaledb", "influxdb"),
    "m": ("consolidada", "poliglota"),  # carga mista: busca sob ingestão (ADR-068)
    "t": ("pgbench",),
}
CENARIOS: Tuple[str, ...] = ("calibracao", "curta", "mista", "longa", "qualidade")
CHAVES_PLANO: Set[str] = {"nome", "semente", "espaco_livre_min_gib", "configuracao"}
CHAVES_CONFIGURACAO: Set[str] = {
    "eixo", "arquitetura", "cenario", "concorrencias",
    "repeticoes", "aquecimento_s", "medicao_s", "coleta_interna", "espera", "taxas",
}
# Modo de espera dos consumidores (ADR-051); só a fila consolidada do Eixo B o aceita no
# plano, para a medida do teto do aviso (ADR-067). Sem a chave, vale o da composição.
ESPERAS: Tuple[str, ...] = ("consulta", "aviso")
ACEITAM_ESPERA: Set[Tuple[str, str]] = {("b", "consolidada")}
# Taxa fixa de escrita. No Eixo B, mensagens/s dos produtores, para a latência fim a fim
# (ADR-054, revisão de 10/11/2026). Na carga mista, métricas/s da ingestão que concorre com a
# busca (ADR-068). No Eixo C, métricas/s da ingestão contínua, só no cenário longa (ADR-069).
# Sem a chave, os produtores ficam em laço fechado (B), não há ingestão (m) ou a carga é a do
# arquivo inteiro (c).
ACEITAM_TAXA: Set[str] = {"b", "m", "c"}
TAXA_SO_NA_LONGA: Set[str] = {"c"}
UNIDADE_TAXA: Dict[str, str] = {"b": "mensagens/s", "m": "métricas/s", "c": "métricas/s"}


class ErroPlano(Exception):
    """Plano inválido, com a explicação do problema."""


@dataclass(frozen=True)
class Configuracao:
    eixo: str
    arquitetura: str
    cenario: str
    concorrencia: int
    repeticoes: int
    aquecimento_s: int
    medicao_s: int
    coleta_interna: bool = True  # False só na medida do custo da coleta (ADR-066)
    espera: Optional[str] = None  # "aviso" só na medida do teto do aviso (ADR-067)
    taxa: Optional[int] = None  # taxa fixa de escrita: B, m e c na longa (ADR-054, 068 e 069)


@dataclass(frozen=True)
class Execucao:
    configuracao: Configuracao
    repeticao: int
    ordem: int

    def sufixo(self) -> str:
        """Parte final do identificador da execução (harness/README.md)."""
        c = self.configuracao
        taxa = f"_t{c.taxa}" if c.taxa is not None else ""
        aviso = "_aviso" if c.espera == "aviso" else ""
        sem_coleta = "" if c.coleta_interna else "_semcoleta"
        return (f"{c.eixo}_{c.arquitetura}_{c.cenario}_c{c.concorrencia}_r{self.repeticao:02d}"
                f"{taxa}{aviso}{sem_coleta}")


@dataclass(frozen=True)
class Plano:
    nome: str
    semente: int
    espaco_livre_min_gib: float
    configuracoes: Tuple[Configuracao, ...]


def _sem_chaves_desconhecidas(dados: Dict[str, Any], permitidas: Set[str], onde: str) -> None:
    desconhecidas = sorted(set(dados) - permitidas)
    if desconhecidas:
        raise ErroPlano(f"{onde}: chave(s) desconhecida(s): {', '.join(desconhecidas)}")


def _texto(dados: Dict[str, Any], chave: str, onde: str) -> str:
    valor = dados.get(chave)
    if not isinstance(valor, str) or not valor:
        raise ErroPlano(f"{onde}: '{chave}' precisa ser um texto não vazio")
    return valor


def _inteiro(dados: Dict[str, Any], chave: str, onde: str, minimo: int) -> int:
    valor = dados.get(chave)
    if isinstance(valor, bool) or not isinstance(valor, int) or valor < minimo:
        raise ErroPlano(f"{onde}: '{chave}' precisa ser um inteiro maior ou igual a {minimo}")
    return valor


def _numero_positivo(dados: Dict[str, Any], chave: str, onde: str) -> float:
    valor = dados.get(chave)
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or valor <= 0:
        raise ErroPlano(f"{onde}: '{chave}' precisa ser um número positivo")
    return float(valor)


def _concorrencias(dados: Dict[str, Any], onde: str) -> List[int]:
    valor = dados.get("concorrencias")
    if not isinstance(valor, list) or not valor:
        raise ErroPlano(f"{onde}: 'concorrencias' precisa ser uma lista não vazia")
    itens = cast(List[Any], valor)
    if any(isinstance(n, bool) or not isinstance(n, int) or n < 1 for n in itens):
        raise ErroPlano(f"{onde}: 'concorrencias' só aceita inteiros maiores ou iguais a 1")
    numeros = [int(n) for n in itens]
    if len(set(numeros)) != len(numeros):
        raise ErroPlano(f"{onde}: 'concorrencias' tem valores repetidos")
    return numeros


def _taxas(dados: Dict[str, Any], onde: str) -> List[Optional[int]]:
    """Taxas fixas da configuração; [None] sem a chave (laço fechado)."""
    if "taxas" not in dados:
        return [None]
    valor = dados.get("taxas")
    if not isinstance(valor, list) or not valor:
        raise ErroPlano(f"{onde}: 'taxas' precisa ser uma lista não vazia")
    itens = cast(List[Any], valor)
    if any(isinstance(t, bool) or not isinstance(t, int) or t < 1 for t in itens):
        raise ErroPlano(f"{onde}: 'taxas' só aceita inteiros maiores ou iguais a 1")
    taxas = [int(t) for t in itens]
    if len(set(taxas)) != len(taxas):
        raise ErroPlano(f"{onde}: 'taxas' tem valores repetidos")
    return [t for t in taxas]


def _configuracoes(item: Dict[str, Any], onde: str) -> List[Configuracao]:
    """Valida uma tabela [[configuracao]] e a expande, uma por concorrência e taxa."""
    _sem_chaves_desconhecidas(item, CHAVES_CONFIGURACAO, onde)
    eixo = _texto(item, "eixo", onde)
    if eixo not in ARQUITETURAS:
        raise ErroPlano(f"{onde}: eixo '{eixo}' desconhecido; use {', '.join(ARQUITETURAS)}")
    arquitetura = _texto(item, "arquitetura", onde)
    if arquitetura not in ARQUITETURAS[eixo]:
        raise ErroPlano(f"{onde}: o eixo {eixo} não tem a arquitetura '{arquitetura}'; "
                        f"use {', '.join(ARQUITETURAS[eixo])}")
    cenario = _texto(item, "cenario", onde)
    if cenario not in CENARIOS:
        raise ErroPlano(f"{onde}: cenário '{cenario}' desconhecido; use {', '.join(CENARIOS)}")
    repeticoes = _inteiro(item, "repeticoes", onde, 1)
    aquecimento_s = _inteiro(item, "aquecimento_s", onde, 0)
    medicao_s = _inteiro(item, "medicao_s", onde, 1)
    coleta_interna = item.get("coleta_interna", True)
    if not isinstance(coleta_interna, bool):
        raise ErroPlano(f"{onde}: 'coleta_interna' precisa ser true ou false")
    espera: Optional[str] = None
    if "espera" in item:
        espera = _texto(item, "espera", onde)
        if espera not in ESPERAS:
            raise ErroPlano(f"{onde}: espera '{espera}' desconhecida; use {', '.join(ESPERAS)}")
        if (eixo, arquitetura) not in ACEITAM_ESPERA:
            raise ErroPlano(f"{onde}: 'espera' só vale na fila consolidada do Eixo B (ADR-067)")
    taxas = _taxas(item, onde)
    if taxas != [None] and eixo not in ACEITAM_TAXA:
        raise ErroPlano(f"{onde}: 'taxas' só vale nos eixos {', '.join(sorted(ACEITAM_TAXA))} "
                        "(ADR-054, ADR-068 e ADR-069)")
    if taxas != [None] and eixo in TAXA_SO_NA_LONGA and cenario != "longa":
        raise ErroPlano(f"{onde}: no eixo {eixo}, 'taxas' só vale no cenário longa (ADR-069)")
    return [Configuracao(eixo, arquitetura, cenario, n, repeticoes, aquecimento_s, medicao_s,
                         coleta_interna, espera, taxa)
            for n in _concorrencias(item, onde) for taxa in taxas]


def ler_plano(caminho: str) -> Plano:
    """Lê e valida o plano; levanta ErroPlano com a explicação do problema."""
    try:
        with open(caminho, "rb") as f:
            dados = tomllib.load(f)
    except tomllib.TOMLDecodeError as erro:
        raise ErroPlano(f"{caminho}: TOML inválido: {erro}") from erro
    except OSError as erro:
        raise ErroPlano(f"{caminho}: não foi possível ler o arquivo: {erro.strerror}") from erro


    _sem_chaves_desconhecidas(dados, CHAVES_PLANO, "plano")
    nome = _texto(dados, "nome", "plano")
    if not re.fullmatch(r"[a-z0-9_-]+", nome):
        raise ErroPlano("plano: 'nome' aceita só letras minúsculas sem acento, dígitos, '_' e '-'")
    semente = _inteiro(dados, "semente", "plano", 0)
    espaco = _numero_positivo(dados, "espaco_livre_min_gib", "plano")

    tabelas = dados.get("configuracao")
    if not isinstance(tabelas, list) or not tabelas:
        raise ErroPlano("plano: é preciso pelo menos uma tabela [[configuracao]]")

    configuracoes: List[Configuracao] = []
    vistas: Set[Tuple[str, str, str, int, bool, str, int]] = set()
    for i, item in enumerate(cast(List[Any], tabelas), start=1):
        onde = f"configuracao {i}"
        if not isinstance(item, dict):
            raise ErroPlano(f"{onde}: precisa ser uma tabela [[configuracao]]")
        for c in _configuracoes(cast(Dict[str, Any], item), onde):
            # sem a chave, a composição usa consulta: as duas formas são a mesma configuração
            espera = c.espera or "consulta"
            taxa = c.taxa if c.taxa is not None else 0  # 0: laço fechado
            chave = (c.eixo, c.arquitetura, c.cenario, c.concorrencia, c.coleta_interna, espera, taxa)
            if chave in vistas:
                coleta = "" if c.coleta_interna else " sem coleta interna"
                aviso = " com espera por aviso" if espera == "aviso" else ""
                por_taxa = f" e taxa {c.taxa}/s" if c.taxa is not None else ""
                raise ErroPlano(f"{onde}: {c.eixo}/{c.arquitetura}/{c.cenario} com concorrência "
                                f"{c.concorrencia}{por_taxa}{aviso}{coleta} aparece mais de uma "
                                f"vez no plano")
            vistas.add(chave)
            configuracoes.append(c)

    cenarios = {c.cenario for c in configuracoes}
    if "longa" in cenarios and cenarios != {"longa"}:
        raise ErroPlano("plano: execuções do cenário 'longa' ocupam janelas próprias (ADR-032)")

    return Plano(nome, semente, espaco, tuple(configuracoes))


def sortear(plano: Plano) -> List[Execucao]:
    """Ordem das execuções em blocos (ADR-032), reproduzível pela semente."""
    gerador = random.Random(plano.semente)
    execucoes: List[Execucao] = []
    total_blocos = max(c.repeticoes for c in plano.configuracoes)
    for repeticao in range(1, total_blocos + 1):
        bloco = [c for c in plano.configuracoes if c.repeticoes >= repeticao]
        gerador.shuffle(bloco)
        for c in bloco:
            execucoes.append(Execucao(c, repeticao, len(execucoes) + 1))
    return execucoes


def duracao_minima_s(execucoes: List[Execucao]) -> int:
    """Repouso, aquecimento e medição somados; sem restauração, subida e desligamento."""
    return sum(REPOUSO_S + e.configuracao.aquecimento_s + e.configuracao.medicao_s
               for e in execucoes)
