"""Gera os embeddings das passagens e das consultas da NQ (ADR-005 e ADR-035)."""

import hashlib
import importlib.metadata
import itertools
import json
import os
import platform
import sys
import time
from typing import Any, Dict, Iterator, List, Optional, Tuple, cast

import numpy as np
import numpy.typing as npt
import torch
from sentence_transformers import SentenceTransformer

MODELO: str = "BAAI/bge-small-en-v1.5"
REVISAO: str = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
DIMENSAO: int = 384
PREFIXO_CONSULTA: str = "Represent this sentence for searching relevant passages: "
LOTE_GPU: int = 256
BLOCO: int = 100_000

DADOS: str = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dados")
NQ: str = os.path.join(DADOS, "nq")
SAIDAS: Tuple[str, ...] = ("passagens.npy", "passagens-ids.txt", "consultas.npy", "consultas-ids.txt")


def registro(linha: str) -> Dict[str, Any]:
    dados = json.loads(linha)
    if not isinstance(dados, dict):
        raise ValueError(f"linha não é um objeto JSON: {linha[:80]!r}")
    return cast(Dict[str, Any], dados)


def campo(doc: Dict[str, Any], nome: str) -> str:
    valor = doc.get(nome)
    if not isinstance(valor, str):
        raise ValueError(f"campo {nome!r} ausente ou não textual em {doc.get('_id')!r}")
    return valor


def ler(caminho: str, com_titulo: bool) -> Iterator[Tuple[str, str]]:
    """Identificador e texto de cada linha do JSONL, na ordem do arquivo."""
    with open(caminho, encoding="utf-8") as f:
        for linha in f:
            doc = registro(linha)
            texto = campo(doc, "text")
            if com_titulo:
                texto = f"{campo(doc, 'title')} {texto}".strip()
            yield campo(doc, "_id"), texto


def codificar(modelo: SentenceTransformer, textos: List[str],
              prefixo: Optional[str]) -> npt.NDArray[np.float32]:
    vetores = modelo.encode(  # pyright: ignore[reportUnknownMemberType]
        textos, prompt=prefixo, batch_size=LOTE_GPU, normalize_embeddings=True,
        convert_to_numpy=True, show_progress_bar=False)
    if vetores.shape != (len(textos), DIMENSAO) or vetores.dtype != np.float32:
        raise RuntimeError(f"saída inesperada do modelo: {vetores.shape} {vetores.dtype}")
    if not np.isfinite(vetores).all():
        raise RuntimeError("o modelo devolveu valores não finitos")
    if not np.allclose(np.linalg.norm(vetores, axis=1), 1.0, atol=1e-3):
        raise RuntimeError("vetores não normalizados")
    return vetores


def gravar_ids(caminho: str, ids: List[str]) -> None:
    if len(set(ids)) != len(ids):
        raise ValueError(f"{caminho}: identificadores repetidos")
    with open(caminho, "w", encoding="utf-8") as f:
        f.writelines(f"{i}\n" for i in ids)


def passagens(modelo: SentenceTransformer) -> int:
    """Codifica em blocos direto no .npy em disco, sem montar a matriz inteira na memória."""
    origem = os.path.join(NQ, "corpus.jsonl")
    with open(origem, "rb") as f:
        total = sum(1 for _ in f)
    parcial = os.path.join(DADOS, ".passagens.npy.parcial")
    ids: List[str] = []
    inicio = time.monotonic()
    try:
        matriz = np.lib.format.open_memmap(parcial, mode="w+", dtype=np.float32,
                                           shape=(total, DIMENSAO))
        for bloco in itertools.batched(ler(origem, com_titulo=True), BLOCO):
            matriz[len(ids):len(ids) + len(bloco)] = codificar(modelo, [t for _, t in bloco], None)
            ids.extend(i for i, _ in bloco)
            taxa = len(ids) / (time.monotonic() - inicio)
            print(f"  {len(ids):,} de {total:,} passagens ({taxa:,.0f}/s)", flush=True)
        if len(ids) != total:
            raise RuntimeError(f"lidas {len(ids)} passagens, esperadas {total}")
        matriz.flush()
        del matriz
        os.replace(parcial, os.path.join(DADOS, "passagens.npy"))
    except BaseException:
        if os.path.exists(parcial):
            os.remove(parcial)
        raise
    gravar_ids(os.path.join(DADOS, "passagens-ids.txt"), ids)
    return total


def consultas(modelo: SentenceTransformer) -> int:
    pares = list(ler(os.path.join(NQ, "queries.jsonl"), com_titulo=False))
    vetores = codificar(modelo, [t for _, t in pares], PREFIXO_CONSULTA)
    np.save(os.path.join(DADOS, "consultas.npy"), vetores)
    gravar_ids(os.path.join(DADOS, "consultas-ids.txt"), [i for i, _ in pares])
    return len(pares)


def sha256(caminho: str) -> str:
    with open(caminho, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def main() -> int:
    if not torch.cuda.is_available():
        print("CUDA indisponível: a geração roda na GPU", file=sys.stderr)
        return 1
    inicio = time.monotonic()
    modelo = SentenceTransformer(MODELO, revision=REVISAO, device="cuda")
    if modelo.get_embedding_dimension() != DIMENSAO:
        print(f"Dimensão {modelo.get_embedding_dimension()}, esperada {DIMENSAO}", file=sys.stderr)
        return 1

    print("Consultas", flush=True)
    n_consultas = consultas(modelo)
    print("Passagens", flush=True)
    n_passagens = passagens(modelo)

    versoes: Dict[str, Optional[str]] = {
        p: importlib.metadata.version(p)
        for p in ("sentence-transformers", "transformers", "torch", "numpy")
    }
    versoes["python"] = platform.python_version()
    versoes["cuda"] = torch.version.cuda
    registro_saida: Dict[str, Any] = {
        "modelo": MODELO, "revisao": REVISAO, "dimensao": DIMENSAO,
        "max_seq_length": modelo.max_seq_length,
        "texto_passagem": "title + ' ' + text", "prefixo_consulta": PREFIXO_CONSULTA,
        "normalizados": True, "dtype": "float32",
        "precisao_matmul": torch.get_float32_matmul_precision(),
        "lote_gpu": LOTE_GPU, "gpu": torch.cuda.get_device_name(0), "versoes": versoes,
        "passagens": n_passagens, "consultas": n_consultas,
        "duracao_s": round(time.monotonic() - inicio, 1),
        "gerado_em": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "arquivos": [{"nome": nome, "bytes": os.path.getsize(os.path.join(DADOS, nome)),
                      "sha256": sha256(os.path.join(DADOS, nome))} for nome in SAIDAS],
    }
    with open(os.path.join(DADOS, "embeddings.json"), "w", encoding="utf-8") as f:
        json.dump(registro_saida, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"{n_passagens:,} passagens e {n_consultas:,} consultas em "
          f"{registro_saida['duracao_s']} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
