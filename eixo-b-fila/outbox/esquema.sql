-- Esquema da configuração com outbox do Eixo B (ADR-050 e ADR-053).
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

CREATE TABLE pedidos (
    id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cliente   integer NOT NULL,
    valor     bigint NOT NULL,
    criado_em timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE saida (
    id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pedido   bigint NOT NULL,
    mensagem bytea NOT NULL
);
