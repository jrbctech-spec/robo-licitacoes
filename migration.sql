-- =============================================================
-- Robô Licitações – Script de Migração Incremental (migration.sql)
-- Supabase / PostgreSQL
-- 
-- Executável com segurança em bancos existentes sem apagar ou duplicar dados.
-- =============================================================

-- 1. Extensões e Tipos Customizados (ENUMs)
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

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
        'configurada',
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
-- 2. TABELAS (Defensivo: CREATE TABLE IF NOT EXISTS)
-- -------------------------------------------------------------

-- Tabela: licitacoes
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
    url_sala_disputa    TEXT,
    observacoes         TEXT,
    criado_em           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    atualizado_em       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_uasg_pregao UNIQUE (numero_uasg, numero_pregao)
);

-- Tabela: itens_licitacao
CREATE TABLE IF NOT EXISTS itens_licitacao (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    licitacao_id            UUID NOT NULL REFERENCES licitacoes(id) ON DELETE CASCADE,
    numero_item             INTEGER NOT NULL,
    descricao               TEXT NOT NULL,
    unidade_medida          TEXT DEFAULT 'UN',
    quantidade              NUMERIC(18, 4) DEFAULT 1.0,
    valor_referencia        NUMERIC(18, 4),
    custo_unitario          NUMERIC(18, 4) NOT NULL DEFAULT 0.0,
    margem_minima_percent   NUMERIC(5, 2) NOT NULL DEFAULT 15.0,
    preco_minimo            NUMERIC(18, 4) NOT NULL DEFAULT 0.0,
    preco_inicial_proposta  NUMERIC(18, 4),
    decremento_minimo       NUMERIC(18, 4),
    decremento_percentual   NUMERIC(5, 2),
    melhor_lance_atual      NUMERIC(18, 4),
    nossa_posicao_atual     INTEGER,
    lance_vencedor          NUMERIC(18, 4),
    criado_em               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    atualizado_em           TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_item_licitacao UNIQUE (licitacao_id, numero_item)
);

-- Tabela: historico_lances
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

-- Tabela: logs_sessao
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

-- Tabela: oportunidades_salvas (Buscador do PNCP)
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

-- -------------------------------------------------------------
-- 3. ALTERAÇÕES INCREMENTAIS DE COLUNAS (ADD COLUMN IF NOT EXISTS)
-- -------------------------------------------------------------
ALTER TABLE licitacoes ADD COLUMN IF NOT EXISTS url_sala_disputa TEXT;
ALTER TABLE itens_licitacao ADD COLUMN IF NOT EXISTS valor_referencia NUMERIC(18, 4);
ALTER TABLE itens_licitacao ADD COLUMN IF NOT EXISTS decremento_minimo NUMERIC(18, 4);
ALTER TABLE itens_licitacao ADD COLUMN IF NOT EXISTS decremento_percentual NUMERIC(5, 2);
ALTER TABLE oportunidades_salvas ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'favoritada';

-- -------------------------------------------------------------
-- 4. ÍNDICES DE DESEMPENHO (CREATE INDEX IF NOT EXISTS)
-- -------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_itens_licitacao_id       ON itens_licitacao (licitacao_id);
CREATE INDEX IF NOT EXISTS idx_historico_item_id         ON historico_lances (item_id);
CREATE INDEX IF NOT EXISTS idx_historico_licitacao_id    ON historico_lances (licitacao_id);
CREATE INDEX IF NOT EXISTS idx_historico_tipo_lance      ON historico_lances (tipo_lance);
CREATE INDEX IF NOT EXISTS idx_historico_registrado_em    ON historico_lances (registrado_em DESC);
CREATE INDEX IF NOT EXISTS idx_historico_bloqueado       ON historico_lances (bloqueado_stop_loss) WHERE bloqueado_stop_loss = TRUE;
CREATE INDEX IF NOT EXISTS idx_logs_sessao_id            ON logs_sessao (sessao_id);
CREATE INDEX IF NOT EXISTS idx_logs_licitacao_id         ON logs_sessao (licitacao_id);
CREATE INDEX IF NOT EXISTS idx_logs_nivel                ON logs_sessao (nivel);
CREATE INDEX IF NOT EXISTS idx_logs_registrado_em        ON logs_sessao (registrado_em DESC);
CREATE INDEX IF NOT EXISTS idx_oportunidades_id_pncp     ON oportunidades_salvas (id_pncp);
CREATE INDEX IF NOT EXISTS idx_oportunidades_salvo_em    ON oportunidades_salvas (salvo_em DESC);

-- -------------------------------------------------------------
-- 5. TRIGGER DE ATUALIZAÇÃO AUTOMÁTICA (atualizado_em)
-- -------------------------------------------------------------
CREATE OR REPLACE FUNCTION set_atualizado_em()
RETURNS TRIGGER AS $$
BEGIN
    NEW.atualizado_em = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_licitacoes_atualizado_em') THEN
        CREATE TRIGGER trg_licitacoes_atualizado_em
            BEFORE UPDATE ON licitacoes
            FOR EACH ROW EXECUTE FUNCTION set_atualizado_em();
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_itens_licitacao_atualizado_em') THEN
        CREATE TRIGGER trg_itens_licitacao_atualizado_em
            BEFORE UPDATE ON itens_licitacao
            FOR EACH ROW EXECUTE FUNCTION set_atualizado_em();
    END IF;
END $$;

-- -------------------------------------------------------------
-- 6. VIEW ATUALIZADA: resumo_lances_por_item
-- -------------------------------------------------------------
DROP VIEW IF EXISTS resumo_lances_por_item CASCADE;

CREATE VIEW resumo_lances_por_item AS
SELECT
    il.licitacao_id,
    l.numero_uasg,
    l.numero_pregao,
    l.orgao,
    il.id AS item_id,
    il.numero_item,
    il.descricao,
    il.unidade_medida,
    il.quantidade,
    il.valor_referencia,
    il.custo_unitario,
    il.margem_minima_percent,
    il.preco_minimo,
    il.preco_inicial_proposta,
    il.melhor_lance_atual,
    il.nossa_posicao_atual,
    il.lance_vencedor,
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
    il.licitacao_id, l.numero_uasg, l.numero_pregao, l.orgao,
    il.id, il.numero_item, il.descricao, il.unidade_medida, il.quantidade,
    il.valor_referencia, il.custo_unitario, il.margem_minima_percent,
    il.preco_minimo, il.preco_inicial_proposta, il.melhor_lance_atual,
    il.nossa_posicao_atual, il.lance_vencedor;

-- -------------------------------------------------------------
-- 7. CONFIGURAÇÃO DE SEGURANÇA E RLS (ROW LEVEL SECURITY)
-- Permite leitura e escrita permissiva para evitar bloqueios na API
-- -------------------------------------------------------------
ALTER TABLE licitacoes ENABLE ROW LEVEL SECURITY;
ALTER TABLE itens_licitacao ENABLE ROW LEVEL SECURITY;
ALTER TABLE historico_lances ENABLE ROW LEVEL SECURITY;
ALTER TABLE logs_sessao ENABLE ROW LEVEL SECURITY;
ALTER TABLE oportunidades_salvas ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'Allow_all_licitacoes') THEN
        CREATE POLICY "Allow_all_licitacoes" ON licitacoes FOR ALL USING (true) WITH CHECK (true);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'Allow_all_itens_licitacao') THEN
        CREATE POLICY "Allow_all_itens_licitacao" ON itens_licitacao FOR ALL USING (true) WITH CHECK (true);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'Allow_all_historico_lances') THEN
        CREATE POLICY "Allow_all_historico_lances" ON historico_lances FOR ALL USING (true) WITH CHECK (true);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'Allow_all_logs_sessao') THEN
        CREATE POLICY "Allow_all_logs_sessao" ON logs_sessao FOR ALL USING (true) WITH CHECK (true);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'Allow_all_oportunidades_salvas') THEN
        CREATE POLICY "Allow_all_oportunidades_salvas" ON oportunidades_salvas FOR ALL USING (true) WITH CHECK (true);
    END IF;
END $$;

-- Permissões globais para as roles anon e authenticated/service_role
GRANT ALL ON ALL TABLES IN SCHEMA public TO anon, authenticated, service_role;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO anon, authenticated, service_role;
GRANT ALL ON ALL ROUTINES IN SCHEMA public TO anon, authenticated, service_role;
