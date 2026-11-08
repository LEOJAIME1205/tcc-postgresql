-- Esquema da fila consolidada do Eixo B (ADR-050 e ADR-051).
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

CREATE TABLE pedidos (
    id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cliente   integer NOT NULL,
    valor     bigint NOT NULL,
    criado_em timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE tarefas (
    id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pedido   bigint NOT NULL,
    mensagem bytea NOT NULL
);
