"""Testes de harness/analise/custo.py: Compose sintético, marcadores, fechamento do lock,
escolha da instância, subida e a leitura dos arquivos versionados do próprio repositório.

    uv run python -m unittest harness.analise.teste_custo -v
"""

import os
import tempfile
import unittest
from typing import Any, Dict, List

from harness.analise.comum import Linha
from harness.analise.custo import (CLIENTES, Instancia, br, celulas_complexidade,
                                   celulas_memoria, celulas_nuvem, configuracoes_de_memoria,
                                   fechamento, instancias_agrupadas, ler_lock, linhas_uteis,
                                   maquina_para, memoria_da_execucao,
                                   marcador_vale, memoria_gib, montados, opcoes_c, requisito,
                                   resolver, servicos_da_arquitetura, subida_s, tabular,
                                   ultimos_por_rotulo, valor)

MODELOS = """services:
  comum:
    restart: "no"
    logging:
      driver: json-file

  sistema-sob-teste:
    extends: comum
    cpuset: "0-5,8-13"

  postgres:
    extends: sistema-sob-teste
    image: tcc-postgres:18
    mem_limit: 20g
    memswap_limit: 20g
    shm_size: 1g
    environment:
      POSTGRES_PASSWORD: tcc
"""

COMPOSE = """# comentário
services:
  postgres:
    extends:
      file: ../modelos.yaml
      service: postgres
    mem_limit: 12g
    memswap_limit: 12g
    environment:
      POSTGRES_DB: fila
    command:
      - postgres
      # comentário dentro da lista
      - -c
      - work_mem=4MB

  rabbitmq:
    extends:
      file: ../modelos.yaml
      service: sistema-sob-teste
    image: rabbitmq:4@sha256:abc
    mem_limit: 7g
    volumes:
      - ./rabbitmq.conf:/etc/rabbitmq/conf.d/20-tcc.conf:ro
      - ${DADOS:?defina}:/var/lib/rabbitmq

  gerador:
    image: tcc-gerador:1
"""


class TesteCompose(unittest.TestCase):
    def setUp(self) -> None:
        self.pasta = tempfile.TemporaryDirectory()
        raiz = self.pasta.name
        self.modelos = os.path.join(raiz, "modelos.yaml")
        os.makedirs(os.path.join(raiz, "arq"))
        self.compose = os.path.join(raiz, "arq", "compose.yaml")
        arquivos = {self.modelos: MODELOS, self.compose: COMPOSE,
                    os.path.join(raiz, "arq", "rabbitmq.conf"): "# c\n\nlimite = 1\nlog = info\n"}
        for caminho, texto in arquivos.items():
            with open(caminho, "w", encoding="utf-8") as f:
                f.write(texto)

    def tearDown(self) -> None:
        self.pasta.cleanup()

    def test_heranca_do_modelo_postgres(self) -> None:
        chaves = resolver(self.compose, "postgres", self.modelos)
        self.assertEqual(valor(chaves, "image"), "tcc-postgres:18")
        self.assertEqual(valor(chaves, "mem_limit"), "12g")
        self.assertEqual(chaves["environment"],
                         ["environment:", "POSTGRES_DB: fila", "POSTGRES_PASSWORD: tcc"])
        self.assertEqual(chaves["command"], ["command:", "- postgres", "- -c", "- work_mem=4MB"])
        for controle in ("extends", "cpuset", "restart", "logging"):
            self.assertNotIn(controle, chaves)
        # image, mem_limit, memswap_limit, shm_size, 3 de environment e 4 de command
        self.assertEqual(sum(len(v) for v in chaves.values()), 11)
        self.assertEqual(opcoes_c(chaves), 1)
        self.assertEqual(opcoes_c(resolver(self.compose, "rabbitmq", self.modelos)), 0)

    def test_modelo_de_controle_nao_herda(self) -> None:
        chaves = resolver(self.compose, "rabbitmq", self.modelos)
        self.assertEqual(sorted(chaves), ["image", "mem_limit", "volumes"])
        self.assertEqual(valor(chaves, "image"), "rabbitmq:4@sha256:abc")
        arquivos = montados(chaves, os.path.dirname(self.compose))
        self.assertEqual([os.path.basename(a) for a in arquivos], ["rabbitmq.conf"])
        self.assertEqual(linhas_uteis(arquivos[0]), 2)


class TesteRepositorio(unittest.TestCase):
    """Lê os arquivos versionados de verdade; falha se o formato deles mudar."""

    def test_outbox(self) -> None:
        servicos = {s.nome: s for s in servicos_da_arquitetura("b", "outbox")}
        self.assertEqual(sorted(servicos), ["postgres", "rabbitmq", "relay"])
        self.assertEqual(servicos["postgres"].imagem, "tcc-postgres:18.6-ts2.31.0-pgv0.8.7")
        self.assertEqual([servicos[n].memoria_gib for n in ("postgres", "rabbitmq", "relay")],
                         [12.0, 7.0, 1.0])
        self.assertEqual([os.path.basename(a) for a in servicos["rabbitmq"].montados],
                         ["rabbitmq.conf"])
        self.assertEqual([servicos[n].opcoes_c for n in ("postgres", "rabbitmq", "relay")],
                         [5, 0, 0])

    def test_opcoes_c_do_timescaledb(self) -> None:
        servicos = servicos_da_arquitetura("c", "timescaledb")
        self.assertEqual([s.opcoes_c for s in servicos], [22])

    def test_orcamento_igual_por_eixo(self) -> None:
        for eixo, arquiteturas in (("a", ("consolidada", "poliglota")),
                                   ("b", ("consolidada", "ingenua", "outbox")),
                                   ("c", ("timescaledb", "influxdb")),
                                   ("m", ("consolidada", "poliglota"))):
            for arquitetura in arquiteturas:
                total = sum(s.memoria_gib for s in servicos_da_arquitetura(eixo, arquitetura))
                self.assertEqual(total, 20.0, f"{eixo}/{arquitetura}")

    def test_fechamento_no_lock(self) -> None:
        pacotes = ler_lock()
        poliglota = fechamento(pacotes, CLIENTES[("a", "poliglota")])
        for nome in ("opensearch-py", "aiohttp", "qdrant-client"):
            self.assertIn(nome, poliglota)
        consolidada = fechamento(pacotes, CLIENTES[("b", "consolidada")])
        self.assertIn("psycopg-binary", consolidada)


class TesteDependencias(unittest.TestCase):
    def test_marcadores(self) -> None:
        self.assertTrue(marcador_vale(None))
        self.assertFalse(marcador_vale("sys_platform == 'win32'"))
        self.assertTrue(marcador_vale("platform_machine == 'aarch64' or platform_machine == 'x86_64'"))
        self.assertTrue(marcador_vale("implementation_name != 'pypy'"))
        self.assertFalse(marcador_vale("sys_platform == 'linux' and platform_machine == 'arm64'"))
        with self.assertRaises(ValueError):
            marcador_vale("python_full_version < '3.11'")
        with self.assertRaises(ValueError):
            marcador_vale("(sys_platform == 'linux')")

    def test_requisito(self) -> None:
        self.assertEqual(requisito("opensearch-py[async]"), ("opensearch-py", ("async",)))
        self.assertEqual(requisito("Psycopg"), ("psycopg", ()))

    def test_fechamento_com_extra_marcador_e_ciclo(self) -> None:
        pacotes: List[Dict[str, Any]] = [
            {"name": "a", "dependencies": [{"name": "b"},
                                           {"name": "c", "marker": "sys_platform == 'win32'"}],
             "optional-dependencies": {"x": [{"name": "d"}]}},
            {"name": "b"},
            {"name": "c"},
            {"name": "d", "dependencies": [{"name": "a", "extra": ["x"]}]},
        ]
        self.assertEqual(fechamento(pacotes, ["a"]), ["a", "b"])
        self.assertEqual(fechamento(pacotes, ["a[x]"]), ["a", "b", "d"])


class TesteNuvem(unittest.TestCase):
    INSTANCIAS: List[Instancia] = [Instancia("m.medium", 1, 4, 0.06), Instancia("r.medium", 1, 8, 0.08),
                  Instancia("m.large", 2, 8, 0.12), Instancia("r.large", 2, 16, 0.16),
                  Instancia("r.xlarge", 4, 32, 0.32)]

    def test_maquina_mais_barata_que_cabe(self) -> None:
        escolhas = {m: maquina_para(m, self.INSTANCIAS).nome for m in (1, 7, 8, 9, 20)}
        self.assertEqual(escolhas, {1: "m.medium", 7: "r.medium", 8: "r.medium", 9: "r.large",
                                    20: "r.xlarge"})
        with self.assertRaises(ValueError):
            maquina_para(40, self.INSTANCIAS)

    def test_memoria(self) -> None:
        self.assertEqual(memoria_gib("19g"), 19.0)
        self.assertEqual(memoria_gib("512m"), 0.5)
        with self.assertRaises(ValueError):
            memoria_gib("muito")

    def test_ultimos_por_rotulo(self) -> None:
        linhas = ["ts_ms,origem,metrica,rotulo,valor",
                  "2000,pg,pg_tabela_bytes,pedidos,200",
                  "1000,pg,pg_tabela_bytes,pedidos,100",
                  "1000,pg,pg_tabela_bytes,tarefas,10",
                  "3000,pg,pg_tabela_bytes,tarefas,",
                  "3000,pg,pg_wal_bytes_total,,999"]
        with tempfile.TemporaryDirectory() as pasta:
            caminho = os.path.join(pasta, "metricas-pg.csv")
            with open(caminho, "w", encoding="utf-8") as f:
                f.write("\n".join(linhas) + "\n")
            ultimos = ultimos_por_rotulo(caminho, ("pg_tabela_bytes",))
        self.assertEqual(ultimos, {("pg_tabela_bytes", "pedidos"): 200.0,
                                   ("pg_tabela_bytes", "tarefas"): 10.0})


class TesteTabela(unittest.TestCase):
    def test_numero_brasileiro(self) -> None:
        self.assertEqual(br(None), "--")
        self.assertEqual(br(2.293, 1), "2,3")
        self.assertEqual(br(277.6756, 2), "277,68")

    def test_celulas_com_e_sem_valores(self) -> None:
        a: Linha = {"eixo": "a", "arquitetura": "consolidada", "servicos": "2", "imagens": "2",
                    "imagens_proprias": "2", "linhas_total": "67",
                    "linhas_total_pares_juntos": "62", "dependencias_diretas": "3",
                    "dependencias_total": "5", "preparo_s": "400.0", "subida_mediana_s": "2.293",
                    "subida_min_s": "1.834", "subida_max_s": "2.327"}
        self.assertEqual(celulas_complexidade(a), ["A", "consolidada", "2", "2 (2)", "67 (62)",
                                                   "3 (5)", "400",
                                                   "2,3 [1,8--2,3]"])
        c: Linha = {"eixo": "c", "arquitetura": "influxdb", "servicos": "1", "imagens": "1",
                    "imagens_proprias": "0", "linhas_total": "9",
                    "linhas_total_pares_juntos": "9", "preparo_s": "1.0", "subida_mediana_s": ""}
        self.assertEqual(celulas_complexidade(c)[5:], ["--", "1", "--"])
        b: Linha = {"eixo": "b", "arquitetura": "ingenua",
                    "maquinas": "postgres=r8a.large rabbitmq=r8a.medium",
                    "maquinas_usd_mes": "174.9364", "disco_usd_mes": "", "total_usd_mes": "",
                    "referencia_usd_mes": "233.2496"}
        self.assertEqual(celulas_nuvem(b), ["B", "ingênua", "r8a.large + r8a.medium", "174,94",
                                            "--", "--", "233,25"])

    def test_instancias_agrupadas(self) -> None:
        self.assertEqual(instancias_agrupadas("opensearch=r8a.large qdrant=r8a.large busca=m8a.medium"),
                         "2$\\times$r8a.large + m8a.medium")

    def test_tabular_agrupa_o_eixo(self) -> None:
        texto = tabular(("Eixo", "Arquitetura", "n"),
                        [["A", "x", "1"], ["A", "y", "2"], ["B", "z", "3"]])
        linhas = texto.splitlines()
        self.assertEqual(linhas[0], r"\begin{tabular}{llr}")
        self.assertIn(r"A & x & 1 \\", linhas)
        self.assertIn(r" & y & 2 \\", linhas)
        self.assertEqual(linhas.index(r"B & z & 3 \\") - 1, linhas.index(r"\hline", 4))


class TesteMemoria(unittest.TestCase):
    def test_maximos_por_servico(self) -> None:
        linhas = ["ts_ms,origem,metrica,rotulo,valor",
                  "1000,container,cont_mem_pico_bytes,tcc-b-outbox-postgres-1,100",
                  "1000,container,cont_mem_anon_bytes,tcc-b-outbox-postgres-1,10",
                  "1000,container,cont_mem_shmem_bytes,tcc-b-outbox-postgres-1,50",
                  "1000,container,cont_mem_arquivo_bytes,tcc-b-outbox-postgres-1,999",
                  "2000,container,cont_mem_pico_bytes,tcc-b-outbox-postgres-1,120",
                  "2000,container,cont_mem_anon_bytes,tcc-b-outbox-postgres-1,30",
                  "2000,container,cont_mem_shmem_bytes,tcc-b-outbox-postgres-1,20",
                  "1000,container,cont_mem_pico_bytes,tcc-b-outbox-gerador-run-ab12,5000",
                  "1000,container,cont_mem_pico_bytes,tcc-b-outbox-relay-1,7",
                  "1000,container,cont_cpu_us_total,tcc-b-outbox-relay-1,123"]
        with tempfile.TemporaryDirectory() as pasta:
            caminho = os.path.join(pasta, "metricas-host.csv")
            with open(caminho, "w", encoding="utf-8") as f:
                f.write("\n".join(linhas) + "\n")
            # anon + shmem é somado na mesma amostra: 60 em 1000, 50 em 2000
            self.assertEqual(memoria_da_execucao(caminho, ("postgres", "relay")),
                             {"postgres": (120.0, 60.0), "relay": (7.0, 0.0)})
            with self.assertRaises(ValueError):
                memoria_da_execucao(caminho, ("postgres", "rabbitmq"))

    def test_configuracoes_e_celulas(self) -> None:
        linhas: List[Dict[str, Any]] = [
            {"eixo": "b", "grupo": "b-vazao", "arquitetura": "outbox", "nivel": 1,
             "mem_pico_gib": v, "mem_anon_shmem_gib": v / 2} for v in (10.0, 12.0, 11.0)]
        linhas.append({"eixo": "b", "grupo": "b-vazao", "arquitetura": "outbox", "nivel": 2,
                       "mem_pico_gib": 19.0, "mem_anon_shmem_gib": 6.0})
        configs = configuracoes_de_memoria(linhas)
        self.assertEqual([(c["nivel"], c["n"], c["mem_pico_gib_mediana"], c["mem_pico_gib_min"],
                           c["mem_pico_gib_max"]) for c in configs],
                         [(1, 3, 11.0, 10.0, 12.0), (2, 1, 19.0, 19.0, 19.0)])
        texto: List[Linha] = [{k: str(v) for k, v in c.items()} for c in configs]
        self.assertEqual(celulas_memoria("b", "outbox", texto),
                         ["B", r"\emph{outbox}", "11,0--19,0", "5,5--6,0"])
        self.assertEqual(celulas_memoria("b", "ingenua", texto), ["B", "ingênua", "--", "--"])


class TesteSubida(unittest.TestCase):
    def test_desconta_repouso_e_espera_do_gerador(self) -> None:
        tempos = {"repouso_inicio": 1_000, "aquecimento_inicio": 1_000 + 1_200_000 + 15_000}
        a: Dict[str, Any] = {"eixo": "a", "tempos_ms": tempos, "controles": {"repouso_s": 1200}}
        c: Dict[str, Any] = {"eixo": "c", "tempos_ms": tempos, "controles": {"repouso_s": 1200}}
        self.assertEqual(subida_s(a), 12.0)
        self.assertEqual(subida_s(c), 15.0)
        self.assertIsNone(subida_s({"eixo": "a", "tempos_ms": {}, "controles": {}}))


if __name__ == "__main__":
    unittest.main()
