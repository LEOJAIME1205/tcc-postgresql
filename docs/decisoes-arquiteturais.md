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

**Revisão do item 2 (12/11/2026, ADR-069, item 5): taxa e padrão do controle com `fio` na longa duração.** Aprovada pelo Léo.

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

**Revisão de 12/11/2026, com o orientador: papel da ingênua.** A configuração ingênua fica nas execuções curtas como referência de teto, identificada assim no artigo, e fica fora da longa duração (ADR-069). O PostgreSQL dela guarda só os pedidos, sem tabela de alta rotatividade, e os fenômenos que a longa duração mede (tuplas mortas, autovacuum e inflação) aparecem na `tarefas` da consolidada e na tabela de saída da *outbox*. A comparação principal (consolidada contra *outbox*) não muda.

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

**Decisão.** Desligar o APST antes de cada janela de coleta e religá-lo ao fim, pelo controle de tolerância de latência do kernel: `echo 0 > /sys/class/nvme/nvmeN/power/pm_qos_latency_tolerance_us` para desligar e `echo 100000` no mesmo arquivo para restaurar o padrão, em que `nvmeN` é o controlador do P510, achado pelo modelo (a numeração muda entre inicializações; em 03/11/2026 o P510 apareceu como `nvme1`). Verificado em 25/10/2026: com 0, o recurso fica desabilitado no dispositivo; com 100000, volta habilitado e com a tabela de transições refeita pelo kernel. O ASPM L1 do link PCIe e os estados ociosos da CPU (até C3, saída em 350 µs) permanecem no padrão.

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

**Estado.** Aceita. O item 6 é executado como define a ADR-066 (08/11/2026): sem coleta interna, o `pg_stat_statements` continua carregado, mas sem rastrear. Executado na janela `20261108-2233` (fila consolidada do Eixo B, c = 8, 3 execuções com coleta e 3 sem): nenhuma diferença de vazão, latência ou CPU superou a variação entre repetições. A maior diferença foi de 0,88 desvio padrão, no p95 do consumo. A vazão de enfileiramento diferiu em −0,06% e a CPU do PostgreSQL em −0,6%. Os intervalos ficam como estão. Análise em `harness/analise/custo_coleta.py`; tabelas em `resultados/processados/custo-coleta-20261108-2233-*.csv`.

**Revisão do item 4 (12/11/2026, ADR-069, item 3): `pgstattuple_approx` só nas execuções longas do Eixo B, e idade das transações a cada 60 s em todos os PostgreSQL da longa duração.** Aprovada pelo Léo.

**Complemento da revisão do item 4 (15/11/2026): criação da extensão `pgstattuple`.** O teste funcional da longa mostrou que nenhum esquema do Eixo B cria a extensão. Só o teste do harness com pgbench a criava, e por isso a falta não apareceu antes. Sem ela, o coletor registrava o erro a cada 5 min e não gravava as tuplas mortas da fila.

- **Correção:** o coletor do PostgreSQL executa `CREATE EXTENSION IF NOT EXISTS pgstattuple` ao conectar, só quando recebe `--pgstattuple`. Isso acontece apenas nas execuções longas do Eixo B, na consolidada e na *outbox*.
- **Alternativa descartada:** criar a extensão nos esquemas do B. Os estados iniciais mudariam depois das curtas e precisariam ser preparados de novo.
- **Efeito sobre a comparação:** nenhum mensurável. A criação é uma única alteração de catálogo no início da execução, antes do aquecimento, sem atividade em segundo plano. Ela vale igualmente para as duas arquiteturas do B na longa. O custo da sonda continua o descrito no item 4.

Aprovada pelo Léo em 15/11/2026.

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

**Estado.** Aceita. Executada em 08/11/2026 com o `timescaledb-tune` 0.19.0 da imagem da ADR-022: `--dry-run --memory=20GB --cpus=12 --pg-version=18`. Saída completa em `eixo-c-temporal/timescaledb/timescaledb-tune.txt`. Valores congelados na composição do Eixo C (ADR-063):

| Parâmetro | Valor |
|---|---|
| `shared_buffers` | 5GB |
| `effective_cache_size` | 15GB |
| `maintenance_work_mem` | 2047MB |
| `work_mem` | 13653kB |
| `timescaledb.max_background_workers` | 16 |
| `max_worker_processes` | 31 |
| `max_parallel_workers_per_gather` | 6 |
| `max_parallel_workers` | 12 |
| `wal_buffers` | 16MB |
| `min_wal_size` | 512MB |
| `default_statistics_target` | 100 |
| `random_page_cost` | 1.1 |
| `checkpoint_completion_target` | 0.9 |
| `max_locks_per_transaction` | 512 |
| `autovacuum_max_workers` | 10 |
| `autovacuum_naptime` | 10 |
| `default_toast_compression` | lz4 |
| `jit` | off |
| `effective_io_concurrency` | 256 |

A ferramenta não recomendou `max_wal_size` nem mudou os parâmetros do *background writer*.

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

**Estado.** Aceita. Item 5 revisto com o piloto e o custo da coleta (09/11/2026, 13 execuções com 8 clientes). Nenhum container teve OOM.
- **Memória própria (anônima):** no aquecimento e na medição dos Eixos A e B, e na carga do Eixo C, o gerador usou de 0,11 a 0,28 GiB. Os maiores valores vieram depois: 0,91 GiB nas consultas do InfluxDB e 1,04 GiB na ordenação do `operacoes.csv` do Eixo B, que tem buffer fixo de 1 GiB (`sort -S 1G`).
- **Pico total:** 3,55 GiB, no gerador do InfluxDB. Desses, 2,59 GiB eram cache do arquivo de dados, que o kernel recupera.

O limite de 4 GiB fica. O piloto só usou 8 clientes; o gerador com 32 é conferido na calibração.

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

**Estado.** Aceita. No Eixo A, o `shm_size` do PostgreSQL é de 9 GiB, porque a construção paralela do índice HNSW guarda o grafo nesse espaço (ADR-042).

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

**Estado.** Aceita. Revisada em 07/11/2026: a semente de cada execução, passada ao gerador, é derivada da semente do plano e da configuração sem a arquitetura (eixo, cenário, concorrência e repetição). Assim, a mesma configuração e repetição recebem a mesma semente nas arquiteturas de um eixo, e o gerador produz a mesma sequência de operações nelas (números aleatórios comuns), o que reduz a variância da comparação pareada. A ordem das execuções continua sorteada pela semente do plano. Antes, a semente incluía a arquitetura.

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

**Complemento (18/11/2026): execução concluída com o ambiente contaminado por falha externa.**

- **Contexto:** a execução longa `20261116-222128_b_consolidada_longa_c32_r01_t2800` terminou `ok`, mas a interface gráfica falhou durante a medição.
  - Às 04:06 de 17/11, o `gnome-shell` começou a falhar no KMS da placa NVIDIA. Às 04:20 ele travou e foi reiniciado.
  - A partir de 04:30, o driver da NVIDIA gravou 12,3 milhões de linhas de erro no diário até 10:47. Nas seis horas anteriores houve 4 linhas.
  - A CPU dos núcleos do sistema sob teste (`0-5,8-13`), lida do `/proc/stat` do host, passou de 0,17 para ~1,1 núcleo da 6ª à 12ª hora de medição, sem mudança na carga. No mesmo período, a CPU do contêiner do PostgreSQL, lida do cgroup, seguiu a reta das horas anteriores.
  - Às 10:00, o `systemd-logind` travou e foi morto, e as travas de suspensão caíram. Às 10:46, a conferência antes da execução seguinte encerrou a janela pelo item 2.
  - Os itens 1 a 4 não cobrem esse caso: a execução não falhou nem foi interrompida, mas o ambiente deixou de ser válido no meio da medição.
- **Decisão:**
  1. Uma execução concluída é tratada como `abortada` quando há evidência objetiva, registrada fora do sistema sob teste, de uma falha da máquina que não pertence às arquiteturas comparadas. Valem o diário do sistema, a CPU dos núcleos do sistema sob teste sem relação com a carga e as travas do protocolo. O resultado da execução não entra no critério.
  2. O manifesto recebe o estado `abortada`, com o motivo, a evidência e o estado original (`ok`). A execução continua registrada e é reportada.
  3. A janela é retomada pelo item 4: a execução abortada é repetida como nova execução, na posição original da ordem sorteada.
  4. Para não depender só da conferência entre execuções, um vigia lê o diário do kernel a cada 5 min durante as janelas. Se os erros da NVIDIA voltarem, ele interrompe a execução em curso com um único SIGINT, como o operador faria pelo item 3.
- **Justificativa:** a falha é da máquina, não das arquiteturas. Manter a execução misturaria ~7 h de interferência a uma medição de 12 h, e descartá-la sem registro esconderia o incidente. O critério depende só de registros do sistema, nunca do valor medido, o que impede o uso do complemento para repetir execuções desfavoráveis.
- **Efeito sobre a comparação:**
  - Neutro por desenho, porque o critério não olha o resultado.
  - Neste caso, a execução repetida é a da fila consolidada no PostgreSQL. A versão contaminada favoreceria a *outbox*, porque a interferência caiu só sobre a consolidada.
  - As ~5,5 h iniciais limpas da execução contaminada podem ser mostradas como dado complementar, nunca no lugar da repetição.
- **Estado:** aprovado pelo Léo em 18/11/2026.

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

**Estado.** Aceita. Conferido em 03/11/2026: o Ubuntu 26.04 já vem com `vm.max_map_count` = 1.048.576, acima do exigido, e o host não precisa de ajuste. Imagens fixadas pelo digest do repositório: `opensearchproject/opensearch:3.8.0@sha256:fafe3fc3587088674669235575aa166228c48bdb940294a8cdbbc1da75236a40` (publicada em 15/09/2026) e `qdrant/qdrant:v1.19.2@sha256:b7b0444c4c351c970b98e90a6f89c2ee4287c65b44e52b4cb503fa5b2aa927ad` (05/10/2026). Padrões do Qdrant 1.19.2 conferidos no arquivo de configuração oficial: m = 16, `ef_construct` = 100, número de segmentos e threads de indexação escolhidos pelos núcleos disponíveis.

---

## ADR-037 — Divisão dos 20 GiB na arquitetura poliglota do Eixo A medida no preparo

**Contexto.** A ADR-025 dá 20 GiB ao sistema sob teste e deixa a divisão entre os serviços da arquitetura poliglota para a etapa de cada eixo. No Eixo A, o orçamento é dividido entre OpenSearch, Qdrant e o serviço de busca (ADR-039). Uma divisão arbitrária poderia deixar um dos motores fora do regime de memória sem necessidade.

**Decisão.** Depois do preparo dos dados, medir o que cada serviço ocupa de verdade (índices em disco e memória própria, `anon` + `shmem`) e dividir os 20 GiB na proporção dessa necessidade, com margem para o cache de arquivos. O serviço de busca recebe o mesmo limite nas duas arquiteturas. A divisão medida e os números que a sustentam são registrados nesta ADR antes da coleta. Estimativa inicial: cerca de 12 GiB para o OpenSearch e 8 GiB para o Qdrant.

**Justificativa.** O objetivo é que cada motor tenha o que precisa para manter seus índices em memória, como o PostgreSQL terá na arquitetura consolidada. Medir evita que a divisão favoreça um dos motores por palpite.

**Efeito sobre a comparação.** Neutro por construção. Registra-se que a arquitetura poliglota precisa repartir o orçamento entre motores (ADR-003), o que é uma característica real dela.

**Estado.** Aceita. Medido na carga de desenvolvimento de 03/11/2026, com 12 GiB para o OpenSearch (heap de 6 GiB) e 8 GiB para o Qdrant, depois do estado pronto:

| Motor | Dados em disco | Memória própria (`anon` + `shmem`) | Cache de arquivo | Pico na carga |
|---|---|---|---|---|
| OpenSearch | 1,6 GiB (índice de 1,18 GiB, 5 segmentos) | 6,8 GiB (heap de 6 GiB travado e ~0,8 GiB fora do heap) | 0,4 GiB | 8,8 GiB |
| Qdrant | 4,1 GiB (6 segmentos, todos indexados) | 0,24 GiB | 3,5 GiB | 6,7 GiB |

O Qdrant mantém vetores e grafos em arquivos mapeados, contados pelo kernel como cache de arquivo; por isso a necessidade de memória de cada motor é a memória própria somada aos arquivos que ele lê nas consultas, e não só `anon` + `shmem`. O heap do OpenSearch é metade do container (ADR-036), então sua memória própria cresce com o limite dado e não mede necessidade: o mínimo coerente é o container em que a outra metade comporta o que fica fora do heap e o índice. A divisão final depende da memória do serviço de busca (ADR-039), que sai dos mesmos 20 GiB, e é registrada aqui quando ele for definido.

Divisão definida em 03/11/2026, com o serviço de busca em 1 GiB nas duas arquiteturas (ADR-046): OpenSearch com 10 GiB (heap de 5 GiB) e Qdrant com 9 GiB. Os mínimos medidos são de cerca de 4,8 GiB para o OpenSearch (com o heap na metade, a outra metade precisa comportar os ~0,8 GiB fora do heap e o índice de 1,6 GiB) e de cerca de 4,4 GiB para o Qdrant (4,1 GiB de arquivos e 0,24 GiB próprios); os 19 GiB foram divididos nessa proporção (52% e 48%), e cada motor fica com mais que o dobro do seu mínimo. Na consolidada, o PostgreSQL fica com 19 GiB (ADR-042).

---

## ADR-038 — HNSW com o mesmo grafo e esforço de busca calibrado para o mesmo recall

**Contexto.** Os padrões dos dois sistemas diferem: o `pgvector` usa m = 16, `ef_construction` = 64 e `ef_search` = 40; o Qdrant usa m = 16, `ef_construct` = 100 e, na busca, `ef` igual ao `ef_construct`. Com os padrões, o PostgreSQL buscaria com menos da metade do esforço do Qdrant: pareceria mais rápido devolvendo vizinhos piores.

**Decisão.**

1. O mesmo grafo nos dois: m = 16 e construção = 100.
2. A mesma medida de semelhança, sobre os mesmos vetores (ADR-035), sem quantização. Como os vetores têm norma 1, cosseno e produto interno dão a mesma ordem, e cada sistema usa a forma que seu mantenedor recomenda para vetores normalizados: produto interno no `pgvector` (`vector_ip_ops`, operador `<#>`), como manda o README do projeto; cosseno no Qdrant, que o implementa como produto interno sobre vetores normalizados.
3. O esforço de busca de cada sistema é calibrado na Etapa 3.1: o menor `ef` que atinge recall@100 de 0,95 em relação aos 100 vizinhos exatos, medido nas consultas da NQ, com piso de 100 nos dois sistemas. O recall@100 mede a lista que de fato entra na fusão (ADR-039). O piso existe porque o `pgvector` devolve no máximo `ef_search` linhas: com `ef` menor que 100, o ramo devolveria menos de 100 vizinhos. As medições usam o valor calibrado.
4. O índice é construído depois da carga dos dados, nos dois sistemas.

**Justificativa.** Comparar latência com qualidade de vizinhança igual é a prática dos benchmarks de busca aproximada. Copiar os padrões compararia esforços diferentes; copiar o mesmo `ef` nos dois ignoraria que as implementações chegam a recalls diferentes com o mesmo valor.

**Efeito sobre a comparação.** A construção com 100, em vez dos 64 padrão do `pgvector`, favorece o PostgreSQL em relação ao seu padrão: grafo melhor, construção mais lenta. O produto interno também o favorece em relação ao cosseno do `pgvector`, que calcula as normas a cada comparação, e iguala o cálculo ao do Qdrant. A calibração por recall remove o viés de esforço nos dois sentidos. O tempo de construção dos índices é reportado.

**Estado.** Aceita. Revisada em 30/10/2026: produto interno no `pgvector`, e calibração pelo recall@100 com piso de 100 (antes: cosseno nos dois e recall@10).

**Calibração (09/11/2026, ADR-067, item 2).**
- **Valores escolhidos:**
  - `pgvector` 0.8.7: `hnsw.ef_search` = 260, com recall@100 médio de 0,9506; 270 e 280 também passam.
  - Qdrant 1.19.2: `hnsw_ef` = 120, com 0,9531; 130 e 140 também passam.
- **Valores no padrão de busca:** com `ef` = 100, os recalls eram de 0,883 e 0,944.
- **Vizinhos exatos:** conferidos em quatro consultas contra a busca exata do próprio PostgreSQL, sem índice. As listas são idênticas.
- **Consultas com recall perto de zero no `pgvector`:** com `ef` até 230, algumas consultas têm recall perto de zero, porque a busca no grafo termina numa região sem nenhum dos vizinhos exatos. Com `ef` = 100, as 100 respostas da consulta 442 ficam todas abaixo do centésimo vizinho exato. Com 260, o pior recall é 0,31.
- **Por que o Qdrant atinge o alvo com `ef` menor:** a coleção fica em 6 segmentos, e cada um é buscado com o `ef` pedido.

O `ef` maior no `pgvector` encarece a busca vetorial da consolidada em relação ao padrão (40). É o custo de devolver vizinhos da mesma qualidade. Registros em `resultados/calibracao/ef-consolidada.json` e `ef-poliglota.json`; vizinhos exatos em `eixo-a-busca/dados/vizinhos-exatos.npy`, com hash em `vizinhos-exatos.json`.

---

## ADR-039 — Fusão por RRF num serviço de busca dentro do sistema sob teste

**Contexto.** A busca híbrida funde um ramo léxico e um vetorial. Na arquitetura consolidada, a fusão roda dentro do PostgreSQL, nos núcleos do sistema sob teste. Na poliglota, ela roda na aplicação. Se a aplicação fosse o próprio gerador de carga, o trabalho de fusão da poliglota correria nos núcleos do gerador, fora da medição.

**Decisão.**

1. Fusão por Reciprocal Rank Fusion, com k = 60: os 100 primeiros resultados de cada ramo, fundidos pelo inverso da posição; devolve os 10 primeiros, só identificadores e pontuações (ADR-041). A mesma fórmula e os mesmos números nas duas arquiteturas.
2. Ramo léxico com a mesma semântica e o mesmo tratamento de texto: basta um termo da consulta para o documento entrar (OU), sobre o mesmo texto (título e texto, ADR-041). No PostgreSQL, configuração `english`, termos do `plainto_tsquery` ligados por OU e ordenação por `ts_rank_cd`; no OpenSearch, analisador `english` e consulta `match` com o operador padrão (OU), ordenação por BM25.
3. Ramo vetorial: os 100 vizinhos mais próximos, com os parâmetros da ADR-038.
4. Um serviço de busca, com a mesma interface HTTP nas duas arquiteturas, chamado pelo gerador. Roda dentro do sistema sob teste (cpuset da ADR-018 e dentro dos 20 GiB) e é feito da mesma forma nos dois lados. Na consolidada, faz uma única consulta SQL que inclui a fusão; na poliglota, consulta OpenSearch e Qdrant em paralelo e funde. Linguagem e framework do serviço são decididos na implementação, em ADR própria.

**Justificativa.** A RRF é o método de fusão mais usado e não tem pesos a calibrar, o que evita ajustar um lado. O mesmo tratamento de texto torna os ramos léxicos comparáveis. A semântica OU é a do `match` padrão do OpenSearch e a das linhas de base BM25 do BEIR; o `websearch_to_tsquery`, previsto na primeira versão desta ADR, exige todos os termos e compararia semânticas diferentes, com o lado E achando muito menos documentos nas perguntas da NQ. O serviço dentro do sistema sob teste faz o trabalho de fusão e as chamadas de rede da poliglota contarem na medição, como a fusão do PostgreSQL conta. É o "serviço único de busca híbrida" da proposta.

**Efeito sobre a comparação.** Remove uma vantagem artificial da poliglota, que de outro modo fundiria resultados fora do sistema sob teste. A função de ordenação léxica continua diferente (`ts_rank_cd` não é BM25), o que provavelmente favorece o OpenSearch em qualidade; a diferença é real, não é corrigida e é declarada. Com a semântica OU, o PostgreSQL calcula o `ts_rank_cd` de todo documento que contém algum termo da consulta, porque o índice GIN não ordena por relevância, enquanto o OpenSearch descarta cedo os documentos que não podem ficar entre os melhores. Isso favorece a poliglota em latência; é uma diferença estrutural da busca textual nativa, medida e não corrigida. O tratamento de texto também não é idêntico: o `english` do PostgreSQL descarta 127 palavras vazias, e o do OpenSearch, as 33 do Lucene, mantendo termos como "who", "what" e "over" (conferido em 03/11/2026: na pergunta "what emperor took over france after the reign of terror", 5 termos no PostgreSQL e 8 no OpenSearch); os radicalizadores também diferem. Igualar exigiria personalizar um dos lados (ADR-010), e a diferença é declarada. O serviço precisa não ser o gargalo; isso é verificado na calibração.

**Estado.** Aceita. Revisada em 30/10/2026: ramo léxico com semântica OU nos dois lados (antes: `websearch_to_tsquery`, que exige todos os termos) e resposta só com identificadores e pontuações.

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

## ADR-041 — Esquema, identificadores e resposta do serviço no Eixo A

**Contexto.** As duas arquiteturas precisam guardar as mesmas passagens e devolver resultados comparáveis entre si e com os julgamentos da NQ. O corpus identifica as passagens por texto (`doc0` a `doc2681467`); o Qdrant aceita como identificador de ponto só inteiros sem sinal ou UUID.

**Decisão.**

1. Identificador inteiro: o número *N* de `docN`, que é também a posição da passagem no corpus (conferido nas 2.681.468 linhas). É o `id` no PostgreSQL, o identificador do ponto no Qdrant e o `_id` no OpenSearch. O gerador converte de volta para `docN` ao comparar com os julgamentos.
2. Texto indexado no ramo léxico: título e texto separados por um espaço, num único campo, sem pesos, o mesmo texto dos embeddings (ADR-035).
3. PostgreSQL: tabela `passagens (id integer PRIMARY KEY, titulo text, texto text, lexico tsvector, embedding vector(384))`, com `lexico` como coluna gerada e armazenada, `to_tsvector('english', titulo || ' ' || texto)`, como recomenda a documentação do PostgreSQL (seção 12.2.2); índice GIN em `lexico` e HNSW em `embedding` (ADR-038). Armazenamento das colunas no padrão.
4. Onde o texto fica guardado: no PostgreSQL, nas colunas da tabela; no OpenSearch, no `_source`, ligado por padrão; no Qdrant, nada além do vetor e do identificador.
5. O serviço de busca devolve os 10 primeiros como identificadores e pontuações da fusão, sem o texto das passagens.

**Justificativa.** O identificador inteiro é aceito pelos três sistemas sem tabela de tradução. A coluna gerada calcula o `tsvector` uma vez, na ingestão, como o OpenSearch analisa o texto ao indexar; a alternativa do exemplo do `pgvector`, um índice sobre expressão, refaria o `to_tsvector` de cada documento ordenado durante a consulta. Guardar o texto segue o padrão de cada sistema: o `_source` do OpenSearch vem ligado, e uma tabela de passagens guarda as passagens. Devolver só identificadores restringe a medição à recuperação e à fusão, que são o objeto do eixo.

**Efeito sobre a comparação.** Os itens 1 a 4 são neutros: mesmo texto, mesmos identificadores, cada sistema com o armazenamento padrão; o texto guardado conta no disco e na memória do PostgreSQL e do OpenSearch. O item 5 favorece a poliglota: se o serviço devolvesse o texto, os resultados vindos só do Qdrant exigiriam uma segunda consulta ao OpenSearch, ou o texto duplicado no Qdrant, enquanto o PostgreSQL o devolveria na mesma consulta. Declarado no artigo.

**Estado.** Aceita.

---

## ADR-042 — Configuração do PostgreSQL no Eixo A

**Contexto.** A ADR-003 manda configurar explicitamente `shared_buffers`, `work_mem` e `effective_cache_size`, e a ADR-024 deixa os Eixos A e B com a documentação do PostgreSQL, sem ferramenta de ajuste. O README do `pgvector` recomenda, para construir índices HNSW, aumentar o `maintenance_work_mem` até o grafo caber na memória e aumentar os processos paralelos de manutenção, que são 2 por padrão. A construção paralela guarda o grafo inteiro em memória compartilhada dinâmica, que no container fica em `/dev/shm` (conferido no código do `pgvector` 0.8.7). O Qdrant e o OpenSearch constroem seus índices com todos os núcleos que enxergam.

**Decisão.**

1. Servidor: `shared_buffers` = 5 GB (25% dos 20 GiB, valor inicial indicado pela documentação do PostgreSQL); `effective_cache_size` = 15 GB (o restante do orçamento); `work_mem` = 4 MB (o padrão, declarado); `max_worker_processes` = 16 (o padrão, 8, não comporta 11 processos paralelos); `shared_preload_libraries` = `pg_stat_statements` (ADR-021).
2. Só na sessão que constrói os índices: `maintenance_work_mem` = 8 GB (valor do exemplo do README do `pgvector`; o grafo estimado ocupa uns 6 GB); `max_parallel_maintenance_workers` = 11; `max_parallel_workers` = 12. O `pgvector` usa exatamente esse número de processos na construção do HNSW, que com o processo líder somam os 12 núcleos do sistema sob teste (conferido no código do `pgvector` 0.8.7 e numa construção de teste com 300 mil passagens). No índice GIN, o próprio PostgreSQL escolhe quantos processos usar pelo tamanho da tabela, até esse teto; o número não é forçado.
3. `shm_size` de 9 GiB no container do PostgreSQL do Eixo A: os 8 GB do grafo e 1 GiB de margem (revisão da ADR-028 para este eixo).
4. Os demais parâmetros ficam no padrão, inclusive o `hnsw.ef_search`, até a calibração (ADR-038).

**Justificativa.** São os valores da documentação do PostgreSQL e do mantenedor do `pgvector` (ADR-010). Os parâmetros de construção valem só na sessão de preparo porque o `autovacuum_work_mem` padrão herda o `maintenance_work_mem`, e 8 GB por processo de autovacuum não cabem no orçamento; assim, nada muda durante as medições. Sem o `/dev/shm` maior, a construção paralela falharia por um limite do Docker. Com os 2 processos padrão, o tempo de construção compararia esforços diferentes, já que os motores da poliglota usam todos os núcleos.

**Efeito sobre a comparação.** Os parâmetros de construção favorecem o PostgreSQL em relação ao seu padrão: índice construído em paralelo e com o grafo em memória. Igualam o esforço de construção ao dos motores da poliglota, e o tempo de construção é reportado nessas condições. Os parâmetros de memória do servidor são os da documentação e valem só no Eixo A. O limite do `/dev/shm` é um teto: o que se usa ali conta dentro dos 20 GiB e fica vazio durante as consultas.

**Estado.** Aceita. Conferida na carga de desenvolvimento de 03/11/2026, com as 2,68 milhões de passagens: o grafo coube nos 8 GB (nenhum aviso do `pgvector`); o HNSW usou 11 processos paralelos e o GIN, 6, escolhidos pelo PostgreSQL; o pico de memória do container foi de 19,0 GiB, sem atingir o limite.

Revisada em 03/11/2026: o serviço de busca ocupa 1 GiB dos 20 GiB (ADR-046), e o container do PostgreSQL passa a ter 19 GiB. Pela mesma regra do item 1, `shared_buffers` = 4864 MB (25% de 19 GiB) e `effective_cache_size` = 14592 MB (o restante). Os parâmetros de construção, o `max_worker_processes` e o `/dev/shm` de 9 GiB não mudam.

---

## ADR-043 — Carga dos dados do Eixo A no PostgreSQL

**Contexto.** As 2,68 milhões de passagens e seus vetores (ADR-035) precisam entrar no PostgreSQL. A documentação do PostgreSQL (seção 14.4) e o README do `pgvector` recomendam `COPY` para carga em massa e índices criados depois da carga; o `pgvector` indica o `COPY` binário, com o cliente Python do próprio projeto. O tempo de construção dos índices é métrica do eixo.

**Decisão.**

1. Carga por `COPY passagens (id, titulo, texto, embedding) FROM STDIN (FORMAT BINARY)`, com o `pgvector` para Python 0.5.1 (cliente do mantenedor, publicado em 09/10/2026, dentro do `exclude-newer` da ADR-031) sobre o `psycopg`.
2. Ordem: `COPY` na tabela sem índices; chave primária; índice GIN em `lexico`; índice HNSW em `embedding` (ADR-038 e ADR-042); `VACUUM ANALYZE`. Durante a carga, `max_wal_size` de 16 GB, desfeito ao fim (ADR-045).
3. A duração de cada fase e o tamanho da tabela e de cada índice são registrados.
4. O carregador fica em `eixo-a-busca/consolidada/` e passa a ser o `preparar` do gerador do Eixo A quando o gerador existir; até lá, roda do host, contra o IP do container.

**Justificativa.** Segue as recomendações dos mantenedores (ADR-010). O formato binário evita converter um bilhão de números em texto e de volta. Criar os índices depois da carga é a recomendação das duas documentações e o que a ADR-038 fixa nos dois lados. O `VACUUM ANALYZE` final gera as estatísticas do planejador e grava as marcações de visibilidade nas páginas; sem ele, as primeiras leituras de cada página durante a medição fariam essas gravações, e a cópia inicial (ADR-029) guarda o estado já limpo.

**Efeito sobre a comparação.** Neutro nas consultas, cuja medição começa depois do preparo. O tempo de preparo é reportado por fase, do banco vazio ao estado pronto (ADR-044).

**Estado.** Aceita. Revisada em 03/11/2026: chave primária criada depois do `COPY` (seção 14.4.3 da documentação) e `max_wal_size` maior durante a carga (ADR-045). Antes, a chave existia durante o `COPY` e o `max_wal_size` ficava no padrão, com checkpoints a cada 9 segundos.

---

## ADR-044 — Arquitetura poliglota do Eixo A: composição, índices e estado pronto

**Contexto.** A ADR-036 fixa as versões e a configuração de referência do OpenSearch e do Qdrant; a ADR-037 deixa a divisão de memória para depois da carga; a ADR-041 fixa o texto indexado e os identificadores; a ADR-038, os parâmetros do HNSW. Falta definir como os dois motores são montados e carregados, e em que estado a carga termina.

**Decisão.**

1. Composição em `eixo-a-busca/poliglota/compose.yaml`, com os dois motores herdando o `sistema-sob-teste` (cpuset da ADR-018) e as imagens fixadas pelo digest registrado na ADR-036. OpenSearch conforme o exemplo oficial de Docker Compose: `discovery.type=single-node`, `bootstrap.memory_lock=true` com `memlock` ilimitado, `nofile` de 65536, `DISABLE_INSTALL_DEMO_CONFIG` e `DISABLE_SECURITY_PLUGIN` (ADR-036), heap fixo em metade da memória do container. Qdrant com `QDRANT__TELEMETRY_DISABLED=true` (ADR-023). Os dois com 2 minutos para encerrar, pela mesma razão dos containers PostgreSQL na ADR-030: a cópia inicial precisa sair de dados consistentes. Memória durante a carga: 12 GiB para o OpenSearch (heap de 6 GiB) e 8 GiB para o Qdrant, a estimativa da ADR-037, revista com o que for medido.
2. OpenSearch: índice `passagens` com 1 shard e 0 réplicas; `_id` = N (ADR-041); um único campo `texto` (título, espaço, texto) do tipo `text` com o analisador `english`; `_source` no padrão.
3. Qdrant: coleção `passagens` com vetores de 384 dimensões, distância `Cosine`, `m` = 16 e `ef_construct` = 100 (os padrões, declarados); identificador do ponto = N; sem payload; o resto no padrão, inclusive a segmentação automática e os vetores em memória.
4. Carga com os clientes oficiais dos mantenedores, `opensearch-py` 3.2.0 e `qdrant-client` 1.19.1 (por gRPC), fixados no lock, como o `pgvector` no lado consolidado (ADR-043).
5. Estado pronto, em que a cópia inicial (ADR-029) é tirada: no PostgreSQL, `VACUUM ANALYZE` concluído; no OpenSearch, `refresh` feito e nenhuma fusão de segmentos em andamento; no Qdrant, coleção verde e todos os vetores indexados. O tempo de preparo de cada arquitetura vai do banco vazio ao estado pronto, por fase. Não há `force merge`.

**Justificativa.** São as configurações de referência dos mantenedores (ADR-010). Com um nó só, a réplica nunca seria alocada e o índice ficaria amarelo, sem efeito sobre os dados. O mesmo texto e os mesmos identificadores da ADR-041 tornam os resultados comparáveis entre si e com os julgamentos. O estado pronto definido pelo critério de cada sistema garante que nenhuma atividade de fundo da carga (fusões, indexação) invada a medição. O `force merge` é uma otimização de busca para índices somente leitura, não o estado padrão depois da carga.

**Efeito sobre a comparação.** O Qdrant divide os dados em vários segmentos, cada um com seu grafo HNSW, enquanto o `pgvector` tem um grafo só: os parâmetros são os mesmos, a estrutura não, e a calibração por recall (ADR-038) iguala a qualidade da vizinhança; a diferença é declarada. Sem `force merge`, o OpenSearch fica no estado padrão depois da carga, com vários segmentos, o que pode lhe custar latência em relação a um índice fundido; também declarado. O `memory_lock` impede que o heap vá para o swap, sem efeito prático com o swap desligado (ADR-016).

**Estado.** Aceita. As recomendações de carga em massa de cada sistema são tratadas na ADR-045.

---

## ADR-045 — Recomendações de carga em massa no preparo do Eixo A

**Contexto.** Cada mantenedor publica orientações para cargas em massa. A documentação do PostgreSQL 18 (seção 14.4) manda usar `COPY`, criar os índices depois da carga, aumentar temporariamente o `maintenance_work_mem` e o `max_wal_size` e rodar `ANALYZE` ao fim, sem indicar valores. A do OpenSearch (ajuste para velocidade de indexação) indica desligar o `refresh` numa indexação pura e começar com lotes de 5 a 15 MiB; o resto da página são sugestões a experimentar (buffer de indexação, translog, threads de fusão). A do Qdrant (envio em massa) indica lotes de 64 a 256 pontos em 2 a 4 fluxos paralelos. Na carga de desenvolvimento de 03/11/2026, o PostgreSQL com `max_wal_size` padrão (1 GB) fez checkpoints a cada 9 segundos durante o `COPY`.

**Decisão.** Aplicar só as recomendações concretas de cada mantenedor, só durante o preparo:

1. PostgreSQL: `max_wal_size` de 16 GB durante a carga, acima dos cerca de 13 GB de WAL que ela gera, para que só o `checkpoint_timeout` dispare checkpoints, e volta ao padrão ao fim (`ALTER SYSTEM` e `RESET`); chave primária criada depois do `COPY`. O `COPY`, os índices depois da carga, o `maintenance_work_mem` e o `VACUUM ANALYZE` já estão nas ADR-042 e ADR-043.
2. OpenSearch: `refresh_interval` = `-1` durante a carga e de volta ao padrão ao fim, com um `refresh`; lotes de 10 MiB.
3. Qdrant: lotes de 256 pontos, em 4 fluxos paralelos, um por núcleo do gerador.

As sugestões que a documentação do OpenSearch trata como experimento não são aplicadas.

**Justificativa.** Segue a ADR-010 nos três sistemas da mesma forma: o que o mantenedor recomenda para carga em massa entra; o que ele sugere testar, não. Os ajustes de servidor são desfeitos antes do estado pronto (ADR-044), e os do Qdrant ficam só no cliente; nada do que muda o preparo chega às consultas. Para o `max_wal_size`, a documentação não dá valor; 16 GB é o menor valor redondo acima do WAL da carga.

**Efeito sobre a comparação.** Neutro nas consultas. No tempo de preparo, cada sistema fica no melhor documentado pelo seu mantenedor. O envio paralelo é recomendação só do Qdrant: o `COPY` do PostgreSQL e o envio ao OpenSearch ficam com um fluxo cada, o que favorece o tempo de envio do Qdrant. A construção do grafo do Qdrant vem depois do envio, feita pelo otimizador, e é contada até o estado pronto.

**Estado.** Aceita. Corrige a proposta inicial, que atribuía ao Qdrant a recomendação de desligar a indexação durante o envio; ela não consta da documentação atual.

---

## ADR-046 — Serviço de busca do Eixo A

**Contexto.** A ADR-039 põe a fusão num serviço de busca dentro do sistema sob teste, com a mesma interface HTTP nas duas arquiteturas, e deixa a linguagem, o framework e os detalhes para a implementação. O serviço precisa não ser o gargalo, fazer o mesmo trabalho comum nos dois lados e respeitar a ADR-005 (nenhum modelo roda durante a medição).

**Decisão.**

1. Linguagem e servidor: Python 3.14, no mesmo ambiente travado da ADR-031, com Starlette 1.8.0 e Uvicorn 0.54.0 (`uvloop` 0.23.0 e `httptools` 0.9.0), em 6 processos, um por núcleo físico do sistema sob teste, sem registro de acesso por requisição. Clientes assíncronos oficiais: `psycopg` com `psycopg-pool` 3.3.3, `AsyncOpenSearch` do `opensearch-py` (com `aiohttp` 3.14.5) e `AsyncQdrantClient` por gRPC.
2. Interface: `POST /buscar` recebe `{"texto": ..., "vetor": [384 números]}` e devolve `{"ids": [10 inteiros], "pontuacoes": [10 números]}`; `GET /saude` responde quando o serviço e os motores estão prontos. O vetor da consulta vem do gerador, calculado antes (ADR-005).
3. Consulta: na consolidada, uma única consulta SQL com os 100 primeiros de cada ramo, a posição de cada resultado pelo `ROW_NUMBER`, a junção dos dois ramos e a soma de 1/(60 + posição); na poliglota, o `match` no OpenSearch (`size` 100, sem `_source`) e a busca no Qdrant (`limit` 100) em paralelo, e a mesma fusão em Python. Empates na fusão se resolvem pelo menor `id` nos dois lados; dentro do ramo léxico do PostgreSQL, onde empates do `ts_rank_cd` são comuns, também; no OpenSearch e no Qdrant vale a ordem que o motor devolve. O `ef` de cada motor vem da calibração (ADR-038): no PostgreSQL, fixado em cada conexão; no Qdrant, em cada busca.
4. Conexões: um pool por processo e por motor, dimensionado para que nenhuma requisição espere por conexão na maior concorrência do plano (concorrência máxima ÷ 6 por processo), com a mesma regra para os três motores. Se a soma passar das 100 conexões padrão do PostgreSQL, a mudança vira ADR.
5. Memória: 1 GiB para o serviço nas duas arquiteturas, dentro dos 20 GiB (ADR-037 e ADR-042).
6. Imagem construída pelo projeto e usada nas duas arquiteturas: base `python:3.14.4-slim-trixie` fixada por digest, `uv` 0.12.24 instalando só as dependências principais do lock (sem o grupo `embeddings`), código em `eixo-a-busca/servico/`, motor escolhido por variável de ambiente.
7. Equivalência: antes de qualquer medição, um teste de contrato manda as mesmas consultas aos dois lados e confere o formato da resposta, e confere que a fusão é idêntica, fundindo em Python os dois ramos do PostgreSQL obtidos em separado e comparando com a consulta SQL única.

**Justificativa.** Python mantém o código no mesmo ambiente, ferramentas e autoria do harness, e os três motores têm clientes oficiais assíncronos nele. Os 6 processos usam os núcleos do sistema sob teste sem disputar com o gerador. Receber o vetor pronto imita um cliente que calculou o embedding antes, em vez de um serviço que conhece as consultas de antemão. O desempate pelo `id` torna a fusão determinística e permite comparar os dois lados resultado a resultado. O pool dimensionado pela concorrência máxima impede que a espera por conexão apareça como latência de um motor.

**Efeito sobre a comparação.** O código do serviço é o mesmo nos dois lados, menos a parte que fala com os motores. A poliglota faz mais trabalho dentro dele (duas chamadas, leitura de 200 resultados e a fusão) do que a consolidada (uma chamada e 10 linhas); como Python é mais lento nesse trabalho do que uma linguagem compilada, a escolha encarece o lado poliglota, e isso é declarado. A calibração confere que o serviço não é o gargalo. O desempate pela ordem do motor no OpenSearch e no Qdrant, e não pelo `id`, é declarado; ordenar pelo `_id` no OpenSearch é desaconselhado pela documentação, e empates em BM25 e cosseno são raros.

**Estado.** Aceita.

---

## ADR-047 — Consultas do serviço ao PostgreSQL sem comandos preparados

**Contexto.** Por padrão, o `psycopg` transforma uma consulta em comando preparado no servidor depois de 5 execuções na mesma conexão (`prepare_threshold` = 5), e o PostgreSQL, no modo padrão (`plan_cache_mode` = `auto`), passa a usar um plano genérico depois de 5 planos específicos. No plano genérico da consulta híbrida (ADR-046), o `tsquery` montado a partir do texto é recalculado em cada linha encontrada pelo índice GIN, em vez de virar uma constante. Medido em 03/11/2026 sobre os dados completos, com 12 perguntas da NQ numa conexão: a partir da 11ª execução, o padrão levou 979 ms de mediana, contra 340 ms com o plano específico. Reescrever a consulta com `MATERIALIZED` ou com subconsulta escalar estabiliza os dois planos em ~870 ms, mas o planejador deixa de paralelizar o ramo léxico, porque não enxerga a pergunta.

**Decisão.** O pool do serviço abre as conexões com `prepare_threshold=None`: o cliente não prepara comandos, e cada consulta é planejada com os valores reais. Nenhuma configuração do servidor muda.

**Justificativa.** É como o OpenSearch e o Qdrant tratam cada busca: interpretam e planejam a consulta inteira a cada requisição, sem plano em cache. O custo do planejamento (~0,8 ms por consulta) entra na medição. O padrão mediria uma regressão de plano causada pelo cliente e pela forma da consulta, que um usuário atento evitaria; as reescritas trocariam essa regressão por outra.

**Efeito sobre a comparação.** Favorece o PostgreSQL em relação ao padrão do `psycopg`, e isso é declarado. A paralelização do ramo léxico (2 processos auxiliares por consulta) é o padrão do PostgreSQL; sob carga, os processos auxiliares são disputados (8 no total, `max_parallel_workers`), e a medição mostra esse efeito como ele é.

**Estado.** Aceita.

---

## ADR-048 — Gerador de carga do Eixo A

**Contexto.** O contrato do gerador (`harness/README.md`) pede um programa que carregue os dados (`preparar`) e rode, numa invocação contínua, aquecimento e medição com um nível de concorrência, gravando cada operação no `operacoes.csv`. A proposta cita k6 ou Locust para os Eixos A e B e define o nível de concorrência como variável independente.

**Decisão.**

1. Ferramenta: gerador próprio em Python, com asyncio e `aiohttp`, no mesmo ambiente travado da ADR-031, em até 2 processos, um por núcleo físico do gerador (ADR-018), com os clientes divididos entre eles.
2. Modelo de carga: laço fechado. N clientes, cada um com uma conexão HTTP persistente ao serviço de busca, mandam a próxima consulta assim que recebem a resposta, sem pausa. N é o nível de concorrência; a vazão é resultado.
3. Níveis de concorrência: 1, 2, 4, 8, 16, 32 e 64, iguais nas duas arquiteturas, confirmados na calibração (Etapa 3.1), que precisa encontrar a saturação das duas dentro da faixa. O pool do serviço passa a 11 conexões por processo (64 ÷ 6, para cima), 66 conexões ao PostgreSQL. Ampliar a faixa vira ADR.
4. Consultas: cada cliente percorre as 3.452 consultas da NQ numa ordem embaralhada pela semente da execução, começando em pontos diferentes da ordem e recomeçando ao fim. Com a revisão da ADR-032, a sequência é a mesma nas duas arquiteturas.
5. Qualidade: recall@10 e nDCG@10 medidos numa passagem própria por arquitetura, com as 3.452 consultas, uma de cada vez, depois da calibração do `ef`. As execuções de carga gravam só latência e sucesso.
6. Durações e erros: aquecimento de 120 s e medição de 300 s nas execuções curtas, confirmados na calibração. O gerador espera o `/saude` responder 200 antes de começar (até 5 min). Tempo limite de 60 s por requisição; erro e tempo esgotado são gravados com `ok` = 0, e a taxa de erro vai para o `gerador.json`. O gerador só termina com erro quando não consegue rodar.
7. Imagem do gerador do Eixo A, com a base, o `uv` e o lock da imagem do serviço, contendo o gerador e os dois carregadores; os dados entram montados como somente leitura. O `preparar` roda o carregador da arquitetura e grava o registro da carga em `/saida`. Os descritores `arquitetura.toml` das duas arquiteturas incluem o serviço de busca entre os serviços sob teste.

**Justificativa.** O gerador próprio grava o `operacoes.csv` no formato do harness, com instante Unix e relógio monotônico por operação, e cumpre o contrato numa invocação, sem conversão de saída; o k6 traria outra linguagem e formato, e o Locust agrega estatísticas e pesa mais por requisição. O laço fechado corresponde à variável independente da proposta e produz a curva de saturação. A mesma sequência de consultas nos dois lados compara as arquiteturas sob perguntas idênticas. A qualidade não depende da carga, porque os três motores devolvem sempre o mesmo resultado para a mesma consulta.

**Efeito sobre a comparação.** Neutro: o mesmo gerador, a mesma sequência de consultas e os mesmos níveis nos dois lados. O laço fechado não mede a latência sob uma taxa fixa de chegada: perto da saturação, a arquitetura mais lenta recebe menos requisições por segundo, e a comparação se faz pela vazão e pela latência em cada nível; essa limitação é declarada. O desvio em relação à proposta (gerador próprio em vez de k6 ou Locust) é declarado. A calibração confere que o gerador não é o gargalo.

**Estado.** Aceita.

**Qualidade (09/11/2026, item 5).** Passagem com as 3.452 consultas, uma de cada vez, com o `ef` calibrado (ADR-038: 260 no `pgvector`, 120 no Qdrant):

| Arquitetura | nDCG@10 | recall@10 | Duração da passagem |
|---|---|---|---|
| Consolidada | 0,3698 | 0,5906 | 1.917 s (0,55 s por consulta) |
| Poliglota | 0,4443 | 0,6538 | 35 s (10 ms por consulta) |

A poliglota tem qualidade maior. Isso já estava previsto como diferença declarada na ADR-039: o ramo vetorial tem o mesmo recall@100 nos dois lados (ADR-038), e o que muda é a ordenação léxica (`ts_rank_cd` em vez de BM25) e o tratamento de texto. A duração mostra o outro lado da mesma diferença estrutural: com a semântica OU, o PostgreSQL calcula o `ts_rank_cd` de todo documento que contém algum termo. Para comparação, a busca vetorial exata sozinha tem nDCG@10 de 0,5018 (ADR-035), e a fusão fica abaixo disso nas duas arquiteturas. A separação da qualidade por ramo, que mostra quanto da diferença vem do ramo léxico, fica para a análise (Fase 4). Os resultados estão em `resultados/qualidade/a-consolidada.json` e `a-poliglota.json`, com o nDCG e o recall de cada consulta.

**Revisão do item 6 (10/11/2026, ADR-067, item 6): aquecimento de 300 s.** Aprovada pelo Léo.
- **Contexto.** Na rampa do Eixo A (janela `20261109-2139`), o critério das durações falhou na consolidada com 2 clientes. A vazão do último minuto da medição ficou 12,4% acima da do primeiro, e o limite era 5%.
- **Diagnóstico.** Saiu de `harness/analise/calibracao.py`.
  - **Ruído:** com 110 a 330 consultas por minuto e CV de 0,65 a 0,91 nas latências, a diferença entre dois minutos oscila de 5 a 9 pontos só pelo sorteio das consultas. A estimativa é sqrt(2) · CV / sqrt(n). Uma simulação de clientes em laço fechado com as latências observadas dá a mesma ordem. Nessa simulação, um sistema estacionário passa dos 5% com probabilidade de 38 a 61%.
  - **Tendência:** a inclinação dentro da medição não tem sinal consistente entre os níveis.
  - **Aquecimento incompleto:** o último minuto do aquecimento ficou abaixo da média da medição em todos os níveis até a saturação: −13,2%, −6,9%, −6,3% e −5,4% com 1, 2, 4 e 8 clientes. Na poliglota a diferença não passou de 0,4% em nenhum nível.

  | Clientes | Consolidada, 1º e 2º min do aquecimento | Consolidada, média da medição |
  |---|---|---|
  | 1 | 1,3 e 1,6 consultas/s | 1,86 consultas/s |
  | 2 | 2,9 e 3,3 | 3,52 |
  | 4 | 4,5 e 4,7 | 5,0 |
  | 8 | 4,8 e 5,0 | 5,28 |
- **Decisão.** Aquecimento de 300 s nas execuções curtas do Eixo A, nas duas arquiteturas. A medição continua com 300 s, e o critério dos 5% continua o mesmo para os outros eixos.
- **Justificativa.** A consolidada ainda está esquentando no fim dos 120 s. Com 300 s de aquecimento, a medição começa no sexto minuto, onde a curva já estabilizou nos quatro níveis. A falha do critério com 2 clientes vem sobretudo do ruído, então aumentar a medição não resolveria. A variação entre repetições das execuções curtas mede esse ruído diretamente.
- **Efeito sobre a comparação.** Favorece a consolidada, que é a que ainda esquentava. Com 120 s, o primeiro minuto da medição ficava em média 1,4% abaixo da média da medição (de 1 a 8 clientes), um viés de cerca de 0,3% contra ela na média dos 5 minutos. A poliglota não muda, porque já estava estável. Cada execução do A fica 3 minutos mais longa.

---

## ADR-049 — Coletores internos do OpenSearch e do Qdrant

**Contexto.** A ADR-021 manda coletar estatísticas internas de todos os sistemas de dados com a mesma frequência, e o `harness/README.md` deixa o catálogo de cada sistema especializado para a etapa do seu eixo.

**Decisão.** Um coletor por sistema, nos núcleos do gerador, com a mesma cadência do PostgreSQL: estatísticas a cada 10 s e tamanhos a cada 60 s.

1. OpenSearch (`os_`), por `_nodes/stats`: buscas e tempo de busca acumulados, operações de indexação, heap da JVM usado, coletas de lixo (quantidade e tempo, geração nova e velha), memória do cache de consultas e número de segmentos; a cada 60 s, tamanho e documentos de cada índice, por `_cat/indices`.
2. Qdrant (`qd_`), por `/metrics`: requisições e tempo acumulado por rota (gRPC e REST) e memória alocada e residente; a cada 60 s, pontos, vetores indexados e segmentos de cada coleção, pela API de coleções.

**Justificativa.** Cobre, para os sistemas especializados, o que o coletor do PostgreSQL cobre: a atividade de busca, a memória própria do processo e o tamanho dos dados. Pedidos HTTP leves, a cada 10 s, como as consultas do coletor do PostgreSQL.

**Efeito sobre a comparação.** Neutro, pela regra da ADR-021. O custo da coleta é medido no piloto.

**Estado.** Aceita.

---

## ADR-050 — Operação de negócio, mensagem e esquema do Eixo B

**Contexto.** A proposta pede mensagens sintéticas com carga útil de tamanho controlado (seção 6.2), e a ADR-006 fixa as três configurações do Eixo B: consolidada, ingênua e *outbox*. Falta definir o que cada enfileiramento grava e o formato da mensagem, iguais nas três.

**Decisão.**

1. Operação de negócio: cada enfileiramento grava um pedido na tabela `pedidos` (identificador gerado pelo banco, cliente, valor e instante de criação) e enfileira uma tarefa que se refere a ele. Cliente e valor vêm de um gerador pseudoaleatório com a semente da execução.
2. Mensagem: 16 bytes de cabeçalho, com o instante Unix em microssegundos e o relógio monotônico em nanossegundos do início do enfileiramento, seguidos de 1 KiB de carga útil, sorteada uma vez por execução pela semente. São os mesmos bytes nas três configurações. O identificador do pedido viaja ao lado da mensagem, na coluna `pedido` das tabelas e no `message_id` do RabbitMQ, e é a chave de idempotência (ADR-056).
3. Enfileiramento em cada configuração:
   - consolidada: um único comando SQL grava o pedido e a tarefa (`tarefas`), numa só transação;
   - ingênua: o `INSERT` do pedido é confirmado, a mensagem é publicada no RabbitMQ em seguida, e o enfileiramento espera a confirmação do broker;
   - *outbox*: o mesmo comando da consolidada, gravando na tabela de saída (`saida`) em vez de `tarefas`; o relay publica depois (ADR-053).
4. Esquemas em `esquema.sql` na pasta de cada arquitetura, aplicados pelo `preparar`. `tarefas` e `saida` têm a mesma estrutura (identificador gerado pelo banco, pedido e mensagem), sem chave estrangeira para `pedidos`.

**Justificativa.** O pedido torna real o problema da escrita dupla, que é o que distingue as três configurações. A carga útil fixa por execução não muda o custo: nenhum dos sistemas comprime valores desse tamanho nas configurações usadas (o PostgreSQL só comprime acima de cerca de 2 KB). O relógio monotônico do host é o mesmo em todos os processos e permite medir a latência fim a fim sem depender de ajustes do relógio Unix. O comando único é a forma mais direta de gravar as duas linhas numa transação com uma ida ao banco, e vale igualmente para a consolidada e para a *outbox*.

**Efeito sobre a comparação.** Neutro entre a consolidada e a *outbox*, que gravam no PostgreSQL exatamente o mesmo: pedido e linha de 1 KiB. A ingênua faz uma ida ao banco e outra ao broker por enfileiramento, característica da própria arquitetura. A ausência de chave estrangeira vale para as duas tabelas.

**Estado.** Aceita. Revisada em 08/11/2026: o comando de enfileiramento deixou de chamar `pg_notify` (revisão da ADR-051).

**Revisão do item 2 (10/11/2026, ADR-054).** Nas execuções com taxa fixa, o cabeçalho leva a origem da latência definida na revisão da ADR-054, em Unix e no relógio monotônico. É o instante agendado do envio quando o produtor ainda estava ocupado nessa hora, e o início real do enfileiramento quando ele estava livre. Assim, a latência fim a fim inclui a espera causada pelo sistema sob teste. O tamanho e a carga útil não mudam. Em laço fechado, nada muda.

---

## ADR-051 — Retirada e espera na fila consolidada

**Contexto.** A fila consolidada usa `SELECT ... FOR UPDATE SKIP LOCKED` (ADR-006). Falta definir o comando de retirada, a confirmação e o que o consumidor faz quando a fila está vazia.

**Decisão.**

1. Retirada: `DELETE FROM tarefas WHERE id = (SELECT id FROM tarefas ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING pedido, mensagem`, em modo *autocommit*. Cada tarefa é uma transação, e o commit do `DELETE` é a confirmação. A ordem pelo identificador mantém a fila na ordem de chegada, como a fila do RabbitMQ.
2. Consumo sem processamento além da retirada e da confirmação, para isolar o mecanismo de fila (QS2). O mesmo vale para o RabbitMQ (ADR-054).
3. Espera por consulta periódica: um consumidor que encontra a fila vazia tenta de novo depois de 100 ms. O enfileiramento não avisa (`pg_notify`).
4. O teto do aviso é medido uma vez na calibração, com o modo de espera por aviso do gerador (`TCC_ESPERA=aviso`), e reportado como achado. Nesse modo, o enfileiramento chama `pg_notify`, e uma conexão por processo escuta o canal e acorda um consumidor ocioso por aviso.

**Justificativa.** A documentação do PostgreSQL apresenta o `SKIP LOCKED` como o meio de evitar contenção de bloqueio quando vários consumidores acessam uma tabela usada como fila. O `DELETE ... RETURNING` retira e confirma numa ida ao banco. O intervalo de 100 ms é o padrão de duas filas de referência sobre PostgreSQL: o PGMQ (`read_with_poll`, `poll_interval_ms` = 100) e o Solid Queue, a fila padrão do Rails (`polling_interval` de 0,1 s nos trabalhadores, também com `FOR UPDATE SKIP LOCKED`). O aviso foi descartado depois de medido (ver o estado).

**Efeito sobre a comparação.**
- A espera por consulta acrescenta até 100 ms à latência fim a fim das tarefas que chegam com a fila vazia, e cada consumidor ocioso faz até 10 consultas vazias por segundo. Desfavorece a consolidada em relação a um mecanismo de aviso sem custo, que o PostgreSQL não tem. A mesma regra vale para o relay da *outbox* (ADR-053).
- O teto do aviso é uma limitação real do PostgreSQL como fila e entra no artigo como achado. A configuração medida é a que as filas de referência usam.
- Com consumo vazio, a confirmação no próprio commit equivale à confirmação manual do RabbitMQ. Com um processamento real, o consumidor do PostgreSQL precisaria abrir a transação, processar e confirmar, uma ida ao banco a mais por tarefa, enquanto a confirmação do RabbitMQ é assíncrona e não espera resposta. Medir sem processamento favorece a consolidada em relação a esse cenário.

**Estado.** Aceita. Revisada em 08/11/2026, com aprovação do Léo. A versão anterior acordava os consumidores por `pg_notify` no enfileiramento e `LISTEN` no gerador. Medido em 07/11/2026, num PostgreSQL de desenvolvimento com o esquema da *outbox*, comando de enfileiramento repetido por 8 s em cada nível:

| Clientes | Com `pg_notify` | Sem `pg_notify` |
|---|---|---|
| 1 | 1.303/s | 1.659/s |
| 4 | 1.811/s | 5.014/s |
| 16 | 1.890/s | 19.315/s |
| 32 | 1.737/s | 34.480/s |

O commit que avisa toma um bloqueio global e espera o próprio `fsync` (cerca de 0,7 ms no P510, ver `ambiente/README.md`), sem dividir a gravação com os outros commits. Na consolidada completa, com 32 produtores e 32 consumidores, o aviso deu 1.734 tarefas/s, com o PostgreSQL em 0,3 núcleo; a consulta a cada 100 ms consumiu 13 mil tarefas/s, com o PostgreSQL saturado em 10,4 núcleos. A latência fim a fim com 4 clientes foi de 2,9 ms com aviso e de 3,3 ms com consulta.

**Correção (10/11/2026).** Os 3,3 ms com consulta e 4 clientes foram medidos num momento sem atraso acumulado na fila. Na rampa da calibração, a mesma configuração deu 1,7 ms nos primeiros 10 s e ~25 ms depois de uma perturbação que não drenou. Com espera por aviso, deu 2,8 ms, estável. A causa e a forma de medir a latência fim a fim estão na revisão da ADR-054.

---

## ADR-052 — RabbitMQ no Eixo B

**Contexto.** As configurações ingênua e *outbox* usam o RabbitMQ como broker. A ADR-010 manda seguir as recomendações do mantenedor, e a ADR-011, fixar a imagem por digest. A garantia do lado do broker precisa equivaler ao commit do PostgreSQL: mensagem confirmada é mensagem em disco.

**Decisão.**

1. Versão 4.4.0, publicada em 19/10/2026, a primeira da série 4.4 e a última com pelo menos duas semanas de publicação em 07/11/2026, mesma regra da ADR-036. Imagem oficial com o plugin de gerenciamento, fixada por digest. Nó único, com nome de máquina fixo (`rabbitmq`). Usuário padrão da imagem (`guest`, que a própria imagem libera na rede), só na rede interna (ADR-026).
2. Uma fila `tarefas`, do tipo quorum, durável, declarada no preparo. Mensagens persistentes.
3. Publicação com confirmação (*publisher confirms*): cada enfileiramento espera a confirmação do broker.
4. Consumo com confirmação manual, uma por mensagem, e *prefetch* de 100 por consumidor.
5. Conexões separadas para publicação e consumo, uma de cada por processo, com um canal por produtor ou consumidor.
6. Limite de memória absoluto (`vm_memory_high_watermark.absolute`) em 60% do limite do container: 4915 MiB na ingênua (8 GiB) e 4300 MiB na *outbox* (7 GiB).
7. Limite de 65.536 descritores de arquivo (`nofile`) no container, acima do mínimo de 50 mil do checklist de produção.
8. Demais parâmetros no padrão. Sem telemetria: conferido em 07/11/2026, nenhuma conexão de saída do container.

**Justificativa.**
- A documentação das filas quorum recomenda confirmação do publicador e confirmação manual do consumidor, e diz que a fila grava tudo em disco antes de agir. A confirmação só sai depois disso, como o commit do PostgreSQL só retorna depois do WAL em disco.
- A faixa de *prefetch* de 100 a 300 é a que a documentação de confirmações indica como a de melhor vazão. O checklist de produção pede conexões separadas para publicação e consumo.
- O nome de máquina fixo é necessário porque o nome do nó e o diretório de dados dependem dele. Sem isso, a restauração da ADR-029 não acharia os dados.
- O limite absoluto segue a documentação de memória, que o recomenda em containers porque o RabbitMQ nem sempre detecta o limite do cgroup. Conferido em 07/11/2026: o 4.4.0 calculou o limite relativo padrão (0,6) sobre a memória da máquina, 19,3 GB, ignorando o limite de 8 GiB do container, e seria encerrado pelo kernel antes de acionar o próprio alarme. Os 60% preservam a proporção do padrão.

**Efeito sobre a comparação.**
- A fila quorum foi feita para replicação, e a documentação avisa que, com menos de três nós, ela não dá todas as suas garantias. A replicação está fora do escopo (proposta, seção 4.4). Num nó único resta a gravação em disco antes da confirmação, que é a garantia equivalente ao commit.
- Uma fila clássica durável, com mensagens persistentes, também confirmaria depois de gravar em disco e pode ser mais rápida num nó único. Se for, a escolha desfavorece as configurações com RabbitMQ. A alternativa fica declarada e não medida.
- O limite de memória corrige um defeito de detecção que prejudicaria o RabbitMQ.
- O limite de descritores favorece o RabbitMQ em relação ao padrão do Docker, de forma legítima, pelo mesmo raciocínio da ADR-028: sem ele, o sistema roda abaixo do mínimo que o próprio mantenedor pede. Na escala do experimento, com poucas conexões, o efeito esperado é nulo.
- Os demais itens são neutros.

**Estado.** Aceita. Revisada em 08/11/2026, com aprovação do Léo: item 7 acrescentado. Na janela `teste-eixo-b`, o RabbitMQ avisou no log "Available file handles: 1024. Please consider increasing system limits", o padrão do Docker. O checklist de produção pede pelo menos 50 mil descritores, inclusive em desenvolvimento. O valor de 65.536 é o mesmo do OpenSearch no Eixo A (ADR-044).

---

## ADR-053 — Relay da *outbox*

**Contexto.** Na *outbox*, um processo relay lê a tabela de saída e publica no RabbitMQ (ADR-006). O passo 2.3.3 pede tratamento de reentrega, e o README do eixo pede a vazão do relay medida isoladamente, porque ele pode virar o gargalo.

**Decisão.**

1. Processo Python com asyncio, `psycopg` e `aio-pika` (os mesmos do gerador, ADR-054), num container próprio dentro do sistema sob teste, com 1 GiB (ADR-055). Imagem própria, só com o relay.
2. Cada trabalhador repete, numa transação:
   - `DELETE FROM saida WHERE id = ANY(ARRAY(SELECT id FROM saida ORDER BY id LIMIT 100 FOR UPDATE SKIP LOCKED)) RETURNING ...`;
   - publicação das linhas retiradas, na ordem do identificador, com confirmação do broker;
   - commit.

   Se a publicação falhar, a transação é desfeita e as linhas voltam à tabela.
3. Dois trabalhadores, cada um com a sua conexão ao PostgreSQL e o seu canal no RabbitMQ. Um trabalhador que encontra a tabela vazia tenta de novo depois de 100 ms, a mesma regra dos consumidores da consolidada (ADR-051).
4. Reentrega: um relay que cai depois de publicar e antes do commit publica de novo as mesmas linhas quando volta. A entrega é pelo menos uma vez, e o identificador do pedido no `message_id` permite reconhecer a repetição (ADR-056).
5. Vazão isolada, medida antes da calibração com `eixo-b-fila/cargas/relay_isolado.py`:
   - a tabela de saída recebe 1 milhão de linhas com o relay parado;
   - o relay é ligado com os consumidores ativos;
   - a vazão é o número de linhas dividido pelo tempo até a tabela e a fila esvaziarem.

   Se a vazão da *outbox* na calibração chegar perto da vazão isolada, o relay é o gargalo, e o número de trabalhadores sobe por revisão desta ADR.

**Justificativa.** É o relay por consulta periódica descrito na literatura do padrão (Richardson, 2018), com `SKIP LOCKED` para permitir mais de um trabalhador. O commit depois da confirmação garante que nenhuma linha some sem ter sido publicada. Lotes de 100 linhas dividem o custo do commit e da confirmação. A alternativa seria a captura pelo log de transações, como faz o Debezium, ao custo de mais um sistema na arquitetura.

**Efeito sobre a comparação.** O relay é uma peça a mais que só a *outbox* tem, e o seu custo de CPU e memória é um custo real dela. O lote e os dois trabalhadores são escolha do autor, não recomendação de um mantenedor. É a medida da vazão isolada que impede o relay de distorcer a comparação. A espera de até 100 ms com a tabela vazia soma-se à latência fim a fim da *outbox*.

**Estado.** Aceita. Revisada em 08/11/2026, junto com a ADR-051: a espera por aviso (`LISTEN`, com `pg_notify` no enfileiramento) foi trocada pela consulta a cada 100 ms. O modo por aviso continua disponível no relay (`TCC_ESPERA=aviso`) para a medida do teto na calibração. Primeira medida, sem valor de calibração (07/11/2026, banco recém-preparado, 32 produtores e 32 consumidores): o relay publicou cerca de 15,6 mil mensagens/s, sempre em lotes cheios, com as conexões esperando a confirmação do RabbitMQ, enquanto os produtores gravavam 24,6 mil linhas/s; nesse nível, o relay é o gargalo.

**Vazão isolada (09/11/2026, item 5 e ADR-067, item 1).** Medida dentro do protocolo, com o `max_wal_size` final (8 GB, ADR-055). A *outbox* foi restaurada da cópia inicial e passou por `fstrim` e 20 min de repouso. Postgres, RabbitMQ e relay subiram com o `cpuset` do sistema sob teste, e o script rodou nos núcleos do gerador. Configuração: 2 trabalhadores, lotes de 100, espera por consulta e 8 consumidores.

| Medida | Valor |
|---|---|
| Vazão em regime (de 90% a 10% das linhas restantes) | 20.886 mensagens/s |
| Vazão média até a tabela esvaziar | 20.379 mensagens/s |
| Linhas | 1 milhão, todas consumidas, sem repetição |
| Tabela de saída vazia | 49,1 s |
| Fila vazia | 58,2 s |

Na busca do `max_wal_size`, com c = 32, a *outbox* consumiu de 10,2 a 10,6 mil mensagens/s, cerca de 50% da vazão isolada. Isso fica abaixo do critério de 90% (ADR-067, item 6), e os 2 trabalhadores ficam. A primeira medida de 07/11, com o relay como gargalo, foi feita sem protocolo e com o `max_wal_size` padrão. O critério volta a ser aplicado em cada nível da rampa. Registro em `resultados/relay/vazao-isolada.json`.

**Revisão do item 5 (10/11/2026, ADR-067): relay como gargalo com 32 clientes.** Aprovada pelo Léo.

*Contexto.* Na rampa do Eixo B (janela `20261110-0928`), o critério do relay não disparou em nenhum nível. O máximo foi 71% da vazão isolada, com 16 clientes. Com 32 clientes, porém:
- os produtores enfileiraram ~25 mil/s desde o início;
- o relay entregou no máximo 16,2 mil/s nos primeiros 30 s, 78% da vazão isolada, e caiu para ~11 mil/s com a tabela de saída acima de 4 milhões de linhas;
- o PostgreSQL da *outbox* ficou em ~2,5 núcleos.

O relay foi, portanto, o componente que limitou esse nível. A média da medição (10.290/s, 49%) ficou longe dos 90% porque a vazão isolada foi medida sem enfileiramento concorrente e superestima a capacidade do relay sob carga. Os últimos ~90 s dessa execução também passaram pelo esgotamento do cache pSLC do SSD (ADR-067).

*Decisão.*
1. Os 2 trabalhadores ficam, e o critério da ADR-067, item 6, não muda.
2. A vazão sustentável da *outbox* (14.871/s com 16 clientes) é reportada como limite inferior, com a observação de que, com 32 clientes, o relay com 2 trabalhadores foi o gargalo.
3. A vazão isolada continua sendo reportada, com a ressalva de que não vale como capacidade do relay com produtores concorrentes.

*Justificativa.*
- O critério foi fixado antes das rampas para não ser ajustado depois de ver os dados, em nenhuma direção.
- Com 16 clientes, a *outbox* já sustenta 2,6 vezes a vazão da consolidada. O sentido da comparação não depende do nível 32.
- A *outbox* a ~25 mil/s grava ~63 GiB em menos de 7 min. Uma nova execução com 32 clientes e mais trabalhadores passaria do cache pSLC durante a medição e misturaria o efeito do relay com o do SSD.

*Efeito sobre a comparação.* Favorece a consolidada: a vazão sustentável da *outbox* fica limitada por uma escolha do autor (item 3), não por recomendação de mantenedor, e é reportada como limite inferior. A alternativa descartada era passar a 4 trabalhadores, medir de novo a vazão isolada e repetir a *outbox* com 32 clientes, ao custo de ~1,5 h.

---

## ADR-054 — Gerador e modelo de carga do Eixo B

**Contexto.** O contrato do gerador (`harness/README.md`) vale para os três eixos, e o catálogo de operações do Eixo B já define `enfileira`, `consome` e `fim_a_fim`. A pergunta QS2 pede a vazão sustentável e a latência fim a fim.

**Decisão.**

1. Ferramenta: gerador próprio em Python, como o do Eixo A (ADR-048), com asyncio sobre `uvloop`, `psycopg` 3.3.6 e `aio-pika` 10.1.1, no ambiente travado da ADR-031. Até 2 processos, um por núcleo físico do gerador, com produtores e consumidores divididos entre eles.
2. Modelo de carga: o nível de concorrência N corresponde a N produtores e N consumidores, todos em laço fechado e sem pausa. Cada produtor e cada consumidor tem a sua conexão ao PostgreSQL ou o seu canal no RabbitMQ (ADR-052).
3. Níveis: 1, 2, 4, 8, 16 e 32, iguais nas três arquiteturas, confirmados na calibração. Com 32, a consolidada usa 64 conexões, dentro do `max_connections` padrão (100) com o coletor e o relay. Ampliar a faixa vira ADR.
4. Operações gravadas no `operacoes.csv`:
   - `enfileira`: do início ao commit, e, na ingênua, até a confirmação do broker;
   - `consome`: no PostgreSQL, o `DELETE` com o commit; no RabbitMQ, da entrega da mensagem ao gerador até o envio da confirmação;
   - `fim_a_fim`: do início do enfileiramento ao fim do consumo, com `ts_us` igual ao instante do enfileiramento e a latência pelo relógio monotônico que viaja na mensagem (ADR-050).

   As retiradas que encontram a fila vazia não são operações e vão contadas no `gerador.json`.
5. Vazão sustentável: a maior vazão de consumo em que a fila não cresce durante a medição. A profundidade vem da diferença acumulada entre enfileiramentos e consumos do `operacoes.csv`, conferida pelas métricas dos sistemas (tuplas vivas de `tarefas` e de `saida`, mensagens prontas e pendentes no RabbitMQ).
6. Durações e erros como no Eixo A: 120 s de aquecimento e 300 s de medição, confirmados na calibração. O gerador espera os sistemas aceitarem conexões antes de começar. Cada operação tem tempo limite de 60 s. Os erros são gravados com `ok` = 0 e seguidos de uma pausa de 0,1 s.
7. Cada processo grava as operações em disco durante a execução, e o `operacoes.csv` é ordenado ao fim, porque o volume do eixo e o teste de longa duração não cabem em memória.
8. Imagem `tcc-gerador-fila:1`, com o gerador e os três esquemas. O `preparar` aplica o esquema da arquitetura e, nas poliglotas, declara a fila.

**Justificativa.** O mesmo gerador nas três arquiteturas, com a mesma semente (ADR-032), manda a mesma sequência de pedidos e a mesma carga útil. Produtores e consumidores em igual número correspondem a um serviço que produz e consome tarefas na mesma escala. A profundidade calculada das operações vale igualmente para as três arquiteturas, inclusive para a *outbox*, em que a fila é a soma da tabela de saída e da fila do broker. O limite de 32 respeita o padrão do PostgreSQL sem ADR de desvio.

**Efeito sobre a comparação.**
- Neutro quanto ao gerador e à sequência de operações.
- A latência de `consome` mede coisas diferentes nos dois mecanismos. No PostgreSQL, o consumidor busca cada tarefa numa ida ao banco. No RabbitMQ, o broker empurra até 100 mensagens por consumidor antes da confirmação. Por isso `consome` não é comparada entre arquiteturas, e a comparação usa a vazão e a latência fim a fim.
- O laço fechado tem a limitação já declarada na ADR-048.
- A latência fim a fim inclui a de `enfileira`, igualmente nas três.

**Estado.** Aceita.

**Revisão dos itens 2 e 5 (10/11/2026, ADR-067): latência fim a fim com taxa fixa.** Aprovada pelo Léo.

*Contexto.* Na rampa do Eixo B, níveis 1 a 4 (janela `20261110-0402`), a latência fim a fim da fila consolidada com espera por consulta deu p50 de 171 ms com 1 cliente, 96 ms com 2 e 24 ms com 4. A *outbox* e a ingênua ficaram estáveis, em 3,6 a 4,5 ms e 1,9 a 2,2 ms.
- **Com 1 e 2 clientes:** cada consumidor achou a fila vazia uma única vez na execução inteira, nos primeiros milissegundos. Dormiu 100 ms (ADR-051, item 3), e o produtor enfileirou ~260 tarefas nesse intervalo. Esse atraso continuou na fila até o fim, com a profundidade variando poucas tarefas em 7 minutos.
- **Com 4 clientes:** a latência foi de 1,7 ms nos primeiros 10 s. Depois, uma perturbação deixou ~140 tarefas de atraso, e a latência ficou em ~25 ms até o fim.
- **Causa:** no modelo do item 2, produtor e consumidor da consolidada fazem um commit cada um por tarefa e andam no mesmo ritmo. O consumidor fica com utilização perto de 1 e não tem folga para drenar um atraso.
- **Nas poliglotas é diferente:** o RabbitMQ empurra as mensagens e confirma de forma assíncrona, o que deixa o consumidor com folga.

Por isso, nesse modelo, a latência fim a fim da consolidada mede o transitório da partida, não o mecanismo de fila, e varia ao acaso entre repetições. A vazão sustentável não é afetada.

*Decisão.*
1. **Vazão sustentável:** continua medida como no item 2, com N produtores e N consumidores em laço fechado, nas rampas e nas execuções curtas de vazão. O critério do item 5 não muda.
2. **Latência fim a fim:** medida em execuções próprias, com os produtores em taxa fixa (laço aberto).
   - **No plano:** a chave `taxas` (mensagens/s, a soma de todos os produtores), aceita só no Eixo B. O identificador da execução termina em `_t<taxa>`.
   - **Produtores e consumidores:** 32 de cada, o nível mais alto da faixa (item 3), iguais nas três arquiteturas.
   - **Agenda uniforme:** o envio k está agendado para o início do aquecimento mais k/R, com R a taxa, e cabe ao produtor k mod 32. Cada produtor tem uma operação em curso por vez. Se a anterior ainda não terminou na hora agendada, o envio sai logo que ela termina, sem pular o agendamento.
   - **Origem da latência:** o `ts_us` e a latência de `enfileira` e de `fim_a_fim` partem do instante agendado quando o produtor ainda estava ocupado com o envio anterior nessa hora. Assim, a espera causada pelo sistema sob teste entra na latência, em vez de reduzir a carga oferecida (correção da omissão coordenada). Quando o produtor estava livre, eles partem do início real do envio, para que a resolução do temporizador do gerador (cerca de 1 ms no `uvloop`) não entre na latência. O cabeçalho da mensagem leva a origem usada (revisão da ADR-050).
   - **Taxas:** 25%, 50% e 75% da menor vazão sustentável máxima entre as três arquiteturas na rampa (níveis 1 a 32), arredondadas para baixo na centena. Os valores entram na proposta das execuções curtas (ADR-067, item 7).
   - **Validade:** na medição, a taxa de enfileiramento fica a menos de 1% da planejada e a fila não cresce (item 5). Se uma arquitetura não cumprir isso, a execução é reportada como taxa não sustentada por ela, não como latência.
   - **Diagnóstico:** o `gerador.json` registra a taxa, quantos envios encontraram o produtor ocupado, quantos saíram mais de 1 ms depois do agendado e o maior atraso.
3. **Durações e erros:** iguais aos do item 6.

*Justificativa.*
- **Laço aberto para latência:** benchmarks de filas medem a latência fim a fim com taxa de produção fixa e a vazão máxima em teste separado; é o que faz o OpenMessaging Benchmark. Em laço fechado e no limite da vazão, a latência de uma fila não tem valor de regime.
- **Contagem a partir do instante agendado:** evita que um produtor atrasado esconda a espera, como faz o wrk2.
- **32 produtores:** dão capacidade de produção às três arquiteturas em todas as taxas. A mais lenta por produtor é a ingênua, com ~540 envios/s com 1 cliente.
- **Agenda uniforme:** é a do limitador de taxa usual e não acrescenta rajadas sorteadas.

*Efeito sobre a comparação.*
- **Favorece a consolidada em relação ao modelo anterior:** o atraso congelado da partida sai da medida.
- **Custo da espera por consulta:** passa a aparecer quando a fila esvazia, como a ADR-051 declara. Vale na consolidada e no relay da *outbox* (ADR-053).
- **32 consumidores:** é o valor da faixa mais favorável à consolidada. Uma tarefa que chega com a fila vazia espera, em média, cerca de 100/(C + 1) ms até a próxima consulta de algum consumidor ocioso. As consultas vazias, até 320 por segundo, entram no custo medido. Nas poliglotas, que recebem mensagens empurradas, o número de consumidores pesa pouco na latência.
- **Agenda uniforme:** forma filas menores que chegadas de Poisson em todas as arquiteturas, o que fica declarado.

*Teste funcional (10/11/2026).* Imagem `tcc-gerador-fila:2`, fora do protocolo e sem valor de medição. As três arquiteturas foram restauradas da cópia inicial e rodaram com 32 produtores e 32 consumidores, 10 s de aquecimento e 30 s de medição, a 1.400 e a 4.200 mensagens/s.
- **Taxa e fila:** a taxa obtida ficou a menos de 0,003% da planejada, e a fila não cresceu. Não houve erros.
- **Agenda:** de 0,1% a 0,5% dos envios encontraram o produtor ocupado, com atraso máximo de 33 a 84 ms, registrados no `gerador.json`.
- **Latência fim a fim (p50 / p99):**
  - consolidada: 49,7 / 98,9 ms a 1.400/s e 34,3 / 98,4 ms a 4.200/s;
  - ingênua: 2,7 / 4,1 ms e 2,8 / 4,2 ms;
  - *outbox*: 4,0 / 71 ms e 4,5 / 7,9 ms.

*Correção do efeito dos 32 consumidores.* A estimativa de ~100/(C + 1) ms de espera supunha consumidores ociosos com fases independentes. No teste, eles se sincronizaram: esvaziam a fila juntos, dormem os mesmos 100 ms (ADR-051, item 3) e acordam juntos. Foram ~308 retiradas vazias por segundo a 1.400/s, quase os 32 consumidores a cada ciclo. Com isso, a espera média fica perto da metade do intervalo, e o p99, perto do intervalo inteiro. Os 32 consumidores não são, portanto, o valor mais favorável à consolidada. Ao esvaziar a fila mais depressa, eles fazem todos dormirem juntos. O custo da espera por consulta pesa mais que o declarado e desfavorece a consolidada. A configuração não muda, porque o intervalo vem das filas de referência (ADR-051) e o número de consumidores é igual nas três arquiteturas.

---

## ADR-055 — Divisão da memória e configuração dos sistemas no Eixo B

**Contexto.** A ADR-025 fixa 20 GiB para o sistema sob teste em cada arquitetura. A consolidada concentra o orçamento no PostgreSQL; as poliglotas o dividem. A ADR-010 proíbe tuning além das recomendações dos mantenedores.

**Decisão.**

1. Divisão:
   - consolidada: PostgreSQL com 20 GiB;
   - ingênua: PostgreSQL com 12 GiB e RabbitMQ com 8 GiB;
   - *outbox*: PostgreSQL com 12 GiB, RabbitMQ com 7 GiB e relay com 1 GiB.
2. PostgreSQL:
   - pela regra da ADR-042, `shared_buffers` em 25% da memória do container e `effective_cache_size` no restante: 5120 MB e 15360 MB na consolidada, 3072 MB e 9216 MB nas poliglotas;
   - `work_mem` de 4 MB (o padrão, declarado);
   - `pg_stat_statements` carregado (ADR-021), `/dev/shm` de 1 GiB (ADR-028);
   - todo o resto no padrão, inclusive `synchronous_commit`, `max_wal_size`, `max_connections` e o autovacuum.
3. RabbitMQ: padrão, com o limite de memória da ADR-052.
4. Ajustes guiados pelos próprios sistemas, na calibração. Um aviso do sistema muda a configuração por revisão desta ADR, antes das execuções curtas, em todas as arquiteturas que usam aquele sistema:
   - no PostgreSQL, "checkpoints are occurring too frequently": o `max_wal_size` sobe até o aviso parar;
   - no RabbitMQ, alarme de memória ou de disco.
5. A divisão é conferida no piloto, pela memória própria de cada container sob carga, como na ADR-037.

**Justificativa.** O PostgreSQL da consolidada faz o trabalho dos dois sistemas e recebe o orçamento inteiro. Nas poliglotas, o PostgreSQL guarda só os pedidos e, na *outbox*, a tabela de saída. O RabbitMQ recebe a maior fatia que ainda deixa ao PostgreSQL o bastante para o cache de dados. O `shared_buffers` em 25% é o valor inicial indicado pela documentação do PostgreSQL, já usado no Eixo A. Ajustar só pelo aviso do próprio sistema mantém a regra da ADR-010 e não depende da familiaridade do autor com cada um.

**Efeito sobre a comparação.**
- Neutro por construção (ADR-003).
- O PostgreSQL da consolidada tem mais memória que o das poliglotas. O efeito deve ser pequeno, porque os dados quentes de uma fila são poucos, e a análise usa a memória medida de cada container.
- Os ajustes guiados por aviso valem para todas as arquiteturas que usam o sistema avisado.

**Estado.** Aceita.

**Revisão do item 4 (09/11/2026, ADR-067, item 3): `max_wal_size` = 8GB nos três PostgreSQL.** No piloto, com o padrão (1 GB), o PostgreSQL da fila consolidada avisou "checkpoints are occurring too frequently" de 22 a 25 vezes por execução, com c = 8. A busca rodou dentro do protocolo, com 32 produtores e 32 consumidores, contando os avisos da execução inteira. Como o valor vale para os três PostgreSQL do eixo, a *outbox*, que gravou mais WAL que a consolidada nesse nível, também foi testada:

| `max_wal_size` | Consolidada: avisos (checkpoints) | Consolidada: consumo/s | *Outbox*: avisos (checkpoints) | *Outbox*: consumo/s |
|---|---|---|---|---|
| 8 GB | 0 (3) | 12.629 | 0 (4) | 10.239 |
| 4 GB | 0 (8) | 9.860 | 1 (11) | 10.570 |
| 2 GB | 11 (16) | 8.607 | 20 (23) | 10.234 |

O primeiro valor, 8 GB, veio da taxa de WAL do piloto (pico de 41 MiB/s com c = 8). A consolidada passou sem aviso com 8 GB e com 4 GB e avisou com 2 GB. A *outbox* avisou com 2 GB, com intervalos de até 12 s, e uma vez com 4 GB. Esse único aviso saiu 4 s depois do fim da medição, com o gerador já parado e o relay esvaziando sozinho os 4,1 milhões de linhas que tinham sobrado na tabela de saída. A ADR-067 conta a execução inteira, e a regra foi aplicada como estava. Fica 8 GB, o menor valor sem aviso nos dois. A ingênua não foi testada à parte: no piloto, com c = 8, gravou 189 bytes de WAL por enfileiramento (cerca de 1 MiB/s, 1 checkpoint), contra 2.755 bytes da consolidada e 1.559 da *outbox*, porque o seu PostgreSQL guarda só os pedidos. O RabbitMQ não deu alarme.

**Efeito:**
- Favorece o lado PostgreSQL das três arquiteturas, com menos checkpoints e menos páginas inteiras no WAL.
- Nessas execuções únicas, o consumo da fila consolidada caiu junto com o `max_wal_size` (12,6 mil/s com 8 GB, 8,6 mil/s com 2 GB).
- O consumo da *outbox* ficou igual nos três valores, o que é coerente com o limite estar no relay (ADR-053).
- São execuções únicas, com a vazão do primeiro e do último minuto da medição diferindo em até 39% na consolidada e 62% na *outbox*, porque a fila cresce o tempo todo com c = 32. A relação entre o `max_wal_size` e a vazão não é afirmada a partir delas.
- A regra escolheu o valor pelos avisos, não pela vazão.

Janelas `20261109-1737`, `-1831`, `-1923`, `-2019` e `-2048`.

---

## ADR-056 — Verificação de equivalência do Eixo B

**Contexto.** O README do eixo e o passo 2.3.4 exigem um teste que derrube o relay no meio da carga e confirme que nenhuma mensagem se perde nas configurações com garantia. Sem ele, a equivalência entre a consolidada e a *outbox* seria só alegação.

**Decisão.**

1. Script `eixo-b-fila/cargas/equivalencia.py`, no host, com a arquitetura no ar e recém-preparada. Produtores e consumidores rodam em processos próprios.
2. Carga com 8 produtores e 8 consumidores por 60 s. Falhas provocadas com `SIGKILL`:
   - nos produtores, aos 15 s e aos 35 s, nas três configurações, com reinício 2 s depois;
   - na *outbox*, também no container do relay, aos 25 s, religado 5 s depois.
3. Depois da carga, o script espera a fila esvaziar: tabela de tarefas ou de saída vazia e fila do RabbitMQ sem mensagens prontas nem pendentes. Então compara os pedidos gravados (tabela `pedidos`, a fonte da verdade) com os identificadores que os consumidores registraram.
4. Critério:
   - na consolidada e na *outbox*, nenhum pedido sem mensagem consumida e nenhuma mensagem de pedido inexistente;
   - duplicatas contadas: zero esperado na consolidada; possíveis na *outbox*, que entrega pelo menos uma vez;
   - na ingênua, perdas esperadas, e o teste registra quantas, para demonstrar a escrita dupla.
5. Idempotência no consumidor (passo 2.3.3): a chave é o identificador do pedido (`message_id`). Como o consumo não tem efeito (ADR-051), a idempotência se reduz a reconhecer a repetição, o que o verificador faz.
6. Registro em `resultados/equivalencia/b-<arquitetura>.json`.

**Justificativa.** Matar os produtores enquanto esperam o banco reproduz o caso da escrita dupla: o PostgreSQL confirma o pedido, mas a publicação não acontece. Na consolidada e na *outbox*, o pedido e a tarefa (ou a linha de saída) entram juntos. Matar o relay entre a publicação e o commit reproduz o caso da reentrega. A tabela de pedidos como referência não depende de nenhum registro do próprio gerador.

**Efeito sobre a comparação.**
- Nenhum sobre as medições, que não usam o script.
- Os consumidores não são derrubados, porque a equivalência em questão é a da gravação com o enfileiramento.
- Numa aplicação real, o consumidor da *outbox* precisaria guardar de forma durável os identificadores já processados para descartar as repetições. Na consolidada, isso não é necessário, porque a retirada e o processamento podem estar na mesma transação. Esse custo não é medido, e a omissão favorece a *outbox*.

**Estado.** Aceita.

---

## ADR-057 — Coletor interno do RabbitMQ

**Contexto.** A ADR-021 manda coletar as estatísticas internas de todos os sistemas com a mesma frequência, e o `harness/README.md` prevê o prefixo `rmq_`, lido pela API de gerenciamento, com a profundidade de cada fila.

**Decisão.** Coletor nos núcleos do gerador, com a cadência dos outros:

1. A cada 10 s, pela API de gerenciamento:
   - por fila: mensagens prontas e pendentes de confirmação, bytes das mensagens, memória do processo da fila, e publicações, entregas, confirmações e reentregas acumuladas;
   - do nó: memória usada e alarmes de memória e de disco.
2. A cada 60 s, o tamanho do diretório de dados (`du` dentro do container), para comparar o espaço em disco da fila quorum com o das tabelas no PostgreSQL.

**Justificativa.** Cobre o que o coletor do PostgreSQL cobre: a atividade da fila, a profundidade, a memória e o espaço em disco. A documentação avisa que filas quorum podem ocupar muito espaço em disco, e o teste de longa duração precisa desse dado para comparar com a inflação das tabelas. O RabbitMQ atualiza as estatísticas da API a cada 5 s, abaixo do intervalo da coleta.

**Efeito sobre a comparação.** Neutro, pela regra da ADR-021. O `du` roda dentro do container do RabbitMQ, como as consultas de tamanho rodam dentro do PostgreSQL. O custo da coleta é medido no piloto.

**Estado.** Aceita. Testada na janela `teste-eixo-b` em 08/11/2026: todas as métricas gravadas, limite de memória lido em 4300 MiB, como configurado. O RabbitMQ 4.4.0 avisa no log que a coleta de métricas da API de gerenciamento (`management_metrics_collection`) está obsoleta, mas ainda é permitida por padrão. Se uma versão futura a desligar, o coletor passa ao endpoint Prometheus, que a imagem já traz ligado.

---

## ADR-058 — InfluxDB 1.13.1 com índice TSI

**Contexto.** O lado especializado do Eixo C é o InfluxDB, carregado e consultado pelo TSBS (ADR-008). Há três linhas mantidas: 1.x (motor TSM), 2.x (TSM, com token obrigatório) e 3 Core (Parquet). A proposta pede versões estáveis correntes. Fatos levantados em 08/11/2026 no código do TSBS (commit `8323e59`) e na documentação da InfluxData:
- o TSBS escreve por `POST /write?db=...` e consulta por `POST /query` com InfluxQL, a API da 1.x, e não envia token;
- a 2.x exige token na API de compatibilidade v1 e não aceita `CREATE DATABASE`;
- o 3 Core aceita a API v1, mas, pelo código da 3.12.1, devolve erros de consulta dentro de respostas HTTP 200, que o TSBS mede como sucesso, e responde 429 quando o buffer enche, que o TSBS trata como erro fatal. A própria InfluxData indica o Core para dados recentes: ele consulta no máximo 432 arquivos (cerca de 72 horas de dados), não compacta além do primeiro nível e encaminha consultas históricas para o Enterprise.

**Decisão.**

1. InfluxDB OSS 1.13.1, publicada em setembro de 2026 e a última com pelo menos duas semanas de publicação (a 1.13.2 é de 30/10/2026). Imagem oficial fixada por digest, nó único, sem autenticação (o padrão da 1.x), só na rede interna (ADR-026).
2. Índice TSI (`index-version = "tsi1"`), que a documentação da 1.x recomenda "para a maioria dos usuários" e para produção. O padrão do arquivo, `inmem`, é indicado por ela para conjuntos pequenos e testes.
3. Envio de estatísticas de uso desligado (`reporting-disabled = true`), pela ADR-023.

**Justificativa.** É a linha com que o TSBS funciona sem alteração e que tem o mesmo modelo de uso do outro lado: um servidor que guarda e consulta todo o histórico. Usar o 3 Core exigiria adaptar o TSBS e mediria um produto que o próprio fabricante não indica para consultas sobre o histórico.

**Efeito sobre a comparação.**
- Se o InfluxDB 3 ingerir ou consultar mais rápido, a escolha desfavorece o lado especializado. Fica declarado, com as razões acima.
- O índice TSI pode ser mais lento que o `inmem` com poucas séries; seguir a recomendação do fabricante pode desfavorecer o InfluxDB nesse caso.
- Desligar o envio de estatísticas é neutro (ADR-023).

**Estado.** Aceita. Imagem `influxdb:1.13.1@sha256:33fa5b045658ddd676f3f9a72c1f40eb92ad1582539a691cd36e578ce0a6f751`, conferida em 08/11/2026: `InfluxDB 1.13.1 (git: f0281924)`, com `reporting-disabled = false` e `index-version = "inmem"` no padrão.

---

## ADR-059 — TSBS compilado do código-fonte, com uma correção de compatibilidade

**Contexto.** O TSBS não tem versões publicadas. O último commit do `master` é de 27/05/2026 (`8323e59`, só integração contínua); a última mudança de código, de 21/09/2023. O carregador do TimescaleDB chama `create_hypertable` com o parâmetro `replication_factor`, que deixou de existir no TimescaleDB 2.14 (fim do multinó), e por isso falha com o TimescaleDB 2.31 da ADR-022. A correção está no PR #255, ainda aberto.

**Decisão.**

1. Compilar o TSBS do commit `8323e59c74027b108f4ad5ec5d3e498b0101a02e`, numa etapa de construção da imagem do gerador, com Go 1.27.2 (imagem oficial `golang:1.27.2-trixie`, fixada por digest).
2. Uma única alteração no código: o PR #255, que só reescreve a chamada de `create_hypertable` sem o `replication_factor`, guardado em `eixo-c-temporal/cargas/tsbs/pr255.patch`.
3. Compilar com `GOFLAGS=-mod=mod`. O Go atual confere o grafo de módulos com mais rigor que o Go 1.14 do `go.mod`, e o `go.sum` não tinha a entrada de uma dependência indireta (`github.com/tklauser/numcpus`). Assim o Go acrescenta essa entrada, na versão que a dependência pede, conferida no banco de checksums público, sem mudar o `go.mod`.
4. Só os seis programas usados: `tsbs_generate_data`, `tsbs_generate_queries`, `tsbs_load_timescaledb`, `tsbs_load_influx`, `tsbs_run_queries_timescaledb` e `tsbs_run_queries_influx`.

**Justificativa.** Sem a correção, o TSBS não carrega dados no TimescaleDB atual; a mudança não altera o que é gravado nem como. Fixar o commit, o compilador e o remendo torna os programas reprodutíveis.

**Efeito sobre a comparação.** Neutro: a correção vale só para a criação da hypertable e é a mesma que o mantenedor tem para revisar. O remendo fica declarado no artigo.

**Estado.** Aceita. Compilação conferida em 08/11/2026: os seis programas gerados; `go.mod` sem mudança e uma linha a mais no `go.sum`.

---

## ADR-060 — Dados do Eixo C

**Contexto.** A ADR-008 manda usar os cenários padrão do TSBS, e a ADR-025 deixa a relação entre dados e memória para a etapa de cada eixo.

**Decisão.**

1. Caso de uso `cpu-only`, que tem as mesmas consultas do `devops` e é o mais usado nas comparações publicadas com o TSBS.
2. 4.000 hosts, intervalo de 10 s, de 2016-01-01T00:00:00Z a 2016-01-04T00:00:00Z (os três dias do exemplo do README do TSBS): 103,68 milhões de linhas e 1,04 bilhão de métricas.
3. Gerados uma única vez com semente fixa, um arquivo por formato (TimescaleDB e InfluxDB), comprimidos com gzip, fora do repositório, com o SHA-256 registrado. As consultas são geradas uma vez por formato: as medidas com a semente dos dados e as de aquecimento com outra (ADR-061).
4. O teste de longa duração tem volume próprio, decidido na Etapa 3.4.

**Justificativa.** Escala e intervalo padrão evitam escolher um volume que favoreça um dos lados. Gerar uma vez separa o custo da geração da medida e garante os mesmos dados nos dois sistemas.

**Efeito sobre a comparação.** Neutro. Sem compressão, o volume ocupa cerca de 20 GB no PostgreSQL, perto dos 20 GiB do container: o fim de cada carga já acontece com o cache cheio, o que é declarado no artigo. Cada carga grava menos que os 62 GB do cache pSLC do SSD (ADR-004).

**Estado.** Aceita. Revisada em 08/11/2026, com aprovação do Léo: a versão anterior usava 1 dia (o padrão do código do TSBS). Medido em desenvolvimento com 8 trabalhadores: o dia inteiro carregava em 31 s no TimescaleDB e em 71 s no InfluxDB, e os efeitos de início pesariam na vazão. Com 3 dias, a carga dura de cerca de 90 s a 215 s nesse nível.

**Item 4 resolvido pela ADR-069, item 4 (12/11/2026):** 31,25 dias com os mesmos parâmetros, gerados durante a execução. Aprovado pelo Léo.

**Correção (10/11/2026, rampa da ADR-067).** A frase "cada carga grava menos que os 62 GB do cache pSLC" está errada. Ela tomou o tamanho da tabela (~20 GB) pelo volume gravado no SSD. Medido no coletor do host, nas 12 execuções da rampa:
- **TimescaleDB:** cada carga gravou ~75 GiB, contando os 10 s de aquecimento. Desse total, ~27 GiB foram de WAL, e o restante, dos arquivos de dados e dos índices. A compressão gravou mais 10 a 14 GiB.
- **InfluxDB:** cada carga gravou de 22 a 29 GiB.
- **Esgotamento do cache:** no TimescaleDB, o cache se esgotou em todos os níveis, por volta de 63 a 66 GiB gravados, com cerca de 80% das métricas carregadas. Depois disso, a vazão da carga caiu de 40% a 80%, com o disco em 0,3 a 0,7 GB/s, a faixa pós-cache da caracterização.

Pela ADR-004 (revisão de 24/10/2026), essa queda é reportada como custo do TimescaleDB neste hardware, porque é o volume gravado por ele que leva o disco além do cache, e não é descontada. O efeito sobre a comparação deixa de ser neutro nesse ponto: favorece o InfluxDB. Os bytes gravados por métrica entram como resultado (ADR-004, item 4, e ADR-065, item 4).

---

## ADR-061 — Carga e consultas do Eixo C

**Contexto.** O TSBS tem padrões próprios para lote, trabalhadores e compressão de rede, e a proposta define o nível de concorrência como variável independente.

**Decisão.**

1. Nível de concorrência: o número de trabalhadores do `tsbs_load` (1, 2, 4, 8, 16 e 32), como nos outros eixos, confirmado na calibração.
2. Lote de 10.000 linhas, o padrão do TSBS e dentro da faixa de 5 a 10 mil que a InfluxData recomenda.
3. Gzip ligado na escrita do InfluxDB, o padrão do TSBS e recomendação da InfluxData. O TimescaleDB recebe por `COPY`, o padrão do TSBS.
4. Consultas: todos os tipos do caso `cpu-only`, 20 por tipo em cada execução, com 1 trabalhador (o padrão do TSBS). São as mesmas 20 em todas as execuções e nas duas arquiteturas. Antes de cada tipo, uma invocação de aquecimento com 5 consultas geradas com outra semente, que não entra no resumo (`harness/README.md`).
5. Opções das consultas do TimescaleDB iguais às da carga (`--timescale-use-tags`, `--timescale-use-json` e `--timescale-use-time-bucket` nos padrões).

**Justificativa.** São os padrões da ferramenta e as recomendações do fabricante do lado especializado (ADR-008 e ADR-010).

**Efeito sobre a comparação.** Neutro: os mesmos parâmetros e as mesmas consultas nos dois lados, sem seleção de tipos. Com 20 consultas por execução, os percentis altos de uma execução saem de poucas amostras; a análise usa a média por tipo em cada execução e a dispersão entre as repetições.

**Estado.** Aceita. Revisada em 08/11/2026, com aprovação do Léo: a versão anterior previa 1.000 consultas por tipo. Medido em desenvolvimento (1 dia de dados, TimescaleDB comprimido): uma consulta de cada tipo soma 4,3 s no TimescaleDB e 24,6 s no InfluxDB, em que o `high-cpu-all` sozinho leva 12,7 s. Com 1.000 por tipo, seriam 71 min e 6,8 h de consultas por execução. Com 20, cerca de 1,5 min e 9 min. O encaixe das consultas na execução está na ADR-065.

---

## ADR-062 — Compressão do TimescaleDB depois da carga

**Contexto.** O TSBS não liga a compressão do TimescaleDB, e o InfluxDB comprime sempre (TSM). A proposta mede razão de compressão e espaço por ponto. Uma política de compressão durante a carga não funciona com os dados do TSBS: como as datas são de 2016, ela comprimiria cada chunk logo depois de criado.

**Decisão.**

1. A ingestão é medida sem compressão, como no TSBS.
2. Depois da carga, todos os chunks são comprimidos com a configuração da documentação do TimescaleDB: segmentação por `tags_id` e ordenação por `time DESC` (colunas pelas quais as consultas filtram e ordenam).
3. O tempo de compressão e o espaço antes e depois são reportados.
4. As consultas rodam sobre os dados comprimidos.

**Justificativa.** É a configuração que o mantenedor recomenda para séries temporais (ADR-010). Comprimir depois da carga evita uma interação artificial com as datas antigas do TSBS.

**Efeito sobre a comparação.** A ingestão do TimescaleDB não paga a compressão, enquanto o InfluxDB compacta em segundo plano durante a carga. Por isso o artigo compara também a soma do tempo de carga com o de compressão.

**Estado.** Aceita.

---

## ADR-063 — Configuração e memória dos sistemas do Eixo C

**Contexto.** A ADR-024 manda rodar o `timescaledb-tune` uma vez, com recursos explícitos, e dar ao InfluxDB tratamento simétrico. A ADR-025 fixa 20 GiB para o sistema sob teste.

**Decisão.**

1. Cada arquitetura tem um container: TimescaleDB com 20 GiB e `/dev/shm` de 1 GiB (ADR-028); InfluxDB com 20 GiB.
2. TimescaleDB: os valores do `timescaledb-tune` 0.19.0 (ADR-024), mais `pg_stat_statements` (ADR-021) e `timescaledb.telemetry_level = off` (ADR-023).
3. InfluxDB: o arquivo da imagem, com o índice TSI e o envio de estatísticas desligado (ADR-058). O resto no padrão: cache de 1 GB, compactações em até metade dos núcleos, `wal-fsync-delay` de 0 s e o banco `_internal` ligado (o TSBS depende dele para listar os bancos).
4. Ajustes só quando o próprio sistema avisar, pela regra da ADR-055, valendo para o sistema avisado.

**Justificativa.** Configuração de referência dos dois fabricantes, com o mesmo orçamento de memória.

**Efeito sobre a comparação.** O TimescaleDB recebe a configuração da ferramenta do fabricante, mais completa que a dos Eixos A e B (ADR-024). O InfluxDB recebe as recomendações do seu fabricante. O banco `_internal` grava estatísticas a cada 10 s dentro do InfluxDB, um custo pequeno e real dele.

**Estado.** Aceita. Na implementação, o InfluxDB passou a rodar com o usuário 1000: a imagem roda como o usuário 1500 e não ajusta o dono do volume, que o orquestrador cria com o usuário 1000. É uma mudança operacional, sem efeito no comportamento do banco.

**Revisão do item 4 (09/11/2026, ADR-067, item 3): `max_wal_size` = 32GB no TimescaleDB.** No piloto, com o valor do `timescaledb-tune` (1 GB), o TimescaleDB avisou "checkpoints are occurring too frequently" 83 vezes por execução, durante a carga. A busca rodou dentro do protocolo, com 32 trabalhadores, contando os avisos da execução inteira:

| `max_wal_size` | Avisos | Checkpoints | Carga | WAL na carga |
|---|---|---|---|---|
| 1 GB (piloto, c = 8) | 83 | 83 | 91,5 s (11,3 milhões de métricas/s) | 30,0 GiB |
| 32 GB | 0 | 1 | 105,9 s (9,8 milhões/s) | 27,5 GiB |
| 16 GB | 3 (25 a 29 s de intervalo) | 3 | 90,4 s (11,5 milhões/s) | 28,5 GiB |

O primeiro valor testado, 32 GB, veio da taxa de WAL do piloto (cerca de 330 MiB/s na carga). Como 16 GB avisou, fica 32 GB, o menor valor da grade sem aviso. O InfluxDB não deu aviso equivalente e não muda. **Efeito:** favorece o TimescaleDB, com menos checkpoints e cerca de 8% menos WAL na carga em relação ao piloto. Nessas execuções únicas, a vazão da carga não mostrou ganho: com 32 GB ela foi a menor das três. A comparação de vazão sai das repetições das execuções curtas, não daqui. Execuções `20261109-180435` e `20261109-185832`, janelas `20261109-1737` e `20261109-1831`.

---

## ADR-064 — Coletores do TimescaleDB e do InfluxDB

**Contexto.** A ADR-021 manda coletar as estatísticas internas de todos os sistemas com a mesma frequência, e o `harness/README.md` prevê os prefixos `tsdb_` e `influx_`.

**Decisão.**

1. TimescaleDB: o coletor do PostgreSQL (ADR-021) e, a cada 60 s, o coletor `tsdb_`, com o tamanho da hypertable (tabela, índices e total), o número de chunks, os chunks comprimidos e o espaço antes e depois da compressão, pelas funções do TimescaleDB.
2. InfluxDB: coletor `influx_`, a cada 10 s, pelo `/debug/vars` da 1.x (pontos escritos, erros de escrita, cache do TSM, compactações, séries e memória do processo). A cada 60 s, o tamanho do diretório de dados (`du` dentro do container), como no RabbitMQ (ADR-057). O `harness/README.md` previa o `/metrics`, que é do 2.x.

**Justificativa.** Cobre, nos dois lados, a atividade de escrita, a memória e o espaço em disco, que são as variáveis do eixo.

**Efeito sobre a comparação.** Neutro, pela regra da ADR-021. O custo da coleta é medido no piloto.

**Estado.** Aceita. Testada nas janelas `teste-eixo-c-timescaledb` e `teste-eixo-c-influxdb` em 08/11/2026: todas as métricas gravadas. Durante a compressão do TimescaleDB, uma leitura de tamanhos esbarrou nos bloqueios dos chunks e foi cancelada pelo `lock_timeout` de 1 s do coletor, que é o comportamento previsto: o coletor não espera bloqueios e lê de novo na volta seguinte.

---

## ADR-065 — Execução do Eixo C e limite do gerador

**Contexto.** O contrato do gerador recebe aquecimento e medição em segundos, mas o Eixo C mede a carga de um conjunto fixo (ADR-060), seguida da compressão (ADR-062) e das consultas (ADR-061). Medido em desenvolvimento em 08/11/2026, com 8 trabalhadores: no InfluxDB, o limite é o próprio banco (9,5 dos 12 núcleos, gerador em 1,5); no TimescaleDB, é o cliente do TSBS, que monta os `COPY` (gerador em 3,6 das suas 4 CPUs lógicas, PostgreSQL em 3,4 núcleos). Comprimir os dados com zstd em vez de gzip rendeu só 5%, e 16 trabalhadores, 12%.

**Decisão.**

1. Cada execução do Eixo C, no `executar` do gerador:
   1. carga do arquivo inteiro com N trabalhadores, lido do gzip por um pipe;
   2. no TimescaleDB, compressão de todos os chunks (ADR-062) e `ANALYZE` da tabela, como a documentação do PostgreSQL pede depois de cargas em massa; no InfluxDB, espera até nenhuma compactação estar em andamento, sem forçar a compactação completa, que o padrão só faz 4 h depois da última escrita;
   3. as consultas da ADR-061.
2. A vazão é a da série por segundo do TSBS, do fim do aquecimento ao fim da carga. No plano, `aquecimento_s` é de 10 s, e `medicao_s` é o tempo máximo da carga: se ela não terminar nesse prazo, a execução falha, porque as consultas precisam dos dados inteiros.
3. A divisão de núcleos da ADR-018 se mantém. A vazão do TimescaleDB é reportada como limite inferior sempre que o gerador passar de 85% da sua CPU com o sistema sob teste folgado, condição lida da telemetria de containers de cada execução.
4. Além da vazão, a análise reporta a eficiência de cada sistema: CPU do sistema sob teste e bytes gravados por milhão de métricas, medidas que não dependem do limite do gerador.
5. O adaptador grava `metricas-gerador.csv` (série da carga), `resumo.json` (tempos de carga, compressão e espera, espaço antes e depois e percentis das consultas, em microssegundos) e as saídas originais do TSBS em `ferramenta/`, como descreve o `harness/README.md`.

**Justificativa.** A carga do conjunto inteiro é o método padrão do TSBS e deixa os dados completos para as consultas. Dar mais núcleos ao gerador tiraria núcleos do sistema sob teste, e o InfluxDB já usa quase todos. A eficiência por métrica compara os dois sistemas mesmo quando o gerador limita um deles.

**Efeito sobre a comparação.** Quando o gerador limita o TimescaleDB, a vazão medida fica abaixo da capacidade dele, e a diferença para o InfluxDB é subestimada: o limite desfavorece o lado consolidado e fica declarado. A espera pelas compactações do InfluxDB sem a compactação completa deixa as consultas sobre arquivos de nível 1 e 2, o estado normal logo depois de uma carga; o espaço medido nesse momento pode ser maior que o final. O `ANALYZE` do TimescaleDB não tem contraparte no InfluxDB, que não usa estatísticas de planejamento.

**Estado.** Aceita. Adaptador testado em desenvolvimento em 08/11/2026, com os 3 dias de dados e 8 trabalhadores, sem protocolo:

| | TimescaleDB | InfluxDB |
|---|---|---|
| Carga | 94,8 s (10,9 milhões de métricas/s) | 211,7 s (4,9 milhões/s) |
| Depois da carga | compressão em 108 s (20,5 GB para 947 MB); `ANALYZE` em 0,1 s | espera das compactações em 4 s |
| Consultas (15 tipos × 20) | 90 s | 676 s |

Conferido também que nenhuma consulta do InfluxDB volta com erro no corpo da resposta, e que as duas bases devolvem as mesmas contagens e somas para as mesmas consultas.

**Revisão do item 3 (10/11/2026, ADR-067): trecho em que a CPU do gerador é avaliada.** Aprovada pelo Léo.

*Contexto.* Na rampa do Eixo C (janela `20261110-1455`), toda carga do TimescaleDB passou do cache pSLC por volta de 64 GiB gravados, e a vazão do resto da carga caiu de 40% a 80% (correção da ADR-060).
- **Na média da carga inteira,** o gerador ficou em 2,92 a 2,99 núcleos com 8 a 32 trabalhadores, abaixo do limite de 3,4.
- **Antes do esgotamento,** ficou em 3,43 a 3,47 núcleos, com o TimescaleDB em ~3,4 dos 12 núcleos.

A fase lenta do disco diluía a média e escondia o limite que este item descreve.

*Decisão.* A CPU do gerador, do sistema sob teste e, no Eixo C, a vazão de diagnóstico são medidas também no trecho da medição anterior aos primeiros 60 GiB gravados no SSD, contados desde o início da coleta da execução. O limiar é o mesmo da marcação do cache (ADR-067, revisão dos itens 5 e 6). O critério do gerador como gargalo usa esse trecho, com pelo menos 10 s de medição. Nas execuções que não chegam aos 60 GiB, o trecho é a medição inteira, e nada muda. A vazão reportada continua sendo a da carga inteira (item 2).

*Justificativa.* O critério existe para saber se o cliente limita o banco. Na fase em que o disco limita, o gerador espera e a sua CPU cai, então a média da carga inteira mede o disco, não o cliente. O limiar de 60 GiB já é o usado para marcar o cache e não foi escolhido para este fim.

*Efeito sobre a comparação.* Nenhum número muda. Com essa regra, o TimescaleDB com 8, 16 e 32 trabalhadores é reportado como limite inferior, o que reconhece que a vantagem dele sobre o InfluxDB está subestimada, como este item já previa. Nas execuções dos Eixos A e B, só a *outbox* com 32 clientes passa dos 60 GiB, com o gerador em ~1,1 núcleo, sem efeito. A regra vale para todos os eixos.

---

## ADR-066 — Piloto e medida do custo da coleta

**Contexto.** A Etapa 2.5 pede uma execução curta de todos os eixos para validar a coleta de ponta a ponta. A ADR-021 (item 6) manda medir no piloto o custo da coleta interna e do `pg_stat_statements`, comparando a mesma execução com e sem eles, e a ADR-025 (item 5) manda rever o limite de memória do gerador com o uso real.

**Decisão.**

1. Piloto: uma execução de cada uma das 7 arquiteturas, com 8 clientes e as durações reais (120 s de aquecimento e 300 s de medição nos Eixos A e B; a carga inteira no Eixo C, ADR-065), numa janela própria (`harness/planos/piloto.toml`).
2. Custo da coleta: a fila consolidada do Eixo B com 8 clientes, que é a arquitetura com mais comandos por segundo no PostgreSQL. São 3 execuções com coleta e 3 sem, sorteadas em blocos com a mesma semente por repetição, numa janela própria (`harness/planos/custo-coleta.toml`).
3. Sem coleta interna, o orquestrador não inicia os coletores internos, não zera nem captura o `pg_stat_statements` e, logo depois de subir cada PostgreSQL, aplica `pg_stat_statements.track = none` com `ALTER SYSTEM` e `pg_reload_conf()`. A biblioteca continua carregada. A telemetria de host e containers continua, porque não passa pelo sistema sob teste. A opção vale por configuração do plano (`coleta_interna = false`), e o identificador dessas execuções termina em `_semcoleta`.
4. Ordem: o piloto vem antes da calibração (Etapa 3.1).

**Justificativa.** O pior caso para o custo da observação é o PostgreSQL com o maior número de comandos curtos por segundo: se o custo for pequeno ali, é pequeno nos demais. Desligar o rastreamento remove o trabalho do `pg_stat_statements` a cada comando sem precisar mudar as composições. O `ALTER SYSTEM` grava no diretório de dados, que é restaurado antes de cada execução (ADR-029), então não passa de uma execução para outra.

**Efeito sobre a comparação.** Nenhum sobre as medições: o piloto não entra na análise. Com a biblioteca carregada e o rastreamento desligado, sobra o custo da chamada ao módulo, que é desprezível; a medida pode subestimar o custo de carregar a biblioteca, o que fica declarado.

**Estado.** Aceita. Opção testada em 08/11/2026 com a arquitetura de teste do harness (eixo `t`): a execução sem coleta gravou só a telemetria de host, e o log do PostgreSQL registrou `pg_stat_statements.track` em `none`. A execução seguinte, com coleta, teve o coletor e o `pg_stat_statements` normais, sem herdar o `ALTER SYSTEM`, porque os dados são restaurados.

**Execução (08 e 09/11/2026).**
- **Piloto** (janela `20261108-1846`, 3 h 16 min): as 7 execuções terminaram `ok`, sem erros no gerador. Todos os coletores gravaram séries completas, e os resultados repetem os de desenvolvimento.
- **Custo da coleta** (janela `20261108-2233`, 2 h 43 min): as 6 execuções terminaram `ok`. O resultado está na ADR-021, e a revisão da memória do gerador, na ADR-025.

---

## ADR-067 — Calibração

**Contexto.** A Etapa 3.1 determina o ponto de saturação de cada arquitetura, para definir a faixa de concorrência das execuções curtas. Várias ADRs deixaram valores para esta etapa:
- o `ef` da busca vetorial (ADR-038, item 3);
- o `max_wal_size`, se o PostgreSQL avisar (ADR-055 e ADR-063, item 4);
- a vazão isolada do relay (ADR-053, item 5);
- o teto do aviso na fila consolidada (ADR-051, item 4);
- a confirmação dos níveis e das durações (ADR-048, ADR-054 e ADR-061).

No piloto (ADR-066), o PostgreSQL avisou "checkpoints are occurring too frequently" de 22 a 25 vezes por execução na fila consolidada e 83 vezes no TimescaleDB.

**Decisão.**

1. **Ordem:** configuração primeiro, rampas depois.
   1. `ef` e passagem de qualidade do Eixo A.
   2. `max_wal_size`.
   3. Vazão isolada do relay, já com o `max_wal_size` final.
   4. Rampas de concorrência.
   5. Proposta das execuções curtas.

   O `ef` e a qualidade não dependem de desempenho e rodam fora do protocolo. Todo o resto roda dentro dele.
2. **`ef`:**
   - **Vizinhos exatos:** produto interno das 3.452 consultas contra as 2.681.468 passagens. O cálculo é feito em float32, e os 200 melhores de cada consulta são reordenados em float64. O resultado fica gravado com hash em `eixo-a-busca/dados/`.
   - **Varredura:** de 100 em diante, de 10 em 10, nos dois sistemas, com a mesma busca vetorial do serviço:
     - no PostgreSQL, `ORDER BY embedding <#> ... LIMIT 100` com `hnsw.ef_search`;
     - no Qdrant, `query_points` com `hnsw_ef` e limite de 100.
   - **Escolha:** o menor valor com recall@100 médio de pelo menos 0,95, desde que os dois valores seguintes da grade também passem. O valor vai para o `TCC_EF_BUSCA` de cada serviço.
   - **Depois:** a passagem de qualidade das duas arquiteturas (ADR-048, item 5).
3. **`max_wal_size`:**
   - **Valores testados:** potências de 2 GB, no nível mais pesado da rampa (32), contando os avisos da execução inteira.
   - **Busca:** o primeiro valor testado é estimado pela taxa de WAL medida. Se a execução avisar, o valor dobra; se não avisar, testa-se a metade. Fica o menor valor sem aviso.
   - **Onde vale:**
     - no Eixo B, nos três PostgreSQL (ADR-055), conferido também na *outbox* com 32 clientes;
     - no Eixo C, só no TimescaleDB (ADR-063).
4. **Teto do aviso:** a fila consolidada com espera por aviso nos 6 níveis do Eixo B, uma execução por nível, nas janelas da rampa do eixo. No plano, isso é a chave `espera = "aviso"`, aceita só na consolidada do Eixo B. O identificador da execução termina em `_aviso`.
5. **Rampas:**
   - **Níveis:** os das ADRs (A: 1 a 64; B e C: 1 a 32).
   - **Execuções:** cenário `calibracao`, uma repetição, protocolo completo, ordem sorteada dentro de cada janela.
   - **Durações:** as mesmas das execuções curtas.
     - A e B: 120 s de aquecimento e 300 s de medição.
     - C: carga inteira, com prazo de 3.590 s.
   - **Janelas:** quatro.
     - A.
     - B, níveis 1 a 4.
     - B, níveis 8 a 32.
     - C.

   Se uma arquitetura não saturar dentro da faixa, a ampliação vira ADR.
6. **Critérios, fixados antes das rampas:**
   - **Saturação (A e C):** o menor nível em que dobrar a concorrência aumenta a vazão em menos de 10%.
   - **Vazão sustentável (B, ADR-054, item 5):** a maior vazão de consumo num nível em que a fila não cresce. Isto é, a profundidade no fim da medição, menos a do início, fica em até 1% do que foi enfileirado na medição.
   - **Relay como gargalo (ADR-053, item 5):** vazão da *outbox* de pelo menos 90% da vazão isolada.
   - **Gerador como gargalo (A e B):** CPU do gerador acima de 1,7 núcleo, que é 85% dos seus 2 processos. Nesse caso, a revisão da ADR-048 ou da ADR-054 passa o gerador para 4 processos, e os níveis afetados são repetidos. No Eixo C vale a ADR-065.
   - **Durações:** abaixo da saturação, a vazão do primeiro e do último minuto da medição difere em menos de 5%. Se não, aquecimento ou medição são revistos.
7. **Proposta das execuções curtas:** feita com os dados da calibração.
   - **Níveis:** saem os níveis em que todas as arquiteturas do eixo já saturaram.
   - **Repetições:** dimensionadas pela variação entre repetições medida.
   - **Janelas:** divisão definida na mesma proposta.

**Justificativa.**
- A configuração vem antes da rampa para que a rampa meça a configuração das execuções curtas.
- Potências de 2 evitam ajuste fino.
- Testar o menor valor sem aviso mantém o desvio do padrão no mínimo que o próprio sistema pede.
- Os critérios são fixados antes de ver os dados para que os limiares não sejam escolhidos a favor de uma arquitetura.
- Os vizinhos exatos reordenados em float64 eliminam o erro de arredondamento na fronteira da centésima posição.

**Efeito sobre a comparação.**
- **`max_wal_size` maior:** favorece o lado PostgreSQL de cada eixo, com menos checkpoints, menos páginas inteiras no WAL e menos bytes gravados por operação ou métrica. É a recomendação do próprio sistema, no texto do aviso. O RabbitMQ e o InfluxDB não deram aviso equivalente no piloto.
- **`ef` calibrado:** remove o viés de esforço nos dois sentidos (ADR-038).
- **Teto do aviso:** entra como achado, não como configuração medida.
- **Rampas:** uma repetição só serve para escolher a faixa e não entra na análise.
- **Quatro processos no gerador, se necessários:** valeriam para todas as arquiteturas do eixo.

**Estado.** Aceita em 09/11/2026, com aprovação do Léo.

**Execução das rampas (a partir de 09/11/2026).** Análise com `harness/analise/calibracao.py`, tabelas em `resultados/processados/calibracao-*.csv`.
- **Eixo A** (janela `20261109-2139`, 14 execuções `ok`):
  - **Consolidada:** satura em 4 clientes, com ~5,5 consultas/s e o sistema sob teste em 11,9 dos 12 núcleos. O p50 vai de 434 ms com 1 cliente a 9,9 s com 64.
  - **Poliglota:** satura em 8 clientes, com ~1.010 consultas/s e também 11,9 núcleos. O p50 vai de 3,9 ms com 1 cliente a 61 ms com 64.
  - **Gerador:** no máximo 0,15 núcleo, então não é gargalo em nenhum nível.
  - **Durações:** o critério falhou na consolidada com 2 clientes, o que levou à revisão da ADR-048 (aquecimento de 300 s).
- **Eixo B, níveis 1 a 4** (janela `20261110-0402`, 12 execuções `ok`):
  - **Critérios:** nenhuma fila cresceu, o gerador ficou em no máximo 0,38 núcleo, o relay chegou a no máximo 20% da vazão isolada e não houve nível instável.
  - **Consumo com 4 clientes:** consolidada 5.681/s, *outbox* 4.170/s e ingênua 1.775/s.
  - **Teto do aviso (item 4):** 1.407/s com 1 cliente, 1.877/s com 2 e 1.873/s com 4, ou seja, satura em 2. A latência fim a fim com aviso fica em 1,7 a 2,8 ms.
  - **Latência fim a fim:** na consolidada com espera por consulta, ela dependia do transitório da partida, o que levou à revisão da ADR-054 (latência com taxa fixa).
- **Eixo B, níveis 8 a 32** (janela `20261110-0928`, 12 execuções `ok`; série completa de 1 a 32 em `calibracao-b-*.csv`):
  - **Critérios:**
    - **Consolidada:** a fila cresce em 8, 16 e 32. A vazão sustentável é de 5.681/s, com 4 clientes.
    - ***Outbox*:** 14.871/s com 16 clientes. A fila cresce em 32.
    - **Ingênua:** nenhuma fila cresceu. Com 32 clientes, consome 9.321/s, e o sistema sob teste usa 1,35 núcleo. Não saturou na faixa (item 5).
    - **Gerador:** no máximo 1,28 núcleo, na ingênua com 32 clientes.
    - **Relay:** no máximo 71% da vazão isolada, na média da medição, com 16 clientes. O critério não disparou.
  - **Teto do aviso (item 4):** 1.866 a 1.875/s de 8 a 32 clientes, o que confirma a saturação em 2. A latência fim a fim com aviso sobe de 4,9 ms com 8 clientes para 17,5 ms com 32.
  - **Consolidada saturada** (vazão e tuplas de `tarefas` a cada 30 s):
    - **Com 8 clientes:** o consumo acompanhou o enfileiramento, ~11,2 mil/s, por ~3,5 min, com a fila quase vazia e uma única retirada vazia na execução. Depois, formou-se um atraso. O consumo caiu para 8,2 a 10,0 mil/s e não voltou, e a fila terminou com 403 mil tarefas.
    - **Com 16 e 32 clientes:** nos primeiros 30 s, o consumo foi de 21,4 e 19,3 mil/s. Depois oscilou entre 7,5 e 14,7 mil/s, em oposição às tuplas mortas de `tarefas`. Foi mais alto logo depois de cada autovacuum e mais baixo quando as mortas passavam de ~900 mil. O PostgreSQL usou 8,2 e 10,5 núcleos.
    - **Disco:** latência de escrita normal, de 0,2 a 0,5 ms.
  - ***Outbox* com 32 clientes:**
    - **Relay:** os produtores enfileiraram ~25 mil/s desde o início. O relay entregou 16,2 mil/s nos primeiros 30 s, 78% da vazão isolada. Depois caiu para ~11 mil/s, com a tabela de saída passando de 4 milhões de linhas. O PostgreSQL da *outbox* ficou em ~2,5 núcleos.
    - **Critério do relay:** a média da medição foi de 10.290/s, 49% da vazão isolada, então o critério não disparou. Mesmo assim, o relay foi o componente que limitou esse nível. A vazão isolada, medida sem enfileiramento concorrente, superestima a capacidade do relay sob carga.
    - **Disco:** aos ~330 s do início, com ~63 GiB gravados (cerca de 200 MB/s em média), a latência por escrita passou de ~0,5 ms para 3,6 a 5,7 ms. O enfileiramento caiu de ~27 mil/s para 4 a 9,6 mil/s. O volume coincide com o tamanho do cache pSLC (≈62 GiB, `ambiente/README.md`), então os últimos ~90 s da execução refletem o SSD.
    - **Veredito:** a fila já crescia antes do evento do disco, então o veredito do nível não depende dele.
    - **Volume gravado:** foi a única execução do B que chegou ao tamanho do cache, com 77,6 GiB gravados na execução inteira. A seguinte foi a *outbox* com 16 clientes, com 47,6 GiB.
- **Eixo C** (janela `20261110-1455`, 12 execuções `ok`, das 14:55 às 21:18; `calibracao-c-*.csv`):
  - **InfluxDB:** satura em 8 trabalhadores, com 4,99 milhões de métricas/s, contra 5,24 milhões com 16 (+4,9%) e 5,02 milhões com 32.
    - **Limite:** é o próprio banco, com o sistema sob teste em 9,6 núcleos com 8 trabalhadores e 11,6 com 16. O gerador usa no máximo 1,56 núcleo.
    - **Durações:** variação entre o primeiro e o último minuto de no máximo 3,5%.
    - **Volume gravado:** de 22 a 29 GiB por execução.
  - **TimescaleDB:** com a carga inteira (ADR-065, item 2), 2,94 milhões de métricas/s com 1 trabalhador, 7,20 milhões com 4, 9,99 milhões com 8 e 10,0 a 10,2 milhões com 16 e 32. Pelo critério, satura em 8.
  - **TimescaleDB, cache pSLC:** todas as execuções passaram do cache, com 85,6 a 87,9 GiB gravados (correção da ADR-060).
    - **Antes do esgotamento:** a vazão foi estável, de 3,0 a 3,2 milhões/s com 1 trabalhador, ~7,9 milhões com 4 e 11,2 a 11,9 milhões com 8, 16 e 32.
    - **Depois:** caiu de 40% a 80%.
    - **Critério das durações:** falhou com 1, 2 e 4 trabalhadores (−31%, −31% e −21%), porque o último minuto cai depois do esgotamento. Com 8 ou mais, a carga dura menos de 2 min depois do aquecimento, e o critério não se calcula.
    - **Atribuição:** pela ADR-004, a queda é custo do TimescaleDB neste hardware. No Eixo C, a medição é a carga inteira (ADR-065), então não há aquecimento ou medição a rever.
  - **TimescaleDB, gerador:** na média da carga inteira, o gerador ficou em 2,92 a 2,99 núcleos com 8 a 32 trabalhadores, abaixo do limite de 3,4 (85% das 4 CPUs lógicas). Antes do esgotamento do cache, porém, ficou em 3,42 a 3,48 núcleos, com o sistema sob teste em 3,2 a 3,7 dos 12 núcleos. A média da carga inteira dilui o limite com a fase lenta do disco. É o limite do cliente do TSBS que a ADR-065 descreve. Com a revisão da ADR-065, item 3, a análise aponta o gerador como gargalo com 8, 16 e 32 trabalhadores, e a vazão do TimescaleDB nesses níveis é limite inferior. O nível 8 é o do gerador, não o do banco.
  - **Eficiência antes do esgotamento:** com 8 trabalhadores, ~0,3 núcleo do sistema sob teste por milhão de métricas/s no TimescaleDB e ~1,9 no InfluxDB.

**Revisão dos itens 5 e 6 (10/11/2026): ingênua sem saturar e cache pSLC.** Aprovada pelo Léo.

*Contexto.*
- **Ingênua:** não saturou até 32 clientes. Consumiu 9.321/s com o sistema sob teste em 1,35 núcleo. O limite é o cliente: cada produtor espera a confirmação do broker a cada mensagem (ADR-052, item 3), ~3,4 ms com 32 clientes. Pelo item 5, ampliar a faixa vira ADR.
- **Custo da ampliação:**
  - com 64 clientes, o gerador passaria de 1,7 núcleo (~137 µs de CPU por mensagem na ingênua), o que exigiria 4 processos (item 6);
  - se 64 ainda não saturasse, 128 conexões passariam do `max_connections` padrão do PostgreSQL (ADR-054, item 3).
- **Cache pSLC:** a *outbox* com 32 clientes esgotou o cache do SSD na medição. As outras execuções do B gravaram no máximo 47,6 GiB.

*Decisão.*
1. **Faixa do Eixo B:** a faixa não é ampliada. A vazão da ingênua é reportada como "pelo menos 9.321/s, sem saturar até 32 clientes", com a causa: a confirmação por mensagem limita cada produtor.
2. **Níveis do Eixo B nas execuções curtas:** pelo item 7, os 6 níveis ficam, porque a ingênua não saturou.
3. **Marcação do cache pSLC:** a análise marca toda execução que gravar mais de 60 GiB no SSD, contados no coletor do host durante a coleta inteira da execução. O limiar fica abaixo dos ≈62 GiB do cache medido com o disco vazio (`ambiente/README.md`), porque o cache diminui com o disco mais ocupado. Execuções marcadas não saem da análise. A marcação é reportada junto com o resultado e declarada como ameaça à validade. Ela não muda nenhum critério.

*Justificativa.*
- A ingênua já supera a vazão sustentável da consolidada (9.321 contra 5.681/s), então o sentido da comparação não depende da ampliação, só o tamanho da diferença. A ampliação exigiria dois desvios só para uma arquitetura.
- O limite de 32 clientes vem do padrão do PostgreSQL e dos 2 núcleos do gerador (ADR-018). É uma limitação do banco de ensaio, declarada.
- O cache pSLC é a ameaça que a caracterização do SSD apontou. A marcação permite separar os efeitos do disco dos efeitos da arquitetura sem descartar dados.

*Efeito sobre a comparação.*
- **Item 1:** subestima a ingênua e favorece a consolidada no tamanho da diferença, nunca no sentido. O mesmo vale para a *outbox* (revisão da ADR-053). As vazões sustentáveis das três arquiteturas são limites inferiores da grade de níveis dobrados. A da consolidada fica entre 5.681/s (4 clientes) e o ponto em que ela deixou de sustentar 11,2 mil/s (8 clientes).
- **Item 3:** neutro. As arquiteturas que gravam mais por mensagem chegam antes ao limite do cache, e isso é uma propriedade delas, que fica visível.

**Proposta das execuções curtas (item 7, 11/11/2026).** Aprovada pelo Léo.

*Contexto.*
- **Saturação nas rampas:**
  - A: consolidada em 4 clientes, poliglota em 8;
  - B: a ingênua não saturou até 32 (revisão dos itens 5 e 6);
  - C: InfluxDB em 8 trabalhadores; TimescaleDB em 8, com o gerador como gargalo (revisão da ADR-065, item 3).
- **Variação entre repetições:** só o piloto a mediu (ADR-066). O CV foi de 0,6% a 1,1% no consumo do B e de 1% a 2% no p99.
- **Diferenças entre arquiteturas no mesmo nível, na rampa:**
  - a menor foi de ~15%, a consolidada contra a *outbox* no B com 32 clientes (11.866 contra 10.290/s);
  - a maior foi de ~180 vezes, no A com 8 clientes.
- **Taxas do B (ADR-054, revisão dos itens 2 e 5):** a menor vazão sustentável máxima da rampa é a da consolidada, 5.681/s. As taxas são 25%, 50% e 75% dela, arredondadas para baixo na centena: 1.400, 2.800 e 4.200/s.

*Decisão.*
1. **Configurações:** 43, no cenário `curta`.

   | Grupo | Arquiteturas | Níveis | Durações | Configurações |
   |---|---|---|---|---|
   | A | consolidada, poliglota | 1, 2, 4, 8 | 300 s de aquecimento e 300 s de medição (revisão da ADR-048) | 8 |
   | B, vazão | consolidada, ingênua, *outbox* | 1, 2, 4, 8, 16, 32, laço fechado | 120 s e 300 s | 18 |
   | B, latência | as três | 32 produtores e 32 consumidores, a 1.400, 2.800 e 4.200/s | 120 s e 300 s | 9 |
   | C | TimescaleDB, InfluxDB | 1, 2, 4, 8 | carga inteira, com prazo de 3.590 s (ADR-065) | 8 |

   A fila consolidada usa a espera da composição, por consulta. A espera por aviso fica fora, porque o teto do aviso é um achado, não uma configuração medida (item 4).
2. **Repetições:** 5 por configuração.
3. **Teste:**
   - **Comparação:** Mann-Whitney exato e bilateral, por nível e por métrica, entre a consolidada e cada poliglota.
   - **Correção:** Holm dentro de cada nível, com nível de família de 0,05. São 2 comparações no B e até 4 na latência do B, com p50 e p99.
   - **Tamanho de efeito:** Â12 de Vargha-Delaney e razão das medianas.
   - **Pareamento:** o mesmo bloco usa a mesma semente nas arquiteturas (ADR-032). O pareamento entra só como descrição, pela razão entre as arquiteturas em cada bloco.
   - **Consultas do C:** rodam depois da carga, sobre os mesmos dados, e não dependem do nível. São 20 observações por tipo de consulta e arquitetura (5 repetições × 4 níveis).
4. **Janelas:** quatro, uma por grupo, cada uma com os 5 blocos completos e a ordem sorteada dentro de cada bloco (ADR-032).

   | Janela | Plano | Execuções | Tempo estimado |
   |---|---|---|---|
   | A | `curta-a.toml` | 40 | ~20 h |
   | B, vazão | `curta-b-vazao.toml` | 90 | ~41 h |
   | B, latência | `curta-b-latencia.toml` | 45 | ~20 h |
   | C | `curta-c.toml` | 40 | ~22 h |

   As estimativas vêm da duração real das execuções nas rampas. As janelas rodam em sequência, nessa ordem. Uma janela pode ser interrompida entre execuções e retomada com `--retomar` (ADR-033).

*Justificativa.*
- **Níveis:** seguem a regra do item 7, fixada antes das rampas.
- **5 repetições:** é o menor número em que o teste exato consegue rejeitar depois da correção.
  - Com 5 contra 5, o menor p bilateral é 0,0079. Isso fica abaixo do limiar de Holm para 2 comparações (0,025) e para 4 (0,0125).
  - Com 4, o menor p é 0,029, e nenhuma comparação do B passaria. Com 3, é 0,10.
- **Poder:** com CV de ~1% e diferenças de pelo menos ~15%, 5 repetições já separam as distribuições por completo. O limite é a granularidade do teste exato, não o poder.
- **Pareamento:** o Wilcoxon pareado com 5 pares tem menor p bilateral de 0,0625, então não serve como teste.
- **Blocos completos dentro da janela:** uma deriva lenta da máquina se espalha igualmente pelas arquiteturas.

*Efeito sobre a comparação.*
- **Níveis:** neutros, porque a regra foi fixada antes dos dados. As latências do A com 16 a 64 clientes e do C com 16 e 32 trabalhadores ficam só na rampa, com uma repetição.
- **5 repetições:** a variação foi medida só no piloto, em poucas configurações. A consolidada saturada do B oscila com o autovacuum (bloco do Eixo B, níveis 8 a 32) e pode variar mais entre repetições. Se a variação for maior, as diferenças pequenas podem não passar no teste. Isso vale nos dois sentidos e fica declarado.
- **Limites inferiores já declarados:** a ingênua e a *outbox* no B (revisão dos itens 5 e 6 e da ADR-053) e o TimescaleDB com 8 trabalhadores no C (revisão da ADR-065, item 3). Eles favorecem a consolidada no B e o InfluxDB no C, sempre no tamanho da diferença.
- **Volume no SSD:** as execuções do TimescaleDB passam de 60 GiB e são marcadas (revisão dos itens 5 e 6). Pela ADR-004, a queda depois do cache é custo dele.

**Complemento do item 7, teste (19/11/2026): métricas testadas e famílias de Holm.** Aprovado pelo Léo em 19/11/2026, antes de qualquer teste sobre os dados.

*Contexto.* O item 7 fixou o teste "por nível e por métrica", com Holm dentro de cada nível, e o tamanho das famílias do B. Ficaram em aberto:
- quais métricas do A e do C entram no teste;
- a família das consultas do C, que não dependem do nível;
- o que é só descrição.

O tamanho da família muda o limiar de cada comparação. Por isso a lista precisa ser fixada antes de ver os resultados, como os critérios da calibração (item 6). Até aqui só foram vistos os resumos preliminares de cada janela, sem nenhum teste.

*Decisão.* Implementada em `harness/analise/curtas.py`, ainda sem rodar.

| Grupo | Métricas testadas, consolidada contra cada poliglota | Família de Holm |
|---|---|---|
| A | vazão, p50 e p99 da busca híbrida (ADR-048: "pela vazão e pela latência em cada nível") | 3 por nível |
| B, vazão | consumo por segundo | 2 por nível (item 7) |
| B, latência | p50 e p99 fim a fim | 4 por taxa (item 7) |
| C, carga | vazão da carga inteira (ADR-065, item 2); núcleos-segundo do sistema sob teste por milhão de métricas; MiB gravados no SSD por milhão de métricas, os dois na carga (ADR-065, item 4) | 3 por nível |
| C, consultas | média de cada tipo em cada execução (ADR-061), 20 contra 20 | os 15 tipos |

1. **Significância:** p ajustado de Holm de no máximo 0,05.
2. **Descritivas:** todo o resto, com mediana, mínimo e máximo por configuração (ADR-070, item 5). Inclui o p95, a latência fim a fim em laço fechado, a CPU, o crescimento da fila, as retiradas vazias, o desvio da taxa, a vazão do C antes dos 60 GiB e o volume gravado.
3. **Marcas:** contadas por configuração. São a execução acima de 60 GiB gravados (revisão dos itens 5 e 6), o gerador como gargalo (item 6 e revisão da ADR-065, item 3) e a fila crescendo no B.
4. **Leitura:** o Â12 e a razão são sempre da consolidada contra a poliglota. A tabela traz também o Â12 no sentido da métrica, como a probabilidade de a consolidada ser melhor.
5. **Alcance:** a tabela marca quando o teste não alcançaria 0,05, mesmo sem empates, com o tamanho de amostra e de família da linha. Com 5 contra 5, isso só acontece numa família de 7 ou mais.

*Justificativa.*
- As métricas testadas são as que as ADRs já nomeiam como comparação de cada eixo. Nenhuma foi escolhida olhando os dados.
- No C, a eficiência é a medida que não depende do limite do gerador (ADR-065, item 4). Testá-la dá ao C uma comparação inferencial também nos níveis em que a vazão do TimescaleDB é limite inferior.
- As consultas formam uma família única porque os 15 tipos são perguntas diferentes sobre os mesmos dados. Corrigir sobre eles evita que algum tipo passe por acaso.
- Com 20 contra 20, o menor p é da ordem de 10⁻¹¹, então a família de 15 não tira poder.
- Com 5 contra 5, o menor p (0,0079) passa em famílias de 3 e 4 (limiares de 0,0167 e 0,0125).

*Efeito sobre a comparação.*
- **A e B:** neutros. As métricas são as mesmas dos dois lados, e a família de 3 no A tem o mesmo limiar para os dois sentidos.
- **C, eficiência por CPU:** tende a favorecer o TimescaleDB, pelo que a calibração mostrou (~0,3 contra ~1,9 núcleo por milhão de métricas/s).
- **C, volume gravado por métrica:** tende a favorecer o InfluxDB (22 a 29 GiB por carga, contra 86 a 89 GiB).
- **C, conjunto:** as duas medidas de eficiência entram juntas, e nenhuma é escolhida pelo resultado.
- **C, CPU por métrica com o gerador limitando:** o TimescaleDB trabalha abaixo da capacidade. A CPU por métrica pode ficar menor ou maior do que ficaria sem o limite. Isso fica declarado.
- **Consultas:** a família de 15 é conservadora e vale para os dois sentidos.

---

## ADR-068 — Carga mista: busca sob ingestão temporal

**Contexto.**
- **Pergunta:** a QS4 pergunta o impacto da escrita intensiva sobre a latência de consulta na arquitetura consolidada, onde as duas competem pelos mesmos recursos. Na proposta, é o custo específico da consolidação (seção 2).
- **Escolha da carga (12/11/2026, com o Léo):** a escrita é a ingestão do Eixo C, e a leitura é a busca do Eixo A. Na consolidada, as duas ficam no mesmo PostgreSQL. Na poliglota, ficam em motores separados, na mesma máquina e no mesmo cpuset.
- **Dados das rampas (ADR-067):**
  - **Busca:** a consolidada satura em 4 clientes. Com 2, usa 5,9 dos 12 núcleos, e a poliglota usa 3,9.
  - **Ingestão:** o InfluxDB satura em 4,99 milhões de métricas/s. Por métrica, o TimescaleDB grava ~77 bytes no SSD (correção da ADR-060) e o InfluxDB, de 22 a 30. O InfluxDB gasta ~6 vezes mais CPU por métrica que o TimescaleDB.
  - **Memória do InfluxDB:** a memória própria dele não passou de 0,93 GiB nas rampas com 1 a 4 trabalhadores.
- **Alternativa descartada:** a fila do Eixo B como escrita. De 1.400 a 5.700 mensagens/s, a fila consolidada usa de 0,06 a 0,25 núcleo e quase não disputa recursos com a busca.

**Decisão.**

1. **Composições:** pasta `eixo-m-mista/`, eixo `m` no harness, com duas arquiteturas.
   - **Consolidada:** um PostgreSQL de 19 GiB com o banco `busca` do Eixo A e o banco `benchmark` com TimescaleDB, mais o serviço de busca com 1 GiB, como no Eixo A.
   - **Poliglota:** OpenSearch com 9 GiB (heap de 4,5 GiB, metade do container, ADR-036), Qdrant com 8 GiB, InfluxDB com 2 GiB e o serviço de busca com 1 GiB.
   - **Divisão da poliglota:** pela regra da ADR-037, os 19 GiB dos motores são divididos na proporção da necessidade mínima medida. Os mínimos são ~4,8 GiB no OpenSearch, ~4,4 GiB no Qdrant e ~1 GiB no InfluxDB. A divisão dá 8,9, 8,2 e 1,9 GiB, arredondados para 9, 8 e 2 GiB. Cada motor fica com pelo menos o dobro do seu mínimo.
   - **Estado inicial:** cópia do estado inicial do Eixo A (volumes do PostgreSQL, do OpenSearch e do Qdrant), com o banco `benchmark` vazio criado como no Eixo C:
     - no PostgreSQL, com as extensões `timescaledb` e `pg_stat_statements`;
     - no InfluxDB, com o banco vazio.

     É restaurado antes de cada execução (ADR-029).
2. **PostgreSQL da consolidada:** a configuração do Eixo A (ADR-042) somada ao que o TimescaleDB exige e ao que foi calibrado para a ingestão.
   - **Do Eixo A:**
     - `shared_buffers` de 4864MB, `effective_cache_size` de 14592MB e `work_mem` de 4MB;
     - os padrões de paralelismo, `jit` e custos, que definem o plano das consultas da busca.
   - **Do TimescaleDB (ADR-024 e ADR-063):**
     - `shared_preload_libraries` com `timescaledb` e telemetria desligada;
     - `timescaledb.max_background_workers` = 16, `max_worker_processes` = 31 e `max_locks_per_transaction` = 512;
     - `max_wal_size` de 32GB (calibrado) e `min_wal_size` de 512MB;
     - `autovacuum_max_workers` = 10 e `autovacuum_naptime` = 10.
   - **Fora:** os parâmetros de sessão do `timescaledb-tune` (`work_mem`, paralelismo, custos, `jit` e estatísticas). Eles valem para consultas, e, na carga mista, o banco `benchmark` só recebe inserções.
3. **Escrita:** ingestão do TSBS em taxa fixa.
   - **Dados:** o arquivo das execuções curtas do C (ADR-060), lido desde o início em cada execução.
   - **Taxas:** 250 mil, 500 mil e 1 milhão de métricas/s, ou 25, 50 e 100 mil pontos/s. São ~5%, 10% e 20% da vazão de saturação do InfluxDB.
     - **Teto:** 1 milhão é a maior dobra que cabe no arquivo durante a execução inteira, com ~620 milhões de 1,04 bilhão de métricas.
     - **Disco:** no TimescaleDB, 1 milhão/s grava ~48 GB, abaixo do limiar de 60 GiB da revisão da ADR-067.
   - **Envio:** um alimentador entrega os pontos ao `tsbs_load` no ritmo da taxa, em fatias de 10 ms. É a agenda uniforme da ADR-054.
     - O `tsbs_load` usa 4 trabalhadores e lotes de 10.000 pontos (ADR-061).
     - Com 4 trabalhadores, a capacidade medida na rampa é 3,6 vezes a maior taxa no InfluxDB e 7,2 vezes no TimescaleDB.
   - **Duração:** a ingestão começa 10 s antes do aquecimento da busca e para no fim da medição.
   - **Validade:** na medição da busca, a taxa obtida fica a menos de 1% da planejada. Se não ficar, a execução é reportada como taxa não sustentada pela arquitetura.
4. **Leitura:** a busca do Eixo A com 2 clientes, 300 s de aquecimento e 300 s de medição (revisão da ADR-048).
   - **Nível:** 2 é o maior nível das execuções curtas abaixo da saturação das duas arquiteturas.
   - **Semente:** é a mesma em todas as taxas e nas duas arquiteturas de um bloco, então as mesmas consultas são comparadas. No eixo `m`, a taxa não entra na semente.
5. **Configurações e janela:**
   - 2 arquiteturas × 4 taxas (sem escrita, 250 mil, 500 mil e 1 milhão) = 8 configurações;
   - 5 repetições em blocos (ADR-032);
   - uma janela, `harness/planos/mista.toml`, com 40 execuções e ~21 h.
6. **Análise:**
   - **Medida principal:** p50, p95 e p99 da busca e a vazão, na medição.
   - **Dentro de cada arquitetura:** Mann-Whitney exato e bilateral de cada taxa contra a configuração sem escrita, para p50 e p99. A correção de Holm cobre as 6 comparações da arquitetura. Com 5 contra 5, o menor p (0,0079) fica abaixo do primeiro limiar (0,0083).
   - **Entre arquiteturas:**
     - A degradação de cada execução é a razão entre a latência dela e a mediana da mesma arquitetura sem escrita.
     - Teste: Mann-Whitney exato das degradações, consolidada contra poliglota, em cada taxa, para p50 e p99. A correção de Holm cobre as 6 comparações.
     - Tamanho de efeito: Â12 e razão das medianas das degradações.
   - **Diagnóstico:**
     - CPU e memória de cada container, bytes lidos e gravados no SSD;
     - tuplas e autovacuum do banco `benchmark`;
     - taxa obtida e atraso da ingestão.
   - **Coletores:** no PostgreSQL da consolidada há um coletor por banco (`busca` e `benchmark`), com o arquivo de saída nomeado pelo banco.
7. **Código:**
   - **Imagens:** `tcc-gerador-mista:1`, com o gerador do Eixo A, o TSBS e o alimentador da versão 2 do gerador do C (`tcc-gerador-temporal:2`).
   - **Harness:** eixo `m`, chave `taxas` em métricas/s nos eixos `m` e `c` (no `c`, só no cenário `longa`) e semente sem a taxa no eixo `m`.
   - **Quando:** as mudanças no harness e nos arquivos de composição dos Eixos B e C são aplicadas depois da janela das curtas do C, que ainda lê os atuais.

**Justificativa.**
- **Ingestão em vez de fila:** a QS4 fala de escrita intensiva. A ingestão disputa cache, WAL e disco, justamente o que a consolidação compartilha e a poliglota separa.
- **Taxa fixa:** as duas arquiteturas recebem a mesma escrita. Em laço fechado, cada uma receberia a escrita que conseguisse aceitar, e a interferência ficaria confundida com a vazão.
- **Base na mesma composição:** a configuração sem escrita mede a mudança de composição (memória da poliglota e configuração do PostgreSQL), e a degradação fica só com a interferência.
- **Taxas em dobras:** seguem o padrão dos níveis de concorrência. O teto vem do arquivo e do cache do SSD, não de uma escolha.

**Efeito sobre a comparação.**
- **Escrita escolhida:** desfavorece a consolidada. É a escrita que mais disputa recursos com a busca dentro do mesmo servidor, e a QS4 foi formulada para expor essa desvantagem.
- **Divisão da poliglota:** tira 1 GiB de cada motor de busca, o que pode aumentar a latência da poliglota mesmo sem escrita. Isso fica medido na base e não entra na degradação. Na consolidada, o PostgreSQL divide os seus 19 GiB entre as duas cargas sem limite fixo.
- **Configuração do PostgreSQL:** o `max_wal_size` de 32 GB e o autovacuum do TimescaleDB favorecem a consolidada sob ingestão, com menos checkpoints. São os valores calibrados e recomendados para o TimescaleDB. O InfluxDB fica com a configuração do Eixo C (ADR-063).
- **Recursos disputados:** são diferentes nas duas arquiteturas.
  - Na poliglota, a ingestão disputa mais CPU, porque o InfluxDB gasta ~6 vezes mais por métrica.
  - Na consolidada, disputa mais cache e disco, porque o TimescaleDB grava ~3 vezes mais bytes por métrica.

  O desenho mede as duas disputas e não escolhe uma.

**Estado.** Aceita em 12/11/2026, com aprovação do Léo.

---

## ADR-069 — Longa duração

**Contexto.**
- **Proposta:** a seção 4.2 e a QS2 pedem uma execução contínua de várias horas, com crescimento de tuplas mortas, atividade de autovacuum, inflação de tabela e índice e deriva de latência. As objeções ao PostgreSQL como fila e como armazenamento temporal são sobre o comportamento ao longo do tempo.
- **ADRs anteriores:**
  - estado contínuo (ADR-013);
  - uma execução por arquitetura, em janelas próprias (ADR-032);
  - `pgstattuple_approx` a cada 5 min (ADR-021, item 4);
  - controle com `fio` pelo mesmo tempo (ADR-004, item 2);
  - volume próprio do Eixo C (ADR-060, item 4).
- **Decisões de 12/11/2026:**
  - com o Léo: Eixos B e C, e 12 h por execução;
  - com o orientador: a ingênua fica fora (revisão da ADR-006).
- **Consumo de transações na fila, a 2.800 mensagens/s:**
  - **Consolidada:** 2 transações por mensagem, uma no enfileiramento e outra no `DELETE`. Uma retirada vazia não consome identificador. A consolidada chega ao `autovacuum_freeze_max_age` (200 milhões) em ~9,9 h, e o PostgreSQL força o congelamento.
  - ***Outbox*:** ~1 transação por mensagem, mais uma por lote do relay. Chega ao mesmo limite em ~19,8 h.

**Decisão.**

1. **Execuções:** quatro, uma por arquitetura:
   - fila consolidada;
   - *outbox*;
   - TimescaleDB;
   - InfluxDB.

   Todas no cenário `longa`, num plano só, `harness/planos/longa.toml`, com a ordem sorteada pela semente do plano (ADR-032). O estado é restaurado só antes de cada execução (ADR-013).
2. **Duração:** 12 h de medição em todas.
3. **Eixo B:**
   - **Carga:** 32 produtores e 32 consumidores em taxa fixa de 2.800 mensagens/s, com a agenda uniforme da ADR-054. É 50% da menor vazão sustentável máxima (5.681/s) e a taxa intermediária da latência nas curtas.
   - **Durações:** 120 s de aquecimento e 43.200 s de medição. São ~121 milhões de mensagens. A tabela `pedidos` cresce durante todo o teste, porque o dado de negócio fica.
   - **Coleta:** a das curtas, mais:
     - `pgstattuple_approx` a cada 5 min (ADR-021, item 4);
     - a idade das transações a cada 60 s: `age(datfrozenxid)` de cada banco e `age(relfrozenxid)` de cada tabela.
4. **Eixo C:**
   - **Carga:** ingestão contínua em taxa fixa de 250 mil métricas/s (25 mil pontos/s), com o alimentador da ADR-068 e 4 trabalhadores. É ~5% da saturação do InfluxDB e a menor taxa da carga mista.
   - **Dados:** gerados durante a execução pelo `tsbs_generate_data`.
     - **Parâmetros:** os da ADR-060 (`cpu-only`, 4.000 hosts, 10 s, semente 20261108, a partir de 2016-01-01), até 2016-02-01T06:00:00Z.
     - **Volume:** 1,08 bilhão de pontos e 10,8 bilhões de métricas, em 12 h de ingestão. É o volume próprio da ADR-060, item 4, ~10 vezes o das curtas.
     - **Conferência:** antes da janela, confere-se que os 3 primeiros dias são os mesmos pontos do arquivo das curtas.
   - **Consultas periódicas:** a cada 30 min, um lote com 5 consultas de cada um dos 15 tipos (ADR-061), com 1 trabalhador.
     - As consultas são geradas na hora sobre o intervalo já carregado, com uma semente por lote, igual nos dois sistemas.
     - São 24 lotes.
   - **Depois da ingestão:**
     - **TimescaleDB:** fica sem compressão durante a ingestão, como nas curtas (ADR-062). No fim, todos os chunks são comprimidos e roda o `ANALYZE`.
     - **InfluxDB:** espera as compactações pararem, com prazo de 2 h.
     - **Bateria final:** a das curtas, com os mesmos arquivos (20 consultas medidas e 5 de aquecimento por tipo), para comparar as mesmas consultas com 10 vezes mais dados.
   - **Prazo:** se a ingestão atrasar, a execução continua até 13 h, e o que não foi carregado fica registrado.
   - **Coleta:** a das curtas, mais a idade das transações no TimescaleDB. Não há `pgstattuple_approx`.
5. **Controle do SSD (revisão da ADR-004, item 2):** `fio` por 12 h, depois das quatro execuções.
   - **Padrão:** escrita aleatória de 8 KiB, o `rand8k` da caracterização, num arquivo de 64 GiB.
   - **Taxa:** limitada à maior vazão média de escrita no SSD entre as quatro execuções, medida no coletor do host.
   - **Protocolo:** o das execuções, com `fstrim`, repouso de 1200 s e telemetria do host.
   - **Por que revisar:** a redação original pede "a maior taxa do piloto". Essa taxa é a da carga do TimescaleDB, de centenas de MB/s, e não representa as longas, que gravam dezenas de MB/s. Em 12 h, ela gravaria ~15 TB.
6. **Análise:** descritiva, sem teste, como manda a proposta (seção 6.2), com séries por minuto.
   - **Eixo B:**
     - enfileiramento, consumo e profundidade da fila;
     - latência fim a fim (p50 e p99);
     - tuplas vivas e mortas, autovacuum (contagem e tempo), tamanho de tabelas e índices, `pgstattuple`;
     - WAL, checkpoints e idade das transações;
     - CPU, memória e disco.
   - **Eixo C:**
     - taxa obtida e atraso da ingestão;
     - latência de cada lote de consultas por tipo;
     - tamanhos (hypertable e TSM), autovacuum dos chunks e compactações;
     - CPU, memória e disco.
   - **Deriva:** inclinação por hora da latência e da vazão, e a primeira hora contra a última.
   - **Atribuição de quedas ao disco:** pelo critério da ADR-004, item 3.
7. **Tempo e disco:**
   - **Tempo:** ~53 h para as quatro execuções (~12,7 h em cada uma do B e ~13,5 h em cada uma do C, com repouso e pós-carga), mais ~12,5 h para o controle.
   - **Espaço:** no fim da ingestão, o TimescaleDB ocupa ~205 GB sem compressão, mais até 32 GB de WAL.
   - **Volume gravado:** ~6 a 7 TB no total, entre as quatro execuções e o controle. O SMART registra o volume em cada janela.

**Justificativa.**
- **2.800/s:** fica abaixo da capacidade das duas arquiteturas do B. Assim, a deriva aparece como mudança de latência e de recursos, não como saturação imediata. É também a taxa já medida nas curtas.
- **12 h:** inclui o primeiro congelamento forçado previsto na consolidada.
- **250 mil/s no C:** o limite é o disco. Sem compressão, o TimescaleDB ocupa ~19 bytes por métrica, e o volume de 12 h cabe com folga nos ~730 GiB livres. Taxas maiores não caberiam.
- **TimescaleDB sem compressão durante a ingestão:** a política de compressão decide pela idade dos dados em relação ao relógio (`now()`), e os dados do TSBS são de 2016. Qualquer política comprimiria todos os chunks de uma vez, inclusive o que está recebendo dados, ou nenhum. Emular a política seria configuração própria.
- **`pgstattuple_approx` só no B:** no C, a ingestão só acrescenta linhas. Varrer ~200 GB a cada 5 min seria um custo sem pergunta a responder.
- **Dados gerados na hora:** evitam ~40 GB de arquivos. A geração dos 3 dias levou ~5 min para os dois formatos, então 25 mil pontos/s usam menos de 10% de um núcleo do gerador.
- **`rand8k` no controle:** é o padrão mais exigente da caracterização e o mais próximo da escrita de páginas dos checkpoints.

**Efeito sobre a comparação.**
- **Ingênua fora:** nenhum efeito na comparação principal, consolidada contra *outbox*.
- **2.800/s:** abaixo da saturação. A metaestabilidade da consolidada vista na rampa, que só aparece com atraso acumulado, pode não se manifestar. Uma taxa maior a desfavoreceria. Fica declarado.
- **12 h:** desfavorece a consolidada, que chega ao congelamento forçado. A *outbox* não chega.
- **Uma execução por arquitetura:** resultados descritivos, sem teste estatístico.
- **TimescaleDB sem compressão durante a ingestão:** desfavorece o TimescaleDB no espaço ocupado e, possivelmente, nas consultas sobre dados antigos. Na prática, uma política comprimiria os chunks antigos. O InfluxDB compacta durante a ingestão.
- **`pgstattuple_approx` só no B:** favorece o TimescaleDB em relação à redação original da ADR-021, porque tira dele a sonda mais cara.
- **Taxa baixa no C:** favorece os dois sistemas na vazão. O que se mede é o comportamento com o volume crescente.

**Estado.** Aceita em 12/11/2026, com aprovação do Léo.

**Complemento do item 6 (19/11/2026): definições da análise.** Aprovado pelo Léo em 19/11/2026, antes de qualquer cálculo de deriva.

*Contexto.* O item 6 lista as séries e pede a inclinação por hora e a primeira hora contra a última. Não define:
- como cada valor por minuto é formado;
- com que estimador se calcula a inclinação;
- como cada hora é resumida;
- qual estatística representa um lote de consultas;
- como o critério da ADR-004, item 3, é lido nas longas.

As escolhas mudam os números da deriva, então são fixadas antes da análise. Até a aprovação só foram vistos os acompanhamentos da coleta: CPU, atrasos da *outbox*, algumas medianas de consultas do TimescaleDB e, no repouso antes do controle com `fio`, a CPU, a memória e a escrita por hora do InfluxDB longo.

*Decisão.* Implementada em `harness/analise/longa.py`. Antes da aprovação, só o `resumir` do InfluxDB longo rodou; nenhuma inclinação foi calculada.
1. **Minutos:** contados desde o início da medição, 720 por execução.
   - **Vazões do B:** contagem de operações no minuto, dividida pela duração dele.
   - **Latência fim a fim:** p50 e p99 das mensagens consumidas no minuto.
   - **Profundidade:** mensagens enfileiradas menos consumidas desde o início do aquecimento, no fim de cada minuto. Na *outbox*, inclui a tabela de saída e a fila do RabbitMQ.
2. **Métricas dos coletores:** valem pela última amostra de cada minuto. As taxas saem da diferença entre as últimas amostras de dois minutos seguidos, dividida pelo tempo entre elas.
3. **Deriva:**
   - **Inclinação por hora:** a principal é a de Theil-Sen, a mediana das inclinações entre todos os pares de minutos. A de mínimos quadrados vai na mesma tabela, como referência.
   - **Primeira hora contra a última:** mediana dos 60 valores por minuto de cada uma, e a razão entre as duas.
4. **Tabela por hora:** mediana, mínimo e máximo dos valores por minuto em cada uma das 12 horas (ADR-070, item 5).
5. **Consultas do C:**
   - **Valor de cada lote:** a média por tipo, como nas curtas (ADR-061).
   - **Deriva:** calculada só sobre os 24 lotes periódicos, com Theil-Sen e mínimos quadrados contra a hora do lote, mais o primeiro lote contra o último.
   - **À parte:** o lote logo depois do fim da ingestão e a bateria final. A bateria final é comparada com a mediana das 20 médias das curtas da mesma arquitetura.
6. **Atribuição ao disco (ADR-004, item 3):**
   - **Execução de controle:** o `fio` passa pelas mesmas séries e pela mesma tabela de deriva das execuções (MiB/s gravados e ms por escrita).
   - **Para atribuir uma queda ao disco:** ela precisa coincidir com uma escrita sustentada perto do piso do padrão, e o controle precisa degradar na mesma tabela.
   - **Fora disso:** a queda é do banco.
   - **Registro:** cada atribuição fica escrita com os minutos e os valores.
7. **Execução abortada da B consolidada:** pode ser resumida pelo mesmo código, só para a comparação descritiva da ADR-033. Ela aparece nas tabelas com o estado `abortada` e não é usada como resultado.

*Justificativa.*
- **Theil-Sen e medianas:** descrevem a tendência sem que um pico isolado a defina. Com mínimos quadrados, poucos minutos extremos mudariam o sinal da inclinação. Os picos continuam visíveis no máximo por hora, nas séries e na inclinação de referência.
- **Média por lote:** mantém a estatística das curtas, e as duas consultas ficam comparáveis.
- **Controle pela mesma tabela:** a condição "o controle também degrada" passa a ser lida com as mesmas definições da execução, sem um limiar escolhido depois.

*Efeito sobre a comparação.*
- **Estimadores robustos:** na inclinação e na primeira hora contra a última, favorecem a arquitetura com picos isolados. Pelo acompanhamento da coleta, essa é a *outbox*, cujos atrasos coincidem com o PostgreSQL no limite de 12 GiB. A mínimos quadrados e o máximo por hora ficam ao lado para mostrar o que a mediana não mostra.
- **Média de 5 consultas por lote:** uma consulta lenta pesa nos dois sistemas da mesma forma.
- **Atribuição ao banco por padrão:** desfavorece a arquitetura que grava mais no SSD, como já declara a ADR-004.

---

## ADR-070 — Valores extremos na análise

**Contexto.** A Etapa 4.1 do plano pede tratar outliers com critério declarado. As execuções curtas e a carga mista têm 5 repetições por configuração, comparadas pelo Mann-Whitney exato com correção de Holm (ADR-067, item 7; ADR-068, item 6). A longa duração tem uma execução por arquitetura e é descritiva (ADR-069, item 6). As regras de validade já existentes não dependem do valor medido:
- `abortada`: interrupção ou ambiente inválido, inclusive o ambiente contaminado por falha externa (ADR-033 e complemento de 18/11/2026). A execução é repetida.
- `falha` e `oom`: mantidas e reportadas, nunca substituídas (ADR-033, item 1).
- Taxa não sustentada na mista: reportada como resultado da arquitetura (ADR-068, item 3).

**Decisão.**
1. Nenhuma execução válida sai da análise por ter valor extremo. Entram todas cujo último estado é `ok`.
2. Só ficam fora dos testes as execuções que as regras acima já tratam: a `abortada`, substituída pela repetição; a `falha` e a `oom`, contadas e reportadas como resultado da arquitetura; a taxa não sustentada da mista, reportada como tal.
3. Dentro de cada execução, nenhuma operação é descartada além do aquecimento. Percentis e vazão usam todas as operações da medição, de `medicao_inicio_ms` a `medicao_fim_ms`.
4. Na longa duração, as séries por minuto são reportadas sem suavização nem remoção de pontos.
5. A dispersão fica visível: as tabelas trazem mediana, mínimo e máximo das repetições, e as figuras mostram cada repetição como um ponto.

**Justificativa.**
- **O teste já é robusto:** o Mann-Whitney usa postos. Um valor extremo conta como a maior (ou a menor) posição, qualquer que seja o seu tamanho, e não arrasta o resultado como arrastaria uma média.
- **Remover decide o teste:** com 5 repetições, tirar uma deixa 4 contra 5, cujo menor p bilateral é 0,016, e 4 contra 4, cujo menor p é 0,029. Os dois ficam acima do limiar de Holm para 4 comparações (0,0125), e o segundo também para 2 (0,025). Remover um valor extremo seria escolher quais comparações podem ser significativas.
- **Regras usuais não servem com 5 pontos:** a regra de 1,5 vez o intervalo interquartil e o escore z dependem de quartis e desvios estimados com 5 valores, que mudam muito com um único valor. Escolher a regra depois de ver os dados é um grau de liberdade do pesquisador.
- **Exclusão só por evidência externa:** o complemento da ADR-033 já cobre a falha da máquina, por registros do sistema, sem olhar o resultado.

**Efeito sobre a comparação.** Neutro por desenho: a regra vale igual para todas as arquiteturas e não depende do resultado. Na prática, desfavorece a arquitetura que oscila mais, qualquer que seja, porque a oscilação fica nos dados, alarga a dispersão e pode impedir a significância. Pelo que se viu na coleta, isso inclui a consolidada saturada no Eixo B, que oscila com o autovacuum (ADR-067, item 7), e a *outbox* na longa duração, com picos de até 3,6 s no enfileiramento depois que o PostgreSQL atinge o limite de memória do container (ADR-055). É essa instabilidade que o experimento quer medir.

**Estado.** Aceita em 19/11/2026, com aprovação do Léo.

---

## ADR-071 — Indicadores de custo e complexidade (QS5)

**Contexto.** A QS5 da proposta pede a diferença de complexidade operacional e de custo entre as arquiteturas, por indicadores objetivos: pico de memória, uso de CPU, tamanho e tempo de construção de índices; número de serviços, imagens, linhas de configuração de infraestrutura e dependências; tempo de provisionamento a frio; e estimativa mensal de infraestrutura equivalente em nuvem. A proposta declara esses indicadores como substitutos imperfeitos (validade de construto). Nenhuma ADR define como cada um é contado. Memória e CPU já saem das análises das execuções. O resto depende de regras de contagem que mudam os números, por isso as regras são fixadas antes de contar.

O que já existe nos brutos:
- **Preparo:** `resultados/brutos/preparos/` registra início e fim de cada preparo, e o do Eixo A traz a duração e o tamanho de cada índice (`carga.json`). É uma medida por arquitetura, feita fora das janelas, sem repouso.
- **Subida:** cada execução registra o fim do repouso, quando a arquitetura começa a subir, e o início do aquecimento. Todos os geradores esperam os sistemas aceitarem a carga antes de começar.
- **Cache:** a restauração dos volumes (ADR-029) copia os dados sem esvaziar o cache de páginas do host, e a subida acontece com parte dos dados já em memória. Nenhuma medida "a frio", com o cache vazio, foi coletada.
- **Memória:** o orçamento é o mesmo entre as arquiteturas de cada eixo (ADR-003 e ADR-055). A poliglota o divide entre mais contêineres.

**Decisão.**
1. **Escopo:** contam só os serviços sob teste de cada arquitetura (`servicos_sob_teste` do `arquitetura.toml`). O gerador e os coletores ficam fora. O serviço de busca, que existe dos dois lados no Eixo A e na mista, entra nos dois.
2. **Complexidade, por arquitetura:**
   - **Serviços:** número de serviços sob teste.
   - **Imagens:** número de imagens distintas e, entre elas, quantas são construídas pelo projeto (com Dockerfile próprio).
   - **Linhas de configuração:** linhas não vazias e sem comentário dos blocos dos serviços sob teste no `compose.yaml`, dos arquivos de configuração montados (`rabbitmq.conf`) e dos Dockerfiles das imagens próprias. Ficam fora as linhas que só existem por controle do experimento (`extends`, `cpuset` e `memswap_limit`), o esquema SQL e o código de carga.
   - **Dependências:** pacotes Python que a aplicação importa para falar com a arquitetura (diretos) e o fechamento deles no `uv.lock` (diretos mais transitivos). No Eixo C, as duas arquiteturas usam o mesmo binário do TSBS, e a contagem não se aplica.
3. **Provisionamento:**
   - **Preparo:** do volume vazio aos dados carregados e indexados, pelo `preparo.json` cujo volume foi usado nas execuções, com a duração e o tamanho de cada índice no Eixo A. É uma medida única e descritiva. Na mista, o preparo é uma cópia do Eixo A e não entra.
   - **Subida:** do `compose up` até o gerador encontrar os sistemas prontos, em todas as execuções curtas, com mediana, mínimo e máximo por arquitetura. A medida é chamada "subida com dados restaurados", não "a frio", e a diferença fica escrita no artigo.
   - **Sem coleta nova:** o escopo fica congelado a partir de 25/11.
4. **Custo em nuvem:**
   - **Máquinas:** cada serviço sob teste vira uma máquina virtual sob demanda, a menor das famílias de uso geral e de memória da geração atual com memória igual ou maior que o `mem_limit` do contêiner.
   - **Preços:** AWS EC2, Linux, região `us-east-1`, 730 h por mês, consultados numa única data e registrados com a fonte.
   - **Disco:** gp3 pelo tamanho dos volumes de dados. No Eixo A e na mista, o tamanho é o do preparo; no B e no C, o do fim da execução longa. A ingênua, que não tem longa, fica sem disco comparável e é marcada assim.
   - **Referência:** ao lado, o custo de uma única máquina com o orçamento inteiro do eixo, igual entre as arquiteturas por construção. A diferença entre os dois mostra quanto do custo vem da divisão em várias máquinas.
   - **Serviços gerenciados** (RDS, OpenSearch Service, Amazon MQ, Qdrant Cloud, InfluxDB Cloud) ficam fora. Os modelos de preço não são comparáveis entre si, e a escolha entre eles seria um grau de liberdade a mais.
5. **Implementação:** um script em `harness/analise/` faz as contagens a partir dos arquivos versionados e dos brutos e grava uma tabela por eixo. Os preços entram num arquivo de dados com a data da consulta.
6. **Detalhes fixados na implementação (19/11/2026, antes de qualquer contagem):**
   - **`extends`:** os blocos são contados depois de resolver a herança. As linhas herdadas do modelo `postgres` (imagem, memória, `shm_size` e senha) contam, como contariam se estivessem escritas no bloco. Os modelos `comum` e `sistema-sob-teste` (reinício, rotação de logs e `cpuset`) são controle do experimento e não contam. Sem isso, os serviços do PostgreSQL pareceriam ter menos configuração do que têm.
   - **Família das instâncias:** m8a e r8a, AMD EPYC de 5ª geração (Zen 5), a mesma microarquitetura do Ryzen 7 9700X da medição. Entram os tamanhos de `medium` a `2xlarge`.
   - **Fonte dos preços:** a tabela pública de preços sob demanda que a própria página de preços do EC2 carrega, baixada depois da Fase 3 e guardada com a data. Sites agregadores servem só de conferência.
   - **Disco no B e no C:** o tamanho dos dados informado pelos coletores na última amostra da execução longa. No PostgreSQL, são as tabelas mais os índices; no TimescaleDB, o total da hipertabela; no RabbitMQ e no InfluxDB, o diretório de dados. No A e na mista, é o tamanho dos volumes medido no preparo.

**Justificativa.**
- **Contagens pelos arquivos versionados:** qualquer pessoa as refaz a partir do repositório, sem julgamento caso a caso.
- **Fora as linhas de controle do experimento:** um sistema em produção não teria `cpuset` nem `memswap_limit`. Contá-las inflaria a configuração em proporção ao número de serviços.
- **Fechamento das dependências:** o número de pacotes diretos sozinho engana, porque um cliente pode trazer dezenas de pacotes transitivos que também precisam ser mantidos e atualizados.
- **Subida das execuções:** tem 20 ou mais medidas por arquitetura, todas sob o protocolo. Uma medida a frio nova exigiria uma coleta fora do escopo congelado.
- **Uma máquina por serviço:** é a forma comum de implantar sistemas distintos e mantém o orçamento de cada contêiner. A referência de máquina única separa o efeito da divisão do efeito do orçamento, que é igual por construção.

**Efeito sobre a comparação.**
- **Contagens:** favorecem a consolidada por construção, porque ela tem menos serviços, imagens e clientes. É a diferença que a QS5 quer medir, e os indicadores são declarados como substitutos imperfeitos. Excluir as linhas de controle favorece levemente a poliglota, que teria mais delas.
- **Imagens próprias:** a consolidada também paga por imagem própria (`tcc-postgres`, com TimescaleDB e pgvector), e a *outbox* paga pelo relay.
- **Subida com cache quente:** favorece os sistemas que leem dados do disco ao subir (Qdrant, OpenSearch e InfluxDB), ou seja, a poliglota. Com o cache vazio, a subida deles tenderia a ser mais lenta. O PostgreSQL sobe sem ler as tabelas.
- **Preparo de medida única:** não sustenta comparação inferencial; é reportado como descritivo.
- **Uma máquina por serviço:** o arredondamento para os tamanhos de instância pode favorecer qualquer lado. Corrigido em 19/11/2026, ao montar a tabela de instâncias, antes de qualquer cálculo: a proposta dizia que favorecia a consolidada. As memórias das instâncias dobram a cada tamanho (4, 8, 16 e 32 GiB). O PostgreSQL de 19 ou 20 GiB da consolidada sobe para 32 GiB, e as peças menores da poliglota (10, 9, 8, 7, 2 e 1 GiB) cabem em instâncias de 16, 8 e 4 GiB. Com o orçamento de 20 GiB, que não é um tamanho comum de instância (ADR-055), a divisão tende a favorecer a poliglota. A referência de máquina única mostra o tamanho desse efeito.
- **Sem serviços gerenciados:** omite o custo operacional que eles reduzem, como cópias de segurança e atualizações. Isso pesa igual nos dois lados, mas a poliglota teria mais serviços a contratar.

**Estado.** Aceita em 19/11/2026, com aprovação do Léo, antes de qualquer contagem.

**Complemento (20/11/2026): pares `-c` na contagem de linhas e disco sem o WAL.** Aprovado pelo Léo em 20/11/2026, depois da primeira contagem e do primeiro cálculo de custo.

- **Contexto:**
  - **Pares `-c`:** o PostgreSQL recebe cada parâmetro no `command` do Compose como dois itens de lista, `- -c` e o valor, e a regra do item 2 conta os dois como linhas. O InfluxDB e o RabbitMQ escrevem cada ajuste numa linha só (variável de ambiente ou linha do `rabbitmq.conf`). Os pares são 5 no A consolidado e nas três arquiteturas do B, 12 na mista consolidada e 22 no TimescaleDB. A assimetria foi achada depois da primeira contagem.
  - **Disco sem o WAL:** pelo item 6, o disco do B e do C vem dos coletores. No PostgreSQL e no TimescaleDB, eles medem tabelas e índices, sem o WAL e sem os catálogos. No RabbitMQ e no InfluxDB, medem o diretório de dados inteiro com `du`, que inclui o WAL do InfluxDB. O tamanho do `pg_wal` em disco não foi coletado. No A e na mista, o disco é o tamanho dos volumes no preparo e inclui tudo nos dois lados.
- **Decisão:**
  1. A contagem do item 2, com cada `-c` e o valor como duas linhas, continua sendo a métrica principal, como foi registrada antes da contagem.
  2. Ao lado dela, a tabela traz uma variante de sensibilidade com cada par `-c` e o valor contados como uma linha (`linhas_total_pares_juntos`, em `custo-complexidade.csv`). O artigo reporta as duas e diz que a variante foi definida depois da primeira contagem.
  3. O disco fica como foi calculado. A exclusão do WAL é declarada abaixo e no artigo, com o limite superior do efeito pelo `max_wal_size`.
- **Justificativa:**
  - **Principal sem mudança:** trocar a métrica principal depois de ver os números, num sentido que favorece o lado do PostgreSQL, seria um grau de liberdade usado a favor da hipótese. A variante mostra o tamanho do efeito sem substituir o que foi registrado.
  - **Disco sem recálculo:** o `pg_wal` não foi medido. Estimá-lo agora exigiria escolher um valor depois de ver o resultado, e o limite superior mostra que o efeito é pequeno diante do custo das máquinas.
- **Efeito sobre a comparação:**
  - **Contagem principal:** desfavorece o lado PostgreSQL, que paga duas linhas por parâmetro. Com os pares juntos, as linhas caem de 67 para 62 no A consolidado, de 42, 54 e 79 para 37, 49 e 74 no B, de 75 para 53 no TimescaleDB e de 81 para 69 na mista consolidada. Os lados sem PostgreSQL ficam iguais. A ordem entre as arquiteturas não muda em nenhum eixo.
  - **Variante:** se fosse lida como principal, favoreceria o lado PostgreSQL; por isso fica como sensibilidade.
  - **Disco no B:** as três arquiteturas medem o PostgreSQL do mesmo modo. A única diferença é o diretório do RabbitMQ (0,06 GiB na *outbox*), e o efeito é de centavos.
  - **Disco no C:** favorece o TimescaleDB. O WAL fica limitado, de forma aproximada, pelo `max_wal_size` de 32 GB (ADR-063), o que somaria até US$ 2,56 por mês, cerca de 1% do total de US$ 233,99. Com esse limite, a diferença de US$ 0,35 a favor do InfluxDB chegaria a até ~US$ 2,90. Nenhuma conclusão da QS5 depende dessa diferença.
- **Estado:** aprovado em 20/11/2026.

**Complemento (20/11/2026): pico de memória.** Aprovado pelo Léo em 20/11/2026, antes de calcular o indicador.

- **Contexto:**
  - A proposta lista o pico de memória entre os indicadores da QS5. O contexto desta ADR diz que ele "já sai das análises das execuções", mas nenhuma análise o calcula para a QS5. A da mista grava o maior `memory.current` de cada contêiner na janela de medição, como diagnóstico da ADR-068.
  - O coletor do host (ADR-021) grava a cada amostra, por contêiner, o `memory.peak` do cgroup v2 e, do `memory.stat`, a memória anônima (`anon`), o cache de arquivos (`file`) e a memória compartilhada (`shmem`). No cgroup v2, a `shmem` faz parte do `file`, e não da `anon`.
  - A escolha do contador muda o resultado, porque o cgroup conta o cache de arquivos no limite do contêiner (ADR-003).
- **Decisão:**
  1. **Principal:** o `memory.peak` de cada serviço sob teste, pelo maior valor das amostras da execução, somado por arquitetura. O contador guarda o máximo desde a criação do contêiner e cobre a subida, o aquecimento e a medição. A soma dos picos é um limite superior do pico conjunto, porque eles podem acontecer em momentos diferentes.
  2. **Variante descritiva:** o maior valor, nas mesmas amostras, de `anon` mais `shmem` de cada serviço, somado por arquitetura. Com o swap desligado (ADR-016), é a memória que o kernel não consegue liberar.
  3. **Execuções:** as curtas e as da mista que entram nas análises delas (na mista, só as válidas), com mediana, mínimo e máximo por configuração (ADR-070, item 5). A tabela da QS5 mostra, por arquitetura, a menor e a maior mediana entre as configurações. As longas ficam fora, porque têm uma execução por arquitetura.
  4. **Implementação:** etapa `memoria` do `harness/analise/custo.py`, com `custo-memoria-execucoes.csv`, `custo-memoria-configuracoes.csv` e a tabela `custo-qs5-memoria.tex`.
- **Justificativa:**
  - **`memory.peak` como principal:** é o contador que o kernel mantém, sem escolha de quem analisa, igual para todos os sistemas. É também o número que um operador vê ao dimensionar a máquina.
  - **Variante:** mostra quanto do pico é cache, que o kernel pode liberar, e quanto é memória presa.
- **Efeito sobre a comparação:**
  - **Principal:** inclui o cache de arquivos, que o kernel enche até perto do limite do contêiner quando há leitura ou escrita em disco. Isso tende a aproximar as arquiteturas do mesmo orçamento e a comprimir as diferenças. Não favorece nenhum lado por desenho.
  - **Soma dos picos:** infla mais as arquiteturas com mais serviços, cujos picos não precisam coincidir. Favorece levemente a consolidada.
  - **Variante:** favorece a poliglota. O que os sistemas leem por mapeamento de arquivos, como os índices do OpenSearch e os arquivos TSM do InfluxDB, conta como cache e fica de fora. Já o `shared_buffers` do PostgreSQL conta como `shmem` e entra. Por isso a variante fica como descritiva.
- **Estado:** aprovado em 20/11/2026. Calculado no mesmo dia, nas 255 execuções (5 por configuração). A aproximação do limite prevista no efeito do principal não se confirmou em todos os casos: a ingênua fica entre 0,7 e 2,1 GiB, e o InfluxDB, entre 4,3 e 4,6 GiB. Só o TimescaleDB fica perto do limite, com 19,0 a 19,2 GiB. O texto do efeito fica como foi escrito antes do cálculo.

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
| 30/10/2026 | ADR-038 revisada (produto interno no `pgvector`; calibração pelo recall@100 com piso de 100). ADR-039 revisada (ramo léxico com semântica OU nos dois lados; resposta só com identificadores). ADR-028: `/dev/shm` de 9 GiB no Eixo A. ADR-041 a ADR-043: esquema e identificadores, configuração do PostgreSQL e carga do Eixo A |
| 03/11/2026 | ADR-020: controlador do P510 achado pelo modelo. ADR-036: `vm.max_map_count` já atende; digests das imagens. ADR-042: conferida na carga. ADR-044: composição, índices e estado pronto da poliglota do Eixo A. ADR-045: recomendações de carga em massa dos três sistemas. ADR-043 revisada (chave primária depois do `COPY`, `max_wal_size` na carga) |
| 03/11/2026 | ADR-037: memória e disco medidos nos dois motores da poliglota. ADR-039: diferença das listas de palavras vazias e dos radicalizadores declarada |
| 03/11/2026 | ADR-046: serviço de busca do Eixo A. ADR-037: divisão definida (10 GiB OpenSearch, 9 GiB Qdrant, 1 GiB serviço). ADR-042 revisada: PostgreSQL com 19 GiB, `shared_buffers` 4864 MB e `effective_cache_size` 14592 MB |
| 03/11/2026 | ADR-047: consultas do serviço ao PostgreSQL sem comandos preparados |
| 07/11/2026 | ADR-048: gerador de carga do Eixo A. ADR-049: coletores internos do OpenSearch e do Qdrant. ADR-032 revisada: semente da execução sem a arquitetura |
| 07/11/2026 | ADR-050 a ADR-057: operação, mensagem e esquema do Eixo B; retirada e aviso na fila consolidada; RabbitMQ; relay da *outbox*; gerador e modelo de carga; divisão da memória e configuração; verificação de equivalência; coletor do RabbitMQ |
| 08/11/2026 | ADR-051 e ADR-053 revisadas: espera por consulta a cada 100 ms no lugar do `pg_notify`, depois de medido o teto do aviso; ADR-050 ajustada |
| 08/11/2026 | ADR-052 revisada: limite de 65.536 descritores de arquivo no RabbitMQ |
| 08/11/2026 | ADR-058 a ADR-064: InfluxDB 1.13.1 com TSI, TSBS compilado com a correção do PR #255, dados, carga e consultas, compressão depois da carga, configuração e coletores do Eixo C. ADR-024 executada |
| 08/11/2026 | ADR-060 e ADR-061 revisadas (3 dias de dados; 20 consultas por tipo). ADR-065: execução do Eixo C e limite do gerador |
| 08/11/2026 | ADR-066: piloto e medida do custo da coleta |
| 09/11/2026 | ADR-021, ADR-025 e ADR-066: resultados do piloto e do custo da coleta; intervalos de coleta e limite de memória do gerador mantidos |
| 09/11/2026 | ADR-067: calibração (ordem, `ef`, `max_wal_size`, teto do aviso, rampas e critérios) |
| 09/11/2026 | ADR-038 e ADR-048: `ef` calibrado (260 no `pgvector`, 120 no Qdrant) e qualidade da busca híbrida nas duas arquiteturas |
| 09/11/2026 | ADR-055 e ADR-063 revisadas pela calibração (ADR-067, item 3): `max_wal_size` de 8 GB nos três PostgreSQL do Eixo B e de 32 GB no TimescaleDB |
| 09/11/2026 | ADR-053: vazão isolada do relay medida (20.886 mensagens/s em regime); 2 trabalhadores mantidos |
| 10/11/2026 | ADR-048 revisada pela rampa do Eixo A: aquecimento de 300 s nas execuções curtas do eixo |
| 10/11/2026 | ADR-054 revisada pela rampa do Eixo B: latência fim a fim medida com taxa fixa (32 produtores e 32 consumidores, envio agendado); ADR-050 (cabeçalho com o instante agendado) e correção na ADR-051 |
| 10/11/2026 | ADR-067: resultados das rampas dos Eixos A e B (níveis 1 a 32), com o comportamento da consolidada e da *outbox* saturadas e o esgotamento do cache pSLC na *outbox* com 32 clientes |
| 10/11/2026 | ADR-067 revisada: faixa do Eixo B mantida com a ingênua sem saturar (limite inferior) e marcação das execuções com mais de 60 GiB gravados. ADR-053 revisada: relay como gargalo da *outbox* com 32 clientes, 2 trabalhadores mantidos |
| 10/11/2026 | ADR-067: resultados da rampa do Eixo C. ADR-060 corrigida: cada carga do TimescaleDB grava ~75 GiB e passa do cache pSLC |
| 10/11/2026 | ADR-054: teste funcional do modo de taxa fixa com a imagem `tcc-gerador-fila:2`; correção do efeito dos 32 consumidores (sincronização da espera por consulta) |
| 10/11/2026 | ADR-065 revisada: CPU do gerador avaliada antes dos primeiros 60 GiB gravados no SSD; TimescaleDB com 8 a 32 trabalhadores reportado como limite inferior |
| 11/11/2026 | ADR-067, item 7: proposta das execuções curtas aprovada (43 configurações, 5 repetições, Mann-Whitney exato com Holm dentro de cada nível, quatro janelas) |
| 12/11/2026 | ADR-006 revisada com o orientador: a ingênua fica nas execuções curtas como referência de teto e fora da longa duração |
| 12/11/2026 | ADR-068 (carga mista: busca sob ingestão temporal) e ADR-069 (longa duração dos Eixos B e C, 12 h), propostas; revisões propostas das ADR-004 (item 2), ADR-021 (item 4) e ADR-060 (item 4) |
| 12/11/2026 | ADR-068 e ADR-069 aceitas, com as revisões das ADR-004 (item 2), ADR-021 (item 4) e ADR-060 (item 4) |
| 15/11/2026 | ADR-021, item 4: complemento aprovado; o coletor cria a extensão `pgstattuple` nas execuções longas do Eixo B, que nenhum esquema criava |
| 18/11/2026 | ADR-033: complemento aprovado; execução concluída com o ambiente contaminado por falha externa (GPU NVIDIA) é tratada como `abortada` e repetida, com vigia do diário do kernel durante as janelas |
| 19/11/2026 | ADR-070 (valores extremos na análise) aceita: nenhuma execução válida sai por valor extremo, nenhuma operação é cortada além do aquecimento, e as séries da longa não são suavizadas |
| 19/11/2026 | ADR-067, complemento do item 7, aprovado: métricas testadas e famílias de Holm das execuções curtas (A com 3 por nível, C com vazão e eficiência, consultas do C numa família de 15 tipos) |
| 19/11/2026 | ADR-069, complemento do item 6, proposto: definições da análise da longa (séries por minuto, Theil-Sen como inclinação principal, média por lote de consultas, controle com `fio` na mesma tabela de deriva) |
| 19/11/2026 | ADR-069, complemento do item 6, aprovado: Theil-Sen como inclinação principal, com mínimos quadrados e máximo por hora como referência; atribuição ao disco só com o controle degradando na mesma tabela |
| 19/11/2026 | ADR-071 (indicadores de custo e complexidade da QS5), proposta: contagens pelos arquivos versionados, subida com dados restaurados no lugar do provisionamento a frio, uma máquina AWS por serviço com a máquina única como referência |
| 19/11/2026 | ADR-071 aceita |
| 20/11/2026 | ADR-071: complemento aprovado; contagem de linhas mantida como principal, com variante de sensibilidade que conta cada par `-c` e o valor como uma linha; disco do PostgreSQL e do TimescaleDB sem o WAL declarado, com limite superior de US$ 2,56 por mês no Eixo C |
| 20/11/2026 | ADR-071: complemento do pico de memória aprovado, antes do cálculo; `memory.peak` do cgroup somado por arquitetura como principal e `anon` mais `shmem` como variante descritiva, nas curtas e na mista |
