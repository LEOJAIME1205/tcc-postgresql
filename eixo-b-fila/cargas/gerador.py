"""Gerador de carga do Eixo B (ADR-054), no contrato do gerador de harness/README.md.

    gerador.py preparar [--saida DIR]
    gerador.py executar --saida DIR --concorrencia N --aquecimento-s A --medicao-s M --semente S
    gerador.py produzir --produtores N --semente S
    gerador.py consumir --consumidores N --registro ARQUIVO

`preparar` aplica o esquema da arquitetura de TCC_ARQUITETURA e, nas poliglotas, declara a
fila no RabbitMQ (TCC_RABBITMQ). `executar` mantém N produtores e N consumidores em laço
fechado e grava o operacoes.csv e o gerador.json. `produzir` e `consumir` rodam cada metade
até receber SIGTERM; `consumir` grava os pedidos consumidos, um por linha. São usados pela
verificação de equivalência (ADR-056) e pela medida da vazão isolada do relay (ADR-053).
O PostgreSQL é lido pelas variáveis PG* da libpq.
"""

import argparse
import asyncio
import hashlib
import json
import multiprocessing
import os
import platform
import random
import signal
import struct
import subprocess
import sys
import time
from collections import Counter, deque
from typing import Any, Callable, Deque, Dict, List, LiteralString, Optional, Set, Tuple, cast

import aio_pika
import aio_pika.abc
import aio_pika.exceptions
import psycopg
import uvloop

EIXO_B: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARQUITETURAS: Tuple[str, ...] = ("consolidada", "ingenua", "outbox")
POLIGLOTAS: Tuple[str, ...] = ("ingenua", "outbox")
VERSAO: str = "1"
FILA: str = "tarefas"  # fila do RabbitMQ (ADR-052)
CARGA_BYTES: int = 1024
PREFETCH: int = 100
TEMPO_LIMITE_S: float = 60.0
ESPERA_PRONTO_S: float = 300.0
ESPERA_AVISO_S: float = 1.0
PAUSA_APOS_FALHA_S: float = 0.1
ATRASO_INICIO_S: float = 3.0
MAX_PROCESSOS: int = 2
NUCLEOS_GERADOR: Tuple[Set[int], ...] = ({6, 14}, {7, 15})  # um por núcleo físico (ADR-018)
CABECALHO: List[str] = ["ts_us", "operacao", "latencia_us", "ok", "cliente", "itens"]
CABECALHO_MENSAGEM: struct.Struct = struct.Struct(">qq")  # instante Unix em µs, monotônico em ns

# Espera do consumidor diante da fila vazia (ADR-051): "consulta", nova tentativa a cada
# TCC_INTERVALO_MS, sem aviso; ou "aviso" (LISTEN/NOTIFY), só para a medida do teto do aviso.
ESPERA: str = os.environ.get("TCC_ESPERA", "consulta")
INTERVALO_S: float = int(os.environ.get("TCC_INTERVALO_MS", "100")) / 1000
# Pedido e tarefa (ou linha de saída) num só comando e numa só transação (ADR-050).
ENFILEIRA: Dict[Tuple[str, str], LiteralString] = {
    ("consolidada", "aviso"):
        "WITH p AS (INSERT INTO pedidos (cliente, valor) VALUES (%s, %s) RETURNING id), "
        "t AS (INSERT INTO tarefas (pedido, mensagem) SELECT id, %b FROM p RETURNING pedido) "
        "SELECT pedido, pg_notify('tarefas', '') FROM t",
    ("consolidada", "consulta"):
        "WITH p AS (INSERT INTO pedidos (cliente, valor) VALUES (%s, %s) RETURNING id) "
        "INSERT INTO tarefas (pedido, mensagem) SELECT id, %b FROM p",
    ("outbox", "aviso"):
        "WITH p AS (INSERT INTO pedidos (cliente, valor) VALUES (%s, %s) RETURNING id), "
        "s AS (INSERT INTO saida (pedido, mensagem) SELECT id, %b FROM p RETURNING pedido) "
        "SELECT pedido, pg_notify('saida', '') FROM s",
    ("outbox", "consulta"):
        "WITH p AS (INSERT INTO pedidos (cliente, valor) VALUES (%s, %s) RETURNING id) "
        "INSERT INTO saida (pedido, mensagem) SELECT id, %b FROM p",
}
GRAVA_PEDIDO: LiteralString = "INSERT INTO pedidos (cliente, valor) VALUES (%s, %s) RETURNING id"
RETIRA: LiteralString = ("DELETE FROM tarefas WHERE id = (SELECT id FROM tarefas ORDER BY id "
                         "FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING pedido, mensagem")
ESCUTA: LiteralString = "LISTEN tarefas"
PENDENTES: Dict[str, LiteralString] = {
    "consolidada": "SELECT count(*) FROM tarefas",
    "outbox": "SELECT count(*) FROM saida",
}


def avisar(mensagem: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {mensagem}", file=sys.stderr, flush=True)


def arquitetura_do_ambiente() -> str:
    arquitetura = os.environ.get("TCC_ARQUITETURA", "")
    if arquitetura not in ARQUITETURAS:
        raise SystemExit(f"TCC_ARQUITETURA precisa ser {', '.join(ARQUITETURAS)}, não {arquitetura!r}")
    return arquitetura


def url_rabbitmq() -> str:
    return os.environ.get("TCC_RABBITMQ", "amqp://guest:guest@rabbitmq:5672/")


def carga_util(semente: int) -> bytes:
    """1 KiB sorteado pela semente: os mesmos bytes nas três arquiteturas (ADR-050)."""
    return random.Random(semente).randbytes(CARGA_BYTES)


class Registro:
    """Operações gravadas em disco à medida que terminam (ADR-054, item 7)."""

    def __init__(self, caminho: Optional[str]) -> None:
        self._arquivo = open(caminho, "w", encoding="utf-8", buffering=1 << 20) if caminho else None
        self.contagem: Counter[str] = Counter()

    def linha(self, ts_us: int, operacao: str, latencia_us: int, ok: int, cliente: int) -> None:
        self.contagem[operacao if ok else f"{operacao}_erro"] += 1
        if self._arquivo is not None:
            self._arquivo.write(f"{ts_us},{operacao},{latencia_us},{ok},{cliente},1\n")

    def fechar(self) -> None:
        if self._arquivo is not None:
            self._arquivo.close()


class Parada:
    """Fim da carga: um instante, um sinal recebido, ou os dois."""

    def __init__(self, fim: float) -> None:
        self.fim = fim
        self.sinal = asyncio.Event()

    def ativa(self) -> bool:
        return not self.sinal.is_set() and time.time() < self.fim

    def restante(self) -> float:
        return max(0.0, min(ESPERA_AVISO_S, self.fim - time.time()))


class Aviso:
    """Acorda um consumidor ocioso por aviso recebido do PostgreSQL, no modo "aviso".

    O consumidor guarda a geração antes de tentar a retirada; se um aviso chegar entre a
    retirada vazia e a espera, a geração já mudou e ele tenta de novo sem esperar.
    """

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
        if self.geracao != geracao or limite_s <= 0:
            return
        futuro: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._esperando.append(futuro)
        try:
            await asyncio.wait_for(futuro, limite_s)
        except TimeoutError:
            if futuro in self._esperando:
                self._esperando.remove(futuro)


async def escutar(aviso: Aviso, parada: Parada) -> None:
    """Uma conexão por processo escuta o canal das tarefas e repassa cada aviso."""
    async with await psycopg.AsyncConnection.connect(autocommit=True) as conn:
        await conn.execute(ESCUTA)
        while parada.ativa():
            async for _ in conn.notifies(timeout=parada.restante()):
                aviso.avisar()


async def conectar_pg() -> psycopg.AsyncConnection[Tuple[Any, ...]]:
    return await psycopg.AsyncConnection.connect(autocommit=True)


async def produtor_tabela(arquitetura: str, numero: int, semente: int, carga: bytes,
                          parada: Parada, registro: Registro, erros: Counter[str]) -> None:
    """Consolidada e outbox: pedido e tarefa (ou linha de saída) num comando só."""
    sorteio = random.Random(f"{semente}:{numero}")
    comando = ENFILEIRA[(arquitetura, ESPERA)]
    conn = await conectar_pg()
    try:
        while parada.ativa():
            cliente, valor = sorteio.randint(1, 100_000), sorteio.randint(100, 1_000_000)
            ts_us = time.time_ns() // 1000
            t0 = time.monotonic_ns()
            mensagem = CABECALHO_MENSAGEM.pack(ts_us, t0) + carga
            ok = 0
            try:
                async with asyncio.timeout(TEMPO_LIMITE_S):
                    await conn.execute(comando, (cliente, valor, mensagem))
                ok = 1
            except (psycopg.Error, TimeoutError) as erro:
                erros[f"enfileira_{type(erro).__name__}"] += 1
            registro.linha(ts_us, "enfileira", (time.monotonic_ns() - t0) // 1000, ok, numero)
            if not ok:
                await asyncio.sleep(PAUSA_APOS_FALHA_S)
                if conn.broken or conn.closed:
                    conn = await conectar_pg()
    finally:
        await conn.close()


async def produtor_ingenua(numero: int, semente: int, carga: bytes,
                           conexao: aio_pika.abc.AbstractConnection, parada: Parada,
                           registro: Registro, erros: Counter[str]) -> None:
    """Ingênua: o pedido é confirmado no banco e só então publicado no broker (ADR-050)."""
    sorteio = random.Random(f"{semente}:{numero}")
    conn = await conectar_pg()
    canal = await conexao.channel(publisher_confirms=True)
    troca = canal.default_exchange
    try:
        while parada.ativa():
            cliente, valor = sorteio.randint(1, 100_000), sorteio.randint(100, 1_000_000)
            ts_us = time.time_ns() // 1000
            t0 = time.monotonic_ns()
            mensagem = CABECALHO_MENSAGEM.pack(ts_us, t0) + carga
            ok = 0
            try:
                async with asyncio.timeout(TEMPO_LIMITE_S):
                    cur = await conn.execute(GRAVA_PEDIDO, (cliente, valor))
                    linha = await cur.fetchone()
                    if linha is None:
                        raise psycopg.DataError("INSERT sem RETURNING")
                    await troca.publish(
                        aio_pika.Message(mensagem, delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                                         message_id=str(linha[0])),
                        routing_key=FILA)
                ok = 1
            except (psycopg.Error, aio_pika.exceptions.AMQPException, TimeoutError) as erro:
                erros[f"enfileira_{type(erro).__name__}"] += 1
            registro.linha(ts_us, "enfileira", (time.monotonic_ns() - t0) // 1000, ok, numero)
            if not ok:
                await asyncio.sleep(PAUSA_APOS_FALHA_S)
                if conn.broken or conn.closed:
                    conn = await conectar_pg()
    finally:
        await conn.close()
        if not canal.is_closed:
            await canal.close()


async def consumidor_tabela(numero: int, aviso: Aviso, parada: Parada, registro: Registro,
                            erros: Counter[str], vazias: Counter[str],
                            consumidos: Optional[List[int]]) -> None:
    """Consolidada: DELETE ... SKIP LOCKED em autocommit, o commit é a confirmação (ADR-051).
    Com a fila vazia, espera o intervalo de consulta (ou um aviso, no modo "aviso")."""
    conn = await conectar_pg()
    try:
        while parada.ativa():
            geracao = aviso.geracao
            ts_us = time.time_ns() // 1000
            t0 = time.monotonic_ns()
            try:
                async with asyncio.timeout(TEMPO_LIMITE_S):
                    cur = await conn.execute(RETIRA, binary=True)
                    linha = await cur.fetchone()
            except (psycopg.Error, TimeoutError) as erro:
                erros[f"consome_{type(erro).__name__}"] += 1
                registro.linha(ts_us, "consome", (time.monotonic_ns() - t0) // 1000, 0, numero)
                await asyncio.sleep(PAUSA_APOS_FALHA_S)
                if conn.broken or conn.closed:
                    conn = await conectar_pg()
                continue
            if linha is None:
                vazias["retiradas_vazias"] += 1
                if ESPERA == "aviso":
                    await aviso.esperar(geracao, parada.restante())
                else:
                    await asyncio.sleep(min(INTERVALO_S, parada.restante()))
                continue
            agora = time.monotonic_ns()
            pedido, mensagem = cast(Tuple[int, bytes], linha)
            enfileirada_us, enfileirada_ns = CABECALHO_MENSAGEM.unpack_from(mensagem)
            registro.linha(ts_us, "consome", (agora - t0) // 1000, 1, numero)
            registro.linha(enfileirada_us, "fim_a_fim", (agora - enfileirada_ns) // 1000, 1, numero)
            if consumidos is not None:
                consumidos.append(pedido)
    finally:
        await conn.close()


async def consumidor_rabbitmq(numero: int, conexao: aio_pika.abc.AbstractConnection,
                              parada: Parada, registro: Registro, erros: Counter[str],
                              consumidos: Optional[List[int]]) -> None:
    """Poliglotas: confirmação manual, uma por mensagem, prefetch de 100 (ADR-052)."""
    canal = await conexao.channel()
    await canal.set_qos(prefetch_count=PREFETCH)
    fila = await canal.declare_queue(FILA, passive=True)
    entregas: asyncio.Queue[aio_pika.abc.AbstractIncomingMessage] = asyncio.Queue()
    marca = await fila.consume(entregas.put, no_ack=False)
    try:
        while parada.ativa():
            try:
                mensagem = await asyncio.wait_for(entregas.get(), max(0.001, parada.restante()))
            except TimeoutError:
                continue
            ts_us = time.time_ns() // 1000
            t0 = time.monotonic_ns()
            enfileirada_us, enfileirada_ns = CABECALHO_MENSAGEM.unpack_from(mensagem.body)
            try:
                await mensagem.ack()
            except aio_pika.exceptions.AMQPException as erro:
                erros[f"consome_{type(erro).__name__}"] += 1
                registro.linha(ts_us, "consome", (time.monotonic_ns() - t0) // 1000, 0, numero)
                continue
            agora = time.monotonic_ns()
            registro.linha(ts_us, "consome", (agora - t0) // 1000, 1, numero)
            registro.linha(enfileirada_us, "fim_a_fim", (agora - enfileirada_ns) // 1000, 1, numero)
            if consumidos is not None and mensagem.message_id is not None:
                consumidos.append(int(mensagem.message_id))
    finally:
        if not canal.is_closed:
            await fila.cancel(marca)
            await canal.close()


async def carga(arquitetura: str, produtores: List[int], consumidores: List[int], semente: int,
                parada: Parada, inicio: float, registro: Registro,
                consumidos: Optional[List[int]]) -> Tuple[Counter[str], Counter[str]]:
    """Produtores e consumidores de um processo; devolve os erros e as retiradas vazias."""
    erros: Counter[str] = Counter()
    vazias: Counter[str] = Counter()
    bytes_carga = carga_util(semente)
    url = url_rabbitmq()
    # Conexões separadas para publicação e consumo (ADR-052, item 5).
    publicacao = await aio_pika.connect(url) if arquitetura == "ingenua" and produtores else None
    consumo = await aio_pika.connect(url) if arquitetura in POLIGLOTAS and consumidores else None
    aviso = Aviso()
    tarefas: List[asyncio.Task[None]] = []
    try:
        if arquitetura == "consolidada" and consumidores and ESPERA == "aviso":
            tarefas.append(asyncio.create_task(escutar(aviso, parada)))
        await asyncio.sleep(max(0.0, inicio - time.time()))
        for n in produtores:
            if publicacao is not None:
                tarefas.append(asyncio.create_task(produtor_ingenua(
                    n, semente, bytes_carga, publicacao, parada, registro, erros)))
            else:
                tarefas.append(asyncio.create_task(produtor_tabela(
                    arquitetura, n, semente, bytes_carga, parada, registro, erros)))
        for n in consumidores:
            if consumo is not None:
                tarefas.append(asyncio.create_task(consumidor_rabbitmq(
                    n, consumo, parada, registro, erros, consumidos)))
            else:
                tarefas.append(asyncio.create_task(consumidor_tabela(
                    n, aviso, parada, registro, erros, vazias, consumidos)))
        await asyncio.gather(*tarefas)
    finally:
        for tarefa in tarefas:
            tarefa.cancel()
        for conexao in (publicacao, consumo):
            if conexao is not None:
                await conexao.close()
    return erros, vazias


def processo(k: int, arquitetura: str, produtores: List[int], consumidores: List[int],
             cpus: List[int], semente: int, inicio: float, fim: float, pasta: str) -> None:
    """Um processo do gerador: grava a sua parte das operações e as suas contagens."""
    os.sched_setaffinity(0, cpus)
    registro = Registro(os.path.join(pasta, f"operacoes-{k}.csv"))

    async def principal() -> Tuple[Counter[str], Counter[str]]:
        return await carga(arquitetura, produtores, consumidores, semente, Parada(fim), inicio,
                           registro, None)

    try:
        erros, vazias = uvloop.run(principal())
    finally:
        registro.fechar()
    with open(os.path.join(pasta, f"contagens-{k}.json"), "w", encoding="utf-8") as f:
        json.dump({"operacoes": dict(registro.contagem), "erros": dict(erros),
                   "vazias": dict(vazias)}, f)


def grupos_de_cpus(processos: int) -> List[List[int]]:
    """Um conjunto de CPUs por processo: um núcleo físico do gerador, se disponível."""
    disponiveis = os.sched_getaffinity(0)
    grupos = [sorted(n & disponiveis) for n in NUCLEOS_GERADOR[:processos]]
    if any(not g for g in grupos):
        return [sorted(disponiveis)] * processos
    return grupos


async def pronto(arquitetura: str) -> bool:
    """Se o PostgreSQL tem o esquema e, nas poliglotas, se a fila existe no RabbitMQ."""
    try:
        async with await psycopg.AsyncConnection.connect(connect_timeout=2, autocommit=True) as conn:
            cur = await conn.execute("SELECT to_regclass('pedidos') IS NOT NULL")
            linha = await cur.fetchone()
            if linha is None or not linha[0]:
                return False
        if arquitetura in POLIGLOTAS:
            async with await aio_pika.connect(url_rabbitmq(), timeout=2) as conexao:
                canal = await conexao.channel()
                await canal.declare_queue(FILA, passive=True)
        return True
    except (psycopg.Error, aio_pika.exceptions.AMQPException, OSError, TimeoutError):
        return False


async def esperar_pronto(arquitetura: str) -> bool:
    prazo = time.monotonic() + ESPERA_PRONTO_S
    while time.monotonic() < prazo:
        if await pronto(arquitetura):
            return True
        await asyncio.sleep(1.0)
    return False


async def fila_restante(arquitetura: str) -> Dict[str, int]:
    """Mensagens que ficaram no sistema ao fim: tabela de tarefas ou de saída e fila do broker."""
    restante: Dict[str, int] = {}
    async with await psycopg.AsyncConnection.connect(autocommit=True) as conn:
        if arquitetura in PENDENTES:
            cur = await conn.execute(PENDENTES[arquitetura])
            linha = await cur.fetchone()
            restante["postgres"] = int(linha[0]) if linha else -1
    if arquitetura in POLIGLOTAS:
        async with await aio_pika.connect(url_rabbitmq()) as conexao:
            canal = await conexao.channel()
            fila = await canal.declare_queue(FILA, passive=True)
            restante["rabbitmq"] = int(fila.declaration_result.message_count or 0)
    return restante


def ordenar(partes: List[str], destino: str, temporaria: str) -> None:
    """operacoes.csv com cabeçalho e linhas pelo instante, com o sort externo do sistema."""
    with open(destino, "w", encoding="utf-8") as f:
        f.write(",".join(CABECALHO) + "\n")
        f.flush()
        subprocess.run(["sort", "-t", ",", "-k1,1n", "-S", "1G", "-T", temporaria, *partes],
                       stdout=f, check=True, env={**os.environ, "LC_ALL": "C"})


def executar(saida: str, concorrencia: int, aquecimento_s: int, medicao_s: int,
             semente: int) -> int:
    arquitetura = arquitetura_do_ambiente()
    if not asyncio.run(esperar_pronto(arquitetura)):
        avisar(f"a arquitetura {arquitetura} não ficou pronta em {ESPERA_PRONTO_S:.0f} s")
        return 1
    processos = min(MAX_PROCESSOS, concorrencia)
    temporaria = os.path.join(saida, "ferramenta", "gerador-partes")
    os.makedirs(temporaria, exist_ok=True)
    inicio = time.time() + ATRASO_INICIO_S
    fim = inicio + aquecimento_s + medicao_s

    contexto = multiprocessing.get_context("spawn")
    filhos = [contexto.Process(target=processo, args=(
        k, arquitetura, list(range(k, concorrencia, processos)),
        list(range(k, concorrencia, processos)), cpus, semente, inicio, fim, temporaria))
        for k, cpus in enumerate(grupos_de_cpus(processos))]
    for filho in filhos:
        filho.start()
    for filho in filhos:
        filho.join()
    falhos = [k for k, filho in enumerate(filhos) if filho.exitcode != 0]
    if falhos:
        avisar(f"processo(s) {falhos} do gerador terminaram com erro")
        return 1
    processo_fim_ms = time.time_ns() // 1_000_000
    restante = asyncio.run(fila_restante(arquitetura))

    operacoes: Counter[str] = Counter()
    erros: Counter[str] = Counter()
    vazias: Counter[str] = Counter()
    partes = [os.path.join(temporaria, f"operacoes-{k}.csv") for k in range(processos)]
    for k in range(processos):
        caminho = os.path.join(temporaria, f"contagens-{k}.json")
        with open(caminho, encoding="utf-8") as f:
            contagens = cast(Dict[str, Dict[str, int]], json.load(f))
        operacoes.update(contagens["operacoes"])
        erros.update(contagens["erros"])
        vazias.update(contagens["vazias"])
        os.remove(caminho)
    ordenar(partes, os.path.join(saida, "operacoes.csv"), temporaria)
    for parte in partes:
        os.remove(parte)
    os.rmdir(temporaria)

    registro: Dict[str, Any] = {
        "ferramenta": "tcc-gerador-fila",
        "versao": (f"{VERSAO} (psycopg {psycopg.__version__}, aio-pika {aio_pika.__version__}, "
                   f"uvloop {uvloop.__version__}, Python {platform.python_version()})"),
        "aquecimento_inicio_ms": int(inicio * 1000),
        "medicao_inicio_ms": int((inicio + aquecimento_s) * 1000),
        "medicao_fim_ms": int(fim * 1000),
        "processo_fim_ms": processo_fim_ms,
        "parametros": {
            "arquitetura": arquitetura, "concorrencia": concorrencia, "produtores": concorrencia,
            "consumidores": concorrencia, "processos": processos, "semente": semente,
            "aquecimento_s": aquecimento_s, "medicao_s": medicao_s, "carga_bytes": CARGA_BYTES,
            "carga_sha256": hashlib.sha256(carga_util(semente)).hexdigest(),
            "prefetch": PREFETCH if arquitetura in POLIGLOTAS else None,
            "espera": ESPERA if arquitetura != "ingenua" else None,
            "intervalo_ms": round(INTERVALO_S * 1000) if arquitetura != "ingenua" and ESPERA == "consulta" else None,
            "tempo_limite_s": TEMPO_LIMITE_S,
        },
        "operacoes": dict(operacoes),
        "erros": sum(erros.values()),
        "erros_por_tipo": dict(erros),
        "retiradas_vazias": vazias["retiradas_vazias"],
        "fila_restante": restante,
    }
    with open(os.path.join(saida, "gerador.json"), "w", encoding="utf-8") as f:
        json.dump(registro, f, indent=2, ensure_ascii=False)
        f.write("\n")
    avisar(f"{dict(operacoes)}, {registro['erros']} erro(s), restante {restante}")
    return 0


async def aplicar_esquema(arquitetura: str) -> Dict[str, Any]:
    """Aplica o esquema.sql da arquitetura num banco vazio e, nas poliglotas, declara a fila."""
    caminho = os.path.join(EIXO_B, arquitetura, "esquema.sql")
    with open(caminho, encoding="utf-8") as f:
        esquema = f.read()
    async with await psycopg.AsyncConnection.connect(autocommit=True) as conn:
        cur = await conn.execute("SELECT to_regclass('pedidos') IS NOT NULL")
        linha = await cur.fetchone()
        if linha is None or linha[0]:
            raise RuntimeError("a tabela pedidos já existe: o preparo parte de um banco vazio")
        await conn.execute(cast(LiteralString, esquema))
        cur = await conn.execute("SELECT version()")
        versao = await cur.fetchone()
    registro: Dict[str, Any] = {
        "arquitetura": arquitetura, "esquema": os.path.relpath(caminho, EIXO_B),
        "esquema_sha256": hashlib.sha256(esquema.encode()).hexdigest(),
        "postgres": versao[0] if versao else None,
    }
    if arquitetura in POLIGLOTAS:
        async with await aio_pika.connect(url_rabbitmq()) as conexao:
            canal = await conexao.channel()
            await canal.declare_queue(FILA, durable=True, arguments={"x-queue-type": "quorum"})
            transporte = conexao.transport
            if transporte is not None:
                propriedades = cast(Dict[str, Any], transporte.connection.server_properties)
                registro["rabbitmq"] = str(propriedades.get("version", ""))
            registro["fila"] = {"nome": FILA, "tipo": "quorum", "duravel": True}
    registro["concluido_em"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    return registro


def preparar(saida: str) -> int:
    arquitetura = arquitetura_do_ambiente()

    async def principal() -> Optional[Dict[str, Any]]:
        # O Compose sobe os containers sem esperar os sistemas aceitarem conexões.
        prazo = time.monotonic() + ESPERA_PRONTO_S
        while time.monotonic() < prazo:
            try:
                async with await psycopg.AsyncConnection.connect(connect_timeout=2):
                    pass
                if arquitetura in POLIGLOTAS:
                    async with await aio_pika.connect(url_rabbitmq(), timeout=2):
                        pass
                return await aplicar_esquema(arquitetura)
            except (psycopg.OperationalError, aio_pika.exceptions.AMQPConnectionError,
                    OSError, TimeoutError):
                await asyncio.sleep(1.0)
        return None

    registro = asyncio.run(principal())
    if registro is None:
        avisar(f"os sistemas da arquitetura {arquitetura} não ficaram prontos em {ESPERA_PRONTO_S:.0f} s")
        return 1
    with open(os.path.join(saida, "carga.json"), "w", encoding="utf-8") as f:
        json.dump(registro, f, indent=2, ensure_ascii=False)
        f.write("\n")
    avisar(f"esquema de {arquitetura} aplicado")
    return 0


def metade(produtores: int, consumidores: int, semente: int,
           gravar: Optional[Callable[[List[int]], None]]) -> int:
    """Produtores ou consumidores, num processo, até SIGTERM (ADR-053 e ADR-056)."""
    arquitetura = arquitetura_do_ambiente()
    consumidos: List[int] = []

    async def principal() -> None:
        if not await esperar_pronto(arquitetura):
            raise SystemExit(f"a arquitetura {arquitetura} não ficou pronta")
        parada = Parada(float("inf"))
        laco = asyncio.get_running_loop()
        for sinal in (signal.SIGTERM, signal.SIGINT):
            laco.add_signal_handler(sinal, parada.sinal.set)
        registro = Registro(None)
        await carga(arquitetura, list(range(produtores)), list(range(consumidores)), semente,
                    parada, time.time(), registro, consumidos)
        avisar(f"{dict(registro.contagem)}")

    uvloop.run(principal())
    if gravar is not None:
        gravar(consumidos)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Gerador de carga do Eixo B")
    sub = parser.add_subparsers(dest="comando", required=True)
    pp = sub.add_parser("preparar", help="aplica o esquema da arquitetura de TCC_ARQUITETURA")
    pp.add_argument("--saida", default="/saida")
    pe = sub.add_parser("executar", help="aquecimento e medição com N produtores e N consumidores")
    pe.add_argument("--saida", required=True)
    pe.add_argument("--concorrencia", type=int, required=True)
    pe.add_argument("--aquecimento-s", type=int, required=True)
    pe.add_argument("--medicao-s", type=int, required=True)
    pe.add_argument("--semente", type=int, required=True)
    pr = sub.add_parser("produzir", help="só produtores, até SIGTERM")
    pr.add_argument("--produtores", type=int, required=True)
    pr.add_argument("--semente", type=int, required=True)
    pc = sub.add_parser("consumir", help="só consumidores, até SIGTERM; grava os pedidos consumidos")
    pc.add_argument("--consumidores", type=int, required=True)
    pc.add_argument("--registro", required=True)
    args = parser.parse_args()

    comando = cast(str, args.comando)
    if comando == "preparar":
        return preparar(cast(str, args.saida))
    if comando == "executar":
        concorrencia = cast(int, args.concorrencia)
        if concorrencia < 1:
            avisar("--concorrencia precisa ser pelo menos 1")
            return 2
        return executar(cast(str, args.saida), concorrencia, cast(int, args.aquecimento_s),
                        cast(int, args.medicao_s), cast(int, args.semente))
    if comando == "produzir":
        return metade(cast(int, args.produtores), 0, cast(int, args.semente), None)
    caminho = cast(str, args.registro)

    def gravar(consumidos: List[int]) -> None:
        with open(caminho, "w", encoding="utf-8") as f:
            f.writelines(f"{pedido}\n" for pedido in consumidos)

    return metade(0, cast(int, args.consumidores), 0, gravar)


if __name__ == "__main__":
    sys.exit(main())
