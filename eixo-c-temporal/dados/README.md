# Dados do Eixo C

Gerados uma única vez com o TSBS da imagem `tcc-gerador-temporal:1` (ADR-059 e
ADR-060). Não entram no Git. O registro completo, com o SHA-256 de cada arquivo,
fica em `geracao.json`, também fora do Git, e os valores abaixo permitem conferir
uma cópia.

```bash
docker build -f eixo-c-temporal/cargas/Dockerfile -t tcc-gerador-temporal:1 .
docker run --rm --user $(id -u):$(id -g) \
  -v $PWD/eixo-c-temporal/dados:/app/eixo-c-temporal/dados tcc-gerador-temporal:1 gerar
```

| Parâmetro | Valor |
|---|---|
| Caso de uso | `cpu-only` |
| Hosts | 4.000 |
| Intervalo | 10 s |
| Período dos dados | 2016-01-01T00:00:00Z a 2016-01-04T00:00:00Z |
| Semente | 20261108 (dados e consultas medidas); 20261109 (consultas de aquecimento) |
| Pontos | 103.680.000 (1.036.800.000 métricas) |
| Consultas medidas | 20 por tipo, 15 tipos, período até 2016-01-04T00:00:01Z |
| Consultas de aquecimento | 5 por tipo, em `consultas/aquecimento/` |

Gerado em 08/11/2026, em cerca de 5 minutos, comprimido com `gzip -1`.

| Arquivo | Tamanho | SHA-256 |
|---|---|---|
| `carga-timescaledb.gz` | 3,72 GiB | `9f21bb3a9278988a1a2578300d7b825a12f9c61ae8f19d9a06fe608171d3fdbd` |
| `carga-influxdb.gz` | 5,60 GiB | `0deea88caa5c3a97cf77eeea6e6a1764aa19fff007f7770d2d6ee54d1e1f315d` |

Os dois formatos trazem os mesmos pontos com a mesma semente. Conferido na
geração de 1 dia, com os mesmos parâmetros: o primeiro ponto (`host_0`,
2016-01-01T00:00:00Z) e o último, com os mesmos dez valores, e as contagens
batendo (o formato do TimescaleDB tem uma linha de tags por ponto e três de
cabeçalho). Nos 3 dias, as duas cargas de desenvolvimento de 08/11/2026
gravaram 103.680.000 pontos, e três consultas de `high-cpu-1` conferidas direto
nos dois bancos devolveram as mesmas contagens e somas (0; 1.197 e 63.825; 184 e
7.121). O TimescaleDB recebe os valores como `double precision` e
o InfluxDB como inteiros, como o TSBS gera.

As consultas ficam em `consultas/<arquitetura>-<tipo>.gz` e
`consultas/aquecimento/<arquitetura>-<tipo>.gz`, 60 arquivos, com os hashes em
`geracao.json`.
