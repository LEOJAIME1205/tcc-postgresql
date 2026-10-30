# Dados do Eixo A

Os arquivos desta pasta não são versionados: são baixados ou gerados pelos
scripts de `eixo-a-busca/preparo/`. Este README registra o que eles produzem e
os hashes da cópia usada no trabalho.

## Reprodução

A partir da raiz do repositório:

```bash
uv run python eixo-a-busca/preparo/baixar_corpus.py
uv sync --locked --group embeddings
uv run --group embeddings python eixo-a-busca/preparo/gerar_embeddings.py
```

O primeiro script baixa a NQ do BEIR, confere o MD5 publicado e extrai três
arquivos. O segundo gera os embeddings na GPU (ADR-005 e ADR-035). Cada um
grava um registro: `nq.json` e `embeddings.json`.

## Corpus (ADR-009)

Origem: `https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/nq.zip`,
498.307.926 bytes, MD5 `d4d3d2e48787a744b6f6e691ff534307` (o publicado pelo BEIR).
Conferido em 30/10/2026.

| Arquivo | Linhas | SHA-256 |
|---|---|---|
| `nq.zip` | — | `2553bf7bfbab47b1436ca00a34bce57320e18e611fd00999a2a3a1b4714be752` |
| `nq/corpus.jsonl` | 2.681.468 | `f7e3588dd416772c9830baf67e827d49678f8d5a6ccf6e101b9ce990cc7b208c` |
| `nq/queries.jsonl` | 3.452 | `72f08f0cd333a47e393dac61cf1e56d4fb41eabf1d6ad5ca99f40250b0b491c8` |
| `nq/qrels/test.tsv` | 4.202 (cabeçalho e 4.201 julgamentos) | `6df0cd2cbbe88504b64c68f21e946a759b62c4d864225720cec256f0196e2210` |

## Embeddings (ADR-035)

`BAAI/bge-small-en-v1.5`, revisão `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`,
384 dimensões, float32, normalizados. Texto das passagens: título, espaço,
texto. Consultas com o prefixo
`Represent this sentence for searching relevant passages: `. Gerados em
30/10/2026 numa RTX 5070, em fp32 (precisão de matmul `highest`), com
sentence-transformers 6.1.0, transformers 5.20.0, torch 2.14.1 e CUDA 13.0,
em 2.053 s.

A linha *i* de cada `.npy` corresponde à linha *i* do arquivo de
identificadores de mesmo nome.

| Arquivo | Conteúdo | Bytes | SHA-256 |
|---|---|---|---|
| `passagens.npy` | 2.681.468 × 384 | 4.118.734.976 | `79f18857d21a8057aa67089ae0ed114572dff5df57f4bcbc38929c436c6b246a` |
| `passagens-ids.txt` | identificadores do corpus | 28.385.038 | `9993b33e051d4b1e874e7fff6ac31b8734f2312ef9048b7cc7db5784368c491f` |
| `consultas.npy` | 3.452 × 384 | 5.302.400 | `78d2c33d0ed47bf22aa5947b263c8a99677d94fe0bb64025e6013d119d2f650d` |
| `consultas-ids.txt` | identificadores das consultas | 29.958 | `ddba63d8d8c14f515c05515bca33fdd54fc66d0689cd914d43b73cc9b9549288` |

Conferência: 20 passagens sorteadas, recodificadas à parte, saem idênticas
às do arquivo. A busca vetorial exata dá nDCG@10 de 0,5018 e recall@10 de
0,7083 nas 3.452 consultas. O nDCG@10 é igual ao publicado no MTEB para o
modelo na NQ (50,18).

Gerar de novo em outra GPU, ou com outra versão de CUDA, pode mudar os vetores
na ordem de 10⁻⁷, e os hashes deixam de coincidir. As medições usam esta cópia.
