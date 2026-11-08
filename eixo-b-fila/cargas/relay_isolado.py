"""Vazão isolada do relay da outbox (ADR-053, item 5).

    uv run python eixo-b-fila/cargas/relay_isolado.py --registro ARQUIVO.json [--linhas N]

Roda com a outbox no ar e recém-preparada. Para o relay, grava N linhas na tabela de saída
com COPY, liga os consumidores e religa o relay. A vazão em regime é a de 90% a 10% das
linhas restantes, pela contagem da tabela a cada 2 s; a média vai do religamento até a
tabela esvaziar. Os consumidores rodam no host, como processos do gerador.py.
"""

import argparse
import os
import random
import signal
import struct
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple, cast

import psycopg

from apoio import (CPUS_GERADOR, container, conninfo, docker, gerador, gravar_json,
                   mensagens_rabbitmq, rodando)

CONSUMIDORES: int = 8
CARGA_BYTES: int = 1024
INTERVALO_S: float = 2.0
LIMITE_S: float = 1800.0
ESTAVEL_S: float = 6.0  # a API do RabbitMQ atualiza as filas a cada 5 s
AVISO: int = 200_000


def avisar(mensagem: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {mensagem}", flush=True)


def restantes(conn: psycopg.Connection[Tuple[Any, ...]]) -> int:
    linha = conn.execute("SELECT count(*) FROM saida").fetchone()
    return int(linha[0]) if linha else -1


def encher(conn: psycopg.Connection[Tuple[Any, ...]], linhas: int) -> float:
    """COPY binário das linhas na tabela de saída, com a mensagem no formato da ADR-050."""
    carga = random.Random(linhas).randbytes(CARGA_BYTES)
    cabecalho = struct.Struct(">qq")
    inicio = time.monotonic()
    with conn.cursor() as cur, cur.copy("COPY saida (pedido, mensagem) FROM STDIN (FORMAT BINARY)") as copia:
        copia.set_types(["int8", "bytea"])
        for pedido in range(1, linhas + 1):
            copia.write_row((pedido, cabecalho.pack(time.time_ns() // 1000, time.monotonic_ns()) + carga))
            if pedido % AVISO == 0:
                avisar(f"  {pedido:,} linhas")
    conn.execute("ANALYZE saida")
    return round(time.monotonic() - inicio, 1)


def instante_de(serie: List[Tuple[float, int]], alvo: float) -> Optional[float]:
    """Instante, por interpolação linear, em que as linhas restantes caem a `alvo`."""
    for (t0, r0), (t1, r1) in zip(serie, serie[1:]):
        if r0 >= alvo >= r1 and r0 != r1:
            return t0 + (t1 - t0) * (r0 - alvo) / (r0 - r1)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Vazão isolada do relay da outbox (ADR-053)")
    parser.add_argument("--registro", required=True, help="arquivo JSON com o resultado")
    parser.add_argument("--linhas", type=int, default=1_000_000)
    args = parser.parse_args()
    linhas = cast(int, args.linhas)
    caminho_registro = cast(str, args.registro)
    os.sched_setaffinity(0, CPUS_GERADOR)

    relay = container("outbox", "relay")
    fora = [s for s in ("postgres", "rabbitmq", "relay") if not rodando(container("outbox", s))]
    if fora:
        print(f"Recusado: fora do ar: {', '.join(fora)}", file=sys.stderr)
        return 1
    ambiente_relay = docker(["inspect", "-f", "{{range .Config.Env}}{{println .}}{{end}}", relay]).stdout
    parametros = dict(linha.split("=", 1) for linha in ambiente_relay.splitlines()
                      if linha.startswith(("TCC_TRABALHADORES=", "TCC_LOTE=", "TCC_ESPERA=", "TCC_INTERVALO_MS=")))
    conn = psycopg.connect(conninfo("outbox"), autocommit=True)
    docker(["stop", relay])
    avisar("relay parado")
    if restantes(conn) != 0 or mensagens_rabbitmq("outbox") != 0:
        print("Recusado: a tabela de saída ou a fila não está vazia; rode sobre a outbox recém-preparada",
              file=sys.stderr)
        docker(["start", relay])
        return 1

    avisar(f"gravando {linhas:,} linhas na tabela de saída")
    carga_s = encher(conn, linhas)
    log = os.path.splitext(caminho_registro)[0] + ".log"
    os.makedirs(os.path.dirname(os.path.abspath(log)), exist_ok=True)
    consumidos_arquivo = os.path.join(tempfile.mkdtemp(prefix="relay-isolado-"), "consumidos.txt")
    consumidores = gerador(["consumir", "--consumidores", str(CONSUMIDORES),
                            "--registro", consumidos_arquivo], "outbox", log)
    time.sleep(3.0)

    inicio = time.monotonic()
    docker(["start", relay])
    avisar("relay religado")
    serie: List[Tuple[float, int]] = [(0.0, linhas)]
    saida_vazia_s: Optional[float] = None
    fila_vazia_s: Optional[float] = None
    try:
        while time.monotonic() - inicio < LIMITE_S:
            time.sleep(INTERVALO_S)
            restante = restantes(conn)
            serie.append((round(time.monotonic() - inicio, 2), restante))
            if restante == 0:
                saida_vazia_s = serie[-1][0]
                break
        avisar(f"tabela de saída vazia em {saida_vazia_s} s")
        vazia_desde: Optional[float] = None
        while saida_vazia_s is not None and time.monotonic() - inicio < LIMITE_S:
            if mensagens_rabbitmq("outbox") == 0:
                vazia_desde = vazia_desde or time.monotonic()
                if time.monotonic() - vazia_desde >= ESTAVEL_S:
                    fila_vazia_s = round(vazia_desde - inicio, 2)
                    break
            else:
                vazia_desde = None
            time.sleep(1.0)
        avisar(f"fila vazia em {fila_vazia_s} s")
    finally:
        consumidores.send_signal(signal.SIGTERM)
        consumidores.wait(timeout=60)
    conn.close()

    with open(consumidos_arquivo, encoding="utf-8") as f:
        consumidos = [int(linha) for linha in f if linha.strip()]
    t90, t10 = instante_de(serie, 0.9 * linhas), instante_de(serie, 0.1 * linhas)
    regime = round(0.8 * linhas / (t10 - t90)) if t90 is not None and t10 is not None and t10 > t90 else None
    registro: Dict[str, Any] = {
        "concluido_em": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "linhas": linhas,
        "relay": parametros, "consumidores": CONSUMIDORES, "carga_copy_s": carga_s,
        "saida_vazia_s": saida_vazia_s, "fila_vazia_s": fila_vazia_s,
        "vazao_media_por_s": round(linhas / saida_vazia_s) if saida_vazia_s else None,
        "vazao_regime_por_s": regime,
        "consumidos": len(consumidos), "consumidos_distintos": len(set(consumidos)),
        "serie_restantes": serie,
    }
    gravar_json(caminho_registro, registro)
    avisar(f"vazão do relay: {registro['vazao_regime_por_s']}/s em regime, "
           f"{registro['vazao_media_por_s']}/s na média; {len(consumidos)} consumidos "
           f"({len(set(consumidos))} distintos)")
    return 0 if saida_vazia_s is not None and len(set(consumidos)) == linhas else 1


if __name__ == "__main__":
    sys.exit(main())
