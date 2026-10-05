# Eixo C — Ingestão de Séries Temporais

Perfil de carga: escrita intensiva sustentada.

| Arquitetura | Composição |
|---|---|
| Consolidada | PostgreSQL com TimescaleDB |
| Especializada | InfluxDB |

O particionamento declarativo nativo foi removido do escopo por decisão do
orientador. Em consequência, a afirmação deste eixo é sobre o ecossistema
extensível do PostgreSQL, e não sobre o PostgreSQL isolado. Ver ADR-007.

## Geração de carga

Time Series Benchmark Suite, com o mesmo conjunto gerado e os mesmos cenários
em ambos os sistemas. Registra-se que a ferramenta foi originada pela empresa
mantenedora do TimescaleDB; a mitigação está descrita em ADR-008.

## Métricas

- Taxa de ingestão sustentável em pontos por segundo
- Amplificação de escrita — volume gravado em disco por ponto ingerido
- Razão de compressão e espaço ocupado
- Latência das consultas analíticas padrão do TSBS
- Deriva de vazão ao longo da execução prolongada
- Temperatura do NVMe durante toda a coleta

## Advertência sobre o armazenamento

Queda de vazão em escrita sustentada pode originar-se do cache pSLC do SSD e
não do banco de dados. O dimensionamento do conjunto de dados é escolhido para
que a comparação principal ocorra em regime permanente. Ver ADR-004 e a
caracterização em `../ambiente/caracterizacao/`.
