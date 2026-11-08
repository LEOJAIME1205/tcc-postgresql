# Eixo C — Ingestão de Séries Temporais

Perfil de carga: escrita intensiva sustentada.

| Arquitetura | Composição |
|---|---|
| Consolidada | PostgreSQL 18.6 com TimescaleDB 2.31.0, configurado pelo `timescaledb-tune` |
| Especializada | InfluxDB OSS 1.13.1, motor TSM com índice TSI |

O particionamento declarativo nativo foi removido do escopo por decisão do
orientador. Em consequência, a afirmação deste eixo é sobre o ecossistema
extensível do PostgreSQL, e não sobre o PostgreSQL isolado. Ver ADR-007.

A escolha da linha 1.x do InfluxDB, e não da 2.x nem do 3 Core, está na
ADR-058: é a única com que o TSBS funciona sem alteração e que guarda e consulta
todo o histórico.

| Pasta | Conteúdo |
|---|---|
| `timescaledb/`, `influxdb/` | Composição e descritor do orquestrador de cada arquitetura; saída congelada do `timescaledb-tune` |
| `cargas/` | Imagem do gerador (TSBS compilado e adaptador), `gerador.py` e o remendo do TSBS |
| `dados/` | Dados e consultas gerados, fora do Git; hashes em `dados/README.md` |

## Geração de carga

Time Series Benchmark Suite, compilado do commit `8323e59` com uma única
correção, a do PR #255, sem a qual o carregador não funciona com o TimescaleDB
a partir da 2.14 (ADR-059). Registra-se que a ferramenta foi originada pela
empresa mantenedora do TimescaleDB; a mitigação está descrita na ADR-008.

Dados: caso de uso `cpu-only`, 4.000 hosts, um ponto a cada 10 s durante 3 dias:
103,68 milhões de linhas e 1,04 bilhão de métricas, gerados uma vez e iguais nos
dois sistemas (ADR-060). Consultas: os 15 tipos do caso de uso, 20 de cada em
cada execução, com 1 trabalhador e aquecimento à parte (ADR-061).

```bash
docker build -f eixo-c-temporal/cargas/Dockerfile -t tcc-gerador-temporal:1 .
docker run --rm --user $(id -u):$(id -g) \
  -v $PWD/eixo-c-temporal/dados:/app/eixo-c-temporal/dados tcc-gerador-temporal:1 gerar
```

O gerador cumpre o contrato do harness:

- `preparar` cria o banco `benchmark` vazio (no TimescaleDB, com as extensões
  `timescaledb` e `pg_stat_statements`). O TSBS cria as tabelas na carga.
- `executar` carrega o arquivo inteiro com N trabalhadores, comprime os chunks
  e roda `ANALYZE` no TimescaleDB (ADR-062), ou espera as compactações do
  InfluxDB pararem, e roda as consultas. Grava `metricas-gerador.csv`,
  `resumo.json` e as saídas do TSBS em `ferramenta/` (ADR-065).

Com 2 núcleos físicos, o gerador limita a ingestão do TimescaleDB: em
desenvolvimento, com 8 trabalhadores, ele ficou em 3,6 das suas 4 CPUs lógicas,
enquanto o PostgreSQL usou 3,4 dos 12 núcleos. Nesses casos, a vazão do
TimescaleDB é reportada como limite inferior, e a comparação inclui a
eficiência de cada sistema: CPU e bytes gravados por milhão de métricas
(ADR-065).

Coletores: o do PostgreSQL com as métricas das hypertables (`tsdb_`) e o do
InfluxDB pelo `/debug/vars` (`influx_`), ambos com o tamanho dos dados (ADR-064).

## Métricas

- Taxa de ingestão sustentável em pontos por segundo
- Eficiência: CPU do sistema sob teste e bytes gravados por milhão de métricas
- Amplificação de escrita — volume gravado em disco por ponto ingerido
- Razão de compressão e espaço ocupado
- Latência das consultas analíticas padrão do TSBS
- Deriva de vazão ao longo da execução prolongada
- Temperatura do NVMe durante toda a coleta

## Advertência sobre o armazenamento

Queda de vazão em escrita sustentada pode originar-se do cache pSLC do SSD e
não do banco de dados. O cache é dinâmico e esvaziado em segundo plano, então o
controle não é dimensionar os dados para fugir dele. Em todas as execuções
medem-se a taxa de escrita do disco e os bytes gravados por container; uma
execução de controle com `fio` reproduz a taxa observada; e a atribuição de uma
queda ao SSD segue um critério fixado antes da coleta. Ver a revisão da ADR-004
e a caracterização em `../ambiente/README.md`.
