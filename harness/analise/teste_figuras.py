"""Testes das partes de harness/analise/figuras.py que não dependem do desenho.

    uv run --group analise python -m unittest harness.analise.teste_figuras -v
"""

import unittest
from typing import List

from harness.analise.comum import Linha
from harness.analise.figuras import (PASSO_ARQUITETURA, PASSO_REPETICAO, Ponto, deslocamento,
                                     formato_numero, medianas, pontos, subdivisoes_log)


class TesteFormatos(unittest.TestCase):
    def test_numeros(self) -> None:
        self.assertEqual(formato_numero(0), "0")
        self.assertEqual(formato_numero(0.5), "0,5")
        self.assertEqual(formato_numero(5), "5")
        self.assertEqual(formato_numero(1400), "1,4 mil")
        self.assertEqual(formato_numero(15000), "15 mil")
        self.assertEqual(formato_numero(2_500_000), "2,5 mi")

    def test_subdivisoes_conforme_a_faixa(self) -> None:
        self.assertEqual(subdivisoes_log(1, 10_000), (1.0,))
        self.assertEqual(subdivisoes_log(2, 40), (1.0, 2.0, 5.0))
        self.assertEqual(subdivisoes_log(4.5, 9), (1.0, 1.5, 2.0, 3.0, 5.0, 7.0))


class TestePontos(unittest.TestCase):
    def test_mediana_sem_os_pontos_fora(self) -> None:
        linhas: List[Linha] = [
            {"arquitetura": "poliglota", "repeticao": str(r), "nivel": "1", "v": str(v),
             "valida": "False" if r == 5 else "True"}
            for r, v in zip(range(1, 6), (10, 20, 30, 40, 1000))
        ]
        linhas.append({"arquitetura": "consolidada", "repeticao": "1", "nivel": "1", "v": "7",
                       "valida": "True"})
        linhas.append({"arquitetura": "poliglota", "repeticao": "6", "nivel": "1", "v": "",
                       "valida": "True"})
        serie = pontos(linhas, "poliglota", "v", lambda _l: 0.0,
                       vazado=lambda l: l["valida"] != "True",
                       na_mediana=lambda l: l["valida"] == "True")
        self.assertEqual(len(serie), 5)
        self.assertEqual([p.vazado for p in serie], [False] * 4 + [True])
        self.assertEqual(medianas(serie), [(0.0, 25.0)])

    def test_mediana_por_posicao(self) -> None:
        serie = [Ponto(1.0, 3.0, 1, False, True), Ponto(0.0, 1.0, 1, False, True),
                 Ponto(1.0, 5.0, 2, False, True)]
        self.assertEqual(medianas(serie), [(0.0, 1.0), (1.0, 4.0)])

    def test_deslocamento(self) -> None:
        self.assertEqual(deslocamento(0, 1), 0.0)
        self.assertAlmostEqual(deslocamento(0, 2), -PASSO_ARQUITETURA / 2)
        self.assertAlmostEqual(deslocamento(2, 3, repeticao=5),
                               PASSO_ARQUITETURA + 2 * PASSO_REPETICAO)


if __name__ == "__main__":
    unittest.main()
