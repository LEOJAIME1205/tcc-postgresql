"""Calibração do esforço de busca vetorial (ADR-038, item 3, e ADR-067, item 2).

    uv run python eixo-a-busca/preparo/calibrar_ef.py vizinhos
    uv run python eixo-a-busca/preparo/calibrar_ef.py varrer consolidada
    uv run python eixo-a-busca/preparo/calibrar_ef.py varrer poliglota

`vizinhos` calcula os 100 vizinhos exatos de cada consulta por produto interno: o
cálculo é feito em float32 e os 200 melhores de cada consulta são reordenados em
float64, com empate pelo menor identificador. `varrer` mede o recall@100 médio da
busca vetorial de cada sistema contra esses vizinhos, de 10 em 10 a partir de 100, e
escolhe o menor `ef` com recall de pelo menos 0,95 cujos dois valores seguintes da
grade também passem. O sistema precisa estar no ar, com os dados do preparo.
"""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, LiteralString, Optional, Tuple, cast

import numpy as np
import numpy.typing as npt

RAIZ: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DADOS: str = os.path.join(RAIZ, "eixo-a-busca", "dados")
SAIDA: str = os.path.join(RAIZ, "resultados", "calibracao")
VIZINHOS: str = os.path.join(DADOS, "vizinhos-exatos.npy")
REGISTRO_VIZINHOS: str = os.path.join(DADOS, "vizinhos-exatos.json")

K: int = 100  # vizinhos por consulta: a lista que entra na fusão (ADR-039)
CANDIDATOS: int = 200  # reordenados em float64
BLOCO: int = 100_000  # passagens por bloco no cálculo em float32
EF_INICIAL: int = 100  # piso da ADR-038
PASSO: int = 10
EF_MAXIMO: int = 1000
ALVO: float = 0.95
CONFIRMACOES: int = 2  # valores seguintes da grade que também precisam passar

# Mesma busca vetorial dos serviços (eixo-a-busca/servico/consolidada.py e poliglota.py)
VETORIAL_PG: LiteralString = "SELECT id FROM passagens ORDER BY embedding <#> %s LIMIT 100"
COLECAO: str = "passagens"
CONTAINERS: Dict[str, str] = {
    "consolidada": "tcc-a-consolidada-postgres-1",
    "poliglota": "tcc-a-poliglota-qdrant-1",
}

Busca = Callable[[npt.NDArray[np.float32]], List[int]]


def avisar(mensagem: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {mensagem}", flush=True)


def sha256(caminho: str) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 24), b""):
            h.update(bloco)
    return h.hexdigest()


def gravar_json(caminho: str, dados: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
        f.write("\n")


def carregar(nome: str) -> npt.NDArray[np.float32]:
    matriz = np.load(os.path.join(DADOS, nome), mmap_mode="r")
    if matriz.dtype != np.float32 or matriz.ndim != 2:
        raise ValueError(f"{nome}: esperado float32 com 2 dimensões, encontrado {matriz.dtype} {matriz.shape}")
    return cast(npt.NDArray[np.float32], matriz)


def vizinhos_exatos() -> int:
    passagens = carregar("passagens.npy")
    consultas = np.ascontiguousarray(carregar("consultas.npy"))
    total, dimensao = passagens.shape
    if consultas.shape[1] != dimensao:
        raise ValueError(f"dimensões diferentes: passagens {dimensao}, consultas {consultas.shape[1]}")
    n = consultas.shape[0]
    inicio = time.monotonic()
    ids = np.zeros((n, 0), dtype=np.int64)
    pontos = np.zeros((n, 0), dtype=np.float32)
    for comeco in range(0, total, BLOCO):
        bloco = np.asarray(passagens[comeco:comeco + BLOCO])
        produto = consultas @ bloco.T
        melhores = np.argpartition(-produto, CANDIDATOS - 1, axis=1)[:, :CANDIDATOS]
        ids = np.concatenate([ids, melhores + comeco], axis=1)
        pontos = np.concatenate([pontos, np.take_along_axis(produto, melhores, axis=1)], axis=1)
        if ids.shape[1] > CANDIDATOS:
            ficam = np.argpartition(-pontos, CANDIDATOS - 1, axis=1)[:, :CANDIDATOS]
            ids = np.take_along_axis(ids, ficam, axis=1)
            pontos = np.take_along_axis(pontos, ficam, axis=1)
        avisar(f"float32: {min(comeco + BLOCO, total):,} de {total:,} passagens")
    exatos = np.zeros((n, K), dtype=np.int32)
    consultas64 = consultas.astype(np.float64)
    for i in range(n):
        candidatos = np.sort(ids[i])
        vetores = np.asarray(passagens[candidatos]).astype(np.float64)
        produto64 = vetores @ consultas64[i]
        ordem = np.lexsort((candidatos, -produto64))  # maior produto; empate pelo menor id
        exatos[i] = candidatos[ordem[:K]]
    duracao = time.monotonic() - inicio
    np.save(VIZINHOS, exatos)
    gravar_json(REGISTRO_VIZINHOS, {
        "descricao": "100 vizinhos exatos de cada consulta por produto interno (ADR-067, item 2)",
        "metodo": f"float32 em blocos de {BLOCO}, {CANDIDATOS} candidatos por consulta reordenados "
                  "em float64, empate pelo menor identificador",
        "consultas": n, "passagens": total, "k": K,
        "entradas": {nome: sha256(os.path.join(DADOS, nome))
                     for nome in ("passagens.npy", "consultas.npy")},
        "arquivo": {"nome": os.path.basename(VIZINHOS), "bytes": os.path.getsize(VIZINHOS),
                    "sha256": sha256(VIZINHOS)},
        "duracao_s": round(duracao, 1),
        "versoes": {"numpy": np.__version__, "python": platform.python_version()},
        "gerado_em": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    })
    avisar(f"vizinhos exatos gravados em {VIZINHOS} ({duracao:.0f} s)")
    return 0


def ip(container: str) -> str:
    r = subprocess.run(["docker", "inspect", "-f",
                        "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", container],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"container {container} não encontrado ou sem rede: {r.stderr.strip()}")
    return r.stdout.strip()


def imagem(container: str) -> str:
    r = subprocess.run(["docker", "inspect", "-f", "{{.Config.Image}}", container],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def busca_consolidada(ef: int) -> Tuple[Busca, Callable[[], None], Dict[str, Any]]:
    import psycopg
    from pgvector.psycopg import register_vector

    conn = psycopg.connect(host=ip(CONTAINERS["consolidada"]), user="postgres", password="tcc",
                           dbname="busca", autocommit=True, prepare_threshold=None)
    register_vector(conn)
    conn.execute("SELECT set_config('hnsw.ef_search', %s, false)", (str(ef),))
    linha = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()
    detalhes: Dict[str, Any] = {"pgvector": str(linha[0]) if linha else None}

    def buscar(vetor: npt.NDArray[np.float32]) -> List[int]:
        return [int(i) for (i,) in conn.execute(VETORIAL_PG, (vetor,)).fetchall()]

    return buscar, conn.close, detalhes


def busca_poliglota(ef: int) -> Tuple[Busca, Callable[[], None], Dict[str, Any]]:
    from qdrant_client import QdrantClient
    from qdrant_client.models import SearchParams

    cliente = QdrantClient(host=ip(CONTAINERS["poliglota"]), prefer_grpc=True, timeout=120)
    info = cliente.get_collection(COLECAO)
    detalhes: Dict[str, Any] = {"segmentos": info.segments_count, "pontos": info.points_count}

    def buscar(vetor: npt.NDArray[np.float32]) -> List[int]:
        pontos = cliente.query_points(COLECAO, query=vetor.tolist(), limit=K,
                                      search_params=SearchParams(hnsw_ef=ef)).points
        return [int(p.id) for p in pontos]

    return buscar, cliente.close, detalhes


def medir(arquitetura: str, ef: int, consultas: npt.NDArray[np.float32],
          exatos: npt.NDArray[np.int32]) -> Dict[str, Any]:
    abrir = busca_consolidada if arquitetura == "consolidada" else busca_poliglota
    buscar, fechar, detalhes = abrir(ef)
    recalls: List[float] = []
    incompletas = 0
    inicio = time.monotonic()
    try:
        for i in range(consultas.shape[0]):
            achados = buscar(np.ascontiguousarray(consultas[i]))
            if len(achados) < K:
                incompletas += 1
            recalls.append(len(set(achados) & set(exatos[i].tolist())) / K)
    finally:
        fechar()
    valores = np.asarray(recalls)
    return {
        "ef": ef, "recall_medio": float(valores.mean()), "recall_minimo": float(valores.min()),
        "recall_p05": float(np.percentile(valores, 5)),
        "consultas_abaixo_de_0_9": int((valores < 0.9).sum()),
        "respostas_com_menos_de_100": incompletas,
        "duracao_s": round(time.monotonic() - inicio, 1), "detalhes": detalhes,
    }


def varrer(arquitetura: str) -> int:
    if not os.path.exists(VIZINHOS):
        avisar(f"faltam os vizinhos exatos ({VIZINHOS}); rode o subcomando vizinhos antes")
        return 1
    consultas = np.ascontiguousarray(carregar("consultas.npy"))
    exatos = cast(npt.NDArray[np.int32], np.load(VIZINHOS))
    if exatos.shape != (consultas.shape[0], K):
        avisar(f"vizinhos com forma {exatos.shape}, esperado {(consultas.shape[0], K)}")
        return 1
    medidas: List[Dict[str, Any]] = []
    escolhido: Optional[int] = None
    ef = EF_INICIAL
    while ef <= EF_MAXIMO:
        m = medir(arquitetura, ef, consultas, exatos)
        medidas.append(m)
        avisar(f"{arquitetura}: ef {ef}: recall@100 médio {m['recall_medio']:.4f} "
               f"(mínimo {m['recall_minimo']:.2f}, {m['duracao_s']:.0f} s)")
        passaram = [x["ef"] for x in medidas if x["recall_medio"] >= ALVO]
        for candidato in passaram:
            seguintes = [x for x in medidas if candidato < x["ef"] <= candidato + CONFIRMACOES * PASSO]
            if len(seguintes) == CONFIRMACOES and all(x["recall_medio"] >= ALVO for x in seguintes):
                escolhido = candidato
                break
        if escolhido is not None:
            break
        ef += PASSO
    gravar_json(os.path.join(SAIDA, f"ef-{arquitetura}.json"), {
        "arquitetura": arquitetura,
        "criterio": f"menor ef com recall@100 médio >= {ALVO}, com os {CONFIRMACOES} valores "
                    f"seguintes da grade (passo {PASSO}) também >= {ALVO}; piso {EF_INICIAL} "
                    "(ADR-038 e ADR-067)",
        "escolhido": escolhido,
        "busca": VETORIAL_PG if arquitetura == "consolidada"
        else f"query_points('{COLECAO}', limit={K}, search_params=SearchParams(hnsw_ef=ef))",
        "imagem": imagem(CONTAINERS[arquitetura]),
        "vizinhos_exatos_sha256": sha256(VIZINHOS),
        "medidas": medidas,
        "medido_em": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    })
    if escolhido is None:
        avisar(f"{arquitetura}: nenhum ef até {EF_MAXIMO} atingiu {ALVO}")
        return 1
    avisar(f"{arquitetura}: ef escolhido {escolhido}")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Calibração do ef da busca vetorial (ADR-038 e ADR-067)")
    sub = p.add_subparsers(dest="subcomando", required=True)
    sub.add_parser("vizinhos", help="calcula os 100 vizinhos exatos de cada consulta")
    pv = sub.add_parser("varrer", help="mede o recall@100 por ef num sistema no ar")
    pv.add_argument("arquitetura", choices=sorted(CONTAINERS))
    args = p.parse_args()
    if args.subcomando == "vizinhos":
        sys.exit(vizinhos_exatos())
    sys.exit(varrer(str(args.arquitetura)))


if __name__ == "__main__":
    main()
