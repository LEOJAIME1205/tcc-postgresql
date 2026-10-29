"""Coletor de métricas internas do PostgreSQL."""

import argparse
import csv
import os
import signal
import sys
import time
import threading
from types import FrameType
from typing import Callable, List, LiteralString, Optional, Set, Tuple, Union

import psycopg
from psycopg.rows import TupleRow

Metrica = Tuple[str, str, str, Union[int, float]]
Conexao = psycopg.Connection[TupleRow]
Leitor = Callable[[Conexao], List[Metrica]]

CPUS_COLETA: Set[int] = {6, 7, 14, 15}
INTERVALO_S: float = 10.0
VOLTAS_TAMANHOS: int = 6
VOLTAS_PGSTATTUPLE: int = 30

SQL_GERAIS: LiteralString = """
    SELECT current_database(),
           (SELECT wal_bytes FROM pg_stat_wal),
           (SELECT num_timed + num_requested FROM pg_stat_checkpointer),
           (SELECT count(*) FROM pg_locks WHERE NOT granted),
           xact_commit, xact_rollback, deadlocks
    FROM pg_stat_database
    WHERE datname = current_database()
"""

SQL_TABELAS: LiteralString = """
    SELECT schemaname || '.' || relname,
           n_live_tup, n_dead_tup, autovacuum_count,
           autoanalyze_count, total_autovacuum_time, total_autoanalyze_time
    FROM pg_stat_user_tables
"""

SQL_TAMANHOS: LiteralString = """
    SELECT schemaname || '.' || relname,
           pg_table_size(relid), pg_indexes_size(relid)
    FROM pg_stat_user_tables
"""

SQL_PGSTATTUPLE: LiteralString = """
    SELECT t.schemaname || '.' || t.relname,
           a.dead_tuple_percent, a.approx_free_percent
    FROM pg_stat_user_tables t
    JOIN pg_class c ON c.oid = t.relid
    CROSS JOIN LATERAL pgstattuple_approx(t.relid) a
    WHERE c.relkind IN ('r', 'm')
"""


def ler_estatisticas(conexao: Conexao) -> List[Metrica]:
    """Estatísticas do banco e de cada tabela, a cada 10 s."""
    linhas: List[Metrica] = []
    with conexao.cursor() as cur:
        geral = cur.execute(SQL_GERAIS).fetchone()
        if geral is not None:
            banco, wal, checkpoints, esperas, commits, rollbacks, deadlocks = geral
            linhas.extend([
                ("postgres", "pg_wal_bytes_total", "", int(wal)),
                ("postgres", "pg_checkpoints_total", "", int(checkpoints)),
                ("postgres", "pg_esperas_lock", "", int(esperas)),
                ("postgres", "pg_commits_total", str(banco), int(commits)),
                ("postgres", "pg_rollbacks_total", str(banco), int(rollbacks)),
                ("postgres", "pg_deadlocks_total", str(banco), int(deadlocks)),
            ])
        for tabela, vivas, mortas, n_av, n_aa, ms_av, ms_aa in cur.execute(SQL_TABELAS).fetchall():
            nome = str(tabela)
            linhas.extend([
                ("postgres", "pg_tuplas_vivas", nome, int(vivas)),
                ("postgres", "pg_tuplas_mortas", nome, int(mortas)),
                ("postgres", "pg_autovacuum_total", nome, int(n_av)),
                ("postgres", "pg_autoanalyze_total", nome, int(n_aa)),
                ("postgres", "pg_autovacuum_ms_total", nome, float(ms_av)),
                ("postgres", "pg_autoanalyze_ms_total", nome, float(ms_aa)),
            ])
    return linhas


def ler_tamanhos(conexao: Conexao) -> List[Metrica]:
    """Tamanho de cada tabela e dos seus índices, a cada 60 s."""
    linhas: List[Metrica] = []
    with conexao.cursor() as cur:
        for tabela, tabela_bytes, indices_bytes in cur.execute(SQL_TAMANHOS).fetchall():
            if tabela_bytes is None or indices_bytes is None:
                continue
            linhas.append(("postgres", "pg_tabela_bytes", str(tabela), int(tabela_bytes)))
            linhas.append(("postgres", "pg_indices_bytes", str(tabela), int(indices_bytes)))
    return linhas


def ler_pgstattuple(conexao: Conexao) -> List[Metrica]:
    """Tuplas mortas e espaço livre de cada tabela, a cada 5 min (cenário longa)."""
    linhas: List[Metrica] = []
    with conexao.cursor() as cur:
        for tabela, mortas_pct, livre_pct in cur.execute(SQL_PGSTATTUPLE).fetchall():
            linhas.append(("postgres", "pg_tuplas_mortas_pct", str(tabela), float(mortas_pct)))
            linhas.append(("postgres", "pg_espaco_livre_pct", str(tabela), float(livre_pct)))
    return linhas


def avisar(mensagem: str) -> None:
    """Mensagem com horário na saída de erro, para o log da execução."""
    print(f"[{time.strftime('%H:%M:%S')}] {mensagem}", file=sys.stderr, flush=True)


def conectar(conninfo: str) -> Optional[Conexao]:
    """Abre a conexão de coleta, ou devolve None se o banco ainda não aceita."""
    try:
        return psycopg.connect(
            conninfo,
            autocommit=True,
            application_name="tcc-coletor",
            connect_timeout=2,
            options="-c statement_timeout=60000 -c lock_timeout=1000",
        )
    except psycopg.OperationalError:
        return None


def main() -> None:
    p = argparse.ArgumentParser(description="Coletor de métricas internas do PostgreSQL")
    p.add_argument("saida", help="arquivo metricas-postgres.csv a criar; não pode existir")
    p.add_argument("--conninfo", required=True,
                   help="ex.: 'host=172.18.0.2 dbname=postgres user=postgres password=tcc'")
    p.add_argument("--pgstattuple", action="store_true",
                   help="amostra pgstattuple_approx a cada 5 min (cenário longa)")
    args = p.parse_args()
    saida: str = args.saida
    conninfo: str = args.conninfo
    com_pgstattuple: bool = args.pgstattuple

    os.sched_setaffinity(0, CPUS_COLETA)

    parar = threading.Event()

    def ao_sinal(_signum: int, _frame: Optional[FrameType]) -> None:
        parar.set()


    signal.signal(signal.SIGTERM, ao_sinal)
    signal.signal(signal.SIGINT, ao_sinal)

    conexao: Optional[Conexao] = None
    with open(saida, "x", newline="", encoding="utf-8") as f:
        escritor = csv.writer(f)
        escritor.writerow(["ts_ms", "origem", "metrica", "rotulo", "valor"])
        volta: int = 0
        proximo: float = time.monotonic()

        while not parar.is_set():
            ts_ms: int = time.time_ns() // 1_000_000

            if conexao is None:
                conexao = conectar(conninfo)
                if conexao is not None:
                    avisar("conectado")

            if conexao is not None:
                leitores: List[Tuple[str, Leitor]] = [("estatísticas", ler_estatisticas)]
                if volta % VOLTAS_TAMANHOS == 0:
                    leitores.append(("tamanhos", ler_tamanhos))
                if com_pgstattuple and volta % VOLTAS_PGSTATTUPLE == 0:
                    leitores.append(("pgstattuple", ler_pgstattuple))

                for nome, ler in leitores:
                    try:
                        escritor.writerows((ts_ms, *linha) for linha in ler(conexao))
                    except psycopg.Error as erro:
                        avisar(f"{nome}: {erro}")
                        if conexao.broken:
                            conexao.close()
                            conexao = None
                            avisar("conexão perdida; nova tentativa na próxima volta")
                            break
            f.flush()

            volta += 1
            proximo += INTERVALO_S
            agora = time.monotonic()
            if proximo < agora:
                proximo = agora
            parar.wait(max(0.0, proximo - time.monotonic()))


    if conexao is not None:
        conexao.close()


if __name__ == "__main__":
    main()
