"""Coletor de telemetria de host e containers."""

import argparse
import csv
import glob
import os
import signal
import subprocess
import time
from types import FrameType
from typing import List, Optional, Set, Tuple, Union, Dict

Metrica = Tuple[str, str, str, Union[int, float]]
CGROUP_DOCKER: str = "/sys/fs/cgroup/system.slice"
MODELO_SSD: str = "CT1000P510SSD5"
CPUS_COLETA: Set[int] = {6, 7, 14, 15}
MS_POR_JIFFY: int = 1000 // os.sysconf("SC_CLK_TCK")

def ler_arquivo(caminho: str) -> str:
    with open(caminho, encoding="utf-8") as f:
        return f.read().strip()


def freq_cpus(cpus: List[int]) -> List[Metrica]:
    return [("host", "cpu_freq_khz", f"cpu{n}",
             int(ler_arquivo(f"/sys/devices/system/cpu/cpu{n}/cpufreq/scaling_cur_freq"))) for n in cpus]


def tempo_cpus() -> List[Metrica]:
    """Tempo ocupado e ocioso acumulado de cada CPU lógica, de /proc/stat."""
    linhas: List[Metrica] = []
    with open("/proc/stat", encoding="utf-8") as f:
        for linha in f:
            campos = linha.split()
            if not campos:
                continue

            if campos[0] == "cpu":
                continue

            if not campos[0].startswith("cpu"):
                break

            valores = [int(v) for v in campos[1:9]]
            if len(valores) >=8:
                user, nice, system, idle, iowait, irq, softirq, steal = valores[:8]
                ocupado = user + nice + system + irq + softirq + steal
                ocioso = idle + iowait

                linhas.append(("host", "cpu_ocupado_ms_total", campos[0], ocupado * MS_POR_JIFFY))
                linhas.append(("host", "cpu_ocioso_ms_total", campos[0], ocioso * MS_POR_JIFFY))

    return linhas


def temperaturas(sensor_cpu: str, nome_ssd: str, sensor_ssd: str) -> List[Metrica]:
    return [
        ("host", "cpu_temp_c", "tctl", int(ler_arquivo(sensor_cpu)) / 1000.0),
        ("host", "nvme_temp_c", nome_ssd, int(ler_arquivo(sensor_ssd)) / 1000.0),
    ]


def memoria() -> List[Metrica]:
    with open("/proc/meminfo", encoding="utf-8") as f:
        for linha in f:
            if linha.startswith("MemAvailable:"):
                partes = linha.split()
                if len(partes) > 1:
                    return [("host", "mem_disponivel_bytes", "", int(partes[1]) * 1024)]
    return []

def disco(nome: str) -> List[Metrica]:
    """Contadores de /proc/diskstats. Ali os setores têm sempre 512 bytes."""
    with open("/proc/diskstats", encoding="utf-8") as f:
        for linha in f:
            c = linha.split()
            if len(c) > 12 and c[2] == nome:
                return [
                    ("host", "disco_leituras_total", nome, int(c[3])),
                    ("host", "disco_leitura_bytes_total", nome, int(c[5]) * 512),
                    ("host", "disco_leitura_ms_total", nome, int(c[6])),
                    ("host", "disco_escritas_total", nome, int(c[7])),
                    ("host", "disco_escrita_bytes_total", nome, int(c[9]) * 512),
                    ("host", "disco_escrita_ms_total", nome, int(c[10])),
                    ("host", "disco_ocupado_ms_total", nome, int(c[12])),                    
                ]
    return []

def achar_sensor_cpu() -> str:
    """Caminho do sensor Tctl do k10temp."""
    for hwmon in glob.glob("/sys/class/hwmon/hwmon*"):
        if ler_arquivo(f"{hwmon}/name") != "k10temp":
            continue
        for rotulo in glob.glob(f"{hwmon}/temp*_label"):
            if ler_arquivo(rotulo) == "Tctl":
                return rotulo.replace("_label", "_input")

    raise RuntimeError("sensor Tctl do k10temp não encontrado")


def achar_ssd() -> Tuple[str, str, str, str]:
    """Controlador do P510: nome, sensor, disco e major:minor do disco."""
    for ctrl in glob.glob("/sys/class/nvme/nvme*"):
        if ler_arquivo(f"{ctrl}/model") != MODELO_SSD:
            continue

        nome = os.path.basename(ctrl)
        
        sensores = glob.glob(f"{ctrl}/hwmon*/temp1_input")
        if not sensores:
            raise RuntimeError(f"Sensor de temperatura não encontrado para o SSD {nome}")
        sensor = sensores[0]
        
        disco_ssd = f"{nome}n1"
        dev = ler_arquivo(f"/sys/block/{disco_ssd}/dev")
        
        return nome, sensor, disco_ssd, dev
        
    raise RuntimeError(f"SSD {MODELO_SSD} não encontrado")


def ler_chave_valor(caminho: str) -> Dict[str, str]:
    """Lê arquivos do cgroup no formato 'chave valor', um par por linha."""
    resultado: Dict[str, str] = {}
    with open(caminho, encoding="utf-8") as f:
        for linha in f:
            partes = linha.split()
            if len(partes) >= 2:
                resultado[partes[0]] = partes[1]
    return resultado


def metricas_cgroup(escopo: str, nome: str, dev: str) -> List[Metrica]:
    """Métricas de cgroup v2 de um container Docker."""
    cpu = ler_chave_valor(f"{escopo}/cpu.stat")
    eventos = ler_chave_valor(f"{escopo}/memory.events")
    memstat = ler_chave_valor(f"{escopo}/memory.stat")
    lidos: int = 0
    escritos: int = 0
    
    with open(f"{escopo}/io.stat", encoding="utf-8") as f:
        for linha in f:
            campos = linha.split()
            if campos and campos[0] == dev:
                io_dict: Dict[str, str] = {}
                for c in campos[1:]:
                    partes = c.split("=")
                    if len(partes) == 2:
                        io_dict[partes[0]] = partes[1]
                
                lidos = int(io_dict.get("rbytes", "0"))
                escritos = int(io_dict.get("wbytes", "0"))
                
    return [
        ("container", "cont_cpu_us_total", nome, int(cpu.get("usage_usec", "0"))),
        ("container", "cont_mem_bytes", nome, int(ler_arquivo(f"{escopo}/memory.current"))),
        ("container", "cont_mem_pico_bytes", nome, int(ler_arquivo(f"{escopo}/memory.peak"))),
        ("container", "cont_mem_anon_bytes", nome, int(memstat.get("anon", "0"))),
        ("container", "cont_mem_arquivo_bytes", nome, int(memstat.get("file", "0"))),
        ("container", "cont_mem_shmem_bytes", nome, int(memstat.get("shmem", "0"))),
        ("container", "cont_leitura_bytes_total", nome, lidos),
        ("container", "cont_escrita_bytes_total", nome, escritos),
        ("container", "cont_oom_total", nome, int(eventos.get("oom_kill", "0"))),
    ]


def nome_container(cid: str, nomes: Dict[str, str]) -> str:
    """Nome do container, perguntado ao Docker uma vez e guardado."""
    r = subprocess.run(
        ["docker", "inspect", "--format", "{{.Name}}", cid],
        capture_output=True, 
        text=True
    )
    if r.returncode != 0:
        return cid[:12]
    
    nome_recuperado = r.stdout.strip().lstrip("/")
    nomes[cid] = nome_recuperado
    return nome_recuperado


def containers(dev: str, nomes: Dict[str, str]) -> List[Metrica]:
    """Métricas de cgroup v2 de cada container Docker em execução."""
    linhas: List[Metrica] = []
    
    for escopo in glob.glob(f"{CGROUP_DOCKER}/docker-*.scope"):
        cid = os.path.basename(escopo)[len("docker-"):-len(".scope")]
        
        nome = nomes.get(cid)
        if not nome:
            nome = nome_container(cid, nomes)
            
        try:
            linhas.extend(metricas_cgroup(escopo, nome, dev))
        except OSError:
            continue
            
    return linhas


def main() -> None:
    p = argparse.ArgumentParser(description="Coletor de telemetria de host e containers")
    p.add_argument("saida", help="arquivo metricas.csv a criar; não pode existir")
    p.add_argument("--intervalo", type=float, default=1.0,
                   help="segundos entre leituras")
    args = p.parse_args()

    os.sched_setaffinity(0, CPUS_COLETA)
    
    cpus: List[int] = sorted(
        int(c[len("/sys/devices/system/cpu/cpu"):])
        for c in glob.glob("/sys/devices/system/cpu/cpu[0-9]*")
    )
    
    sensor_cpu: str = achar_sensor_cpu()
    nome_ssd, sensor_ssd, disco_ssd, dev_ssd = achar_ssd()
    
    nomes: Dict[str, str] = {}

    parar: bool = False

    def ao_sinal(_signum: int, _frame: Optional[FrameType]) -> None:
        nonlocal parar
        parar = True

    signal.signal(signal.SIGTERM, ao_sinal)
    signal.signal(signal.SIGINT, ao_sinal)

    with open(args.saida, "x", newline="", encoding="utf-8") as f:
        escritor = csv.writer(f)
        escritor.writerow(["ts_ms", "origem", "metrica", "rotulo", "valor"])
        
        proximo: float = time.monotonic()
        
        while not parar:
            ts_ms: int = time.time_ns() // 1_000_000
            
            linhas: List[Metrica] = (
                freq_cpus(cpus) + 
                tempo_cpus() + 
                temperaturas(sensor_cpu, nome_ssd, sensor_ssd) + 
                memoria() + 
                disco(disco_ssd) + 
                containers(dev_ssd, nomes)
            )
            
            escritor.writerows((ts_ms, *linha) for linha in linhas)
            f.flush()
            
            proximo += args.intervalo
            espera: float = proximo - time.monotonic()
            
            if espera > 0:
                time.sleep(espera)
            else:
                proximo = time.monotonic()


if __name__ == "__main__":
    main()