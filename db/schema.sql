CREATE TABLE IF NOT EXISTS import_execucao (
    id               SERIAL PRIMARY KEY,
    iniciado_em      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finalizado_em    TIMESTAMPTZ,
    ambiente         TEXT NOT NULL,              -- test | prod
    arquivo_origem   TEXT NOT NULL,
    dry_run          BOOLEAN NOT NULL,
    total_linhas          INT NOT NULL DEFAULT 0,
    total_enviadas        INT NOT NULL DEFAULT 0,
    total_ja_existentes   INT NOT NULL DEFAULT 0,
    total_puladas         INT NOT NULL DEFAULT 0,
    total_erros           INT NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'em_andamento',  -- em_andamento | concluido | falhou
    -- origem: excel_manual (tela atual) | odoo_manual (botao "rodar agora") | odoo_automatico (2h)
    origem           TEXT NOT NULL DEFAULT 'excel_manual'
);

CREATE TABLE IF NOT EXISTS import_linha (
    id               SERIAL PRIMARY KEY,
    execucao_id      INT NOT NULL REFERENCES import_execucao(id),
    linha_excel      INT NOT NULL,
    colaborador      TEXT,
    data             DATE,
    projeto          TEXT,
    idsap            TEXT,
    tarefa           TEXT,
    descricao        TEXT,
    horas            NUMERIC(6,2),
    intervalo        TEXT,
    start_time       TEXT,
    end_time         TEXT,
    break_time       TEXT,
    employee_id_sap  INT,
    -- status: pendente_dry_run | enviado | ja_existe_sap | pulado_zero_horas |
    --         erro_horario_invalido | erro_dados_ausentes | erro_envio
    status           TEXT NOT NULL,
    motivo           TEXT,
    sap_response     JSONB,
    sap_request      JSONB,
    criado_em        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_import_linha_execucao ON import_linha(execucao_id);
CREATE INDEX IF NOT EXISTS idx_import_linha_status ON import_linha(status);
CREATE INDEX IF NOT EXISTS idx_import_linha_colab_data ON import_linha(colaborador, data);

-- Log de aplicacao (substitui os antigos logs em arquivo/print) - alimentado pelo
-- PostgresLogHandler (src/log_handler.py), configurado via src/logging_setup.py tanto
-- na app FastAPI quanto no script agendado (scripts/run_import_odoo_automatico.py).
CREATE TABLE IF NOT EXISTS app_log (
    id           BIGSERIAL PRIMARY KEY,
    criado_em    TIMESTAMPTZ NOT NULL DEFAULT now(),
    nivel        TEXT NOT NULL,   -- INFO | WARNING | ERROR | ...
    logger       TEXT NOT NULL,   -- nome do logger (modulo de origem)
    mensagem     TEXT NOT NULL,
    execucao_id  INT REFERENCES import_execucao(id),
    detalhes     TEXT             -- traceback, quando o registro vier de uma excecao
);

CREATE INDEX IF NOT EXISTS idx_app_log_criado_em ON app_log(criado_em);
CREATE INDEX IF NOT EXISTS idx_app_log_nivel ON app_log(nivel);
CREATE INDEX IF NOT EXISTS idx_app_log_execucao ON app_log(execucao_id);

-- Flag manual/automatico do envio diario Odoo -> SAP (linha unica, id sempre 1).
-- O agendamento em si e uma Tarefa Agendada do Windows que roda todo dia as 2h;
-- essa tabela so diz se o script deve realmente enviar (automatico) ou so logar e sair (manual).
CREATE TABLE IF NOT EXISTS automacao_config (
    id             SMALLINT PRIMARY KEY DEFAULT 1,
    modo           TEXT NOT NULL DEFAULT 'manual' CHECK (modo IN ('manual', 'automatico')),
    atualizado_em  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT automacao_config_singleton CHECK (id = 1)
);
INSERT INTO automacao_config (id, modo) VALUES (1, 'manual') ON CONFLICT (id) DO NOTHING;

-- Modulo Aging de Oportunidades (mesmo conteudo de db/migrations/0003_aging_oportunidades.sql)
-- A view depende das tabelas/funcoes abaixo - sai primeiro e e recriada no fim.
DROP VIEW IF EXISTS aging_base;

-- Mesma pessoa tem nomes diferentes nas duas bases ("Marcos Liebl" no SharePoint, "Marcos Vinicius
-- Henrique Liebl" no Odoo; "Fábio Alves" / "Fábio Alves Ferreira"; "Adir Trevisan Jr" / "Jr.").
-- Palavras do nome sem acento/pontuacao e sem particulas (da, de, dos, jr, filho...) - a view junta
-- dois nomes quando tem o mesmo primeiro nome e todas as palavras do mais curto estao no mais longo.
CREATE OR REPLACE FUNCTION aging_palavras_nome(nome TEXT) RETURNS TEXT[]
LANGUAGE sql IMMUTABLE AS $$
    SELECT coalesce(array_agg(p ORDER BY n), '{}')
    FROM unnest(string_to_array(trim(regexp_replace(regexp_replace(lower(translate(coalesce(nome, ''),
             'áàâãäéèêëíìîïóòôõöúùûüçÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ',
             'aaaaaeeeeiiiiooooouuuucaaaaaeeeeiiiiooooouuuuc')), '[^a-z0-9 ]', ' ', 'g'), '\s+', ' ', 'g')), ' '))
         WITH ORDINALITY AS t(p, n)
    WHERE p <> '' AND p NOT IN ('da', 'de', 'do', 'dos', 'das', 'e', 'jr', 'junior', 'filho', 'neto')
$$;

-- Tipo de oferta: nao existe campo nas bases - classificado por palavras-chave no titulo (primeira
-- regra que casar vale; titulo sem pista = 'Outros / não identificado'). E uma aproximacao.
-- Mudou alguma regra? Rodar a migracao de novo recalcula a coluna (UPDATE no fim do arquivo).
CREATE OR REPLACE FUNCTION aging_tipo_oferta(nome TEXT) RETURNS TEXT
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN t ~ '(aditiv|adiciona|complement|addendum|extensao de escopo)' THEN 'Aditivos e complementos'
        WHEN t ~ '(treinament|capacitac|curso|training|workshop)' THEN 'Treinamento'
        WHEN t ~ '(licen[cs]|software|assinatura|subscription|renovac)' THEN 'Licenças e software'
        WHEN t ~ '(migra|retrofit|moderniza|atualiza|upgrade|substitui|\mtroca|reforma)' THEN 'Migração, retrofit e atualização'
        WHEN t ~ '(comissiona|commissioning|comissioning|start[ -]?up|partida|campo|field|montagem|instalac|\mtestes?\M|\mtaf\M|\mfat\M|\msat\M)' THEN 'Comissionamento e campo'
        WHEN t ~ '(suporte|support|assist|atendimento|horas tecnicas|\mhh\M|plantao|manutenc|sustentac|contrato)' THEN 'Suporte e atendimento'
        WHEN t ~ '(estudo|hazop|hazid|consultori|engenharia|engineering|projeto basico|projeto executivo|detalhamento|analise|auditori|levantamento|diagnostic|\mfeed\M|\mdde\M|laudo|conceitual|feasibility|especific|\mdesign\M)' THEN 'Estudos, engenharia e consultoria'
        WHEN t ~ '(painel|paineis|panel|\mccms?\M|\mqgbt\M|\mqdf\M|eletric|cabine|switchgear|cubiculo|acionament|\mquadros?\M|eletrocentro|comando|gaveta|bornes|tomada|subestac|transformador|disjuntor|capacitor|\mdrives?\M|iluminac|lighting|caixas? de junc|juncao)' THEN 'Painéis e elétrica'
        WHEN t ~ '(automac|automation|\mclp\M|\mplc\M|scada|supervisori|\mihm\M|\mhmi\M|sdcd|\mdcs\M|pcs ?7|programac|sistema|controle|intertravament|\mtaas\M|\mrede\M|profibus|integrac|remota|visao|deteccao|\maut\M|\myot\M|\msfc\M)' THEN 'Projetos de automação'
        WHEN t ~ '(sobressalent|component|\mpecas?\M|\mkit\M|cartao|modulo|inversor|material|equipament|fornecimento|spare|\mfonte\M|caixaria|reposic|bateria|transmissor|instrument|hardware|\mcpus?\M|switch|estac(ao|oes) de trabalho|bancada)' THEN 'Componentes e sobressalentes'
        WHEN t ~ '(melhori|ajuste|correc|alterac|modific|otimiz|optimiz|adequac|ampliac|implementac)' THEN 'Melhorias e ajustes'
        ELSE 'Outros / não identificado'
    END
    FROM (SELECT lower(translate(coalesce(nome, ''),
                 'áàâãäéèêëíìîïóòôõöúùûüçÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ',
                 'aaaaaeeeeiiiiooooouuuucaaaaaeeeeiiiiooooouuuuc')) AS t) x
$$;

-- Oportunidades das duas bases, ja com nomes resolvidos. O aging NAO e gravado (depende de
-- "hoje" pras abertas) - e calculado na view aging_base.
CREATE TABLE IF NOT EXISTS aging_oportunidade (
    id                        BIGSERIAL PRIMARY KEY,
    base_origem               TEXT NOT NULL CHECK (base_origem IN ('Odoo', 'SharePoint')),
    id_origem                 TEXT NOT NULL,          -- crm.lead.id (Odoo) | coluna ID da lista (SharePoint)
    codigo                    TEXT,                   -- como esta na origem: C24.0474, 26.0916
    codigo_norm               TEXT,                   -- normalizado pra cruzar as bases: 24.474, 26.916
    nome                      TEXT,
    cliente                   TEXT,
    responsavel               TEXT,
    equipe                    TEXT,                   -- equipe de vendas do Odoo (crm.team), como esta
    vertical                  TEXT,                   -- NULL = equipe generica, inferida na view
    status                    TEXT NOT NULL CHECK (status IN ('ganha', 'perdida', 'aberta', 'arquivada')),
    estagio                   TEXT,
    data_criacao              DATE,
    data_fechamento           DATE,
    fonte_data_fechamento     TEXT,
    data_prevista_fechamento  DATE,
    valor                     NUMERIC(16, 2),
    probabilidade             NUMERIC(6, 2),
    motivo_perda              TEXT,
    tags                      TEXT,
    chance                    TEXT,
    excluida_na_origem        BOOLEAN NOT NULL DEFAULT false,
    write_date_origem         TIMESTAMPTZ,
    criado_em                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em             TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (base_origem, id_origem)
);

-- Gravada (e nao calculada na view) porque a classificacao usa regex - por linha custaria caro a cada consulta.
ALTER TABLE aging_oportunidade
    ADD COLUMN IF NOT EXISTS tipo_oferta TEXT GENERATED ALWAYS AS (aging_tipo_oferta(nome)) STORED;
-- Versao anterior gravava uma chave de pessoa (primeiro + ultimo nome) - trocada pela regra da view.
ALTER TABLE aging_oportunidade DROP COLUMN IF EXISTS responsavel_chave;
DROP FUNCTION IF EXISTS aging_chave_pessoa(TEXT);

CREATE INDEX IF NOT EXISTS idx_aging_oport_codigo_norm ON aging_oportunidade(codigo_norm);
CREATE INDEX IF NOT EXISTS idx_aging_oport_data_criacao ON aging_oportunidade(data_criacao);

-- Uma linha por execucao: sync incremental (hora em hora), completa (diaria) e importacao historica.
CREATE TABLE IF NOT EXISTS aging_sync_log (
    id                  SERIAL PRIMARY KEY,
    tipo                TEXT NOT NULL CHECK (tipo IN ('incremental', 'completa', 'historico')),
    iniciado_em         TIMESTAMPTZ NOT NULL DEFAULT now(),
    finalizado_em       TIMESTAMPTZ,
    status              TEXT NOT NULL DEFAULT 'em_andamento' CHECK (status IN ('em_andamento', 'ok', 'falhou')),
    lidos               INT NOT NULL DEFAULT 0,
    inseridos           INT NOT NULL DEFAULT 0,
    atualizados         INT NOT NULL DEFAULT 0,
    marcados_excluidos  INT NOT NULL DEFAULT 0,
    erro                TEXT
);

CREATE INDEX IF NOT EXISTS idx_aging_sync_log_status ON aging_sync_log(status, iniciado_em);

-- Ajustes manuais das premissas do dashboard original (aba Premissas da planilha).
CREATE TABLE IF NOT EXISTS aging_ajuste (
    id      SERIAL PRIMARY KEY,
    tipo    TEXT NOT NULL CHECK (tipo IN ('excluir_odoo', 'vertical_responsavel')),
    chave   TEXT NOT NULL,   -- excluir_odoo: crm.lead.id | vertical_responsavel: nome do responsavel
    valor   TEXT,            -- vertical_responsavel: a vertical
    motivo  TEXT,
    UNIQUE (tipo, chave)
);

INSERT INTO aging_ajuste (tipo, chave, valor, motivo) VALUES
    ('excluir_odoo', '18525', NULL, 'Teste criado pelo Administrator: "Oportunidade da ANDRITZ PULP & PAPER"'),
    ('excluir_odoo', '19053', NULL, 'Teste criado pelo Administrator: "- Comissionamento LAJA"'),
    ('vertical_responsavel', 'João Battisti', 'ENG', 'Equipe generica no Odoo e sem historico no SharePoint (informado pelo usuario)')
ON CONFLICT (tipo, chave) DO NOTHING;

-- Base de analise: aplica as regras do dashboard na hora da consulta.
--  * Oportunidade que existe no SharePoint vale so a versao do SharePoint: a do Odoo sai quando
--    tem o mesmo codigo normalizado E (foi criada ate a migracao, 15/12/2025, ou tem o mesmo
--    titulo) - sem essa condicao, aditivos novos que reaproveitam um codigo antigo sumiriam.
--    Sem codigo (vazio/0): copia da migracao (criada ate 15/12/2025) casa so pelo titulo - a
--    migracao nao trouxe o cliente delas -; depois disso, titulo + cliente.
--    Excecao: estava aberta no SharePoint e seguiu no Odoo - estagio atual e (se fechou em 2026)
--    status/fechamento/motivo vem do Odoo (CTE copia_no_odoo; colunas andamento_do_odoo/fechada_no_odoo).
--  * Responsavel unificado: o nome mais completo da mesma pessoa (ver aging_palavras_nome).
--  * Vertical das equipes genericas (Sales): ajuste manual, senao a vertical predominante do
--    responsavel no SharePoint.
--  * Aging = (fechamento ou hoje) - criacao, em dias corridos.
--  * Fora da analise: arquivada sem ganho/perda, criacao 16/12/2019 (carga inicial do
--    SharePoint, nao e a data real) e aging negativo (fechamento antes da criacao).
CREATE VIEW aging_base AS
WITH odoo_que_existe_no_sharepoint AS (
    -- juncoes por igualdade (hash join) em vez de EXISTS correlacionado - o EXISTS varria o
    -- SharePoint inteiro pra cada linha do Odoo (~30 mi comparacoes, 3,5 s por consulta)
    SELECT o.id
    FROM aging_oportunidade o
    JOIN aging_oportunidade s ON s.base_origem = 'SharePoint' AND s.codigo_norm = o.codigo_norm
    WHERE o.base_origem = 'Odoo'
      AND (o.data_criacao <= DATE '2025-12-15' OR lower(trim(s.nome)) = lower(trim(o.nome)))
    UNION
    SELECT o.id
    FROM aging_oportunidade o
    JOIN aging_oportunidade s ON s.base_origem = 'SharePoint' AND lower(trim(s.nome)) = lower(trim(o.nome))
    WHERE o.base_origem = 'Odoo' AND o.codigo_norm IS NULL
      AND (o.data_criacao <= DATE '2025-12-15' OR lower(trim(s.cliente)) = lower(trim(o.cliente)))
),
-- Estava aberta no SharePoint e continua no Odoo: o SharePoint parou no tempo, entao o andamento vem
-- da copia no Odoo - estagio atual e, se ela fechou depois da migracao, status, data de fechamento e
-- motivo da perda (criacao, vertical, cliente, responsavel e valor continuam do SharePoint). So conta
-- fechamento apos 15/12/2025 - as datas da carga de migracao (15/12/2025 18:24-18:27) nao sao a data
-- real do ganho. Varias copias: a fechada mais recente, senao a aberta alterada por ultimo.
copia_no_odoo AS (
    SELECT DISTINCT ON (s.id) s.id, o.status, o.data_fechamento, o.motivo_perda, o.estagio
    FROM aging_oportunidade s
    JOIN aging_oportunidade o ON o.base_origem = 'Odoo' AND o.codigo_norm = s.codigo_norm
                             AND (o.data_criacao <= DATE '2025-12-15' OR lower(trim(o.nome)) = lower(trim(s.nome)))
    WHERE s.base_origem = 'SharePoint' AND s.status = 'aberta' AND NOT o.excluida_na_origem
      AND (o.status = 'aberta' OR (o.status IN ('ganha', 'perdida') AND o.data_fechamento > DATE '2025-12-15'))
    ORDER BY s.id, (o.status <> 'aberta') DESC, o.data_fechamento DESC NULLS LAST, o.write_date_origem DESC NULLS LAST
),
oportunidade AS (
    SELECT o.id, o.base_origem, o.id_origem, o.codigo, o.codigo_norm, o.nome, o.cliente, o.responsavel,
           o.equipe, o.vertical,
           coalesce(c.status, o.status) AS status,
           coalesce(c.estagio, o.estagio) AS estagio,
           o.data_criacao,
           coalesce(c.data_fechamento, o.data_fechamento) AS data_fechamento,
           CASE WHEN c.status IN ('ganha', 'perdida') THEN 'Odoo (aberta no SharePoint, fechada em 2026)'
                ELSE o.fonte_data_fechamento END AS fonte_data_fechamento,
           o.data_prevista_fechamento, o.valor, o.probabilidade,
           CASE WHEN c.status IN ('ganha', 'perdida') THEN c.motivo_perda ELSE o.motivo_perda END AS motivo_perda,
           o.tags, o.chance, o.excluida_na_origem, o.write_date_origem, o.criado_em, o.atualizado_em,
           o.tipo_oferta,
           coalesce(c.status IN ('ganha', 'perdida'), false) AS fechada_no_odoo,
           (c.id IS NOT NULL) AS andamento_do_odoo
    FROM aging_oportunidade o
    LEFT JOIN copia_no_odoo c ON c.id = o.id
),
-- mapa nome -> nome unificado, calculado so sobre os nomes distintos (~60), nao por linha
nomes AS (
    SELECT responsavel, aging_palavras_nome(responsavel) AS palavras
    FROM (SELECT DISTINCT responsavel FROM aging_oportunidade WHERE responsavel IS NOT NULL) d
),
pessoa AS (
    SELECT DISTINCT ON (a.responsavel) a.responsavel,
           regexp_replace(trim(b.responsavel), '\s+', ' ', 'g') AS unificado
    FROM nomes a
    JOIN nomes b ON b.responsavel = a.responsavel
                 OR (cardinality(a.palavras) >= 2 AND b.palavras[1] = a.palavras[1] AND b.palavras @> a.palavras)
    ORDER BY a.responsavel, cardinality(b.palavras) DESC, length(b.responsavel) DESC, b.responsavel
),
vertical_predominante AS (
    SELECT DISTINCT ON (p.unificado) p.unificado, o.vertical
    FROM aging_oportunidade o JOIN pessoa p ON p.responsavel = o.responsavel
    WHERE o.base_origem = 'SharePoint' AND o.vertical IS NOT NULL
    GROUP BY p.unificado, o.vertical
    ORDER BY p.unificado, count(*) DESC, o.vertical
),
vertical_manual AS (
    SELECT DISTINCT ON (coalesce(p.unificado, aj.chave)) coalesce(p.unificado, aj.chave) AS unificado,
           aj.valor AS vertical
    FROM aging_ajuste aj LEFT JOIN pessoa p ON p.responsavel = aj.chave
    WHERE aj.tipo = 'vertical_responsavel'
),
consolidada AS (
    SELECT o.*,
           coalesce(p.unificado, o.responsavel) AS responsavel_unificado,
           lower(coalesce(p.unificado, o.responsavel)) AS responsavel_chave,
           COALESCE(o.vertical, vm.vertical, vp.vertical) AS vertical_final,
           CASE WHEN o.status = 'aberta' THEN CURRENT_DATE - o.data_criacao
                ELSE COALESCE(o.data_fechamento, CURRENT_DATE) - o.data_criacao END AS aging_dias
    FROM oportunidade o
    LEFT JOIN pessoa p ON p.responsavel = o.responsavel
    LEFT JOIN vertical_manual vm ON vm.unificado = p.unificado
    LEFT JOIN vertical_predominante vp ON vp.unificado = p.unificado
    WHERE NOT o.excluida_na_origem
      AND NOT (o.base_origem = 'Odoo' AND o.id_origem IN (
            SELECT chave FROM aging_ajuste WHERE tipo = 'excluir_odoo'))
      AND o.id NOT IN (SELECT id FROM odoo_que_existe_no_sharepoint)
),
-- Cliente recorrente = ja tinha uma vitoria registrada antes de a oportunidade ser criada.
-- A base comeca em 2019, entao os primeiros anos tem mais "novos" do que de fato eram.
primeira_vitoria AS (
    SELECT lower(trim(cliente)) AS cliente_norm, min(data_fechamento) AS data
    FROM consolidada WHERE status = 'ganha' AND cliente IS NOT NULL
    GROUP BY 1
)
SELECT c.*,
       (pv.data IS NOT NULL AND pv.data < c.data_criacao) AS cliente_recorrente,
       CASE WHEN c.aging_dias IS NULL THEN NULL
            WHEN c.aging_dias < 0 THEN 'Negativo'
            WHEN c.aging_dias <= 30 THEN '0-30 dias'
            WHEN c.aging_dias <= 60 THEN '31-60 dias'
            WHEN c.aging_dias <= 90 THEN '61-90 dias'
            WHEN c.aging_dias <= 180 THEN '91-180 dias'
            WHEN c.aging_dias <= 365 THEN '181-365 dias'
            ELSE 'Acima de 365 dias' END AS faixa_aging,
       (c.status <> 'arquivada'
        AND c.data_criacao IS NOT NULL
        AND c.data_criacao <> DATE '2019-12-16'
        AND (c.status = 'aberta' OR c.aging_dias >= 0)) AS usar_na_analise,
       CASE WHEN c.status = 'arquivada' THEN 'Arquivada no Odoo sem ganho/perda'
            WHEN c.data_criacao = DATE '2019-12-16' THEN 'Data de criação = carga inicial do SharePoint (16/12/2019)'
            WHEN c.status <> 'aberta' AND c.aging_dias < 0 THEN 'Fechamento anterior à criação'
            END AS motivo_fora_analise
FROM consolidada c
LEFT JOIN primeira_vitoria pv ON pv.cliente_norm = lower(trim(c.cliente));

-- Recalcula a coluna gerada tipo_oferta se as regras de aging_tipo_oferta mudaram
UPDATE aging_oportunidade SET nome = nome WHERE tipo_oferta IS DISTINCT FROM aging_tipo_oferta(nome);
