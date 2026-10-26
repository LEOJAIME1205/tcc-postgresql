# Harness Experimental

Infraestrutura comum aos três eixos. Construída antes das implementações para
evitar instrumentação divergente entre eles.

## Diretórios

- `orquestracao/` — composições base, definição de `cpuset` e limites de recurso
- `metricas/` — coletores de `pg_stat_*`, `pgstattuple`, `pg_stat_statements`,
  métricas dos serviços especializados e telemetria de host
- `analise/` — scripts de consolidação, tratamento estatístico e geração de figuras

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
        ├── metricas.csv         séries amostradas de host, containers e sistemas
        ├── resumo.json          valores únicos informados por ferramentas
        ├── instantaneos/        estados capturados no fim da execução
        └── ferramenta/          saídas originais das ferramentas, sem tratamento
```

| Arquivo | Escrito por |
|---|---|
| `manifesto.json` | Orquestrador: início ao subir a execução, complemento ao fim |
| `operacoes.csv` | Gerador de carga |
| `metricas.csv` | Coletores do harness e, no Eixo C, o adaptador do TSBS |
| `resumo.json`, `instantaneos/`, `ferramenta/` | Orquestrador, ao fim |

### Identificadores

- `<id-janela>`: `AAAAMMDD-HHMM` do início da janela.
- `<id-execucao>`:
  `AAAAMMDD-HHMMSS_<eixo>_<arquitetura>_<cenario>_c<concorrencia>_r<repeticao>`.
  Exemplo: `20261108-142530_b_outbox_curta_c32_r03`.

| Campo | Valores |
|---|---|
| `eixo` | `a`, `b`, `c` |
| `arquitetura` | Eixo A: `consolidada`, `poliglota`. Eixo B: `consolidada`, `ingenua`, `outbox`. Eixo C: `timescaledb`, `influxdb` |
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
    "perfil_energia": "performance", "governador": "performance",
    "epp": "performance", "boost": true, "swap_ativo": false, "apst": false,
    "fstrim_timer": "inactive", "fstrim_saida": "/: 12,3 GiB descartado",
    "repouso_s": 1200, "volumes_restaurados": true
  },
  "disco": { "ocupado_bytes": 0, "livre_bytes": 0 },
  "arquivos": [
    { "nome": "operacoes.csv", "bytes": 0, "sha256": "..." }
  ]
}
```

- `estado` assume `ok`, `falha`, `oom` ou `abortada`. Execuções que não
  terminaram em `ok` são mantidas e reportadas (ADR-016).
- `controles` são lidos do sistema no início de cada execução, não copiados de
  configuração. Se algum estiver fora do protocolo de `docs/protocolo-coleta.md`,
  o orquestrador não inicia a execução.
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
| B | `enfileira` | Gravação do dado de negócio e enfileiramento, até a confirmação |
| B | `consome` | Retirada, processamento e confirmação de uma tarefa |
| B | `fim_a_fim` | Do enfileiramento ao fim do consumo. `ts_us` é o instante do enfileiramento, que viaja na carga útil da mensagem |

O Eixo C não grava `operacoes.csv`, porque o TSBS não registra operações
individuais. Ver "Eixo C: adaptação do TSBS".

### metricas.csv

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
| `host` | `nvme_temp_c` | `nvme0` | `/sys/class/nvme/nvme0/hwmon*/temp1_input` |
| `host` | `mem_disponivel_bytes` | | `/proc/meminfo`, `MemAvailable` |
| `host` | `disco_escrita_bytes_total`, `disco_escritas_total`, `disco_escrita_ms_total` | `nvme0n1` | `/proc/diskstats` |
| `host` | `disco_leitura_bytes_total`, `disco_leituras_total`, `disco_leitura_ms_total` | `nvme0n1` | `/proc/diskstats` |
| `host` | `disco_ocupado_ms_total` | `nvme0n1` | `/proc/diskstats`, tempo com I/O em andamento |
| `container` | `cont_cpu_us_total` | container | cgroup v2, `cpu.stat` (`usage_usec`) |
| `container` | `cont_mem_bytes`, `cont_mem_pico_bytes` | container | cgroup v2, `memory.current` e `memory.peak` |
| `container` | `cont_escrita_bytes_total`, `cont_leitura_bytes_total` | container | cgroup v2, `io.stat`, linha `259:0` |
| `container` | `cont_oom_total` | container | cgroup v2, `memory.events` (`oom_kill`) |

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

**Sistemas especializados e TimescaleDB**, estatísticas a cada 10 s e tamanhos
a cada 60 s, como no PostgreSQL. O catálogo de cada um é definido na etapa do
seu eixo, com prefixo por sistema: `os_` (OpenSearch, `_nodes/stats`),
`qd_` (Qdrant, `/metrics`), `rmq_` (RabbitMQ, API de gerenciamento; inclui a
profundidade de cada fila), `influx_` (InfluxDB, `/metrics`) e `tsdb_`
(TimescaleDB, tamanho de hypertables e compressão por chunk).

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

O TSBS não registra operações individuais. O Eixo C segue as regras abaixo,
baseadas no código-fonte da ferramenta (`load/loader.go`,
`pkg/query/benchmarker.go` e `pkg/query/stat_processor.go`):

- **Ingestão.** `tsbs_load` roda com `--reporting-period=1s` e
  `--results-file`. A cada segundo, ele imprime na saída padrão uma linha com o
  tempo Unix em segundos, a taxa do período e o total acumulado de métricas e
  de linhas. O adaptador grava os totais em `metricas.csv` como
  `tsbs_metricas_total` e `tsbs_linhas_total` (origem `gerador`), com `ts_ms`
  igual ao tempo da linha multiplicado por 1000. Os totais vêm em notação
  exponencial com 7 algarismos significativos, precisão suficiente para
  calcular taxas.
- **Consultas.** Cada tipo de consulta padrão roda numa invocação própria de
  `tsbs_run_queries`, com `--results-file`. O JSON traz, por tipo de consulta,
  mínimo, p50, p95, p99, p99,9, máximo e taxa, calculados pelo próprio TSBS a
  partir de um histograma HDR. O adaptador copia esses valores para
  `resumo.json`, em microssegundos (`consulta_latencia_us`, com rótulo
  `<tipo>:p50`, `<tipo>:p95` e assim por diante). É uma exceção declarada ao
  princípio 2: no Eixo C, os percentis das consultas são calculados pela
  ferramenta, não pela análise.
- **Arquivo HDR.** A opção `--hdr-latencies` não é usada. O código não esvazia
  o buffer de escrita antes de terminar, e o arquivo pode sair incompleto.
- **Aquecimento.** As consultas de aquecimento rodam numa invocação separada,
  antes da medição. O JSON dela fica em `ferramenta/` e não entra no resumo. Os
  limites de tempo ficam no manifesto, como nos outros eixos.
- **Deriva.** Nos cenários `mista` e `longa`, as consultas rodam em lotes
  periódicos, cada um com o seu JSON. A série de percentis por lote, com o
  instante de cada lote, mostra a deriva.

As saídas originais (`stdout` e os JSON) ficam em `ferramenta/`.

### instantaneos/ e ferramenta/

- `instantaneos/`: estados capturados ao fim, como `docker-inspect.json`,
  `pg_stat_statements.csv` e tamanhos finais em disco.
- `ferramenta/`: saída padrão e de erro das ferramentas (TSBS, gerador) e
  `docker logs` de cada container, sem tratamento. A análise só lê esses
  arquivos quando o formato estiver documentado aqui.

### Registros de janela

`abertura.json` e `encerramento.json` trazem o horário, os controles lidos do
sistema, a ocupação do disco e os campos do SMART do P510 (temperatura, dados
gravados, desgaste). O encerramento acrescenta a lista das execuções da janela,
na ordem em que rodaram. A saída completa do `nvme smart-log` fica ao lado, em
texto.

### Mapa das variáveis dependentes

Cada variável dependente da proposta (seção 6.2) e de onde ela sai:

| Variável | Origem nos brutos |
|---|---|
| Latência p50, p95 e p99 | `operacoes.csv`, operações com `ts_us` dentro da medição; consultas do Eixo C em `resumo.json`, com percentis do TSBS |
| Vazão | `operacoes.csv`, operações `ok` divididas pela duração da medição; no Eixo C, diferença de `tsbs_metricas_total` |
| Latência fim a fim | `operacoes.csv`, `operacao = fim_a_fim` |
| Taxa de ingestão sustentável | `tsbs_metricas_total` por intervalo, nas execuções de calibração |
| recall@10 e nDCG@10 | `ferramenta/ranking.trec` e julgamentos de relevância |
| WAL por operação lógica | Diferença de `pg_wal_bytes_total` dividida pelas operações `ok` |
| Tuplas mortas e inflação de tabela e índice | `pg_tuplas_mortas`, `pg_tabela_bytes`, `pg_indices_bytes`, `pg_tuplas_mortas_pct` |
| Frequência e tempo de autovacuum | Diferenças de `pg_autovacuum_total` e de `pg_autovacuum_ms_total` |
| Contenção de lock | `pg_esperas_lock`, `pg_deadlocks_total` |
| Razão de compressão e espaço por ponto | Métricas `tsdb_` e `influx_` de tamanho, divididas pelos pontos ingeridos |
| Bytes gravados por métrica ingerida (ADR-004) | Diferença de `cont_escrita_bytes_total` dos containers do sistema sob teste, dividida pela de `tsbs_metricas_total` |
| Deriva de latência | `operacoes.csv` ao longo do tempo, cenário `longa`; no Eixo C, percentis por lote de consultas |
| Pico de memória e uso de CPU | `cont_mem_pico_bytes`, `cont_cpu_us_total` |
| Tamanho e tempo de construção de índices | `resumo.json` e métricas de tamanho |
| Complexidade operacional e custo | Fora deste formato: medidos sobre os arquivos de composição, não por execução |

### Versionamento

O `.gitignore` exclui `*.csv`, `*.jsonl` e `*.parquet` de `resultados/brutos/`.
Entram no Git os manifestos, os resumos e os registros de janela, todos em
JSON.

As séries completas são publicadas como anexos de uma release do GitHub, fora
do histórico do repositório. São pacotes `tar` comprimidos com `zstd`, um por
eixo e cenário, gerados fora do diretório do repositório. O GitHub aceita
anexos de até 2 GiB cada e até 1000 por release, sem limite de tamanho total
nem de banda. Um pacote que passe de 2 GiB é dividido por arquitetura. Os
SHA-256 dos manifestos, que estão no Git, permitem conferir cada arquivo depois
de descompactado. O tamanho real dos pacotes é estimado no piloto (Etapa 2.5).

### Pendências

1. Confirmar na Etapa 2.4, com o TSBS instalado, as saídas descritas em
   "Eixo C: adaptação do TSBS" e a unidade dos percentis no JSON.
2. Fixar o PostgreSQL em versão 18 ou posterior ao escolher as imagens
   (ADR-011).
3. Medir o custo da coleta no piloto (ADR-021, item 6).

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
