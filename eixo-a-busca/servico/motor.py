"""Contrato comum dos motores e a fusão por RRF (ADR-039 e ADR-046)."""

from typing import Dict, List, Protocol, Sequence, Tuple

DIMENSAO: int = 384
K_RRF: int = 60
POR_RAMO: int = 100
RESPOSTA: int = 10

Resultado = List[Tuple[int, float]]


class Motor(Protocol):
    async def abrir(self) -> None: ...

    async def fechar(self) -> None: ...

    async def pronto(self) -> bool: ...

    async def buscar(self, texto: str, vetor: List[float]) -> Resultado: ...


def fundir(lexico: Sequence[int], vetorial: Sequence[int]) -> Resultado:
    """Soma de 1/(K_RRF + posição) nos dois ramos; empates pelo menor id.

    A conta é a mesma da consulta SQL da consolidada, na mesma ordem: léxico mais vetorial.
    """
    pontos: Dict[int, float] = {}
    for posicao, i in enumerate(lexico, start=1):
        pontos[i] = 1.0 / (K_RRF + posicao)
    for posicao, i in enumerate(vetorial, start=1):
        pontos[i] = pontos.get(i, 0.0) + 1.0 / (K_RRF + posicao)
    return sorted(pontos.items(), key=lambda par: (-par[1], par[0]))[:RESPOSTA]
