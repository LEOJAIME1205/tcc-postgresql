"""Gerador de carga do Eixo C: adaptador do TSBS ao contrato do harness (ADR-059 a ADR-065).

    gerador.py gerar [--dados DIR]
    gerador.py preparar [--saida DIR]
    gerador.py executar --saida DIR --concorrencia N --aquecimento-s A --medicao-s M --semente S
                        [--taxa R]
    gerador.py ingerir --saida DIR --taxa R [--trabalhadores T] [--limite-s L]

`gerar` produz, uma única vez, os dados e as consultas dos dois formatos e grava em
DIR/geracao.json os parâmetros, os tempos e o SHA-256 de cada arquivo (ADR-060).
`preparar` cria o banco `benchmark` vazio na arquitetura de TCC_ARQUITETURA. `executar` carrega
o arquivo inteiro com N trabalhadores, comprime (TimescaleDB) ou espera as compactações
(InfluxDB) e roda as consultas (ADR-065). A carga tem até A + M segundos para terminar.

Com --taxa, a execução é a da longa duração (ADR-069): os dados são gerados durante a execução,
com os parâmetros da ADR-060 e o período que cabe em M segundos a R métricas/s, e um alimentador
os entrega ao tsbs_load em taxa fixa (ADR-068). A cada 30 min roda um lote de consultas sobre o
que já foi carregado. No fim, compressão ou compactações e as consultas das execuções curtas.
`ingerir` é a escrita da carga mista (ADR-068): carrega o arquivo das execuções curtas a R
métricas/s até receber SIGTERM ou SIGINT, o arquivo acabar ou L segundos passarem, e grava
DIR/metricas-ingestao.csv e DIR/ingestao.json. Quem o chama é eixo-m-mista/cargas/gerador.py.

O TimescaleDB é lido pelas variáveis PG* da libpq, e o InfluxDB por TCC_INFLUXDB.
"""

import argparse
import csv
import hashlib
import json
import os
import platform
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import FrameType
from typing import IO, Any, Callable, Dict, List, LiteralString, Optional, Tuple, cast

import psycopg

EIXO_C: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DADOS: str = os.path.join(EIXO_C, "dados")
FORMATOS: Dict[str, str] = {"timescaledb": "timescaledb", "influxdb": "influx"}  # arquitetura -> TSBS
VERSAO: str = "2"  # 2: taxa fixa, longa duração e alimentador da carga mista (ADR-068 e ADR-069)
TSBS: str = "8323e59c74027b108f4ad5ec5d3e498b0101a02e com pr255.patch (ADR-059)"
CASO: str = "cpu-only"
ESCALA: int = 4000
INICIO: str = "2016-01-01T00:00:00Z"
FIM: str = "2016-01-04T00:00:00Z"
FIM_CONSULTAS: str = "2016-01-04T00:00:01Z"  # o gerador de consultas pede o fim depois do último ponto
INTERVALO: str = "10s"
INTERVALO_S: int = 10
SEMENTE: int = 20261108
SEMENTE_AQUECIMENTO: int = SEMENTE + 1
CONSULTAS_POR_TIPO: int = 20
AQUECIMENTO_POR_TIPO: int = 5
LINHAS_ESPERADAS: int = ESCALA * 3 * 86400 // 10
METRICAS_ESPERADAS: int = LINHAS_ESPERADAS * 10  # 10 campos por linha no caso cpu-only
TIPOS_CONSULTA: Tuple[str, ...] = (
    "single-groupby-1-1-1", "single-groupby-1-1-12", "single-groupby-1-8-1",
    "single-groupby-5-1-1", "single-groupby-5-1-12", "single-groupby-5-8-1",
    "cpu-max-all-1", "cpu-max-all-8", "double-groupby-1", "double-groupby-5",
    "double-groupby-all", "high-cpu-all", "high-cpu-1", "lastpoint", "groupby-orderby-limit",
)
BANCO: str = "benchmark"
LOTE: int = 10000
ESPERA_PRONTO_S: float = 300.0
ESPERA_COMPACTACAO_S: float = 1800.0
COMPACTACAO_ESTAVEL: int = 3  # leituras seguidas, a cada 2 s, sem compactação em andamento
# Taxa fixa (ADR-068 e ADR-069)
CAMPOS_POR_PONTO: int = 10  # métricas por linha do caso cpu-only
LINHAS_POR_PONTO: Dict[str, int] = {"timescaledb": 2, "influxdb": 1}  # tags e cpu; uma linha só
LINHAS_CABECALHO: Dict[str, int] = {"timescaledb": 3, "influxdb": 0}  # tags, cpu e linha vazia
FATIA_S: float = 0.01  # o alimentador entrega os pontos devidos a cada 10 ms
LEITURA_BYTES: int = 1 << 20
TRABALHADORES_TAXA: int = 4
FOLGA_LONGA_S: int = 3600  # a ingestão da longa duração pode atrasar até 1 h (ADR-069, item 4)
# Um lote de consultas a cada 30 min (ADR-069, item 4). TCC_LOTE_INTERVALO_S só existe para o
# teste funcional; o valor usado vai para o gerador.json.
LOTE_INTERVALO_S: int = int(os.environ.get("TCC_LOTE_INTERVALO_S", "1800"))
LOTE_POR_TIPO: int = 5
SEMENTE_LOTES: int = SEMENTE + 100  # lote k usa SEMENTE_LOTES + k, igual nos dois sistemas
ESPERA_COMPACTACAO_LONGA_S: float = 7200.0
LINHA_CARGA: re.Pattern[str] = re.compile(r"^(\d+),[^,]*,([0-9.eE+]+),[^,]*,[^,]*,([0-9.eE+]+),")
LINHA_RESUMO: re.Pattern[str] = re.compile(r"^loaded (\d+) (metrics|rows) in ([0-9.]+)sec")
LINHA_CONSULTAS: re.Pattern[str] = re.compile(
    r"min:\s*([0-9.]+)ms, med:\s*([0-9.]+)ms, mean:\s*([0-9.]+)ms, max:\s*([0-9.]+)ms, "
    r"stddev:\s*([0-9.]+)ms, sum:\s*[0-9.]+sec, count: (\d+)")
COMPRIME: Tuple[LiteralString, ...] = (
    "ALTER TABLE cpu SET (timescaledb.enable_columnstore = true, "
    "timescaledb.segmentby = 'tags_id', timescaledb.orderby = 'time DESC')",
    "SELECT count(compress_chunk(c)) FROM show_chunks('cpu') c",
)
ESPACO: LiteralString = """
    SELECT number_compressed_chunks, before_compression_total_bytes, after_compression_total_bytes
    FROM hypertable_compression_stats('cpu')"""

Valor = Tuple[str, str, float, str]  # métrica, rótulo, valor, fonte


def avisar(mensagem: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {mensagem}", file=sys.stderr, flush=True)


def sha256(caminho: str) -> str:
    with open(caminho, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def gravar_json(caminho: str, dados: Dict[str, Any]) -> None:
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dados, f, indent=2, ensure_ascii=False)
        f.write("\n")


def arquitetura_do_ambiente() -> str:
    arquitetura = os.environ.get("TCC_ARQUITETURA", "")
    if arquitetura not in FORMATOS:
        raise SystemExit(f"TCC_ARQUITETURA precisa ser {' ou '.join(FORMATOS)}, não {arquitetura!r}")
    return arquitetura


# --- geração dos dados (ADR-060) -----------------------------------------------------------


def comprimir(comando: List[str], destino: str) -> float:
    """Roda um programa do TSBS e grava a saída comprimida com gzip; devolve a duração."""
    inicio = time.monotonic()
    with open(destino + ".parcial", "wb") as saida:
        programa = subprocess.Popen(comando, stdout=subprocess.PIPE)
        compressor = subprocess.Popen(["gzip", "-1"], stdin=programa.stdout, stdout=saida)
        if programa.stdout is not None:
            programa.stdout.close()  # o gzip fica com o único leitor do pipe
        if compressor.wait() != 0 or programa.wait() != 0:
            raise RuntimeError(f"{comando[0]} terminou com erro (código {programa.returncode})")
    os.replace(destino + ".parcial", destino)
    return round(time.monotonic() - inicio, 1)


def parametros_comuns(semente: int) -> List[str]:
    return [f"--use-case={CASO}", f"--seed={semente}", f"--scale={ESCALA}",
            f"--timestamp-start={INICIO}"]


def arquivo_consultas(dados: str, arquitetura: str, tipo: str, aquecimento: bool) -> str:
    pasta = os.path.join(dados, "consultas", "aquecimento" if aquecimento else "")
    return os.path.join(pasta, f"{arquitetura}-{tipo}.gz")


def gerar(dados: str) -> int:
    """Dados e consultas dos dois formatos: as medidas com a semente dos dados (ADR-061)."""
    os.makedirs(os.path.join(dados, "consultas", "aquecimento"), exist_ok=True)
    arquivos: Dict[str, Dict[str, Any]] = {}
    for arquitetura, formato in FORMATOS.items():
        destino = os.path.join(dados, f"carga-{arquitetura}.gz")
        avisar(f"gerando os dados de {arquitetura}")
        duracao = comprimir(["tsbs_generate_data", *parametros_comuns(SEMENTE), f"--timestamp-end={FIM}",
                             f"--log-interval={INTERVALO}", f"--format={formato}"], destino)
        arquivos[os.path.relpath(destino, dados)] = {"duracao_s": duracao}
        for tipo in TIPOS_CONSULTA:
            for aquecimento, semente, quantidade in ((False, SEMENTE, CONSULTAS_POR_TIPO),
                                                     (True, SEMENTE_AQUECIMENTO, AQUECIMENTO_POR_TIPO)):
                destino = arquivo_consultas(dados, arquitetura, tipo, aquecimento)
                duracao = comprimir(["tsbs_generate_queries", *parametros_comuns(semente),
                                     f"--timestamp-end={FIM_CONSULTAS}", f"--queries={quantidade}",
                                     f"--query-type={tipo}", f"--format={formato}"], destino)
                arquivos[os.path.relpath(destino, dados)] = {"duracao_s": duracao}
    for nome, registro in arquivos.items():
        caminho = os.path.join(dados, nome)
        registro["bytes"] = os.path.getsize(caminho)
        registro["sha256"] = sha256(caminho)
    gravar_json(os.path.join(dados, "geracao.json"), {
        "caso_de_uso": CASO, "escala": ESCALA, "inicio": INICIO, "fim": FIM,
        "fim_consultas": FIM_CONSULTAS, "intervalo": INTERVALO, "semente": SEMENTE,
        "semente_aquecimento": SEMENTE_AQUECIMENTO, "consultas_por_tipo": CONSULTAS_POR_TIPO,
        "aquecimento_por_tipo": AQUECIMENTO_POR_TIPO, "tipos_de_consulta": list(TIPOS_CONSULTA),
        "linhas": LINHAS_ESPERADAS, "metricas": METRICAS_ESPERADAS, "tsbs": TSBS,
        "arquivos": arquivos, "concluido_em": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    })
    avisar(f"{len(arquivos)} arquivos gerados em {dados}")
    return 0


# --- acesso aos dois sistemas --------------------------------------------------------------


def url_influxdb() -> str:
    return os.environ.get("TCC_INFLUXDB", "http://influxdb:8086").rstrip("/")


def influx(caminho: str, consulta: Optional[str] = None) -> Any:
    """GET (sem consulta) ou POST /query (com consulta InfluxQL) no InfluxDB; devolve o JSON."""
    dados = urllib.parse.urlencode({"q": consulta}).encode() if consulta is not None else None
    with urllib.request.urlopen(url_influxdb() + caminho, data=dados, timeout=10) as resposta:
        corpo = resposta.read()
    return json.loads(corpo) if corpo else None


def bancos_influxdb() -> List[str]:
    resposta = cast(Dict[str, Any], influx("/query", "SHOW DATABASES"))
    series = cast(List[Dict[str, Any]], resposta["results"][0].get("series", []))
    return [str(linha[0]) for serie in series for linha in cast(List[List[Any]], serie.get("values", []))]


def conectar(banco: str) -> psycopg.Connection[Tuple[Any, ...]]:
    return psycopg.connect(dbname=banco, autocommit=True, connect_timeout=2)


def pronto(arquitetura: str, com_banco: bool) -> bool:
    """Se o sistema aceita conexões e, com `com_banco`, se o banco benchmark já existe."""
    try:
        if arquitetura == "timescaledb":
            with conectar(BANCO if com_banco else "postgres"):
                return True
        influx("/ping?verbose=true")
        return not com_banco or BANCO in bancos_influxdb()
    except (psycopg.OperationalError, urllib.error.URLError, OSError, KeyError, ValueError):
        return False


def esperar(condicao: Callable[[], bool], limite_s: float, intervalo_s: float = 1.0) -> bool:
    prazo = time.monotonic() + limite_s
    while time.monotonic() < prazo:
        if condicao():
            return True
        time.sleep(intervalo_s)
    return False


def preparar(saida: str, arquitetura: Optional[str] = None) -> int:
    """Banco benchmark vazio: o TSBS cria as tabelas na carga (--do-create-db=false).

    Sem `arquitetura`, vale TCC_ARQUITETURA; a carga mista passa a do sistema de ingestão."""
    arquitetura = arquitetura or arquitetura_do_ambiente()
    if not esperar(lambda: pronto(arquitetura, False), ESPERA_PRONTO_S):
        avisar(f"{arquitetura} não aceitou conexões em {ESPERA_PRONTO_S:.0f} s")
        return 1
    registro: Dict[str, Any] = {"arquitetura": arquitetura, "banco": BANCO}
    if arquitetura == "timescaledb":
        with conectar("postgres") as conn:
            existe = conn.execute("SELECT 1 FROM pg_database WHERE datname = 'benchmark'").fetchone()
            if existe is not None:
                avisar("o banco benchmark já existe: o preparo parte de um servidor vazio")
                return 1
            conn.execute("CREATE DATABASE benchmark")
        with conectar(BANCO) as conn:
            conn.execute("CREATE EXTENSION timescaledb")
            # A biblioteca vem do shared_preload_libraries; a visão precisa da extensão (ADR-021).
            conn.execute("CREATE EXTENSION pg_stat_statements")
            versao = conn.execute("SELECT version()").fetchone()
            extensao = conn.execute(
                "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'").fetchone()
        registro["postgres"] = versao[0] if versao else None
        registro["timescaledb"] = extensao[0] if extensao else None
    else:
        if BANCO in bancos_influxdb():
            avisar("o banco benchmark já existe: o preparo parte de um servidor vazio")
            return 1
        influx("/query", "CREATE DATABASE benchmark")
        with urllib.request.urlopen(url_influxdb() + "/ping", timeout=10) as resposta:
            registro["influxdb"] = resposta.headers.get("X-Influxdb-Version")
    registro["concluido_em"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    gravar_json(os.path.join(saida, "carga.json"), registro)
    avisar(f"banco {BANCO} criado em {arquitetura}")
    return 0


# --- execução (ADR-065) --------------------------------------------------------------------


def opcoes_tsbs(arquitetura: str) -> List[str]:
    """Endereço do sistema para os programas do TSBS; o resto fica nos padrões (ADR-061)."""
    if arquitetura == "timescaledb":
        return [f"--host={os.environ.get('PGHOST', 'timescaledb')}",
                f"--user={os.environ.get('PGUSER', 'postgres')}",
                f"--pass={os.environ.get('PGPASSWORD', '')}"]
    return [f"--urls={url_influxdb()}"]


def comando_carga(arquitetura: str, trabalhadores: int, resultados: str) -> List[str]:
    """tsbs_load do sistema, lendo os pontos da entrada padrão (ADR-061)."""
    return [f"tsbs_load_{FORMATOS[arquitetura]}", *opcoes_tsbs(arquitetura), f"--db-name={BANCO}",
            "--do-create-db=false", f"--workers={trabalhadores}", f"--batch-size={LOTE}",
            "--reporting-period=1s", f"--results-file={resultados}"]


def carregar(arquitetura: str, concorrencia: int, limite_s: float, ferramenta: str,
             metricas: str) -> Tuple[float, float, Dict[str, float], Optional[str]]:
    """Carga do arquivo inteiro; devolve início, fim, totais do resumo do TSBS e o erro, se houver."""
    arquivo = os.path.join(DADOS, f"carga-{arquitetura}.gz")
    comando = comando_carga(arquitetura, concorrencia, os.path.join(ferramenta, "tsbs-carga.json"))
    totais: Dict[str, float] = {}
    estourou = threading.Event()
    inicio = time.time()
    leitor = subprocess.Popen(["zcat", arquivo], stdout=subprocess.PIPE)
    carga = subprocess.Popen(comando, stdin=leitor.stdout, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
    if leitor.stdout is not None:
        leitor.stdout.close()

    def estourar() -> None:
        estourou.set()
        carga.kill()
        leitor.kill()

    relogio = threading.Timer(limite_s, estourar)
    relogio.start()
    with (open(os.path.join(ferramenta, "tsbs-carga.txt"), "w", encoding="utf-8") as original,
          open(metricas, "w", newline="", encoding="utf-8") as f):
        escritor = csv.writer(f)
        escritor.writerow(["ts_ms", "origem", "metrica", "rotulo", "valor"])
        if carga.stdout is None:
            raise RuntimeError("tsbs_load sem saída padrão")
        for linha in carga.stdout:
            original.write(linha)
            serie = LINHA_CARGA.match(linha)
            if serie is not None:
                ts_ms = int(serie.group(1)) * 1000
                escritor.writerow((ts_ms, "gerador", "tsbs_metricas_total", "", int(float(serie.group(2)))))
                escritor.writerow((ts_ms, "gerador", "tsbs_linhas_total", "", int(float(serie.group(3)))))
                f.flush()
            resumo = LINHA_RESUMO.match(linha)
            if resumo is not None:
                totais[resumo.group(2)] = float(resumo.group(1))
                totais["segundos"] = float(resumo.group(3))
    codigo = carga.wait()
    leitor.wait()
    fim = time.time()
    relogio.cancel()
    if estourou.is_set():
        return inicio, fim, totais, f"a carga não terminou em {limite_s:.0f} s"
    if codigo != 0:
        return inicio, fim, totais, f"tsbs_load terminou com código {codigo}; veja ferramenta/tsbs-carga.txt"
    if totais.get("metrics") != METRICAS_ESPERADAS:
        return inicio, fim, totais, f"carregadas {totais.get('metrics')} métricas, esperadas {METRICAS_ESPERADAS}"
    return inicio, fim, totais, None


def comprimir_timescaledb() -> List[Valor]:
    """Compressão de todos os chunks (ADR-062) e ANALYZE depois da carga em massa (ADR-065)."""
    valores: List[Valor] = []
    with conectar(BANCO) as conn:
        inicio = time.monotonic()
        for comando in COMPRIME:
            conn.execute(comando)
        valores.append(("compressao_s", "cpu", round(time.monotonic() - inicio, 3), "gerador.py"))
        inicio = time.monotonic()
        conn.execute("ANALYZE cpu")
        valores.append(("analyze_s", "cpu", round(time.monotonic() - inicio, 3), "gerador.py"))
        espaco = conn.execute(ESPACO).fetchone()
    if espaco is not None:
        valores += [("chunks_comprimidos", "cpu", float(espaco[0]), "hypertable_compression_stats"),
                    ("espaco_antes_compressao_bytes", "cpu", float(espaco[1]), "hypertable_compression_stats"),
                    ("espaco_depois_compressao_bytes", "cpu", float(espaco[2]), "hypertable_compression_stats")]
    return valores


def compactacoes_influxdb() -> Tuple[int, int]:
    """Compactações em andamento e bytes dos arquivos TSM do banco benchmark."""
    variaveis = cast(Dict[str, Any], influx("/debug/vars"))
    ativas, tsm = 0, 0
    for grupo in variaveis.values():
        if not isinstance(grupo, dict):
            continue
        conteudo = cast(Dict[str, Any], grupo)
        rotulos = cast(Dict[str, str], conteudo.get("tags") or {})
        if rotulos.get("database") != BANCO:
            continue
        valores = cast(Dict[str, Any], conteudo.get("values") or {})
        if conteudo.get("name") == "tsm1_engine":
            ativas += sum(int(v) for k, v in valores.items() if k.endswith("Active"))
        elif conteudo.get("name") == "tsm1_filestore":
            tsm += int(valores.get("diskBytes", 0))
    return ativas, tsm


def esperar_influxdb(limite_s: float = ESPERA_COMPACTACAO_S) -> Tuple[List[Valor], Optional[str]]:
    """Espera nenhuma compactação estar em andamento, sem forçar a completa (ADR-065)."""
    inicio = time.monotonic()
    seguidas = 0
    tsm = 0
    while time.monotonic() - inicio < limite_s:
        ativas, tsm = compactacoes_influxdb()
        seguidas = seguidas + 1 if ativas == 0 else 0
        if seguidas >= COMPACTACAO_ESTAVEL:
            return [("espera_compactacao_s", "", round(time.monotonic() - inicio, 3), "gerador.py"),
                    ("tsm_bytes", BANCO, float(tsm), "/debug/vars, tsm1_filestore")], None
        time.sleep(2.0)
    return [], f"as compactações do InfluxDB não pararam em {limite_s:.0f} s"


def consultar(arquitetura: str, tipo: str, ferramenta: str) -> Tuple[List[Valor], Optional[str]]:
    """Aquecimento e as 20 consultas medidas de um tipo, com 1 trabalhador (ADR-061)."""
    pasta = os.path.join(ferramenta, "consultas")
    comando = comando_consultas(arquitetura)
    saidas: Dict[bool, str] = {}
    for aquecimento in (True, False):
        nome = f"{'aquecimento-' if aquecimento else ''}{tipo}"
        with (open(arquivo_consultas(DADOS, arquitetura, tipo, aquecimento), "rb") as entrada,
              open(os.path.join(pasta, f"{nome}.txt"), "w", encoding="utf-8") as saida):
            leitor = subprocess.Popen(["zcat"], stdin=entrada, stdout=subprocess.PIPE)
            programa = subprocess.Popen(
                [*comando, f"--results-file={os.path.join(pasta, nome + '.json')}"],
                stdin=leitor.stdout, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            if leitor.stdout is not None:
                leitor.stdout.close()  # o TSBS fica com o único leitor do pipe
            texto, _ = programa.communicate()
            leitor.wait()
            saida.write(texto)
        if programa.returncode != 0:
            return [], f"{nome}: tsbs_run_queries terminou com código {programa.returncode}"
        saidas[aquecimento] = texto
    estatisticas = resumo_consultas(saidas[False], os.path.join(pasta, f"{tipo}.json"))
    if estatisticas is None:
        return [], f"{tipo}: resumo das consultas não encontrado na saída do TSBS"
    valores: List[Valor] = [("consulta_contagem", tipo, estatisticas["contagem"], "saída do tsbs_run_queries")]
    for rotulo, fonte in (("min", "saída"), ("media", "saída"), ("desvio", "saída"), ("p50", "saída"),
                          ("max", "saída"), ("p95", "JSON"), ("p99", "JSON"), ("p999", "JSON")):
        valores.append(("consulta_latencia_us", f"{tipo}:{rotulo}", round(estatisticas[rotulo] * 1000, 1),
                        f"tsbs_run_queries ({fonte})"))
    return valores, None


def resumo_consultas(texto: str, resultados: str) -> Optional[Dict[str, float]]:
    """Contagem e latências em ms de uma rodada do tsbs_run_queries: da saída de texto, mínimo,
    mediana, média, máximo e desvio; do JSON de resultados, os quantis 95, 99 e 99,9."""
    estatisticas = LINHA_CONSULTAS.findall(texto.split("all queries", 1)[-1])
    if not estatisticas:
        return None
    minimo, mediana, media, maximo, desvio, contagem = estatisticas[0]
    with open(resultados, encoding="utf-8") as f:
        quantis = cast(Dict[str, float], json.load(f)["Totals"]["overallQuantiles"]["all_queries"])
    return {"contagem": float(contagem), "min": float(minimo), "p50": float(mediana),
            "media": float(media), "max": float(maximo), "desvio": float(desvio),
            "p95": float(quantis["q95"]), "p99": float(quantis["q99"]), "p999": float(quantis["q999"])}


def comando_consultas(arquitetura: str) -> List[str]:
    """tsbs_run_queries do sistema, com 1 trabalhador (ADR-061)."""
    comando = [f"tsbs_run_queries_{FORMATOS[arquitetura]}", f"--db-name={BANCO}", "--workers=1",
               "--print-interval=0"]
    if arquitetura == "timescaledb":
        return [*comando, f"--hosts={os.environ.get('PGHOST', 'timescaledb')}",
                f"--user={os.environ.get('PGUSER', 'postgres')}",
                f"--pass={os.environ.get('PGPASSWORD', '')}"]
    return [*comando, f"--urls={url_influxdb()}"]


# --- taxa fixa: alimentador e ingestão (ADR-068 e ADR-069) --------------------------------


class Alimentador(threading.Thread):
    """Entrega ao tsbs_load, em taxa fixa, os pontos lidos de uma fonte (ADR-068, item 3).

    O ponto k está agendado para o início mais k/R. A cada fatia de 10 ms, o alimentador envia
    todos os pontos já devidos. Se a escrita bloquear porque o tsbs_load não acompanha, os pontos
    atrasados saem logo que ela volta, sem pular o agendamento, como na agenda da ADR-054. O
    cabeçalho do formato do TimescaleDB sai antes do primeiro ponto."""

    def __init__(self, fonte: int, destino: IO[bytes], arquitetura: str, pontos_por_s: float,
                 parar: threading.Event) -> None:
        super().__init__(name="alimentador", daemon=True)
        self.fonte = fonte
        self.destino = destino
        self.linhas_por_ponto = LINHAS_POR_PONTO[arquitetura]
        self.linhas_cabecalho = LINHAS_CABECALHO[arquitetura]
        self.pontos_por_s = pontos_por_s
        self.parar = parar
        self.enviados: int = 0
        self.devidos: int = 0
        self.maior_atraso_s: float = 0.0  # quanto o último ponto enviado saiu depois do agendado
        self.fonte_esgotada: bool = False
        self.inicio: Optional[float] = None  # time.time() do agendamento do primeiro ponto
        self.erro: Optional[str] = None
        self._fila: List[bytes] = []
        self._pos: int = 0
        self._resto: bytes = b""

    def _ler(self, quantidade: int) -> List[bytes]:
        """Até `quantidade` linhas completas da fonte, sem o fim de linha."""
        while len(self._fila) - self._pos < quantidade and not self.fonte_esgotada:
            pedaco = os.read(self.fonte, LEITURA_BYTES)
            if not pedaco:
                self.fonte_esgotada = True
                if self._resto:
                    self._fila.append(self._resto)
                    self._resto = b""
                break
            partes = (self._resto + pedaco).split(b"\n")
            self._resto = partes.pop()
            if self._pos:
                del self._fila[:self._pos]
                self._pos = 0
            self._fila.extend(partes)
        fim = min(self._pos + quantidade, len(self._fila))
        linhas = self._fila[self._pos:fim]
        self._pos = fim
        return linhas

    def _escrever(self, linhas: List[bytes]) -> None:
        self.destino.write(b"\n".join(linhas) + b"\n")
        self.destino.flush()

    def run(self) -> None:
        try:
            cabecalho = self._ler(self.linhas_cabecalho)
            if cabecalho:
                self._escrever(cabecalho)
            inicio = time.monotonic()
            self.inicio = time.time()
            proxima = inicio
            while not self.parar.is_set():
                self.devidos = int(self.pontos_por_s * (time.monotonic() - inicio))
                falta = self.devidos - self.enviados
                if falta > 0:
                    linhas = self._ler(falta * self.linhas_por_ponto)
                    if linhas:
                        self._escrever(linhas)
                    self.enviados += len(linhas) // self.linhas_por_ponto
                    atraso = (time.monotonic() - inicio) - self.enviados / self.pontos_por_s
                    self.maior_atraso_s = max(self.maior_atraso_s, atraso)
                    if self.fonte_esgotada and self._pos >= len(self._fila):
                        break
                proxima += FATIA_S
                espera = proxima - time.monotonic()
                if espera > 0:
                    self.parar.wait(espera)
                else:
                    proxima = time.monotonic()
        except OSError as erro:  # BrokenPipeError: o tsbs_load terminou antes do fim
            self.erro = f"alimentador: {erro!r}"
        finally:
            try:
                self.destino.close()
            except OSError:
                pass


@dataclass
class Progresso:
    """Pontos já carregados, pelo último relatório do tsbs_load; lido pelos lotes de consultas."""
    pontos: int = 0
    terminou: threading.Event = field(default_factory=threading.Event)


@dataclass
class Ingestao:
    inicio: float  # time.time()
    fim: float
    totais: Dict[str, float]
    enviados: int
    devidos: int
    maior_atraso_s: float
    fonte_esgotada: bool
    estourou: bool  # o prazo passou antes de a fonte acabar
    erro: Optional[str]  # falha do tsbs_load ou do alimentador


def ingerir_em_taxa(arquitetura: str, fonte: List[str], taxa: int, trabalhadores: int,
                    ferramenta: str, metricas: str, parar: threading.Event, limite_s: float,
                    progresso: Optional[Progresso] = None) -> Ingestao:
    """Ingestão de `taxa` métricas/s, lida da saída do comando `fonte`, até a fonte acabar,
    `parar` ser sinalizado ou `limite_s` passar (ADR-068 e ADR-069).

    A cada relatório de 1 s do tsbs_load, grava em `metricas` os totais carregados e os pontos
    enviados e devidos pelo alimentador. Ao sinal de parar, o alimentador fecha a entrada do
    tsbs_load, que grava os lotes pendentes e termina; se não terminar em 120 s, é encerrado."""
    comando = comando_carga(arquitetura, trabalhadores, os.path.join(ferramenta, "tsbs-ingestao.json"))
    totais: Dict[str, float] = {}
    estourou = threading.Event()
    with open(os.path.join(ferramenta, "tsbs-fonte.txt"), "wb") as erros_fonte:
        origem = subprocess.Popen(fonte, stdout=subprocess.PIPE, stderr=erros_fonte)
    carga = subprocess.Popen(comando, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT)
    if origem.stdout is None or carga.stdin is None or carga.stdout is None:
        raise RuntimeError("os pipes do TSBS não foram abertos")
    alimentador = Alimentador(origem.stdout.fileno(), carga.stdin, arquitetura,
                              taxa / CAMPOS_POR_PONTO, parar)

    def encerrar() -> None:
        if carga.poll() is None:
            carga.kill()

    def estourar() -> None:
        estourou.set()
        parar.set()
        threading.Timer(120.0, encerrar).start()

    relogio = threading.Timer(limite_s, estourar)
    inicio = time.time()
    alimentador.start()
    relogio.start()
    with (open(os.path.join(ferramenta, "tsbs-ingestao.txt"), "w", encoding="utf-8") as original,
          open(metricas, "w", newline="", encoding="utf-8") as f):
        escritor = csv.writer(f)
        escritor.writerow(["ts_ms", "origem", "metrica", "rotulo", "valor"])
        for bruta in carga.stdout:
            linha = bruta.decode("utf-8", "replace")
            original.write(linha)
            serie = LINHA_CARGA.match(linha)
            if serie is not None:
                ts_ms = int(serie.group(1)) * 1000
                linhas_total = int(float(serie.group(3)))
                escritor.writerows([
                    (ts_ms, "gerador", "tsbs_metricas_total", "", int(float(serie.group(2)))),
                    (ts_ms, "gerador", "tsbs_linhas_total", "", linhas_total),
                    (ts_ms, "gerador", "alimentador_pontos_enviados_total", "", alimentador.enviados),
                    (ts_ms, "gerador", "alimentador_pontos_devidos_total", "", alimentador.devidos),
                ])
                f.flush()
                if progresso is not None:
                    progresso.pontos = linhas_total
            resumo = LINHA_RESUMO.match(linha)
            if resumo is not None:
                totais[resumo.group(2)] = float(resumo.group(1))
                totais["segundos"] = float(resumo.group(3))
    codigo = carga.wait()
    fim = time.time()
    relogio.cancel()
    alimentador.join(timeout=30)
    if origem.poll() is None:
        origem.kill()  # parada antes do fim da fonte
    origem.wait()
    if progresso is not None:
        if "rows" in totais:
            progresso.pontos = int(totais["rows"])
        progresso.terminou.set()
    erro: Optional[str] = None
    if codigo != 0:
        erro = f"tsbs_load terminou com código {codigo}; veja ferramenta/tsbs-ingestao.txt"
    elif alimentador.erro is not None:
        erro = alimentador.erro
    return Ingestao(alimentador.inicio or inicio, fim, totais, alimentador.enviados,
                    alimentador.devidos, round(alimentador.maior_atraso_s, 3),
                    alimentador.fonte_esgotada, estourou.is_set(), erro)


def ingerir(saida: str, taxa: int, trabalhadores: int, limite_s: float) -> int:
    """Escrita da carga mista: o arquivo das curtas em taxa fixa até o sinal de parar (ADR-068)."""
    arquitetura = arquitetura_do_ambiente()
    if not esperar(lambda: pronto(arquitetura, True), ESPERA_PRONTO_S):
        avisar(f"{arquitetura} não ficou pronto com o banco {BANCO} em {ESPERA_PRONTO_S:.0f} s")
        return 1
    ferramenta = os.path.join(saida, "ferramenta")
    os.makedirs(ferramenta, exist_ok=True)
    parar = threading.Event()

    def ao_sinal(_signum: int, _frame: Optional[FrameType]) -> None:
        parar.set()

    signal.signal(signal.SIGTERM, ao_sinal)
    signal.signal(signal.SIGINT, ao_sinal)
    avisar(f"ingestão de {taxa} métricas/s em {arquitetura}, com {trabalhadores} trabalhador(es)")
    ingestao = ingerir_em_taxa(arquitetura, ["zcat", os.path.join(DADOS, f"carga-{arquitetura}.gz")],
                               taxa, trabalhadores, ferramenta,
                               os.path.join(saida, "metricas-ingestao.csv"), parar, limite_s)
    gravar_json(os.path.join(saida, "ingestao.json"), {
        "ferramenta": "tcc-gerador-temporal",
        "versao": f"{VERSAO} (TSBS {TSBS}, Python {platform.python_version()})",
        "arquitetura": arquitetura, "taxa_metricas_s": taxa, "trabalhadores": trabalhadores,
        "lote": LOTE, "inicio_ms": int(ingestao.inicio * 1000), "fim_ms": int(ingestao.fim * 1000),
        "metricas": ingestao.totais.get("metrics"), "linhas": ingestao.totais.get("rows"),
        "pontos_enviados": ingestao.enviados, "pontos_devidos": ingestao.devidos,
        "maior_atraso_s": ingestao.maior_atraso_s, "fonte_esgotada": ingestao.fonte_esgotada,
        "estourou": ingestao.estourou, "erro": ingestao.erro,
    })
    if ingestao.erro is not None:
        avisar(ingestao.erro)
        return 1
    avisar(f"ingestão parada: {ingestao.totais.get('metrics', 0):.0f} métricas em "
           f"{ingestao.fim - ingestao.inicio:.1f} s")
    return 0


# --- longa duração (ADR-069) -----------------------------------------------------------------


def periodo_da_longa(taxa: int, medicao_s: int) -> Tuple[str, int]:
    """Fim do período gerado e pontos: o que cabe em `medicao_s` a `taxa` métricas/s."""
    intervalos = taxa * medicao_s // (CAMPOS_POR_PONTO * ESCALA)
    inicio = datetime.fromisoformat(INICIO)
    fim = inicio + timedelta(seconds=intervalos * INTERVALO_S)
    return fim.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), intervalos * ESCALA


def lote_de_consultas(arquitetura: str, lote: int, pontos: int, ferramenta: str,
                      escritor: Any) -> Optional[str]:
    """5 consultas de cada tipo sobre o período já carregado, com 1 trabalhador (ADR-069, item 4).

    As consultas são geradas na hora, com a semente do lote, igual nos dois sistemas. Grava uma
    linha por tipo no CSV dos lotes; devolve o erro, se houver."""
    intervalos = pontos // ESCALA
    fim_dados = datetime.fromisoformat(INICIO) + timedelta(seconds=intervalos * INTERVALO_S + 1)
    fim = fim_dados.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    pasta = os.path.join(ferramenta, "lotes", f"{lote:02d}")
    os.makedirs(pasta, exist_ok=True)
    for tipo in TIPOS_CONSULTA:
        arquivo = os.path.join(pasta, f"{tipo}.consultas")
        with open(arquivo, "wb") as destino:
            gerado = subprocess.run(
                ["tsbs_generate_queries", f"--use-case={CASO}", f"--seed={SEMENTE_LOTES + lote}",
                 f"--scale={ESCALA}", f"--timestamp-start={INICIO}", f"--timestamp-end={fim}",
                 f"--queries={LOTE_POR_TIPO}", f"--query-type={tipo}",
                 f"--format={FORMATOS[arquitetura]}"],
                stdout=destino, stderr=subprocess.PIPE)
        if gerado.returncode != 0:
            return f"lote {lote}, {tipo}: tsbs_generate_queries terminou com código {gerado.returncode}"
        resultados = os.path.join(pasta, f"{tipo}.json")
        inicio_ms = time.time_ns() // 1_000_000
        with open(arquivo, "rb") as entrada:
            rodada = subprocess.run([*comando_consultas(arquitetura), f"--results-file={resultados}"],
                                    stdin=entrada, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True)
        with open(os.path.join(pasta, f"{tipo}.txt"), "w", encoding="utf-8") as saida:
            saida.write(rodada.stdout)
        if rodada.returncode != 0:
            return f"lote {lote}, {tipo}: tsbs_run_queries terminou com código {rodada.returncode}"
        estatisticas = resumo_consultas(rodada.stdout, resultados)
        if estatisticas is None:
            return f"lote {lote}, {tipo}: resumo das consultas não encontrado na saída do TSBS"
        escritor.writerow([inicio_ms, lote, tipo, fim, pontos, *(estatisticas[c] for c in COLUNAS_LOTE)])
    return None


COLUNAS_LOTE: Tuple[str, ...] = ("contagem", "min", "p50", "media", "p95", "p99", "max")


def executar_longa(saida: str, trabalhadores: int, aquecimento_s: int, medicao_s: int,
                   semente: int, taxa: int) -> int:
    """Ingestão contínua em taxa fixa com dados gerados na hora, lotes de consultas a cada 30 min,
    e no fim compressão ou compactações e as consultas das execuções curtas (ADR-069, item 4)."""
    arquitetura = arquitetura_do_ambiente()
    if not esperar(lambda: pronto(arquitetura, True), ESPERA_PRONTO_S):
        avisar(f"{arquitetura} não ficou pronto com o banco {BANCO} em {ESPERA_PRONTO_S:.0f} s")
        return 1
    ferramenta = os.path.join(saida, "ferramenta")
    os.makedirs(os.path.join(ferramenta, "consultas"), exist_ok=True)
    fim_dados, pontos_esperados = periodo_da_longa(taxa, medicao_s)
    fonte = ["tsbs_generate_data", *parametros_comuns(SEMENTE), f"--timestamp-end={fim_dados}",
             f"--log-interval={INTERVALO}", f"--format={FORMATOS[arquitetura]}"]
    limite_s = aquecimento_s + medicao_s + FOLGA_LONGA_S
    avisar(f"ingestão de {taxa} métricas/s com {trabalhadores} trabalhador(es), dados até {fim_dados} "
           f"({pontos_esperados} pontos), prazo de {limite_s} s")

    progresso = Progresso()
    erros_lotes: List[str] = []
    lotes_feitos: List[int] = []
    arquivo_lotes = open(os.path.join(saida, "metricas-lotes.csv"), "w", newline="", encoding="utf-8")
    escritor_lotes = csv.writer(arquivo_lotes)
    escritor_lotes.writerow(["ts_ms", "lote", "tipo", "fim_consultas", "pontos_carregados",
                             *(f"{c}_ms" if c != "contagem" else c for c in COLUNAS_LOTE)])
    relogio_lotes = time.monotonic()

    def rodar_lotes() -> None:
        lote = 1
        while not progresso.terminou.wait(max(0.0, relogio_lotes + lote * LOTE_INTERVALO_S
                                                 - time.monotonic())):
            avisar(f"lote {lote} de consultas, com {progresso.pontos} pontos carregados")
            erro_lote = lote_de_consultas(arquitetura, lote, progresso.pontos, ferramenta, escritor_lotes)
            arquivo_lotes.flush()
            lotes_feitos.append(lote)
            if erro_lote is not None:
                erros_lotes.append(erro_lote)
                avisar(erro_lote)
            lote += 1

    tarefa_lotes = threading.Thread(target=rodar_lotes, name="lotes", daemon=True)
    tarefa_lotes.start()
    ingestao = ingerir_em_taxa(arquitetura, fonte, taxa, trabalhadores, ferramenta,
                               os.path.join(saida, "metricas-gerador.csv"), threading.Event(),
                               limite_s, progresso)
    tarefa_lotes.join()
    final = (lotes_feitos[-1] + 1) if lotes_feitos else 1
    avisar(f"ingestão terminada; lote {final} de consultas, ao fim da ingestão")
    erro_final = lote_de_consultas(arquitetura, final, progresso.pontos, ferramenta, escritor_lotes)
    arquivo_lotes.close()
    if erro_final is not None:
        erros_lotes.append(erro_final)

    carregadas = ingestao.totais.get("metrics", 0.0)
    valores: List[Valor] = [
        ("carga_s", "", round(ingestao.fim - ingestao.inicio, 3), "gerador.py"),
        ("carga_metricas", "", carregadas, "resumo do tsbs_load"),
        ("carga_linhas", "", ingestao.totais.get("rows", 0.0), "resumo do tsbs_load"),
        ("pontos_esperados", "", float(pontos_esperados), "gerador.py"),
        ("pontos_enviados", "", float(ingestao.enviados), "alimentador"),
        ("maior_atraso_s", "", ingestao.maior_atraso_s, "alimentador"),
        ("ingestao_completa", "", float(carregadas == pontos_esperados * CAMPOS_POR_PONTO), "gerador.py"),
        ("lotes_de_consultas", "", float(final), "gerador.py"),
    ]
    erro = ingestao.erro
    if ingestao.estourou:  # o atraso fica registrado, e a execução continua (ADR-069, item 4)
        avisar(f"a ingestão não terminou em {limite_s} s; {ingestao.enviados} de {pontos_esperados} "
               "pontos enviados")
    fases: Dict[str, float] = {}
    if erro is None:
        avisar("compressão" if arquitetura == "timescaledb" else "espera das compactações")
        t = time.monotonic()
        if arquitetura == "timescaledb":
            valores += comprimir_timescaledb()
        else:
            espera, erro = esperar_influxdb(ESPERA_COMPACTACAO_LONGA_S)
            valores += espera
        fases["pos_carga_s"] = round(time.monotonic() - t, 3)
    if erro is None:
        t = time.monotonic()
        for tipo in TIPOS_CONSULTA:
            avisar(f"consultas {tipo}")
            consulta, erro = consultar(arquitetura, tipo, ferramenta)
            valores += consulta
            if erro is not None:
                break
        fases["consultas_s"] = round(time.monotonic() - t, 3)
    if erro is None and erros_lotes:
        erro = erros_lotes[0]

    gravar_json(os.path.join(saida, "resumo.json"), {
        "versao_formato": 1,
        "valores": [{"metrica": m, "rotulo": r, "valor": v, "fonte": f} for m, r, v, f in valores],
    })
    gravar_json(os.path.join(saida, "gerador.json"), {
        "ferramenta": "tcc-gerador-temporal",
        "versao": f"{VERSAO} (TSBS {TSBS}, psycopg {psycopg.__version__}, Python {platform.python_version()})",
        "aquecimento_inicio_ms": int(ingestao.inicio * 1000),
        "medicao_inicio_ms": int((ingestao.inicio + aquecimento_s) * 1000),
        "medicao_fim_ms": int(ingestao.fim * 1000),
        "processo_fim_ms": time.time_ns() // 1_000_000,
        "parametros": {
            "arquitetura": arquitetura, "modo": "taxa fixa, longa duração (ADR-069)",
            "taxa_metricas_s": taxa, "trabalhadores": trabalhadores, "lote": LOTE,
            "gzip": arquitetura == "influxdb", "semente_execucao": semente, "semente_dados": SEMENTE,
            "caso_de_uso": CASO, "escala": ESCALA, "inicio": INICIO, "fim": fim_dados,
            "pontos_esperados": pontos_esperados, "lote_intervalo_s": LOTE_INTERVALO_S,
            "lote_por_tipo": LOTE_POR_TIPO, "semente_lotes": SEMENTE_LOTES,
            "consultas_por_tipo": CONSULTAS_POR_TIPO, "aquecimento_por_tipo": AQUECIMENTO_POR_TIPO,
            "aquecimento_s": aquecimento_s, "limite_carga_s": limite_s,
        },
        "ingestao": {"enviados": ingestao.enviados, "devidos": ingestao.devidos,
                     "maior_atraso_s": ingestao.maior_atraso_s,
                     "fonte_esgotada": ingestao.fonte_esgotada, "estourou": ingestao.estourou,
                     "erro": ingestao.erro},
        "fases_s": fases,
        "erro": erro,
    })
    if erro is not None:
        avisar(erro)
        return 1
    avisar(f"{carregadas:.0f} métricas em {ingestao.fim - ingestao.inicio:.1f} s; fases {fases}")
    return 0


def executar(saida: str, concorrencia: int, aquecimento_s: int, medicao_s: int, semente: int) -> int:
    arquitetura = arquitetura_do_ambiente()
    if not esperar(lambda: pronto(arquitetura, True), ESPERA_PRONTO_S):
        avisar(f"{arquitetura} não ficou pronto com o banco {BANCO} em {ESPERA_PRONTO_S:.0f} s")
        return 1
    ferramenta = os.path.join(saida, "ferramenta")
    os.makedirs(os.path.join(ferramenta, "consultas"), exist_ok=True)
    avisar(f"carga com {concorrencia} trabalhador(es)")
    inicio, fim, totais, erro = carregar(arquitetura, concorrencia, aquecimento_s + medicao_s,
                                         ferramenta, os.path.join(saida, "metricas-gerador.csv"))
    valores: List[Valor] = [("carga_s", "", round(fim - inicio, 3), "gerador.py"),
                            ("carga_metricas", "", totais.get("metrics", 0.0), "resumo do tsbs_load"),
                            ("carga_linhas", "", totais.get("rows", 0.0), "resumo do tsbs_load")]
    fases: Dict[str, float] = {}
    if erro is None:
        avisar("carga concluída; " + ("compressão" if arquitetura == "timescaledb" else "espera das compactações"))
        t = time.monotonic()
        if arquitetura == "timescaledb":
            valores += comprimir_timescaledb()
        else:
            espera, erro = esperar_influxdb()
            valores += espera
        fases["pos_carga_s"] = round(time.monotonic() - t, 3)
    if erro is None:
        t = time.monotonic()
        for tipo in TIPOS_CONSULTA:
            avisar(f"consultas {tipo}")
            consulta, erro = consultar(arquitetura, tipo, ferramenta)
            valores += consulta
            if erro is not None:
                break
        fases["consultas_s"] = round(time.monotonic() - t, 3)

    gravar_json(os.path.join(saida, "resumo.json"), {
        "versao_formato": 1,
        "valores": [{"metrica": m, "rotulo": r, "valor": v, "fonte": f} for m, r, v, f in valores],
    })
    gravar_json(os.path.join(saida, "gerador.json"), {
        "ferramenta": "tcc-gerador-temporal",
        "versao": f"{VERSAO} (TSBS {TSBS}, psycopg {psycopg.__version__}, Python {platform.python_version()})",
        "aquecimento_inicio_ms": int(inicio * 1000),
        "medicao_inicio_ms": int((inicio + aquecimento_s) * 1000),
        "medicao_fim_ms": int(fim * 1000),
        "processo_fim_ms": time.time_ns() // 1_000_000,
        "parametros": {
            "arquitetura": arquitetura, "trabalhadores": concorrencia, "lote": LOTE,
            "gzip": arquitetura == "influxdb", "semente_execucao": semente, "semente_dados": SEMENTE,
            "caso_de_uso": CASO, "escala": ESCALA, "inicio": INICIO, "fim": FIM,
            "consultas_por_tipo": CONSULTAS_POR_TIPO, "aquecimento_por_tipo": AQUECIMENTO_POR_TIPO,
            "aquecimento_s": aquecimento_s, "limite_carga_s": aquecimento_s + medicao_s,
        },
        "fases_s": fases,
        "erro": erro,
    })
    if erro is not None:
        avisar(erro)
        return 1
    avisar(f"{totais.get('metrics', 0):.0f} métricas em {fim - inicio:.1f} s; fases {fases}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Gerador de carga do Eixo C (adaptador do TSBS)")
    sub = parser.add_subparsers(dest="comando", required=True)
    pg = sub.add_parser("gerar", help="gera os dados e as consultas dos dois formatos")
    pg.add_argument("--dados", default=DADOS)
    pp = sub.add_parser("preparar", help="cria o banco benchmark vazio na arquitetura de TCC_ARQUITETURA")
    pp.add_argument("--saida", default="/saida")
    pe = sub.add_parser("executar", help="carga, compressão ou compactação e consultas")
    pe.add_argument("--saida", required=True)
    pe.add_argument("--concorrencia", type=int, required=True)
    pe.add_argument("--aquecimento-s", type=int, required=True)
    pe.add_argument("--medicao-s", type=int, required=True)
    pe.add_argument("--semente", type=int, required=True)
    pe.add_argument("--taxa", type=int, default=None,
                    help="métricas/s em taxa fixa, com dados gerados na hora: longa duração (ADR-069)")
    pi = sub.add_parser("ingerir", help="escrita da carga mista: taxa fixa até o sinal de parar")
    pi.add_argument("--saida", required=True)
    pi.add_argument("--taxa", type=int, required=True)
    pi.add_argument("--trabalhadores", type=int, default=TRABALHADORES_TAXA)
    pi.add_argument("--limite-s", type=float, default=3600.0)
    args = parser.parse_args()

    comando = cast(str, args.comando)
    if comando == "ingerir":
        if cast(int, args.taxa) < CAMPOS_POR_PONTO or cast(int, args.trabalhadores) < 1:
            avisar(f"--taxa precisa ser pelo menos {CAMPOS_POR_PONTO} e --trabalhadores pelo menos 1")
            return 2
        return ingerir(cast(str, args.saida), cast(int, args.taxa), cast(int, args.trabalhadores),
                       cast(float, args.limite_s))
    if comando == "gerar":
        return gerar(cast(str, args.dados))
    if comando == "preparar":
        return preparar(cast(str, args.saida))
    concorrencia = cast(int, args.concorrencia)
    if concorrencia < 1:
        avisar("--concorrencia precisa ser pelo menos 1")
        return 2
    taxa = cast(Optional[int], args.taxa)
    if taxa is not None:
        if taxa < CAMPOS_POR_PONTO:
            avisar(f"--taxa precisa ser pelo menos {CAMPOS_POR_PONTO} métricas/s")
            return 2
        return executar_longa(cast(str, args.saida), concorrencia, cast(int, args.aquecimento_s),
                              cast(int, args.medicao_s), cast(int, args.semente), taxa)
    return executar(cast(str, args.saida), concorrencia, cast(int, args.aquecimento_s),
                    cast(int, args.medicao_s), cast(int, args.semente))


if __name__ == "__main__":
    sys.exit(main())
