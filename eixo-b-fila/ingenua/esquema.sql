-- Esquema da configuração ingênua do Eixo B (ADR-050): só o dado de negócio; a fila é o RabbitMQ.
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

CREATE TABLE pedidos (
    id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cliente   integer NOT NULL,
    valor     bigint NOT NULL,
    criado_em timestamptz NOT NULL DEFAULT now()
);
