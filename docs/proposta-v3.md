# Proposta de Trabalho de Conclusão de Curso — versão 3

**Título provisório:** Consolidação arquitetural em PostgreSQL: avaliação experimental de cargas de leitura e de escrita frente a arquiteturas poliglotas

**Aluno:** Léo Jaime
**Orientador:** Prof. Marcelo Iury de Sousa Oliveira (DINF/CI)
**Curso:** Ciência de Dados e Inteligência Artificial — UFPB
**Modalidade (Res. 1/2023 CI-DC, Art. 4º):** III — pesquisa envolvendo trabalho prático com avaliação de software
**Formato de entrega (Art. 5º, §2º e §3º):** artigo científico, template de artigo completo da SBC
**Data prevista de defesa:** após 10/12/2026

---

## 0. Alterações desta versão

A versão 2 incorporou os eixos de escrita apontados pelo orientador. Esta versão registra as decisões tomadas em 25/09/2026 e especifica o ambiente de medição.

1. **Outbox confirmado.** O Eixo B passa a ter três configurações: fila consolidada, fila poliglota ingênua e fila poliglota com *transactional outbox*. A comparação principal ocorre entre a primeira e a terceira, que têm garantias transacionais equivalentes.
2. **Eixo C reduzido a duas vias.** O particionamento declarativo nativo sai do escopo. A comparação passa a ser PostgreSQL com TimescaleDB contra InfluxDB.
3. **Ambiente de medição especificado**, com tratamento explícito dos fatores de confusão que a máquina única introduz (seção 9).
4. A revisão multivocal em paralelo à construção do ambiente.

### Consequência de enunciado do Eixo C

Com o particionamento nativo fora, o lado consolidado do eixo temporal depende de uma extensão de terceiros. O trabalho deixa de afirmar que o PostgreSQL isolado substitui um banco temporal e passa a afirmar que o **ecossistema extensível do PostgreSQL** o substitui, sem sair do mesmo motor, do mesmo dialeto SQL e da mesma transação.

É uma tese mais defensável, e coerente com o argumento geral — que trata de extensibilidade, não de funcionalidade nativa. Perde-se, porém, a capacidade de distinguir limitação do motor de ausência de extensão adequada. Essa perda entra nas limitações declaradas.

---

## 1. Contexto e motivação

A arquitetura de software da última década consolidou o princípio da *polyglot persistence*: para cada tipo de carga, um armazenamento especializado. Busca textual vai para o Elasticsearch, filas para o RabbitMQ ou Kafka, embeddings para um banco vetorial dedicado, séries temporais para o InfluxDB. A tese subjacente foi formulada academicamente por Stonebraker e Çetintemel (2005), que argumentaram que sistemas de propósito geral seriam progressivamente superados por motores especializados em cada domínio.

Esse princípio tem custo. Cada serviço adicional introduz um processo a operar, um modelo de consistência a conciliar, um pipeline de sincronização a manter e uma fatia de custo de infraestrutura. Introduz também um problema de correção frequentemente subestimado: a escrita dupla. Gravar um dado de negócio no banco relacional e publicar o evento correspondente em um broker externo são duas operações que não compartilham transação, o que exige padrões de compensação como o *transactional outbox* (Richardson, 2018; Kleppmann, 2017).

Em paralelo, o PostgreSQL evoluiu de banco relacional para plataforma extensível. Tipos nativos, estruturas de índice diversas (GIN, GiST, BRIN, HNSW), `SELECT ... FOR UPDATE SKIP LOCKED` e um ecossistema maduro de extensões permitem que funções historicamente delegadas a serviços especializados sejam executadas dentro do próprio banco, sob uma única transação.

Existe hoje um movimento expressivo na indústria defendendo essa consolidação, sintetizado no lema "just use Postgres". O movimento, porém, é predominantemente argumentativo e veiculado em literatura cinzenta. Falta evidência empírica sistematizada, e falta especialmente evidência sobre cargas de escrita, onde residem as objeções técnicas mais consistentes.

## 2. Problema e pergunta de pesquisa

**Pergunta de pesquisa principal (PP):**
> Sob quais condições de carga uma arquitetura consolidada em PostgreSQL entrega desempenho, qualidade e complexidade operacional comparáveis aos de arquiteturas poliglotas compostas por motores especializados, considerando tanto cargas de consulta quanto de escrita intensiva?

**Questões secundárias:**

- **QS1 — Leitura.** Qual a diferença de latência (p50/p95/p99), vazão e qualidade de recuperação (recall@10, nDCG@10) entre busca híbrida consolidada e arquitetura poliglota de busca?
- **QS2 — Fila.** Qual a vazão sustentável e a latência fim a fim de uma fila implementada com `SKIP LOCKED` frente a um broker dedicado com garantias transacionais equivalentes, e como esses indicadores evoluem sob execução prolongada?
- **QS3 — Séries temporais.** Qual a taxa de ingestão sustentável, a amplificação de escrita e a razão de compressão do PostgreSQL com TimescaleDB frente a um banco temporal dedicado?
- **QS4 — Interferência.** Em carga mista, qual o impacto da escrita intensiva sobre a latência de consulta na arquitetura consolidada, onde ambas competem pelos mesmos recursos? Este é o custo específico da consolidação e não tem equivalente na arquitetura poliglota.
- **QS5 — Custo e complexidade.** Qual a diferença de complexidade operacional e de custo estimado entre as arquiteturas, por indicadores objetivos?
- **QS6 — Critérios.** Que critérios de decisão emergem da literatura acadêmica e cinzenta para escolher entre consolidação e especialização?

**Nota sobre a formulação.** A hipótese de trabalho é que a consolidação é competitiva até um patamar de carga, mas o desenho admite refutação. A QS4, em particular, foi formulada para expor uma desvantagem estrutural da consolidação.

## 3. Objetivos

**Objetivo geral:** avaliar empiricamente os limites da consolidação arquitetural em PostgreSQL sob cargas de leitura e de escrita, produzindo critérios objetivos de decisão arquitetural.

**Objetivos específicos:**

1. Sistematizar, por revisão multivocal, os argumentos favoráveis e contrários à consolidação presentes na literatura acadêmica e cinzenta.
2. Implementar três cargas de referência — busca, fila e ingestão temporal — em arquiteturas consolidada e poliglota funcionalmente equivalentes e com garantias equivalentes.
3. Medir desempenho, qualidade, consumo de recursos, amplificação de escrita e degradação temporal em cada eixo.
4. Caracterizar a interferência entre leitura e escrita concorrentes na arquitetura consolidada.
5. Derivar critérios de decisão em função de perfil de carga, volume e restrições de equipe.
6. Publicar todos os artefatos em repositório público (Art. 5º, §8º).

## 4. Delimitação de escopo

### 4.1 Eixos avaliados experimentalmente

| Eixo | Perfil | Arquitetura consolidada | Baseline especializado |
|---|---|---|---|
| **A. Busca híbrida** | Leitura | `TSVECTOR` + GIN, `pg_trgm`, `pgvector` (HNSW), fusão em SQL | OpenSearch (BM25) + Qdrant (HNSW), fusão na aplicação |
| **B. Fila de tarefas** | Escrita transacional | Tabela com `SELECT ... FOR UPDATE SKIP LOCKED` | RabbitMQ, em configuração ingênua e com outbox |
| **C. Ingestão temporal** | Escrita intensiva | PostgreSQL + TimescaleDB | InfluxDB |

**Eixo A.** Avalia-se um único serviço de busca híbrida em vez de três comparações independentes. A fusão de resultados léxicos e semânticos em uma só query SQL é onde a consolidação tem vantagem estrutural, e é o cenário relevante para sistemas de RAG, o que ancora o trabalho na área do curso.

**Eixo B.** Na arquitetura consolidada, enfileirar a tarefa e gravar o dado de negócio ocorrem na mesma transação, o que elimina a escrita dupla por construção. A configuração poliglota ingênua não oferece essa garantia. Comparar as duas diretamente compararia uma solução correta com outra que é mais rápida porque abre mão da correção. Por isso implementa-se também a configuração com outbox, que restabelece a paridade. A ingênua permanece como referência de teto de desempenho, identificada como tal.

**Eixo C.** Duas vias, conforme a seção 0.

### 4.2 Cenário transversal: carga mista e longa duração

- **Carga mista:** escrita intensiva e consulta concorrentes, medindo degradação da latência de leitura.
- **Teste de longa duração:** execução contínua de várias horas com produção e consumo sustentados, medindo crescimento de tuplas mortas, atividade de autovacuum, inflação de tabela e índice, e deriva de latência.

Esse cenário é o que dá credibilidade ao trabalho. As objeções ao uso do PostgreSQL como fila e como armazenamento temporal são objeções sobre comportamento ao longo do tempo, não sobre desempenho instantâneo. Uma execução curta não as detectaria.

### 4.3 Escopo discutido qualitativamente, sem medição

Documentos não estruturados (`JSONB` + GIN) frente a MongoDB; agregações analíticas (views materializadas com `REFRESH CONCURRENTLY`) frente a data warehouses; geração automática de API (PostgREST, `pg_graphql`); dados geoespaciais (PostGIS); e particionamento declarativo nativo com índices BRIN, retirado da avaliação experimental conforme a seção 0.

### 4.4 Fora de escopo

Sharding horizontal e arquiteturas distribuídas multi-nó; cargas na ordem de milhões de eventos por segundo; cache em memória com latência sub-milissegundo; alta disponibilidade e replicação; custo total de propriedade incluindo pessoal.

Esses limites coincidem com as limitações declaradas do argumento de consolidação e serão apresentados como tal.

## 5. Fundamentação teórica e trabalhos relacionados

O trabalho dialoga diretamente com uma tese estabelecida. Stonebraker e Çetintemel (2005), em *One Size Fits All: An Idea Whose Time Has Come and Gone*, e Stonebraker et al. (2007), em *The End of an Architectural Era*, argumentaram que motores especializados superariam sistemas de propósito geral por margens de uma ordem de grandeza. Fowler e Sadalage (2012) consolidaram a prática correspondente sob o rótulo de *polyglot persistence*.

A contribuição pretendida é revisitar essa tese à luz da extensibilidade alcançada pelo PostgreSQL duas décadas depois, verificando se a extensibilidade de um sistema de propósito geral pode absorver os ganhos da especialização em uma faixa relevante de aplicações — e, crucialmente, se consegue fazê-lo também sob escrita intensiva, onde o argumento de Stonebraker é mais forte.

A literatura complementar cobre: consistência e integração entre sistemas heterogêneos (Kleppmann, 2017; Richardson, 2018); busca vetorial aproximada e recuperação de informação (Malkov e Yashunin, 2020; Thakur et al., 2021); benchmarking de sistemas de dados (Cooper et al., 2010; TSBS); e engenharia de software empírica (Wohlin et al., 2012).

## 6. Metodologia

### 6.1 Revisão multivocal da literatura

Adota-se o protocolo de Garousi, Felderer e Mäntylä (2019), com strings de busca, bases consultadas, critérios de inclusão e exclusão e critérios de avaliação de qualidade das fontes cinzentas. O funil de seleção será documentado como figura.

### 6.2 Estudo experimental comparativo

**Cargas de referência.** Cada eixo é implementado em variantes funcionalmente equivalentes, expondo a mesma interface e verificadas por testes de contrato executados contra todas elas.

**Dados.** Eixo A: subconjunto do BEIR ou MS MARCO, com julgamentos de relevância — condição indispensável para medir qualidade. Eixo B: mensagens sintéticas com carga útil de tamanho controlado. Eixo C: geração pelo TSBS, que suporta TimescaleDB e InfluxDB sob o mesmo harness.

**Variáveis independentes:** arquitetura; eixo; nível de concorrência; duração da execução.

**Variáveis dependentes:**

*Desempenho e qualidade* — latência p50/p95/p99 e vazão; latência fim a fim de enfileiramento a consumo; taxa de ingestão sustentável; recall@10 e nDCG@10.

*Comportamento de escrita* — volume de WAL por operação lógica; crescimento de tuplas mortas e inflação de tabela e índice; tempo e frequência de autovacuum; contenção de lock; razão de compressão e espaço por ponto ingerido; deriva de latência ao longo do teste prolongado.

*Recursos, complexidade e custo* — pico de memória, uso de CPU, tamanho e tempo de construção de índices; número de serviços, imagens, linhas de configuração de infraestrutura e dependências; tempo de provisionamento a frio; estimativa mensal de infraestrutura equivalente em nuvem.

**Protocolo de execução.** Múltiplas repetições independentes por cenário, com descarte de aquecimento, restauração do estado inicial entre execuções e ordem aleatorizada entre arquiteturas. O teste de longa duração roda uma vez por arquitetura, com estado contínuo e amostragem periódica, e é reportado como série temporal. Resultados com medidas de dispersão e teste estatístico apropriado.

## 7. Ameaças à validade

- **Validade de construto:** as métricas de complexidade operacional são proxies imperfeitos e serão declaradas como tais.
- **Validade interna — otimização assimétrica:** o autor tem mais familiaridade com PostgreSQL do que com os motores especializados. Mitigação: nenhum sistema recebe tuning customizado, todos seguem recomendações oficiais de seus mantenedores, e toda configuração está registrada no documento de decisões arquiteturais do repositório. A inclusão dos eixos de escrita e do teste de longa duração é ela própria uma mitigação, por exercitar deliberadamente os pontos fracos da consolidação.
- **Validade interna — confusão com o armazenamento:** SSDs de consumo empregam cache pSLC, cuja saturação sob escrita sustentada produz queda de vazão que poderia ser confundida com degradação do banco. Mitigação na seção 9.
- **Validade interna — contenção com o gerador de carga:** gerador e sistema sob teste dividem a mesma máquina. Mitigação na seção 9.
- **Validade interna — ferramenta de terceiros:** o TSBS foi originado pela empresa mantenedora do TimescaleDB. Mitigação: uso exclusivo dos cenários e consultas padrão, sem seleção de subconjunto favorável, com publicação dos parâmetros exatos. A origem é declarada no artigo.
- **Validade externa:** resultados limitados aos três eixos, aos volumes testados e a um único nó; não generalizáveis para os cenários de 4.4.
- **Validade de conclusão:** número de repetições e testes estatísticos justificados explicitamente. O teste de longa duração, por ter execução única, é evidência descritiva e não inferencial.

## 8. Resultados esperados e contribuições

1. Caracterização empírica da faixa de carga em que a consolidação é competitiva, separadamente para leitura e escrita.
2. Quantificação do custo de interferência entre leitura e escrita na arquitetura consolidada.
3. Evidência sobre degradação temporal do PostgreSQL nos papéis de fila e de armazenamento temporal.
4. Comparação de desempenho entre arquiteturas com garantias transacionais equivalentes, e não apenas funcionalmente semelhantes.
5. Conjunto de critérios de decisão arquitetural derivado de evidência.
6. Ambiente experimental reprodutível e publicado.

## 9. Ambiente experimental

Todos os experimentos são executados em uma única máquina de mesa.

| Componente | Especificação |
|---|---|
| Processador | AMD Ryzen 7 9700X — 8 núcleos, 16 threads, Zen 5 |
| Memória | 32 GB DDR5 |
| Armazenamento | SSD NVMe Crucial P510 1 TB, PCIe 5.0 x4, com dissipador |
| GPU | ASUS GeForce RTX 5070 |
| Placa-mãe | ASUS com suporte a PCIe 5.0 |

A máquina é adequada à faixa de escala investigada, que é explicitamente a de aplicações de porte pequeno e médio — o domínio em que a tese de consolidação é defendida. Ela introduz, porém, quatro fatores de confusão que precisam ser controlados.

**Contenção entre gerador de carga e sistema sob teste.** Com oito núcleos físicos, a competição por CPU é real e assimétrica: a arquitetura poliglota mantém mais processos ativos e sofreria mais. Controle: fixação de `cpuset`, com conjuntos disjuntos de núcleos reservados ao sistema sob teste e ao gerador de carga, idênticos entre as arquiteturas comparadas.

**Orçamento de memória.** Vários dos sistemas avaliados dimensionam cache e heap automaticamente a partir da memória visível do host; o OpenSearch, em particular, pode reivindicar heap proporcional à RAM total. Controle: limites explícitos por container e configuração explícita dos parâmetros internos de memória de cada sistema, com orçamento total idêntico entre as arquiteturas de cada eixo. Registra-se que a arquitetura poliglota precisa dividir esse orçamento entre dois serviços enquanto a consolidada o concentra — diferença real das arquiteturas, não artefato do experimento.

**Cache pSLC e throttling térmico do SSD.** SSDs de consumo operam em alta velocidade até a saturação do cache pSLC e caem para a vazão de regime permanente em seguida. O Eixo C consiste em escrita sustentada por horas. Uma queda originada no dispositivo poderia ser atribuída indevidamente ao banco de dados, comprometendo justamente a contribuição central do trabalho. Controle: caracterização prévia do dispositivo com `fio`, identificando o ponto de saturação, a vazão em regime permanente e a curva de temperatura sob carga prolongada; dimensionamento do conjunto de dados de modo que a comparação principal ocorra fora da faixa de cache; amostragem contínua da temperatura do NVMe durante todas as execuções, publicada junto aos resultados.

**Variabilidade de frequência.** Processadores Ryzen ajustam frequência dinamicamente conforme temperatura e consumo, e a ordem de execução introduziria deriva térmica sistemática. Controle: aleatorização da ordem entre arquiteturas, intervalo de resfriamento entre execuções, e amostragem de frequência e temperatura durante toda a coleta.

**Uso da GPU.** Os embeddings do Eixo A são gerados uma única vez, offline, e persistidos. Nenhum modelo é executado durante as medições, de modo que carga de GPU e transferência PCIe não entram como ruído.


## 10. Recursos necessários

Máquina especificada na seção 9. Docker e Docker Compose; k6 ou Locust para os eixos A e B; TSBS para o Eixo C; `fio` para caracterização de armazenamento. PostgreSQL 17 ou superior com `pgvector`, `pg_trgm` e TimescaleDB. OpenSearch, Qdrant, RabbitMQ e InfluxDB em versões estáveis correntes, fixadas por digest. Coleta via `pg_stat_*`, `pgstattuple` e `pg_stat_statements`. Modelo de embedding de código aberto executado localmente. Repositório público no GitHub.

## 11. Aderência aos critérios de avaliação (Art. 12)

| Critério | Peso | Como a proposta atende |
|---|---|---|
| Domínio do assunto | 2,0 | Fundamentação em literatura primária; execução prática de ambos os lados sob leitura e escrita |
| Adequação metodológica | 2,0 | MLR e experimento controlado, com protocolo, repetições, controle de fatores de confusão de hardware e tratamento estatístico |
| Estruturação do texto | 3,0 | Formato SBC, com rastreabilidade entre questões de pesquisa, métricas e resultados |
| Adequação da bibliografia | 1,5 | Literatura clássica e atual, com literatura cinzenta sob protocolo reconhecido |
| Adequação aos objetivos do TCC | 1,5 | Trabalho individual, artefatos verificáveis, limitações declaradas e desenho que expõe as próprias fraquezas |

## 12. Referências preliminares

- STONEBRAKER, M.; ÇETINTEMEL, U. One Size Fits All: An Idea Whose Time Has Come and Gone. In: *ICDE*, 2005.
- STONEBRAKER, M. et al. The End of an Architectural Era (It's Time for a Complete Rewrite). In: *VLDB*, 2007.
- SADALAGE, P.; FOWLER, M. *NoSQL Distilled*. Addison-Wesley, 2012.
- KLEPPMANN, M. *Designing Data-Intensive Applications*. O'Reilly, 2017.
- RICHARDSON, C. *Microservices Patterns*. Manning, 2018.
- GAROUSI, V.; FELDERER, M.; MÄNTYLÄ, M. Guidelines for including grey literature and conducting multivocal literature reviews in software engineering. *Information and Software Technology*, v. 106, 2019.
- WOHLIN, C. et al. *Experimentation in Software Engineering*. Springer, 2012.
- COOPER, B. et al. Benchmarking cloud serving systems with YCSB. In: *SoCC*, 2010.
- THAKUR, N. et al. BEIR: A Heterogeneous Benchmark for Zero-shot Evaluation of Information Retrieval Models. In: *NeurIPS Datasets and Benchmarks*, 2021.
- MALKOV, Y.; YASHUNIN, D. Efficient and robust approximate nearest neighbor search using Hierarchical Navigable Small World graphs. *IEEE TPAMI*, 2020.

*A lista será expandida durante a execução da revisão multivocal.*
