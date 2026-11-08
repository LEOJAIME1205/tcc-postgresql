"""Gerador de carga do Eixo A (ADR-048), no contrato do gerador de harness/README.md.

    gerador.py preparar [--saida DIR]
    gerador.py executar --saida DIR --concorrencia N --aquecimento-s A --medicao-s M --semente S
    gerador.py qualidade --url URL --saida ARQUIVO.json [--rotulo NOME] [--limite K]

`preparar` roda o carregador da arquitetura indicada em TCC_ARQUITETURA e grava o registro
da carga em DIR/carga.json. `executar` mantém N clientes em laço fechado contra o serviço de
busca (TCC_SERVICO, padrão http://busca:8000) e grava o operacoes.csv e o gerador.json.
`qualidade` mede recall@10 e nDCG@10 de todas as consultas, uma de cada vez.
"""

import argparse
import asyncio
import csv
import json
import math
import multiprocessing
import os
import platform
import random
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, cast

import aiohttp
import numpy as np
import numpy.typing as npt
import psycopg

EIXO_A: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DADOS: str = os.path.join(EIXO_A, "dados")
ARQUITETURAS: Tuple[str, ...] = ("consolidada", "poliglota")
VERSAO: str = "1"
OPERACAO: str = "busca_hibrida"
TEMPO_LIMITE_S: float = 60.0
ESPERA_SAUDE_S: float = 300.0
PAUSA_APOS_FALHA_S: float = 0.1
ATRASO_INICIO_S: float = 3.0
MAX_PROCESSOS: int = 2
NUCLEOS_GERADOR: Tuple[Set[int], ...] = ({6, 14}, {7, 15})  # um por núcleo físico (ADR-018)
RESPOSTA: int = 10
CABECALHO: List[str] = ["ts_us", "operacao", "latencia_us", "ok", "cliente", "itens"]

Linha = Tuple[int, int, int, int]  # ts_us, latencia_us, ok, cliente


def avisar(mensagem: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {mensagem}", file=sys.stderr, flush=True)


def ler_consultas() -> Tuple[List[str], List[bytes]]:
    """Identificadores e corpos JSON prontos das consultas da NQ, na ordem do arquivo."""
    vetores: npt.NDArray[np.float32] = np.load(os.path.join(DADOS, "consultas.npy"))
    ids: List[str] = []
    corpos: List[bytes] = []
    with open(os.path.join(DADOS, "nq", "queries.jsonl"), encoding="utf-8") as f:
        for i, linha in enumerate(f):
            consulta = cast(Dict[str, Any], json.loads(linha))
            ids.append(str(consulta["_id"]))
            corpos.append(json.dumps({"texto": str(consulta["text"]),
                                      "vetor": vetores[i].tolist()}).encode())
    if len(corpos) != len(vetores):
        raise ValueError(f"{len(corpos)} consultas e {len(vetores)} vetores")
    return ids, corpos


def ordem_das_consultas(semente: int, total: int) -> List[int]:
    """Ordem embaralhada pela semente: a mesma em todas as arquiteturas (ADR-032)."""
    ordem = list(range(total))
    random.Random(semente).shuffle(ordem)
    return ordem


def responde(url: str) -> bool:
    """Se o endereço responde 200."""
    try:
        with urllib.request.urlopen(url, timeout=5) as resposta:
            return resposta.status == 200
    except (urllib.error.URLError, OSError):
        return False


def motores_prontos(arquitetura: str) -> bool:
    """Se os motores da arquitetura já aceitam conexões (endereços pelas variáveis do Compose)."""
    if arquitetura == "consolidada":
        try:
            with psycopg.connect(connect_timeout=2):
                return True
        except psycopg.OperationalError:
            return False
    return (responde(f"http://{os.environ.get('OPENSEARCH_HOST', 'opensearch')}:9200/_cluster/health")
            and responde(f"http://{os.environ.get('QDRANT_HOST', 'qdrant')}:6333/readyz"))


def esperar(pronto: Callable[[], bool]) -> bool:
    """Espera a condição valer, consultando a cada segundo, por até ESPERA_SAUDE_S."""
    prazo = time.monotonic() + ESPERA_SAUDE_S
    while time.monotonic() < prazo:
        if pronto():
            return True
        time.sleep(1.0)
    return False


async def cliente(sessao: aiohttp.ClientSession, url: str, corpos: List[bytes],
                  ordem: List[int], posicao: int, fim: float, numero: int,
                  linhas: List[Linha], erros: Counter[str]) -> None:
    """Um cliente em laço fechado: a próxima consulta sai assim que a resposta chega."""
    cabecalhos = {"Content-Type": "application/json"}
    while time.time() < fim:
        corpo = corpos[ordem[posicao % len(ordem)]]
        posicao += 1
        ts_us = time.time_ns() // 1000
        t0 = time.monotonic_ns()
        ok = 0
        try:
            async with sessao.post(url, data=corpo, headers=cabecalhos) as resposta:
                await resposta.read()
                if resposta.status == 200:
                    ok = 1
                else:
                    erros[f"http_{resposta.status}"] += 1
        except asyncio.TimeoutError:
            erros["tempo_esgotado"] += 1
        except aiohttp.ClientError as erro:
            erros[type(erro).__name__] += 1
        linhas.append((ts_us, (time.monotonic_ns() - t0) // 1000, ok, numero))
        if ok == 0:
            await asyncio.sleep(PAUSA_APOS_FALHA_S)


async def rodar_clientes(servico: str, numeros: List[int], concorrencia: int, semente: int,
                         inicio: float, fim: float) -> Tuple[List[Linha], Counter[str]]:
    _, corpos = ler_consultas()
    ordem = ordem_das_consultas(semente, len(corpos))
    linhas: List[Linha] = []
    erros: Counter[str] = Counter()
    conector = aiohttp.TCPConnector(limit=len(numeros))
    tempo_limite = aiohttp.ClientTimeout(total=TEMPO_LIMITE_S)
    async with aiohttp.ClientSession(connector=conector, timeout=tempo_limite) as sessao:
        await asyncio.sleep(max(0.0, inicio - time.time()))
        await asyncio.gather(*(
            cliente(sessao, f"{servico}/buscar", corpos, ordem,
                    (n * len(ordem)) // concorrencia, fim, n, linhas, erros)
            for n in numeros))
    return linhas, erros


def processo(k: int, numeros: List[int], cpus: List[int], servico: str, concorrencia: int,
             semente: int, inicio: float, fim: float, pasta: str) -> None:
    """Um processo do gerador: grava a sua parte das operações e os seus erros."""
    os.sched_setaffinity(0, cpus)
    linhas, erros = asyncio.run(rodar_clientes(servico, numeros, concorrencia, semente,
                                               inicio, fim))
    with open(os.path.join(pasta, f"operacoes-{k}.csv"), "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(linhas)
    with open(os.path.join(pasta, f"erros-{k}.json"), "w", encoding="utf-8") as f:
        json.dump(dict(erros), f)


def grupos_de_cpus(processos: int) -> List[List[int]]:
    """Um conjunto de CPUs por processo: um núcleo físico do gerador, se disponível."""
    disponiveis = os.sched_getaffinity(0)
    grupos = [sorted(n & disponiveis) for n in NUCLEOS_GERADOR[:processos]]
    if any(not g for g in grupos):
        return [sorted(disponiveis)] * processos
    return grupos


def executar(saida: str, concorrencia: int, aquecimento_s: int, medicao_s: int,
             semente: int) -> int:
    servico = os.environ.get("TCC_SERVICO", "http://busca:8000").rstrip("/")
    if not esperar(lambda: responde(f"{servico}/saude")):
        avisar(f"{servico}/saude não respondeu 200 em {ESPERA_SAUDE_S:.0f} s")
        return 1
    ids, _ = ler_consultas()
    processos = min(MAX_PROCESSOS, concorrencia)
    temporaria = os.path.join(saida, "ferramenta", "gerador-partes")
    os.makedirs(temporaria, exist_ok=True)
    inicio = time.time() + ATRASO_INICIO_S
    fim = inicio + aquecimento_s + medicao_s

    contexto = multiprocessing.get_context("spawn")
    filhos = [contexto.Process(target=processo, args=(
        k, list(range(k, concorrencia, processos)), cpus, servico, concorrencia, semente,
        inicio, fim, temporaria)) for k, cpus in enumerate(grupos_de_cpus(processos))]
    for filho in filhos:
        filho.start()
    for filho in filhos:
        filho.join()
    falhos = [k for k, filho in enumerate(filhos) if filho.exitcode != 0]
    if falhos:
        avisar(f"processo(s) {falhos} do gerador terminaram com erro")
        return 1

    linhas: List[Tuple[int, ...]] = []
    erros: Counter[str] = Counter()
    for k in range(processos):
        with open(os.path.join(temporaria, f"operacoes-{k}.csv"), encoding="utf-8") as f:
            linhas.extend(tuple(int(campo) for campo in linha) for linha in csv.reader(f))
        with open(os.path.join(temporaria, f"erros-{k}.json"), encoding="utf-8") as f:
            erros.update(cast(Dict[str, int], json.load(f)))
        os.remove(os.path.join(temporaria, f"operacoes-{k}.csv"))
        os.remove(os.path.join(temporaria, f"erros-{k}.json"))
    os.rmdir(temporaria)
    linhas.sort()
    with open(os.path.join(saida, "operacoes.csv"), "w", newline="", encoding="utf-8") as f:
        escritor = csv.writer(f)
        escritor.writerow(CABECALHO)
        escritor.writerows((ts, OPERACAO, lat, ok, n, 1) for ts, lat, ok, n in linhas)

    ordem = ordem_das_consultas(semente, len(ids))
    registro: Dict[str, Any] = {
        "ferramenta": "tcc-gerador-busca",
        "versao": f"{VERSAO} (aiohttp {aiohttp.__version__}, Python {platform.python_version()})",
        "aquecimento_inicio_ms": int(inicio * 1000),
        "medicao_inicio_ms": int((inicio + aquecimento_s) * 1000),
        "medicao_fim_ms": int(fim * 1000),
        "processo_fim_ms": time.time_ns() // 1_000_000,
        "parametros": {
            "concorrencia": concorrencia, "processos": processos, "semente": semente,
            "aquecimento_s": aquecimento_s, "medicao_s": medicao_s, "consultas": len(ids),
            "tempo_limite_s": TEMPO_LIMITE_S, "servico": servico,
            "primeiras_consultas": [ids[i] for i in ordem[:5]],
        },
        "operacoes": len(linhas),
        "erros": sum(erros.values()),
        "erros_por_tipo": dict(erros),
    }
    with open(os.path.join(saida, "gerador.json"), "w", encoding="utf-8") as f:
        json.dump(registro, f, indent=2, ensure_ascii=False)
        f.write("\n")
    avisar(f"{len(linhas)} operações, {registro['erros']} erro(s)")
    return 0


def preparar(saida: str) -> int:
    arquitetura = os.environ.get("TCC_ARQUITETURA", "")
    if arquitetura not in ARQUITETURAS:
        avisar(f"TCC_ARQUITETURA precisa ser {' ou '.join(ARQUITETURAS)}, não {arquitetura!r}")
        return 2
    # O Compose sobe os containers sem esperar os motores aceitarem conexões.
    if not esperar(lambda: motores_prontos(arquitetura)):
        avisar(f"os motores da arquitetura {arquitetura} não ficaram prontos em {ESPERA_SAUDE_S:.0f} s")
        return 1
    carregador = os.path.join(EIXO_A, arquitetura, "carga.py")
    return subprocess.run([sys.executable, carregador, "--registro",
                           os.path.join(saida, "carga.json")]).returncode


def relevantes() -> Dict[str, Set[int]]:
    """Passagens relevantes de cada consulta, pelos julgamentos de teste da NQ."""
    julgamentos: Dict[str, Set[int]] = {}
    with open(os.path.join(DADOS, "nq", "qrels", "test.tsv"), encoding="utf-8") as f:
        next(f)
        for linha in f:
            consulta, passagem, nota = linha.rstrip("\n").split("\t")
            if int(nota) > 0:
                julgamentos.setdefault(consulta, set()).add(int(passagem.removeprefix("doc")))
    return julgamentos


def ndcg_e_recall(ids: List[int], relevante: Set[int]) -> Tuple[float, float]:
    """nDCG@10 e recall@10 com julgamentos binários."""
    dcg = sum(1.0 / math.log2(posicao + 2) for posicao, i in enumerate(ids[:RESPOSTA])
              if i in relevante)
    ideal = sum(1.0 / math.log2(posicao + 2) for posicao in range(min(len(relevante), RESPOSTA)))
    return dcg / ideal, len(set(ids[:RESPOSTA]) & relevante) / len(relevante)


def qualidade(url: str, saida: str, rotulo: str, limite: Optional[int]) -> int:
    """recall@10 e nDCG@10 de cada consulta julgada, uma de cada vez (ADR-048, item 5)."""
    ids, corpos = ler_consultas()
    julgamentos = relevantes()
    alvos = [i for i, q in enumerate(ids) if q in julgamentos][:limite]
    por_consulta: List[Dict[str, Any]] = []
    inicio = time.monotonic()
    for i in alvos:
        req = urllib.request.Request(f"{url.rstrip('/')}/buscar", data=corpos[i],
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=TEMPO_LIMITE_S) as resposta:
            resultado = cast(Dict[str, List[int]], json.loads(resposta.read()))
        ndcg, recall = ndcg_e_recall(resultado["ids"], julgamentos[ids[i]])
        por_consulta.append({"consulta": ids[i], "ndcg_10": ndcg, "recall_10": recall,
                             "ids": resultado["ids"]})
    registro: Dict[str, Any] = {
        "rotulo": rotulo, "servico": url, "consultas": len(por_consulta),
        "ndcg_10": sum(c["ndcg_10"] for c in por_consulta) / len(por_consulta),
        "recall_10": sum(c["recall_10"] for c in por_consulta) / len(por_consulta),
        "duracao_s": round(time.monotonic() - inicio, 1),
        "concluido_em": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "por_consulta": por_consulta,
    }
    with open(saida, "w", encoding="utf-8") as f:
        json.dump(registro, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"{rotulo}: nDCG@10 {registro['ndcg_10']:.4f}, recall@10 {registro['recall_10']:.4f} "
          f"em {len(por_consulta)} consultas ({registro['duracao_s']} s)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Gerador de carga do Eixo A")
    sub = parser.add_subparsers(dest="comando", required=True)
    pp = sub.add_parser("preparar", help="carrega os dados da arquitetura de TCC_ARQUITETURA")
    pp.add_argument("--saida", default="/saida")
    pe = sub.add_parser("executar", help="aquecimento e medição com N clientes em laço fechado")
    pe.add_argument("--saida", required=True)
    pe.add_argument("--concorrencia", type=int, required=True)
    pe.add_argument("--aquecimento-s", type=int, required=True)
    pe.add_argument("--medicao-s", type=int, required=True)
    pe.add_argument("--semente", type=int, required=True)
    pq = sub.add_parser("qualidade", help="recall@10 e nDCG@10, uma consulta de cada vez")
    pq.add_argument("--url", required=True)
    pq.add_argument("--saida", required=True)
    pq.add_argument("--rotulo", default="")
    pq.add_argument("--limite", type=int)
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
    return qualidade(cast(str, args.url), cast(str, args.saida), cast(str, args.rotulo),
                     cast(Optional[int], args.limite))


if __name__ == "__main__":
    sys.exit(main())
