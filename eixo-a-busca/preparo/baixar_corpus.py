"""Baixa a coleção NQ do BEIR, confere a integridade e extrai os arquivos usados."""

import hashlib
import json
import os
import sys
import time
import urllib.request
import zipfile
from typing import Any, Dict, List, Tuple

URL: str = "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/nq.zip"
MD5: str = "d4d3d2e48787a744b6f6e691ff534307"
TAMANHO: int = 498_307_926
MEMBROS: Tuple[str, ...] = ("nq/corpus.jsonl", "nq/queries.jsonl", "nq/qrels/test.tsv")
BLOCO: int = 1 << 20

DADOS: str = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dados")
ZIP: str = os.path.join(DADOS, "nq.zip")


def resumos(caminho: str) -> Tuple[str, str]:
    """MD5 e SHA-256 do arquivo, numa única leitura."""
    md5 = hashlib.md5(usedforsecurity=False)
    sha = hashlib.sha256()
    with open(caminho, "rb") as f:
        while bloco := f.read(BLOCO):
            md5.update(bloco)
            sha.update(bloco)
    return md5.hexdigest(), sha.hexdigest()


def linhas(caminho: str) -> int:
    with open(caminho, "rb") as f:
        return sum(1 for _ in f)


def baixar() -> None:
    """Baixa num arquivo parcial e só troca de nome quando chegou inteiro."""
    parcial = ZIP + ".parcial"
    recebido = 0
    aviso = TAMANHO // 10
    try:
        with urllib.request.urlopen(URL, timeout=60) as resposta, open(parcial, "wb") as f:
            while bloco := resposta.read(BLOCO):
                f.write(bloco)
                recebido += len(bloco)
                if recebido >= aviso:
                    print(f"  {recebido / 2**20:,.0f} de {TAMANHO / 2**20:,.0f} MiB", flush=True)
                    aviso += TAMANHO // 10
        if recebido != TAMANHO:
            raise RuntimeError(f"recebidos {recebido} bytes, esperados {TAMANHO}")
        os.replace(parcial, ZIP)
    except BaseException:
        if os.path.exists(parcial):
            os.remove(parcial)
        raise


def main() -> int:
    os.makedirs(DADOS, exist_ok=True)
    if not os.path.exists(ZIP):
        print(f"Baixando {URL}", flush=True)
        baixar()
    md5, sha = resumos(ZIP)
    if md5 != MD5:
        os.remove(ZIP)
        print(f"MD5 {md5} não confere com o publicado ({MD5}); zip apagado, rode de novo",
              file=sys.stderr)
        return 1
    print(f"MD5 confere: {md5}")

    with zipfile.ZipFile(ZIP) as z:
        faltando = [m for m in MEMBROS if m not in z.namelist()]
        if faltando:
            print(f"Membros ausentes no zip: {faltando}", file=sys.stderr)
            return 1
        for membro in MEMBROS:
            z.extract(membro, DADOS)

    arquivos: List[Dict[str, Any]] = []
    for membro in MEMBROS:
        caminho = os.path.join(DADOS, membro)
        n = linhas(caminho)
        arquivos.append({"nome": membro, "bytes": os.path.getsize(caminho), "linhas": n,
                         "sha256": resumos(caminho)[1]})
        print(f"{membro}: {n:,} linhas")

    registro: Dict[str, Any] = {
        "url": URL, "bytes": TAMANHO, "md5": md5, "sha256": sha,
        "conferido_em": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "arquivos": arquivos,
    }
    with open(os.path.join(DADOS, "nq.json"), "w", encoding="utf-8") as f:
        json.dump(registro, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
