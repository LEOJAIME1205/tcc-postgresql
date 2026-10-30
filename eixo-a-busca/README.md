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

## Consulta híbrida

- Ramo léxico: configuração `english` do PostgreSQL (`websearch_to_tsquery` e
  `ts_rank_cd`) e analisador `english` do OpenSearch (consulta `match`, BM25).
  As funções de ordenação diferem, e a diferença é declarada.
- Ramo vetorial: os 100 vizinhos mais próximos por cosseno, com o mesmo grafo
  HNSW nos dois sistemas (m = 16, construção = 100) e esforço de busca
  calibrado para o mesmo recall (ADR-038).
- Fusão: Reciprocal Rank Fusion com k = 60 sobre os 100 primeiros de cada
  ramo; devolve os 10 primeiros.

O `pg_trgm` não entra na consulta medida. Ver ADR-040.

## Métricas

- Latência p50, p95 e p99 por nível de concorrência
- Vazão em consultas por segundo
- recall@10 e nDCG@10 contra os julgamentos de relevância
- Tempo de construção e tamanho dos índices
- Pico de memória e uso de CPU

## Equivalência funcional

Ambas as variantes expõem a mesma interface e são verificadas por um conjunto
de testes de contrato antes de qualquer medição.
