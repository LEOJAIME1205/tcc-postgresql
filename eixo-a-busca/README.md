# Eixo A — Busca Híbrida

Perfil de carga: leitura.

| Arquitetura | Composição |
|---|---|
| Consolidada | Serviço de busca + PostgreSQL com `TSVECTOR` + GIN e `pgvector` (HNSW); fusão léxico-semântica numa única consulta SQL |
| Poliglota | Serviço de busca + OpenSearch 3.8.0 (BM25) + Qdrant 1.19.2 (HNSW); fusão no serviço |

Nas duas arquiteturas, o gerador de carga chama um serviço de busca com a mesma
interface HTTP, que roda dentro do sistema sob teste. Assim o trabalho de fusão
e as chamadas de rede contam na medição dos dois lados. Ver ADR-039.

## Dados

Coleção NQ (Natural Questions) do BEIR: 2,68 milhões de passagens da Wikipédia
e 3.452 consultas de teste com julgamentos de relevância. Ver ADR-009.

Embeddings gerados uma única vez, offline, com `BAAI/bge-small-en-v1.5`
(384 dimensões), e compartilhados pelas duas arquiteturas. As consultas recebem
o prefixo recomendado pelo modelo. Ver ADR-005 e ADR-035.

O corpus é baixado e os embeddings são gerados pelos scripts de `preparo/`.
Comandos, arquivos e hashes em [`dados/README.md`](dados/README.md).

## Consulta híbrida

- Ramo léxico: basta um termo da consulta para o documento entrar (OU), sobre
  título e texto. Configuração `english` do PostgreSQL (termos do
  `plainto_tsquery` ligados por OU, ordenação por `ts_rank_cd`) e analisador
  `english` do OpenSearch (consulta `match` padrão, BM25). As funções de
  ordenação diferem, e a diferença é declarada.
- Ramo vetorial: os 100 vizinhos mais próximos, com o mesmo grafo HNSW nos
  dois sistemas (m = 16, construção = 100), produto interno no `pgvector` e
  cosseno no Qdrant (mesma ordem, vetores normalizados) e esforço de busca
  calibrado para o mesmo recall@100 (ADR-038).
- Fusão: Reciprocal Rank Fusion com k = 60 sobre os 100 primeiros de cada
  ramo; devolve os 10 primeiros, só identificadores e pontuações (ADR-041).

Esquema, configuração e carga do PostgreSQL: ADR-041 a ADR-043.

O `pg_trgm` não entra na consulta medida. Ver ADR-040.

## Métricas

- Latência p50, p95 e p99 por nível de concorrência
- Vazão em consultas por segundo
- recall@10 e nDCG@10 contra os julgamentos de relevância
- Tempo de construção e tamanho dos índices
- Pico de memória e uso de CPU

## Equivalência funcional

As duas variantes expõem a mesma interface, `POST /buscar` e `GET /saude`, no
serviço de `servico/` (ADR-046). Antes de qualquer medição, o teste de contrato
confere, com a arquitetura no ar:

- que o serviço está pronto;
- o formato das respostas: 10 identificadores distintos do corpus e 10
  pontuações, em ordem decrescente e com empates pelo menor identificador;
- que pedidos inválidos são recusados com 400, e o método errado com 405;
- que cada resposta é idêntica, em identificadores e pontuações exatas, à fusão
  em Python dos dois ramos buscados direto nos motores, com o mesmo `ef` do
  serviço. Na consolidada, isso compara a consulta SQL única com a fusão em
  Python, e garante que a fórmula e o desempate são os mesmos nos dois lados.

```bash
TCC_DADOS_POSTGRES=~/tcc-dados/a/consolidada/postgres/atual \
  docker compose -p tcc-a-consolidada -f eixo-a-busca/consolidada/compose.yaml up -d
uv run python eixo-a-busca/servico/contrato.py consolidada --registro resultados/contrato/a-consolidada.json
```

As 100 consultas da amostra são fixas (semente no script), as mesmas nas duas
arquiteturas. Resultado em 07/11/2026: as duas arquiteturas passaram em todas as
verificações; os registros estão em `resultados/contrato/`. Nas mesmas consultas,
as duas arquiteturas têm em média 3,9 dos 10 resultados em comum, efeito das
funções de ordenação léxica diferentes (ADR-039) e do `ef` ainda não calibrado
(ADR-038); a qualidade de cada uma é medida pelo nDCG@10.
