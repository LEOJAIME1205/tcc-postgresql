"""Testes de harness/analise/estatistica.py.

    uv run python -m unittest harness.analise.teste_estatistica -v
"""

import itertools
import math
import random
import unittest
from typing import List, Sequence

from harness.analise.estatistica import (a12, comparar, contagens_da_soma, holm,
                                         mann_whitney_exato, menor_p_possivel, postos_dobrados,
                                         razao_das_medianas)


def u_por_pares(a: Sequence[float], b: Sequence[float]) -> float:
    """u contado direto pelos pares, sem postos."""
    return sum(1.0 if x > y else 0.5 if x == y else 0.0 for x in a for y in b)


def p_por_enumeracao(a: Sequence[float], b: Sequence[float]) -> float:
    """p bilateral enumerando todas as divisões das execuções em dois grupos. Referência
    independente da programação dinâmica: usa o u por pares, que é função crescente da soma
    dos postos para n1 fixo."""
    todos: List[float] = [*a, *b]
    n1 = len(a)
    observado = u_por_pares(a, b)
    baixa = alta = total = 0
    for escolha in itertools.combinations(range(len(todos)), n1):
        dentro = set(escolha)
        g1 = [todos[i] for i in escolha]
        g2 = [todos[i] for i in range(len(todos)) if i not in dentro]
        u = u_por_pares(g1, g2)
        total += 1
        baixa += int(u <= observado)
        alta += int(u >= observado)
    return min(1.0, 2 * min(baixa, alta) / total)


class TestePostos(unittest.TestCase):
    def test_empates_recebem_posto_medio(self) -> None:
        self.assertEqual(postos_dobrados([10, 20, 20, 30]), [2, 5, 5, 8])

    def test_ordem_de_entrada_preservada(self) -> None:
        self.assertEqual(postos_dobrados([30, 10, 20]), [6, 2, 4])

    def test_contagens_somam_o_total_de_divisoes(self) -> None:
        postos = postos_dobrados([1, 2, 2, 3, 3, 3, 4, 5])
        self.assertEqual(sum(contagens_da_soma(postos, 3).values()), math.comb(8, 3))


class TesteMannWhitney(unittest.TestCase):
    def test_separacao_completa_5_contra_5(self) -> None:
        menor = mann_whitney_exato([1, 2, 3, 4, 5], [6, 7, 8, 9, 10])
        maior = mann_whitney_exato([6, 7, 8, 9, 10], [1, 2, 3, 4, 5])
        self.assertEqual(menor.u, 0)
        self.assertEqual(maior.u, 25)
        self.assertAlmostEqual(menor.p, 2 / 252)
        self.assertAlmostEqual(maior.p, 2 / 252)

    def test_valor_de_tabela_u_igual_a_2(self) -> None:
        # Com 5 contra 5, P(U ≤ 2) = 4/252; o bilateral é 8/252 ≈ 0,0317.
        r = mann_whitney_exato([1, 2, 3, 5, 6], [4, 7, 8, 9, 10])
        self.assertEqual(r.u, 2)
        self.assertAlmostEqual(r.p, 8 / 252)

    def test_grupos_iguais(self) -> None:
        r = mann_whitney_exato([3, 3, 3], [3, 3, 3, 3])
        self.assertEqual(r.u, 6)
        self.assertEqual(r.p, 1.0)

    def test_igual_a_enumeracao_com_empates(self) -> None:
        sorteio = random.Random(20261119)
        for n1, n2 in [(3, 4), (5, 5), (4, 6), (2, 7), (6, 6)]:
            for _ in range(40):
                a = [float(sorteio.randint(0, 6)) for _ in range(n1)]
                b = [float(sorteio.randint(0, 6)) for _ in range(n2)]
                with self.subTest(a=a, b=b):
                    r = mann_whitney_exato(a, b)
                    self.assertEqual(r.u, u_por_pares(a, b))
                    self.assertAlmostEqual(r.p, p_por_enumeracao(a, b), places=12)

    def test_20_contra_20(self) -> None:
        r = mann_whitney_exato(list(range(20)), list(range(20, 40)))
        self.assertEqual(r.u, 0)
        self.assertAlmostEqual(r.p, 2 / math.comb(40, 20))

    def test_grupo_vazio_ou_nan(self) -> None:
        with self.assertRaises(ValueError):
            mann_whitney_exato([], [1.0])
        with self.assertRaises(ValueError):
            mann_whitney_exato([1.0, float("nan")], [2.0])

    def test_menor_p_possivel(self) -> None:
        self.assertAlmostEqual(menor_p_possivel(5, 5), 2 / 252)
        self.assertAlmostEqual(menor_p_possivel(4, 5), 2 / 126)
        self.assertAlmostEqual(menor_p_possivel(4, 4), 2 / 70)


class TesteEfeito(unittest.TestCase):
    def test_a12_com_empate(self) -> None:
        # Pares: 1<2, 1<3, 2=2, 2<3 → nenhum maior e um empate em 4 pares.
        self.assertAlmostEqual(a12([1, 2], [2, 3]), 0.125)

    def test_a12_extremos(self) -> None:
        self.assertEqual(a12([6, 7], [1, 2]), 1.0)
        self.assertEqual(a12([1, 2], [6, 7]), 0.0)
        self.assertEqual(a12([4, 4], [4, 4]), 0.5)

    def test_a12_e_u_normalizado(self) -> None:
        sorteio = random.Random(7)
        for _ in range(50):
            a = [float(sorteio.randint(0, 9)) for _ in range(5)]
            b = [float(sorteio.randint(0, 9)) for _ in range(5)]
            self.assertAlmostEqual(a12(a, b), mann_whitney_exato(a, b).u / 25)

    def test_razao_das_medianas(self) -> None:
        self.assertAlmostEqual(razao_das_medianas([1, 2, 3], [2, 4, 6]), 0.5)
        with self.assertRaises(ValueError):
            razao_das_medianas([1, 2, 3], [0, 0, 1])

    def test_comparar(self) -> None:
        c = comparar([4.1, 3.9, 4.0, 4.5, 4.2], [2.1, 2.3, 2.2, 2.4, 2.0])
        self.assertEqual((c.n1, c.n2), (5, 5))
        self.assertAlmostEqual(c.mediana1, 4.1)
        self.assertAlmostEqual(c.mediana2, 2.2)
        self.assertEqual(c.a12, 1.0)
        self.assertAlmostEqual(c.p, 2 / 252)
        self.assertAlmostEqual(c.razao_medianas, 4.1 / 2.2)


class TesteHolm(unittest.TestCase):
    def test_exemplo(self) -> None:
        # Ordenados: 0,005 × 4 = 0,02; 0,01 × 3 = 0,03; 0,03 × 2 = 0,06; 0,04 × 1 = 0,04 → 0,06.
        ajustados = holm([0.01, 0.04, 0.03, 0.005])
        for obtido, esperado in zip(ajustados, [0.03, 0.06, 0.06, 0.02]):
            self.assertAlmostEqual(obtido, esperado)

    def test_limite_em_1(self) -> None:
        self.assertEqual(holm([0.6, 0.7]), [1.0, 1.0])

    def test_5_contra_5_passa_com_4_comparacoes(self) -> None:
        # O menor p de 5 contra 5 sobrevive à correção com 4 comparações (ADR-067, item 7).
        self.assertLessEqual(holm([2 / 252, 0.5, 0.6, 0.7])[0], 0.05)

    def test_vazio(self) -> None:
        self.assertEqual(holm([]), [])


if __name__ == "__main__":
    unittest.main()
