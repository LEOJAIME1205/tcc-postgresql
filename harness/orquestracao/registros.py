"""Registros das execuções e das janelas."""

import hashlib
import json
import os
import subprocess
import tempfile
import time
from dataclasses import asdict
from typing import Any, Dict, List, Tuple, cast

from harness.orquestracao.arquitetura import RAIZ
from harness.orquestracao.controles import Resultado

VERSAO_FORMATO: int = 1
CAMPOS_SMART: Tuple[str, ...] = (
    "temperature", "percentage_used", "Data Units Written",
    "power_on_hours", "media_errors", "unsafe_shutdowns",
)


def agora_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def agora_ms() -> int:
    return time.time_ns() // 1_000_000


def gravar_json(caminho: str, dados: Dict[str, Any]) -> None:
    """Grava num temporário e troca de nome: o arquivo nunca fica pela metade."""
    fd, temporario = tempfile.mkstemp(dir=os.path.dirname(caminho), prefix=".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(dados, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporario, caminho)
    except BaseException:
        if os.path.exists(temporario):
            os.remove(temporario)
        raise


def ler_json(caminho: str) -> Dict[str, Any]:
    with open(caminho, encoding="utf-8") as f:
        dados = json.load(f)
    if not isinstance(dados, dict):
        raise ValueError(f"{caminho}: era esperado um objeto JSON")
    return cast(Dict[str, Any], dados)


def sha256(caminho: str) -> str:
    with open(caminho, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def arquivos(pasta: str) -> List[Dict[str, Any]]:
    """Nome relativo, tamanho e SHA-256 de cada arquivo da execução, menos o manifesto."""
    lista: List[Dict[str, Any]] = []
    for raiz, _, nomes in os.walk(pasta):
        for nome in nomes:
            caminho = os.path.join(raiz, nome)
            relativo = os.path.relpath(caminho, pasta)
            if relativo == "manifesto.json":
                continue
            lista.append({"nome": relativo, "bytes": os.path.getsize(caminho),
                          "sha256": sha256(caminho)})
    return sorted(lista, key=lambda a: a["nome"])


def controles(resultados: List[Resultado]) -> List[Dict[str, Any]]:
    """Os controles conferidos, no formato do manifesto e dos registros de janela."""
    return [asdict(r) for r in resultados]


def repositorio() -> Dict[str, Any]:
    """Commit do repositório e se há arquivos versionados alterados sem commit."""
    def git(*argumentos: str) -> str:
        return subprocess.run(["git", *argumentos], cwd=RAIZ,
                              capture_output=True, text=True).stdout.strip()
    return {"commit": git("rev-parse", "HEAD"),
            "alteracoes_locais": bool(git("status", "--porcelain", "--untracked-files=no"))}


def disco() -> Dict[str, int]:
    st = os.statvfs("/")
    return {"ocupado_bytes": (st.f_blocks - st.f_bfree) * st.f_frsize,
            "livre_bytes": st.f_bavail * st.f_frsize}


def smart() -> Tuple[str, Dict[str, str]]:
    """Saída completa do nvme smart-log do P510 e os campos principais."""
    r = subprocess.run(["sudo", "-n", "/usr/sbin/nvme", "smart-log", "/dev/nvme0"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"nvme smart-log falhou: {r.stderr.strip()}")
    campos: Dict[str, str] = {}
    for linha in r.stdout.splitlines():
        chave, _, valor = linha.partition(":")
        if chave.strip() in CAMPOS_SMART:
            campos[chave.strip()] = valor.strip()
    return r.stdout, campos


def host() -> Dict[str, str]:
    docker = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"],
                            capture_output=True, text=True).stdout.strip()
    return {"kernel": os.uname().release, "docker": docker}
