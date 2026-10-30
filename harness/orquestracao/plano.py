"""Leitura do plano de uma janela e sorteio da ordem das execuções."""

import random
import re
import tomllib
from dataclasses import dataclass
from typing import Any, Dict, List, Set, Tuple, cast

REPOUSO_S: int = 1200  # repouso antes de cada execução (ADR-004)
ARQUITETURAS: Dict[str, Tuple[str, ...]] = {
    "a": ("consolidada", "poliglota"),
    "b": ("consolidada", "ingenua", "outbox"),
    "c": ("timescaledb", "influxdb"),
    "t": ("pgbench",),
}
CENARIOS: Tuple[str, ...] = ("calibracao", "curta", "mista", "longa", "qualidade")
CHAVES_PLANO: Set[str] = {"nome", "semente", "espaco_livre_min_gib", "configuracao"}
CHAVES_CONFIGURACAO: Set[str] = {
    "eixo", "arquitetura", "cenario", "concorrencias",
    "repeticoes", "aquecimento_s", "medicao_s",
}


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


@dataclass(frozen=True)
class Execucao:
    configuracao: Configuracao
    repeticao: int
    ordem: int

    def sufixo(self) -> str:
        """Parte final do identificador da execução (harness/README.md)."""
        c = self.configuracao
        return f"{c.eixo}_{c.arquitetura}_{c.cenario}_c{c.concorrencia}_r{self.repeticao:02d}"


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


def _configuracoes(item: Dict[str, Any], onde: str) -> List[Configuracao]:
    """Valida uma tabela [[configuracao]] e a expande, uma por concorrência."""
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
    return [Configuracao(eixo, arquitetura, cenario, n, repeticoes, aquecimento_s, medicao_s)
            for n in _concorrencias(item, onde)]


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
    vistas: Set[Tuple[str, str, str, int]] = set()
    for i, item in enumerate(cast(List[Any], tabelas), start=1):
        onde = f"configuracao {i}"
        if not isinstance(item, dict):
            raise ErroPlano(f"{onde}: precisa ser uma tabela [[configuracao]]")
        for c in _configuracoes(cast(Dict[str, Any], item), onde):
            chave = (c.eixo, c.arquitetura, c.cenario, c.concorrencia)
            if chave in vistas:
                raise ErroPlano(f"{onde}: {c.eixo}/{c.arquitetura}/{c.cenario} com concorrência "
                                f"{c.concorrencia} aparece mais de uma vez no plano")
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
