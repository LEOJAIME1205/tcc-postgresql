"""Funções comuns aos testes do Eixo B que rodam no host (ADR-053 e ADR-056).

Os testes falam com os containers do projeto tcc-b-<arquitetura> pelos IPs da rede interna
e rodam o gerador.py como processos filhos, presos aos núcleos do gerador.
"""

import base64
import json
import os
import subprocess
import sys
import urllib.request
from typing import Any, Dict, List, Optional, cast

CPUS_GERADOR: List[int] = [6, 7, 14, 15]
GERADOR: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gerador.py")
SENHA_POSTGRES: str = "tcc"
CREDENCIAL_RABBITMQ: str = "guest:guest"  # usuário padrão da imagem (ADR-052)
FILA: str = "tarefas"
TABELA_PENDENTE: Dict[str, str] = {"consolidada": "tarefas", "outbox": "saida"}


def container(arquitetura: str, servico: str) -> str:
    return f"tcc-b-{arquitetura}-{servico}-1"


def docker(argumentos: List[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *argumentos], capture_output=True, text=True)


def rodando(nome: str) -> bool:
    r = docker(["inspect", "-f", "{{.State.Running}}", nome])
    return r.returncode == 0 and r.stdout.strip() == "true"


def ip(nome: str) -> str:
    r = docker(["inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", nome])
    if r.returncode != 0 or not r.stdout.strip():
        raise SystemExit(f"o container {nome} não está no ar")
    return r.stdout.strip()


def conninfo(arquitetura: str) -> str:
    return (f"host={ip(container(arquitetura, 'postgres'))} dbname=fila user=postgres "
            f"password={SENHA_POSTGRES}")


def ambiente(arquitetura: str) -> Dict[str, str]:
    """Variáveis para o gerador.py no host, com os endereços dos containers."""
    variaveis = {**os.environ, "TCC_ARQUITETURA": arquitetura,
                 "PGHOST": ip(container(arquitetura, "postgres")), "PGUSER": "postgres",
                 "PGPASSWORD": SENHA_POSTGRES, "PGDATABASE": "fila"}
    if arquitetura != "consolidada":
        variaveis["TCC_RABBITMQ"] = (f"amqp://{CREDENCIAL_RABBITMQ}@"
                                     f"{ip(container(arquitetura, 'rabbitmq'))}:5672/")
    return variaveis


def gerador(argumentos: List[str], arquitetura: str, log: Optional[str] = None) -> "subprocess.Popen[bytes]":
    """gerador.py como processo filho, com a saída num log ou herdada."""
    saida = open(log, "ab") if log else None
    return subprocess.Popen([sys.executable, GERADOR, *argumentos], env=ambiente(arquitetura),
                            stdout=saida, stderr=subprocess.STDOUT if saida else None)


def mensagens_rabbitmq(arquitetura: str) -> int:
    """Mensagens prontas e pendentes de confirmação na fila, pela API de gerenciamento."""
    pedido = urllib.request.Request(
        f"http://{ip(container(arquitetura, 'rabbitmq'))}:15672/api/queues/%2F/{FILA}")
    pedido.add_header("Authorization",
                      "Basic " + base64.b64encode(CREDENCIAL_RABBITMQ.encode()).decode())
    with urllib.request.urlopen(pedido, timeout=5) as resposta:
        fila = cast(Dict[str, Any], json.loads(resposta.read()))
    return int(fila.get("messages", 0))


def gravar_json(caminho: str, dados: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(caminho)), exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dados, f, indent=2, ensure_ascii=False)
        f.write("\n")
