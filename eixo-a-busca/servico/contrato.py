"""Teste de contrato do serviço de busca do Eixo A (ADR-046, item 7).

Com uma arquitetura no ar (projeto do Compose `tcc-a-<arquitetura>`), confere:

1. que o serviço está pronto (`/saude`);
2. o formato de cada resposta: 10 identificadores distintos e válidos, 10 pontuações,
   em ordem decrescente de pontuação e, nos empates, de identificador;
3. que pedidos inválidos são recusados com 400 (e método errado com 405);
4. que cada resposta é idêntica, em identificadores e pontuações exatas, à fusão em Python
   (`motor.fundir`) dos dois ramos buscados direto nos motores, com o mesmo `ef` do serviço.
   Na consolidada, isso compara a consulta SQL única com a fusão em Python.

As consultas são uma amostra fixa da NQ, a mesma nas duas arquiteturas.
"""

import argparse
import json
import os
import random
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, LiteralString, Optional, Tuple, cast

import numpy as np
import numpy.typing as npt
import psycopg
from opensearchpy import OpenSearch
from pgvector.psycopg import register_vector
from qdrant_client import QdrantClient
from qdrant_client.models import SearchParams

from motor import DIMENSAO, POR_RAMO, RESPOSTA, Resultado, fundir

DADOS: str = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dados")
SEMENTE: int = 20261107
AMOSTRA: int = 100
EXEMPLOS: int = 5

LEXICO_PG: LiteralString = (
    "SELECT p.id FROM passagens p, "
    "CAST(replace(plainto_tsquery('english', %s)::text, ' & ', ' | ') AS tsquery) AS q "
    "WHERE p.lexico @@ q ORDER BY ts_rank_cd(p.lexico, q) DESC, p.id LIMIT 100")
VETORIAL_PG: LiteralString = "SELECT id FROM passagens ORDER BY embedding <#> %s LIMIT 100"

INVALIDOS: List[Tuple[str, bytes]] = [
    ("sem vetor", b'{"texto": "who wrote hamlet"}'),
    ("vetor curto", json.dumps({"texto": "x", "vetor": [0.1] * 10}).encode()),
    ("vetor com texto", json.dumps({"texto": "x", "vetor": ["a"] * DIMENSAO}).encode()),
    ("texto vazio", json.dumps({"texto": "  ", "vetor": [0.0] * DIMENSAO}).encode()),
    ("corpo que não é objeto", b"[1, 2, 3]"),
    ("JSON quebrado", b"{texto"),
]

Ramos = Callable[[str, npt.NDArray[np.float32]], Tuple[List[int], List[int]]]


def docker_inspect(container: str, formato: str) -> str:
    r = subprocess.run(["docker", "inspect", "-f", formato, container],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"container {container} não encontrado: {r.stderr.strip()}")
    return r.stdout.strip()


def ip(container: str) -> str:
    return docker_inspect(container, "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}")


def ambiente(container: str) -> Dict[str, str]:
    """Variáveis de ambiente com que o container foi criado."""
    variaveis: Dict[str, str] = {}
    for linha in cast(List[str], json.loads(docker_inspect(container, "{{json .Config.Env}}"))):
        chave, _, valor = linha.partition("=")
        variaveis[chave] = valor
    return variaveis


def pedir(url: str, corpo: Optional[bytes] = None) -> Tuple[int, Any]:
    """Status e corpo JSON de um pedido ao serviço; POST quando há corpo."""
    req = urllib.request.Request(url, data=corpo, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resposta:
            return resposta.status, json.loads(resposta.read())
    except urllib.error.HTTPError as erro:
        conteudo = erro.read()
        try:
            return erro.code, json.loads(conteudo)
        except ValueError:
            return erro.code, conteudo.decode(errors="replace")


def problemas_formato(corpo: Any, total: int) -> List[str]:
    if not isinstance(corpo, dict):
        return ["a resposta não é um objeto JSON"]
    resposta = cast(Dict[str, Any], corpo)
    ids = resposta.get("ids")
    pontuacoes = resposta.get("pontuacoes")
    if not isinstance(ids, list) or not isinstance(pontuacoes, list):
        return ["faltam as listas 'ids' e 'pontuacoes'"]
    lista_ids = cast(List[Any], ids)
    lista_pontos = cast(List[Any], pontuacoes)
    problemas: List[str] = []
    if len(lista_ids) != RESPOSTA or len(lista_pontos) != RESPOSTA:
        problemas.append(f"{len(lista_ids)} ids e {len(lista_pontos)} pontuações, esperados {RESPOSTA}")
    if not all(isinstance(i, int) and 0 <= i < total for i in lista_ids):
        problemas.append("identificador fora do corpus ou não inteiro")
    if len(set(lista_ids)) != len(lista_ids):
        problemas.append("identificador repetido")
    if not all(isinstance(p, float) for p in lista_pontos):
        problemas.append("pontuação não numérica")
    elif lista_pontos != sorted(lista_pontos, reverse=True):
        problemas.append("pontuações fora de ordem decrescente")
    else:
        pares = list(zip(lista_pontos, lista_ids))
        if pares != sorted(pares, key=lambda par: (-par[0], par[1])):
            problemas.append("empate não resolvido pelo menor id")
    return problemas


def ramos_consolidada(ef: int) -> Tuple[Ramos, Callable[[], None]]:
    conn = psycopg.connect(host=ip("tcc-a-consolidada-postgres-1"), user="postgres",
                           password="tcc", dbname="busca", autocommit=True,
                           prepare_threshold=None)
    register_vector(conn)
    conn.execute("SELECT set_config('hnsw.ef_search', %s, false)", (str(ef),))

    def ramos(texto: str, vetor: npt.NDArray[np.float32]) -> Tuple[List[int], List[int]]:
        lexico = [int(i) for (i,) in conn.execute(LEXICO_PG, (texto,)).fetchall()]
        vetorial = [int(i) for (i,) in conn.execute(VETORIAL_PG, (vetor,)).fetchall()]
        return lexico, vetorial

    return ramos, conn.close


def ramos_poliglota(ef: int) -> Tuple[Ramos, Callable[[], None]]:
    lexical = OpenSearch(hosts=[{"host": ip("tcc-a-poliglota-opensearch-1"), "port": 9200}],
                         timeout=120)
    vetorial = QdrantClient(host=ip("tcc-a-poliglota-qdrant-1"), prefer_grpc=True, timeout=120)

    def ramos(texto: str, vetor: npt.NDArray[np.float32]) -> Tuple[List[int], List[int]]:
        corpo = {"query": {"match": {"texto": texto}}, "size": POR_RAMO, "_source": False}
        resposta = lexical.search(index="passagens", body=corpo)
        pontos = vetorial.query_points("passagens", query=vetor.tolist(), limit=POR_RAMO,
                                       search_params=SearchParams(hnsw_ef=ef)).points
        return [int(h["_id"]) for h in resposta["hits"]["hits"]], [int(p.id) for p in pontos]

    def fechar() -> None:
        lexical.close()
        vetorial.close()

    return ramos, fechar


def main() -> int:
    parser = argparse.ArgumentParser(description="Teste de contrato do serviço de busca do Eixo A")
    parser.add_argument("arquitetura", choices=["consolidada", "poliglota"])
    parser.add_argument("--registro", help="arquivo JSON com o resultado de cada verificação")
    args = parser.parse_args()
    arquitetura = cast(str, args.arquitetura)
    caminho_registro = cast(Optional[str], args.registro)

    servico = f"tcc-a-{arquitetura}-busca-1"
    try:
        base = f"http://{ip(servico)}:8000"
        variaveis = ambiente(servico)
    except RuntimeError as erro:
        print(f"{erro}; suba a arquitetura antes do teste", file=sys.stderr)
        return 1
    if variaveis.get("TCC_MOTOR") != arquitetura:
        print(f"{servico} roda o motor {variaveis.get('TCC_MOTOR')!r}", file=sys.stderr)
        return 1
    ef = int(variaveis.get("TCC_EF_BUSCA", "100"))

    vetores: npt.NDArray[np.float32] = np.load(os.path.join(DADOS, "consultas.npy"))
    with open(os.path.join(DADOS, "nq", "queries.jsonl"), encoding="utf-8") as f:
        textos = [cast(str, json.loads(linha)["text"]) for linha in f]
    total = len(np.load(os.path.join(DADOS, "passagens.npy"), mmap_mode="r"))
    amostra = random.Random(SEMENTE).sample(range(len(textos)), AMOSTRA)

    falhas: Dict[str, List[str]] = {"saude": [], "formato": [], "invalidos": [], "fusao": []}
    status, corpo = pedir(f"{base}/saude")
    if status != 200:
        falhas["saude"].append(f"/saude respondeu {status}: {corpo}")

    for nome, pedido in INVALIDOS:
        status, corpo = pedir(f"{base}/buscar", pedido)
        if status != 400:
            falhas["invalidos"].append(f"{nome}: {status} {corpo}")
    status, corpo = pedir(f"{base}/buscar")
    if status != 405:
        falhas["invalidos"].append(f"GET em /buscar: {status}, esperado 405")

    ramos, fechar = ramos_consolidada(ef) if arquitetura == "consolidada" else ramos_poliglota(ef)
    respostas: Dict[str, List[int]] = {}
    inicio = time.monotonic()
    try:
        for q in amostra:
            pedido = json.dumps({"texto": textos[q], "vetor": vetores[q].tolist()}).encode()
            status, corpo = pedir(f"{base}/buscar", pedido)
            if status != 200:
                falhas["formato"].append(f"consulta {q}: status {status} {corpo}")
                continue
            problemas = problemas_formato(corpo, total)
            if problemas:
                falhas["formato"].append(f"consulta {q}: {'; '.join(problemas)}")
                continue
            resposta = cast(Dict[str, List[Any]], corpo)
            obtido: Resultado = list(zip(resposta["ids"], resposta["pontuacoes"]))
            respostas[str(q)] = [i for i, _ in obtido]
            esperado = fundir(*ramos(textos[q], vetores[q]))
            if obtido != esperado:
                falhas["fusao"].append(f"consulta {q}: serviço {obtido[:3]}…, fusão {esperado[:3]}…")
    finally:
        fechar()

    for nome, lista in falhas.items():
        marca = "OK   " if not lista else "FALHA"
        print(f"[{marca}] {nome}: {len(lista)} falha(s)")
        for exemplo in lista[:EXEMPLOS]:
            print(f"        {exemplo}")
    print(f"{AMOSTRA} consultas em {time.monotonic() - inicio:.0f} s")

    if caminho_registro:
        registro: Dict[str, Any] = {
            "arquitetura": arquitetura, "imagem_servico": docker_inspect(servico, "{{.Image}}"),
            "ef": ef, "semente": SEMENTE, "consultas": amostra, "falhas": falhas,
            "respostas": respostas, "concluido_em": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        with open(caminho_registro, "w", encoding="utf-8") as f:
            json.dump(registro, f, indent=2, ensure_ascii=False)
            f.write("\n")
    return 0 if not any(falhas.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
