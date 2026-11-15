# Carga mista — busca sob ingestão temporal

Etapa 3.3 e QS4: o impacto da escrita intensiva sobre a latência de consulta,
quando as duas cargas competem pelos mesmos recursos (ADR-068). A leitura é a
busca híbrida do Eixo A; a escrita é a ingestão do Eixo C em taxa fixa.

| Arquitetura | Composição |
|---|---|
| Consolidada | Um PostgreSQL de 19 GiB com o banco `busca` do Eixo A e o banco `benchmark` com TimescaleDB, e o serviço de busca (1 GiB) |
| Poliglota | OpenSearch (9 GiB, heap de 4,5 GiB), Qdrant (8 GiB), InfluxDB (2 GiB) e o serviço de busca (1 GiB) |

A divisão da poliglota segue a regra da ADR-037: os 19 GiB dos motores na
proporção da necessidade mínima medida (~4,8 GiB no OpenSearch, ~4,4 GiB no
Qdrant e ~1 GiB no InfluxDB). O PostgreSQL da consolidada tem a memória e o
plano das consultas do Eixo A (ADR-042) e, do TimescaleDB, o que ele exige e o
que foi calibrado para a ingestão (ADR-068, item 2).

| Pasta | Conteúdo |
|---|---|
| `consolidada/`, `poliglota/` | Composição e descritor do orquestrador de cada arquitetura |
| `cargas/` | Imagem do gerador e `gerador.py`, que coordena os geradores dos Eixos A e C |

## Carga

- **Busca:** o gerador do Eixo A com 2 clientes, 300 s de aquecimento e 300 s
  de medição. A semente não depende da taxa, então as mesmas consultas são
  comparadas em todas as taxas de um bloco.
- **Ingestão:** o subcomando `ingerir` do gerador do Eixo C, com o arquivo das
  execuções curtas, a 250 mil, 500 mil ou 1 milhão de métricas/s, 4
  trabalhadores e lotes de 10.000 pontos. Começa 10 s antes do aquecimento da
  busca e para quando a busca termina. Sem a chave `taxas` no plano, a
  execução roda só a busca: é a configuração de base.
- **Validade:** a taxa obtida na medição da busca fica a menos de 1% da
  planejada. O `gerador.json` traz, no bloco `mista`, a taxa obtida, se ela
  foi sustentada e se a ingestão cobriu a medição.

## Preparo

O estado inicial parte da cópia inicial do Eixo A, com o banco `benchmark`
vazio criado como no Eixo C:

```bash
docker build -f eixo-c-temporal/cargas/Dockerfile -t tcc-gerador-temporal:2 .
docker build -f eixo-m-mista/cargas/Dockerfile -t tcc-gerador-mista:1 .
uv run python -m harness.orquestracao.executar preparar m consolidada --copiar-de a
uv run python -m harness.orquestracao.executar preparar m poliglota --copiar-de a
```

A imagem da carga mista copia os programas do TSBS da imagem do Eixo C, que os
compila do código-fonte; por isso ela vem primeiro.

## Saídas

Além do `operacoes.csv` e do `gerador.json` da busca, cada execução com
ingestão grava `metricas-ingestao.csv` (totais carregados e pontos enviados e
devidos a cada segundo), `ingestao.json` e as saídas do TSBS em `ferramenta/`.
Na consolidada, há um coletor por banco: `metricas-postgres-busca.csv` e
`metricas-postgres-benchmark.csv`.
