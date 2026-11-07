"""Serviço de busca híbrida do Eixo A, com a mesma interface nas duas arquiteturas (ADR-046).

O motor vem da variável TCC_MOTOR: "consolidada" (PostgreSQL) ou "poliglota" (OpenSearch e
Qdrant). Cada processo do Uvicorn importa este módulo e abre os seus próprios pools.
"""

import os
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict, List, Tuple, cast

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from consolidada import Consolidada
from motor import DIMENSAO, Motor
from poliglota import Poliglota


def criar_motor(nome: str) -> Motor:
    if nome == "consolidada":
        return Consolidada()
    if nome == "poliglota":
        return Poliglota()
    raise ValueError(f"TCC_MOTOR desconhecido: {nome!r}")


MOTOR: Motor = criar_motor(os.environ.get("TCC_MOTOR", ""))


def ler_pedido(dados: Any) -> Tuple[str, List[float]]:
    """Valida o corpo de /buscar: um texto e um vetor de DIMENSAO números."""
    if not isinstance(dados, dict):
        raise ValueError("o corpo precisa ser um objeto JSON")
    pedido = cast(Dict[str, Any], dados)
    texto = pedido.get("texto")
    if not isinstance(texto, str) or not texto.strip():
        raise ValueError("'texto' precisa ser um texto não vazio")
    vetor = pedido.get("vetor")
    if not isinstance(vetor, list):
        raise ValueError(f"'vetor' precisa ser uma lista de {DIMENSAO} números")
    numeros = cast(List[Any], vetor)
    if len(numeros) != DIMENSAO or not all(isinstance(x, (int, float)) for x in numeros):
        raise ValueError(f"'vetor' precisa ser uma lista de {DIMENSAO} números")
    return texto, [float(x) for x in numeros]


async def buscar(request: Request) -> Response:
    try:
        texto, vetor = ler_pedido(await request.json())
    except ValueError as erro:
        return JSONResponse({"erro": str(erro)}, status_code=400)
    resultado = await MOTOR.buscar(texto, vetor)
    return JSONResponse({"ids": [i for i, _ in resultado], "pontuacoes": [p for _, p in resultado]})


async def saude(_: Request) -> Response:
    try:
        pronto = await MOTOR.pronto()
    except Exception as erro:
        return JSONResponse({"pronto": False, "erro": str(erro)}, status_code=503)
    return JSONResponse({"pronto": pronto}, status_code=200 if pronto else 503)


@asynccontextmanager
async def ciclo(_: Starlette) -> AsyncGenerator[None, None]:
    await MOTOR.abrir()
    try:
        yield
    finally:
        await MOTOR.fechar()


app = Starlette(routes=[Route("/buscar", buscar, methods=["POST"]), Route("/saude", saude)],
                lifespan=ciclo)
