#!/usr/bin/env bash
# Teste slc-cliff repetido depois da Fase 3 (ADR-004, complemento do item 5).
#
# Pré-condição: abertura da janela aplicada (docs/protocolo-coleta.md, seções 1.1 a 1.3:
# máquina ociosa, swap desligado, timers parados, snaps suspensos, APST desligado).
# Usa só os comandos com sudo liberados sem senha (fstrim -v / e nvme smart-log).
#
#     systemd-inhibit --what=sleep:shutdown --who=tcc-coleta --why="caracterização do SSD" \
#         --mode=block powerprofilesctl launch -p performance -r caracterizacao -- \
#         bash ambiente/caracterizacao/2026-11-20-pos-fase3/medir.sh
set -euo pipefail

PASTA="$(cd "$(dirname "$0")" && pwd)"
ARQUIVO=/var/tmp/fio-slc-cliff
REPOUSO_S=1200
P510=$(dirname "$(grep -l CT1000P510SSD5 /sys/class/nvme/nvme*/model)")
DISPOSITIVO=/dev/$(basename "$P510")

etapa() { echo "[$(date '+%d/%m %H:%M:%S')] $*" | tee -a "$PASTA/progresso.log"; }

etapa "início; P510 em $DISPOSITIVO"
{
    echo "kernel: $(uname -r)"
    echo "governador: $(sort -u /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor | tr '\n' ' ')"
    echo "epp: $(sort -u /sys/devices/system/cpu/cpu*/cpufreq/energy_performance_preference | tr '\n' ' ')"
    echo "swap: $(swapon --show --noheadings | wc -l) dispositivo(s) ativo(s)"
    echo "fstrim.timer: $(systemctl is-active fstrim.timer || true)"
    echo "apst (pm_qos_latency_tolerance_us): $(cat "$P510/power/pm_qos_latency_tolerance_us")"
    echo "fio: $(fio --version)"
} > "$PASTA/controles.txt"
cat "$PASTA/controles.txt" | tee -a "$PASTA/progresso.log"

df -B1 / > "$PASTA/df-antes.txt"
sudo -n /usr/sbin/nvme smart-log "$DISPOSITIVO" > "$PASTA/smart-antes.txt"

etapa "sync e fstrim"
sync
sudo -n /usr/sbin/fstrim -v / | tee -a "$PASTA/progresso.log"

etapa "repouso de $REPOUSO_S s"
sleep "$REPOUSO_S"

# Temperatura do P510 a cada segundo durante o teste.
(while true; do
     echo "$(date +%s),$(( $(cat "$P510"/hwmon*/temp1_input) / 1000 ))"
     sleep 1
 done) > "$PASTA/nvme-temp.csv" &
TEMPERATURA=$!
trap 'kill $TEMPERATURA 2>/dev/null || true; rm -f "$ARQUIVO"' EXIT

etapa "fio slc-cliff: 400 GiB sequenciais"
cd "$PASTA"
fio --name=slc-cliff --filename="$ARQUIVO" --size=400g --rw=write --bs=1M \
    --ioengine=io_uring --iodepth=32 --direct=1 \
    --write_bw_log=slc-cliff --log_avg_msec=1000 --output=slc-cliff.txt
kill "$TEMPERATURA"

etapa "remoção do arquivo, sync e fstrim"
rm -f "$ARQUIVO"
sync
sudo -n /usr/sbin/fstrim -v / | tee -a "$PASTA/progresso.log"

df -B1 / > "$PASTA/df-depois.txt"
sudo -n /usr/sbin/nvme smart-log "$DISPOSITIVO" > "$PASTA/smart-depois.txt"
etapa "fim"
