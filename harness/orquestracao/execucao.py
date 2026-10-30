"""Uma execução do experimento, do controle ao manifesto fechado.

Segue a "Sequência de cada execução" de harness/README.md. Quem chama (a
janela, em executar.py) cuida da ordem, do estado.json e da retomada.
"""

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from types import FrameType
from typing import Any, Dict, List, Optional, Tuple, cast

from harness.orquestracao import controles, dados, registros
from harness.orquestracao.arquitetura import RAIZ, Arquitetura, ErroDescritor, ler_arquitetura
from harness.orquestracao.compose import Compose
from harness.orquestracao.plano import Execucao, Plano

SENHA_POSTGRES: str = "tcc"
Processo = subprocess.Popen[bytes]


class ErroJanela(Exception):
    """O ambiente deixou de ser válido e a janela precisa ser encerrada (ADR-033)."""


def _interromper_uma_vez(_signum: int, _frame: Optional[FrameType]) -> None:
    """Primeiro Ctrl+C interrompe; os seguintes são ignorados, para a limpeza terminar."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    raise KeyboardInterrupt


def semente(pl: Plano, ex: Execucao) -> int:
    """Semente própria da execução: reproduzível e diferente entre repetições."""
    resumo = hashlib.sha256(f"{pl.semente}:{ex.sufixo()}".encode()).hexdigest()
    return int(resumo[:8], 16)


def _docker(argumentos: List[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *argumentos], capture_output=True, text=True)


def _esperar_banco(container: str, limite_s: float = 120.0) -> bool:
    """Espera o PostgreSQL aceitar conexões TCP, como os clientes farão."""
    prazo = time.monotonic() + limite_s
    while time.monotonic() < prazo:
        if _docker(["exec", container, "pg_isready", "-q", "-h", "127.0.0.1",
                    "-U", "postgres"]).returncode == 0:
            return True
        time.sleep(0.5)
    return False


def _ip(container: str) -> str:
    return _docker(["inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                    container]).stdout.strip()


def _iniciar_coletor(argumentos: List[str], log: str) -> Processo:
    """Coletor como processo filho, num grupo próprio, para o Ctrl+C não o atingir."""
    with open(log, "w", encoding="utf-8") as f:
        return subprocess.Popen([sys.executable, *argumentos], cwd=RAIZ, stdout=f,
                                stderr=subprocess.STDOUT, start_new_session=True)


def _parar(processos: List[Processo]) -> None:
    """SIGTERM a cada coletor; quem não sair em 15 s é encerrado à força."""
    for p in processos:
        if p.poll() is None:
            p.send_signal(signal.SIGTERM)
    for p in processos:
        try:
            p.wait(timeout=15)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()


def _iniciar_coletores_internos(arq: Arquitetura, cenario: str, pasta: str,
                                processos: List[Processo]) -> Optional[str]:
    """Espera cada banco e inicia o seu coletor; devolve o motivo da falha, se houver."""
    for coletor in arq.coletores:
        container = arq.container(coletor.servico)
        if not _esperar_banco(container):
            return f"{coletor.servico} não aceitou conexões em 120 s"
        conninfo = f"host={_ip(container)} dbname={coletor.banco} user=postgres password={SENHA_POSTGRES}"
        argumentos = ["harness/metricas/postgres.py",
                      os.path.join(pasta, f"metricas-{coletor.servico}.csv"), "--conninfo", conninfo]
        if cenario == "longa":
            argumentos.append("--pgstattuple")
        processos.append(_iniciar_coletor(
            argumentos, os.path.join(pasta, "ferramenta", f"coletor-{coletor.servico}.log")))
    return None


def _zerar_pg_stat_statements(arq: Arquitetura) -> None:
    """ADR-034. Num banco sem a extensão, o comando falha sem efeito."""
    for coletor in arq.coletores:
        _docker(["exec", arq.container(coletor.servico), "psql", "-U", "postgres",
                 "-d", coletor.banco, "-tAc", "SELECT pg_stat_statements_reset()"])


def _inspecionar(arq: Arquitetura) -> List[Dict[str, Any]]:
    """docker inspect de todos os containers da arquitetura."""
    ids = _docker(["ps", "-aq", "--filter",
                   f"label=com.docker.compose.project={arq.projeto()}"]).stdout.split()
    if not ids:
        return []
    return cast(List[Dict[str, Any]], json.loads(_docker(["inspect", *ids]).stdout))


def _servico(container: Dict[str, Any]) -> str:
    return str(container["Config"]["Labels"].get("com.docker.compose.service", ""))


def _estado(codigo_gerador: int, inspecao: List[Dict[str, Any]],
            servicos: Tuple[str, ...]) -> Tuple[str, Optional[str]]:
    """ok, oom ou falha, conforme os containers sob teste e o gerador (ADR-033)."""
    vistos = {_servico(c): c for c in inspecao}
    for servico in servicos:
        c = vistos.get(servico)
        if c is None:
            return "falha", f"{servico} não existe ao fim da execução"
        if c["State"]["OOMKilled"]:
            return "oom", f"{servico} encerrado por falta de memória"
        if c["State"]["Status"] != "running":
            return "falha", f"{servico} terminou durante a execução (código {c['State']['ExitCode']})"
    if codigo_gerador != 0:
        return "falha", f"gerador terminou com código {codigo_gerador}; veja ferramenta/compose.log"
    return "ok", None


def _imagens(inspecao: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    imagens: List[Dict[str, Any]] = []
    for c in inspecao:
        imagem_id = str(c["Image"])
        digests = _docker(["image", "inspect", "-f", "{{json .RepoDigests}}", imagem_id]).stdout.strip()
        imagens.append({"servico": _servico(c), "imagem": c["Config"]["Image"], "id": imagem_id,
                        "digests": json.loads(digests or "[]")})
    return imagens


def _recursos(inspecao: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{"servico": _servico(c), "cpuset": c["HostConfig"]["CpusetCpus"],
             "memoria_bytes": c["HostConfig"]["Memory"]} for c in inspecao]


def _capturar(arq: Arquitetura, inspecao: List[Dict[str, Any]], pasta: str) -> None:
    """Instantâneos e logs dos containers, antes de a arquitetura ser desligada."""
    registros.gravar_json(os.path.join(pasta, "instantaneos", "docker-inspect.json"),
                          {"containers": inspecao})
    for coletor in arq.coletores:
        r = _docker(["exec", arq.container(coletor.servico), "psql", "-U", "postgres",
                     "-d", coletor.banco, "-c",
                     "\\copy (SELECT * FROM pg_stat_statements) TO STDOUT WITH CSV HEADER"])
        sufixo = "csv" if r.returncode == 0 else "erro"
        with open(os.path.join(pasta, "instantaneos", f"pg_stat_statements-{coletor.servico}.{sufixo}"),
                  "w", encoding="utf-8") as f:
            f.write(r.stdout if r.returncode == 0 else r.stderr)
    for c in inspecao:
        with open(os.path.join(pasta, "ferramenta", f"{_servico(c)}.log"), "w", encoding="utf-8") as f:
            subprocess.run(["docker", "logs", str(c["Id"])], stdout=f, stderr=subprocess.STDOUT)


def _remover_restos(arq: Arquitetura) -> None:
    """Remove containers que o down não levou, como um gerador interrompido no meio."""
    ids = _docker(["ps", "-aq", "--filter",
                   f"label=com.docker.compose.project={arq.projeto()}"]).stdout.split()
    if ids:
        _docker(["rm", "-f", *ids])


def executar_uma(pl: Plano, ex: Execucao, janela: str, resultados: str,
                 repouso_s: int) -> Tuple[str, str]:
    """Roda uma execução e devolve (estado, identificador).

    Levanta ErroJanela se o ambiente deixar de ser válido e KeyboardInterrupt se o
    operador interromper; nos dois casos o manifesto é fechado como 'abortada'.
    """
    signal.signal(signal.SIGINT, _interromper_uma_vez)
    conferidos = controles.conferir(pl.espaco_livre_min_gib)
    fora = [r for r in conferidos if r.bloqueante and not r.ok]
    if fora:
        raise ErroJanela("fora do protocolo: " + "; ".join(f"{r.controle} ({r.detalhe})" for r in fora))
    cfg = ex.configuracao
    try:
        arq = ler_arquitetura(cfg.eixo, cfg.arquitetura)
    except ErroDescritor as erro:
        raise ErroJanela(str(erro)) from erro

    identificador = f"{time.strftime('%Y%m%d-%H%M%S')}_{ex.sufixo()}"
    pasta = os.path.join(resultados, cfg.eixo, identificador)
    os.makedirs(os.path.join(pasta, "ferramenta"))
    os.makedirs(os.path.join(pasta, "instantaneos"))
    log = os.path.join(pasta, "ferramenta", "orquestrador.log")
    semente_execucao = semente(pl, ex)

    def registrar(mensagem: str) -> None:
        linha = f"[{time.strftime('%H:%M:%S')}] {identificador}: {mensagem}"
        print(linha, flush=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write(linha + "\n")

    base: Dict[str, Any] = {
        "versao_formato": registros.VERSAO_FORMATO, "id": identificador, "janela": janela,
        "eixo": cfg.eixo, "arquitetura": cfg.arquitetura, "cenario": cfg.cenario,
        "concorrencia": cfg.concorrencia, "repeticao": ex.repeticao, "ordem_na_janela": ex.ordem,
        "semente": semente_execucao,
        "duracao_planejada_s": {"aquecimento": cfg.aquecimento_s, "medicao": cfg.medicao_s},
        "repositorio": registros.repositorio(), "host": registros.host(),
        "inicio": registros.agora_iso(),
    }
    controles_execucao: Dict[str, Any] = {"conferidos": registros.controles(conferidos),
                                          "volumes_restaurados": False, "fstrim": None,
                                          "repouso_s": repouso_s}
    tempos: Dict[str, int] = {}
    extra: Dict[str, Any] = {"imagens": [], "recursos": [], "gerador": None}
    manifesto = os.path.join(pasta, "manifesto.json")

    def gravar_manifesto(estado: str, motivo: Optional[str], final: bool) -> None:
        documento: Dict[str, Any] = {**base, "estado": estado, "motivo_falha": motivo,
                                     "tempos_ms": tempos, "controles": controles_execucao,
                                     **extra, "disco": registros.disco()}
        if final:
            documento["fim"] = registros.agora_iso()
            documento["arquivos"] = registros.arquivos(pasta)
        registros.gravar_json(manifesto, documento)

    gravar_manifesto("em_andamento", None, False)
    c = Compose(arq, pasta, os.path.join(pasta, "ferramenta", "compose.log"))
    processos: List[Processo] = []
    estado, motivo = "abortada", "interrompida antes do fim"
    try:
        registrar("restaurando os dados (ADR-029)")
        try:
            dados.restaurar(arq, pl.espaco_livre_min_gib)
            controles_execucao["volumes_restaurados"] = True
            controles_execucao["fstrim"] = dados.descartar()
        except dados.ErroDados as erro:
            raise ErroJanela(f"restauração dos dados: {erro}") from erro

        registrar(f"repouso de {repouso_s} s (ADR-004)")
        tempos["repouso_inicio"] = registros.agora_ms()
        time.sleep(repouso_s)

        registrar("telemetria iniciada; subindo a arquitetura")
        processos.append(_iniciar_coletor(
            ["harness/metricas/telemetria.py", os.path.join(pasta, "metricas-host.csv")],
            os.path.join(pasta, "ferramenta", "coletor-host.log")))
        if c.subir() != 0:
            estado, motivo = "falha", "a arquitetura não subiu; veja ferramenta/compose.log"
        else:
            erro_banco = _iniciar_coletores_internos(arq, cfg.cenario, pasta, processos)
            if erro_banco is not None:
                estado, motivo = "falha", erro_banco
            else:
                _zerar_pg_stat_statements(arq)
                registrar(f"gerador: {cfg.concorrencia} clientes, {cfg.aquecimento_s} s de "
                          f"aquecimento e {cfg.medicao_s} s de medição")
                codigo = c.gerador(["executar", "--saida", "/saida",
                                    "--concorrencia", str(cfg.concorrencia),
                                    "--aquecimento-s", str(cfg.aquecimento_s),
                                    "--medicao-s", str(cfg.medicao_s),
                                    "--semente", str(semente_execucao)])
                estado, motivo = _estado(codigo, _inspecionar(arq), arq.servicos_sob_teste)
    except KeyboardInterrupt:
        motivo = "interrompida pelo operador (Ctrl+C)"
        raise
    except ErroJanela as erro:
        motivo = str(erro)
        raise
    except Exception as erro:
        motivo = f"erro inesperado no orquestrador: {erro!r}"
        raise
    finally:
        registrar("parando os coletores e capturando instantâneos")
        _parar(processos)
        inspecao = _inspecionar(arq)
        extra["imagens"] = _imagens(inspecao)
        extra["recursos"] = _recursos(inspecao)
        try:
            _capturar(arq, inspecao, pasta)
        except Exception as erro:  # a captura não pode esconder o motivo original
            registrar(f"captura incompleta: {erro!r}")
        c.desligar()
        _remover_restos(arq)
        caminho_gerador = os.path.join(pasta, "gerador.json")
        if os.path.exists(caminho_gerador):
            g = registros.ler_json(caminho_gerador)
            for chave in ("aquecimento_inicio", "medicao_inicio", "medicao_fim"):
                if f"{chave}_ms" in g:
                    tempos[chave] = int(g[f"{chave}_ms"])
            extra["gerador"] = {"ferramenta": g.get("ferramenta"), "versao": g.get("versao"),
                                "parametros": g.get("parametros")}
        registrar(f"fim: {estado}" + (f" ({motivo})" if motivo else ""))
        gravar_manifesto(estado, motivo, True)
    return estado, identificador
