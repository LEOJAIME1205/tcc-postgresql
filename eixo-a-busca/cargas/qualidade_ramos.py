"""Qualidade de cada ramo da busca híbrida (ADR-048, complemento do item 5).

Consulta os ramos léxico e vetorial direto nos motores, com o código do serviço
(`servico/consolidada.py` e `servico/poliglota.py`), e mede nDCG@10 e recall@10 dos 10
primeiros de cada ramo, com os julgamentos e a função da passagem de qualidade
(`gerador.py qualidade`). Confere, consulta a consulta, que a fusão das duas listas pela RRF do
serviço (`motor.fundir`) dá os mesmos 10 identificadores gravados em
resultados/qualidade/a-<arquitetura>.json.

Roda no host, fora das janelas e sem o protocolo, porque não mede tempo. Restaura os volumes do
estado inicial do preparo (os mesmos das execuções), sobe só os motores da arquitetura, mede e
desliga:

    uv run python eixo-a-busca/cargas/qualidade_ramos.py consolidada
    uv run python eixo-a-busca/cargas/qualidade_ramos.py poliglota [--limite N]
"""

import argparse
import asyncio
import importlib
import json
import os
import subprocess
import sys
import time
from types import ModuleType
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, cast

import numpy as np

AQUI: str = os.path.dirname(os.path.abspath(__file__))
RAIZ: str = os.path.dirname(os.path.dirname(AQUI))
SERVICO: str = os.path.join(os.path.dirname(AQUI), "servico")
for caminho in (RAIZ, SERVICO):
    if caminho not in sys.path:
        sys.path.insert(0, caminho)

from gerador import esperar, ler_consultas, motores_prontos, ndcg_e_recall, relevantes  # noqa: E402
from harness.orquestracao import dados  # noqa: E402
from harness.orquestracao.arquitetura import Arquitetura, ler_arquitetura  # noqa: E402

QUALIDADE: str = os.path.join(RAIZ, "resultados", "qualidade")
# Variáveis que o serviço recebe no compose.yaml de cada arquitetura (ADR-038: ef calibrado).
AMBIENTE: Dict[str, Dict[str, str]] = {
    "consolidada": {"TCC_EF_BUSCA": "260", "TCC_CONEXOES": "2", "PGUSER": "postgres",
                    "PGPASSWORD": "tcc", "PGDATABASE": "busca"},
    "poliglota": {"TCC_EF_BUSCA": "120", "TCC_CONEXOES": "2"},
}
# Serviço do Compose e variável de endereço de cada motor.
MOTORES: Dict[str, Tuple[Tuple[str, str], ...]] = {
    "consolidada": (("postgres", "PGHOST"),),
    "poliglota": (("opensearch", "OPENSEARCH_HOST"), ("qdrant", "QDRANT_HOST")),
}
MARGEM_GIB: float = 100.0  # a mesma do preparar do orquestrador
VETORIAL: str = "vetorial AS ("
FIM_DOS_RAMOS: str = "\n)\nSELECT coalesce("


def ramos_da_consulta(consulta: str) -> Tuple[str, str]:
    """As subconsultas léxica e vetorial da consulta da consolidada, cada uma sozinha, com o
    texto copiado da própria consulta do serviço."""
    cabeca, resto = consulta.split(",\n" + VETORIAL, 1)
    vetorial, _ = resto.split(FIM_DOS_RAMOS, 1)
    if "lexico AS (" not in cabeca or "LIMIT 100" not in vetorial:
        raise ValueError("a consulta da consolidada mudou de formato")
    return (cabeca + "\nSELECT id FROM lexico ORDER BY posicao",
            "WITH " + VETORIAL + vetorial + "\n)\nSELECT id FROM vetorial ORDER BY posicao")


def endereco(arq: Arquitetura, servico: str) -> str:
    r = subprocess.run(["docker", "inspect", "-f",
                        "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                        arq.container(servico)], capture_output=True, text=True, check=True)
    return r.stdout.strip()


def compose(arq: Arquitetura, argumentos: List[str]) -> None:
    ambiente = {**os.environ, **arq.ambiente(), "TCC_SAIDA": QUALIDADE}
    subprocess.run(["docker", "compose", "-p", arq.projeto(), "-f", arq.compose, *argumentos],
                   env=ambiente, check=True)


class Ramos:
    """Os dois ramos de uma arquitetura, pelo motor do serviço."""

    def __init__(self, arquitetura: str) -> None:
        self.arquitetura = arquitetura
        modulo: ModuleType = importlib.import_module(arquitetura)
        self.motor: Any = getattr(modulo, arquitetura.capitalize())()
        if arquitetura == "consolidada":
            self.lexico_sql, self.vetorial_sql = ramos_da_consulta(str(getattr(modulo, "CONSULTA")))

    async def abrir(self) -> None:
        await self.motor.abrir()
        prazo = time.monotonic() + 300
        while not await self.motor.pronto():
            if time.monotonic() > prazo:
                raise RuntimeError(f"{self.arquitetura}: motores sem estado pronto em 300 s")
            await asyncio.sleep(1)

    async def fechar(self) -> None:
        await self.motor.fechar()

    async def _sql(self, consulta: str, parametros: Dict[str, Any]) -> List[int]:
        async with self.motor.pool.connection() as conn:
            cur = await conn.execute(consulta, parametros)
            return [int(linha[0]) for linha in await cur.fetchall()]

    async def ramos(self, texto: str, vetor: List[float]) -> Tuple[List[int], List[int]]:
        if self.arquitetura == "consolidada":
            return (await self._sql(self.lexico_sql, {"texto": texto}),
                    await self._sql(self.vetorial_sql,
                                    {"vetor": np.array(vetor, dtype=np.float32)}))
        return (cast(List[int], await self.motor.ramo_lexico(texto)),
                cast(List[int], await self.motor.ramo_vetorial(vetor)))


def gravadas(arquitetura: str) -> Dict[str, List[int]]:
    """Os 10 identificadores da fusão de cada consulta, na passagem de qualidade de 09/11."""
    with open(os.path.join(QUALIDADE, f"a-{arquitetura}.json"), encoding="utf-8") as f:
        registro = cast(Dict[str, Any], json.load(f))
    return {str(c["consulta"]): [int(i) for i in c["ids"]]
            for c in cast(List[Dict[str, Any]], registro["por_consulta"])}


def media(valores: Sequence[float]) -> float:
    return sum(valores) / len(valores)


async def medir(arquitetura: str, limite: Optional[int]) -> Dict[str, Any]:
    motor_rrf: ModuleType = importlib.import_module("motor")
    fundir: Any = getattr(motor_rrf, "fundir")
    ids, corpos = ler_consultas()
    julgamentos: Dict[str, Set[int]] = relevantes()
    fusao = gravadas(arquitetura)
    alvos = [i for i, q in enumerate(ids) if q in julgamentos][:limite]
    ramos = Ramos(arquitetura)
    await ramos.abrir()
    por_consulta: List[Dict[str, Any]] = []
    inicio = time.monotonic()
    try:
        for n, i in enumerate(alvos, start=1):
            pedido = cast(Dict[str, Any], json.loads(corpos[i]))
            lexico, vetorial = await ramos.ramos(str(pedido["texto"]), cast(List[float], pedido["vetor"]))
            refeita = [int(par[0]) for par in cast(List[Tuple[int, float]], fundir(lexico, vetorial))]
            relevante = julgamentos[ids[i]]
            ndcg_l, recall_l = ndcg_e_recall(lexico, relevante)
            ndcg_v, recall_v = ndcg_e_recall(vetorial, relevante)
            gravada = fusao.get(ids[i], [])
            registro: Dict[str, Any] = {
                "consulta": ids[i], "lexico_ndcg_10": ndcg_l, "lexico_recall_10": recall_l,
                "vetorial_ndcg_10": ndcg_v, "vetorial_recall_10": recall_v,
                "lexico_n": len(lexico), "vetorial_n": len(vetorial),
                "fusao_igual": refeita == gravada,
                "fusao_mesmo_conjunto": set(refeita) == set(gravada),
                "fusao_ndcg_10": ndcg_e_recall(refeita, relevante)[0],
                "gravada_ndcg_10": ndcg_e_recall(gravada, relevante)[0],
                "lexico_ids": lexico[:10], "vetorial_ids": vetorial[:10]}
            if not registro["fusao_igual"]:  # listas inteiras, para achar onde a fusão diverge
                registro.update(fusao_refeita=refeita, lexico_completo=lexico,
                                vetorial_completo=vetorial)
            por_consulta.append(registro)
            if n % 500 == 0:
                print(f"  {n}/{len(alvos)} consultas, {time.monotonic() - inicio:.0f} s", flush=True)
    finally:
        await ramos.fechar()
    return {
        "arquitetura": arquitetura, "consultas": len(por_consulta),
        "lexico_ndcg_10": media([c["lexico_ndcg_10"] for c in por_consulta]),
        "lexico_recall_10": media([c["lexico_recall_10"] for c in por_consulta]),
        "vetorial_ndcg_10": media([c["vetorial_ndcg_10"] for c in por_consulta]),
        "vetorial_recall_10": media([c["vetorial_recall_10"] for c in por_consulta]),
        # Conferência da fusão: idêntica, mesmo conjunto em outra ordem (empates de pontuação,
        # como passagens com embeddings iguais) ou conjunto diferente.
        "fusao_identica": sum(1 for c in por_consulta if c["fusao_igual"]),
        "fusao_outra_ordem": sum(1 for c in por_consulta
                                 if not c["fusao_igual"] and c["fusao_mesmo_conjunto"]),
        "fusao_conjunto_diferente": sum(1 for c in por_consulta if not c["fusao_mesmo_conjunto"]),
        "fusao_ndcg_10": media([c["fusao_ndcg_10"] for c in por_consulta]),
        "gravada_ndcg_10": media([c["gravada_ndcg_10"] for c in por_consulta]),
        "duracao_s": round(time.monotonic() - inicio, 1),
        "concluido_em": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "parametros": AMBIENTE[arquitetura],
        "por_consulta": por_consulta,
    }


def main() -> int:
    analisador = argparse.ArgumentParser(description=__doc__,
                                         formatter_class=argparse.RawDescriptionHelpFormatter)
    analisador.add_argument("arquitetura", choices=tuple(AMBIENTE))
    analisador.add_argument("--limite", type=int, default=None,
                            help="só as N primeiras consultas julgadas (teste rápido)")
    args = analisador.parse_args()
    arquitetura = cast(str, args.arquitetura)
    limite = cast(Optional[int], args.limite)
    arq = ler_arquitetura("a", arquitetura)
    print(f"restaurando {arq.projeto()}: {dados.restaurar(arq, MARGEM_GIB)}", flush=True)
    servicos = [s for s, _ in MOTORES[arquitetura]]
    compose(arq, ["up", "-d", *servicos])
    try:
        os.environ.update(AMBIENTE[arquitetura])
        for servico, variavel in MOTORES[arquitetura]:
            os.environ[variavel] = endereco(arq, servico)
        if not esperar(lambda: motores_prontos(arquitetura)):
            print("os motores não aceitaram conexões a tempo", file=sys.stderr)
            return 1
        registro = asyncio.run(medir(arquitetura, limite))
    finally:
        compose(arq, ["down"])
    sufixo = "-ramos" if limite is None else f"-ramos-teste{limite}"
    with open(os.path.join(QUALIDADE, f"a-{arquitetura}{sufixo}.json"), "w", encoding="utf-8") as f:
        json.dump(registro, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"{arquitetura}: léxico nDCG@10 {registro['lexico_ndcg_10']:.4f}, vetorial "
          f"{registro['vetorial_ndcg_10']:.4f}, fusão refeita {registro['fusao_ndcg_10']:.4f} "
          f"(gravada {registro['gravada_ndcg_10']:.4f}); fusão idêntica em "
          f"{registro['fusao_identica']}, outra ordem em {registro['fusao_outra_ordem']} e "
          f"conjunto diferente em {registro['fusao_conjunto_diferente']} de "
          f"{registro['consultas']} consultas; {registro['duracao_s']} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
