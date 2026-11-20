"""Testes de harness/analise/longa.py, com arquivos pequenos e séries sintéticas.

    uv run python -m unittest harness.analise.teste_longa -v
"""

import json
import os
import tempfile
import unittest
from typing import List

from harness.analise.comum import Linha
from harness.analise.longa import (SerieInterna, deriva, deriva_dos_lotes, horas,
                                   info_da_execucao, minimos_quadrados, minutos_internos,
                                   operacoes_por_minuto, serie_interna, taxa, theil_sen,
                                   ultimas_por_minuto, ultimo)

INICIO_MS: int = 1_000_000


class TesteUltimasPorMinuto(unittest.TestCase):
    def test_taxa_entre_minutos(self) -> None:
        """Contador de 100 por segundo, amostrado a cada 10 s, de 20 s antes da medição até o
        fim do terceiro minuto."""
        linhas = ["ts_ms,origem,metrica,rotulo,valor"]
        for ts in range(INICIO_MS - 20_000, INICIO_MS + 180_001, 10_000):
            linhas.append(f"{ts},pg,contador,,{100 * (ts - INICIO_MS) // 1000}")
        linhas.append(f"{INICIO_MS + 5_000},pg,medidor,t1,")
        linhas.append(f"{INICIO_MS + 7_000},pg,medidor,t1,42")
        with tempfile.TemporaryDirectory() as pasta:
            caminho = os.path.join(pasta, "metricas-pg.csv")
            with open(caminho, "w", encoding="utf-8") as f:
                f.write("\n".join(linhas) + "\n")
            ultimas = ultimas_por_minuto(caminho, INICIO_MS, INICIO_MS + 180_000)
        contador = ultimas[("contador", "")]
        self.assertEqual(sorted(contador), [-1, 0, 1, 2])
        self.assertEqual(contador[-1], (INICIO_MS - 10_000, -1000.0))
        self.assertEqual(contador[2], (INICIO_MS + 170_000, 17_000.0))
        for minuto in (0, 1, 2):
            self.assertAlmostEqual(taxa(contador, minuto) or 0.0, 100.0)
        self.assertIsNone(taxa(contador, -1))
        self.assertEqual(ultimo(ultimas[("medidor", "t1")], 0), 42.0)


class TesteOperacoesPorMinuto(unittest.TestCase):
    def test_contagens_aquecimento_erros_e_latencias(self) -> None:
        us = INICIO_MS * 1000
        linhas = ["ts_us,operacao,latencia_us,ok,cliente,itens",
                  f"{us - 2_000_000},enfileira,10,1,0,1",
                  f"{us - 1_000_000},enfileira,10,1,0,1",
                  f"{us - 500_000},consome,10,1,0,1",
                  f"{us + 1},enfileira,10,1,0,1",
                  f"{us + 2},enfileira,10,1,0,1",
                  f"{us + 3},enfileira,,0,0,1",
                  f"{us + 4},consome,10,1,0,1",
                  f"{us + 5},fim_a_fim,1000,1,0,1",
                  f"{us + 6},fim_a_fim,3000,1,0,1",
                  f"{us + 60_000_000},consome,10,1,0,1",
                  f"{us + 60_000_001},fim_a_fim,5000,1,0,1",
                  f"{us + 120_000_000},consome,10,1,0,1"]
        with tempfile.TemporaryDirectory() as pasta:
            caminho = os.path.join(pasta, "operacoes.csv")
            with open(caminho, "w", encoding="utf-8") as f:
                f.write("\n".join(linhas) + "\n")
            ops = operacoes_por_minuto(caminho, INICIO_MS, INICIO_MS + 120_000)
        self.assertEqual(ops.antes, {"enfileira": 2, "consome": 1})
        self.assertEqual(ops.contar("enfileira"), [2, 0])
        self.assertEqual(ops.contar("consome"), [1, 1])
        self.assertEqual(ops.erros, [1, 0])
        self.assertEqual([list(a) for a in ops.fim_a_fim_us], [[1000, 3000], [5000]])


class TesteInclinacao(unittest.TestCase):
    def test_theil_sen_ignora_um_ponto_extremo(self) -> None:
        x = [float(i) for i in range(10)]
        y = [2 * v + 1 for v in x]
        y[9] = 1000.0
        self.assertAlmostEqual(theil_sen(x, y) or 0.0, 2.0)
        self.assertGreater(minimos_quadrados(x, y) or 0.0, 2.0)

    def test_poucos_pontos(self) -> None:
        self.assertIsNone(theil_sen([1.0], [1.0]))
        self.assertIsNone(minimos_quadrados([1.0, 1.0], [1.0, 2.0]))


def _minutos(total: int) -> List[Linha]:
    """Série que sobe 2 por hora a partir de 5, com uma coluna vazia no primeiro minuto."""
    return [{"minuto": str(m), "v": str(5 + 2 * m / 60), "w": "" if m == 0 else "1"}
            for m in range(total)]


class TesteDeriva(unittest.TestCase):
    def test_reta_de_tres_horas(self) -> None:
        d = deriva(_minutos(180), "v", 180)
        assert d is not None
        self.assertAlmostEqual(d["inclinacao_theil_sen_por_h"], 2.0)
        self.assertAlmostEqual(d["inclinacao_mq_por_h"], 2.0)
        self.assertAlmostEqual(d["primeira_hora_mediana"], 5 + 2 * 29.5 / 60)
        self.assertAlmostEqual(d["ultima_hora_mediana"], 5 + 2 * 149.5 / 60)
        self.assertEqual(d["minutos"], 180)

    def test_valor_ausente(self) -> None:
        d = deriva(_minutos(180), "w", 180)
        assert d is not None
        self.assertEqual(d["minutos"], 179)
        self.assertAlmostEqual(d["razao_ultima_primeira"], 1.0)

    def test_horas(self) -> None:
        h = horas(_minutos(180), "v")
        self.assertEqual([x["hora"] for x in h], [1, 2, 3])
        self.assertEqual(h[0]["minutos"], 60)
        self.assertAlmostEqual(h[0]["min"], 5.0)
        self.assertAlmostEqual(h[2]["max"], 5 + 2 * 179 / 60)


class TesteControle(unittest.TestCase):
    def test_janela_limitada_a_duracao_planejada(self) -> None:
        manifesto = {"estado": "ok", "taxa_mib_s": 24.3, "duracao_s": 120,
                     "fio_inicio_ms": INICIO_MS, "fio_fim_ms": INICIO_MS + 120_443}
        with tempfile.TemporaryDirectory() as pasta:
            with open(os.path.join(pasta, "manifesto.json"), "w", encoding="utf-8") as f:
                json.dump(manifesto, f)
            info = info_da_execucao(pasta)
        self.assertEqual((info["eixo"], info["arquitetura"]), ("controle", "fio"))
        self.assertEqual(info["fim_ms"] - info["inicio_ms"], 120_000)


class TesteLotes(unittest.TestCase):
    def test_so_periodicos(self) -> None:
        lotes: List[Linha] = []
        for k in range(1, 5):
            lotes.append({"tipo": "lastpoint", "hora": str(k / 2), "media_ms": str(10 + 4 * k / 2),
                          "periodico": "True"})
        lotes.append({"tipo": "lastpoint", "hora": "2.1", "media_ms": "999", "periodico": "False"})
        d = deriva_dos_lotes(lotes)
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["lotes"], 4)
        self.assertAlmostEqual(d[0]["inclinacao_theil_sen_ms_por_h"], 4.0)
        self.assertAlmostEqual(d[0]["primeiro_lote_media_ms"], 12.0)
        self.assertAlmostEqual(d[0]["ultimo_lote_media_ms"], 18.0)
        self.assertAlmostEqual(d[0]["razao_ultimo_primeiro"], 1.5)


class TesteInternas(unittest.TestCase):
    def test_regras(self) -> None:
        self.assertEqual(serie_interna("postgres", "pg_tuplas_mortas", "public.tarefas"),
                         SerieInterna("tuplas_mortas_tarefas", "valor"))
        self.assertEqual(serie_interna("timescaledb", "pg_autovacuum_total",
                                       "_timescaledb_internal._hyper_1_2_chunk"),
                         SerieInterna("autovacuum_chunks", "desde_inicio"))
        self.assertIsNone(serie_interna("timescaledb", "pg_tuplas_mortas_pct",
                                        "_timescaledb_internal._hyper_1_2_chunk"))
        self.assertIsNone(serie_interna("timescaledb", "pg_tuplas_mortas",
                                        "_timescaledb_catalog.chunk"))
        self.assertIsNone(serie_interna("postgres", "pg_idade_xid", "template1"))
        self.assertEqual(serie_interna("postgres", "pg_idade_xid", "fila"),
                         SerieInterna("idade_xid", "valor"))
        self.assertEqual(serie_interna("influxdb", "influx_series", "benchmark"),
                         SerieInterna("series", "valor"))
        self.assertIsNone(serie_interna("influxdb", "influx_series", "_internal"))
        self.assertIsNone(serie_interna("rabbitmq", "rmq_publicadas_total", "tarefas"))

    def test_valor_desde_inicio_taxa_e_soma(self) -> None:
        def linha(arquivo: str, minuto: int, metrica: str, rotulo: str, valor: float) -> Linha:
            return {"arquivo": arquivo, "minuto": str(minuto), "metrica": metrica,
                    "rotulo": rotulo, "ts_ms": str(INICIO_MS + minuto * 60_000 + 59_000),
                    "valor": repr(valor)}
        chunk = "_timescaledb_internal._hyper_1_%d_chunk"
        linhas: List[Linha] = [
            linha("postgres", 0, "pg_tuplas_mortas", "public.tarefas", 10.0),
            linha("postgres", 2, "pg_tuplas_mortas", "public.tarefas", 30.0),
            linha("postgres", 0, "pg_autovacuum_total", "public.tarefas", 5.0),
            linha("postgres", 1, "pg_autovacuum_total", "public.tarefas", 7.0),
            linha("postgres", 0, "pg_wal_bytes_total", "", 0.0),
            linha("postgres", 1, "pg_wal_bytes_total", "", 60.0 * 2**20),
            linha("timescaledb", 0, "pg_autovacuum_total", chunk % 1, 1.0),
            linha("timescaledb", 1, "pg_autovacuum_total", chunk % 1, 2.0),
            linha("timescaledb", 1, "pg_autovacuum_total", chunk % 2, 4.0),
            linha("postgres", 5, "pg_tuplas_mortas", "public.tarefas", 99.0),
        ]
        saida = minutos_internos(linhas, 3)
        self.assertEqual(len(saida), 3)
        self.assertEqual([m.get("tuplas_mortas_tarefas") for m in saida], [10.0, None, 30.0])
        self.assertEqual([m.get("autovacuum_tarefas") for m in saida], [0.0, 2.0, None])
        self.assertEqual([m.get("wal_mib_s") for m in saida], [None, 1.0, None])
        # chunk novo no minuto 1 entra na soma: 1 + 0 no minuto 0, 2 + 4 no minuto 1
        self.assertEqual([m.get("autovacuum_chunks") for m in saida], [0.0, 5.0, None])


if __name__ == "__main__":
    unittest.main()
