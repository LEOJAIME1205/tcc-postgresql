# Ambiente de Medição

Todos os experimentos são executados em uma única máquina, especificada abaixo.
Os valores absolutos aqui obtidos dependem deste hardware; as comparações
relativas entre arquiteturas é que devem se sustentar em outros ambientes.

## Especificação

| Componente | Especificação |
|---|---|
| Processador | AMD Ryzen 7 9700X — 8 núcleos, 16 threads, arquitetura Zen 5 |
| Memória | 32 GB DDR5-6000 (2 × 16 GB, perfil EXPO) |
| Armazenamento | SSD NVMe Crucial P510 1 TB, PCIe 5.0 x4, com dissipador |
| GPU | ASUS GeForce RTX 5070 |
| Placa-mãe | ASUS PRIME B650EM-A, BIOS 0221 |
| Sistema operacional | Ubuntu 26.04.1 LTS, nativo, no próprio P510 |
| Versão do Docker | Docker CE 29.9.2 |
| Versão do kernel | 7.0.0-38-generic |

## Alocação de recursos

A divisão de núcleos entre sistema sob teste e gerador de carga está definida
em `../harness/orquestracao/` e é idêntica entre as arquiteturas comparadas em
cada eixo. Ver ADR-002.

Os limites de memória por container e os parâmetros internos de cada sistema
estão nos arquivos de composição de cada eixo. Ver ADR-003.

## Caracterização do armazenamento

Antes de qualquer coleta, o dispositivo NVMe é caracterizado com `fio` para
determinar:

1. O ponto de saturação do cache pSLC
2. A vazão de escrita sequencial e aleatória em regime permanente
3. A curva de temperatura sob escrita sustentada prolongada

Isso é necessário porque o Eixo C consiste em escrita sustentada por horas, e
uma queda de vazão originada no dispositivo poderia ser confundida com
degradação do banco de dados. Ver ADR-004.

### Execução de 24/10/2026

Três testes com fio 3.41, sobre arquivo no ext4 montado em `/`, com o sistema
de arquivos 3% ocupado (19 GB de 915 GB). O dispositivo estava em repouso, a
39 °C, havia pelo menos seis minutos antes do primeiro teste.

| Teste | Horário | O que simula |
|---|---|---|
| `slc-cliff` | 14:15–14:20 | Escrita sequencial sustentada até muito além do cache |
| `rand8k` | 14:36–15:06 | Escrita aleatória de páginas de 8 KiB |
| `wal-fdatasync` | 15:12–15:17 | Commit síncrono de um único cliente |

### Cache pSLC e escrita sequencial sustentada

400 GiB gravados sequencialmente em blocos de 1 MiB, fila de profundidade 32,
um processo, vazão registrada a cada segundo.

| Fase | Tempo | Vazão média | Volume acumulado |
|---|---|---|---|
| 1 | 0–7 s | 9.127 MiB/s | 0 → 62 GiB |
| 2 | 8–104 s | ~1.100 MiB/s | 62 → 168 GiB |
| 3 | 105–191 s | 935 MiB/s | 168 → 247 GiB |
| 4 | 192–207 s | 606 MiB/s (mínimo de 484) | 247 → 257 GiB |
| 5 | 209–278 s | 2.052 MiB/s | 258 → 399 GiB |

A fase 1 é o cache pSLC: o controlador grava as células em modo SLC, de um bit
por célula, e a vazão coincide com a especificação do fabricante. O cache é
dinâmico e seu tamanho depende do espaço livre. Os 62 GiB valem para a ocupação
em que o teste foi feito.

Nas fases 2 a 4, com o cache cheio, a vazão cai para 0,5–1,1 GiB/s. Na fase 5
ela sobe para 2,0 GiB/s e se mantém estável até o fim. A explicação mais
provável é que, nas fases 2 a 4, o controlador grava os dados novos em TLC
enquanto transfere para TLC o conteúdo que estava no cache, e as duas tarefas
disputam a mesma banda. Terminada a transferência, a escrita em TLC passa a ter
a banda inteira. Esse comportamento é típico de controladores com cache SLC
dinâmico, mas o firmware não documenta o mecanismo: trata-se de inferência a
partir do formato da curva.

A vazão média do teste inteiro, 1.470 MiB/s, não descreve nenhuma das fases e
não deve ser usada isoladamente.

| Grandeza | Valor |
|---|---|
| Tamanho do cache pSLC, com o disco 3% ocupado | ≈ 62 GiB |
| Vazão no cache | ≈ 9,1 GiB/s |
| Piso da escrita sequencial sustentada (fase 4) | ≈ 0,5–0,6 GiB/s |
| Escrita sequencial direta em TLC (fase 5) | ≈ 2,0 GiB/s |

Duas verificações indicam que a curva reflete o dispositivo:

- **Não há efeito térmico.** Na passagem da fase 4 para a 5, a vazão dobrou
  enquanto a temperatura subia de 61 °C para 68 °C. O SMART registra zero
  transições de gerenciamento térmico.
- **A latência é do dispositivo, não do cache de páginas do sistema.** Com
  32 MiB em voo, a lei de Little prevê ~3,4 ms de latência a 9,1 GiB/s. O fio
  mediu 3,1 ms no 10º percentil, e 15,0% das operações ficaram abaixo de 4 ms,
  praticamente a fração do volume gravada no cache (15,5%).

### Escrita aleatória de 8 KiB

Quatro processos com fila de profundidade 32 cada, sobre um arquivo de 64 GiB,
durante 30 minutos, totalizando 1,5 TiB gravados.

Vazão média de 857 MiB/s, cerca de 110 mil IOPS. A partir do terceiro minuto, a
média por minuto ficou entre 847 e 908 MiB/s, sem tendência de queda, e o menor
valor por segundo foi 625 MiB/s. Os dois primeiros minutos foram mais lentos,
com médias de 516 e 752 MiB/s; a causa não foi investigada.

Latência por operação: mediana de 0,69 ms, p99 de 3,98 ms e p99,99 de 125 ms.

### fsync do WAL

Escrita sequencial de 8 KiB com `fdatasync` após cada operação, um processo,
fila de profundidade 1, durante 5 minutos.

1.406 operações por segundo. O `fdatasync` levou em média 0,71 ms, com p99 de
0,95 ms e p99,99 de 3,1 ms. É a ordem de grandeza do teto de um cliente que
confirma cada transação isoladamente, sem agrupamento de commits.

### Temperatura

| Teste | Início | Máximo | Comportamento |
|---|---|---|---|
| `slc-cliff` | 39 °C | 68 °C | Ainda subindo ao fim dos 4,6 minutos |
| `rand8k` | 49 °C | 65 °C | Estável em 63–64 °C a partir de ~9 minutos |
| `wal-fdatasync` | 42 °C | 50 °C | — |

O SMART lido após os testes não registra tempo acima da temperatura de aviso
nem transições de gerenciamento térmico. Não houve throttling.

### Limitações desta execução

- A linha de comando do fio não foi registrada. Os parâmetros descritos acima
  foram reconstruídos a partir da saída do próprio fio.
- Não há registro de quais controles do protocolo de medição estavam aplicados
  (governador de CPU, swap, `fstrim` prévio).
- Cada teste foi executado uma única vez. O tamanho do cache foi reproduzido
  na execução de 25/10/2026, descrita adiante.
- Como o tamanho do cache depende do espaço livre, os valores valem para o
  disco 3% ocupado.

### Arquivos

| Arquivo | Conteúdo |
|---|---|
| `caracterizacao/slc-cliff.txt` | Saída do fio do teste sequencial |
| `caracterizacao/slc-cliff_bw.1.log` | Vazão por segundo do teste sequencial |
| `caracterizacao/rand8k.txt` | Saída do fio do teste aleatório |
| `caracterizacao/rand8k_bw.{1..4}.log` | Vazão por segundo de cada processo do teste aleatório |
| `caracterizacao/wal-fdatasync.txt` | Saída do fio do teste de fsync |
| `caracterizacao/nvme-temp.log` | Temperatura do NVMe a cada 5 s, de 14:09 a 15:19 |
| `caracterizacao/smart-pos-fio.txt` | Log SMART lido após os testes |
| `caracterizacao/inventario.txt` | Inventário de hardware e sistema |

Nos arquivos `_bw`, as colunas são: tempo em ms, vazão em KiB/s e direção
(1 = escrita).

### Recuperação do cache — execução de 25/10/2026

O objetivo era saber quanto tempo o cache pSLC leva para voltar depois de
esgotado, para definir o repouso entre a restauração dos volumes e cada
execução (revisão da ADR-004).

Disco 3% ocupado. Antes do primeiro ciclo, `fstrim` de todo o espaço livre e
10 minutos de repouso. Controles aplicados: perfil `performance` do
`power-profiles-daemon` (governador e preferência de energia em `performance`
nos 16 processadores, boost ligado), swap desligado, `fstrim.timer` e timers de
manutenção parados.

Para cada repouso T de 60, 300, 900 e 1800 segundos, em sequência, tudo dentro
de `powerprofilesctl launch -p performance -- ...`:

```bash
# 1. esgota o cache
fio --name=esgota-T --filename=/var/tmp/fio-a --size=100g --rw=write --bs=1M \
    --ioengine=io_uring --iodepth=32 --direct=1 \
    --write_bw_log=esgota-T --log_avg_msec=1000 --output=esgota-T.txt
rm /var/tmp/fio-a && sudo fstrim -v /

# 2. repouso de T segundos
sleep T

# 3. mede o cache recuperado: GiB gravados acima de 8 GiB/s
fio --name=recupera-T --filename=/var/tmp/fio-b --size=80g --rw=write --bs=1M \
    --ioengine=io_uring --iodepth=32 --direct=1 \
    --write_bw_log=recupera-T --log_avg_msec=1000 --output=recupera-T.txt
rm /var/tmp/fio-b && sudo fstrim -v /
```

O passo 1 de cada ciclo também mede o cache disponível naquele momento:

| Passo 1 do ciclo | Condição | Cache disponível |
|---|---|---|
| 60 s | 10 min de repouso após descarte de todo o espaço livre | 62,2 GiB |
| 300 s | Segundos após descarte dos 180 GiB do ciclo anterior | 62,3 GiB |
| 900 s | Idem | 52,6 GiB |
| 1800 s | Idem | 60,8 GiB |

- **O tamanho do cache se repetiu.** Após repouso e descarte completos, 62,2 GiB,
  contra 62,4 GiB em 24/10. O teto de todas as medições ficou entre 62,1 e
  62,3 GiB.
- **Com 15 e 30 minutos de repouso, o cache voltou inteiro**: 62,1 e 62,2 GiB.
- **Com 1 e 5 minutos, a recuperação foi parcial e não monotônica**: 53,7 e
  44,6 GiB, ou seja, 5 minutos recuperaram menos que 1. No ciclo de 300 segundos, os primeiros 4 segundos da
  medição correram a ~0,8 GiB/s antes de o cache aparecer, sinal de que o
  controlador ainda trabalhava. Com uma medição por ponto, não é possível traçar
  a curva de recuperação entre 1 e 15 minutos.
- **O descarte sozinho não garante cache cheio de imediato**: segundos depois de
  descartar 180 GiB, o cache disponível variou de 52,6 a 62,3 GiB.
- **O piso pós-cache se repetiu.** Só no passo 1 do primeiro ciclo, logo após o
  descarte de todo o espaço livre, a vazão depois do cache ficou em ~1,1 GiB/s
  até o fim, como na fase 2 de 24/10. No passo 1 do ciclo de 300 segundos, ela
  alternou trechos de 0,7–0,8 GiB/s com novas rajadas de cache. Em todas as
  outras medições, inclusive após 15 e 30 minutos de repouso, ficou em
  ~1,1 GiB/s por cerca de 11 segundos e caiu para 0,4–0,8 GiB/s, a faixa da
  fase 4 de 24/10. A duração do regime de ~1,1 GiB/s depende do histórico do
  disco; o piso de ~0,5 GiB/s aparece de forma consistente e é o limite
  conservador adequado.
- **A temperatura volta ao repouso no mesmo intervalo.** O máximo foi 56 °C, e o
  dispositivo voltou a ~38 °C em 10 a 12 minutos. O SMART não registrou eventos
  térmicos. Foram gravados 720 GiB no total.

**Repouso adotado: 20 minutos**, contados depois de `sync` e `fstrim -v /`, entre
a restauração dos volumes e o início de cada execução (ADR-004 e ADR-017). A
recuperação completa foi medida com 15 minutos; os 5 minutos adicionais são
margem de precaução, porque houve uma única medição por valor de repouso.

Limitações:

- Uma medição por valor de repouso.
- O pior caso medido foi uma escrita de 100 GiB, acima do tamanho do cache.
  Restaurações de volumes menores devem exigir menos repouso, mas isso não foi
  medido.
- VS Code aberto durante o teste.

Arquivos em `caracterizacao/2026-10-25-recuperacao/`:

| Arquivo | Conteúdo |
|---|---|
| `progresso.log` | Etapas com horário, linhas de progresso do fio e resultado de cada ciclo |
| `esgota-T.txt`, `recupera-T.txt` | Saída do fio de cada passo |
| `esgota-T_bw.1.log`, `recupera-T_bw.1.log` | Vazão por segundo de cada passo |
| `nvme-temp.log` | Temperatura do P510 a cada 5 s |
| `smart-antes.txt`, `smart-depois.txt` | Log SMART antes e depois do teste |
| `df-antes.txt`, `df-depois.txt` | Ocupação do sistema de arquivos antes e depois |

## Monitoramento durante as execuções

Amostrados continuamente e publicados junto com os resultados:

- Frequência de CPU e temperatura dos núcleos
- Temperatura do dispositivo NVMe
- Uso de memória do host
- Taxa de leitura e escrita em disco

Ver ADR-012.

## Versões dos sistemas avaliados

Todas as imagens fixadas por digest. Ver ADR-011.

| Sistema | Versão | Digest |
|---|---|---|
| PostgreSQL | 18.6 (`18.6-1.pgdg13+2`), Debian 13 | base `postgres:18.6-trixie@sha256:edb0c08dc33befd0262ab3b5cf1c4f3f8cfd8b09e88c05b45adee29d6761ddb6` |
| pgvector | 0.8.7 (`0.8.7-1.pgdg13+1`, PGDG) | na imagem `tcc-postgres` |
| TimescaleDB | 2.31.0 Community (`2.31.0~debian13-1806`), licença `timescale` | na imagem `tcc-postgres` |
| OpenSearch | a registrar | a registrar |
| Qdrant | a registrar | a registrar |
| RabbitMQ | a registrar | a registrar |
| InfluxDB | a registrar | a registrar |

### Imagem do PostgreSQL usada nos três eixos

Construída a partir de `harness/orquestracao/postgres/Dockerfile` (ADR-022) e
verificada em 27/10/2026.

| Item | Valor |
|---|---|
| Tag local | `tcc-postgres:18.6-ts2.31.0-pgv0.8.7` |
| ID da imagem | `sha256:3c71fe3d3acb92bc6b4c2a1dfce722e9eff10c352252a6450051cd04880067ec` |
| Pacotes acrescentados à base | `ca-certificates` 20250419+deb13u1, `postgresql-18-pgvector`, `timescaledb-2-postgresql-18`, `timescaledb-2-loader-postgresql-18`, `timescaledb-tools` 0.19.0 |
| Extensões verificadas | `vector` 0.8.7, `timescaledb` 2.31.0, `pg_trgm` 1.6, `pgstattuple` 1.5, `pg_stat_statements` 1.12 |

Verificação: índice HNSW usado em busca por vizinhos; hypertable com 4 chunks
comprimidos de 23 MB para 3,9 MB (razão 6,1), o que confirma a edição Community;
colunas `total_autovacuum_time` (PostgreSQL 18), `pg_stat_wal` e
`pg_stat_checkpointer` disponíveis. A telemetria do TimescaleDB vem em `basic` e
é desligada na configuração do Eixo C (ADR-023).

O ID de uma imagem construída localmente muda a cada reconstrução, mesmo com as
mesmas versões, porque inclui datas de criação. A referência do que foi medido é
este ID, registrado também no manifesto de cada execução; a receita para
reconstruir é a base fixada por digest com as versões fixadas dos pacotes.
