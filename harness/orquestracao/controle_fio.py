"""Execução de controle do SSD com fio, depois da longa duração (ADR-004, item 2, revisto na ADR-069).

    python -m harness.orquestracao.controle_fio --taxa-mib-s R [--duracao-s D] [--resultados DIR]

Escrita aleatória de 8 KiB, o padrão rand8k da caracterização (ambiente/README.md): quatro
processos com fila de 32, num arquivo de 64 GiB, com a vazão total limitada a R MiB/s, a maior
vazão média de escrita no SSD entre as quatro execuções longas. Segue o protocolo de uma
execução: controles conferidos, sync e fstrim, repouso de 1200 s, telemetria do host a cada 1 s,
fio no cpuset do sistema sob teste, e SMART e ocupação do disco no início e no fim. Roda dentro
das travas da janela (docs/protocolo-coleta.md, seção 1.5).
"""

import argparse
import os
import subprocess
import sys
import time
from typing import Any, Dict, List

from harness.orquestracao import controles, dados, plano, registros
from harness.orquestracao.arquitetura import RAIZ

ARQUIVO: str = "/var/tmp/fio-controle"
TAMANHO: str = "64g"
PROCESSOS: int = 4
CPUSET_SOB_TESTE: str = "0-5,8-13"
DURACAO_PADRAO_S: int = 43200  # a duração das execuções longas (ADR-069, item 2)


def registrar(pasta: str, mensagem: str) -> None:
    linha = f"[{time.strftime('%H:%M:%S')}] controle_fio: {mensagem}"
    print(linha, flush=True)
    with open(os.path.join(pasta, "ferramenta", "orquestrador.log"), "a", encoding="utf-8") as f:
        f.write(linha + "\n")


def comando_fio(pasta: str, taxa_mib_s: float, duracao_s: int) -> List[str]:
    """fio do rand8k com a vazão total limitada; --rate vale por processo."""
    por_processo_kib = int(taxa_mib_s * 1024 / PROCESSOS)
    prefixo = os.path.join(pasta, "fio")
    return ["taskset", "-c", CPUSET_SOB_TESTE, "fio", "--name=controle", f"--filename={ARQUIVO}",
            f"--size={TAMANHO}", "--rw=randwrite", "--bs=8k", "--ioengine=io_uring", "--iodepth=32",
            f"--numjobs={PROCESSOS}", "--direct=1", f"--rate={por_processo_kib}k", "--time_based",
            f"--runtime={duracao_s}", "--group_reporting", f"--write_bw_log={prefixo}",
            f"--write_lat_log={prefixo}", "--log_avg_msec=1000",
            f"--output={os.path.join(pasta, 'fio.txt')}"]


def executar(taxa_mib_s: float, duracao_s: int, repouso_s: int, resultados: str) -> int:
    conferidos = controles.conferir(100.0)
    fora = [r for r in conferidos if r.bloqueante and not r.ok]
    if fora:
        print("Fora do protocolo: " + "; ".join(f"{r.controle} ({r.detalhe})" for r in fora))
        return 1
    identificador = f"{time.strftime('%Y%m%d-%H%M%S')}_controle_fio"
    pasta = os.path.join(resultados, "controle", identificador)
    os.makedirs(os.path.join(pasta, "ferramenta"))
    texto, smart = registros.smart()
    with open(os.path.join(pasta, "smart-abertura.txt"), "w", encoding="utf-8") as f:
        f.write(texto)
    manifesto: Dict[str, Any] = {
        "id": identificador, "tipo": "controle do SSD com fio (ADR-004, item 2; ADR-069, item 5)",
        "taxa_mib_s": taxa_mib_s, "duracao_s": duracao_s, "repouso_s": repouso_s,
        "controles": registros.controles(conferidos), "repositorio": registros.repositorio(),
        "host": registros.host(), "inicio": registros.agora_iso(),
        "disco_abertura": registros.disco(), "smart_abertura": smart,
    }
    comando = comando_fio(pasta, taxa_mib_s, duracao_s)
    manifesto["comando"] = comando
    registros.gravar_json(os.path.join(pasta, "manifesto.json"), {**manifesto, "estado": "em_andamento"})
    estado = "abortada"
    telemetria = None
    try:
        registrar(pasta, "sync e fstrim (ADR-017)")
        manifesto["fstrim"] = dados.descartar()
        registrar(pasta, f"repouso de {repouso_s} s (ADR-004)")
        time.sleep(repouso_s)
        telemetria = subprocess.Popen(
            [sys.executable, os.path.join(RAIZ, "harness", "metricas", "telemetria.py"),
             os.path.join(pasta, "metricas-host.csv")],
            stdout=subprocess.DEVNULL, stderr=open(os.path.join(pasta, "ferramenta", "coletor-host.log"), "w",
                                                    encoding="utf-8"))
        registrar(pasta, f"fio: escrita aleatória de 8 KiB a {taxa_mib_s:.1f} MiB/s por {duracao_s} s")
        manifesto["fio_inicio_ms"] = registros.agora_ms()
        codigo = subprocess.run(comando).returncode
        manifesto["fio_fim_ms"] = registros.agora_ms()
        estado = "ok" if codigo == 0 else "falha"
        manifesto["codigo_fio"] = codigo
    except KeyboardInterrupt:
        estado = "abortada"
        raise
    finally:
        if telemetria is not None:
            telemetria.terminate()
            telemetria.wait(timeout=15)
        if os.path.exists(ARQUIVO):
            os.remove(ARQUIVO)
        try:
            manifesto["fstrim_final"] = dados.descartar()
        except dados.ErroDados as erro:
            manifesto["fstrim_final"] = f"falhou: {erro}"
        texto, smart = registros.smart()
        with open(os.path.join(pasta, "smart-encerramento.txt"), "w", encoding="utf-8") as f:
            f.write(texto)
        manifesto.update({"estado": estado, "fim": registros.agora_iso(),
                          "disco_encerramento": registros.disco(), "smart_encerramento": smart,
                          "arquivos": registros.arquivos(pasta)})
        registros.gravar_json(os.path.join(pasta, "manifesto.json"), manifesto)
        registrar(pasta, f"fim: {estado}")
    return 0 if estado == "ok" else 1


def main() -> None:
    p = argparse.ArgumentParser(description="Controle do SSD com fio (ADR-004, item 2; ADR-069)")
    p.add_argument("--taxa-mib-s", type=float, required=True,
                   help="maior vazão média de escrita no SSD entre as execuções longas")
    p.add_argument("--duracao-s", type=int, default=DURACAO_PADRAO_S)
    p.add_argument("--repouso-s", type=int, default=plano.REPOUSO_S)
    p.add_argument("--resultados", default=os.path.join(RAIZ, "resultados", "brutos"))
    args = p.parse_args()
    taxa: float = args.taxa_mib_s
    duracao: int = args.duracao_s
    repouso: int = args.repouso_s
    resultados: str = args.resultados
    if taxa <= 0 or duracao < 1:
        print("--taxa-mib-s precisa ser positiva e --duracao-s pelo menos 1")
        sys.exit(2)
    sys.exit(executar(taxa, duracao, repouso, resultados))


if __name__ == "__main__":
    main()
