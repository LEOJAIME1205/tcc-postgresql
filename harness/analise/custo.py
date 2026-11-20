"""Indicadores de custo e complexidade da QS5 (ADR-071).

Quatro etapas; `memoria` lê os metricas-host.csv das curtas e da mista e leva alguns minutos:

    uv run python -m harness.analise.custo contar   # arquivos versionados, preparos e subidas
    uv run python -m harness.analise.custo nuvem    # máquinas e disco, com precos-nuvem.toml
    uv run python -m harness.analise.custo memoria  # pico de memória (depois de curtas e mista)
    uv run python -m harness.analise.custo tabela   # tabelas da QS5 em LaTeX, das três anteriores

`contar` grava custo-complexidade.csv, com uma linha por arquitetura, e custo-subida.csv, com uma
linha por execução curta. `nuvem` grava custo-nuvem.csv. `memoria` grava
custo-memoria-execucoes.csv e custo-memoria-configuracoes.csv. `tabela` grava
custo-qs5-complexidade.tex, custo-qs5-nuvem.tex e custo-qs5-memoria.tex. Tudo vai para
resultados/processados/.
"""

import argparse
import csv
import os
import re
import statistics
import sys
import tomllib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, cast

from harness.analise.comum import (BRUTOS, PROCESSADOS, Linha, execucoes_da_janela, gravar_csv,
                                   ler_execucoes, ler_json, numero, servico_do_container)
from harness.analise.curtas import JANELAS as JANELAS_CURTAS
from harness.analise.longa import JANELA as JANELA_LONGA
from harness.orquestracao.arquitetura import RAIZ, ler_arquitetura

ARQUITETURAS: Tuple[Tuple[str, str], ...] = (
    ("a", "consolidada"), ("a", "poliglota"),
    ("b", "consolidada"), ("b", "ingenua"), ("b", "outbox"),
    ("c", "timescaledb"), ("c", "influxdb"),
    ("m", "consolidada"), ("m", "poliglota"),
)
MODELOS: str = os.path.join(RAIZ, "harness", "orquestracao", "modelos.yaml")
# Modelos e chaves que só existem por controle do experimento (ADR-071, itens 2 e 6).
MODELOS_DE_CONTROLE: Set[str] = {"comum", "sistema-sob-teste"}
CHAVES_DE_CONTROLE: Set[str] = {"extends", "cpuset", "memswap_limit"}
# Chaves que o Compose mescla item a item quando o serviço e o modelo as definem.
MAPEAMENTOS: Set[str] = {"environment"}
DOCKERFILES: Dict[str, str] = {
    "tcc-postgres": "harness/orquestracao/postgres/Dockerfile",
    "tcc-busca": "eixo-a-busca/servico/Dockerfile",
    "tcc-relay": "eixo-b-fila/outbox/Dockerfile",
}
# Pacotes que a aplicação importa para falar com os sistemas da arquitetura (ADR-071, item 2).
# A mista usa o serviço de busca do Eixo A; no Eixo C, as duas usam o mesmo binário do TSBS.
CLIENTES: Dict[Tuple[str, str], Tuple[str, ...]] = {
    ("a", "consolidada"): ("psycopg[binary]", "psycopg-pool", "pgvector"),
    ("a", "poliglota"): ("opensearch-py[async]", "qdrant-client"),
    ("b", "consolidada"): ("psycopg[binary]",),
    ("b", "ingenua"): ("psycopg[binary]", "aio-pika"),
    ("b", "outbox"): ("psycopg[binary]", "aio-pika"),
    ("m", "consolidada"): ("psycopg[binary]", "psycopg-pool", "pgvector"),
    ("m", "poliglota"): ("opensearch-py[async]", "qdrant-client"),
}
# Ambiente da medição, para os marcadores do uv.lock (Linux, x86-64, CPython).
AMBIENTE_MARCADORES: Dict[str, str] = {
    "sys_platform": "linux", "platform_system": "Linux", "os_name": "posix",
    "platform_machine": "x86_64", "implementation_name": "cpython",
    "platform_python_implementation": "CPython",
}
# Espera fixa entre o gerador achar os sistemas prontos e o início do aquecimento
# (ATRASO_INICIO_S de eixo-a-busca/cargas/gerador.py e eixo-b-fila/cargas/gerador.py; o do
# Eixo C começa a carga logo depois da prontidão).
ATRASO_INICIO_S: Dict[str, float] = {"a": 3.0, "b": 3.0, "c": 0.0}
# Tamanho dos dados no fim da longa, pelos coletores (ADR-071, item 6): arquivo e métricas
# somadas sobre todos os rótulos, pela última amostra de cada um.
TAMANHO_NA_LONGA: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "postgres": ("metricas-postgres.csv", ("pg_tabela_bytes", "pg_indices_bytes")),
    "timescaledb": ("metricas-timescaledb.csv", ("tsdb_total_bytes",)),
    "rabbitmq": ("metricas-rabbitmq.csv", ("rmq_dados_bytes",)),
    "influxdb": ("metricas-influxdb.csv", ("influx_dados_bytes",)),
}
PRECOS: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "precos-nuvem.toml")
GIB: float = 1024.0 ** 3

Chaves = Dict[str, List[str]]


# --- Compose ---------------------------------------------------------------------------------

def ler_servicos(caminho: str) -> Dict[str, Chaves]:
    """Linhas de cada serviço do Compose, agrupadas pela chave de primeiro nível.

    Lê linha a linha, pelo recuo de 2 espaços dos arquivos do projeto. Linhas vazias e
    comentários ficam de fora; cada chave guarda a própria linha e as aninhadas."""
    servicos: Dict[str, Chaves] = {}
    atual: Optional[Chaves] = None
    chave: Optional[str] = None
    em_servicos = False
    with open(caminho, encoding="utf-8") as f:
        for bruta in f:
            linha = bruta.rstrip("\n")
            texto = linha.strip()
            if not texto or texto.startswith("#"):
                continue
            recuo = len(linha) - len(linha.lstrip(" "))
            if recuo == 0:
                em_servicos = texto == "services:"
                atual, chave = None, None
            elif not em_servicos:
                continue
            elif recuo == 2:
                atual = servicos.setdefault(texto.rstrip(":"), {})
                chave = None
            elif atual is None:
                continue
            elif recuo == 4:
                chave = texto.split(":", 1)[0]
                atual[chave] = [texto]
            elif chave is not None:
                atual[chave].append(texto)
    return servicos


def _nome(linha: str) -> str:
    return linha.split(":", 1)[0].strip()


def valor(chaves: Chaves, chave: str) -> Optional[str]:
    """Valor escalar de uma chave de primeiro nível, sem aspas."""
    linhas = chaves.get(chave)
    if not linhas:
        return None
    texto = linhas[0].split(":", 1)[1].strip()
    return texto.strip("\"'") or None


def _base(chaves: Chaves) -> Optional[Tuple[str, str]]:
    """(arquivo, serviço) do extends; arquivo vazio quando o modelo está no mesmo arquivo."""
    linhas = chaves.get("extends")
    if not linhas:
        return None
    direto = valor(chaves, "extends")
    if direto is not None:
        return "", direto
    campos = {_nome(l): l.split(":", 1)[1].strip() for l in linhas[1:]}
    return campos.get("file", ""), campos["service"]


def mesclar(base: Chaves, filho: Chaves) -> Chaves:
    """O serviço sobrepõe o modelo chave a chave; nos mapeamentos, item a item."""
    resultado: Chaves = dict(base)
    for chave, linhas in filho.items():
        if chave in MAPEAMENTOS and chave in base:
            nomes = {_nome(l) for l in linhas[1:]}
            resultado[chave] = linhas + [l for l in base[chave][1:] if _nome(l) not in nomes]
        else:
            resultado[chave] = linhas
    return resultado


def resolver(caminho: str, servico: str, modelos: str = MODELOS) -> Chaves:
    """Linhas do serviço com a herança do extends resolvida, sem as dos modelos de controle."""
    chaves = ler_servicos(caminho)[servico]
    proprias = {k: v for k, v in chaves.items() if k != "extends"}
    base = _base(chaves)
    if base is None:
        return proprias
    arquivo, modelo = base
    origem = os.path.normpath(os.path.join(os.path.dirname(caminho), arquivo)) if arquivo else caminho
    if modelo in MODELOS_DE_CONTROLE and os.path.samefile(origem, modelos):
        return proprias
    return mesclar(resolver(origem, modelo, modelos), proprias)


def opcoes_c(chaves: Chaves) -> int:
    """Itens `- -c` do command, que o PostgreSQL usa antes de cada parâmetro.

    A contagem principal soma o `-c` e o valor como duas linhas; a variante de sensibilidade
    (complemento da ADR-071) os conta como uma, como os outros sistemas escrevem cada ajuste."""
    return sum(1 for l in chaves.get("command", [])[1:] if l in ("- -c", '- "-c"', "- '-c'"))


def linhas_uteis(caminho: str) -> int:
    """Linhas não vazias e sem comentário (#) de um arquivo de configuração ou Dockerfile."""
    with open(caminho, encoding="utf-8") as f:
        return sum(1 for l in f if l.strip() and not l.strip().startswith("#"))


def montados(chaves: Chaves, pasta: str) -> List[str]:
    """Arquivos do repositório montados no serviço (caminhos relativos ao Compose)."""
    arquivos: List[str] = []
    for linha in chaves.get("volumes", [])[1:]:
        origem = linha.lstrip("- ").split(":", 1)[0]
        if origem.startswith("."):
            caminho = os.path.normpath(os.path.join(pasta, origem))
            if os.path.isfile(caminho):
                arquivos.append(caminho)
    return arquivos


def memoria_gib(texto: str) -> float:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([gm])", texto.strip().lower())
    if m is None:
        raise ValueError(f"mem_limit não reconhecido: {texto!r}")
    return float(m.group(1)) / (1.0 if m.group(2) == "g" else 1024.0)


def repositorio_da_imagem(imagem: str) -> str:
    """tcc-postgres:18.6-ts2.31.0-pgv0.8.7 → tcc-postgres; ignora o digest."""
    return imagem.split("@", 1)[0].rsplit(":", 1)[0]


@dataclass(frozen=True)
class Servico:
    nome: str
    imagem: str
    memoria_gib: float
    linhas: int
    montados: Tuple[str, ...]
    opcoes_c: int = 0


def servicos_da_arquitetura(eixo: str, arquitetura: str) -> List[Servico]:
    arq = ler_arquitetura(eixo, arquitetura)
    servicos: List[Servico] = []
    for nome in arq.servicos_sob_teste:
        chaves = resolver(arq.compose, nome)
        imagem, memoria = valor(chaves, "image"), valor(chaves, "mem_limit")
        if imagem is None or memoria is None:
            raise ValueError(f"{eixo}/{arquitetura}/{nome}: sem image ou mem_limit")
        linhas = sum(len(v) for k, v in chaves.items() if k not in CHAVES_DE_CONTROLE)
        servicos.append(Servico(nome, imagem.split("@", 1)[0], memoria_gib(memoria), linhas,
                                tuple(montados(chaves, os.path.dirname(arq.compose))),
                                opcoes_c(chaves)))
    return servicos


# --- dependências ----------------------------------------------------------------------------

def _atomo(texto: str) -> bool:
    m = re.fullmatch(r"(\w+) (==|!=) '([^']*)'", texto.strip())
    if m is None or m.group(1) not in AMBIENTE_MARCADORES:
        raise ValueError(f"marcador não suportado: {texto!r}")
    igual = AMBIENTE_MARCADORES[m.group(1)] == m.group(3)
    return igual if m.group(2) == "==" else not igual


def marcador_vale(marcador: Optional[str]) -> bool:
    """Avalia um marcador do uv.lock sem parênteses: cláusulas 'and' ligadas por 'or'."""
    if not marcador:
        return True
    if "(" in marcador:
        raise ValueError(f"marcador com parênteses não suportado: {marcador!r}")
    return any(all(_atomo(a) for a in ou.split(" and ")) for ou in marcador.split(" or "))


def requisito(texto: str) -> Tuple[str, Tuple[str, ...]]:
    """opensearch-py[async] → ('opensearch-py', ('async',))."""
    m = re.fullmatch(r"([A-Za-z0-9_.-]+)(?:\[([^\]]+)\])?", texto)
    if m is None:
        raise ValueError(f"requisito não reconhecido: {texto!r}")
    extras = tuple(e.strip() for e in (m.group(2) or "").split(",") if e.strip())
    return m.group(1).lower(), extras


def fechamento(pacotes: Sequence[Dict[str, Any]], raizes: Sequence[str]) -> List[str]:
    """Nomes dos pacotes que as raízes puxam no lock, elas incluídas, para o ambiente da medição."""
    por_nome = {cast(str, p["name"]): p for p in pacotes}
    vistos: Set[str] = set()
    feitos: Set[Tuple[str, Tuple[str, ...]]] = set()
    pendentes = [requisito(r) for r in raizes]
    while pendentes:
        nome, extras = pendentes.pop()
        if (nome, extras) in feitos:
            continue
        feitos.add((nome, extras))
        vistos.add(nome)
        pacote = por_nome[nome]
        dependencias = list(cast(List[Dict[str, Any]], pacote.get("dependencies", [])))
        opcionais = cast(Dict[str, List[Dict[str, Any]]], pacote.get("optional-dependencies", {}))
        for extra in extras:
            dependencias += opcionais.get(extra, [])
        for d in dependencias:
            if marcador_vale(cast(Optional[str], d.get("marker"))):
                pendentes.append((cast(str, d["name"]),
                                  tuple(cast(List[str], d.get("extra", [])))))
    return sorted(vistos)


def ler_lock() -> List[Dict[str, Any]]:
    with open(os.path.join(RAIZ, "uv.lock"), "rb") as f:
        return cast(List[Dict[str, Any]], tomllib.load(f)["package"])


# --- preparo e subida ------------------------------------------------------------------------

def diretorio_do_preparo(eixo: str, arquitetura: str) -> Optional[str]:
    """O preparo mais recente da arquitetura, cujo volume foi usado nas execuções."""
    pasta = os.path.join(BRUTOS, "preparos")
    nomes = sorted(n for n in os.listdir(pasta) if n.endswith(f"_{eixo}_{arquitetura}"))
    return os.path.join(pasta, nomes[-1]) if nomes else None


def _instante(texto: str) -> datetime:
    return datetime.strptime(texto, "%Y-%m-%dT%H:%M:%S%z")


def preparo(eixo: str, arquitetura: str) -> Dict[str, Any]:
    """Duração do preparo, volumes por serviço e, quando houver, durações e tamanhos da carga."""
    diretorio = diretorio_do_preparo(eixo, arquitetura)
    if diretorio is None:
        return {}
    p = ler_json(os.path.join(diretorio, "preparo.json"))
    tamanhos = cast(Dict[str, int], p.get("tamanhos_bytes", {}))
    r: Dict[str, Any] = {
        "preparo": os.path.basename(diretorio),
        "preparo_s": (_instante(cast(str, p["fim"])) - _instante(cast(str, p["inicio"]))).total_seconds(),
        "preparo_volume_gib": sum(tamanhos.values()) / GIB,
        "volumes_gib": {s: b / GIB for s, b in tamanhos.items()},
    }
    caminho = os.path.join(diretorio, "carga.json")
    if os.path.isfile(caminho):
        carga = ler_json(caminho)
        for nome, segundos in cast(Dict[str, float], carga.get("duracoes_s", {})).items():
            r[f"carga_{nome}_s"] = segundos
        for nome, tamanho in cast(Dict[str, int], carga.get("tamanhos_bytes", {})).items():
            r[f"carga_{nome}_gib"] = tamanho / GIB
    return r


def subida_s(manifesto: Dict[str, Any]) -> Optional[float]:
    """Do compose up até o gerador achar os sistemas prontos (ADR-071, item 3).

    O compose up vem logo depois do repouso; o início do aquecimento menos a espera fixa do
    gerador marca a prontidão, com a resolução de 1 s da consulta de prontidão."""
    tempos = cast(Dict[str, int], manifesto.get("tempos_ms", {}))
    controles = cast(Dict[str, Any], manifesto.get("controles", {}))
    eixo = cast(str, manifesto["eixo"])
    if "repouso_inicio" not in tempos or "aquecimento_inicio" not in tempos or eixo not in ATRASO_INICIO_S:
        return None
    inicio = tempos["repouso_inicio"] + float(controles["repouso_s"]) * 1000
    fim = tempos["aquecimento_inicio"] - ATRASO_INICIO_S[eixo] * 1000
    return (fim - inicio) / 1000


def subidas() -> List[Dict[str, Any]]:
    linhas: List[Dict[str, Any]] = []
    for janela in JANELAS_CURTAS:
        for diretorio in execucoes_da_janela(janela):
            m = ler_json(os.path.join(diretorio, "manifesto.json"))
            linhas.append({"execucao": os.path.basename(diretorio), "janela": janela,
                           "eixo": m["eixo"], "arquitetura": m["arquitetura"],
                           "concorrencia": m["concorrencia"], "repeticao": m["repeticao"],
                           "subida_s": subida_s(m)})
    return linhas


# --- contar ----------------------------------------------------------------------------------

def complexidade(eixo: str, arquitetura: str, pacotes: Sequence[Dict[str, Any]],
                 subidas_da_arquitetura: Sequence[float]) -> Dict[str, Any]:
    servicos = servicos_da_arquitetura(eixo, arquitetura)
    imagens = sorted({s.imagem for s in servicos})
    proprias = sorted({repositorio_da_imagem(i) for i in imagens
                       if repositorio_da_imagem(i).startswith("tcc-")})
    arquivos_conf = sorted({a for s in servicos for a in s.montados})
    r: Dict[str, Any] = {
        "eixo": eixo, "arquitetura": arquitetura,
        "servicos": len(servicos), "servicos_lista": " ".join(s.nome for s in servicos),
        "imagens": len(imagens), "imagens_proprias": len(proprias),
        "imagens_lista": " ".join(imagens),
        "linhas_compose": sum(s.linhas for s in servicos),
        "linhas_conf": sum(linhas_uteis(a) for a in arquivos_conf),
        "linhas_dockerfile": sum(linhas_uteis(os.path.join(RAIZ, DOCKERFILES[p])) for p in proprias),
        "memoria_gib": sum(s.memoria_gib for s in servicos),
    }
    r["linhas_total"] = r["linhas_compose"] + r["linhas_conf"] + r["linhas_dockerfile"]
    # Sensibilidade (complemento da ADR-071): cada par `-c` e valor como uma linha.
    r["opcoes_c"] = sum(s.opcoes_c for s in servicos)
    r["linhas_total_pares_juntos"] = r["linhas_total"] - r["opcoes_c"]
    clientes = CLIENTES.get((eixo, arquitetura))
    if clientes is not None:
        todos = fechamento(pacotes, clientes)
        r.update({"dependencias_diretas": len(clientes), "dependencias_total": len(todos),
                  "dependencias_lista": " ".join(todos)})
    if eixo != "m":  # o preparo da mista é uma cópia do Eixo A (ADR-071, item 3)
        p = preparo(eixo, arquitetura)
        r.update({k: v for k, v in p.items() if k != "volumes_gib"})
    if subidas_da_arquitetura:
        r.update({"subida_n": len(subidas_da_arquitetura),
                  "subida_mediana_s": statistics.median(subidas_da_arquitetura),
                  "subida_min_s": min(subidas_da_arquitetura),
                  "subida_max_s": max(subidas_da_arquitetura)})
    return r


def contar() -> None:
    por_execucao = subidas()
    pacotes = ler_lock()
    linhas: List[Dict[str, Any]] = []
    for eixo, arquitetura in ARQUITETURAS:
        valores = [cast(float, s["subida_s"]) for s in por_execucao
                   if s["eixo"] == eixo and s["arquitetura"] == arquitetura
                   and s["subida_s"] is not None]
        linhas.append(complexidade(eixo, arquitetura, pacotes, valores))
    os.makedirs(PROCESSADOS, exist_ok=True)
    gravar_csv(os.path.join(PROCESSADOS, "custo-subida.csv"), por_execucao)
    gravar_csv(os.path.join(PROCESSADOS, "custo-complexidade.csv"), linhas)
    colunas = ("servicos", "imagens", "imagens_proprias", "linhas_total",
               "linhas_total_pares_juntos", "dependencias_total", "preparo_s", "subida_mediana_s")
    print("eixo arquitetura   " + " ".join(f"{c:>18.18}" for c in colunas))
    for l in linhas:
        print(f"{l['eixo']:4} {l['arquitetura']:12} "
              + " ".join(f"{_fmt(l.get(c)):>18}" for c in colunas))


def _fmt(v: Any) -> str:
    if v is None:
        return "-"
    return f"{v:.1f}" if isinstance(v, float) else str(v)


# --- nuvem -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Instancia:
    nome: str
    vcpu: int
    memoria_gib: float
    hora_usd: float


def ler_precos(caminho: str = PRECOS) -> Tuple[Dict[str, Any], List[Instancia]]:
    with open(caminho, "rb") as f:
        dados = tomllib.load(f)
    instancias = [Instancia(cast(str, i["nome"]), int(i["vcpu"]), float(i["memoria_gib"]),
                            float(i["hora_usd"]))
                  for i in cast(List[Dict[str, Any]], dados["instancia"])]
    return dados, instancias


def maquina_para(memoria_gib: float, instancias: Sequence[Instancia]) -> Instancia:
    """A instância mais barata com memória igual ou maior; no empate, a de menos memória."""
    candidatas = [i for i in instancias if i.memoria_gib >= memoria_gib]
    if not candidatas:
        raise ValueError(f"nenhuma instância com {memoria_gib} GiB ou mais")
    return min(candidatas, key=lambda i: (i.hora_usd, i.memoria_gib))


def ultimos_por_rotulo(caminho: str, metricas: Sequence[str]) -> Dict[Tuple[str, str], float]:
    """Último valor de cada (métrica, rótulo) num CSV de métricas."""
    ultimos: Dict[Tuple[str, str], Tuple[int, float]] = {}
    with open(caminho, encoding="utf-8", newline="") as f:
        leitor = csv.reader(f)
        next(leitor)
        for ts, _origem, metrica, rotulo, texto in leitor:
            if metrica not in metricas or texto == "":
                continue
            instante = int(ts)
            anterior = ultimos.get((metrica, rotulo))
            if anterior is None or instante >= anterior[0]:
                ultimos[(metrica, rotulo)] = (instante, float(texto))
    return {chave: v for chave, (_, v) in ultimos.items()}


def volumes_na_longa(eixo: str, arquitetura: str) -> Optional[Dict[str, float]]:
    """GiB de dados por serviço no fim da execução longa da arquitetura, pelos coletores."""
    for diretorio in execucoes_da_janela(JANELA_LONGA):
        m = ler_json(os.path.join(diretorio, "manifesto.json"))
        if m["eixo"] != eixo or m["arquitetura"] != arquitetura:
            continue
        volumes: Dict[str, float] = {}
        for servico in ler_arquitetura(eixo, arquitetura).servicos_sob_teste:
            if servico not in TAMANHO_NA_LONGA:
                continue
            arquivo, metricas = TAMANHO_NA_LONGA[servico]
            ultimos = ultimos_por_rotulo(os.path.join(diretorio, arquivo), metricas)
            if not ultimos:
                raise ValueError(f"{diretorio}: sem {', '.join(metricas)} em {arquivo}")
            volumes[servico] = sum(ultimos.values()) / GIB
        return volumes
    return None


def volumes_gib(eixo: str, arquitetura: str) -> Tuple[Optional[Dict[str, float]], str]:
    if eixo in ("a", "m"):
        volumes = cast(Optional[Dict[str, float]], preparo(eixo, arquitetura).get("volumes_gib"))
        fonte = "preparo"
    else:
        volumes = volumes_na_longa(eixo, arquitetura)
        fonte = "fim da longa"
    return volumes, (fonte if volumes else "sem disco comparável")


def custo(eixo: str, arquitetura: str, precos: Dict[str, Any],
          instancias: Sequence[Instancia]) -> Dict[str, Any]:
    horas = float(precos["horas_por_mes"])
    gp3 = float(precos["gp3_usd_gib_mes"])
    servicos = servicos_da_arquitetura(eixo, arquitetura)
    maquinas = [(s.nome, maquina_para(s.memoria_gib, instancias)) for s in servicos]
    orcamento = sum(s.memoria_gib for s in servicos)
    referencia = maquina_para(orcamento, instancias)
    volumes, fonte = volumes_gib(eixo, arquitetura)
    r: Dict[str, Any] = {
        "eixo": eixo, "arquitetura": arquitetura,
        "maquinas": " ".join(f"{nome}={i.nome}" for nome, i in maquinas),
        "n_maquinas": len(maquinas),
        "vcpu": sum(i.vcpu for _, i in maquinas),
        "memoria_contratada_gib": sum(i.memoria_gib for _, i in maquinas),
        "orcamento_gib": orcamento,
        "maquinas_usd_mes": sum(i.hora_usd for _, i in maquinas) * horas,
        "referencia": referencia.nome,
        "referencia_usd_mes": referencia.hora_usd * horas,
        "disco_fonte": fonte,
    }
    if volumes:
        disco = sum(volumes.values())
        r.update({"disco_detalhe": " ".join(f"{s}={g:.3f}" for s, g in sorted(volumes.items())),
                  "disco_gib": disco, "disco_usd_mes": disco * gp3,
                  "total_usd_mes": r["maquinas_usd_mes"] + disco * gp3,
                  "referencia_total_usd_mes": r["referencia_usd_mes"] + disco * gp3})
    return r


def nuvem() -> None:
    if not os.path.isfile(PRECOS):
        sys.exit(f"falta {os.path.relpath(PRECOS, RAIZ)}, com os preços da AWS (ADR-071, item 6)")
    precos, instancias = ler_precos()
    linhas = [custo(eixo, arquitetura, precos, instancias) for eixo, arquitetura in ARQUITETURAS]
    gravar_csv(os.path.join(PROCESSADOS, "custo-nuvem.csv"), linhas)
    print(f"preços de {precos['consultado_em']} ({precos['regiao']})")
    for l in linhas:
        print(f"{l['eixo']:4} {l['arquitetura']:12} {l['maquinas']:60} "
              f"{l['maquinas_usd_mes']:8.2f} (máquina única {l['referencia']}: "
              f"{l['referencia_usd_mes']:.2f}); disco {_fmt(l.get('disco_gib'))} GiB, "
              f"{l['disco_fonte']}")


# --- memória ---------------------------------------------------------------------------------

METRICAS_MEMORIA: Tuple[str, ...] = ("cont_mem_pico_bytes", "cont_mem_anon_bytes",
                                     "cont_mem_shmem_bytes")
MEDIDAS_MEMORIA: Tuple[str, ...] = ("mem_pico_gib", "mem_anon_shmem_gib")


def memoria_da_execucao(caminho: str, servicos: Sequence[str]) -> Dict[str, Tuple[float, float]]:
    """(memory.peak, anon + shmem) em bytes de cada serviço sob teste, pelo maior valor das
    amostras da execução (complemento da ADR-071 sobre o pico de memória)."""
    amostras: Dict[Tuple[str, str], Dict[str, float]] = {}
    with open(caminho, encoding="utf-8", newline="") as f:
        for ts, _origem, metrica, rotulo, texto in csv.reader(l for l in f if "cont_mem_" in l):
            servico = servico_do_container(rotulo)
            if metrica in METRICAS_MEMORIA and texto != "" and servico in servicos:
                amostras.setdefault((servico, ts), {})[metrica] = float(texto)
    maximos: Dict[str, Tuple[float, float]] = {}
    for (servico, _), v in amostras.items():
        pico, presa = maximos.get(servico, (0.0, 0.0))
        maximos[servico] = (max(pico, v.get("cont_mem_pico_bytes", 0.0)),
                            max(presa, v.get("cont_mem_anon_bytes", 0.0)
                                + v.get("cont_mem_shmem_bytes", 0.0)))
    faltam = sorted(set(servicos) - set(maximos))
    if faltam:
        raise ValueError(f"{caminho}: sem memória de {', '.join(faltam)}")
    return maximos


def execucoes_com_memoria() -> List[Tuple[str, str, str, str, int, int]]:
    """(execução, eixo, grupo, arquitetura, nível, repetição) das execuções que entram nas
    análises das curtas e da mista; na mista, só as válidas, com a taxa como nível."""
    curtas = ler_execucoes(os.path.join(PROCESSADOS, "curtas-execucoes.csv"))
    mista = ler_execucoes(os.path.join(PROCESSADOS, "mista-execucoes.csv"))
    chaves = [(l["execucao"], l["eixo"], l["grupo"], l["arquitetura"], int(l["nivel"]),
               int(l["repeticao"])) for l in curtas]
    chaves += [(l["execucao"], "m", "m", l["arquitetura"], int(l["taxa"]), int(l["repeticao"]))
               for l in mista if l["valida"] == "True"]
    return chaves


def configuracoes_de_memoria(linhas: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Mediana, mínimo e máximo de cada medida por configuração (ADR-070, item 5)."""
    grupos: Dict[Tuple[str, str, str, int], List[Dict[str, Any]]] = {}
    for l in linhas:
        grupos.setdefault((l["eixo"], l["grupo"], l["arquitetura"], l["nivel"]), []).append(l)
    configs: List[Dict[str, Any]] = []
    for (eixo, grupo, arquitetura, nivel), membros in grupos.items():
        c: Dict[str, Any] = {"eixo": eixo, "grupo": grupo, "arquitetura": arquitetura,
                             "nivel": nivel, "n": len(membros)}
        for medida in MEDIDAS_MEMORIA:
            valores = [cast(float, m[medida]) for m in membros]
            c.update({f"{medida}_mediana": statistics.median(valores),
                      f"{medida}_min": min(valores), f"{medida}_max": max(valores)})
        configs.append(c)
    return configs


def memoria() -> None:
    linhas: List[Dict[str, Any]] = []
    for execucao, eixo, grupo, arquitetura, nivel, repeticao in execucoes_com_memoria():
        servicos = ler_arquitetura(eixo, arquitetura).servicos_sob_teste
        por_servico = memoria_da_execucao(
            os.path.join(BRUTOS, eixo, execucao, "metricas-host.csv"), servicos)
        r: Dict[str, Any] = {"execucao": execucao, "eixo": eixo, "grupo": grupo,
                             "arquitetura": arquitetura, "nivel": nivel, "repeticao": repeticao,
                             "mem_pico_gib": sum(p for p, _ in por_servico.values()) / GIB,
                             "mem_anon_shmem_gib": sum(a for _, a in por_servico.values()) / GIB}
        for servico, (pico, presa) in sorted(por_servico.items()):
            r[f"mem_pico_{servico}_gib"] = pico / GIB
            r[f"mem_anon_shmem_{servico}_gib"] = presa / GIB
        linhas.append(r)
    configs = configuracoes_de_memoria(linhas)
    gravar_csv(os.path.join(PROCESSADOS, "custo-memoria-execucoes.csv"), linhas)
    gravar_csv(os.path.join(PROCESSADOS, "custo-memoria-configuracoes.csv"), configs)
    print(f"{len(linhas)} execuções, {len(configs)} configurações")
    for c in configs:
        print(f"{c['eixo']:2} {c['grupo']:11} {c['arquitetura']:12} {c['nivel']:>8} "
              f"pico {c['mem_pico_gib_mediana']:6.2f} [{c['mem_pico_gib_min']:.2f}-"
              f"{c['mem_pico_gib_max']:.2f}]  anon+shmem {c['mem_anon_shmem_gib_mediana']:6.2f} "
              f"[{c['mem_anon_shmem_gib_min']:.2f}-{c['mem_anon_shmem_gib_max']:.2f}]")


# --- tabela ----------------------------------------------------------------------------------

EIXOS: Dict[str, str] = {"a": "A", "b": "B", "c": "C", "m": "Mista"}
NOMES: Dict[str, str] = {"consolidada": "consolidada", "poliglota": "poliglota",
                         "ingenua": "ingênua", "outbox": r"\emph{outbox}",
                         "timescaledb": "TimescaleDB", "influxdb": "InfluxDB"}
CABECALHO_COMPLEXIDADE: Tuple[str, ...] = (
    "Eixo", "Arquitetura", "Serviços", "Imagens", "Linhas", "Dependências", "Preparo (s)",
    "Subida (s)")
LEGENDA_COMPLEXIDADE: str = (
    "% Imagens: total (próprias). Linhas: contagem principal (variante com cada par -c e valor\n"
    "% como uma linha). Dependências: diretas (fechamento no uv.lock). Subida: mediana\n"
    "% [mín.--máx.], só nas execuções curtas.\n")
CABECALHO_NUVEM: Tuple[str, ...] = (
    "Eixo", "Arquitetura", "Instâncias", "Máquinas", "Disco", "Total", "Máquina única")
LEGENDA_NUVEM: str = (
    "% Valores em US$ por mês (730 h, us-east-1, preços de precos-nuvem.toml). Máquina única:\n"
    "% referência com o orçamento inteiro do eixo, sem o disco.\n")
CABECALHO_MEMORIA: Tuple[str, ...] = ("Eixo", "Arquitetura", "Pico (GiB)",
                                      "Anônima + compartilhada (GiB)")
LEGENDA_MEMORIA: str = (
    "% Menor e maior mediana entre as configurações (níveis e taxas) de cada arquitetura, nas\n"
    "% curtas e na mista. Pico: memory.peak do cgroup somado entre os serviços sob teste\n"
    "% (principal). Anônima + compartilhada: anon + shmem do memory.stat (variante descritiva).\n")


def br(v: Optional[float], casas: int = 0) -> str:
    """Número com vírgula decimal; sem valor vira traço."""
    return "--" if v is None else f"{v:.{casas}f}".replace(".", ",")


def _inteiro(linha: Linha, coluna: str) -> str:
    return br(numero(linha, coluna))


def celulas_complexidade(linha: Linha) -> List[str]:
    """Uma linha da tabela de complexidade e provisionamento, a partir de custo-complexidade.csv."""
    dependencias = "--"
    if numero(linha, "dependencias_diretas") is not None:
        dependencias = f"{_inteiro(linha, 'dependencias_diretas')} ({_inteiro(linha, 'dependencias_total')})"
    subida = "--"
    if numero(linha, "subida_mediana_s") is not None:
        subida = (f"{br(numero(linha, 'subida_mediana_s'), 1)} "
                  f"[{br(numero(linha, 'subida_min_s'), 1)}--{br(numero(linha, 'subida_max_s'), 1)}]")
    return [EIXOS[linha["eixo"]], NOMES[linha["arquitetura"]], _inteiro(linha, "servicos"),
            f"{_inteiro(linha, 'imagens')} ({_inteiro(linha, 'imagens_proprias')})",
            f"{_inteiro(linha, 'linhas_total')} ({_inteiro(linha, 'linhas_total_pares_juntos')})",
            dependencias, _inteiro(linha, "preparo_s"), subida]


def instancias_agrupadas(maquinas: str) -> str:
    """postgres=r8a.large rabbitmq=r8a.large relay=m8a.medium → 2$\\times$r8a.large + m8a.medium."""
    contagem: Dict[str, int] = {}
    for item in maquinas.split():
        nome = item.split("=", 1)[1]
        contagem[nome] = contagem.get(nome, 0) + 1
    return " + ".join(nome if n == 1 else f"{n}$\\times${nome}" for nome, n in contagem.items())


def celulas_nuvem(linha: Linha) -> List[str]:
    """Uma linha da tabela de custo em nuvem, a partir de custo-nuvem.csv."""
    return [EIXOS[linha["eixo"]], NOMES[linha["arquitetura"]],
            instancias_agrupadas(linha["maquinas"]),
            br(numero(linha, "maquinas_usd_mes"), 2), br(numero(linha, "disco_usd_mes"), 2),
            br(numero(linha, "total_usd_mes"), 2), br(numero(linha, "referencia_usd_mes"), 2)]


def tabular(cabecalho: Sequence[str], linhas: Sequence[List[str]]) -> str:
    """Ambiente tabular do LaTeX, com o eixo escrito só na primeira linha de cada grupo."""
    texto = [r"\begin{tabular}{ll" + "r" * (len(cabecalho) - 2) + "}", r"\hline",
             " & ".join(cabecalho) + r" \\", r"\hline"]
    anterior = None
    for celulas in linhas:
        if anterior is not None and celulas[0] != anterior:
            texto.append(r"\hline")
        texto.append(" & ".join([celulas[0] if celulas[0] != anterior else ""] + celulas[1:])
                     + r" \\")
        anterior = celulas[0]
    texto += [r"\hline", r"\end{tabular}"]
    return "\n".join(texto) + "\n"


def celulas_memoria(eixo: str, arquitetura: str, configs: Sequence[Linha]) -> List[str]:
    """Menor e maior mediana por configuração de cada medida de memória, em GiB."""
    proprias = [c for c in configs if c["eixo"] == eixo and c["arquitetura"] == arquitetura]
    celulas = [EIXOS[eixo], NOMES[arquitetura]]
    for medida in MEDIDAS_MEMORIA:
        medianas = [cast(float, numero(c, f"{medida}_mediana")) for c in proprias]
        celulas.append(f"{br(min(medianas), 1)}--{br(max(medianas), 1)}" if medianas else "--")
    return celulas


def tabela() -> None:
    """Tabelas da QS5 em LaTeX, a partir dos CSV de contar, nuvem e memoria."""
    complexidade_csv = ler_execucoes(os.path.join(PROCESSADOS, "custo-complexidade.csv"))
    nuvem_csv = ler_execucoes(os.path.join(PROCESSADOS, "custo-nuvem.csv"))
    memoria_csv = ler_execucoes(os.path.join(PROCESSADOS, "custo-memoria-configuracoes.csv"))
    origem = ("% Gerado por harness/analise/custo.py tabela (ADR-071 e complemento de 20/11/2026);\n"
              "% não editar à mão.\n")
    saidas = {
        "custo-qs5-complexidade.tex": LEGENDA_COMPLEXIDADE + tabular(
            CABECALHO_COMPLEXIDADE, [celulas_complexidade(l) for l in complexidade_csv]),
        "custo-qs5-nuvem.tex": LEGENDA_NUVEM + tabular(
            CABECALHO_NUVEM, [celulas_nuvem(l) for l in nuvem_csv]),
        "custo-qs5-memoria.tex": LEGENDA_MEMORIA + tabular(
            CABECALHO_MEMORIA, [celulas_memoria(e, a, memoria_csv) for e, a in ARQUITETURAS]),
    }
    for nome, texto in saidas.items():
        with open(os.path.join(PROCESSADOS, nome), "w", encoding="utf-8") as f:
            f.write(origem + texto)
        print(f"== {nome}\n{texto}")


def main() -> None:
    analisador = argparse.ArgumentParser(description=__doc__,
                                         formatter_class=argparse.RawDescriptionHelpFormatter)
    analisador.add_argument("etapa", choices=("contar", "nuvem", "memoria", "tabela"))
    etapa = analisador.parse_args().etapa
    if etapa == "contar":
        contar()
    elif etapa == "nuvem":
        nuvem()
    elif etapa == "memoria":
        memoria()
    else:
        tabela()


if __name__ == "__main__":
    main()
