# Decisões Arquiteturais do Experimento

Cada decisão de configuração que pode afetar o resultado está registrada aqui, com o raciocínio que a sustenta e o efeito esperado sobre a comparação.

O propósito deste documento é permitir que um avaliador verifique se a comparação é justa sem precisar ler todo o código. Onde uma decisão favorece uma das arquiteturas, isso está declarado explicitamente.

**Formato.** Cada entrada traz contexto, decisão, justificativa, efeito sobre a comparação e estado.

---

## ADR-001 — Execução em máquina única de desktop

**Contexto.** O experimento roda em computador pessoal: AMD Ryzen 7 9700X (8 núcleos, 16 threads), 32 GB DDR5, SSD NVMe PCIe 5.0 Crucial P510 de 1 TB com dissipador, GPU RTX 5070.

**Decisão.** Manter execução em máquina única, sem cluster e sem instâncias de nuvem.

**Justificativa.** Recursos disponíveis ao trabalho. A máquina é adequada à faixa de escala investigada, que é explicitamente a de aplicações de porte pequeno e médio — justamente o domínio em que a tese de consolidação é defendida.

**Efeito sobre a comparação.** Limita a validade externa. Resultados não se transferem para cenários distribuídos, que já estão fora do escopo declarado. Não favorece sistematicamente nenhuma das arquiteturas, mas comprime a faixa de carga testável.

**Estado.** Aceita.

---

## ADR-002 — Isolamento de CPU entre sistema sob teste e gerador de carga

**Contexto.** O gerador de carga roda na mesma máquina que os sistemas avaliados. Com 8 núcleos físicos, a competição por CPU é real e não é simétrica: a arquitetura poliglota mantém mais processos ativos e sofreria mais com a contenção.

**Decisão.** Fixar `cpuset` nos containers, reservando um conjunto de núcleos para o sistema sob teste e outro para o gerador de carga e a coleta de métricas. A divisão exata é registrada em `harness/orquestracao/` e mantida idêntica entre arquiteturas.

**Justificativa.** Sem isolamento, a medição capturaria contenção de escalonador em vez de desempenho do sistema. O efeito seria confundido com diferença de arquitetura.

**Efeito sobre a comparação.** Corrige um viés que operaria contra a arquitetura poliglota. Reduz o teto absoluto de vazão de ambos os lados, o que é aceitável porque o interesse é comparativo.

**Estado.** Aceita.

---

## ADR-003 — Limites explícitos de memória por container

**Contexto.** 32 GB de RAM para hospedar simultaneamente sistema operacional, sistema sob teste, gerador de carga e coleta. Vários dos sistemas avaliados dimensionam cache e heap automaticamente a partir da memória visível do host — o OpenSearch, em particular, pode reivindicar heap de JVM proporcional à RAM total da máquina.

**Decisão.** Definir `mem_limit` em todos os containers e configurar explicitamente os parâmetros de memória interna de cada sistema: `shared_buffers`, `work_mem` e `effective_cache_size` no PostgreSQL; heap da JVM no OpenSearch; limites equivalentes em Qdrant, RabbitMQ e InfluxDB. O orçamento total de memória disponível ao sistema sob teste é idêntico entre as arquiteturas comparadas em cada eixo.

**Justificativa.** Comparar um sistema que recebeu 16 GB com outro que recebeu 4 GB não mede arquitetura, mede alocação. A paridade de orçamento é condição de validade.

**Efeito sobre a comparação.** Neutro por construção. Registra-se que a arquitetura poliglota precisa dividir o mesmo orçamento entre dois serviços, enquanto a consolidada o concentra em um — essa é uma diferença real das arquiteturas, não um artefato do experimento, e será discutida como tal.

**Estado.** Aceita.

---

## ADR-004 — Caracterização prévia do armazenamento

**Contexto.** SSDs NVMe de consumo empregam cache pSLC. A escrita sustentada opera em alta velocidade enquanto o cache comporta, e cai para a velocidade de regime permanente quando ele satura. Há também risco de throttling térmico sob carga prolongada, parcialmente mitigado pelo dissipador instalado.

O Eixo C consiste precisamente em escrita sustentada por horas. Uma queda de vazão causada pelo SSD poderia ser interpretada como degradação do banco de dados.

**Decisão.** Antes de qualquer coleta, caracterizar o dispositivo com `fio`: identificar o ponto de saturação do cache pSLC, medir a vazão de escrita em regime permanente e registrar a curva de temperatura sob carga prolongada. Resultados em `ambiente/caracterizacao/`. Durante todas as execuções do Eixo C e do teste de longa duração, a temperatura do NVMe é amostrada e publicada junto com os demais dados.

**Justificativa.** É a diferença entre medir o banco e medir o disco. Sem essa caracterização, a principal contribuição do trabalho — a evidência sobre degradação temporal — ficaria sem sustentação.

**Efeito sobre a comparação.** Afeta ambas as arquiteturas do Eixo C de forma semelhante, mas precisa ser distinguido do efeito de banco de dados. O dimensionamento do conjunto de dados será escolhido de modo que a comparação principal ocorra em regime permanente, fora da faixa de cache.

**Estado.** Aceita.

---

## ADR-005 — Embeddings pré-computados fora do caminho de medição

**Contexto.** O Eixo A exige vetores de embedding. A máquina dispõe de GPU capaz de gerá-los localmente.

**Decisão.** Gerar os embeddings uma única vez, em etapa offline, e persisti-los. Ambas as arquiteturas ingerem exatamente o mesmo conjunto de vetores. Nenhum modelo é executado durante as medições.

**Justificativa.** Inferência durante o benchmark introduziria carga de GPU, transferência PCIe e variabilidade de lote como ruído. Além disso, o modelo de embedding deixa de ser fator de confusão na comparação de qualidade de recuperação.

**Efeito sobre a comparação.** Neutro e necessário. O trabalho mede armazenamento e busca vetorial, não geração de embeddings.

**Estado.** Aceita.

---

## ADR-006 — Implementação do padrão *transactional outbox* no lado do RabbitMQ

**Contexto.** Na arquitetura consolidada, enfileirar a tarefa e gravar o dado de negócio ocorrem na mesma transação do PostgreSQL, o que elimina o problema de escrita dupla por construção. A arquitetura poliglota ingênua não oferece essa garantia: uma falha entre a gravação no banco e a publicação no broker produz inconsistência.

**Decisão.** Implementar três configurações no Eixo B: fila consolidada, fila poliglota ingênua e fila poliglota com outbox. Decisão tomada em conjunto com o orientador em 25/09/2026.

**Justificativa.** Comparar a fila consolidada com a poliglota ingênua compararia uma solução correta com outra que é mais rápida porque abre mão da correção. A configuração com outbox restabelece a paridade de garantias. A configuração ingênua é mantida como referência de teto de desempenho e entra no artigo identificada como tal.

**Efeito sobre a comparação.** Corrige um viés que favoreceria a arquitetura poliglota. Introduz o risco de o processo de relay se tornar o gargalo do eixo, o que é mitigado medindo sua vazão isoladamente antes do experimento principal.

**Estado.** Aceita.

---

## ADR-007 — Remoção do particionamento nativo do Eixo C

**Contexto.** O desenho anterior previa três vias no eixo temporal: PostgreSQL com particionamento declarativo e índices BRIN, PostgreSQL com TimescaleDB, e InfluxDB.

**Decisão.** Reduzir a duas vias, mantendo PostgreSQL com TimescaleDB contra InfluxDB. Decisão do orientador em 25/09/2026.

**Justificativa.** Restrição de prazo. O TimescaleDB é a configuração que um praticante adotaria na prática para essa carga, e é a que sustenta o argumento de consolidação.

**Efeito sobre a comparação.** Altera o enunciado da tese no eixo temporal. Com o nativo fora, a afirmação deixa de ser sobre o PostgreSQL isolado e passa a ser sobre o ecossistema extensível do PostgreSQL. Perde-se a capacidade de distinguir limitação do motor de ausência de extensão adequada. Registrado nas limitações declaradas do artigo.

**Estado.** Aceita.

---

## ADR-008 — TSBS como gerador de carga do Eixo C

**Contexto.** O eixo temporal precisa de geração de dados e execução de carga comparável entre sistemas heterogêneos.

**Decisão.** Utilizar o Time Series Benchmark Suite, que suporta TimescaleDB e InfluxDB sob o mesmo conjunto gerado e o mesmo harness.

**Justificativa.** Ferramenta de terceiros reduz o risco de o gerador ser inadvertidamente ajustado a favor de um dos sistemas. Dados idênticos e conjunto de consultas idêntico em ambos os lados.

**Efeito sobre a comparação.** Registra-se que o TSBS foi originado pela empresa mantenedora do TimescaleDB. Isso é declarado no artigo. A mitigação é usar exclusivamente os cenários e consultas padrão da ferramenta, sem seleção de subconjunto favorável, e publicar os parâmetros exatos.

**Estado.** Aceita, com ressalva declarada.

---

## ADR-009 — Corpus com julgamentos de relevância

**Contexto.** O Eixo A precisa medir qualidade de recuperação, não apenas velocidade.

**Decisão.** Utilizar subconjunto de coleção pública com julgamentos de relevância disponíveis (BEIR ou MS MARCO), permitindo cálculo de recall@10 e nDCG@10.

**Justificativa.** Uma arquitetura pode ser mais rápida simplesmente por retornar resultados piores. Sem julgamentos de relevância, a comparação de latência é incompleta e potencialmente enganosa.

**Efeito sobre a comparação.** Neutro. Ambas as arquiteturas são avaliadas contra o mesmo padrão-ouro.

**Estado.** Aceita. Coleção específica a definir com o orientador.

---

## ADR-010 — Ausência de tuning customizado

**Contexto.** O autor tem mais familiaridade com PostgreSQL do que com OpenSearch, Qdrant, RabbitMQ e InfluxDB. Tuning aplicado com profundidade desigual produziria viés sistemático.

**Decisão.** Nenhum sistema recebe otimização além das recomendações publicadas por seus próprios mantenedores para o perfil de carga em questão. Todo desvio é registrado neste documento com justificativa.

**Justificativa.** É a única política que o autor consegue aplicar com honestidade dado seu nível desigual de familiaridade. Um especialista em qualquer dos sistemas provavelmente extrairia mais desempenho dele.

**Efeito sobre a comparação.** Estabelece um piso comum em vez de um teto comum. Declarado como ameaça à validade interna: os números representam o que se obtém com configuração de referência, não o máximo alcançável por especialistas.

**Estado.** Aceita.

---

## ADR-011 — Fixação de versões por digest

**Contexto.** Imagens identificadas apenas por tag mudam de conteúdo ao longo do tempo, comprometendo a reprodução.

**Decisão.** Todas as imagens de container referenciadas por digest. Versões registradas em `ambiente/README.md`.

**Justificativa.** Reprodutibilidade. Um avaliador que execute o repositório meses depois precisa obter os mesmos binários.

**Efeito sobre a comparação.** Neutro.

**Estado.** Aceita.

---

## ADR-012 — Controle de variabilidade de frequência e temperatura

**Contexto.** Processadores Ryzen ajustam frequência dinamicamente em função de temperatura e consumo. Em execuções prolongadas, a frequência sustentada difere da frequência de pico, e a ordem de execução pode introduzir deriva térmica.

**Decisão.** Aleatorizar a ordem de execução entre arquiteturas, inserir intervalo de resfriamento entre execuções, e amostrar frequência de CPU e temperaturas durante toda a coleta, publicando as séries junto com os resultados.

**Justificativa.** Sem aleatorização, a arquitetura testada em segundo lugar operaria sistematicamente sob condições térmicas piores.

**Efeito sobre a comparação.** Corrige um viés de ordem. Aumenta a duração total da coleta.

**Estado.** Aceita.

---

## ADR-013 — Restauração de estado entre execuções

**Contexto.** Execuções de escrita deixam tuplas mortas, índices inflados e caches aquecidos que contaminariam a execução seguinte.

**Decisão.** Restaurar o estado inicial dos volumes entre execuções independentes, com descarte de período de aquecimento em cada uma. A única exceção é o teste de longa duração, cujo objetivo é justamente observar a acumulação — ele roda com estado contínuo, do início ao fim.

**Justificativa.** Independência entre repetições é pressuposto dos testes estatísticos aplicados.

**Efeito sobre a comparação.** Neutro. Aumenta o tempo de execução da bateria.

**Estado.** Aceita.

---

## Registro de alterações

| Data | Alteração |
|---|---|
| 25/09/2026 | Criação do documento com ADR-001 a ADR-013 |
