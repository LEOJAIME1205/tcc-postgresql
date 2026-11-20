# Leitura dos resultados

Fase 4, etapa 4.1. Atualizado em 20/11/2026.

Este documento reúne o que os dados dizem sobre cada questão da proposta (`docs/proposta-v3.md`, seção 2). Os números saem dos arquivos de `resultados/processados/`, gerados pelos scripts de `harness/analise/`. Cada seção indica a fonte. É material de trabalho para a redação (Fase 5), não texto do artigo.

## Como ler

- **Execuções curtas e mista:** 5 repetições por configuração. O valor é a mediana, com mínimo e máximo quando importam (ADR-070). Os testes são Mann-Whitney exatos e bilaterais com correção de Holm em cada família (ADR-067, complemento do item 7; ADR-068). Com 5 contra 5 e uma família de 3, o menor p de Holm possível é 0,024. Ele aparece sempre que as duas arquiteturas não se sobrepõem.
- **Razões:** consolidada dividida pela outra arquitetura (TimescaleDB pelo InfluxDB no Eixo C). Uma razão maior que 1 em latência é pior para a consolidada; em vazão, melhor.
- **Longa duração:** uma execução de 12 h por arquitetura, descritiva, sem teste (ADR-069). A deriva é a mediana da primeira hora contra a da última e a inclinação de Theil-Sen.
- **Disco:** uma queda só é atribuída ao SSD se coincidir com escrita sustentada perto do piso do padrão e se o controle com `fio` também degradar (ADR-004, item 3). O controle não degradou em 12 h: a escrita ficou em 24,3 MiB/s e o tempo por escrita caiu de 0,24 para 0,013 ms (`longa-deriva.csv`). Nenhuma queda das longas é atribuída ao disco.

## QS1 — Busca híbrida (Eixo A)

Fonte: `curtas-comparacoes.csv`, `curtas-configuracoes.csv`, figura `a-busca`.

| Clientes | Vazão (consultas/s) | p50 (ms) | p99 (ms) |
|---|---|---|---|
| 1 | 1,98 contra 250,6 (126 vezes menos) | 403 contra 3,9 (104 vezes) | 1.440 contra 7,0 (206 vezes) |
| 2 | 3,62 contra 449,5 (124 vezes menos) | 437 contra 4,3 (101 vezes) | 1.536 contra 7,7 (200 vezes) |
| 4 | 4,89 contra 729,1 (149 vezes menos) | 655 contra 5,3 (123 vezes) | 2.385 contra 9,4 (254 vezes) |
| 8 | 5,11 contra 950,5 (186 vezes menos) | 1.147 contra 8,2 (140 vezes) | 6.211 contra 13,9 (448 vezes) |

- **Desempenho:** as 12 comparações têm p de Holm de 0,024 e Â12 de 0 ou 1. Nenhuma repetição da consolidada se aproxima de alguma da poliglota.
- **CPU:** a consolidada usa de 2,9 a 11,9 núcleos para entregar de 2 a 5 consultas/s. A poliglota usa de 1,9 a 10,8 núcleos para entregar de 250 a 950. O custo de CPU por consulta é cerca de 200 vezes maior na consolidada.
- **Qualidade** (`resultados/qualidade/`, ADR-048, item 5 e complemento):

  | | Consolidada | Poliglota |
  |---|---|---|
  | Fusão, nDCG@10 | 0,3698 | 0,4443 |
  | Fusão, recall@10 | 0,5906 | 0,6538 |
  | Ramo léxico, nDCG@10 | 0,0268 | 0,2922 |
  | Ramo léxico, recall@10 | 0,0556 | 0,4570 |
  | Ramo vetorial, nDCG@10 | 0,4976 | 0,4975 |
  | Ramo vetorial, recall@10 | 0,7012 | 0,6996 |

  - **Conferência:** nas duas arquiteturas, a fusão refeita a partir dos ramos deu os mesmos 10 identificadores da passagem de 09/11 nas 3.452 consultas.
  - **Empates no Qdrant:** ele tem empates exatos de pontuação, como as passagens 140 e 145, que têm embeddings idênticos, e a ordem dos 10 pode mudar entre uma subida e outra. Numa rodada de teste, isso trocou a ordem em 1 de 20 consultas.
  - **Ramo vetorial:** é igual nas duas, com o mesmo conjunto de 10 em 88% das consultas, como esperado do recall@100 calibrado para o mesmo patamar (ADR-038).
  - **Ramo léxico:** toda a diferença de qualidade vem dele. O `ts_rank_cd` põe algum relevante entre os 10 primeiros em 221 das 3.452 consultas; o BM25 do OpenSearch, em 1.705.
  - **Mecanismo:** as listas não têm sinal de desempate pelo identificador. A função do PostgreSQL pontua cada documento sozinho, sem estatísticas da coleção como o IDF do BM25.
  - **Fusão:** nas duas arquiteturas, a fusão fica abaixo do ramo vetorial sozinho (0,37 e 0,44 contra 0,50). A perda é maior na consolidada, cujo ramo léxico quase não acerta.
- **Leitura:** a consolidada não é competitiva no Eixo A em nenhum nível testado: perde em vazão, em latência e em qualidade.
  - A diferença de desempenho já aparece com 1 cliente, sem disputa por recursos. O que pesa é o custo de cada consulta: com a consulta OU, o PostgreSQL calcula o `ts_rank_cd` de todo documento que contém algum termo (ADR-039 e ADR-048).
  - O mesmo ramo léxico responde pela diferença de qualidade, enquanto o ramo vetorial empata.
  - Essa é a configuração escolhida e registrada, com as funções de texto nativas do PostgreSQL. Extensões que trazem o BM25 para o PostgreSQL seriam outra configuração e não foram medidas.

## QS2 — Fila (Eixo B)

Fonte: `curtas-execucoes.csv`, `curtas-comparacoes.csv`, figuras `b-vazao` e `b-latencia`.

### Vazão

| Clientes | Consolidada | Ingênua | *Outbox* | Fila da consolidada crescendo |
|---|---|---|---|---|
| 1 | 1.401 | 539 | 1.175 | 0 de 5 |
| 2 | 2.856 | 972 | 2.145 | 0 de 5 |
| 4 | 5.675 | 1.777 | 4.170 | 1 de 5 (2,4%) |
| 8 | 8.886 | 3.103 | 7.916 | 5 de 5 (20% a 21%) |
| 16 | 11.570 | 5.806 | 14.940 | 5 de 5 (35% a 38%) |
| 32 | 12.470 | 9.293 | 10.210 | 5 de 5 (28% a 30%) |

Valores em mensagens consumidas por segundo, mediana de 5.

- **Consumo por nível**, a métrica testada: a consolidada consome mais que a ingênua em todos os níveis (1,3 a 3,2 vezes) e mais que a *outbox* em 5 de 6 (1,1 a 1,4 vezes). Com 16 clientes, a *outbox* consome mais (razão de 0,77). As 18 comparações são significativas.
- **Vazão sustentável**, a pedida pela QS2: é a maior vazão de consumo com a fila sem crescer, com tolerância de 1% (ADR-054, item 5).
  - **Consolidada:** 5.675/s com 4 clientes, numa das 5 repetições acima da tolerância. Com 8 clientes ou mais, os produtores enfileiram mais rápido do que os consumidores retiram, e a fila cresce de 20% a 38% durante a medição.
  - ***Outbox*:** 14.945/s com 16 clientes, nas 5 repetições, 2,6 vezes a da consolidada. Com 32 clientes, o relay de 2 trabalhadores vira o gargalo, e a fila cresce 50%. É um limite inferior (ADR-053).
  - **Ingênua:** 9.293/s com 32 clientes, sem crescer em nenhum nível. Também é um limite inferior, porque ela não saturou.
- **CPU com 32 clientes:** 10,4 núcleos na consolidada, 2,5 na *outbox* e 1,3 na ingênua.
- **Escrita no SSD na medição com 32 clientes:** 39 GiB na consolidada, 77 GiB na *outbox* e 24 GiB na ingênua.
- **Leitura:** por cliente, a consolidada retira mais depressa. Mas a vazão que ela sustenta sem acumular fila é menor que a da *outbox*, a arquitetura com garantia transacional equivalente, e custa 4 vezes mais CPU no nível mais alto.

### Latência fim a fim, com taxa fixa

| Taxa | p50 (ms): consolidada, ingênua, *outbox* | p99 (ms): consolidada, ingênua, *outbox* |
|---|---|---|
| 1.400/s | 49,9 · 2,5 · 3,7 | 98,9 · 3,9 · 86,9 |
| 2.800/s | 39,1 · 2,6 · 3,8 | 98,7 · 4,1 · 8,0 |
| 4.200/s | 35,3 · 2,7 · 4,4 | 98,4 · 4,4 · 7,7 |

- A consolidada é mais lenta nas 12 comparações. O p50 é de 8 a 20 vezes maior e o p99, de 12 a 26 vezes, exceto o p99 contra a *outbox* a 1.400/s (1,14 vez).
- O p99 da consolidada fica perto de 100 ms nas três taxas. É o intervalo de espera por consulta da fila (ADR-051, item 3), adotado das filas de referência PGMQ e Solid Queue. Os 32 consumidores esvaziam a fila juntos e dormem juntos, e o p99 encosta no intervalo inteiro (ADR-054). A *outbox* paga a mesma espera no relay quando a tabela de saída esvazia, o que explica os 87 ms a 1.400/s. O aviso por `pg_notify` foi medido e descartado porque limitava a vazão a 1.734/s (ADR-051). Intervalos menores não foram testados.

### 12 horas a 2.800/s

Fonte: `longa-deriva.csv`, `longa-horas.csv`, `longa-internas-horas.csv`, figuras `longa-b`, `longa-internas-b` e `longa-ssd`.

- **Vazão:** as duas consumiram 2.800/s durante as 12 h, sem acumular fila. A profundidade mediana ficou abaixo de 80 mensagens.
- **Latência da consolidada:** melhora depois de ~40 min. O p50 mediano por hora cai de 38,3 para 14,3 ms e o p99, de 98,6 para 54,4 ms. A escrita no SSD cai de ~23 para ~11,7 MiB/s no mesmo momento.
- **Latência da *outbox*:** estável. O p99 mediano por hora vai de 22,5 para 23,7 ms. A partir de ~5,7 h aparecem picos de p99 de ~3 s, que coincidem com o PostgreSQL dela no limite de 12 GiB, sem prova de causa. A escrita no SSD cai de ~50 para ~23 MiB/s nos primeiros 20 min e fica estável.
- **Tabela da fila** (`tarefas` na consolidada, `saida` na *outbox*): não incha. O tamanho fica entre 0,02 e 0,16 GiB.
  - O autovacuum roda cerca de uma vez por minuto nas duas: ~718 vezes em 12 h, com ~3.400 s acumulados, cerca de 8% do tempo de um processo.
  - As tuplas mortas ficam entre 20 e 65 mil, com picos de até 230 mil. O espaço livre medido pelo `pgstattuple` fica entre 83% e 98% na maior parte do tempo.
- **Tabela de pedidos:** cresce em linha reta até 6,8 GiB de dados e 2,5 GiB de índices, igual nas duas, porque são os dados de negócio.
- **Idade das transações na consolidada:** a consolidada faz 2 commits por mensagem, cerca de 5.900 por segundo. Por isso a idade chega a 199,9 milhões no minuto 592 (~9,9 h), o limite padrão do `autovacuum_freeze_max_age`. Um autovacuum de `pedidos` a traz para 95 milhões no minuto seguinte.
  - O p99 não muda nesse trecho: 54,4 ms antes e 54,7 ms depois.
  - Depois do evento, o patamar de tuplas mortas da fila passa de ~27 mil para ~64 mil.
  - Na *outbox*, com cerca de 2.950 commits por segundo, a idade chega a 127 milhões em 12 h.
- **Leitura:** nenhuma das objeções clássicas ao PostgreSQL como fila aparece em 12 h a 2.800/s. A tabela não incha, a vazão não cai e a latência não piora; o p99 até melhora. O ciclo contra o estouro do contador de transações acontece dentro da janela e passa sem efeito visível na latência.

## QS3 — Séries temporais (Eixo C)

Fonte: `curtas-comparacoes.csv`, `curtas-configuracoes.csv`, `longa-*.csv`, figuras `c-carga`, `c-consultas`, `longa-c-consultas` e `longa-internas-c`.

### Ingestão

| Trabalhadores | Vazão (métricas/s) | CPU (núcleos-s por milhão de métricas) | SSD (MiB por milhão de métricas) |
|---|---|---|---|
| 1 | 2,93 mi contra 1,26 mi (2,3 vezes) | 0,23 contra 1,32 | 73,8 contra 29,0 (2,5 vezes) |
| 2 | 4,86 mi contra 2,21 mi (2,2 vezes) | 0,23 contra 1,40 | 74,6 contra 25,0 (3,0 vezes) |
| 4 | 7,17 mi contra 3,55 mi (2,0 vezes) | 0,27 contra 1,62 | 74,9 contra 23,1 (3,2 vezes) |
| 8 | 9,49 mi contra 4,96 mi (1,9 vezes) | 0,29 contra 1,92 | 77,1 contra 22,1 (3,5 vezes) |

- As 12 comparações são significativas. O TimescaleDB ingere mais e gasta cerca de 6 vezes menos CPU por métrica, mas grava de 2,5 a 3,5 vezes mais no SSD.
- Com 8 trabalhadores, o gerador é o gargalo do TimescaleDB nas 5 repetições, e o valor é um limite inferior (ADR-065).
- Todas as cargas do TimescaleDB passam dos 62 GiB do cache pSLC (~87 GiB gravados). A vazão antes desse ponto fica de 7% a 19% acima da carga inteira, de 3,1 a 11,3 milhões/s. As do InfluxDB gravam de 22 a 29 GiB e não passam.

### Espaço e compressão

Fonte: complemento da ADR-062.

| | TimescaleDB | InfluxDB |
|---|---|---|
| Antes da compressão | 19,1 GiB | não se aplica |
| Depois, no formato nativo | 0,88 GiB (21,7 vezes menor) | 0,50 a 0,53 GiB em TSM |
| Bytes por métrica | 0,91 | 0,52 a 0,55 |
| Diretório inteiro | não medido | 0,56 a 0,60 GiB |
| Carga mais compressão ou compactação | 186 a 423 s | 213 a 827 s |

- Comprimido, o TimescaleDB ocupa cerca de 1,7 vez o InfluxDB.
- Mesmo pagando a compressão numa fase separada (68 a 80 s), ele termina carga mais compressão antes do InfluxDB em todos os níveis.

### Consultas

Fonte: `curtas-comparacoes.csv`, família de 15.

- O TimescaleDB é mais rápido em 12 dos 15 tipos, com razões de 0,05 a 0,94; os maiores ganhos são em `groupby-orderby-limit` e `high-cpu-all`.
- O InfluxDB é mais rápido em `cpu-max-all-8` (1,8 vez), `single-groupby-1-1-1` (2,1 vezes) e `single-groupby-1-8-1` (1,5 vez).
- As 15 comparações são significativas.

### 12 horas a 250 mil métricas/s

Fonte: `longa-deriva.csv`, `longa-lotes.csv`, `longa-bateria-final.csv`, `longa-espaco.csv`, `longa-internas-horas.csv`.

- **Ingestão:** os dois sustentaram a taxa sem atraso durante as 12 h. São 10,8 bilhões de métricas em cada um.
- **Espaço durante a ingestão:** o TimescaleDB fica sem compressão, como declara a ADR-069, item 4. A hipertabela cresce em linha reta até 199 GiB, com 63 chunks. O InfluxDB chega a 4,8 GiB em TSM, com 16 compactações concluídas.
- **Espaço no fim:** a compressão leva o TimescaleDB de 199 GiB a 9,2 GiB (21,6 vezes) em 709 s. O InfluxDB fica em 4,8 GiB.
- **Consultas nos lotes periódicos**, primeiro contra último:
  - no TimescaleDB, 4 tipos ficam de 13 a 32 vezes mais lentos, a maior parte nas primeiras 2 h; 8 ficam de 1,5 a 6 vezes mais lentos; os 3 `single-groupby-5-*` ficam mais rápidos;
  - no InfluxDB, só `lastpoint` (6,4 vezes) e `groupby-orderby-limit` (37 vezes, de 1,3 s para 47 s) pioram.
- **Bateria final, depois da compressão, contra as curtas:**
  - no TimescaleDB, de 1,0 a 3,7 vezes o tempo das curtas, com 10 vezes mais dados;
  - no InfluxDB, de 0,88 a 1,2 vez, exceto `lastpoint` (6,5 vezes).
- **Disco:** no TimescaleDB, o tempo por escrita sobe de 0,71 para 1,60 ms enquanto a escrita fica em ~13,5 MiB/s, longe do piso do padrão. Como o controle não degradou, a lentidão das consultas é atribuída ao banco, pelo critério da ADR-004, e não ao SSD.
- **Leitura:**
  - Na carga e nas consultas sobre dados comprimidos, o TimescaleDB supera o InfluxDB, com custo maior de escrita no SSD e de espaço.
  - Em ingestão contínua sem compressão, as consultas do TimescaleDB degradam muito ao longo das horas. A compressão no fim recupera o desempenho.
  - Uma política de compressão em produção comprimiria os chunks antigos durante a ingestão. Isso não foi medido (ADR-069), e o resultado das 12 h vale para a hipertabela sem compressão.

## QS4 — Interferência (carga mista)

Fonte: `mista-comparacoes.csv`, `mista-configuracoes.csv`, figura `m-degradacao`. São 40 execuções, todas válidas.

| Taxa de escrita | Consolidada: p50, p99 contra a base | Poliglota: p50, p99 contra a base |
|---|---|---|
| 250 mil/s | +3,3%, +2,3% | +2,4%, +4,6%* |
| 500 mil/s | +4,2%, +2,4% | +5,7%, +11,6%* |
| 1 milhão/s | +10,2%\*, +3,9% | +12,9%\*, +19,4%* |

\* significativo depois do Holm, na família de 6 de cada arquitetura.

- **Entre arquiteturas:** só o p99 a 1 milhão/s difere. A consolidada degrada menos: razão de 0,87, p de Holm de 0,048.
- **Em valor absoluto,** a 1 milhão/s:
  - a consolidada perde 45 ms no p50 (de 444 para 489 ms), e a poliglota, 0,6 ms (de 4,5 para 5,1 ms);
  - a vazão de busca cai 8% na consolidada (3,57 para 3,28/s) e 13% na poliglota (425 para 371/s).
- **Leitura:**
  - A QS4 foi formulada para expor uma desvantagem estrutural da consolidação. Em termos relativos, os dados não a mostram: a interferência é pequena nas duas arquiteturas e maior na poliglota no p99 a 1 milhão/s.
  - A comparação relativa depende da base. A consulta da consolidada já leva quase meio segundo sem escrita, e um mesmo atraso pesa menos nela. Em milissegundos, ela perde mais.

## QS5 — Custo e complexidade

Fonte: `custo-*.csv` e as tabelas `custo-qs5-*.tex` (ADR-071 e complementos).

| | A cons. | A polig. | B cons. | B ingênua | B *outbox* | C TimescaleDB | C InfluxDB | Mista cons. | Mista polig. |
|---|---|---|---|---|---|---|---|---|---|
| Serviços | 2 | 3 | 1 | 2 | 3 | 1 | 1 | 2 | 4 |
| Linhas de configuração (pares `-c` juntos) | 67 (62) | 48 | 42 (37) | 54 (49) | 79 (74) | 75 (53) | 9 | 81 (69) | 57 |
| Dependências diretas (fechamento) | 3 (5) | 2 (35) | 1 (2) | 2 (9) | 2 (9) | não se aplica | não se aplica | 3 (5) | 2 (35) |
| Subida mediana (s) | 2,3 | 9,9 | 1,1 | 2,2 | 2,3 | 1,1 | 1,0 | não medida | não medida |
| Pico de memória (GiB, faixa das medianas) | 12,7–15,7 | 7,2–8,6 | 1,3–17,2 | 0,7–2,1 | 1,5–12,7 | 19,0–19,2 | 4,3–4,6 | 12,5–18,4 | 7,9–10,0 |
| Máquinas na AWS (US$/mês) | 277,68 | 277,68 | 233,25 | 174,94 | 219,36 | 233,25 | 233,25 | 277,68 | 263,79 |

- **Leitura:**
  - A consolidada tem menos serviços e muito menos dependências: 5 pacotes contra 35 no A e na mista.
  - Não tem menos linhas de configuração: o lado PostgreSQL tem mais em todos os eixos, mesmo com os pares `-c` contados juntos.
  - Sobe mais depressa no A (2,3 contra 9,9 s) e no B. No C, os dois empatam (1,1 contra 1,0 s).
  - Usa mais memória de pico, quase toda cache de arquivos. Na variante sem cache, o A e a mista ficam praticamente iguais.
  - No custo em nuvem com uma máquina por serviço, a consolidada não fica mais barata em nenhum eixo. No B ela é a mais cara, porque o PostgreSQL de 20 GiB pede uma instância de 32 GiB.
  - O disco soma no máximo US$ 1,11 por mês e não muda a ordem.

## Síntese para a pergunta principal

A pergunta é: sob quais condições a consolidada em PostgreSQL entrega desempenho, qualidade e complexidade comparáveis aos da poliglota?

- **Leitura (busca híbrida):** em nenhuma condição testada.
  - A consolidada perde por duas ordens de grandeza em vazão e latência desde 1 cliente, e também perde em qualidade.
  - As duas perdas vêm do ramo léxico nativo (`ts_rank_cd`). O ramo vetorial (`pgvector`) empata em qualidade com o Qdrant.
- **Fila:**
  - É comparável, ou melhor, em consumo por cliente.
  - É estável por 12 h, sem inchaço.
  - Perde em vazão sustentável para a *outbox* (2,6 vezes), em latência fim a fim (p99 perto do intervalo de 100 ms) e em CPU.
- **Séries temporais:**
  - O TimescaleDB supera o InfluxDB em ingestão, CPU, consultas sobre dados comprimidos e tempo de carga mais compressão.
  - Perde em escrita no SSD por métrica e em espaço comprimido.
  - Sem compressão durante a ingestão, as consultas degradam ao longo das horas.
- **Interferência:** pequena nas duas arquiteturas. A única diferença significativa entre elas é o p99 a 1 milhão/s, que piora menos na consolidada em termos relativos.
- **Complexidade e custo:** menos serviços e dependências, mais linhas de configuração, e custo em nuvem igual ou maior.

A hipótese de trabalho era que a consolidação é competitiva até um patamar de carga.

- **No C:** a consolidada com a extensão é competitiva em todos os níveis testados, com as ressalvas de espaço e da ingestão sem compressão.
- **No B:** é competitiva em vazão até ~5,7 mil mensagens/s, o patamar em que a fila dela ainda não cresce, mas não em latência fim a fim.
- **No A:** não é competitiva em nenhum nível, e a hipótese não se sustenta.

## Ressalvas que aparecem nos dados

- **Limites inferiores:** o TimescaleDB com 8 trabalhadores (gerador como gargalo), a *outbox* com 32 clientes (relay) e a ingênua em todos os níveis (não saturou).
- **Cache pSLC:** as cargas curtas do TimescaleDB passam dele. A vazão antes desse ponto está registrada.
- **Configuração de espera:** o intervalo de 100 ms define a latência da consolidada e da *outbox*. Intervalos menores não foram testados.
- **Ranqueamento léxico:** o resultado do Eixo A vale para as funções de texto nativas do PostgreSQL (`ts_rank_cd`). Extensões com BM25 não foram avaliadas, e isso desfavorece a consolidada no que diz respeito ao que o ecossistema do PostgreSQL oferece.
- **Compressão na longa:** a longa do C não usou política de compressão (ADR-069). O resultado das 12 h não representa uma instalação com política.
- **Indicadores de custo:** a subida foi medida com cache quente, e o disco do PostgreSQL não inclui o WAL (até US$ 2,56 por mês no C). O pico de memória inclui o cache de arquivos (ADR-071 e complementos).
- **Execuções únicas:** os picos de ~3 s da *outbox* e o ciclo contra o estouro do contador de transações vêm de uma execução cada.
