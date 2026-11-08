# Eixo B — Fila de Tarefas

Perfil de carga: escrita transacional com consumo concorrente.

| Configuração | Composição | Garantia |
|---|---|---|
| Consolidada | PostgreSQL com a tabela de tarefas: retirada com `SELECT ... FOR UPDATE SKIP LOCKED`; enfileiramento na mesma transação do dado de negócio | Atômica por construção |
| Poliglota ingênua | PostgreSQL com os pedidos e RabbitMQ 4.4.0: publicação direta depois de gravar no banco | Sem garantia — sujeita a escrita dupla |
| Poliglota com outbox | PostgreSQL com os pedidos e a tabela de saída, relay e RabbitMQ 4.4.0 | Equivalente à consolidada |

A configuração ingênua é mantida como referência de teto de desempenho e
identificada como tal no artigo. A comparação principal ocorre entre a
consolidada e a configuração com outbox, que têm garantias equivalentes.
Ver ADR-006.

| Pasta | Conteúdo |
|---|---|
| `consolidada/`, `ingenua/`, `outbox/` | Composição, descritor do orquestrador e `esquema.sql` de cada configuração; `rabbitmq.conf` nas poliglotas |
| `outbox/relay.py` | Relay da outbox, com imagem própria (`outbox/Dockerfile`) |
| `cargas/gerador.py` | Gerador de carga, com imagem própria (`cargas/Dockerfile`) |
| `cargas/equivalencia.py` | Verificação de equivalência, no host |
| `cargas/relay_isolado.py` | Vazão isolada do relay, no host |

## Operação e mensagem

Cada enfileiramento grava um pedido (`pedidos`: cliente, valor e instante) e
uma tarefa que se refere a ele. A mensagem tem 16 bytes de cabeçalho, com o
instante Unix e o relógio monotônico do enfileiramento, e 1 KiB de carga útil
sorteada pela semente: os mesmos bytes nas três configurações. O identificador
do pedido viaja ao lado, na coluna `pedido` e no `message_id` do RabbitMQ.
Ver ADR-050.

| Configuração | Enfileiramento | Consumo |
|---|---|---|
| Consolidada | Um comando: `INSERT` do pedido e da tarefa, uma transação | `DELETE ... WHERE id = (SELECT ... ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING`, em *autocommit* |
| Ingênua | `INSERT` do pedido confirmado; depois publicação com confirmação do broker | Fila quorum, *prefetch* de 100, confirmação manual |
| Outbox | Um comando: `INSERT` do pedido e da linha de saída, uma transação | Relay publica em lotes de até 100; consumo como na ingênua |

Diante da fila vazia, o consumidor da consolidada e o relay da outbox tentam de
novo a cada 100 ms, o padrão do PGMQ e do Solid Queue. O mecanismo de aviso do
PostgreSQL (`LISTEN`/`NOTIFY`) foi medido e descartado: o commit que avisa toma
um bloqueio global e prende o banco em cerca de 1,8 mil enfileiramentos por
segundo com qualquer concorrência, contra 34 mil sem aviso. O teto é medido na
calibração com `TCC_ESPERA=aviso` e reportado como achado. Ver ADR-051 e
ADR-053.

## Carga e medição

O gerador (ADR-054) cumpre o contrato do harness. As imagens são construídas a
partir da raiz do repositório:

```bash
docker build -f eixo-b-fila/cargas/Dockerfile -t tcc-gerador-fila:1 .
docker build -f eixo-b-fila/outbox/Dockerfile -t tcc-relay:1 .
```

- `preparar` aplica o `esquema.sql` da arquitetura e, nas poliglotas, declara a
  fila `tarefas` (quorum, durável). Chamado pelo orquestrador:
  `uv run python -m harness.orquestracao.executar preparar b <arquitetura>`.
- `executar` mantém N produtores e N consumidores em laço fechado (níveis de 1 a
  32) e grava o `operacoes.csv`, com as operações `enfileira`, `consome` e
  `fim_a_fim`, e o `gerador.json`, com as contagens, as retiradas vazias e as
  mensagens que restaram no sistema ao fim.
- A latência de `consome` não é comparada entre arquiteturas: no PostgreSQL ela
  inclui a ida ao banco; no RabbitMQ, o broker já entregou a mensagem. A
  comparação usa a vazão e a latência fim a fim.

Coletores: PostgreSQL nas três; RabbitMQ (`rmq_`) nas poliglotas (ADR-057).

Memória: consolidada com o PostgreSQL em 20 GiB; ingênua com 12 GiB no
PostgreSQL e 8 GiB no RabbitMQ; outbox com 12, 7 e 1 GiB no relay (ADR-055).

## Métricas

- Vazão sustentável em mensagens por segundo
- Latência fim a fim, do enfileiramento ao consumo
- Volume de WAL gerado por mensagem
- Crescimento de tuplas mortas e inflação de tabela e índice ao longo do tempo
- Atividade de autovacuum e ocorrência de contenção de lock
- Espaço em disco da fila quorum, para comparar com a inflação das tabelas
- Deriva de latência entre início e fim do teste de longa duração

## Verificação de equivalência

Teste que derruba o relay no meio da carga e confirma que nenhuma mensagem se
perde nas configurações com garantia (ADR-056). Com a arquitetura no ar e
recém-preparada:

```bash
uv run python eixo-b-fila/cargas/equivalencia.py <arquitetura> \
  --registro resultados/equivalencia/b-<arquitetura>.json
```

Oito produtores e oito consumidores rodam por 60 s. Os produtores são
derrubados com `SIGKILL` aos 15 s e aos 35 s; na outbox, o relay também, aos
25 s. Depois que a fila esvazia, os pedidos gravados são comparados com os
consumidos. Resultado do teste em desenvolvimento, em 08/11/2026:

| Configuração | Pedidos | Perdidos | Duplicatas |
|---|---|---|---|
| Consolidada | 611.784 | 0 | 0 |
| Ingênua | 157.181 | 8 | 0 |
| Outbox | 439.705 | 0 | 1 |

A ingênua perde os pedidos cujo produtor morreu entre o commit e a publicação.
A outbox reentrega a mensagem que o relay publicou antes de morrer e não chegou
a confirmar no banco.

## Risco conhecido

O relay pode se tornar o gargalo da configuração com outbox e distorcer a
comparação. Sua vazão isolada é medida antes do experimento principal:

```bash
uv run python eixo-b-fila/cargas/relay_isolado.py --registro resultados/relay/vazao-isolada.json
```

O script para o relay, grava 1 milhão de linhas na tabela de saída, liga os
consumidores e religa o relay. A vazão é medida pela contagem da tabela até ela
esvaziar. No teste em desenvolvimento, com 200 mil linhas, foram cerca de 20 mil
mensagens por segundo com 2 trabalhadores. Com 32 produtores, a outbox já grava
mais rápido que isso, e a calibração decide o número de trabalhadores
(ADR-053).
