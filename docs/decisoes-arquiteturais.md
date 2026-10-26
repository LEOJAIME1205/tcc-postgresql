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

## Registro de alterações

| Data | Alteração |
|---|---|
| 25/09/2026 | Criação do documento com ADR-001 a ADR-014 |
| 24/10/2026 | ADR-004 revisada com o resultado da caracterização do SSD |
| 25/10/2026 | ADR-015 a ADR-019: governador, swap, fstrim, cpuset por núcleo físico e tarefas de manutenção |
| 25/10/2026 | ADR-004: repouso de 20 minutos (recuperação completa medida em 15, mais margem de precaução). ADR-017: `fstrim` precedido de `sync` |
| 25/10/2026 | ADR-020: APST do NVMe desligado durante as coletas |
| 25/10/2026 | ADR-021: coleta de métricas internas com a mesma frequência em todas as arquiteturas |

