#!/usr/bin/env bash
# Gerador da arquitetura de teste. Cumpre o contrato do gerador de carga
# (harness/README.md, "Contrato do gerador de carga") usando o pgbench.
set -euo pipefail

ESCALA=20
THREADS_MAX=4

esperar_banco() {
    for _ in $(seq 1 120); do
        pg_isready -q && return 0
        sleep 0.5
    done
    echo "o banco não aceitou conexões em 60 s" >&2
    return 1
}

preparar() {
    esperar_banco
    psql -v ON_ERROR_STOP=1 -q \
        -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements" \
        -c "CREATE EXTENSION IF NOT EXISTS pgstattuple"
    pgbench -i -s "$ESCALA" -q
}

executar() {
    local saida="" concorrencia="" aquecimento="" medicao="" semente=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --saida) saida="$2"; shift 2 ;;
            --concorrencia) concorrencia="$2"; shift 2 ;;
            --aquecimento-s) aquecimento="$2"; shift 2 ;;
            --medicao-s) medicao="$2"; shift 2 ;;
            --semente) semente="$2"; shift 2 ;;
            *) echo "argumento desconhecido: $1" >&2; return 2 ;;
        esac
    done
    if [ -z "$saida" ] || [ -z "$concorrencia" ] || [ -z "$aquecimento" ] \
        || [ -z "$medicao" ] || [ -z "$semente" ]; then
        echo "faltam argumentos; veja o uso com: gerador.sh" >&2
        return 2
    fi

    local threads=$(( concorrencia < THREADS_MAX ? concorrencia : THREADS_MAX ))
    mkdir -p "$saida/ferramenta"
    esperar_banco

    local inicio_ms
    inicio_ms=$(date +%s%3N)
    (cd "$saida/ferramenta" && pgbench -n -c "$concorrencia" -j "$threads" \
        -T $(( aquecimento + medicao )) --random-seed="$semente" -l) \
        > "$saida/ferramenta/pgbench.txt"
    local fim_ms
    fim_ms=$(date +%s%3N)

    {
        echo "ts_us,operacao,latencia_us,ok,cliente,itens"
        cat "$saida"/ferramenta/pgbench_log.* | awk '{
            fim = $5 * 1000000 + $6
            if ($3 ~ /^[0-9]+$/) printf "%.0f,transacao_pgbench,%d,1,%d,1\n", fim - $3, $3, $1
            else                 printf "%.0f,transacao_pgbench,,0,%d,1\n", fim, $1
        }' | sort -t, -k1,1n
    } > "$saida/operacoes.csv"
    rm -f "$saida"/ferramenta/pgbench_log.*

    cat > "$saida/gerador.json" <<EOF
{
  "ferramenta": "pgbench",
  "versao": "$(pgbench --version)",
  "aquecimento_inicio_ms": $inicio_ms,
  "medicao_inicio_ms": $(( inicio_ms + aquecimento * 1000 )),
  "medicao_fim_ms": $(( inicio_ms + (aquecimento + medicao) * 1000 )),
  "processo_fim_ms": $fim_ms,
  "parametros": {
    "concorrencia": $concorrencia,
    "threads": $threads,
    "escala": $ESCALA,
    "semente": $semente,
    "aquecimento_s": $aquecimento,
    "medicao_s": $medicao
  }
}
EOF
}

case "${1:-}" in
    preparar) shift; preparar "$@" ;;
    executar) shift; executar "$@" ;;
    *)
        echo "uso: gerador.sh preparar" >&2
        echo "     gerador.sh executar --saida DIR --concorrencia N --aquecimento-s A --medicao-s M --semente S" >&2
        exit 2
        ;;
esac
