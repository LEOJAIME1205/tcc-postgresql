# Protocolo de coleta

Este documento reúne, na ordem de execução, os controles que precisam estar
aplicados antes e durante qualquer coleta. Cada item remete à decisão que o
justifica em `docs/decisoes-arquiteturais.md`. Os passos marcados com
**(harness)** serão automatizados na Etapa 2.1; até lá, são feitos à mão.

## Janelas de coleta

Uma janela é um bloco contínuo de horas em que a máquina fica dedicada ao
experimento. Ninguém a usa, e nada roda além do sistema sob teste, do gerador de
carga e da coleta.

- Calibração e execuções curtas ocupam janelas de algumas horas, com várias
  execuções em ordem aleatorizada.
- Cada execução de longa duração ocupa uma janela inteira, noturna ou de fim de
  semana.
- Toda janela começa pela abertura (seção 1) e termina pelo encerramento
  (seção 3). Se a máquina for reiniciada no meio, a abertura é refeita, porque
  swap, timers e APST voltam ao padrão no reinício.

## 1. Abertura da janela

### 1.1 Máquina ociosa

- [ ] Navegador, clientes de sincronização e programas não essenciais fechados
  (ADR-019)
- [ ] Nenhuma atualização em andamento. O comando abaixo não deve listar nada
  além de `unattended-upgrade-shutdown --wait-for-signal`, que fica ocioso:

  ```bash
  pgrep -a -f "apt|unattended-upgrade|snap refresh"
  ```

### 1.2 Controles do sistema

```bash
# swap desligado (ADR-016)
sudo swapoff -a

# descarte e manutenção fora da janela (ADR-017 e ADR-019)
sudo systemctl stop fstrim.timer apt-daily.timer apt-daily-upgrade.timer \
    fwupd-refresh.timer update-notifier-download.timer man-db.timer \
    anacron.timer logrotate.timer dpkg-db-backup.timer e2scrub_all.timer
sudo snap refresh --hold=24h          # ajustar ao tamanho da janela

# APST do NVMe desligado (ADR-020)
echo 0 | sudo tee /sys/class/nvme/nvme0/power/pm_qos_latency_tolerance_us > /dev/null
```

### 1.3 Verificação

```bash
cat /sys/class/nvme/nvme0/model                            # CT1000P510SSD5
swapon --show                                              # sem saída
systemctl is-active fstrim.timer apt-daily-upgrade.timer   # inactive
sudo nvme get-feature /dev/nvme0 -f 0x0c -H | head -2      # Current value:00000000
```

O primeiro comando confirma que `nvme0` é o P510. A numeração dos dispositivos
NVMe pode mudar entre inicializações.

### 1.4 Registro de abertura

- [ ] Ocupação do disco e SMART salvos com os dados da janela (ADR-004):
  `df -B1 /` e `sudo nvme smart-log /dev/nvme0`
- [ ] Digests das imagens em uso registrados (ADR-011)

## 2. Cada execução

A sequência inteira de cada execução roda dentro de
`powerprofilesctl launch -p performance -r coleta -- <comando>`, que põe
governador e preferência de energia em `performance` e volta ao perfil
`balanced` quando o comando termina (ADR-015).

1. Restaurar o estado inicial dos volumes, exceto no teste de longa duração
   (ADR-013). **(harness)**
2. Descartar os blocos livres:

   ```bash
   sync && sudo fstrim -v /
   ```

   O `sync` é obrigatório: sem ele, o ext4 ainda não liberou os blocos
   apagados e o `fstrim` não os descarta (ADR-017).
3. Repouso de **20 minutos**, com os containers parados. Recupera o cache pSLC
   (ADR-004) e cobre o resfriamento térmico (ADR-012).
4. Iniciar a telemetria de host e containers (`metricas-host.csv`): frequência
   e temperatura da CPU, temperatura do NVMe, memória do host, taxa e latência
   de escrita do disco e bytes gravados por container (ADR-004 e ADR-012).
   **(harness)**
5. Subir a arquitetura sob teste com `cpuset` por núcleo físico (sistema sob
   teste em `0-5,8-13`; gerador de carga e coleta em `6-7,14-15`) e limites de
   memória (ADR-002, ADR-003 e ADR-018). Todo comando do Compose, inclusive o
   `down`, precisa das variáveis de diretório de dados definidas, como
   `TCC_DADOS_POSTGRES`; sem elas o Compose recusa rodar. Conferir:

   ```bash
   docker inspect -f '{{.Name}} cpus={{.HostConfig.CpusetCpus}} mem={{.HostConfig.Memory}}' $(docker ps -q)
   ```

   Logo depois de subir, iniciar os coletores internos de cada sistema, como o
   do PostgreSQL (`metricas-postgres.csv`). Eles precisam do IP do container na
   rede interna, que só existe depois do `up`, e esperam sozinhos o banco
   aceitar conexões. **(harness)**
6. Aquecer, descartando o período inicial, e executar a carga (ADR-013).
   **(harness)**
7. Coletar resultados e telemetria com os metadados da execução: arquitetura,
   eixo, nível de concorrência, duração e digests (`resultados/README.md`).
   Encerrar a arquitetura. **(harness)**

Regras que valem para todas as execuções:

- A ordem das arquiteturas é aleatorizada dentro da janela (ADR-012).
- Um container encerrado por falta de memória é registrado como falha da
  execução, nunca descartado (ADR-016).
- Uma queda de vazão só é atribuída ao SSD pelo critério fixado na ADR-004.

## 3. Encerramento da janela

```bash
# APST de volta ao padrão (ADR-020)
echo 100000 | sudo tee /sys/class/nvme/nvme0/power/pm_qos_latency_tolerance_us > /dev/null

# swap, timers e snaps de volta
sudo swapon -a
sudo systemctl start fstrim.timer apt-daily.timer apt-daily-upgrade.timer \
    fwupd-refresh.timer update-notifier-download.timer man-db.timer \
    anacron.timer logrotate.timer dpkg-db-backup.timer e2scrub_all.timer
sudo snap refresh --unhold
```

- [ ] Ocupação do disco e SMART salvos de novo: `df -B1 /` e
  `sudo nvme smart-log /dev/nvme0`

Reiniciar a máquina também restaura swap, timers e APST. A suspensão dos snaps
vence sozinha no prazo dado.

## 4. Caracterização do SSD com fio

Mesma abertura e mesmo encerramento de uma janela de coleta. Antes de cada
teste, `sync && sudo fstrim -v /` seguido de 20 minutos de repouso. Os comandos
de cada teste estão em `ambiente/README.md`.

## 5. Acompanhamento ao vivo

Em terminais separados, durante qualquer janela:

```bash
iostat -dxm 5 nvme0n1                                        # vazão, latência e utilização do disco
watch -n 5 'echo $(( $(cat /sys/class/nvme/nvme0/hwmon*/temp1_input) / 1000 )) °C'   # temperatura do P510
watch -n 2 'grep MHz /proc/cpuinfo | sort -t: -k2 -n | tail -4'                       # frequências mais altas
```
