"""Estatística das execuções repetidas (ADR-067, item 7; ADR-068, item 6): Mann-Whitney exato e
bilateral, correção de Holm, Â12 de Vargha-Delaney e razão das medianas.

Sem dependências novas. A distribuição exata do teste é contada por programação dinâmica sobre os
postos, o que vale com empates e com 20 contra 20 (consultas do Eixo C), em que enumerar as
C(40, 20) ≈ 1,4 × 10¹¹ divisões não cabe. Com empates, a distribuição é a da permutação condicionada
aos postos médios observados. O p bilateral é o dobro da menor cauda, limitado a 1.
"""

import math
import statistics
from dataclasses import dataclass
from typing import Dict, List, Sequence

ALFA: float = 0.05


def _conferir(grupo: Sequence[float], nome: str) -> None:
    if not grupo:
        raise ValueError(f"grupo {nome} vazio")
    if any(math.isnan(v) for v in grupo):
        raise ValueError(f"grupo {nome} tem NaN")


def postos_dobrados(valores: Sequence[float]) -> List[int]:
    """Posto de cada valor na amostra, multiplicado por 2 para ficar inteiro. Valores empatados
    recebem a média dos postos que ocupariam: 10, 20, 20, 30 têm postos 1; 2,5; 2,5; 4, e
    postos dobrados 2, 5, 5, 8."""
    ordem = sorted(range(len(valores)), key=lambda i: valores[i])
    postos = [0] * len(valores)
    i = 0
    while i < len(ordem):
        j = i
        while j + 1 < len(ordem) and valores[ordem[j + 1]] == valores[ordem[i]]:
            j += 1
        # As posições i..j ocupam os postos i + 1 a j + 1, cuja média dobrada é i + j + 2.
        for k in range(i, j + 1):
            postos[ordem[k]] = i + j + 2
        i = j + 1
    return postos


def contagens_da_soma(postos: Sequence[int], n1: int) -> Dict[int, int]:
    """Para cada soma possível, quantas das C(N, n1) escolhas de n1 postos a produzem. Sob a
    hipótese nula, toda divisão das N execuções em dois grupos é igualmente provável, e essa é
    a distribuição exata da soma dos postos do primeiro grupo."""
    por_tamanho: List[Dict[int, int]] = [{0: 1}] + [{} for _ in range(n1)]
    for posto in postos:
        # De trás para frente, para cada posto entrar no máximo uma vez em cada escolha.
        for k in range(n1, 0, -1):
            destino = por_tamanho[k]
            for soma, n in por_tamanho[k - 1].items():
                destino[soma + posto] = destino.get(soma + posto, 0) + n
    return por_tamanho[n1]


@dataclass(frozen=True)
class MannWhitney:
    """u: pares (x de a, y de b) com x > y, empate valendo meio; vai de 0 a n1 × n2.
    p: bilateral exato."""
    u: float
    p: float


def mann_whitney_exato(a: Sequence[float], b: Sequence[float]) -> MannWhitney:
    """Mann-Whitney exato e bilateral entre dois grupos de execuções."""
    _conferir(a, "a")
    _conferir(b, "b")
    n1, n2 = len(a), len(b)
    postos = postos_dobrados([*a, *b])
    soma = sum(postos[:n1])
    contagens = contagens_da_soma(postos, n1)
    total = math.comb(n1 + n2, n1)
    if sum(contagens.values()) != total:
        raise AssertionError("a distribuição exata não soma C(N, n1)")
    cauda_baixa = sum(n for s, n in contagens.items() if s <= soma)
    cauda_alta = sum(n for s, n in contagens.items() if s >= soma)
    p = min(1.0, 2 * min(cauda_baixa, cauda_alta) / total)
    u = soma / 2 - n1 * (n1 + 1) / 2
    return MannWhitney(u=u, p=p)


def menor_p_possivel(n1: int, n2: int) -> float:
    """Menor p bilateral que o teste exato alcança sem empates: os dois arranjos extremos
    entre C(n1 + n2, n1). Com 5 contra 5, 2/252 ≈ 0,0079."""
    return min(1.0, 2 / math.comb(n1 + n2, n1))


def a12(a: Sequence[float], b: Sequence[float]) -> float:
    """Â12 de Vargha-Delaney: probabilidade de uma execução de a ter valor maior que uma de b,
    com empate valendo meio. 0,5 é nenhuma tendência; 1 é todo valor de a acima de todo valor
    de b; 0, todo abaixo. É o u do Mann-Whitney dividido por n1 × n2."""
    _conferir(a, "a")
    _conferir(b, "b")
    pontos = sum(1.0 if x > y else 0.5 if x == y else 0.0 for x in a for y in b)
    return pontos / (len(a) * len(b))


def razao_das_medianas(a: Sequence[float], b: Sequence[float]) -> float:
    """Mediana de a dividida pela mediana de b: o tamanho da diferença na unidade da métrica."""
    _conferir(a, "a")
    _conferir(b, "b")
    mediana_b = statistics.median(b)
    if mediana_b == 0:
        raise ValueError("a mediana de b é zero")
    return statistics.median(a) / mediana_b


def holm(pvalores: Sequence[float]) -> List[float]:
    """p ajustados de Holm, na ordem de entrada. Uma comparação é significativa na família
    quando o p ajustado dela é no máximo ALFA. Equivale a comparar o menor p com ALFA/m, o
    segundo com ALFA/(m − 1), e assim por diante, parando na primeira que não passar."""
    m = len(pvalores)
    ordem = sorted(range(m), key=lambda i: pvalores[i])
    ajustados = [0.0] * m
    maior = 0.0
    for posicao, i in enumerate(ordem):
        maior = max(maior, min(1.0, (m - posicao) * pvalores[i]))
        ajustados[i] = maior
    return ajustados


@dataclass(frozen=True)
class Comparacao:
    """Uma comparação entre duas arquiteturas numa métrica, com o tamanho de efeito."""
    n1: int
    n2: int
    mediana1: float
    mediana2: float
    u: float
    p: float
    a12: float
    razao_medianas: float


def comparar(a: Sequence[float], b: Sequence[float]) -> Comparacao:
    """Teste e tamanho de efeito de a contra b. O p ainda não tem a correção de Holm, que
    depende da família de comparações (holm)."""
    teste = mann_whitney_exato(a, b)
    return Comparacao(n1=len(a), n2=len(b), mediana1=statistics.median(a),
                      mediana2=statistics.median(b), u=teste.u, p=teste.p, a12=a12(a, b),
                      razao_medianas=razao_das_medianas(a, b))
