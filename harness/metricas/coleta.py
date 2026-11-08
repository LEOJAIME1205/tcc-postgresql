"""Laço comum dos coletores de sistemas acessados por HTTP (ADR-021 e ADR-049).

Mesma cadência e mesmo formato do coletor do PostgreSQL: estatísticas a cada 10 s,
tamanhos a cada 60 s, uma linha por métrica em metricas-<serviço>.csv, nos núcleos do
gerador. Uma leitura que falha é avisada no log e tentada de novo na volta seguinte.
"""

import base64
import csv
import json
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
from types import FrameType
from typing import Any, Callable, List, Optional, Set, Tuple, Union

Metrica = Tuple[str, str, str, Union[int, float]]
Leitor = Callable[[str], List[Metrica]]

CPUS_COLETA: Set[int] = {6, 7, 14, 15}
INTERVALO_S: float = 10.0
VOLTAS_TAMANHOS: int = 6
TEMPO_LIMITE_S: float = 5.0


def avisar(mensagem: str) -> None:
    """Mensagem com horário na saída de erro, para o log da execução."""
    print(f"[{time.strftime('%H:%M:%S')}] {mensagem}", file=sys.stderr, flush=True)


def ler_texto(url: str, credencial: Optional[str] = None) -> str:
    """Corpo da resposta; `credencial`, no formato usuário:senha, vai por autenticação básica."""
    pedido = urllib.request.Request(url)
    if credencial is not None:
        pedido.add_header("Authorization", "Basic " + base64.b64encode(credencial.encode()).decode())
    with urllib.request.urlopen(pedido, timeout=TEMPO_LIMITE_S) as resposta:
        return resposta.read().decode("utf-8")


def ler_json(url: str, credencial: Optional[str] = None) -> Any:
    return json.loads(ler_texto(url, credencial))


def rodar(saida: str, base: str, estatisticas: Leitor, tamanhos: Leitor) -> None:
    """Coleta até receber SIGTERM ou SIGINT; `base` é o endereço do sistema."""
    os.sched_setaffinity(0, CPUS_COLETA)
    parar = threading.Event()

    def ao_sinal(_signum: int, _frame: Optional[FrameType]) -> None:
        parar.set()

    signal.signal(signal.SIGTERM, ao_sinal)
    signal.signal(signal.SIGINT, ao_sinal)

    with open(saida, "x", newline="", encoding="utf-8") as f:
        escritor = csv.writer(f)
        escritor.writerow(["ts_ms", "origem", "metrica", "rotulo", "valor"])
        volta = 0
        proximo = time.monotonic()
        while not parar.is_set():
            ts_ms = time.time_ns() // 1_000_000
            leitores: List[Tuple[str, Leitor]] = [("estatísticas", estatisticas)]
            if volta % VOLTAS_TAMANHOS == 0:
                leitores.append(("tamanhos", tamanhos))
            for nome, ler in leitores:
                try:
                    escritor.writerows((ts_ms, *linha) for linha in ler(base))
                except (urllib.error.URLError, OSError, ValueError, KeyError) as erro:
                    avisar(f"{nome}: {erro}")
            f.flush()

            volta += 1
            proximo += INTERVALO_S
            agora = time.monotonic()
            if proximo < agora:
                proximo = agora
            parar.wait(max(0.0, proximo - time.monotonic()))
