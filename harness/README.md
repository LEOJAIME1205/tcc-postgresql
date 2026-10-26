# Harness Experimental

Infraestrutura comum aos três eixos. Construída antes das implementações para
evitar instrumentação divergente entre eles.

## Diretórios

- `orquestracao/` — composições base, definição de `cpuset` e limites de recurso
- `metricas/` — coletores de `pg_stat_*`, `pgstattuple`, `pg_stat_statements`,
  métricas dos serviços especializados e telemetria de host
- `analise/` — scripts de consolidação, tratamento estatístico e geração de figuras

## Formato de saída

Os três eixos emitem resultados no mesmo formato, de modo que os scripts de
análise sirvam a todos sem adaptação. Especificação a definir na Etapa 2.1.

## Protocolo de execução

1. Restaurar estado inicial dos volumes (exceto no teste de longa duração)
2. `sync` e `fstrim -v /`, seguidos de 20 minutos de repouso com os containers
   parados
3. Subir a arquitetura sob teste com limites e `cpuset` fixados
4. Aquecer, descartando o período inicial
5. Executar a carga, amostrando métricas continuamente
6. Coletar e encerrar
7. Repetir com ordem aleatorizada entre arquiteturas

Toda a sequência roda dentro de `powerprofilesctl launch -p performance`. A
abertura e o encerramento de cada janela de coleta, e os detalhes de cada
passo, estão em `docs/protocolo-coleta.md`.

Ver ADR-002, ADR-004, ADR-012, ADR-013, ADR-015, ADR-017 e ADR-018.
