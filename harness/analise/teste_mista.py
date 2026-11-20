"""Testes da etapa comparar de harness/analise/mista.py, com execuções sintéticas.

    uv run python -m unittest harness.analise.teste_mista -v
"""

import unittest
from typing import Any, Dict, List

from harness.analise.comum import Linha
from harness.analise.mista import comparacoes, configuracoes, medianas_da_base

# Fator sobre a latência sem escrita: a 250 mil métricas/s nada muda.
FATOR: Dict[str, Dict[int, float]] = {
    "consolidada": {0: 1.0, 250_000: 1.0, 500_000: 1.5, 1_000_000: 2.0},
    "poliglota": {0: 1.0, 250_000: 1.0, 500_000: 1.1, 1_000_000: 1.2},
}
BASE: Dict[str, Dict[str, float]] = {
    "consolidada": {"p50": 400.0, "p95": 700.0, "p99": 1000.0},
    "poliglota": {"p50": 4.0, "p95": 7.0, "p99": 10.0},
}


def _execucoes() -> List[Linha]:
    """Cinco repetições de cada configuração; a poliglota a 1 milhão/s na repetição 5 não
    sustentou a taxa."""
    linhas: List[Linha] = []
    for arquitetura, fatores in FATOR.items():
        for taxa, fator in fatores.items():
            for r in range(1, 6):
                escala = 1.0 if arquitetura == "consolidada" else 0.1
                linha: Linha = {"execucao": f"{arquitetura}_{taxa}_r{r}", "arquitetura": arquitetura,
                                "taxa": str(taxa), "repeticao": str(r),
                                "valida": str(not (arquitetura == "poliglota" and taxa == 1_000_000
                                                   and r == 5)),
                                "cpu_sut_nucleos": str(2.0 + taxa / 1e6)}
                for p, valor in BASE[arquitetura].items():
                    linha[f"busca_hibrida_{p}_ms"] = str((valor + r * escala) * fator)
                linhas.append(linha)
    return linhas


def _uma(comparadas: List[Dict[str, Any]], **filtro: Any) -> Dict[str, Any]:
    achadas = [c for c in comparadas if all(c[k] == v for k, v in filtro.items())]
    assert len(achadas) == 1, achadas
    return achadas[0]


class TesteMista(unittest.TestCase):
    def setUp(self) -> None:
        self.linhas = _execucoes()
        self.comparadas = comparacoes(self.linhas)

    def test_medianas_da_base(self) -> None:
        base = medianas_da_base(self.linhas)
        self.assertAlmostEqual(base[("consolidada", "p50")], 403.0)
        self.assertAlmostEqual(base[("poliglota", "p99")], 10.3)

    def test_familias(self) -> None:
        for teste, arquitetura in (("taxa_contra_base", "consolidada"),
                                   ("taxa_contra_base", "poliglota"),
                                   ("entre_arquiteturas", "consolidada_poliglota")):
            familia = [c for c in self.comparadas
                       if c["teste"] == teste and c["arquitetura"] == arquitetura]
            self.assertEqual(len(familia), 6)
            self.assertTrue(all(c["familia"] == 6 for c in familia))

    def test_taxa_contra_base(self) -> None:
        dobro = _uma(self.comparadas, teste="taxa_contra_base", arquitetura="consolidada",
                     taxa=1_000_000, metrica="busca_hibrida_p50_ms")
        self.assertEqual(dobro["a12"], 1.0)
        self.assertAlmostEqual(dobro["razao_medianas"], 2.0)
        self.assertAlmostEqual(dobro["razao_bloco_min"], 2.0)
        self.assertAlmostEqual(dobro["p_holm"], 6 * 2 / 252)
        self.assertTrue(dobro["significativo"])
        igual = _uma(self.comparadas, teste="taxa_contra_base", arquitetura="consolidada",
                     taxa=250_000, metrica="busca_hibrida_p99_ms")
        self.assertEqual(igual["p"], 1.0)
        self.assertFalse(igual["significativo"])

    def test_invalida_fica_de_fora(self) -> None:
        poliglota = _uma(self.comparadas, teste="taxa_contra_base", arquitetura="poliglota",
                         taxa=1_000_000, metrica="busca_hibrida_p50_ms")
        self.assertEqual((poliglota["n1"], poliglota["n2"]), (4, 5))

    def test_entre_arquiteturas(self) -> None:
        meio = _uma(self.comparadas, teste="entre_arquiteturas", taxa=500_000,
                    metrica="degradacao_p50")
        self.assertEqual(meio["a12"], 1.0)
        self.assertAlmostEqual(meio["razao_medianas"], 1.5 / 1.1)
        self.assertTrue(meio["alcancavel"])
        self.assertTrue(meio["significativo"])
        # Com 5 contra 4 numa família de 6, o menor p ajustado é 6 × 2/126 > 0,05.
        dobro = _uma(self.comparadas, teste="entre_arquiteturas", taxa=1_000_000,
                     metrica="degradacao_p50")
        self.assertEqual((dobro["n1"], dobro["n2"]), (5, 4))
        self.assertFalse(dobro["alcancavel"])
        self.assertFalse(dobro["significativo"])

    def test_configuracoes(self) -> None:
        por_chave = {(c["arquitetura"], c["taxa"]): c for c in configuracoes(self.linhas)}
        self.assertEqual(len(por_chave), 8)
        poliglota = por_chave[("poliglota", 1_000_000)]
        self.assertEqual((poliglota["n"], poliglota["invalidas"], poliglota["valida"]), (4, 1, 4))
        consolidada = por_chave[("consolidada", 1_000_000)]
        self.assertAlmostEqual(consolidada["degradacao_p50_mediana"], 2.0)
        self.assertAlmostEqual(consolidada["cpu_sut_nucleos_mediana"], 3.0)
        self.assertAlmostEqual(por_chave[("consolidada", 0)]["degradacao_p99_mediana"], 1.0)


if __name__ == "__main__":
    unittest.main()
