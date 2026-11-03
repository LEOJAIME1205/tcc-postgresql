"""Conferência dos controles do protocolo de coleta."""

import glob
import os
import subprocess
from dataclasses import dataclass
from typing import List, Set, Optional

MODELO_SSD: str = "CT1000P510SSD5"
TIMERS_PARADOS: List[str] = [
    "fstrim", "apt-daily", "apt-daily-upgrade", "fwupd-refresh",
    "update-notifier-download", "man-db", "anacron", "logrotate",
    "dpkg-db-backup", "e2scrub_all",
]
NAVEGADORES: List[str] = ["firefox", "chrome", "chromium", "brave", "msedge", "opera", "vivaldi"]
PROGRAMAS_PESADOS: List[str] = ["code", "claude"]


@dataclass(frozen=True)
class Resultado:
    controle: str
    ok: bool
    bloqueante: bool
    detalhe: str


def ler_arquivo(caminho: str) -> str:
    with open(caminho, encoding="utf-8") as f:
        return f.read().strip()


def controlador_ssd() -> Optional[str]:
    """Controlador do P510 (nvme0, nvme1...): a numeração muda entre inicializações."""
    for modelo in sorted(glob.glob("/sys/class/nvme/nvme*/model")):
        if ler_arquivo(modelo) == MODELO_SSD:
            return os.path.basename(os.path.dirname(modelo))
    return None


def comandos_sudo() -> List[List[str]]:
    """Os comandos que a regra do sudoers precisa liberar, com o controlador do P510."""
    comandos = [["/usr/sbin/fstrim", "-v", "/"]]
    ctrl = controlador_ssd()
    if ctrl is not None:
        comandos.append(["/usr/sbin/nvme", "smart-log", f"/dev/{ctrl}"])
    return comandos


def processos(nomes: List[str]) -> List[str]:
    """Quais dos nomes dados estão entre os processos em execução."""
    encontrados: Set[str] = set()
    for comm in glob.glob("/proc/[0-9]*/comm"):
        try:
            nome = ler_arquivo(comm)
        except OSError:
            continue
        if nome in nomes:
            encontrados.add(nome)
    return sorted(encontrados)


def swap() -> Resultado:
    """ADR-016: nenhuma área de swap ativa."""
    with open("/proc/swaps", encoding="utf-8") as f:
        ativas = [linha.split()[0] for linha in f.readlines()[1:] if linha.strip()]
    detalhe = "nenhuma área ativa" if not ativas else "ativas: " + ", ".join(ativas)
    return Resultado("swap desligado", not ativas, True, detalhe)


def timers() -> Resultado:
    """ADR-017 e ADR-019: timers de descarte e de manutenção parados."""
    unidades = [f"{t}.timer" for t in TIMERS_PARADOS]
    r = subprocess.run(["systemctl", "is-active", *unidades], capture_output=True, text=True)
    ativos = [u for u, estado in zip(unidades, r.stdout.split()) if estado != "inactive"]
    detalhe = f"{len(unidades)} parados" if not ativos else "ativos: " + ", ".join(ativos)
    return Resultado("timers parados", not ativos, True, detalhe)


def ssd() -> Resultado:
    """O P510 precisa estar presente: APST e SMART usam o controlador achado aqui."""
    ctrl = controlador_ssd()
    detalhe = f"controlador {ctrl}" if ctrl is not None else f"{MODELO_SSD} não encontrado"
    return Resultado("P510 encontrado", ctrl is not None, True, detalhe)


def apst() -> Resultado:
    """ADR-020: transição autônoma de energia do P510 desligada."""
    ctrl = controlador_ssd()
    if ctrl is None:
        return Resultado("APST desligado", False, True, f"{MODELO_SSD} não encontrado")
    valor = ler_arquivo(f"/sys/class/nvme/{ctrl}/power/pm_qos_latency_tolerance_us")
    return Resultado("APST desligado", valor == "0", True,
                     f"{ctrl}: pm_qos_latency_tolerance_us = {valor}")


def energia() -> Resultado:
    """ADR-015: governador e preferência de energia em performance, boost ligado."""
    cpus = sorted(glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq"))
    fora = [
        os.path.basename(os.path.dirname(c))
        for c in cpus
        if ler_arquivo(f"{c}/scaling_governor") != "performance"
        or ler_arquivo(f"{c}/energy_performance_preference") != "performance"
    ]
    boost = ler_arquivo("/sys/devices/system/cpu/cpufreq/boost")
    ok = not fora and boost == "1"
    detalhe = (f"{len(cpus)} processadores em performance, boost ligado" if ok
               else f"{len(fora)} processadores fora de performance; boost = {boost}")
    return Resultado("energia em performance", ok, True, detalhe)


def suspensao_bloqueada() -> Resultado:
    """Protocolo de coleta: suspensão e desligamento bloqueados pela trava tcc-coleta."""
    r = subprocess.run(["systemd-inhibit", "--list", "--no-pager", "--no-legend"],
                       capture_output=True, text=True)
    ativa = any(linha.startswith("tcc-coleta ") and "sleep" in linha
                and "shutdown" in linha and linha.rstrip().endswith("block")
                for linha in r.stdout.splitlines())
    detalhe = ("trava tcc-coleta ativa" if ativa
               else "sem a trava tcc-coleta; rode dentro de systemd-inhibit "
                    "(docs/protocolo-coleta.md, seção 1.5)")
    return Resultado("suspensão e desligamento bloqueados", ativa, True, detalhe)



def containers() -> Resultado:
    """Nenhum container rodando antes de a execução subir a sua arquitetura."""
    r = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True)
    if r.returncode != 0:
        return Resultado("nenhum container rodando", False, True, f"docker ps falhou: {r.stderr.strip()}")
    nomes = r.stdout.split()
    detalhe = "nenhum" if not nomes else "rodando: " + ", ".join(nomes)
    return Resultado("nenhum container rodando", not nomes, True, detalhe)


def coletores_orfaos() -> Resultado:
    """Nenhum coletor rodando antes da execução: sobra de um orquestrador que caiu."""
    r = subprocess.run(["pgrep", "-f", "harness/metricas/"], capture_output=True, text=True)
    pids = r.stdout.split()
    detalhe = "nenhum" if not pids else "rodando, PID " + ", ".join(pids)
    return Resultado("nenhum coletor órfão", not pids, True, detalhe)



def navegadores() -> Resultado:
    """Protocolo de coleta: nenhum navegador aberto."""
    abertos = processos(NAVEGADORES)
    detalhe = "nenhum" if not abertos else "abertos: " + ", ".join(abertos)
    return Resultado("nenhum navegador aberto", not abertos, True, detalhe)


def espaco(min_gib: float) -> Resultado:
    """Espaço livre no sistema de arquivos dos dados e dos resultados."""
    st = os.statvfs("/")
    livre_gib = st.f_bavail * st.f_frsize / 2**30
    return Resultado(f"espaço livre de pelo menos {min_gib:.0f} GiB", livre_gib >= min_gib, True,
                     f"{livre_gib:.0f} GiB livres")


def liberados_sem_senha() -> Set[str]:
    """Comandos das entradas NOPASSWD do usuário, lidos da listagem do sudo."""
    r = subprocess.run(["sudo", "-n", "-l"], capture_output=True, text=True)
    liberados: Set[str] = set()
    for linha in r.stdout.splitlines():
        _, achou, comandos = linha.partition("NOPASSWD:")
        if achou:
            liberados.update(c.strip() for c in comandos.split(","))
    return liberados


def sudo_sem_senha() -> Resultado:
    """fstrim e nvme smart-log liberados sem senha pela regra do sudoers."""
    liberados = liberados_sem_senha()
    negados = [" ".join(c) for c in comandos_sudo() if " ".join(c) not in liberados]
    detalhe = "fstrim e nvme smart-log liberados" if not negados else "exigem senha: " + "; ".join(negados)
    return Resultado("sudo sem senha para a coleta", not negados, True, detalhe)



def programas_pesados() -> Resultado:
    """Aviso: programas que pesam na máquina, aceitáveis só em pilotos."""
    abertos = processos(PROGRAMAS_PESADOS)
    detalhe = "nenhum" if not abertos else "abertos: " + ", ".join(abertos)
    return Resultado("sem programas pesados abertos", not abertos, False, detalhe)


def conferir(espaco_min_gib: float) -> List[Resultado]:
    """Todos os controles, na ordem da tabela de harness/README.md."""
    return [swap(), timers(), ssd(), apst(), energia(), suspensao_bloqueada(), containers(),
            coletores_orfaos(), navegadores(), espaco(espaco_min_gib), sudo_sem_senha(),
            programas_pesados()]

