"""Relay da outbox do Eixo B (ADR-053): publica no RabbitMQ as linhas da tabela de saída.

Cada trabalhador retira até TCC_LOTE linhas com SKIP LOCKED, publica com confirmação do
broker e só então faz o commit; se a publicação falhar, a transação é desfeita e as linhas
voltam à tabela. Com a tabela vazia, o trabalhador tenta de novo a cada TCC_INTERVALO_MS;
no modo "aviso", uma conexão escuta os avisos da tabela de saída e acorda um trabalhador
ocioso por aviso. O PostgreSQL é lido pelas variáveis PG* da libpq, e o RabbitMQ por
TCC_RABBITMQ. A cada 10 s, imprime os lotes e as mensagens publicadas desde o início.
"""

import asyncio
import os
import signal
import sys
import time
from collections import deque
from typing import Deque, List, LiteralString, Tuple, cast

import aio_pika
import aio_pika.abc
import psycopg
import uvloop

FILA: str = "tarefas"
TRABALHADORES: int = int(os.environ.get("TCC_TRABALHADORES", "2"))
LOTE: int = int(os.environ.get("TCC_LOTE", "100"))
ESPERA: str = os.environ.get("TCC_ESPERA", "consulta")  # ou "aviso" (LISTEN/NOTIFY), ADR-053
INTERVALO_S: float = int(os.environ.get("TCC_INTERVALO_MS", "100")) / 1000
TEMPO_LIMITE_S: float = 60.0
ESPERA_AVISO_S: float = 1.0
ESPERA_RECONEXAO_S: float = 1.0
INTERVALO_RELATORIO_S: float = 10.0
RETIRA_LOTE: LiteralString = ("DELETE FROM saida WHERE id = ANY(ARRAY(SELECT id FROM saida ORDER BY id "
                              "LIMIT %s FOR UPDATE SKIP LOCKED)) RETURNING id, pedido, mensagem")
ESCUTA: LiteralString = "LISTEN saida"


def avisar(mensagem: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {mensagem}", flush=True)


class Aviso:
    """Acorda um trabalhador ocioso por aviso, no modo "aviso"."""

    def __init__(self) -> None:
        self.geracao = 0
        self._esperando: Deque[asyncio.Future[None]] = deque()

    def avisar(self) -> None:
        self.geracao += 1
        while self._esperando:
            futuro = self._esperando.popleft()
            if not futuro.done():
                futuro.set_result(None)
                return

    async def esperar(self, geracao: int, limite_s: float) -> None:
        if self.geracao != geracao:
            return
        futuro: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._esperando.append(futuro)
        try:
            await asyncio.wait_for(futuro, limite_s)
        except TimeoutError:
            if futuro in self._esperando:
                self._esperando.remove(futuro)


class Contagem:
    def __init__(self) -> None:
        self.lotes = 0
        self.mensagens = 0


async def escutar(aviso: Aviso, parar: asyncio.Event) -> None:
    async with await psycopg.AsyncConnection.connect(autocommit=True) as conn:
        await conn.execute(ESCUTA)
        while not parar.is_set():
            async for _ in conn.notifies(timeout=ESPERA_AVISO_S):
                aviso.avisar()


async def trabalhador(conexao: aio_pika.abc.AbstractConnection, aviso: Aviso,
                      parar: asyncio.Event, contagem: Contagem) -> None:
    """Retira, publica e confirma, até o sinal de parada; o lote em curso termina antes."""
    canal = await conexao.channel(publisher_confirms=True)
    troca = canal.default_exchange
    async with await psycopg.AsyncConnection.connect(autocommit=True) as conn:
        while not parar.is_set():
            geracao = aviso.geracao
            async with conn.transaction():
                cur = await conn.execute(RETIRA_LOTE, (LOTE,), binary=True)
                linhas = cast(List[Tuple[int, int, bytes]], await cur.fetchall())
                if linhas:
                    linhas.sort()
                    async with asyncio.timeout(TEMPO_LIMITE_S):
                        await asyncio.gather(*(
                            troca.publish(aio_pika.Message(
                                mensagem, delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                                message_id=str(pedido)), routing_key=FILA)
                            for _, pedido, mensagem in linhas))
            if linhas:
                contagem.lotes += 1
                contagem.mensagens += len(linhas)
            elif ESPERA == "aviso":
                await aviso.esperar(geracao, ESPERA_AVISO_S)
            else:
                await asyncio.sleep(INTERVALO_S)
    await canal.close()


async def relatar(contagem: Contagem, parar: asyncio.Event) -> None:
    while not parar.is_set():
        try:
            await asyncio.wait_for(parar.wait(), INTERVALO_RELATORIO_S)
        except TimeoutError:
            pass
        avisar(f"lotes={contagem.lotes} mensagens={contagem.mensagens}")


async def servir(parar: asyncio.Event, contagem: Contagem) -> None:
    """Uma conexão ao RabbitMQ, um canal por trabalhador e a escuta dos avisos."""
    async with await aio_pika.connect(os.environ.get("TCC_RABBITMQ", "amqp://guest:guest@rabbitmq:5672/")) as conexao:
        aviso = Aviso()
        avisar(f"conectado: {TRABALHADORES} trabalhador(es), lotes de até {LOTE} linhas, espera por {ESPERA}"
               + (f" a cada {INTERVALO_S * 1000:.0f} ms" if ESPERA == "consulta" else ""))
        async with asyncio.TaskGroup() as grupo:
            if ESPERA == "aviso":
                grupo.create_task(escutar(aviso, parar))
            for _ in range(TRABALHADORES):
                grupo.create_task(trabalhador(conexao, aviso, parar, contagem))


def descrever(erro: BaseException) -> str:
    """As falhas de dentro de um grupo de tarefas, uma a uma."""
    if isinstance(erro, BaseExceptionGroup):
        return "; ".join(descrever(e) for e in cast(Tuple[BaseException, ...], erro.exceptions))
    return repr(erro)


async def principal() -> None:
    parar = asyncio.Event()
    laco = asyncio.get_running_loop()
    for sinal in (signal.SIGTERM, signal.SIGINT):
        laco.add_signal_handler(sinal, parar.set)
    contagem = Contagem()
    relatorio = asyncio.create_task(relatar(contagem, parar))
    while not parar.is_set():
        try:
            await servir(parar, contagem)
        except Exception as erro:  # o relay volta sozinho: banco ou broker fora do ar
            avisar(f"falha: {descrever(erro)}; nova tentativa em {ESPERA_RECONEXAO_S:.0f} s")
            await asyncio.sleep(ESPERA_RECONEXAO_S)
    await relatorio
    avisar(f"encerrado: lotes={contagem.lotes} mensagens={contagem.mensagens}")


if __name__ == "__main__":
    uvloop.run(principal())
    sys.exit(0)
