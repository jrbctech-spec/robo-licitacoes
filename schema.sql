-- =============================================================
-- Robô Licitações – Schema DDL para Supabase (PostgreSQL)
-- Execute no SQL Editor do Supabase na ordem abaixo.
-- =============================================================

-- Habilitar extensão para UUIDs automáticos
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- -------------------------------------------------------------
-- ENUM: modo de disputa conforme Lei 14.133/2021
-- -------------------------------------------------------------
DO $$ BEGIN
    CREATE TYPE modo_disputa AS ENUM (
        'aberto',
        'fechado',
        'aberto_fechado',
        'intermediario'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE status_licitacao AS ENUM (
        'pendente',
        'em_andamento',
        'encerrada',
        'cancelada',
        'suspensa'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE tipo_lance AS ENUM (
        'simulado',
        'real',
        'cancelado'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- -------------------------------------------------------------
-- TABELA: licitacoes
-- Representa cada processo licitatório monitorado.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS licitacoes (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    numero_uasg         TEXT NOT NULL,
    numero_pregao       TEXT NOT NULL,
    objeto              TEXT,
    orgao               TEXT,
    modo_disputa        modo_disputa NOT NULL DEFAULT 'aberto',
    data_abertura       TIMESTAMPTZ,
    data_encerramento   TIMESTAMPTZ,
    status              status_licitacao NOT NULL DEFAULT 'pendente',
    url_edital          TEXT,
    observacoes         TEXT,
    criado_em           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    atualizado_em       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_uasg_pregao UNIQUE (numero_uasg, numero_pregao)
);

COMMENT ON TABLE licitacoes IS
    'Processos licitatórios monitorados pelo robô. '
    'Cada registro representa um Pregão Eletrônico no Compras.gov.br.';

-- -------------------------------------------------------------
-- TABELA: itens_licitacao
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS itens_licitacao (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    licitacao_id            UUID NOT NULL REFERENCES licitacoes(id) ON DELETE CASCADE,
    numero_item             INTEGER NOT NULL,
    descricao               TEXT NOT NULL,
    unidade_medida          TEXT,
    quantidade              NUMERIC(18, 4),
    custo_unitario          NUMERIC(18, 4) NOT NULL,
    margem_minima_percent   NUMERIC(5, 2) NOT NULL DEFAULT 5.0,
    preco_minimo            NUMERIC(18, 4) NOT NULL,
    preco_inicial_proposta  NUMERIC(18, 4),
    decremento_minimo       NUMERIC(18, 4),
    decremento_percentual   NUMERIC(5, 2),
    melhor_lance_atual      NUMERIC(18, 4),
    nossa_posicao_atual     INTEGER,
    lance_vencedor          NUMERIC(18, 4),
    criado_em               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    atualizado_em           TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_item_licitacao UNIQUE (licitacao_id, numero_item),
    CONSTRAINT ck_preco_minimo_valido CHECK (preco_minimo >= custo_unitario)
);

COMMENT ON COLUMN itens_licitacao.preco_minimo IS
    'Trava rígida (Stop-Loss): nenhum lance abaixo deste valor será enviado.';

-- -------------------------------------------------------------
-- TABELA: historico_lances
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS historico_lances (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    item_id             UUID NOT NULL REFERENCES itens_licitacao(id) ON DELETE CASCADE,
    licitacao_id        UUID NOT NULL REFERENCES licitacoes(id) ON DELETE CASCADE,
    numero_item         INTEGER NOT NULL,
    valor_lance         NUMERIC(18, 4) NOT NULL,
    valor_concorrente   NUMERIC(18, 4),
    posicao_antes       INTEGER,
    posicao_apos        INTEGER,
    tipo_lance          tipo_lance NOT NULL DEFAULT 'simulado',
    motivo_calculo      TEXT,
    bloqueado_stop_loss BOOLEAN NOT NULL DEFAULT FALSE,
    valor_stop_loss     NUMERIC(18, 4),
    aceito_pelo_sistema BOOLEAN,
    erro_envio          TEXT,
    registrado_em       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE historico_lances IS
    'Trilha de auditoria completa de todos os lances calculados pelo motor de decisão.';

-- -------------------------------------------------------------
-- TABELA: logs_sessao
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS logs_sessao (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    licitacao_id    UUID REFERENCES licitacoes(id) ON DELETE SET NULL,
    sessao_id       TEXT NOT NULL,
    nivel           TEXT NOT NULL DEFAULT 'INFO',
    modulo          TEXT,
    mensagem        TEXT NOT NULL,
    dados_extras    JSONB,
    registrado_em   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE logs_sessao IS
    'Log estruturado e persistente de todos os eventos durante uma sessão de automação.';

-- -------------------------------------------------------------
-- ÍNDICES
-- -------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_itens_licitacao_id    ON itens_licitacao (licitacao_id);
CREATE INDEX IF NOT EXISTS idx_historico_item_id      ON historico_lances (item_id);
CREATE INDEX IF NOT EXISTS idx_historico_licitacao_id ON historico_lances (licitacao_id);
CREATE INDEX IF NOT EXISTS idx_historico_tipo_lance   ON historico_lances (tipo_lance);
CREATE INDEX IF NOT EXISTS idx_historico_bloqueado    ON historico_lances (bloqueado_stop_loss) WHERE bloqueado_stop_loss = TRUE;
-- -------------------------------------------------------------
-- TABELA: oportunidades_salvas
-- Licitações e oportunidades favoritadas no buscador do PNCP.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS oportunidades_salvas (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    id_pncp                 TEXT UNIQUE NOT NULL,
    orgao                   TEXT NOT NULL,
    objeto                  TEXT NOT NULL,
    valor_global            NUMERIC(18, 2) DEFAULT 0.0,
    data_abertura_proposta  TEXT,
    link_edital             TEXT,
    status                  TEXT DEFAULT 'favoritada',
    salvo_em                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE oportunidades_salvas IS
    'Oportunidades de licitação favoritadas e salvas do PNCP para análise e automação.';

-- -------------------------------------------------------------
-- TABELA: licencas (Controle de Assinaturas e HWID)
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS licencas (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chave_licenca       TEXT UNIQUE NOT NULL,
    cliente_nome        TEXT NOT NULL,
    cnpj_ou_cpf         TEXT,
    hwid                TEXT,
    status              TEXT NOT NULL DEFAULT 'ativa',
    valida_ate          TIMESTAMPTZ NOT NULL,
    plano               TEXT NOT NULL DEFAULT 'mensal',
    limite_maquinas     INTEGER NOT NULL DEFAULT 1,
    observacoes         TEXT,
    criado_em           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    atualizado_em       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE licencas IS
    'Licenças de uso e controle de mensalidade dos clientes do Robô de Licitações.';

CREATE INDEX IF NOT EXISTS idx_licencas_chave         ON licencas (chave_licenca);
CREATE INDEX IF NOT EXISTS idx_licencas_status        ON licencas (status);
CREATE INDEX IF NOT EXISTS idx_licencas_valida_ate    ON licencas (valida_ate);

CREATE INDEX IF NOT EXISTS idx_logs_sessao_id         ON logs_sessao (sessao_id);
CREATE INDEX IF NOT EXISTS idx_logs_licitacao_id      ON logs_sessao (licitacao_id);
CREATE INDEX IF NOT EXISTS idx_logs_nivel             ON logs_sessao (nivel);
CREATE INDEX IF NOT EXISTS idx_logs_registrado_em     ON logs_sessao (registrado_em DESC);

-- -------------------------------------------------------------
-- TRIGGER: atualizar coluna atualizado_em automaticamente
-- -------------------------------------------------------------
CREATE OR REPLACE FUNCTION set_atualizado_em()
RETURNS TRIGGER AS $$
BEGIN
    NEW.atualizado_em = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_licitacoes_atualizado_em
    BEFORE UPDATE ON licitacoes
    FOR EACH ROW EXECUTE FUNCTION set_atualizado_em();

CREATE TRIGGER trg_itens_licitacao_atualizado_em
    BEFORE UPDATE ON itens_licitacao
    FOR EACH ROW EXECUTE FUNCTION set_atualizado_em();

-- -------------------------------------------------------------
-- VIEW: resumo_lances_por_item
-- -------------------------------------------------------------
DROP VIEW IF EXISTS resumo_lances_por_item CASCADE;

CREATE VIEW resumo_lances_por_item
WITH (security_invoker = true)
AS
SELECT
    il.licitacao_id,
    l.numero_uasg,
    l.numero_pregao,
    il.id AS item_id,
    il.numero_item,
    il.descricao,
    il.custo_unitario,
    il.preco_minimo,
    il.melhor_lance_atual,
    COUNT(hl.id)                                                       AS total_lances,
    COUNT(hl.id) FILTER (WHERE hl.tipo_lance = 'real')                 AS lances_reais,
    COUNT(hl.id) FILTER (WHERE hl.tipo_lance = 'simulado')             AS lances_simulados,
    COUNT(hl.id) FILTER (WHERE hl.bloqueado_stop_loss)                 AS bloqueios_stop_loss,
    MIN(hl.valor_lance) FILTER (WHERE hl.tipo_lance = 'real')          AS menor_lance_real,
    MAX(hl.registrado_em)                                              AS ultimo_lance_em
FROM itens_licitacao il
JOIN licitacoes l ON l.id = il.licitacao_id
LEFT JOIN historico_lances hl ON hl.item_id = il.id
GROUP BY
    il.licitacao_id, l.numero_uasg, l.numero_pregao,
    il.id, il.numero_item, il.descricao,
    il.custo_unitario, il.preco_minimo, il.melhor_lance_atual;
