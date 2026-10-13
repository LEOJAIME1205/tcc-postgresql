# Consolidação arquitetural em PostgreSQL — artefatos experimentais

Repositório de artefatos do Trabalho de Conclusão de Curso de **Léo Jaime**, do curso de Ciência de Dados e Inteligência Artificial da Universidade Federal da Paraíba, sob orientação do **Prof. Marcelo Iury de Sousa Oliveira** (Centro de Informática / UFPB).

## Propósito acadêmico

Este repositório existe para tornar verificável um trabalho acadêmico. Ele não é um produto, não é uma recomendação de arquitetura e não é um benchmark oficial de nenhum dos sistemas avaliados.

A publicação atende ao Art. 5º, §8º da Resolução nº 1/2023 do Conselho do Centro de Informática da UFPB, que exige disponibilização pública do código-fonte e dos artefatos produzidos em Trabalhos de Conclusão de Curso.

## Pergunta de pesquisa

> Sob quais condições de carga uma arquitetura consolidada em PostgreSQL entrega desempenho, qualidade e complexidade operacional comparáveis aos de arquiteturas poliglotas compostas por motores especializados, considerando tanto cargas de consulta quanto de escrita intensiva?

## Eixos avaliados

| Eixo | Perfil | Arquitetura consolidada | Baseline especializado |
|---|---|---|---|
| A — Busca híbrida | Leitura | `TSVECTOR` + GIN, `pg_trgm`, `pgvector` (HNSW), fusão em SQL | OpenSearch (BM25) + Qdrant (HNSW), fusão na aplicação |
| B — Fila de tarefas | Escrita transacional | Tabela com `SELECT ... FOR UPDATE SKIP LOCKED` | RabbitMQ, em duas configurações: ingênua e com *transactional outbox* |
| C — Ingestão temporal | Escrita intensiva | PostgreSQL + TimescaleDB | InfluxDB |

Um cenário transversal de carga mista e um teste de longa duração exercitam a interferência entre leitura e escrita e a degradação ao longo do tempo.

## Aviso sobre imparcialidade

Todo benchmark comparativo conduzido por uma única pessoa carrega risco de otimização assimétrica: o autor conhece melhor alguns dos sistemas avaliados do que outros, e isso pode favorecer sistematicamente um dos lados.

Este trabalho trata esse risco de três formas:

1. Toda escolha de configuração está registrada, com justificativa, em [`docs/decisoes-arquiteturais.md`](docs/decisoes-arquiteturais.md).
2. Nenhum sistema recebe tuning customizado. Todos seguem as recomendações oficiais de seus próprios mantenedores, e os desvios estão documentados.
3. O desenho experimental inclui deliberadamente os cenários em que a arquitetura consolidada é estruturalmente mais fraca — escrita sustentada, degradação temporal e interferência entre cargas.

**Contribuições de correção são bem-vindas.** Se você identificar configuração inadequada, comparação injusta ou erro metodológico, abra uma issue. Correções recebidas antes da defesa serão incorporadas; as recebidas depois serão registradas publicamente como errata.

## Estrutura

```
ambiente/          Especificação da máquina e caracterização do hardware
docs/              Proposta, decisões arquiteturais e protocolo de revisão
harness/           Orquestração, coleta de métricas e scripts de análise
eixo-a-busca/      Implementações e cargas do eixo de busca
eixo-b-fila/       Implementações e cargas do eixo de fila
eixo-c-temporal/   Implementações e cargas do eixo temporal
resultados/        Dados brutos e processados das execuções
```

## Reprodução

O ambiente é orquestrado por Docker Compose, com limites fixos de CPU e memória e versões de imagem fixadas por digest. Os procedimentos de reprodução de cada eixo estão no README do respectivo diretório.

Os resultados são sensíveis ao hardware. A máquina utilizada está especificada em [`ambiente/README.md`](ambiente/README.md), e a reprodução em hardware distinto produzirá valores absolutos diferentes — as comparações relativas entre arquiteturas é que devem se sustentar.

## Licença

Código e configurações sob licença MIT (ver [`LICENSE`](LICENSE)). O texto do artigo e a documentação são disponibilizados sob CC BY 4.0.

## Estado

Em desenvolvimento. Defesa prevista para dezembro de 2026.
