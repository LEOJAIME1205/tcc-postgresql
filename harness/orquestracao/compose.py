"""Comandos do Docker Compose de uma arquitetura, com a saída sempre em arquivo."""

import os
import subprocess
from typing import Dict, List

from harness.orquestracao.arquitetura import Arquitetura


class Compose:
    def __init__(self, arq: Arquitetura, saida: str, log: str) -> None:
        self.arq = arq
        self.log = log
        self.ambiente: Dict[str, str] = {**os.environ, **arq.ambiente(), "TCC_SAIDA": saida}
        self.base: List[str] = ["docker", "compose", "-p", f"tcc-{arq.eixo}-{arq.arquitetura}",
                                "-f", arq.compose]

    def _rodar(self, argumentos: List[str]) -> int:
        with open(self.log, "a", encoding="utf-8") as f:
            f.write(f"$ docker compose {' '.join(argumentos)}\n")
            f.flush()
            return subprocess.run([*self.base, *argumentos], env=self.ambiente,
                                  stdout=f, stderr=subprocess.STDOUT).returncode

    def subir(self) -> int:
        """Sobe só os serviços sob teste; o gerador roda à parte."""
        return self._rodar(["up", "-d", *self.arq.servicos_sob_teste])

    def gerador(self, argumentos: List[str]) -> int:
        """Roda o gerador com o seu usuário, para os resultados não pertencerem ao root."""
        usuario = f"{os.getuid()}:{os.getgid()}"
        return self._rodar(["run", "--rm", "--no-deps", "--user", usuario,
                            self.arq.servico_gerador, *argumentos])

    def desligar(self) -> int:
        return self._rodar(["down"])
