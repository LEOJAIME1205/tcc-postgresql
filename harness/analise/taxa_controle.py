"""Taxa do controle com fio (ADR-069, item 5): a maior vazão média de escrita no SSD entre as
execuções da janela longa mais recente, na medição de cada uma. Só entram as execuções que
terminaram em ok; uma abortada pelo complemento da ADR-033 fica de fora. Imprime os MiB/s com uma
casa e grava os detalhes em resultados/processados/taxa-controle.json.

    uv run python -m harness.analise.taxa_controle
"""

import csv
import glob
import json
import os
from typing import Any, Dict, List

from harness.analise.comum import BRUTOS, PROCESSADOS, execucoes_da_janela

EXECUCOES_ESPERADAS: int = 4


def janela_longa() -> str:
    """Identificador da janela mais recente cujo plano se chama longa."""
    for pasta in sorted(glob.glob(os.path.join(BRUTOS, "janelas", "*")), reverse=True):
        with open(os.path.join(pasta, "plano.toml"), encoding="utf-8") as f:
            if any(linha.strip() == 'nome = "longa"' for linha in f):
                return os.path.basename(pasta)
    raise SystemExit("nenhuma janela com o plano longa")


def bytes_no_trecho(caminho: str, inicio_ms: int, fim_ms: int) -> int:
    """Bytes gravados no SSD entre dois instantes, somados entre os rótulos do contador."""
    primeiro: Dict[str, int] = {}
    ultimo: Dict[str, int] = {}
    with open(caminho, encoding="utf-8") as f:
        for linha in csv.DictReader(f):
            if linha["metrica"] != "disco_escrita_bytes_total":
                continue
            ts = int(linha["ts_ms"])
            if inicio_ms <= ts <= fim_ms:
                primeiro.setdefault(linha["rotulo"], int(linha["valor"]))
                ultimo[linha["rotulo"]] = int(linha["valor"])
    return sum(ultimo[r] - primeiro[r] for r in ultimo)


def main() -> None:
    janela = janela_longa()
    detalhes: List[Dict[str, Any]] = []
    for pasta in execucoes_da_janela(janela):
        with open(os.path.join(pasta, "gerador.json"), encoding="utf-8") as f:
            g: Dict[str, Any] = json.load(f)
        inicio, fim = int(g["medicao_inicio_ms"]), int(g["medicao_fim_ms"])
        gravado = bytes_no_trecho(os.path.join(pasta, "metricas-host.csv"), inicio, fim)
        segundos = (fim - inicio) / 1000
        detalhes.append({"execucao": os.path.basename(pasta), "bytes": gravado,
                         "segundos": segundos, "mib_s": gravado / 2**20 / segundos})
    if len(detalhes) != EXECUCOES_ESPERADAS:
        raise SystemExit(f"esperadas {EXECUCOES_ESPERADAS} execuções ok na janela {janela}, "
                         f"há {len(detalhes)}")
    taxa = max(float(d["mib_s"]) for d in detalhes)
    os.makedirs(PROCESSADOS, exist_ok=True)
    with open(os.path.join(PROCESSADOS, "taxa-controle.json"), "w", encoding="utf-8") as f:
        json.dump({"janela": janela, "taxa_mib_s": taxa, "execucoes": detalhes}, f, indent=1,
                  ensure_ascii=False)
    print(f"{taxa:.1f}")


if __name__ == "__main__":
    main()
