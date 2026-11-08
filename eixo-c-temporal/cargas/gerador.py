"""Gerador de carga do Eixo C: adaptador do TSBS ao contrato do harness (ADR-059 a ADR-065).

    gerador.py gerar [--dados DIR]
    gerador.py preparar [--saida DIR]
    gerador.py executar --saida DIR --concorrencia N --aquecimento-s A --medicao-s M --semente S

`gerar` produz, uma única vez, os dados e as consultas dos dois formatos e grava em
DIR/geracao.json os parâmetros, os tempos e o SHA-256 de cada arquivo (ADR-060).
`preparar` cria o banco `benchmark` vazio na arquitetura de TCC_ARQUITETURA. `executar` carrega
o arquivo inteiro com N trabalhadores, comprime (TimescaleDB) ou espera as compactações
(InfluxDB) e roda as consultas (ADR-065). A carga tem até A + M segundos para terminar.
O TimescaleDB é lido pelas variáveis PG* da libpq, e o InfluxDB por TCC_INFLUXDB.
"""

import argparse
import csv
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, LiteralString, Optional, Tuple, cast

import psycopg

EIXO_C: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DADOS: str = os.path.join(EIXO_C, "dados")
FORMATOS: Dict[str, str] = {"timescaledb": "timescaledb", "influxdb": "influx"}  # arquitetura -> TSBS
VERSAO: str = "1"
TSBS: str = "8323e59c74027b108f4ad5ec5d3e498b0101a02e com pr255.patch (ADR-059)"
CASO: str = "cpu-only"
ESCALA: int = 4000
INICIO: str = "2016-01-01T00:00:00Z"
FIM: str = "2016-01-04T00:00:00Z"
FIM_CONSULTAS: str = "2016-01-04T00:00:01Z"  # o gerador de consultas pede o fim depois do último ponto
INTERVALO: str = "10s"
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


def preparar(saida: str) -> int:
    """Banco benchmark vazio: o TSBS cria as tabelas na carga (--do-create-db=false)."""
    arquitetura = arquitetura_do_ambiente()
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


def carregar(arquitetura: str, concorrencia: int, limite_s: float, ferramenta: str,
             metricas: str) -> Tuple[float, float, Dict[str, float], Optional[str]]:
    """Carga do arquivo inteiro; devolve início, fim, totais do resumo do TSBS e o erro, se houver."""
    arquivo = os.path.join(DADOS, f"carga-{arquitetura}.gz")
    comando = [f"tsbs_load_{FORMATOS[arquitetura]}", *opcoes_tsbs(arquitetura), f"--db-name={BANCO}",
               "--do-create-db=false", f"--workers={concorrencia}", f"--batch-size={LOTE}",
               "--reporting-period=1s", f"--results-file={os.path.join(ferramenta, 'tsbs-carga.json')}"]
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


def esperar_influxdb() -> Tuple[List[Valor], Optional[str]]:
    """Espera nenhuma compactação estar em andamento, sem forçar a completa (ADR-065)."""
    inicio = time.monotonic()
    seguidas = 0
    tsm = 0
    while time.monotonic() - inicio < ESPERA_COMPACTACAO_S:
        ativas, tsm = compactacoes_influxdb()
        seguidas = seguidas + 1 if ativas == 0 else 0
        if seguidas >= COMPACTACAO_ESTAVEL:
            return [("espera_compactacao_s", "", round(time.monotonic() - inicio, 3), "gerador.py"),
                    ("tsm_bytes", BANCO, float(tsm), "/debug/vars, tsm1_filestore")], None
        time.sleep(2.0)
    return [], f"as compactações do InfluxDB não pararam em {ESPERA_COMPACTACAO_S:.0f} s"


def consultar(arquitetura: str, tipo: str, ferramenta: str) -> Tuple[List[Valor], Optional[str]]:
    """Aquecimento e as 20 consultas medidas de um tipo, com 1 trabalhador (ADR-061)."""
    pasta = os.path.join(ferramenta, "consultas")
    comando = [f"tsbs_run_queries_{FORMATOS[arquitetura]}", f"--db-name={BANCO}", "--workers=1",
               "--print-interval=0"]
    comando += ([f"--hosts={os.environ.get('PGHOST', 'timescaledb')}",
                 f"--user={os.environ.get('PGUSER', 'postgres')}",
                 f"--pass={os.environ.get('PGPASSWORD', '')}"]
                if arquitetura == "timescaledb" else [f"--urls={url_influxdb()}"])
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
    estatisticas = LINHA_CONSULTAS.findall(saidas[False].split("all queries", 1)[-1])
    if not estatisticas:
        return [], f"{tipo}: resumo das consultas não encontrado na saída do TSBS"
    minimo, mediana, media, maximo, desvio, contagem = estatisticas[0]
    with open(os.path.join(pasta, f"{tipo}.json"), encoding="utf-8") as f:
        quantis = cast(Dict[str, float], json.load(f)["Totals"]["overallQuantiles"]["all_queries"])
    valores: List[Valor] = [("consulta_contagem", tipo, float(contagem), "saída do tsbs_run_queries")]
    for rotulo, ms, fonte in (("min", minimo, "saída"), ("media", media, "saída"), ("desvio", desvio, "saída"),
                              ("p50", mediana, "saída"), ("max", maximo, "saída"),
                              ("p95", quantis["q95"], "JSON"), ("p99", quantis["q99"], "JSON"),
                              ("p999", quantis["q999"], "JSON")):
        valores.append(("consulta_latencia_us", f"{tipo}:{rotulo}", round(float(ms) * 1000, 1),
                        f"tsbs_run_queries ({fonte})"))
    return valores, None


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
    args = parser.parse_args()

    comando = cast(str, args.comando)
    if comando == "gerar":
        return gerar(cast(str, args.dados))
    if comando == "preparar":
        return preparar(cast(str, args.saida))
    concorrencia = cast(int, args.concorrencia)
    if concorrencia < 1:
        avisar("--concorrencia precisa ser pelo menos 1")
        return 2
    return executar(cast(str, args.saida), concorrencia, cast(int, args.aquecimento_s),
                    cast(int, args.medicao_s), cast(int, args.semente))


if __name__ == "__main__":
    sys.exit(main())
