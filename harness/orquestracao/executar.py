"""Orquestrador das execuções do experimento"""

import argparse
import sys

from harness.orquestracao import controles, plano


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


def planejar(caminho: str) -> int:
    """Mostra a ordem sorteada das execuções de um plano e a duração mínima."""
    try:
        pl = plano.ler_plano(caminho)
    except plano.ErroPlano as erro:
        print(f"Plano inválido: {erro}")
        return 1
    execucoes = plano.sortear(pl)
    print(f"Plano '{pl.nome}', semente {pl.semente}: {len(execucoes)} execuções")
    repeticao_atual = 0
    for e in execucoes:
        if e.repeticao != repeticao_atual:
            repeticao_atual = e.repeticao
            print(f"\nBloco {repeticao_atual}")
        print(f"  {e.ordem:3d}. {e.sufixo()}")
    horas = plano.duracao_minima_s(execucoes) / 3600
    print(f"\nDuração mínima: {horas:.1f} h "
          "(repouso, aquecimento e medição; sem restauração, subida e desligamento)")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Orquestrador das execuções do experimento")
    sub = p.add_subparsers(dest="subcomando", required=True)
    v = sub.add_parser("verificar", help="confere os controles do protocolo, sem rodar nada")
    v.add_argument("--espaco-livre-min-gib", type=float, default=100.0)
    pp = sub.add_parser("planejar", help="mostra a ordem sorteada das execuções de um plano")
    pp.add_argument("plano", help="arquivo TOML do plano da janela")

    args = p.parse_args()
    subcomando: str = args.subcomando

    if subcomando == "verificar":
        espaco_min_gib: float = args.espaco_livre_min_gib
        sys.exit(verificar(espaco_min_gib))
    elif subcomando == "planejar":
        caminho: str = args.plano
        sys.exit(planejar(caminho))



if __name__ == "__main__":
    main()
