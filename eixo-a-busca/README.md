# Eixo A — Busca Híbrida

Perfil de carga: leitura.

| Arquitetura | Composição |
|---|---|
| Consolidada | PostgreSQL com `TSVECTOR` + GIN, `pg_trgm` e `pgvector` (HNSW), fusão léxico-semântica em SQL |
| Poliglota | OpenSearch (BM25) + Qdrant (HNSW), fusão na camada de aplicação |

## Dados

Subconjunto de coleção pública com julgamentos de relevância, permitindo medir
qualidade de recuperação e não apenas latência. Embeddings gerados uma única vez,
offline, e compartilhados por ambas as arquiteturas. Ver ADR-005 e ADR-009.

## Métricas

- Latência p50, p95 e p99 por nível de concorrência
- Vazão em consultas por segundo
- recall@10 e nDCG@10 contra os julgamentos de relevância
- Tempo de construção e tamanho dos índices
- Pico de memória e uso de CPU

## Equivalência funcional

Ambas as variantes expõem a mesma interface e são verificadas por um conjunto
de testes de contrato antes de qualquer medição.
