"""Verificação de equivalência do Eixo B (ADR-056).

    uv run python eixo-b-fila/cargas/equivalencia.py <arquitetura> --registro ARQUIVO.json

Roda com a arquitetura no ar e recém-preparada (tabela de pedidos vazia). Produtores e
consumidores rodam no host, como processos do gerador.py, contra o projeto tcc-b-<arquitetura>.
Os produtores são derrubados com SIGKILL aos 15 s e aos 35 s e religados 2 s depois; na
outbox, o relay também é derrubado aos 25 s e religado 5 s depois. Aos 60 s os produtores
param. Quando a fila esvazia, os consumidores param, e os pedidos gravados no PostgreSQL são
comparados com os pedidos consumidos.

A saída dos processos do gerador vai para um .log ao lado do registro.

Critério: na consolidada e na outbox, nenhum pedido perdido nem consumido sem existir; na
consolidada, também nenhuma duplicata. Na ingênua, o registro mostra as perdas da escrita
dupla. Código de saída 0 quando o critério vale (sempre, na ingênua), 1 quando não vale.
"""

import argparse
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections import Counter
from typing import Any, Callable, Dict, List, LiteralString, Optional, Tuple, cast

import psycopg

from apoio import (CPUS_GERADOR, TABELA_PENDENTE, container, conninfo, docker, gerador,
                   gravar_json, mensagens_rabbitmq, rodando)

ARQUITETURAS: Tuple[str, ...] = ("consolidada", "ingenua", "outbox")
PRODUTORES: int = 8
CONSUMIDORES: int = 8
SEMENTE: int = 20261108
DURACAO_S: float = 60.0
ESVAZIAMENTO_LIMITE_S: float = 300.0
ESTAVEL_S: float = 6.0  # a API do RabbitMQ atualiza as filas a cada 5 s
PENDENTES: Dict[str, LiteralString] = {
    "tarefas": "SELECT count(*) FROM tarefas",
    "saida": "SELECT count(*) FROM saida",
}


def avisar(mensagem: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {mensagem}", flush=True)


def contar(conn: psycopg.Connection[Tuple[Any, ...]], comando: LiteralString) -> int:
    linha = conn.execute(comando).fetchone()
    return int(linha[0]) if linha else -1


def esvaziada(arquitetura: str, conn: psycopg.Connection[Tuple[Any, ...]]) -> bool:
    """Tabela de tarefas ou de saída vazia e fila do broker sem mensagens."""
    tabela = TABELA_PENDENTE.get(arquitetura)
    if tabela is not None and contar(conn, PENDENTES[tabela]) != 0:
        return False
    return arquitetura == "consolidada" or mensagens_rabbitmq(arquitetura) == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Verificação de equivalência do Eixo B (ADR-056)")
    parser.add_argument("arquitetura", choices=ARQUITETURAS)
    parser.add_argument("--registro", required=True, help="arquivo JSON com o resultado")
    args = parser.parse_args()
    arquitetura = cast(str, args.arquitetura)
    os.sched_setaffinity(0, CPUS_GERADOR)  # os processos filhos herdam (ADR-018)

    servicos = ["postgres"] + (["rabbitmq"] if arquitetura != "consolidada" else [])
    servicos += ["relay"] if arquitetura == "outbox" else []
    fora = [s for s in servicos if not rodando(container(arquitetura, s))]
    if fora:
        print(f"Recusado: fora do ar: {', '.join(fora)}", file=sys.stderr)
        return 1
    conn = psycopg.connect(conninfo(arquitetura), autocommit=True)
    if contar(conn, "SELECT count(*) FROM pedidos") != 0:
        print("Recusado: a tabela pedidos não está vazia; rode sobre uma arquitetura recém-preparada",
              file=sys.stderr)
        return 1

    caminho_registro = cast(str, args.registro)
    log = os.path.splitext(caminho_registro)[0] + ".log"
    os.makedirs(os.path.dirname(os.path.abspath(log)), exist_ok=True)
    consumidos_arquivo = os.path.join(tempfile.mkdtemp(prefix="equivalencia-b-"), "consumidos.txt")
    relay = container(arquitetura, "relay")
    eventos: List[Dict[str, Any]] = []
    inicio = time.monotonic()

    def registrar(evento: str) -> None:
        eventos.append({"t_s": round(time.monotonic() - inicio, 1), "evento": evento})
        avisar(evento)

    def produtores() -> "subprocess.Popen[bytes]":
        return gerador(["produzir", "--produtores", str(PRODUTORES), "--semente", str(SEMENTE)],
                       arquitetura, log)

    consumidores = gerador(["consumir", "--consumidores", str(CONSUMIDORES),
                            "--registro", consumidos_arquivo], arquitetura, log)
    producao = produtores()
    registrar(f"{PRODUTORES} produtores e {CONSUMIDORES} consumidores iniciados")

    def derrubar_produtores() -> None:
        producao.kill()
        producao.wait()
        registrar("produtores derrubados (SIGKILL)")

    def religar_produtores() -> None:
        nonlocal producao
        producao = produtores()
        registrar("produtores religados")

    def derrubar_relay() -> None:
        r = docker(["kill", "-s", "KILL", relay])
        registrar(f"relay derrubado (SIGKILL): código {r.returncode}")

    def religar_relay() -> None:
        r = docker(["start", relay])
        registrar(f"relay religado: código {r.returncode}")

    roteiro: List[Tuple[float, Callable[[], None]]] = [
        (15.0, derrubar_produtores), (17.0, religar_produtores),
        (35.0, derrubar_produtores), (37.0, religar_produtores),
    ]
    if arquitetura == "outbox":
        roteiro += [(25.0, derrubar_relay), (30.0, religar_relay)]
    roteiro.sort(key=lambda passo: passo[0])

    try:
        for instante, acao in roteiro:
            time.sleep(max(0.0, instante - (time.monotonic() - inicio)))
            acao()
        time.sleep(max(0.0, DURACAO_S - (time.monotonic() - inicio)))
        producao.send_signal(signal.SIGTERM)
        producao.wait(timeout=60)
        registrar("produtores encerrados (SIGTERM)")

        prazo = time.monotonic() + ESVAZIAMENTO_LIMITE_S
        vazia_desde: Optional[float] = None
        while time.monotonic() < prazo:
            if esvaziada(arquitetura, conn):
                vazia_desde = vazia_desde or time.monotonic()
                if time.monotonic() - vazia_desde >= ESTAVEL_S:
                    break
            else:
                vazia_desde = None
            time.sleep(1.0)
        esvaziou = vazia_desde is not None and time.monotonic() - vazia_desde >= ESTAVEL_S
        registrar("fila vazia" if esvaziou else f"a fila não esvaziou em {ESVAZIAMENTO_LIMITE_S:.0f} s")
    finally:
        for processo in (producao, consumidores):
            if processo.poll() is None:
                processo.send_signal(signal.SIGTERM)
        consumidores.wait(timeout=60)
        if arquitetura == "outbox" and not rodando(relay):
            docker(["start", relay])

    with open(consumidos_arquivo, encoding="utf-8") as f:
        consumidos = [int(linha) for linha in f if linha.strip()]
    pedidos = {int(linha[0]) for linha in conn.execute("SELECT id FROM pedidos").fetchall()}
    conn.close()
    contagem = Counter(consumidos)
    distintos = set(contagem)
    perdidos = sorted(pedidos - distintos)
    orfaos = sorted(distintos - pedidos)
    duplicatas = len(consumidos) - len(distintos)
    if arquitetura == "consolidada":
        aprovado: Optional[bool] = esvaziou and not perdidos and not orfaos and duplicatas == 0
        criterio = "nenhum pedido perdido, nenhuma mensagem órfã, nenhuma duplicata"
    elif arquitetura == "outbox":
        aprovado = esvaziou and not perdidos and not orfaos
        criterio = "nenhum pedido perdido, nenhuma mensagem órfã; duplicatas permitidas e contadas"
    else:
        aprovado = None
        criterio = "sem garantia: o teste registra as perdas da escrita dupla"

    registro: Dict[str, Any] = {
        "arquitetura": arquitetura, "concluido_em": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "produtores": PRODUTORES, "consumidores": CONSUMIDORES, "semente": SEMENTE,
        "duracao_s": DURACAO_S, "eventos": eventos, "esvaziou": esvaziou,
        "pedidos": len(pedidos), "consumidos": len(consumidos), "consumidos_distintos": len(distintos),
        "perdidos": len(perdidos), "orfaos": len(orfaos), "duplicatas": duplicatas,
        "maximo_de_entregas": max(contagem.values(), default=0),
        "exemplos_perdidos": perdidos[:10], "exemplos_orfaos": orfaos[:10],
        "criterio": criterio, "aprovado": aprovado,
    }
    gravar_json(caminho_registro, registro)
    avisar(f"{len(pedidos)} pedidos, {len(consumidos)} consumidos ({len(distintos)} distintos): "
           f"{len(perdidos)} perdido(s), {len(orfaos)} órfão(s), {duplicatas} duplicata(s); "
           f"aprovado: {aprovado}; log do gerador em {log}")
    return 0 if aprovado is not False else 1


if __name__ == "__main__":
    sys.exit(main())
