"""Diretórios de dados das arquiteturas: cópia inicial, restauração e descarte."""

import os
import subprocess
from typing import Dict, List

from harness.orquestracao.arquitetura import DADOS_RAIZ, Arquitetura

IMAGEM_AUXILIAR: str = "tcc-postgres:18.6-ts2.31.0-pgv0.8.7"


class ErroDados(Exception):
    """Operação de dados recusada ou que falhou, com a explicação."""


def _auxiliar(base: str, comando: List[str]) -> str:
    """Roda um comando como root num container descartável, com base montado em /d."""
    if not base.startswith(DADOS_RAIZ + os.sep):
        raise ErroDados(f"recusado: {base} fica fora de {DADOS_RAIZ}")
    r = subprocess.run(["docker", "run", "--rm", "--network", "none", "-v", f"{base}:/d",
                        IMAGEM_AUXILIAR, *comando], capture_output=True, text=True)
    if r.returncode != 0:
        raise ErroDados(f"'{' '.join(comando)}' em {base} falhou: {r.stderr.strip()}")
    return r.stdout


def _tamanho_bytes(base: str, copia: str) -> int:
    return int(_auxiliar(base, ["du", "-sb", f"/d/{copia}"]).split()[0])


def _livre_bytes(base: str) -> int:
    st = os.statvfs(base)
    return st.f_bavail * st.f_frsize


def _copiar(base: str, origem: str, destino: str, margem_gib: float) -> int:
    """Apaga o destino, confere o espaço e só então copia; devolve o tamanho copiado."""
    _auxiliar(base, ["rm", "-rf", f"/d/{destino}"])
    tamanho = _tamanho_bytes(base, origem)
    necessario = tamanho + int(margem_gib * 2**30)
    livre = _livre_bytes(base)
    if livre < necessario:
        raise ErroDados(f"espaço insuficiente para copiar {origem}/ em {base}: precisa de "
                        f"{necessario / 2**30:.1f} GiB livres, há {livre / 2**30:.1f} GiB")
    _auxiliar(base, ["cp", "-a", f"/d/{origem}", f"/d/{destino}"])
    return tamanho


def existe_inicial(arq: Arquitetura) -> bool:
    """Se alguma cópia inicial da arquitetura já existe."""
    return any(os.path.isdir(os.path.join(arq.diretorio_dados(sub), "inicial"))
               for _, sub in arq.dados)


def preparar_diretorios(arq: Arquitetura) -> None:
    """Cria os diretórios de dados e deixa cada atual/ vazio, para a carga começar do zero."""
    for _, sub in arq.dados:
        base = arq.diretorio_dados(sub)
        os.makedirs(base, exist_ok=True)
        _auxiliar(base, ["rm", "-rf", "/d/atual"])
        os.makedirs(os.path.join(base, "atual"))


def salvar_inicial(arq: Arquitetura, margem_gib: float) -> Dict[str, int]:
    """Copia cada atual/ para inicial/, depois da carga e do desligamento limpo."""
    return {sub: _copiar(arq.diretorio_dados(sub), "atual", "inicial", margem_gib)
            for _, sub in arq.dados}


def restaurar(arq: Arquitetura, margem_gib: float) -> Dict[str, int]:
    """Substitui cada atual/ por uma cópia de inicial/ (ADR-029)."""
    for _, sub in arq.dados:
        if not os.path.isdir(os.path.join(arq.diretorio_dados(sub), "inicial")):
            raise ErroDados(f"{arq.eixo}/{arq.arquitetura}: falta {sub}/inicial; rode o preparar antes")
    return {sub: _copiar(arq.diretorio_dados(sub), "inicial", "atual", margem_gib)
            for _, sub in arq.dados}


def descartar() -> str:
    """sync e fstrim, para o SSD saber dos blocos liberados (ADR-017)."""
    os.sync()
    r = subprocess.run(["sudo", "-n", "/usr/sbin/fstrim", "-v", "/"], capture_output=True, text=True)
    if r.returncode != 0:
        raise ErroDados(f"fstrim falhou: {r.stderr.strip()}")
    return r.stdout.strip()
