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
2. Subir a arquitetura sob teste com limites e `cpuset` fixados
3. Aquecer, descartando o período inicial
4. Executar a carga, amostrando métricas continuamente
5. Coletar, encerrar, aguardar intervalo de resfriamento
6. Repetir com ordem aleatorizada entre arquiteturas

Ver ADR-002, ADR-012 e ADR-013.
