# Harness Experimental

Infraestrutura comum aos três eixos. Construída antes das implementações para
evitar instrumentação divergente entre eles.

## Diretórios

- `orquestracao/` — composições base, definição de `cpuset` e limites de recurso
- `metricas/` — coletores de `pg_stat_*`, `pgstattuple`, `pg_stat_statements`,
  métricas dos serviços especializados e telemetria de host
- `analise/` — scripts de consolidação, tratamento estatístico e geração de
  figuras. Por ora, `custo_coleta.py` (ADR-021, item 6):
  `uv run python -m harness.analise.custo_coleta <id-janela>`, com saída em
  `resultados/processados/`

## Ambiente Python

O harness roda num ambiente Python isolado, com versões travadas (ADR-031). O
projeto fica na raiz do repositório: `pyproject.toml`, `uv.lock` e
`.python-version`.

Para preparar o ambiente numa máquina nova:

```bash
curl -LsSf https://astral.sh/uv/0.12.24/install.sh | env UV_NO_MODIFY_PATH=1 sh
uv sync --locked
```

O primeiro comando instala o `uv` 0.12.24 em `~/.local/bin`, sem alterar os
arquivos de configuração do shell. O segundo cria o ambiente em `.venv/` com
exatamente as versões do `uv.lock`, e falha se o arquivo estiver desatualizado.
Os scripts rodam com `uv run`, por exemplo:

```bash
uv run python harness/metricas/telemetria.py metricas-host.csv
```

Para acrescentar ou atualizar uma dependência, edite a versão exata no
`pyproject.toml`, rode `uv lock` e versione o `pyproject.toml` e o `uv.lock`
juntos. O `exclude-newer` do `pyproject.toml` impede o `uv` de escolher
pacotes publicados depois da data configurada, inclusive dependências
indiretas; para atualizar, a data é avançada de propósito (ADR-031).

As bibliotecas de geração de embeddings do Eixo A ficam no grupo `embeddings`,
instalado só quando necessário:

```bash
uv sync --locked --group embeddings
uv run --group embeddings python <script de geração>
```

Um `uv sync --locked` sem o grupo remove essas bibliotecas do `.venv/` e libera
o espaço.

## Formato de saída

Versão 1. Os três eixos gravam no mesmo formato, de modo que os scripts de
`analise/` sirvam a todos sem adaptação.

### Princípios

1. **Uma execução, um diretório.** Tudo o que uma execução produz fica no
   diretório dela.
2. **Dados brutos, nunca derivados.** Os arquivos guardam o que foi observado:
   cada operação e cada contador lido. Percentis, taxas e razões são calculados
   pelos scripts de análise, de modo que qualquer número do artigo possa ser
   recalculado a partir dos brutos.
3. **Um relógio só.** Todo carimbo de tempo usa o relógio de tempo real do host,
   em tempo Unix. Gerador, coletores e containers rodam na mesma máquina e
   compartilham esse relógio. Latências são medidas com relógio monotônico.
4. **Contadores como lidos.** Contadores acumulados (bytes gravados, commits,
   WAL) são gravados com o valor acumulado e sufixo `_total`; a análise calcula
   as diferenças. Valores instantâneos (temperatura, memória em uso) são
   gravados como lidos.
5. **Métricas em formato longo.** Uma leitura por linha, para que métricas novas
   não mudem o esquema.
6. **Aquecimento separado por tempo.** Os limites de aquecimento e de medição
   ficam no manifesto. Nenhum dado é apagado durante a coleta.

### Estrutura de diretórios

```
resultados/brutos/
├── janelas/
│   └── <id-janela>/
│       ├── abertura.json        controles, ocupação do disco e SMART na abertura
│       └── encerramento.json    o mesmo no encerramento, e a ordem das execuções
└── <eixo>/
    └── <id-execucao>/
        ├── manifesto.json       metadados, controles e limites de tempo
        ├── operacoes.csv        uma linha por operação do gerador de carga
        ├── metricas-*.csv       séries amostradas, um arquivo por coletor
        ├── resumo.json          valores únicos informados por ferramentas
        ├── instantaneos/        estados capturados no fim da execução
        └── ferramenta/          saídas originais das ferramentas, sem tratamento
```

| Arquivo | Escrito por |
|---|---|
| `manifesto.json` | Orquestrador: início ao subir a execução, complemento ao fim |
| `operacoes.csv` | Gerador de carga |
| `metricas-host.csv` | Coletor de telemetria de host e containers |
| `metricas-postgres.csv` | Coletor do PostgreSQL |
| `metricas-<sistema>.csv` | Coletor de cada sistema especializado e do TimescaleDB |
| `metricas-gerador.csv` | Adaptador do TSBS, no Eixo C |
| `resumo.json`, `instantaneos/`, `ferramenta/` | Orquestrador, ao fim |

### Identificadores

- `<id-janela>`: `AAAAMMDD-HHMM` do início da janela.
- `<id-execucao>`:
  `AAAAMMDD-HHMMSS_<eixo>_<arquitetura>_<cenario>_c<concorrencia>_r<repeticao>`,
  com `_semcoleta` no fim nas execuções sem coleta interna (ADR-066).
  Exemplo: `20261108-142530_b_outbox_curta_c32_r03`.

| Campo | Valores |
|---|---|
| `eixo` | `a`, `b`, `c`; e `t`, o eixo de teste do harness |
| `arquitetura` | Eixo A: `consolidada`, `poliglota`. Eixo B: `consolidada`, `ingenua`, `outbox`. Eixo C: `timescaledb`, `influxdb`. Eixo T: `pgbench` |

O eixo `t` existe só para validar o harness de ponta a ponta. Nenhum resultado
dele entra no artigo.
| `cenario` | `calibracao`, `curta`, `mista`, `longa`, `qualidade` |

Identificadores e nomes de métricas usam apenas letras minúsculas sem acento,
dígitos, `_` e `-`.

### Convenções dos CSV

UTF-8, vírgula como separador, ponto como separador decimal, cabeçalho na
primeira linha, sem separador de milhar. Valor ausente é campo vazio, booleano
é `0` ou `1`, e todo tempo é inteiro.

### manifesto.json

```json
{
  "versao_formato": 1,
  "id": "20261108-142530_b_outbox_curta_c32_r03",
  "janela": "20261108-1400",
  "eixo": "b",
  "arquitetura": "outbox",
  "cenario": "curta",
  "concorrencia": 32,
  "repeticao": 3,
  "ordem_na_janela": 5,
  "semente": 918273,
  "tempos_ms": {
    "repouso_inicio": 1762611930000,
    "aquecimento_inicio": 1762613130000,
    "medicao_inicio": 1762613190000,
    "medicao_fim": 1762613490000
  },
  "duracao_planejada_s": { "aquecimento": 60, "medicao": 300 },
  "estado": "ok",
  "motivo_falha": null,
  "repositorio": { "commit": "7de6871", "alteracoes_locais": false },
  "imagens": [
    { "servico": "postgres", "imagem": "...", "digest": "sha256:..." }
  ],
  "recursos": [
    { "servico": "postgres", "cpuset": "0-5,8-13", "memoria_bytes": 8589934592 }
  ],
  "gerador": {
    "ferramenta": "...", "versao": "...", "cpuset": "6-7,14-15",
    "parametros": {}
  },
  "controles": {
    "conferidos": [
      { "controle": "swap desligado", "ok": true, "bloqueante": true,
        "detalhe": "nenhuma área ativa" }
    ],
    "volumes_restaurados": true,
    "fstrim": "/: 12,3 GiB (13207024640 bytes) descartado",
    "repouso_s": 1200
  },
  "disco": { "ocupado_bytes": 0, "livre_bytes": 0 },
  "host": { "kernel": "7.0.0-38-generic", "docker": "29.9.2" },
  "arquivos": [
    { "nome": "operacoes.csv", "bytes": 0, "sha256": "..." }
  ]
}
```

- `estado` assume `ok`, `falha`, `oom` ou `abortada`. Execuções que não
  terminaram em `ok` são mantidas e reportadas (ADR-016).
- `controles.conferidos` é a lista de controles lidos do sistema no início de
  cada execução, a mesma que o `verificar` mostra. Se algum bloqueante estiver
  fora do protocolo de `docs/protocolo-coleta.md`, o orquestrador não inicia a
  execução.
- `repositorio.alteracoes_locais` indica arquivos versionados alterados sem
  commit; arquivos novos não versionados, como os próprios resultados, não
  contam.
- `arquivos` traz tamanho e SHA-256 de cada arquivo, preenchidos ao fim. Como os
  CSV não são versionados no Git, os hashes no manifesto permitem verificar o
  pacote de dados publicado à parte.

### operacoes.csv

| Coluna | Tipo | Conteúdo |
|---|---|---|
| `ts_us` | inteiro | Início da operação, em tempo Unix, em microssegundos |
| `operacao` | texto | Tipo, conforme o catálogo abaixo |
| `latencia_us` | inteiro | Duração, medida com relógio monotônico |
| `ok` | 0 ou 1 | 1 se terminou sem erro |
| `cliente` | inteiro | Cliente ou conexão do gerador que executou |
| `itens` | inteiro | Itens processados: 1 em operações unitárias, tamanho do lote em lotes |

| Eixo | `operacao` | O que mede |
|---|---|---|
| A | `busca_hibrida` | Consulta híbrida completa, da requisição à lista fundida |
| B | `enfileira` | Gravação do dado de negócio e enfileiramento, até a confirmação (o commit; na ingênua, também a confirmação do broker) |
| B | `consome` | Retirada e confirmação de uma tarefa: no PostgreSQL, o `DELETE` com o commit; no RabbitMQ, da entrega ao gerador até o envio da confirmação. Não é comparada entre arquiteturas (ADR-054) |
| B | `fim_a_fim` | Do enfileiramento ao fim do consumo. `ts_us` é o instante do enfileiramento e a latência vem do relógio monotônico do host, os dois na carga útil da mensagem (ADR-050) |
| T | `transacao_pgbench` | Uma transação do script padrão do `pgbench`, só na arquitetura de teste. Em transações que falham, o `pgbench` não informa a latência: `latencia_us` fica vazio e `ts_us` é o instante do fim |

O Eixo C não grava `operacoes.csv`, porque o TSBS não registra operações
individuais. Ver "Eixo C: adaptação do TSBS".

### metricas-*.csv

Cada coletor roda num processo próprio e grava o seu arquivo, sempre com as
mesmas colunas. A análise lê todos os `metricas-*.csv` do diretório da execução
como uma tabela só. Dois processos nunca escrevem no mesmo arquivo, e a falha de
um coletor não interrompe os outros.

| Coluna | Tipo | Conteúdo |
|---|---|---|
| `ts_ms` | inteiro | Instante da leitura, em tempo Unix, em milissegundos |
| `origem` | texto | `host`, `container`, `postgres`, `timescaledb`, `opensearch`, `qdrant`, `rabbitmq`, `influxdb` ou `gerador` |
| `metrica` | texto | Nome, conforme o catálogo; a unidade está no sufixo |
| `rotulo` | texto | Instância lida: `cpu3`, `nvme0n1`, nome do container, nome da tabela. Vazio quando não se aplica |
| `valor` | número | Valor lido |

Os coletores rodam no conjunto de CPUs do gerador, `6-7,14-15` (ADR-018). Os
intervalos de coleta seguem a ADR-021.

**Host e containers, a cada 1 s:**

| Origem | Métrica | Rótulo | Fonte |
|---|---|---|---|
| `host` | `cpu_freq_khz` | `cpuN` | `/sys/devices/system/cpu/cpuN/cpufreq/scaling_cur_freq` |
| `host` | `cpu_ocupado_ms_total`, `cpu_ocioso_ms_total` | `cpuN` | `/proc/stat` |
| `host` | `cpu_temp_c` | `tctl` | `k10temp`, `temp1_input` |
| `host` | `nvme_temp_c` | `nvmeN` | `/sys/class/nvme/nvmeN/hwmon*/temp1_input` |
| `host` | `mem_disponivel_bytes` | | `/proc/meminfo`, `MemAvailable` |
| `host` | `disco_escrita_bytes_total`, `disco_escritas_total`, `disco_escrita_ms_total` | `nvmeNn1` | `/proc/diskstats` |
| `host` | `disco_leitura_bytes_total`, `disco_leituras_total`, `disco_leitura_ms_total` | `nvmeNn1` | `/proc/diskstats` |
| `host` | `disco_ocupado_ms_total` | `nvmeNn1` | `/proc/diskstats`, tempo com I/O em andamento |
| `container` | `cont_cpu_us_total` | container | cgroup v2, `cpu.stat` (`usage_usec`) |
| `container` | `cont_mem_bytes`, `cont_mem_pico_bytes` | container | cgroup v2, `memory.current` e `memory.peak` |
| `container` | `cont_mem_anon_bytes`, `cont_mem_arquivo_bytes`, `cont_mem_shmem_bytes` | container | cgroup v2, `memory.stat` (`anon`, `file`, `shmem`) |
| `container` | `cont_escrita_bytes_total`, `cont_leitura_bytes_total` | container | cgroup v2, `io.stat`, linha do `major:minor` do P510 |
| `container` | `cont_oom_total` | container | cgroup v2, `memory.events` (`oom_kill`) |

O coletor acha o P510 pelo modelo a cada início, porque a numeração dos
controladores NVMe muda entre inicializações. O rótulo `nvmeN` e o
`major:minor` são os daquela inicialização: o P510 já apareceu como `nvme0` e
como `nvme1`. A análise identifica o disco pela métrica, não pelo rótulo.

O kernel cobra do container o cache de páginas dos arquivos que ele lê e grava,
então `cont_mem_bytes` e `cont_mem_pico_bytes` incluem esse cache e tendem ao
limite de memória em qualquer banco. A memória própria do processo é `anon` mais
`shmem` (onde fica, por exemplo, o `shared_buffers` do PostgreSQL); o cache de
arquivo é `arquivo` menos `shmem`, porque o `file` do kernel já inclui o
`shmem`. Verificado em 27/10/2026 com um container que gravou 200 MiB em
`/dev/shm`, 1 GiB em arquivo e alocou 300 MiB: os três valores apareceram
separados, e `arquivo` marcou 1224 MiB.

**PostgreSQL**, em todas as arquiteturas que o usam (inclusive as poliglotas do
Eixo B, que gravam nele o dado de negócio). Visões e colunas conferidas na
documentação do PostgreSQL 18; a versão usada precisa ser 18 ou posterior, por
causa das colunas de tempo de autovacuum:

| Métrica | Rótulo | Fonte | Intervalo |
|---|---|---|---|
| `pg_wal_bytes_total` | | `pg_stat_wal` (`wal_bytes`) | 10 s |
| `pg_commits_total`, `pg_rollbacks_total`, `pg_deadlocks_total` | banco | `pg_stat_database` | 10 s |
| `pg_checkpoints_total` | | `pg_stat_checkpointer` (`num_timed` + `num_requested`) | 10 s |
| `pg_tuplas_vivas`, `pg_tuplas_mortas` | tabela | `pg_stat_user_tables` | 10 s |
| `pg_autovacuum_total`, `pg_autoanalyze_total` | tabela | `pg_stat_user_tables` | 10 s |
| `pg_autovacuum_ms_total`, `pg_autoanalyze_ms_total` | tabela | `pg_stat_user_tables` (`total_autovacuum_time`, `total_autoanalyze_time`) | 10 s |
| `pg_esperas_lock` | | `pg_locks`, pedidos não concedidos | 10 s |
| `pg_tabela_bytes`, `pg_indices_bytes` | tabela | `pg_table_size`, `pg_indexes_size` | 60 s |
| `pg_tuplas_mortas_pct`, `pg_espaco_livre_pct` | tabela | `pgstattuple_approx` | 5 min, só no cenário `longa` |

O rótulo `tabela` é `esquema.tabela`, para distinguir, por exemplo, os chunks do
TimescaleDB, que ficam no esquema `_timescaledb_internal`. O rótulo `banco` é o
banco a que o coletor se conecta. O coletor usa uma conexão em modo
*autocommit*, de modo que cada consulta termina na hora e nunca deixa uma
transação aberta, o que impediria o autovacuum de remover tuplas mortas e
contaminaria a própria medição de inflação.

**Sistemas especializados e TimescaleDB**, estatísticas a cada 10 s e tamanhos
a cada 60 s, como no PostgreSQL. O catálogo de cada um é definido na etapa do
seu eixo, com prefixo por sistema: `os_` (OpenSearch, `_nodes/stats`),
`qd_` (Qdrant, `/metrics`), `rmq_` (RabbitMQ, API de gerenciamento; inclui a
profundidade de cada fila), `influx_` (InfluxDB, `/metrics`) e `tsdb_`
(TimescaleDB, tamanho de hypertables e compressão por chunk). Os coletores por
HTTP compartilham o laço de `harness/metricas/coleta.py`.

OpenSearch (`harness/metricas/opensearch.py`, ADR-049):

| Métrica | Rótulo | Fonte |
|---|---|---|
| `os_buscas_total`, `os_busca_ms_total` | | `_nodes/stats`, `indices.search` |
| `os_indexacoes_total`, `os_indexacao_ms_total` | | `_nodes/stats`, `indices.indexing` |
| `os_cache_consultas_bytes` | | `_nodes/stats`, `indices.query_cache` |
| `os_segmentos` | | `_nodes/stats`, `indices.segments.count` |
| `os_heap_usado_bytes` | | `_nodes/stats`, `jvm.mem` |
| `os_gc_total`, `os_gc_ms_total` | coletor da JVM (`young`, `old`, `G1 Concurrent GC`) | `_nodes/stats`, `jvm.gc.collectors` |
| `os_indice_bytes`, `os_documentos` (60 s) | índice | `_cat/indices`, tamanho primário e documentos |

Qdrant (`harness/metricas/qdrant.py`, ADR-049):

| Métrica | Rótulo | Fonte |
|---|---|---|
| `qd_requisicoes_total`, `qd_requisicao_s_total` | protocolo, método, rota e status (ex.: `grpc /qdrant.Points/Query 0`) | `/metrics`, `*_responses_duration_seconds_count` e `_sum` |
| `qd_memoria_alocada_bytes`, `qd_memoria_residente_bytes` | | `/metrics`, `memory_allocated_bytes` e `memory_resident_bytes` |
| `qd_pontos`, `qd_vetores_indexados`, `qd_segmentos` (60 s) | coleção | API de coleções, `points_count`, `indexed_vectors_count`, `segments_count` |

RabbitMQ (`harness/metricas/rabbitmq.py`, ADR-057), pela API de gerenciamento
com o usuário padrão da imagem (ADR-052):

| Métrica | Rótulo | Fonte |
|---|---|---|
| `rmq_mensagens_prontas`, `rmq_mensagens_pendentes` | fila | `/api/queues`, `messages_ready` e `messages_unacknowledged` |
| `rmq_mensagens_bytes`, `rmq_fila_memoria_bytes` | fila | `/api/queues`, `message_bytes` e `memory` |
| `rmq_publicadas_total`, `rmq_entregues_total`, `rmq_confirmadas_total`, `rmq_reentregues_total` | fila | `/api/queues`, `message_stats` (`publish`, `deliver_get`, `ack`, `redeliver`) |
| `rmq_memoria_bytes`, `rmq_limite_memoria_bytes` | | `/api/nodes`, `mem_used` e `mem_limit` |
| `rmq_alarme_memoria`, `rmq_alarme_disco` | | `/api/nodes`, `mem_alarm` e `disk_free_alarm` (0 ou 1) |
| `rmq_dados_bytes` (60 s) | | `du -sb /var/lib/rabbitmq` dentro do container |

O RabbitMQ atualiza as estatísticas da API a cada 5 s; os contadores de uma
fila aparecem como 0 até a primeira operação de cada tipo.

TimescaleDB (`harness/metricas/postgres.py --timescaledb`, ADR-064), além do
catálogo do PostgreSQL, a cada 60 s, uma linha por hypertable:

| Métrica | Rótulo | Fonte |
|---|---|---|
| `tsdb_tabela_bytes`, `tsdb_indices_bytes`, `tsdb_toast_bytes`, `tsdb_total_bytes` | hypertable | `hypertable_detailed_size` |
| `tsdb_chunks` | hypertable | `timescaledb_information.hypertables`, `num_chunks` |
| `tsdb_chunks_comprimidos`, `tsdb_antes_compressao_bytes`, `tsdb_depois_compressao_bytes` | hypertable | `hypertable_compression_stats`; ausentes antes da primeira compressão |

InfluxDB 1.x (`harness/metricas/influxdb.py`, ADR-064), pelo `/debug/vars`,
somando as instâncias de cada grupo (uma por shard); nomes conferidos na 1.13.1:

| Métrica | Fonte |
|---|---|
| `influx_pontos_escritos_total`, `influx_erros_escrita_total`, `influx_pontos_descartados_total` | `write` (`pointReq`, `writeError`, `writeDrop`) |
| `influx_pontos_gravados_total` | `localStore` (`pointsWritten`) |
| `influx_requisicoes_escrita_total`, `influx_consultas_total`, `influx_consulta_ns_total` | `httpd` (`writeReq`, `queryReq`, `queryReqDurationNs`) |
| `influx_consultas_executadas_total`, `influx_consulta_execucao_ns_total` | `queryExecutor` (`queriesExecuted`, `queryDurationNs`) |
| `influx_cache_bytes`, `influx_cache_compactacao_ms_total` | `tsm1_cache` (`memBytes`, `WALCompactionTimeMs`) |
| `influx_compactacoes_cache_total`, `influx_compactacoes_nivel1_total` a `nivel3`, `influx_compactacoes_otimizacao_total`, `influx_compactacoes_completas_total` | `tsm1_engine` |
| `influx_compactacoes_ativas` | `tsm1_engine`, soma dos campos `*Active` |
| `influx_tsm_bytes` | `tsm1_filestore` (`diskBytes`) |
| `influx_heap_bytes`, `influx_memoria_go_bytes` | `runtime` (`HeapInUse`, `Sys`) |
| `influx_series` (rótulo: banco) | `database` (`numSeries`) |
| `influx_dados_bytes` (60 s) | `du -sb /var/lib/influxdb` dentro do container |

**Gerador:** `tsbs_metricas_total` e `tsbs_linhas_total`, no Eixo C. Ver
"Eixo C: adaptação do TSBS".

### resumo.json

Valores únicos da execução que não podem ser recalculados das séries, como o
tempo de construção de um índice ou o resumo final de uma ferramenta:

```json
{
  "versao_formato": 1,
  "valores": [
    { "metrica": "indice_construcao_s", "rotulo": "hnsw", "valor": 412.7,
      "fonte": "log de criação do índice" }
  ]
}
```

No cenário `qualidade` do Eixo A, a ferramenta grava `ferramenta/ranking.trec`
no formato de *run* do TREC (`qid Q0 docid posicao escore tag`). recall@10 e
nDCG@10 são calculados pela análise contra os julgamentos de relevância, e não
gravados como número pronto.

### Eixo C: adaptação do TSBS

O TSBS não registra operações individuais, e o Eixo C não grava
`operacoes.csv`. O adaptador `eixo-c-temporal/cargas/gerador.py` cumpre o
contrato do gerador com as regras abaixo (ADR-061 e ADR-065), baseadas no
código-fonte da ferramenta (`load/loader.go`, `pkg/query/benchmarker.go` e
`pkg/query/stat_processor.go`) e conferidas com o TSBS compilado:

- **Execução.** Cada `executar` carrega o arquivo inteiro com N trabalhadores
  (`--workers`, lote de 10.000 linhas), depois comprime todos os chunks e roda
  `ANALYZE` no TimescaleDB, ou espera as compactações do InfluxDB pararem, e
  por fim roda as consultas. No plano, `aquecimento_s` é o trecho inicial da
  carga descartado da vazão, e `aquecimento_s` + `medicao_s` é o prazo máximo da
  carga; se ela não terminar, a execução falha.
- **Ingestão.** `tsbs_load` roda com `--reporting-period=1s` e
  `--results-file`. A cada segundo, imprime uma linha com o tempo Unix em
  segundos, a taxa do período e o total acumulado de métricas e de linhas. O
  adaptador grava os totais em `metricas-gerador.csv` como
  `tsbs_metricas_total` e `tsbs_linhas_total` (origem `gerador`), com `ts_ms`
  igual ao tempo da linha multiplicado por 1000. Os totais vêm em notação
  exponencial com 7 algarismos significativos, precisão suficiente para
  calcular taxas. O `gerador.json` marca o início da carga, o fim do
  aquecimento e o fim da carga. A carga é conferida pelo total do resumo do
  TSBS, que precisa ser igual ao número de métricas geradas.
- **Consultas.** Cada tipo roda em duas invocações de `tsbs_run_queries`, com
  1 trabalhador: o aquecimento, com 5 consultas de outra semente, e a medição,
  com as 20 consultas medidas. O adaptador copia para `resumo.json`, em
  microssegundos, a métrica `consulta_latencia_us` com os rótulos
  `<tipo>:min`, `<tipo>:media`, `<tipo>:desvio`, `<tipo>:p50` e `<tipo>:max`,
  lidos da saída padrão, e `<tipo>:p95`, `<tipo>:p99` e `<tipo>:p999`, lidos do
  JSON (que vem em milissegundos). O `q0` do JSON sai sempre 0, e por isso o
  mínimo vem da saída padrão. É uma exceção declarada ao princípio 2: no Eixo
  C, os percentis das consultas são calculados pela ferramenta, não pela
  análise.
- **Valores únicos.** O `resumo.json` traz também `carga_s`, `carga_metricas`
  e `carga_linhas`; no TimescaleDB, `compressao_s`, `analyze_s`,
  `chunks_comprimidos` e o espaço antes e depois da compressão; no InfluxDB,
  `espera_compactacao_s` e `tsm_bytes`.
- **Arquivo HDR.** A opção `--hdr-latencies` não é usada. O código não esvazia
  o buffer de escrita antes de terminar, e o arquivo pode sair incompleto.
- **Deriva.** Nos cenários `mista` e `longa`, as consultas rodam em lotes
  periódicos, cada um com o seu JSON. A série de percentis por lote, com o
  instante de cada lote, mostra a deriva. A definir na Fase 3.

As saídas originais ficam em `ferramenta/`: `tsbs-carga.txt` e
`tsbs-carga.json` da carga, e `consultas/<tipo>.txt` e `.json` de cada tipo,
com os de aquecimento prefixados por `aquecimento-`.

### instantaneos/ e ferramenta/

- `instantaneos/`: estados capturados ao fim, antes de a arquitetura ser
  desligada: `docker-inspect.json` (todos os containers da arquitetura) e
  `pg_stat_statements-<serviço>.csv` (um por serviço PostgreSQL). O
  `pg_stat_statements` é zerado imediatamente antes de o gerador começar, e
  por isso cobre o aquecimento e a medição (ADR-034).
- `ferramenta/`: saídas sem tratamento. `orquestrador.log` (cada passo da
  execução, com horário), `compose.log` (todos os comandos do Compose e a
  saída do gerador), `coletor-<origem>.log` (saída de cada coletor),
  `<serviço>.log` (`docker logs` de cada container) e as saídas próprias do
  gerador. A análise só lê esses arquivos quando o formato estiver documentado
  aqui.

### Registros de janela

Em `resultados/brutos/janelas/<id-janela>/`:

- `plano.toml`: cópia do plano executado.
- `abertura.json`: horário, controles lidos do sistema, ocupação do disco,
  campos do SMART do P510 (temperatura, dados gravados, desgaste), semente e a
  ordem sorteada das execuções (ADR-032).
- `estado.json`: resumo atualizado depois de cada execução, com o estado de
  cada posição da ordem sorteada e as tentativas feitas.
- `retomada-<AAAAMMDD-HHMMSS>.json`: o mesmo da abertura, a cada retomada.
- `encerramento.json`: o mesmo da abertura, ao fim de cada sessão, mais o
  motivo do encerramento, o histórico de sessões e a lista das execuções na
  ordem em que rodaram.
- `smart-*.txt`: saída completa do `nvme smart-log` em cada um desses momentos.

A fonte de verdade sobre o que já rodou são os manifestos das execuções, que
trazem `janela` e `ordem_na_janela`. Na retomada, uma posição com manifesto
`ok`, `falha` ou `oom` não roda de novo; uma com `abortada`, ou sem manifesto,
roda. Um manifesto que ficou `em_andamento`, porque a máquina ou o orquestrador
caiu no meio, é fechado como `abortada` antes de a retomada começar (ADR-033).

### Mapa das variáveis dependentes

Cada variável dependente da proposta (seção 6.2) e de onde ela sai:

| Variável | Origem nos brutos |
|---|---|
| Latência p50, p95 e p99 | `operacoes.csv`, operações com `ts_us` dentro da medição; consultas do Eixo C em `resumo.json`, com percentis do TSBS |
| Vazão | `operacoes.csv`, operações `ok` divididas pela duração da medição; no Eixo C, diferença de `tsbs_metricas_total` |
| Latência fim a fim | `operacoes.csv`, `operacao = fim_a_fim` |
| Vazão sustentável da fila (Eixo B) | `operacoes.csv`: maior vazão de `consome` em que a profundidade, `enfileira` menos `consome` acumulados, não cresce na medição; conferida por `pg_tuplas_vivas` de `tarefas` e `saida` e por `rmq_mensagens_prontas` e `rmq_mensagens_pendentes` (ADR-054) |
| Espaço em disco da fila quorum | `rmq_dados_bytes`, comparado com `pg_tabela_bytes` e `pg_indices_bytes` |
| Taxa de ingestão sustentável | `tsbs_metricas_total` por intervalo, nas execuções de calibração |
| recall@10 e nDCG@10 | `ferramenta/ranking.trec` e julgamentos de relevância |
| WAL por operação lógica | Diferença de `pg_wal_bytes_total` dividida pelas operações `ok` |
| Tuplas mortas e inflação de tabela e índice | `pg_tuplas_mortas`, `pg_tabela_bytes`, `pg_indices_bytes`, `pg_tuplas_mortas_pct` |
| Frequência e tempo de autovacuum | Diferenças de `pg_autovacuum_total` e de `pg_autovacuum_ms_total` |
| Contenção de lock | `pg_esperas_lock`, `pg_deadlocks_total` |
| Razão de compressão e espaço por ponto | Métricas `tsdb_` e `influx_` de tamanho, divididas pelos pontos ingeridos |
| Bytes gravados por métrica ingerida (ADR-004) | Diferença de `cont_escrita_bytes_total` dos containers do sistema sob teste, dividida pela de `tsbs_metricas_total` |
| Deriva de latência | `operacoes.csv` ao longo do tempo, cenário `longa`; no Eixo C, percentis por lote de consultas |
| Pico de memória e uso de CPU | Máximo de `cont_mem_anon_bytes` + `cont_mem_shmem_bytes` (memória própria), com o cache de arquivo reportado à parte; `cont_cpu_us_total` |
| Tamanho e tempo de construção de índices | `resumo.json` e métricas de tamanho |
| Complexidade operacional e custo | Fora deste formato: medidos sobre os arquivos de composição, não por execução |

### Versionamento

O `.gitignore` exclui `*.csv`, `*.jsonl` e `*.parquet` de `resultados/brutos/`.
Entram no Git os manifestos, os resumos e os registros de janela, todos em
JSON.

As séries completas são publicadas como anexos de uma release do GitHub, fora
do histórico do repositório. São pacotes `tar` comprimidos com `zstd`, um por
eixo e cenário, gerados fora do diretório do repositório, com `zstd -9`. O
GitHub aceita anexos de até 2 GiB cada e até 1000 por release, sem limite de
tamanho total nem de banda. Os SHA-256 dos manifestos, que estão no Git,
permitem conferir cada arquivo depois de descompactado.

Um pacote que passe de 2 GiB é dividido por arquitetura e, se ainda passar,
por concorrência. A execução de longa duração do Eixo B pode passar de 2 GiB
sozinha; a forma de dividi-la fica para a Etapa 3.4.

Tamanhos medidos no piloto (09/11/2026), por execução com 8 clientes e 300 s
de medição:

| Eixo | Descompactado | Compactado |
|---|---|---|
| A | 2 a 20 MiB | não medido |
| B | 139 a 425 MiB, quase tudo `operacoes.csv` | fila consolidada (417 MiB): 79 MiB com `zstd -3`; 68 MiB com `zstd -9`, que leva 1 s |
| C | 3 a 6 MiB | não medido |

O tamanho cresce com a vazão, então o Eixo B com 32 clientes e o teste de
longa duração geram os maiores pacotes.

### Pendências

Nenhuma. O custo da coleta foi medido em 09/11/2026 (ADR-021, item 6) e ficou
abaixo da variação entre repetições; a análise está em
`analise/custo_coleta.py`.

As saídas do TSBS descritas em "Eixo C: adaptação do TSBS" foram conferidas em
08/11/2026, com o TSBS compilado (ADR-059): os percentis do JSON vêm em
milissegundos.

## Protocolo de execução

1. Restaurar estado inicial dos volumes (exceto no teste de longa duração)
2. `sync` e `fstrim -v /`, seguidos de 20 minutos de repouso com os containers
   parados
3. Subir a arquitetura sob teste com limites e `cpuset` fixados
4. Aquecer, descartando o período inicial
5. Executar a carga, amostrando métricas continuamente
6. Coletar e encerrar
7. Repetir com ordem aleatorizada entre arquiteturas

Toda a sequência roda dentro de `powerprofilesctl launch -p performance`. A
abertura e o encerramento de cada janela de coleta, e os detalhes de cada
passo, estão em `docs/protocolo-coleta.md`.

Ver ADR-002, ADR-004, ADR-012, ADR-013, ADR-015, ADR-017 e ADR-018.

## Orquestrador

`harness/orquestracao/executar.py` encadeia o protocolo. Roda no host, preso
aos núcleos do gerador (`6-7,14-15`), dentro de `systemd-inhibit` e de
`powerprofilesctl launch -p performance` (comando completo em
`docs/protocolo-coleta.md`, seção 1.5). Tem estes subcomandos:

| Subcomando | O que faz |
|---|---|
| `verificar` | Confere os controles do protocolo e lista o que está fora, sem rodar nada |
| `planejar` | Lê um plano e mostra a ordem sorteada das execuções, bloco a bloco, com a duração mínima da janela |
| `preparar` | Carrega os dados de uma arquitetura uma vez e grava a cópia inicial (ADR-029) |
| `executar` | Roda uma janela inteira a partir de um plano; `--retomar <id-janela>` continua uma janela interrompida (ADR-033). Código de saída 0 quando a janela termina, 1 quando o ambiente fica inválido ou a configuração está errada, 130 quando o operador interrompe |

### Plano da janela

Arquivo TOML versionado em `harness/planos/`. Cada `[[configuracao]]` se
expande em um nível de concorrência por valor de `concorrencias`, repetido
`repeticoes` vezes. As execuções são sorteadas em blocos com a `semente`
(ADR-032): o bloco *r* contém a repetição *r* de cada configuração. Chaves
desconhecidas, valores fora do domínio, configurações repetidas e a mistura do
cenário `longa` com outros cenários são recusados com a explicação do problema.

A chave opcional `coleta_interna = false` roda a configuração sem os coletores
internos e com o `pg_stat_statements` sem rastrear, para medir o custo da
coleta (ADR-021, item 6, e ADR-066). A mesma configuração pode aparecer com e
sem coleta no mesmo plano; as duas recebem a mesma semente em cada repetição, e
o manifesto registra `coleta_interna`.

```toml
nome = "piloto"
semente = 20261108
espaco_livre_min_gib = 100

[[configuracao]]
eixo = "b"
arquitetura = "outbox"
cenario = "curta"
concorrencias = [8, 32]
repeticoes = 3
aquecimento_s = 60
medicao_s = 300
```

### Descritor de cada arquitetura

Arquivo `arquitetura.toml` em `<pasta do eixo>/<arquitetura>/`, ao lado da
composição. A pasta de cada eixo é fixa: `a` em `eixo-a-busca`, `b` em
`eixo-b-fila`, `c` em `eixo-c-temporal` e `t` em `harness/teste`. Assim o
descritor da arquitetura `outbox` do Eixo B é sempre
`eixo-b-fila/outbox/arquitetura.toml`. Tudo o que é específico de uma
arquitetura fica ali, e o orquestrador permanece genérico.

```toml
eixo = "b"
arquitetura = "outbox"
compose = "compose.yaml"                  # relativo a este arquivo
servicos_sob_teste = ["postgres", "rabbitmq", "relay"]
servico_gerador = "gerador"

[dados]                                    # variável de ambiente -> subdiretório
TCC_DADOS_POSTGRES = "postgres"
TCC_DADOS_RABBITMQ = "rabbitmq"

[[coletor]]
tipo = "postgres"
servico = "postgres"
banco = "fila"
```

Cada `[[coletor]]` tem um `tipo` (`postgres`, `timescaledb`, `opensearch`,
`qdrant`, `rabbitmq` ou `influxdb`) e o `servico` onde o sistema roda, que
precisa estar entre os serviços sob teste. O `banco` vale só para os tipos
`postgres` e `timescaledb`; o `timescaledb` é o coletor do PostgreSQL com a
opção `--timescaledb`, que acrescenta as métricas das hypertables. Os coletores
HTTP leem o sistema pela porta padrão: 9200 no OpenSearch, 6333 no Qdrant,
15672 na API de gerenciamento do RabbitMQ (com o usuário padrão da imagem) e
8086 no InfluxDB, cuja prontidão é conferida em `/ping?verbose=true`. Os
coletores do RabbitMQ e do InfluxDB recebem também o nome do container, para
medir o diretório de dados.

Os diretórios de dados ficam em `~/tcc-dados/<eixo>/<arquitetura>/<subdiretório>/`,
com `inicial/` e `atual/` (ADR-029). A variável de ambiente aponta para `atual/`
e é definida em todo comando do Compose, inclusive no `down`.

Os volumes ficam na composição de cada arquitetura, nunca em
`harness/orquestracao/modelos.yaml`. Ao herdar de um serviço com `extends`, o
Compose resolve o arquivo de modelos inteiro, e uma variável obrigatória ali
passaria a ser exigida de toda arquitetura, inclusive das que não usam aquele
serviço.

O orquestrador sobe só os `servicos_sob_teste`, com
`docker compose up -d <serviços>`. O gerador roda à parte, com
`docker compose run --rm --no-deps --user <uid>:<gid> <servico_gerador> ...`,
para que os arquivos de resultado pertençam ao usuário e não ao root. O serviço
do gerador monta em `/saida` o diretório da execução, indicado pela variável
`TCC_SAIDA`.

### Contrato do gerador de carga

Todo gerador, de qualquer eixo, roda no serviço `servico_gerador` e aceita dois
subcomandos:

- `preparar`: carrega os dados no sistema sob teste. Código de saída 0 em caso
  de sucesso.
- `executar --saida DIR --concorrencia N --aquecimento-s A --medicao-s M --semente S`:
  uma única invocação contínua, com aquecimento seguido de medição. Grava em
  `DIR` o `operacoes.csv` e um `gerador.json` com os seus próprios instantes
  (`aquecimento_inicio_ms`, `medicao_inicio_ms`, `medicao_fim_ms`), além da
  ferramenta, da versão e dos parâmetros. Pode gravar também
  `metricas-gerador.csv` e arquivos em `DIR/ferramenta/`. Código de saída 0 em
  caso de sucesso.

O adaptador do TSBS, no Eixo C, cumpre o mesmo contrato.

### Controles conferidos

Conferidos na abertura da janela e antes de cada execução. O orquestrador
confere, não aplica: a abertura e o encerramento são os comandos de
`docs/protocolo-coleta.md`.

| Tipo | Controle |
|---|---|
| Bloqueante | Swap desligado (ADR-016) |
| Bloqueante | `fstrim.timer` e timers de manutenção parados (ADR-017 e ADR-019) |
| Bloqueante | APST desligado: `pm_qos_latency_tolerance_us` igual a 0 (ADR-020) |
| Bloqueante | Governador e preferência de energia em `performance` nos 16 processadores, boost ligado (ADR-015) |
| Bloqueante | Suspensão e desligamento bloqueados pela trava `tcc-coleta` do `systemd-inhibit` (`docs/protocolo-coleta.md`, seção 1.5) |
| Bloqueante | P510 encontrado entre os controladores NVMe, pelo modelo |
| Bloqueante | Nenhum container rodando além dos da execução |
| Bloqueante | Nenhum coletor órfão: nenhum processo com `harness/metricas/` na linha de comando, sobra de um orquestrador que caiu. Um editor aberto com um arquivo dessa pasta pela linha de comando também conta |
| Bloqueante | Nenhum navegador aberto |
| Bloqueante | Espaço livre de pelo menos `espaco_livre_min_gib` |
| Bloqueante | `fstrim` e `nvme smart-log` executáveis sem senha (regra do `sudoers` abaixo) |
| Aviso | Outros programas pesados abertos, como o VS Code |

Um controle bloqueante que falha antes de uma execução encerra a janela
(ADR-033). Os avisos vão para o manifesto.

### Comandos com `sudo`

O `fstrim` antes de cada execução e a leitura do SMART exigem root, e uma
janela de horas não pode parar pedindo senha. Uma regra em
`/etc/sudoers.d/tcc-coleta` libera sem senha só estes comandos, com estes
argumentos exatos:

```
leojaime ALL=(root) NOPASSWD: /usr/sbin/fstrim -v /, /usr/sbin/nvme smart-log /dev/nvme0, /usr/sbin/nvme smart-log /dev/nvme1
```

O `smart-log` está liberado nos dois controladores NVMe da máquina porque a
numeração muda entre inicializações. O orquestrador acha o P510 pelo modelo e
só lê o SMART dele; o `smart-log` apenas lê.

O orquestrador nunca roda como root.

### Sequência de cada execução

O repouso do passo 3 só pode ser encurtado, com `--repouso-s`, quando todas as
execuções do plano são do eixo `t`. Com qualquer execução dos eixos A, B ou C,
o orquestrador recusa repouso abaixo de 1200 s (ADR-004). O repouso usado vai
para o manifesto.

Os coletores rodam como processos filhos do orquestrador, com o mesmo Python do
ambiente do `uv` (ADR-031), presos aos núcleos do gerador e num grupo de
processos próprio, para que um Ctrl+C no terminal não os derrube antes da hora.
A saída de cada um vai para `ferramenta/`, e eles são encerrados com `SIGTERM`
antes de a arquitetura ser desligada.

Cada execução recebe uma semente derivada da semente do plano e da configuração
sem a arquitetura (eixo, cenário, concorrência e repetição): reproduzível,
diferente entre repetições e igual entre as arquiteturas de um eixo, para que o
gerador mande a mesma sequência de operações a todas elas (ADR-032).

1. Conferir os controles.
2. Restaurar os diretórios de dados a partir de `inicial/`, com um container
   auxiliar (ADR-029).
3. `sync` e `sudo fstrim -v /`; repouso de 20 minutos (ADR-004 e ADR-017).
4. Iniciar a telemetria de host e containers.
5. Subir a arquitetura e iniciar os coletores internos. Sem coleta interna,
   aplicar `pg_stat_statements.track = none` em cada PostgreSQL, com
   `ALTER SYSTEM` e `pg_reload_conf()` (ADR-066).
6. Zerar o `pg_stat_statements` (ADR-034), se houver coleta interna, e rodar o
   gerador.
7. Parar os coletores; capturar `instantaneos/` (`pg_stat_statements`,
   `docker inspect`) e os logs dos containers em `ferramenta/`.
8. Desligar a arquitetura.
9. Definir o estado (`ok`, `falha`, `oom`, `abortada`), calcular o SHA-256 de
   cada arquivo e fechar o `manifesto.json`; atualizar o `estado.json` da
   janela.
