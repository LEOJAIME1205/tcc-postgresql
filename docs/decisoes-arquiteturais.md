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

**Efeito sobre a comparação.** Afeta ambas as arquiteturas do Eixo C de forma semelhante, mas precisa ser distinguido do efeito de banco de dados. O critério de atribuição da revisão de 24/10/2026 não favorece nenhuma arquitetura a priori, mas penaliza a que gravar mais bytes em disco por métrica ingerida: se esse volume levar o dispositivo para além do cache, a queda é reportada como custo daquela arquitetura neste hardware, e não descontada como efeito do SSD. Qual arquitetura é essa só se saberá medindo. A restauração dos volumes também grava mais para a arquitetura de volume maior; o repouso entre restauração e execução existe para que isso não contamine a medição.

**Estado.** Aceita. Revisada em 24/10/2026.

**Revisão de 24/10/2026.** A caracterização (resultados em `ambiente/README.md`) mostrou que o cache pSLC é dinâmico, com ≈ 62 GiB com o disco 3% ocupado, e que depois dele a vazão sequencial não tem um regime único: fica entre 0,5 e 1,1 GiB/s enquanto o controlador esvazia o cache e sobe para 2,0 GiB/s em seguida. A premissa de executar fora da faixa de cache não se sustenta, porque, a taxas de escrita abaixo da capacidade de esvaziamento, o dispositivo nunca sai do cache. O controle passa a ser:

1. Taxa de escrita e latência do dispositivo, e bytes gravados por container, amostradas a cada segundo em todas as execuções do Eixo C e no teste de longa duração.
2. Execução de controle com `fio` limitado à maior taxa de escrita observada no piloto, pelo mesmo tempo do teste de longa duração.
3. Critério de atribuição, fixado antes da coleta: uma queda de vazão do banco só é atribuída ao dispositivo se coincidir com taxa de escrita sustentada próxima do piso medido para aquele padrão de escrita e se a execução de controle também degradar nessa taxa. Fora desses casos, é atribuída ao banco.
4. Bytes gravados em disco por métrica ingerida reportados como resultado, para cada arquitetura.
5. Caracterização refeita com o disco no estado de ocupação da Fase 3, com `fstrim` e repouso entre a restauração dos volumes e cada execução, com duração do repouso definida pela medição do tempo de recuperação do cache. Adotado em 25/10/2026: **20 minutos**, contados depois de `sync` e `fstrim -v /`. No teste de recuperação, o cache voltou inteiro após 15 minutos de repouso e de forma parcial após 1 e 5 minutos (`ambiente/README.md`). Os 5 minutos adicionais são margem de precaução, porque houve uma única medição por valor de repouso.

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

**Estado.** Aceita. Coleção definida em 30/10/2026, com o orientador: **NQ (Natural Questions) do BEIR**, com 2,68 milhões de passagens da Wikipédia e 3.452 consultas de teste com julgamentos de relevância (média de 1,2 documento relevante por consulta). Escolhida por três motivos: é o cenário de RAG que ancora o eixo (perguntas em linguagem natural sobre passagens); tem escala para exercitar os índices e ainda caber nos 20 GiB da ADR-025 nas duas arquiteturas, com vetores de 384 dimensões (ADR-035); e tem consultas em número suficiente para variar a carga. As alternativas maiores (MS MARCO, HotpotQA, FEVER, DBPedia) não cabem no orçamento de memória; as menores (FiQA, SciFact, NFCorpus) não exercitam os índices; a TREC-COVID tem só 50 consultas. A avaliação com 1,2 relevante por consulta é rasa, o que vale igualmente para as duas arquiteturas e é declarado.

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

## ADR-014 — Plataforma de medição em Linux nativo

**Contexto.** O ambiente de desenvolvimento habitual era WSL2, que introduz três
fatores de confusão concentrados no Eixo C e no teste de longa duração:
crescimento do disco virtual durante escrita sustentada, memória reivindicada
dinamicamente pelo hospedeiro e ausência de acesso à telemetria do NVMe.

**Decisão.** Todas as medições são executadas em Ubuntu 26.04 LTS instalado
nativamente em SSD NVMe dedicado, em dual boot com o Windows 11, cada sistema
em seu próprio dispositivo.

**Justificativa.** A contribuição central do trabalho é a evidência sobre
degradação ao longo do tempo. No WSL2, a queda de vazão causada pela expansão
do disco virtual seria indistinguível da degradação do banco de dados.

**Efeito sobre a comparação.** Elimina três ameaças à validade interna e
habilita o controle de governador de CPU, escalonador de I/O e opções de
montagem, impossíveis sob virtualização.

**Estado.** Aceita.

---

## ADR-015 — Governador de CPU em performance, com boost mantido

**Contexto.** O driver de frequência é o `amd-pstate-epp`, em modo ativo, controlado pelo `power-profiles-daemon`. No perfil padrão (`balanced`), o governador fica em `powersave` e a preferência de energia em `balance_performance`: a frequência sobe e desce conforme a carga, e o tempo de reação se soma à medida, sobretudo em cargas de latência curta e intermitente.

**Decisão.** Durante as coletas, perfil `performance` do `power-profiles-daemon`, aplicado com `powerprofilesctl launch -p performance -- <comando>`. O perfil vale só enquanto o comando roda e volta ao `balanced` ao fim. Verificado em 25/10/2026: durante o `launch`, os 16 processadores lógicos ficam com governador e preferência de energia em `performance`. O boost permanece ligado.

**Justificativa.** Remove a rampa de frequência como fonte de ruído. O boost é mantido porque desligá-lo fixaria a frequência na base, condição que não representa o uso real da máquina; a variabilidade que ele introduz é tratada pela ADR-012. Aplicar pelo `power-profiles-daemon`, e não escrevendo direto no sysfs, evita que o daemon reverta a mudança no meio de uma execução.

**Efeito sobre a comparação.** Aplicado igualmente a todas as arquiteturas. Com boost ligado, a frequência alcançada depende de quantos núcleos estão ativos e da temperatura: a arquitetura que mantiver menos núcleos ocupados ao mesmo tempo tende a obter frequências maiores. É um efeito real da arquitetura nesta máquina, não um artefato, e a série de frequência publicada (ADR-012) permite verificá-lo.

**Estado.** Aceita.

---

## ADR-016 — Swap desligado durante as coletas

**Contexto.** O sistema tem um arquivo de swap de 8 GB, ativado pelo `fstab`. Com swap disponível, o Docker permite por padrão que um container com limite de memória use em swap o mesmo tanto que tem de memória. Um container que ultrapasse seu limite seria paginado em vez de encerrado, gerando I/O no mesmo SSD em que os bancos gravam e degradando a latência de forma intermitente.

**Decisão.** Desligar o swap (`swapoff -a`) antes de cada janela de coleta. Ele volta no reinício da máquina, pelo `fstab`.

**Justificativa.** Torna efetivamente rígidos os limites de memória da ADR-003 e elimina a paginação como fator de confusão no dispositivo cuja caracterização sustenta o Eixo C (ADR-004).

**Efeito sobre a comparação.** Um sistema que exceder seu orçamento é encerrado pelo kernel em vez de ficar lento. O encerramento é registrado como falha da execução, não descartado. Penaliza a arquitetura cujo processo mais se aproximar do próprio limite. Como a arquitetura poliglota divide o orçamento entre dois serviços (ADR-003), cada serviço dela tem menos folga, e o risco tende a recair sobre ela.

**Estado.** Aceita.

---

## ADR-017 — fstrim controlado, fora da janela de coleta

**Contexto.** O ext4 está montado sem `discard`; o descarte de blocos livres acontece pelo `fstrim.timer` semanal. Sem descarte, arquivos apagados continuam ocupando blocos do ponto de vista do SSD, o que reduz o cache pSLC dinâmico (ADR-004). Um `fstrim` no meio de uma execução gera atividade no dispositivo e muda esse estado durante a medição.

**Decisão.** `fstrim.timer` parado durante as janelas de coleta. `sync` seguido de `fstrim -v /`, executados manualmente, antes de cada caracterização com `fio` e entre a restauração dos volumes e cada execução, conforme a revisão da ADR-004.

**Justificativa.** Garante que toda execução comece com o mesmo estado de blocos livres e que o descarte nunca coincida com uma medição. O `sync` é necessário porque o ext4 só devolve ao espaço livre os blocos de arquivos apagados no commit seguinte do journal; um `fstrim` logo após o `rm` não os descarta. Verificado em 25/10/2026: com um arquivo de 2 GiB, `rm` seguido de `fstrim` descartou 1,1 MiB; com `sync` no meio, 2 GiB.

**Efeito sobre a comparação.** Neutro. Sem o controle, o descarte semanal cairia sobre uma única execução de uma única arquitetura, a depender do horário.

**Estado.** Aceita.

---

## ADR-018 — Divisão de cpuset por núcleo físico

**Contexto.** O Ryzen 7 9700X tem SMT ativo: o núcleo físico N corresponde aos processadores lógicos N e N+8. Dois threads do mesmo núcleo compartilham unidades de execução e caches L1 e L2. A ADR-002 determina conjuntos disjuntos para sistema sob teste e gerador de carga, mas não diz como dividi-los.

**Decisão.** Dividir por núcleo inteiro: sistema sob teste em `0-5,8-13` (seis núcleos, doze threads); gerador de carga e coleta em `6-7,14-15` (dois núcleos, quatro threads). Mesma divisão em todas as arquiteturas e eixos.

**Justificativa.** Dividir por thread colocaria sistema e gerador em threads irmãos do mesmo núcleo, recriando a contenção que a ADR-002 pretende eliminar.

**Efeito sobre a comparação.** Neutro entre arquiteturas. Reduz o teto do sistema sob teste a seis núcleos. Permanece um compartilhamento que não pode ser isolado: o 9700X tem um único complexo de núcleos, com 32 MB de L3 e controlador de memória comuns a todos; esse resíduo é declarado. O piloto precisa confirmar que o gerador não satura seus dois núcleos, porque nesse caso o teto medido passaria a ser o do gerador.

**Estado.** Aceita.

---

## ADR-019 — Tarefas de manutenção suspensas durante as coletas

**Contexto.** O Ubuntu agenda tarefas que gravam em disco e consomem CPU sem aviso: atualização de pacotes (`apt-daily`, `apt-daily-upgrade` e `unattended-upgrades`), atualização automática de snaps, `fwupd-refresh`, `update-notifier-download`, `man-db`, `anacron`, `logrotate`, `dpkg-db-backup` e `e2scrub_all`. O protocolo exige máquina ociosa durante as coletas.

**Decisão.** Antes de cada janela de coleta, parar os timers dessas tarefas e suspender a atualização automática de snaps (`snap refresh --hold`). Os timers voltam no reinício da máquina, e a suspensão dos snaps vence no prazo indicado. Navegador e clientes de sincronização ficam fechados. O `sysstat-collect` é mantido, porque grava poucos kilobytes a cada 10 minutos e seus dados servem de registro do estado da máquina.

**Justificativa.** Uma atualização de pacotes durante a execução de uma arquitetura grava centenas de megabytes e ocupa CPU, e cairia sobre uma execução só.

**Efeito sobre a comparação.** Neutro. Elimina interferência aleatória que recairia sobre uma única execução. Como contrapartida, atualizações de segurança ficam suspensas durante as janelas, e as versões do sistema permanecem congeladas, o que também favorece a reprodutibilidade.

**Estado.** Aceita.

---

## ADR-020 — Gerência de energia do NVMe desligada durante as coletas

**Contexto.** O P510 tem a transição autônoma de estados de energia (APST) habilitada pelo kernel. Com a configuração padrão, após 100 ms sem I/O o dispositivo passa ao estado PS3, cuja saída leva 2,5 ms, e após mais 2 s passa ao PS4, cuja saída leva 40 ms (inventário em `ambiente/caracterizacao/inventario.txt`). Qualquer pausa acima de 100 ms numa carga faz a operação seguinte pagar esse tempo de despertar, que aparece como latência do banco sem ser.

**Decisão.** Desligar o APST antes de cada janela de coleta e religá-lo ao fim, pelo controle de tolerância de latência do kernel: `echo 0 > /sys/class/nvme/nvme0/power/pm_qos_latency_tolerance_us` para desligar e `echo 100000` no mesmo arquivo para restaurar o padrão. Verificado em 25/10/2026: com 0, o recurso fica desabilitado no dispositivo; com 100000, volta habilitado e com a tabela de transições refeita pelo kernel. O ASPM L1 do link PCIe e os estados ociosos da CPU (até C3, saída em 350 µs) permanecem no padrão.

**Justificativa.** O efeito do APST está na escala de milissegundos, a mesma das latências medidas, e depende do padrão de pausas de cada sistema, não do seu desempenho. É o mesmo raciocínio da ADR-015, aplicado ao armazenamento. O ASPM e os C-states ficam no padrão porque seu efeito está na escala de microssegundos, e desligar os C-states aumentaria o calor e reduziria a margem de boost (ADR-015). O controle do kernel é usado no lugar de `nvme set-feature` porque reconstrói a tabela de transições ao restaurar.

**Efeito sobre a comparação.** Remove um artefato que penalizaria a arquitetura de I/O mais intermitente, que é a que mais deixa o disco ocioso por mais de 100 ms. Qual arquitetura é essa varia por eixo e não foi medido. Os números representam o dispositivo sempre desperto, condição de servidor e não de desktop; o consumo de energia em repouso fica maior durante as janelas.

**Estado.** Aceita.

---

## ADR-021 — Coleta de métricas internas com a mesma frequência em todas as arquiteturas

**Contexto.** Os coletores do harness rodam nos núcleos do gerador de carga (ADR-018), mas as consultas de métricas internas são executadas dentro do sistema sob teste e consomem CPU, memória e I/O dele. Se o PostgreSQL for consultado com mais frequência, ou com sondas mais pesadas, que os sistemas especializados, o custo da observação recai mais sobre a arquitetura consolidada. Leituras de telemetria do kernel (`/proc`, `/sys`, cgroup) não passam pelos sistemas sob teste.

**Decisão.**

1. Telemetria do kernel, de host e containers: a cada 1 s.
2. Estatísticas internas de todos os sistemas de dados (PostgreSQL, TimescaleDB, OpenSearch, Qdrant, RabbitMQ e InfluxDB): a cada 10 s, com uma conexão dedicada e somente leitura por sistema, fora do pool do gerador.
3. Tamanhos em disco de tabelas, índices, coleções e filas: a cada 60 s, em todos os sistemas.
4. `pgstattuple_approx`: a cada 5 min, só no teste de longa duração.
5. `pg_stat_statements` habilitado via `shared_preload_libraries` e lido só ao fim de cada execução.
6. No piloto (Etapa 2.5), medir o custo da coleta: a mesma execução com o protocolo completo e sem coleta interna nem `pg_stat_statements`. Se a diferença de vazão ou de latência superar a variação entre repetições, os intervalos são revistos e esta ADR é atualizada.

**Justificativa.** Frequência igual torna o custo da observação comparável entre arquiteturas. Dez segundos bastam para as tendências que interessam, como tuplas mortas, WAL e autovacuum, que evoluem em minutos; o intervalo de 1 s fica reservado ao que é lido do kernel, sem custo para os sistemas sob teste. O `pgstattuple_approx` percorre páginas da tabela e é a sonda mais cara, por isso fica restrito ao cenário em que a inflação é a pergunta. O `pg_stat_statements` é a forma usual de diagnosticar consultas no PostgreSQL e é amplamente usado em operação.

**Efeito sobre a comparação.** Desfavorece o PostgreSQL em pequena medida: ele recebe duas sondas sem equivalente do outro lado, o `pgstattuple_approx` no teste de longa duração e o custo contínuo do `pg_stat_statements`. No Eixo B, as três configurações gravam o dado de negócio no PostgreSQL, então essas sondas valem para todas elas. O custo total é medido no piloto e reportado.

**Estado.** Aceita.

---

## ADR-022 — Imagem única do PostgreSQL 18 para os três eixos

**Contexto.** Os três eixos usam PostgreSQL: o A com `pgvector` e `pg_trgm`, o B sem extensões de dados, o C com TimescaleDB. O PostgreSQL 18 é a versão estável atual; o 19 está em *release candidate*, com lançamento previsto para 19/11/2026. As imagens prontas diferem entre si de forma relevante. A `timescale/timescaledb` é baseada em Alpine, com a biblioteca C `musl` em vez da `glibc`, e roda o `timescaledb-tune` automaticamente na primeira inicialização; o script lê a CPU da cota do container e ignora o `cpuset` da ADR-018. A `timescale/timescaledb-ha` é feita para Kubernetes, com Patroni e várias ferramentas que o trabalho não usa. O pacote do TimescaleDB nos repositórios Debian e PGDG é a edição Apache, que não inclui a compressão medida no Eixo C.

**Decisão.** Uma imagem construída pelo projeto e usada nos três eixos, com Dockerfile em `harness/orquestracao/postgres/`:

- base oficial `postgres:18.6-trixie` (Debian 13, `glibc`), fixada por digest;
- `postgresql-18-pgvector` 0.8.7, do repositório PGDG que a imagem oficial já configura;
- `timescaledb-2-postgresql-18` 2.31.0 e `timescaledb-tools` 0.19.0, do repositório oficial da Timescale (edição Community, com compressão), com a chave do repositório conferida por hash;
- versões de todos os pacotes fixadas.

Cada eixo ativa apenas o que usa: `timescaledb` em `shared_preload_libraries` só no Eixo C; `pg_stat_statements` em todos (ADR-021). O digest da imagem construída é registrado em `ambiente/README.md` (ADR-011).

**Justificativa.** É a única opção avaliada que garante o mesmo binário do PostgreSQL, com a mesma biblioteca C, em todos os eixos, e em que nenhum parâmetro é aplicado sem decisão explícita, condição das ADR-003 e ADR-010. Corresponde também ao enunciado da tese: um único PostgreSQL com extensões.

**Efeito sobre a comparação.** Neutro. Remove diferenças de biblioteca C e de compilação entre os eixos e não favorece nenhuma das arquiteturas. O custo é manter o Dockerfile; a reconstrução depende de os repositórios PGDG e da Timescale manterem as versões fixadas, e o digest da imagem construída permanece como referência do que foi medido.

**Estado.** Aceita.

---

## ADR-023 — Telemetria dos fabricantes desligada em todos os sistemas

**Contexto.** TimescaleDB, Qdrant e InfluxDB vêm com telemetria ligada por padrão e enviam periodicamente estatísticas de uso aos fabricantes pela rede. Durante as medições, isso é atividade de rede e de CPU dentro do sistema sob teste que não pertence à carga, em intensidade diferente para cada sistema, além de enviar a terceiros dados do ambiente experimental.

**Decisão.** Desligar a telemetria em todos os sistemas que a tenham:

- TimescaleDB: `timescaledb.telemetry_level = off`, que também desliga a verificação de versão;
- Qdrant: `QDRANT__TELEMETRY_DISABLED=true`;
- InfluxDB: opção equivalente da versão adotada, a confirmar na Etapa 2.4;
- OpenSearch, RabbitMQ e PostgreSQL: verificados na etapa de cada eixo; nenhuma telemetria conhecida até aqui.

As métricas locais de cada sistema, como o `/metrics` do Qdrant, continuam disponíveis para a coleta da ADR-021.

**Justificativa.** Remove atividade que não pertence à carga medida e evita o envio de dados. Aplicar a todos mantém a simetria.

**Efeito sobre a comparação.** Neutro, aplicado a todos os sistemas.

**Estado.** Aceita.

---

## ADR-024 — `timescaledb-tune` executado uma vez, com recursos explícitos, e saída congelada

**Contexto.** A instalação oficial do TimescaleDB recomenda rodar o `timescaledb-tune`, que ajusta memória, paralelismo, WAL, processos de fundo e outros parâmetros do PostgreSQL para cargas de séries temporais. Sem ele, o PostgreSQL do Eixo C ficaria com os padrões de fábrica (`shared_buffers` de 128 MB) e sem os ajustes próprios do TimescaleDB, como processos de fundo para as rotinas de compressão e limite de locks para hypertables com muitos chunks. A detecção automática de recursos da ferramenta lê a CPU da cota do container e ignora o `cpuset` da ADR-018, e seu resultado depende da versão da ferramenta.

**Decisão.**

1. Executar o `timescaledb-tune` uma única vez, com `--dry-run`, a partir da imagem da ADR-022, informando explicitamente a memória do container do Eixo C (o orçamento da ADR-003) e `--cpus=12` (ADR-018).
2. Revisar cada parâmetro recomendado e congelar os valores num arquivo de configuração versionado do Eixo C. A lista completa de parâmetros e valores é acrescentada a esta ADR.
3. Nunca deixar a ferramenta rodar automaticamente na inicialização.
4. O InfluxDB recebe tratamento simétrico: a configuração recomendada por seu fabricante para cargas de ingestão, com o mesmo orçamento de memória, documentada da mesma forma.

**Justificativa.** Segue a recomendação publicada pelo mantenedor (ADR-010) sem herdar a leitura errada de CPU da detecção automática. Congelar a saída torna a configuração auditável e reprodutível, independente da versão futura da ferramenta.

**Efeito sobre a comparação.** Em relação aos padrões de fábrica, favorece o lado consolidado do Eixo C, de forma legítima pela ADR-010 e compensada pelo tratamento simétrico do InfluxDB. O PostgreSQL do Eixo C fica com configuração mais completa que o dos Eixos A e B, que não têm ferramenta equivalente e seguem a documentação do PostgreSQL para memória (ADR-003). Como as comparações são feitas dentro de cada eixo, a diferença entre eixos é declarada, e não corrigida.

**Estado.** Aceita. A execução depende da definição do orçamento de memória dos containers.

---

## ADR-025 — Orçamento de memória de 20 GiB para o sistema sob teste

**Contexto.** A ADR-003 exige limites explícitos de memória e orçamento idêntico entre as arquiteturas de cada eixo, mas não fixa o valor. A máquina tem 30,0 GiB visíveis. Sem as ferramentas de desenvolvimento abertas, o desktop, o Docker e o kernel usam de 1,5 a 2 GiB. O kernel cobra de cada container o cache de arquivo que ele usa (verificado em 27/10/2026, ver `harness/README.md`), e todo banco enche esse cache até o limite. Por isso o sistema sob teste ocupa o orçamento inteiro durante toda a execução, e o resto da máquina vive só no que sobra, sem swap (ADR-016). Se o resto ultrapassar essa sobra, o kernel recupera memória de todos os processos, inclusive o cache do banco, ou encerra algum deles.

**Decisão.**

1. Sistema sob teste: 20 GiB, a soma dos limites (`memory.max`) de todos os seus containers. A arquitetura consolidada concentra o valor num serviço; a poliglota o divide entre os seus, inclusive processos auxiliares como o relay do outbox, com a divisão definida na etapa de cada eixo.
2. O mesmo orçamento nos três eixos.
3. Gerador de carga: limite próprio de 4 GiB. A coleta usa poucos megabytes e roda no host.
4. Os 6 GiB restantes ficam para o sistema, o desktop, o Docker e como margem.
5. O limite do gerador é revisto no piloto (Etapa 2.5), com o uso real de memória medido.

**Justificativa.** Vinte GiB aproveitam dois terços da máquina e deixam margem para o sistema sob teste cheio de cache, o gerador e imprevistos, sem que a falta de swap provoque recuperação de memória sobre o banco. O limite do gerador impede que um gerador descontrolado tire memória do sistema sob teste. O mesmo valor nos três eixos simplifica a configuração e a estimativa de custo em nuvem.

**Efeito sobre a comparação.** Neutro entre as arquiteturas de cada eixo, por construção. Registra-se que a poliglota divide o orçamento (ADR-003). O valor define o regime medido junto com o tamanho dos dados: dados menores que 20 GiB colocam o sistema em regime de memória. A relação entre dados e memória é decidida na etapa de cada eixo. Na estimativa de custo em nuvem, 20 GiB não corresponde a um tamanho comum de instância, e a equivalência adotada é declarada no artigo.

**Estado.** Aceita.

---

## ADR-026 — Rede interna do Docker para todos os containers, sem portas publicadas

**Contexto.** O gerador de carga precisa alcançar os sistemas sob teste, e na arquitetura poliglota os serviços conversam entre si. Um gerador fora dos containers, acessando portas publicadas no host, passaria por uma camada de tradução de endereços do Docker com custo próprio, que cresce com a vazão.

**Decisão.** Sistemas sob teste e gerador de carga rodam em containers do mesmo projeto do Docker Compose e se comunicam pela rede interna padrão do projeto. Nenhuma porta é publicada no host. A coleta, que roda no host, acessa os containers pelo endereço deles na rede interna.

**Justificativa.** O mesmo caminho de rede para todas as arquiteturas, sem a camada de tradução de endereços. Sem portas publicadas, os bancos também ficam inacessíveis de fora da máquina.

**Efeito sobre a comparação.** A rede interna é mais rápida que uma rede real entre máquinas, e a arquitetura poliglota faz mais saltos de rede (aplicação para dois motores de busca, relay para o broker). A medição subestima esse custo da arquitetura poliglota e, portanto, a favorece levemente.

**Estado.** Aceita.

---

## ADR-027 — Logs dos containers com rotação por tamanho

**Contexto.** O driver de log padrão do Docker (`json-file`) grava tudo o que os containers imprimem no mesmo SSD dos bancos, sem limite de tamanho.

**Decisão.** Driver `json-file` com rotação, igual para todos os containers: até 5 arquivos de 100 MB cada. Os logs de cada execução são copiados para `ferramenta/` no diretório da execução.

**Justificativa.** Limita o espaço ocupado e o volume de escrita dos logs, sem perder a trilha de auditoria de cada execução.

**Efeito sobre a comparação.** Neutro. Um sistema que registre mais logs grava mais no disco, o que é uma característica real dele.

**Estado.** Aceita.

---

## ADR-028 — `/dev/shm` de 1 GiB nos containers PostgreSQL

**Contexto.** O Docker monta 64 MB de memória compartilhada em `/dev/shm` por padrão. O PostgreSQL usa esse espaço para a memória compartilhada dinâmica das consultas paralelas, que podem falhar quando ele não basta. A documentação da imagem oficial mostra o aumento de `shm_size` na configuração com Docker Compose.

**Decisão.** `shm_size` de 1 GiB nos containers PostgreSQL de todos os eixos. A memória usada ali é cobrada do container e conta dentro dos 20 GiB da ADR-025. Os demais sistemas ficam com o padrão.

**Justificativa.** Remove uma falha que vem do ambiente Docker, não do PostgreSQL. É um ajuste de ambiente, não de desempenho.

**Efeito sobre a comparação.** Favorece o PostgreSQL em relação ao padrão do Docker, de forma legítima: sem o ajuste, ele poderia falhar por uma limitação que não existe numa instalação comum fora de containers.

**Estado.** Aceita.

---

## ADR-029 — Restauração dos volumes por cópia de um estado inicial

**Contexto.** A ADR-013 exige restaurar o estado inicial dos volumes entre execuções independentes, mas não define o mecanismo.

**Decisão.**

1. Os dados de cada arquitetura ficam em diretórios do host, fora do repositório e no P510, montados nos containers.
2. Depois da carga dos dados, com os containers parados de forma limpa, faz-se uma cópia "inicial" do diretório, guardada ao lado.
3. Antes de cada execução, com os containers parados: a cópia em uso é apagada e substituída por uma cópia da inicial, com `cp -a`, que preserva dono e permissões. Como os arquivos pertencem ao usuário do banco dentro do container, a cópia é feita por um container auxiliar, sem `sudo`.
4. Em seguida vêm `sync`, `fstrim` e o repouso de 20 minutos (ADR-004 e ADR-017).

**Justificativa.** Diretórios comuns são fáceis de copiar, conferir e medir, ao contrário dos volumes gerenciados pelo Docker. Os dados ficam em ext4 direto, sem passar pela camada de sistema de arquivos dos containers.

**Efeito sobre a comparação.** Neutro. A restauração grava mais para a arquitetura de volume maior, efeito tratado pelo repouso da ADR-004.

**Estado.** Aceita.

---

## ADR-030 — Containers sem reinício automático e com encerramento limpo

**Contexto.** Com reinício automático, um banco que caísse durante a execução, por exemplo por falta de memória, voltaria sozinho, e a falha ficaria escondida no meio dos dados. Ao parar um container, o Docker espera 10 segundos por padrão antes de forçar o encerramento, e o PostgreSQL pode precisar de mais tempo para o checkpoint de desligamento.

**Decisão.** Nenhum container reinicia automaticamente (`restart: "no"`). Os containers PostgreSQL têm 2 minutos para encerrar (`stop_grace_period`), com o sinal de desligamento rápido e limpo que a imagem oficial já define (`SIGINT`).

**Justificativa.** Uma falha durante a execução fica visível e é registrada (ADR-016). O encerramento limpo garante que a cópia inicial da ADR-029 seja feita a partir de dados consistentes.

**Efeito sobre a comparação.** Neutro.

**Estado.** Aceita.

---

## ADR-031 — Ambiente Python com versões travadas por `uv`

**Contexto.** O harness é escrito em Python e passa a depender de bibliotecas externas, a começar pelo driver do PostgreSQL usado pelo coletor. O Python 3.14.4 do Ubuntu vem sem `pip` e sem `ensurepip`, então nem o `venv` padrão funciona sem pacotes adicionais. A reprodução exige fixar a ferramenta, o interpretador e cada biblioteca, no mesmo espírito da ADR-011.

**Decisão.**

1. Ferramenta: `uv` 0.12.24, instalado pelo instalador oficial na versão fixada e exigido pelo projeto (`required-version`). É a última versão da série 0.12, escolhida em vez de uma recém-lançada.
2. Um projeto na raiz do repositório, descrito em `pyproject.toml`, marcado como aplicação e não como pacote.
3. Interpretador: Python 3.14.4, fixado em `.python-version`. Nesta máquina, o `uv` usa o do sistema; em outra, baixa a mesma versão.
4. Dependências diretas com versão exata no `pyproject.toml`. Todas as dependências, inclusive as indiretas, travadas com hash no `uv.lock`, que é versionado. O ambiente é recriado com `uv sync --locked`, que falha se o `uv.lock` estiver desatualizado.
5. Primeira dependência: `psycopg[binary]` 3.3.6, com a biblioteca cliente do PostgreSQL embutida.
6. Verificação de tipos em modo estrito configurada no próprio `pyproject.toml` (`[tool.pyright]`), para que o Pylance e qualquer avaliador usem o mesmo critério.
7. Acrescentado em 30/10/2026: `exclude-newer` no `pyproject.toml`, com uma data duas semanas anterior à resolução. O `uv` ignora qualquer pacote publicado depois dela, e a regra de não usar versões recém-lançadas passa a valer também para as dependências indiretas.
8. Acrescentado em 30/10/2026: as bibliotecas de geração de embeddings (PyTorch com CUDA e `sentence-transformers`, ADR-035), que somam alguns gigabytes, ficam num grupo de dependências separado (`embeddings`). O grupo é travado no mesmo `uv.lock`, mas só é instalado para gerar os embeddings; o harness e os geradores não o carregam.

**Justificativa.** O `uv` cria o ambiente sem depender do `pip` do sistema, gera um arquivo de travamento com hash de cada pacote e reproduz o ambiente com um único comando. Versões exatas tornam a reprodução independente de quando ela for feita.

**Efeito sobre a comparação.** Neutro. Afeta a reprodutibilidade, não os resultados. A escolha dos drivers usados pelos geradores de carga, que afeta os resultados, será decidida em ADR própria na etapa de cada eixo.

**Estado.** Aceita.

---

## ADR-032 — Ordem das execuções sorteada em blocos

**Contexto.** A ADR-012 manda aleatorizar a ordem entre arquiteturas, mas não define o esquema. Num sorteio livre de todas as execuções de uma janela, as repetições de uma arquitetura podem, por acaso, concentrar-se no início ou no fim. Uma deriva ao longo da janela, como disco mais ocupado, temperatura ambiente mais alta ou cache do SSD em outro estado, ficaria então confundida com a diferença entre arquiteturas.

**Decisão.** As execuções de uma janela são organizadas em blocos. Cada bloco contém uma repetição de cada configuração da janela (arquitetura, cenário e nível de concorrência), em ordem sorteada dentro do bloco, e os blocos rodam em sequência. A semente do sorteio é definida no plano da janela e registrada na abertura, de modo que a ordem pode ser reproduzida. Execuções do cenário de longa duração, que rodam uma vez por arquitetura, ocupam janelas próprias, e a ordem entre elas é registrada.

**Justificativa.** O sorteio em blocos é o desenho de blocos completos aleatorizados: cada arquitetura passa pelo começo, pelo meio e pelo fim da janela na mesma proporção, e a deriva temporal se distribui igualmente entre elas.

**Efeito sobre a comparação.** Neutro. Protege as duas arquiteturas de cada eixo contra um viés de posição na janela.

**Estado.** Aceita.

---

## ADR-033 — Tratamento de falhas e interrupções

**Contexto.** Durante uma janela, uma execução pode falhar: o gerador termina com erro, um container encerra, o kernel mata um processo por falta de memória. A janela também pode ser interrompida pelo operador ou por queda da máquina. Descartar falhas esconderia resultados, e repetir execuções à vontade permitiria escolher as mais favoráveis.

**Decisão.**

1. Uma execução que falha recebe o estado `falha` ou `oom` no manifesto. Os logs são salvos, a arquitetura é desligada, e a janela segue para a próxima execução. A execução falha é mantida e reportada, nunca repetida para substituí-la (ADR-016).
2. Se um controle bloqueante do protocolo falhar na conferência antes de uma execução, a janela é encerrada, porque o ambiente deixou de ser válido.
3. Uma interrupção do operador (Ctrl+C) marca a execução em curso como `abortada`, desliga a arquitetura, para os coletores e grava o registro de encerramento.
4. O registro da janela é atualizado depois de cada execução. Uma janela interrompida pode ser retomada: as execuções pendentes rodam na ordem sorteada original, e só a execução `abortada` é repetida, como nova execução. A abortada continua registrada.

**Justificativa.** Separa o que é resultado (falhas do sistema sob teste, que são dados) do que é acidente de operação (interrupções, que não dizem nada sobre as arquiteturas). Preservar a ordem sorteada na retomada mantém o desenho em blocos da ADR-032.

**Efeito sobre a comparação.** Neutro. Impede tanto o descarte de resultados desfavoráveis quanto a repetição seletiva de execuções.

**Estado.** Aceita.

---

## ADR-034 — `pg_stat_statements` zerado no início do gerador

**Contexto.** A cópia inicial dos dados (ADR-029) é feita depois da carga, e o PostgreSQL guarda as estatísticas acumuladas quando desliga de forma limpa. Sem intervenção, o `pg_stat_statements` lido ao fim de uma execução conteria também as consultas da preparação dos dados.

**Decisão.** Nas arquiteturas com PostgreSQL, o orquestrador executa `pg_stat_statements_reset()` imediatamente antes de iniciar o gerador. Ao fim da execução, o conteúdo do `pg_stat_statements` é gravado em `instantaneos/pg_stat_statements.csv`. Os contadores cumulativos lidos pelo coletor do PostgreSQL não são zerados, porque a análise usa diferenças.

**Justificativa.** O instantâneo passa a cobrir só o aquecimento e a medição, que é o que interessa para diagnosticar as consultas da carga.

**Efeito sobre a comparação.** Neutro. Afeta apenas um dado de diagnóstico do lado do PostgreSQL. As consultas do aquecimento ficam incluídas no instantâneo, o que é declarado.

**Estado.** Aceita.

---

## ADR-035 — Modelo de embeddings: bge-small-en-v1.5

**Contexto.** A ADR-005 define que os embeddings do Eixo A são gerados uma vez, offline, e compartilhados pelas duas arquiteturas, mas não escolhe o modelo. A dimensão dos vetores decide quanta memória os vetores e os índices HNSW ocupam e, com ela, se cada arquitetura cabe nos 20 GiB da ADR-025.

**Decisão.** `BAAI/bge-small-en-v1.5`: 384 dimensões, 33 milhões de parâmetros, licença MIT, média de 51,68 nas tarefas de recuperação do MTEB. Vetores normalizados, em precisão simples, sem quantização. As consultas recebem o prefixo recomendado pelo modelo ("Represent this sentence for searching relevant passages: "); as passagens, não. O texto de cada passagem é o título seguido do texto, separados por um espaço, convenção do BEIR e do MTEB. A versão exata do modelo é fixada pelo identificador de revisão do repositório.

**Justificativa.** O `bge-base-en-v1.5`, de 768 dimensões, tem média de 53,25, só 1,6 ponto acima, com o dobro de memória por vetor. Com 768 dimensões, os vetores e o índice HNSW do PostgreSQL passariam de 17 GB, e o lado consolidado sairia do regime de memória antes do poliglota. Com 384, as duas arquiteturas cabem no orçamento. Licença permissiva e execução local na GPU tornam a geração reproduzível.

**Efeito sobre a comparação.** Neutro entre as arquiteturas: os mesmos vetores alimentam o `pgvector` e o Qdrant. Os modelos BGE foram treinados com dados que incluem a NQ, o que infla o nDCG absoluto do ramo vetorial nas duas arquiteturas igualmente; isso é declarado no artigo.

**Estado.** Aceita. Embeddings gerados em 30/10/2026, revisão `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`, em fp32 na GPU. A busca vetorial exata sobre eles dá nDCG@10 de 0,5018 nas 3.452 consultas, igual ao publicado no MTEB para o modelo na NQ (50,18), o que confere o modelo, o prefixo e o texto das passagens. Arquivos e hashes em `eixo-a-busca/dados/README.md`.

---

## ADR-036 — Versões e configuração do OpenSearch e do Qdrant

**Contexto.** A arquitetura poliglota do Eixo A usa OpenSearch para o ramo léxico e Qdrant para o vetorial. A ADR-010 manda seguir as recomendações dos mantenedores, e a ADR-011, fixar as imagens por digest.

**Decisão.**

1. Versões: OpenSearch 3.8.0 e Qdrant 1.19.2, as últimas com pelo menos duas semanas de publicação em 30/10/2026, mesma regra da ADR-031. Imagens oficiais, fixadas por digest.
2. OpenSearch: nó único (`discovery.type=single-node`); `vm.max_map_count=262144` no host, exigido pela documentação; heap da JVM em metade da memória do container, deixando a outra metade para o cache de arquivos do Lucene, conforme a orientação do mantenedor (regra conferida na implementação); plugin de segurança desligado.
3. Qdrant: configuração padrão (vetores em memória, sem quantização) e telemetria desligada (ADR-023).

**Justificativa.** São as configurações de referência dos mantenedores. O plugin de segurança do OpenSearch liga TLS e autenticação; o PostgreSQL roda sem TLS na rede interna (ADR-026), e TLS de um lado só mediria criptografia, não o motor de busca. O `vm.max_map_count` é uma configuração do host exigida pelo OpenSearch e não altera o comportamento dos outros sistemas.

**Efeito sobre a comparação.** Desligar o plugin de segurança favorece o OpenSearch em relação ao seu padrão de produção, mas iguala as condições com o PostgreSQL, que também roda sem TLS. Os demais itens são neutros.

**Estado.** Aceita.

---

## ADR-037 — Divisão dos 20 GiB na arquitetura poliglota do Eixo A medida no preparo

**Contexto.** A ADR-025 dá 20 GiB ao sistema sob teste e deixa a divisão entre os serviços da arquitetura poliglota para a etapa de cada eixo. No Eixo A, o orçamento é dividido entre OpenSearch, Qdrant e o serviço de busca (ADR-039). Uma divisão arbitrária poderia deixar um dos motores fora do regime de memória sem necessidade.

**Decisão.** Depois do preparo dos dados, medir o que cada serviço ocupa de verdade (índices em disco e memória própria, `anon` + `shmem`) e dividir os 20 GiB na proporção dessa necessidade, com margem para o cache de arquivos. O serviço de busca recebe o mesmo limite nas duas arquiteturas. A divisão medida e os números que a sustentam são registrados nesta ADR antes da coleta. Estimativa inicial: cerca de 12 GiB para o OpenSearch e 8 GiB para o Qdrant.

**Justificativa.** O objetivo é que cada motor tenha o que precisa para manter seus índices em memória, como o PostgreSQL terá na arquitetura consolidada. Medir evita que a divisão favoreça um dos motores por palpite.

**Efeito sobre a comparação.** Neutro por construção. Registra-se que a arquitetura poliglota precisa repartir o orçamento entre motores (ADR-003), o que é uma característica real dela.

**Estado.** Aceita. Números a registrar depois do preparo.

---

## ADR-038 — HNSW com o mesmo grafo e esforço de busca calibrado para o mesmo recall

**Contexto.** Os padrões dos dois sistemas diferem: o `pgvector` usa m = 16, `ef_construction` = 64 e `ef_search` = 40; o Qdrant usa m = 16, `ef_construct` = 100 e, na busca, `ef` igual ao `ef_construct`. Com os padrões, o PostgreSQL buscaria com menos da metade do esforço do Qdrant: pareceria mais rápido devolvendo vizinhos piores.

**Decisão.**

1. O mesmo grafo nos dois: m = 16 e construção = 100.
2. A mesma distância (cosseno), sobre os mesmos vetores (ADR-035), sem quantização.
3. O esforço de busca de cada sistema é calibrado na Etapa 3.1: o menor `ef` que atinge recall@10 de 0,95 em relação à busca exata, medido nas consultas da NQ. As medições usam esse valor.
4. O índice é construído depois da carga dos dados, nos dois sistemas.

**Justificativa.** Comparar latência com qualidade de vizinhança igual é a prática dos benchmarks de busca aproximada. Copiar os padrões compararia esforços diferentes; copiar o mesmo `ef` nos dois ignoraria que as implementações chegam a recalls diferentes com o mesmo valor.

**Efeito sobre a comparação.** A construção com 100, em vez dos 64 padrão do `pgvector`, favorece o PostgreSQL em relação ao seu padrão: grafo melhor, construção mais lenta. A calibração por recall remove o viés de esforço nos dois sentidos. O tempo de construção dos índices é reportado.

**Estado.** Aceita.

---

## ADR-039 — Fusão por RRF num serviço de busca dentro do sistema sob teste

**Contexto.** A busca híbrida funde um ramo léxico e um vetorial. Na arquitetura consolidada, a fusão roda dentro do PostgreSQL, nos núcleos do sistema sob teste. Na poliglota, ela roda na aplicação. Se a aplicação fosse o próprio gerador de carga, o trabalho de fusão da poliglota correria nos núcleos do gerador, fora da medição.

**Decisão.**

1. Fusão por Reciprocal Rank Fusion, com k = 60: os 100 primeiros resultados de cada ramo, fundidos pelo inverso da posição; devolve os 10 primeiros. A mesma fórmula e os mesmos números nas duas arquiteturas.
2. Ramo léxico com o mesmo tratamento de texto: configuração `english` do PostgreSQL (`websearch_to_tsquery` e `ts_rank_cd`) e analisador `english` do OpenSearch (consulta `match`, BM25).
3. Ramo vetorial: os 100 vizinhos mais próximos, com os parâmetros da ADR-038.
4. Um serviço de busca, com a mesma interface HTTP nas duas arquiteturas, chamado pelo gerador. Roda dentro do sistema sob teste (cpuset da ADR-018 e dentro dos 20 GiB) e é feito da mesma forma nos dois lados. Na consolidada, faz uma única consulta SQL que inclui a fusão; na poliglota, consulta OpenSearch e Qdrant em paralelo e funde. Linguagem e framework do serviço são decididos na implementação, em ADR própria.

**Justificativa.** A RRF é o método de fusão mais usado e não tem pesos a calibrar, o que evita ajustar um lado. O mesmo tratamento de texto torna os ramos léxicos comparáveis. O serviço dentro do sistema sob teste faz o trabalho de fusão e as chamadas de rede da poliglota contarem na medição, como a fusão do PostgreSQL conta. É o "serviço único de busca híbrida" da proposta.

**Efeito sobre a comparação.** Remove uma vantagem artificial da poliglota, que de outro modo fundiria resultados fora do sistema sob teste. A função de ordenação léxica continua diferente (`ts_rank_cd` não é BM25), o que provavelmente favorece o OpenSearch em qualidade; a diferença é real, não é corrigida e é declarada. O serviço precisa não ser o gargalo; isso é verificado na calibração.

**Estado.** Aceita.

---

## ADR-040 — `pg_trgm` fora da consulta medida do Eixo A

**Contexto.** A proposta lista o `pg_trgm` entre os recursos da arquitetura consolidada do Eixo A. O `pg_trgm` mede a semelhança entre textos pela quantidade de trigramas (sequências de três caracteres) em comum. Serve para tolerar erros de digitação e variações de grafia, e não tem conhecimento da língua nem do significado. A consulta híbrida da ADR-039 tem dois ramos, léxico e vetorial, com a mesma estrutura nas duas arquiteturas.

**Decisão.** O `pg_trgm` não entra na consulta híbrida medida, nem como terceiro ramo da fusão nem como índice. A extensão continua disponível na imagem (ADR-022), e a busca aproximada por caracteres é discutida qualitativamente no artigo como recurso nativo da arquitetura consolidada.

**Justificativa.**

1. **Simetria.** Um terceiro ramo só do lado consolidado tornaria as fusões diferentes. O equivalente mais próximo no OpenSearch, a busca com tolerância a erros por distância de edição (`fuzziness`), funciona de outro modo e exigiria configurar um terceiro ramo nos dois lados, sem recomendação de mantenedor que fixe os parâmetros, contra a ADR-010.
2. **Pouco efeito no corpus.** As consultas da NQ são perguntas bem escritas, e a tolerância a erros de digitação tem pouco a acrescentar à relevância. Um ramo que não melhora a qualidade só acrescentaria custo.
3. **Custo de memória.** Um índice de trigramas sobre 2,68 milhões de passagens ocupa vários gigabytes e poderia tirar o lado consolidado do regime de memória (ADR-025) por um recurso que não é avaliado.

**Efeito sobre a comparação.** Torna a consulta simétrica: dois ramos, mesma fusão, nos dois lados. Retira do lado consolidado um recurso nativo que fica fora da medição; a afirmação do eixo passa a ser sobre busca léxica e vetorial fundidas, e a busca aproximada por caracteres fica como discussão qualitativa. Alteração em relação à proposta, comunicada ao orientador.

**Estado.** Aceita.

---

## Registro de alterações

| Data | Alteração |
|---|---|
| 25/09/2026 | Criação do documento com ADR-001 a ADR-014 |
| 24/10/2026 | ADR-004 revisada com o resultado da caracterização do SSD |
| 25/10/2026 | ADR-015 a ADR-019: governador, swap, fstrim, cpuset por núcleo físico e tarefas de manutenção |
| 25/10/2026 | ADR-004: repouso de 20 minutos (recuperação completa medida em 15, mais margem de precaução). ADR-017: `fstrim` precedido de `sync` |
| 25/10/2026 | ADR-020: APST do NVMe desligado durante as coletas |
| 25/10/2026 | ADR-021: coleta de métricas internas com a mesma frequência em todas as arquiteturas |
| 27/10/2026 | ADR-022: imagem única do PostgreSQL 18 para os três eixos. ADR-023: telemetria desligada em todos os sistemas |
| 27/10/2026 | ADR-024: `timescaledb-tune` executado uma vez, com recursos explícitos, e saída congelada |
| 27/10/2026 | ADR-025: orçamento de memória de 20 GiB para o sistema sob teste, 4 GiB para o gerador |
| 27/10/2026 | ADR-026 a ADR-030: rede interna sem portas publicadas, logs com rotação, `/dev/shm` de 1 GiB no PostgreSQL, restauração por cópia do estado inicial, sem reinício automático |
| 27/10/2026 | ADR-031: ambiente Python com versões travadas por `uv` |
| 29/10/2026 | ADR-032 a ADR-034: ordem sorteada em blocos, tratamento de falhas e interrupções, `pg_stat_statements` zerado no início do gerador |
| 30/10/2026 | ADR-009: corpus definido com o orientador (NQ do BEIR). ADR-035 a ADR-040: modelo de embeddings, OpenSearch e Qdrant, divisão da memória da poliglota, HNSW, fusão por RRF num serviço de busca, `pg_trgm` fora da consulta medida |
| 30/10/2026 | ADR-035: texto das passagens (título e texto) explicitado; embeddings gerados e conferidos (nDCG@10 da busca exata igual ao do MTEB) |

