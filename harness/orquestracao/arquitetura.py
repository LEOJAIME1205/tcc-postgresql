"""Leitura do descritor de cada arquitetura."""

import os
import re
import tomllib
from dataclasses import dataclass
from typing import Any, Dict, List, Set, Tuple, cast

RAIZ: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DADOS_RAIZ: str = os.path.expanduser("~/tcc-dados")  # fora do repositório (ADR-029)
PASTAS_EIXO: Dict[str, str] = {
    "a": "eixo-a-busca",
    "b": "eixo-b-fila",
    "c": "eixo-c-temporal",
    "t": "harness/teste",
}
TIPOS_COLETOR: Set[str] = {"postgres"}
CHAVES_DESCRITOR: Set[str] = {
    "eixo", "arquitetura", "compose", "servicos_sob_teste",
    "servico_gerador", "dados", "coletor",
}
CHAVES_COLETOR: Set[str] = {"tipo", "servico", "banco"}


class ErroDescritor(Exception):
    """Descritor inválido, com a explicação do problema."""


@dataclass(frozen=True)
class Coletor:
    tipo: str
    servico: str
    banco: str


@dataclass(frozen=True)
class Arquitetura:
    eixo: str
    arquitetura: str
    pasta: str
    compose: str
    servicos_sob_teste: Tuple[str, ...]
    servico_gerador: str
    dados: Tuple[Tuple[str, str], ...]  # (variável de ambiente, subdiretório)
    coletores: Tuple[Coletor, ...]

    def diretorio_dados(self, subdiretorio: str) -> str:
        """~/tcc-dados/<eixo>/<arquitetura>/<subdiretório>, com inicial/ e atual/ dentro."""
        return os.path.join(DADOS_RAIZ, self.eixo, self.arquitetura, subdiretorio)

    def ambiente(self) -> Dict[str, str]:
        """Variáveis de dados para todo comando do Compose, apontando para atual/."""
        return {variavel: os.path.join(self.diretorio_dados(sub), "atual")
                for variavel, sub in self.dados}


def _sem_chaves_desconhecidas(dados: Dict[str, Any], permitidas: Set[str], onde: str) -> None:
    desconhecidas = sorted(set(dados) - permitidas)
    if desconhecidas:
        raise ErroDescritor(f"{onde}: chave(s) desconhecida(s): {', '.join(desconhecidas)}")


def _texto(dados: Dict[str, Any], chave: str, onde: str) -> str:
    valor = dados.get(chave)
    if not isinstance(valor, str) or not valor:
        raise ErroDescritor(f"{onde}: '{chave}' precisa ser um texto não vazio")
    return valor


def _textos(dados: Dict[str, Any], chave: str, onde: str) -> List[str]:
    valor = dados.get(chave)
    if not isinstance(valor, list) or not valor:
        raise ErroDescritor(f"{onde}: '{chave}' precisa ser uma lista não vazia de textos")
    itens = cast(List[Any], valor)
    if not all(isinstance(t, str) and t for t in itens):
        raise ErroDescritor(f"{onde}: '{chave}' só aceita textos não vazios")
    textos = [str(t) for t in itens]
    if len(set(textos)) != len(textos):
        raise ErroDescritor(f"{onde}: '{chave}' tem valores repetidos")
    return textos


def _dados(dados: Dict[str, Any], onde: str) -> Tuple[Tuple[str, str], ...]:
    tabela = dados.get("dados")
    if not isinstance(tabela, dict) or not tabela:
        raise ErroDescritor(f"{onde}: a tabela [dados] precisa ter pelo menos uma variável")
    pares: List[Tuple[str, str]] = []
    for variavel, subdiretorio in cast(Dict[str, Any], tabela).items():
        if not re.fullmatch(r"TCC_DADOS_[A-Z0-9_]+", variavel):
            raise ErroDescritor(f"{onde}: variável '{variavel}' fora do padrão TCC_DADOS_<NOME>")
        if not isinstance(subdiretorio, str) or not re.fullmatch(r"[a-z0-9_-]+", subdiretorio):
            raise ErroDescritor(f"{onde}: o subdiretório de '{variavel}' aceita só minúsculas, "
                                "dígitos, '_' e '-'")
        pares.append((variavel, subdiretorio))
    return tuple(pares)


def _coletores(dados: Dict[str, Any], servicos: List[str], onde: str) -> Tuple[Coletor, ...]:
    tabelas = dados.get("coletor", [])
    if not isinstance(tabelas, list):
        raise ErroDescritor(f"{onde}: 'coletor' precisa ser uma lista de tabelas [[coletor]]")
    coletores: List[Coletor] = []
    for i, item in enumerate(cast(List[Any], tabelas), start=1):
        local = f"{onde}, coletor {i}"
        if not isinstance(item, dict):
            raise ErroDescritor(f"{local}: precisa ser uma tabela [[coletor]]")
        tabela = cast(Dict[str, Any], item)
        _sem_chaves_desconhecidas(tabela, CHAVES_COLETOR, local)
        tipo = _texto(tabela, "tipo", local)
        if tipo not in TIPOS_COLETOR:
            raise ErroDescritor(f"{local}: tipo '{tipo}' desconhecido; use {', '.join(sorted(TIPOS_COLETOR))}")
        servico = _texto(tabela, "servico", local)
        if servico not in servicos:
            raise ErroDescritor(f"{local}: o serviço '{servico}' não está entre os serviços sob teste")
        coletores.append(Coletor(tipo, servico, _texto(tabela, "banco", local)))
    return tuple(coletores)


def ler_arquitetura(eixo: str, arquitetura: str) -> Arquitetura:
    """Lê e valida o descritor; levanta ErroDescritor com a explicação do problema."""
    if eixo not in PASTAS_EIXO:
        raise ErroDescritor(f"eixo '{eixo}' sem pasta conhecida; use {', '.join(PASTAS_EIXO)}")
    pasta = os.path.join(RAIZ, PASTAS_EIXO[eixo], arquitetura)
    caminho = os.path.join(pasta, "arquitetura.toml")
    try:
        with open(caminho, "rb") as f:
            dados = tomllib.load(f)
    except tomllib.TOMLDecodeError as erro:
        raise ErroDescritor(f"{caminho}: TOML inválido: {erro}") from erro
    except OSError as erro:
        raise ErroDescritor(f"{caminho}: não foi possível ler o arquivo: {erro.strerror}") from erro

    onde = os.path.relpath(caminho, RAIZ)
    _sem_chaves_desconhecidas(dados, CHAVES_DESCRITOR, onde)
    if _texto(dados, "eixo", onde) != eixo or _texto(dados, "arquitetura", onde) != arquitetura:
        raise ErroDescritor(f"{onde}: 'eixo' e 'arquitetura' precisam ser '{eixo}' e "
                            f"'{arquitetura}', como a pasta em que o descritor está")

    compose = os.path.join(pasta, _texto(dados, "compose", onde))
    if not os.path.isfile(compose):
        raise ErroDescritor(f"{onde}: arquivo do Compose não encontrado: {os.path.relpath(compose, RAIZ)}")

    servicos = _textos(dados, "servicos_sob_teste", onde)
    gerador = _texto(dados, "servico_gerador", onde)
    if gerador in servicos:
        raise ErroDescritor(f"{onde}: o gerador '{gerador}' não pode estar entre os serviços sob teste")

    return Arquitetura(eixo, arquitetura, pasta, compose, tuple(servicos), gerador,
                       _dados(dados, onde), _coletores(dados, servicos, onde))
