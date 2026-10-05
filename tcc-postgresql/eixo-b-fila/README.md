# Eixo B — Fila de Tarefas

Perfil de carga: escrita transacional com consumo concorrente.

| Configuração | Composição | Garantia |
|---|---|---|
| Consolidada | Tabela de tarefas com `SELECT ... FOR UPDATE SKIP LOCKED`; enfileiramento na mesma transação do dado de negócio | Atômica por construção |
| Poliglota ingênua | Publicação direta no RabbitMQ após gravar no banco | Sem garantia — sujeita a escrita dupla |
| Poliglota com outbox | Tabela de saída no PostgreSQL e processo relay publicando no RabbitMQ | Equivalente à consolidada |

A configuração ingênua é mantida como referência de teto de desempenho e
identificada como tal no artigo. A comparação principal ocorre entre a
consolidada e a configuração com outbox, que têm garantias equivalentes.
Ver ADR-006.

## Métricas

- Vazão sustentável em mensagens por segundo
- Latência fim a fim, do enfileiramento ao consumo
- Volume de WAL gerado por mensagem
- Crescimento de tuplas mortas e inflação de tabela e índice ao longo do tempo
- Atividade de autovacuum e ocorrência de contenção de lock
- Deriva de latência entre início e fim do teste de longa duração

## Verificação de equivalência

Teste que derruba o processo relay no meio da carga e confirma que nenhuma
mensagem se perde nas configurações com garantia. Sem esse teste, a afirmação
de equivalência seria apenas alegação.

## Risco conhecido

O relay pode se tornar o gargalo da configuração com outbox e distorcer a
comparação. Sua vazão isolada é medida antes do experimento principal.
