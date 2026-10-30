"""Orquestrador das execuções do experimento"""

import argparse
import sys

from harness.orquestracao import controles


def verificar(espaco_min_gib: float) -> int:
    """Mostra cada controle; devolve 1 se algum bloqueante estiver fora."""
    resultados = controles.conferir(espaco_min_gib)
    for r in resultados:
        marca = "OK   " if r.ok else ("FALHA" if r.bloqueante else "AVISO")
        print(f"[{marca}] {r.controle}: {r.detalhe}")
    falhas = [r for r in resultados if r.bloqueante and not r.ok]
    if falhas:
        print(f"\n{len(falhas)} controle(s) bloqueante(s) fora do protocolo.")
        return 1
    print("\nTodos os controles bloqueantes dentro do protocolo.")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Orquestrador das execuções do experimento")
    sub = p.add_subparsers(dest="subcomando", required=True)
    v = sub.add_parser("verificar", help="confere os controles do protocolo, sem rodar nada")
    v.add_argument("--espaco-livre-min-gib", type=float, default=100.0)
    args = p.parse_args()
    subcomando: str = args.subcomando

    if subcomando == "verificar":
        espaco_min_gib: float = args.espaco_livre_min_gib
        sys.exit(verificar(espaco_min_gib))


if __name__ == "__main__":
    main()
