"""Orquestrador das execuções do experimento"""

import argparse
import glob
import json
import os
import shutil
import signal
import sys
import time
from collections import Counter
from typing import Any, Dict, List, Optional, Set

from harness.orquestracao import arquitetura, controles, dados, execucao, plano, registros
from harness.orquestracao.compose import Compose

RESULTADOS_PADRAO: str = os.path.join(arquitetura.RAIZ, "resultados", "brutos")
CPUS_COLETA: Set[int] = {6, 7, 14, 15}
ESTADOS_FINAIS: Set[str] = {"ok", "falha", "oom"}


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

def _registrar_maquina(pasta: str, nome: str, pl: plano.Plano, adicionais: Dict[str, Any]) -> None:
    """Grava <nome>.json com o estado da máquina e smart-<nome>.txt com o SMART completo."""
    texto, campos = registros.smart()
    registros.gravar_json(os.path.join(pasta, f"{nome}.json"), {
        "versao_formato": registros.VERSAO_FORMATO, **adicionais,
        "momento": registros.agora_iso(),
        "controles": registros.controles(controles.conferir(pl.espaco_livre_min_gib)),
        "disco": registros.disco(), "smart": campos,
        "host": registros.host(), "repositorio": registros.repositorio(),
    })
    with open(os.path.join(pasta, f"smart-{nome}.txt"), "w", encoding="utf-8") as f:
        f.write(texto)


def _manifestos(resultados: str, janela: str) -> List[Dict[str, Any]]:
    """Manifestos desta janela, em ordem de início: a fonte de verdade da retomada (ADR-033)."""
    lista: List[Dict[str, Any]] = []
    for caminho in glob.glob(os.path.join(resultados, "*", "*", "manifesto.json")):
        m = registros.ler_json(caminho)
        if m.get("janela") == janela:
            lista.append(m)
    return sorted(lista, key=lambda m: str(m.get("inicio", "")))


def _fechar_interrompidas(resultados: str, janela: str) -> None:
    """Manifestos que ficaram em andamento, por queda da máquina ou do orquestrador, viram 'abortada'."""
    for caminho in glob.glob(os.path.join(resultados, "*", "*", "manifesto.json")):
        m = registros.ler_json(caminho)
        if m.get("janela") == janela and m.get("estado") == "em_andamento":
            m["estado"] = "abortada"
            m["motivo_falha"] = "ficou em andamento (queda da máquina ou do orquestrador); fechada na retomada"
            m["fim"] = registros.agora_iso()
            m["arquivos"] = registros.arquivos(os.path.dirname(caminho))
            registros.gravar_json(caminho, m)
            print(f"Execução {m['id']} fechada como abortada")


def _gravar_estado(pasta: str, janela: str, execucoes: List[plano.Execucao],
                   manifestos: List[Dict[str, Any]]) -> None:
    """estado.json: o estado de cada posição da ordem sorteada e as tentativas feitas."""
    linhas: List[Dict[str, Any]] = []
    for ex in execucoes:
        tentativas = [m for m in manifestos if m.get("ordem_na_janela") == ex.ordem]
        linhas.append({"ordem": ex.ordem, "execucao": ex.sufixo(),
                       "estado": tentativas[-1].get("estado") if tentativas else "pendente",
                       "tentativas": [m.get("id") for m in tentativas]})
    registros.gravar_json(os.path.join(pasta, "estado.json"),
                          {"janela": janela, "atualizado": registros.agora_iso(), "execucoes": linhas})


def executar(caminho_plano: Optional[str], retomar: Optional[str], resultados: str,
             repouso_s: int) -> int:
    """Roda uma janela inteira, ou retoma uma interrompida (ADR-032 e ADR-033)."""
    signal.signal(signal.SIGINT, execucao.interromper_uma_vez)
    os.sched_setaffinity(0, CPUS_COLETA)
    inicio_sessao = registros.agora_iso()

    if retomar is not None:
        janela = retomar
        pasta = os.path.join(resultados, "janelas", janela)
        caminho_plano = os.path.join(pasta, "plano.toml")
        if not os.path.isfile(caminho_plano):
            print(f"Janela {janela} não encontrada em {pasta}")
            return 1
    elif caminho_plano is None:
        print("Informe o plano ou --retomar")
        return 1
    else:
        janela = time.strftime("%Y%m%d-%H%M")
        pasta = os.path.join(resultados, "janelas", janela)

    try:
        pl = plano.ler_plano(caminho_plano)
    except plano.ErroPlano as erro:
        print(f"Plano inválido: {erro}")
        return 1
    if repouso_s < plano.REPOUSO_S and any(c.eixo != "t" for c in pl.configuracoes):
        print(f"Repouso de {repouso_s} s recusado: abaixo de {plano.REPOUSO_S} s só em planos "
              "inteiramente do eixo t (ADR-004)")
        return 1
    for eixo, nome in sorted({(c.eixo, c.arquitetura) for c in pl.configuracoes}):
        try:
            arq = arquitetura.ler_arquitetura(eixo, nome)
        except arquitetura.ErroDescritor as erro:
            print(f"Descritor inválido: {erro}")
            return 1
        if not dados.existe_inicial(arq):
            print(f"{eixo}/{nome} não tem cópia inicial; rode antes: executar preparar {eixo} {nome}")
            return 1
    fora = [r for r in controles.conferir(pl.espaco_livre_min_gib) if r.bloqueante and not r.ok]
    if fora:
        for r in fora:
            print(f"[FALHA] {r.controle}: {r.detalhe}")
        print("Janela não iniciada: aplique a abertura de docs/protocolo-coleta.md")
        return 1

    execucoes = plano.sortear(pl)
    adicionais: Dict[str, Any] = {"janela": janela, "plano": pl.nome, "semente": pl.semente,
                                  "repouso_s": repouso_s, "ordem": [e.sufixo() for e in execucoes]}
    if retomar is None:
        try:
            os.makedirs(pasta)
        except FileExistsError:
            print(f"Já existe uma janela {janela}; espere um minuto e tente de novo")
            return 1
        shutil.copyfile(caminho_plano, os.path.join(pasta, "plano.toml"))
        _registrar_maquina(pasta, "abertura", pl, adicionais)
    else:
        _fechar_interrompidas(resultados, janela)
        _registrar_maquina(pasta, f"retomada-{time.strftime('%Y%m%d-%H%M%S')}", pl, adicionais)
    _gravar_estado(pasta, janela, execucoes, _manifestos(resultados, janela))

    motivo, codigo = "concluída", 0
    try:
        for ex in execucoes:
            tentativas = [m for m in _manifestos(resultados, janela) if m.get("ordem_na_janela") == ex.ordem]
            if any(m.get("estado") in ESTADOS_FINAIS for m in tentativas):
                continue
            print(f"\n=== Execução {ex.ordem} de {len(execucoes)}: {ex.sufixo()}", flush=True)
            execucao.executar_uma(pl, ex, janela, resultados, repouso_s)
            _gravar_estado(pasta, janela, execucoes, _manifestos(resultados, janela))
    except KeyboardInterrupt:
        motivo, codigo = "interrompida pelo operador (Ctrl+C)", 130
    except execucao.ErroJanela as erro:
        motivo, codigo = f"ambiente inválido: {erro}", 1

    manifestos = _manifestos(resultados, janela)
    _gravar_estado(pasta, janela, execucoes, manifestos)
    caminho_encerramento = os.path.join(pasta, "encerramento.json")
    sessoes: List[Any] = []
    if os.path.exists(caminho_encerramento):
        sessoes = list(registros.ler_json(caminho_encerramento).get("sessoes", []))
    sessoes.append({"inicio": inicio_sessao, "fim": registros.agora_iso(), "motivo": motivo})
    _registrar_maquina(pasta, "encerramento", pl, {
        **adicionais, "motivo": motivo, "sessoes": sessoes,
        "execucoes": [{"ordem": m.get("ordem_na_janela"), "id": m.get("id"),
                       "estado": m.get("estado")} for m in manifestos],
    })

    contagem = Counter(str(m.get("estado")) for m in manifestos)
    print(f"\nJanela {janela} {motivo}. Execuções por estado: {dict(contagem)}")
    if codigo != 0:
        print(f"Para continuar, com a abertura aplicada: executar --retomar {janela}")
    return codigo



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
    pe = sub.add_parser("executar", help="roda uma janela a partir de um plano, ou retoma uma interrompida")
    grupo = pe.add_mutually_exclusive_group(required=True)
    grupo.add_argument("plano", nargs="?", help="arquivo TOML do plano da janela")
    grupo.add_argument("--retomar", metavar="ID_JANELA", help="continua uma janela interrompida")
    pe.add_argument("--repouso-s", type=int, default=plano.REPOUSO_S,
                    help="repouso antes de cada execução; abaixo de 1200 s só no eixo t (ADR-004)")
    pe.add_argument("--resultados", default=RESULTADOS_PADRAO, help="raiz dos resultados brutos")



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
    elif subcomando == "executar":
        caminho_plano: Optional[str] = args.plano
        retomar: Optional[str] = args.retomar
        repouso_s: int = args.repouso_s
        resultados_janela: str = args.resultados
        sys.exit(executar(caminho_plano, retomar, resultados_janela, repouso_s))




if __name__ == "__main__":
    main()
