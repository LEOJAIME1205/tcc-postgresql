# Ambiente de Medição

Todos os experimentos são executados em uma única máquina, especificada abaixo.
Os valores absolutos aqui obtidos dependem deste hardware; as comparações
relativas entre arquiteturas é que devem se sustentar em outros ambientes.

## Especificação

| Componente | Especificação |
|---|---|
| Processador | AMD Ryzen 7 9700X — 8 núcleos, 16 threads, arquitetura Zen 5 |
| Memória | 32 GB DDR5 |
| Armazenamento | SSD NVMe Crucial P510 1 TB, PCIe 5.0 x4, com dissipador |
| GPU | ASUS GeForce RTX 5070 |
| Placa-mãe | ASUS, com suporte a PCIe 5.0 |
| Sistema operacional | a registrar |
| Versão do Docker | a registrar |
| Versão do kernel | a registrar |

## Alocação de recursos

A divisão de núcleos entre sistema sob teste e gerador de carga está definida
em `../harness/orquestracao/` e é idêntica entre as arquiteturas comparadas em
cada eixo. Ver ADR-002.

Os limites de memória por container e os parâmetros internos de cada sistema
estão nos arquivos de composição de cada eixo. Ver ADR-003.

## Caracterização do armazenamento

Antes de qualquer coleta, o dispositivo NVMe é caracterizado com `fio` para
determinar:

1. O ponto de saturação do cache pSLC
2. A vazão de escrita sequencial e aleatória em regime permanente
3. A curva de temperatura sob escrita sustentada prolongada

Isso é necessário porque o Eixo C consiste em escrita sustentada por horas, e
uma queda de vazão originada no dispositivo poderia ser confundida com
degradação do banco de dados. Ver ADR-004.

Resultados em `caracterizacao/`.

## Monitoramento durante as execuções

Amostrados continuamente e publicados junto com os resultados:

- Frequência de CPU e temperatura dos núcleos
- Temperatura do dispositivo NVMe
- Uso de memória do host
- Taxa de leitura e escrita em disco

Ver ADR-012.

## Versões dos sistemas avaliados

Todas as imagens fixadas por digest. Ver ADR-011.

| Sistema | Versão | Digest |
|---|---|---|
| PostgreSQL | a registrar | a registrar |
| pgvector | a registrar | a registrar |
| TimescaleDB | a registrar | a registrar |
| OpenSearch | a registrar | a registrar |
| Qdrant | a registrar | a registrar |
| RabbitMQ | a registrar | a registrar |
| InfluxDB | a registrar | a registrar |
