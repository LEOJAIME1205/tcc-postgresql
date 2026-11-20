"""Testes da etapa comparar de harness/analise/curtas.py, com execuções sintéticas.

    uv run python -m unittest harness.analise.teste_curtas -v
"""

import unittest
from typing import Any, Dict, List

from harness.analise.comum import Linha, numero
from harness.analise.curtas import comparacoes, configuracoes


def _linha(grupo: str, arquitetura: str, nivel: int, repeticao: int, **valores: Any) -> Linha:
    linha: Linha = {"grupo": grupo, "arquitetura": arquitetura, "nivel": str(nivel),
                    "repeticao": str(repeticao)}
    linha.update({k: "" if v is None else str(v) for k, v in valores.items()})
    return linha


def _execucoes_a() -> List[Linha]:
    """A, nível 1: a poliglota tem vazão maior e latência menor em todas as repetições; no
    p99, as duas se misturam."""
    linhas: List[Linha] = []
    for r in range(1, 6):
        linhas.append(_linha("a", "consolidada", 1, r, busca_hibrida_por_s=2.0 + r / 100,
                             busca_hibrida_p50_ms=400.0 + r, busca_hibrida_p99_ms=10.0 + r,
                             acima_do_cache_pslc=False, gerador_gargalo=False))
        linhas.append(_linha("a", "poliglota", 1, r, busca_hibrida_por_s=250.0 + r,
                             busca_hibrida_p50_ms=4.0 + r / 10, busca_hibrida_p99_ms=10.5 + r,
                             acima_do_cache_pslc=False, gerador_gargalo=r == 3))
    return linhas


def _por(comparadas: List[Dict[str, Any]], **filtro: str) -> List[Dict[str, Any]]:
    return [c for c in comparadas if all(str(c[k]) == v for k, v in filtro.items())]


class TesteNumero(unittest.TestCase):
    def test_conversoes(self) -> None:
        linha: Linha = {"a": "", "b": "None", "c": "True", "d": "False", "e": "2.5"}
        self.assertEqual([numero(linha, k) for k in "abcdef"], [None, None, 1.0, 0.0, 2.5, None])


class TesteComparacoesA(unittest.TestCase):
    def setUp(self) -> None:
        self.comparadas = comparacoes(_execucoes_a())

    def test_familia_de_3_por_nivel(self) -> None:
        self.assertEqual(len(self.comparadas), 3)
        self.assertTrue(all(c["familia"] == 3 for c in self.comparadas))

    def test_sentido_e_significancia(self) -> None:
        vazao = _por(self.comparadas, metrica="busca_hibrida_por_s")[0]
        self.assertEqual(vazao["a12"], 0.0)
        self.assertEqual(vazao["a12_consolidada_melhor"], 0.0)
        self.assertEqual(vazao["melhor_mediana"], "poliglota")
        self.assertAlmostEqual(vazao["p"], 2 / 252)
        self.assertAlmostEqual(vazao["p_holm"], 3 * 2 / 252)
        self.assertTrue(vazao["significativo"])
        p50 = _por(self.comparadas, metrica="busca_hibrida_p50_ms")[0]
        self.assertEqual(p50["a12"], 1.0)
        self.assertEqual(p50["a12_consolidada_melhor"], 0.0)
        self.assertAlmostEqual(p50["razao_medianas"], 403.0 / 4.3)
        p99 = _por(self.comparadas, metrica="busca_hibrida_p99_ms")[0]
        self.assertFalse(p99["significativo"])
        self.assertEqual(p99["melhor_mediana"], "consolidada")

    def test_razao_por_bloco(self) -> None:
        p50 = _por(self.comparadas, metrica="busca_hibrida_p50_ms")[0]
        self.assertEqual(p50["blocos"], 5)
        self.assertAlmostEqual(p50["razao_bloco_min"], 405.0 / 4.5)
        self.assertAlmostEqual(p50["razao_bloco_max"], 401.0 / 4.1)


class TesteComparacoesB(unittest.TestCase):
    def test_familias_da_vazao_e_da_latencia(self) -> None:
        linhas: List[Linha] = []
        for r in range(1, 6):
            for arquitetura, base in (("consolidada", 5000.0), ("ingenua", 1800.0), ("outbox", 4100.0)):
                linhas.append(_linha("b-vazao", arquitetura, 4, r, consome_por_s=base + r,
                                     sustentavel=True))
                linhas.append(_linha("b-latencia", arquitetura, 1400, r,
                                     fim_a_fim_p50_ms=base / 100 + r, fim_a_fim_p99_ms=base / 50 + r,
                                     sustentavel=r != 2))
        comparadas = comparacoes(linhas)
        vazao = _por(comparadas, grupo="b-vazao")
        latencia = _por(comparadas, grupo="b-latencia")
        self.assertEqual(len(vazao), 2)
        self.assertTrue(all(c["familia"] == 2 for c in vazao))
        self.assertEqual({c["poliglota"] for c in vazao}, {"ingenua", "outbox"})
        self.assertEqual(len(latencia), 4)
        self.assertTrue(all(c["familia"] == 4 and c["alcancavel"] for c in latencia))
        self.assertTrue(all(c["melhor_mediana"] == "consolidada" for c in vazao))


class TesteConsultasC(unittest.TestCase):
    def test_20_contra_20_e_familia_dos_tipos(self) -> None:
        linhas: List[Linha] = []
        for nivel in (1, 2, 4, 8):
            for r in range(1, 6):
                linhas.append(_linha("c", "timescaledb", nivel, r, vazao_por_s=1e7,
                                     **{"consulta_lastpoint_media_ms": 50.0 + r,
                                        "consulta_high-cpu-all_media_ms": 900.0 + nivel}))
                linhas.append(_linha("c", "influxdb", nivel, r, vazao_por_s=4e6,
                                     **{"consulta_lastpoint_media_ms": 5.0 + r,
                                        "consulta_high-cpu-all_media_ms": 12000.0 + r}))
        consultas = _por(comparacoes(linhas), grupo="c-consultas")
        self.assertEqual(len(consultas), 2)
        for c in consultas:
            self.assertEqual((c["n_consolidada"], c["n_poliglota"], c["familia"], c["blocos"]),
                             (20, 20, 2, 20))
            self.assertEqual(c["nivel"], "todos")
        lastpoint = _por(consultas, metrica="consulta_lastpoint_media_ms")[0]
        self.assertEqual(lastpoint["melhor_mediana"], "influxdb")
        high = _por(consultas, metrica="consulta_high-cpu-all_media_ms")[0]
        self.assertEqual(high["melhor_mediana"], "timescaledb")


class TesteConfiguracoes(unittest.TestCase):
    def test_mediana_minimo_maximo_e_contagens(self) -> None:
        por_arquitetura = {c["arquitetura"]: c for c in configuracoes(_execucoes_a())}
        poliglota = por_arquitetura["poliglota"]
        self.assertEqual(poliglota["n"], 5)
        self.assertAlmostEqual(poliglota["busca_hibrida_por_s_mediana"], 253.0)
        self.assertAlmostEqual(poliglota["busca_hibrida_por_s_min"], 251.0)
        self.assertAlmostEqual(poliglota["busca_hibrida_por_s_max"], 255.0)
        self.assertEqual(poliglota["gerador_gargalo"], 1)
        self.assertEqual(por_arquitetura["consolidada"]["gerador_gargalo"], 0)


if __name__ == "__main__":
    unittest.main()
