"""Orquestrador das execuções do experimento"""

import argparse
import json
import os
import sys
import time

from harness.orquestracao import arquitetura, controles, dados, plano
from harness.orquestracao.compose import Compose

RESULTADOS_PADRAO: str = os.path.join(arquitetura.RAIZ, "resultados", "brutos")


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

def preparar(eixo: str, nome: str, refazer: bool, margem_gib: float, resultados: str) -> int:
    """Carrega os dados de uma arquitetura e grava a cópia inicial (ADR-029)."""
    try:
        arq = arquitetura.ler_arquitetura(eixo, nome)
    except arquitetura.ErroDescritor as erro:
        print(f"Descritor inválido: {erro}")
        return 1
    if dados.existe_inicial(arq) and not refazer:
        print(f"{eixo}/{nome} já tem cópia inicial; use --refazer para substituí-la")
        return 1
    rodando = controles.containers()
    if not rodando.ok:
        print(f"Recusado: {rodando.detalhe}")
        return 1

    inicio = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    pasta = os.path.join(resultados, "preparos", f"{time.strftime('%Y%m%d-%H%M%S')}_{eixo}_{nome}")
    os.makedirs(pasta)
    log = os.path.join(pasta, "compose.log")
    c = Compose(arq, pasta, log)
    try:
        dados.preparar_diretorios(arq)
        try:
            if c.subir() != 0 or c.gerador(["preparar"]) != 0:
                print(f"Falha na carga dos dados; veja {log}")
                return 1
        finally:
            c.desligar()
        tamanhos = dados.salvar_inicial(arq, margem_gib)
    except dados.ErroDados as erro:
        print(f"Erro nos diretórios de dados: {erro}")
        return 1

    registro = {
        "eixo": eixo,
        "arquitetura": nome,
        "inicio": inicio,
        "fim": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "refeito": refazer,
        "tamanhos_bytes": tamanhos,
    }
    with open(os.path.join(pasta, "preparo.json"), "w", encoding="utf-8") as f:
        json.dump(registro, f, indent=2, ensure_ascii=False)
    for sub, tamanho in tamanhos.items():
        print(f"Cópia inicial de {sub}: {tamanho / 2**20:.0f} MiB em {arq.diretorio_dados(sub)}/inicial")
    print(f"Registro do preparo em {pasta}")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Orquestrador das execuções do experimento")
    sub = p.add_subparsers(dest="subcomando", required=True)
    v = sub.add_parser("verificar", help="confere os controles do protocolo, sem rodar nada")
    v.add_argument("--espaco-livre-min-gib", type=float, default=100.0)
    pp = sub.add_parser("planejar", help="mostra a ordem sorteada das execuções de um plano")
    pp.add_argument("plano", help="arquivo TOML do plano da janela")
    pr = sub.add_parser("preparar", help="carrega os dados de uma arquitetura e grava a cópia inicial")
    pr.add_argument("eixo")
    pr.add_argument("arquitetura")
    pr.add_argument("--refazer", action="store_true", help="substitui a cópia inicial existente")
    pr.add_argument("--margem-gib", type=float, default=100.0, help="espaço livre que precisa sobrar depois da cópia")
    pr.add_argument("--resultados", default=RESULTADOS_PADRAO, help="raiz dos resultados brutos")


    args = p.parse_args()
    subcomando: str = args.subcomando

    if subcomando == "verificar":
        espaco_min_gib: float = args.espaco_livre_min_gib
        sys.exit(verificar(espaco_min_gib))
    elif subcomando == "planejar":
        caminho: str = args.plano
        sys.exit(planejar(caminho))
    elif subcomando == "preparar":
        eixo: str = args.eixo
        nome: str = args.arquitetura
        refazer: bool = args.refazer
        margem_gib: float = args.margem_gib
        resultados: str = args.resultados
        sys.exit(preparar(eixo, nome, refazer, margem_gib, resultados))



if __name__ == "__main__":
    main()
