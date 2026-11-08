"""Coletor de métricas internas do RabbitMQ (ADR-057)."""

import argparse
import subprocess
from typing import Any, Dict, List, cast

from coleta import Leitor, Metrica, ler_json, rodar

CREDENCIAL: str = "guest:guest"  # usuário padrão da imagem, só na rede interna (ADR-052)
DIRETORIO_DADOS: str = "/var/lib/rabbitmq"
FILAS: Dict[str, str] = {
    "messages_ready": "rmq_mensagens_prontas",
    "messages_unacknowledged": "rmq_mensagens_pendentes",
    "message_bytes": "rmq_mensagens_bytes",
    "memory": "rmq_fila_memoria_bytes",
}
CONTADORES: Dict[str, str] = {
    "publish": "rmq_publicadas_total",
    "deliver_get": "rmq_entregues_total",
    "ack": "rmq_confirmadas_total",
    "redeliver": "rmq_reentregues_total",
}
NO: Dict[str, str] = {
    "mem_used": "rmq_memoria_bytes",
    "mem_limit": "rmq_limite_memoria_bytes",
    "mem_alarm": "rmq_alarme_memoria",
    "disk_free_alarm": "rmq_alarme_disco",
}


def ler_estatisticas(base: str) -> List[Metrica]:
    """Profundidade e contadores de cada fila, memória e alarmes do nó, a cada 10 s."""
    linhas: List[Metrica] = []
    for fila in cast(List[Dict[str, Any]], ler_json(base + "/api/queues/%2F", CREDENCIAL)):
        nome = str(fila["name"])
        for campo, metrica in FILAS.items():
            if fila.get(campo) is not None:
                linhas.append(("rabbitmq", metrica, nome, int(fila[campo])))
        # Os contadores só aparecem depois da primeira operação de cada tipo.
        estatisticas = cast(Dict[str, Any], fila.get("message_stats") or {})
        for campo, metrica in CONTADORES.items():
            linhas.append(("rabbitmq", metrica, nome, int(estatisticas.get(campo, 0))))
    # Logo depois da subida, o nó ainda não tem todos os campos: os ausentes ficam para a volta seguinte.
    for no in cast(List[Dict[str, Any]], ler_json(base + "/api/nodes", CREDENCIAL)):
        for campo, metrica in NO.items():
            if no.get(campo) is not None:
                linhas.append(("rabbitmq", metrica, "", int(no[campo])))
    return linhas


def tamanhos(container: str) -> Leitor:
    """Tamanho do diretório de dados, lido dentro do container a cada 60 s."""

    def ler(_base: str) -> List[Metrica]:
        try:
            r = subprocess.run(["docker", "exec", container, "du", "-sb", DIRETORIO_DADOS],
                               capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired as erro:
            raise ValueError(f"du sem resposta em {erro.timeout} s") from erro
        if r.returncode != 0:
            raise ValueError(f"du terminou com código {r.returncode}: {r.stderr.strip()}")
        return [("rabbitmq", "rmq_dados_bytes", "", int(r.stdout.split()[0]))]

    return ler


def main() -> None:
    p = argparse.ArgumentParser(description="Coletor de métricas internas do RabbitMQ")
    p.add_argument("saida", help="arquivo metricas-<serviço>.csv a criar; não pode existir")
    p.add_argument("--url", required=True, help="ex.: http://172.18.0.2:15672 (API de gerenciamento)")
    p.add_argument("--container", required=True, help="container do RabbitMQ, para o tamanho dos dados")
    args = p.parse_args()
    rodar(str(args.saida), str(args.url).rstrip("/"), ler_estatisticas, tamanhos(str(args.container)))


if __name__ == "__main__":
    main()
