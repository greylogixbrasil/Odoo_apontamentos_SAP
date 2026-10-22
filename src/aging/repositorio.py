"""Acesso ao Postgres do modulo de aging (tabelas criadas em db/migrations/0003_aging_oportunidades.sql)."""
import datetime

import psycopg2
import psycopg2.extras

COLUNAS = [
    "base_origem", "id_origem", "codigo", "codigo_norm", "nome", "cliente", "responsavel", "equipe",
    "vertical", "status", "estagio", "data_criacao", "data_fechamento", "fonte_data_fechamento",
    "data_prevista_fechamento", "valor", "probabilidade", "motivo_perda", "tags", "chance",
    "write_date_origem",
]

# Chave fixa do advisory lock do Postgres - garante uma sincronizacao por vez, mesmo que o cron
# e o botao "sincronizar agora" disparem juntos (ou a app rode com mais de um processo).
LOCK_SYNC = 7201001

_SQL_UPSERT = f"""
    INSERT INTO aging_oportunidade ({", ".join(COLUNAS)})
    VALUES %s
    ON CONFLICT (base_origem, id_origem) DO UPDATE SET
        {", ".join(f"{c} = EXCLUDED.{c}" for c in COLUNAS if c not in ("base_origem", "id_origem"))},
        excluida_na_origem = false,
        atualizado_em = now()
    RETURNING (xmax = 0) AS inserido
"""


class AgingRepositorio:
    def __init__(self, dsn: str):
        self.conn = psycopg2.connect(dsn)
        self.conn.autocommit = True

    def close(self):
        self.conn.close()

    # ── sincronizacao ─────────────────────────────────────────
    def tentar_lock(self) -> bool:
        with self.conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_SYNC,))
            return cur.fetchone()[0]

    def liberar_lock(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_SYNC,))

    def upsert(self, registros: list[dict]) -> tuple[int, int]:
        """Insere ou atualiza por (base_origem, id_origem) - rodar de novo com os mesmos dados nao
        duplica nada. Retorna (inseridos, atualizados)."""
        if not registros:
            return 0, 0
        valores = [tuple(r[c] for c in COLUNAS) for r in registros]
        inseridos = 0
        with self.conn.cursor() as cur:
            for inicio in range(0, len(valores), 500):
                resultado = psycopg2.extras.execute_values(cur, _SQL_UPSERT, valores[inicio:inicio + 500],
                                                           fetch=True)
                inseridos += sum(1 for (inserido,) in resultado if inserido)
        return inseridos, len(valores) - inseridos

    def marcar_excluidos(self, base_origem: str, ids_existentes: list[str]) -> int:
        """Marca como excluida o que nao existe mais na origem (e desmarca o que voltou)."""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE aging_oportunidade SET excluida_na_origem = NOT (id_origem = ANY(%s)), atualizado_em = now()
                WHERE base_origem = %s AND excluida_na_origem IS DISTINCT FROM NOT (id_origem = ANY(%s))
                RETURNING excluida_na_origem
                """,
                (ids_existentes, base_origem, ids_existentes),
            )
            return sum(1 for (excluida,) in cur.fetchall() if excluida)

    def iniciar_log(self, tipo: str) -> int:
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO aging_sync_log (tipo) VALUES (%s) RETURNING id", (tipo,))
            return cur.fetchone()[0]

    def finalizar_log(self, log_id: int, status: str, lidos: int = 0, inseridos: int = 0,
                      atualizados: int = 0, marcados_excluidos: int = 0, erro: str | None = None):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE aging_sync_log SET finalizado_em = now(), status = %s, lidos = %s, inseridos = %s,
                       atualizados = %s, marcados_excluidos = %s, erro = %s
                WHERE id = %s
                """,
                (status, lidos, inseridos, atualizados, marcados_excluidos, erro, log_id),
            )

    def ultima_sync_ok(self, tipos=("incremental", "completa")) -> dict | None:
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM aging_sync_log WHERE status = 'ok' AND tipo = ANY(%s)
                ORDER BY iniciado_em DESC LIMIT 1
                """,
                (list(tipos),),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def ultimas_syncs(self, limite: int = 10) -> list[dict]:
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM aging_sync_log ORDER BY id DESC LIMIT %s", (limite,))
            return [dict(r) for r in cur.fetchall()]

    # ── consulta (dashboard) ──────────────────────────────────
    def consultar_base(self, where: str, params: list, colunas: str = "*", ordem: str = "data_criacao DESC, id DESC",
                       limite: int | None = None, offset: int = 0) -> list[dict]:
        sql = f"SELECT {colunas} FROM aging_base WHERE {where} ORDER BY {ordem}"
        if limite is not None:
            sql += " LIMIT %s OFFSET %s"
            params = [*params, limite, offset]
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def contar_base(self, where: str, params: list) -> int:
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM aging_base WHERE {where}", params)
            return cur.fetchone()[0]

    def opcoes_filtros(self) -> dict:
        # uma passada so pela view (cada leitura dela custa uma avaliacao completa das regras)
        with self.conn.cursor() as cur:
            cur.execute("""
                WITH b AS MATERIALIZED (
                    SELECT vertical_final, cliente, base_origem, responsavel_unificado, tipo_oferta, data_criacao
                    FROM aging_base
                )
                SELECT (SELECT array_agg(DISTINCT vertical_final ORDER BY vertical_final) FROM b WHERE vertical_final IS NOT NULL),
                       -- responsavel_unificado: um nome por pessoa (o mesmo responsavel aparece escrito diferente nas bases)
                       (SELECT array_agg(DISTINCT responsavel_unificado ORDER BY responsavel_unificado) FROM b
                        WHERE responsavel_unificado IS NOT NULL),
                       (SELECT array_agg(DISTINCT cliente ORDER BY cliente) FROM b WHERE cliente IS NOT NULL),
                       (SELECT array_agg(DISTINCT base_origem ORDER BY base_origem) FROM b),
                       (SELECT array_agg(DISTINCT tipo_oferta ORDER BY tipo_oferta) FROM b WHERE tipo_oferta IS NOT NULL),
                       (SELECT min(data_criacao) FROM b), (SELECT max(data_criacao) FROM b)
            """)
            verticais, responsaveis, clientes, bases, tipos, minimo, maximo = cur.fetchone()
            return {
                "verticais": verticais or [],
                "responsaveis": responsaveis or [],
                "clientes": clientes or [],
                "bases": bases or [],
                "tipos_oferta": tipos or [],
                "criacao_min": minimo.isoformat() if isinstance(minimo, datetime.date) else None,
                "criacao_max": maximo.isoformat() if isinstance(maximo, datetime.date) else None,
            }

    def qualidade_dos_dados(self) -> dict:
        """Pontos que afetam a leitura dos numeros (base completa, sem filtros) - painel da aba Base de dados."""
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                WITH b AS MATERIALIZED (SELECT * FROM aging_base)
                SELECT
                    count(*) AS total,
                    count(*) FILTER (WHERE NOT usar_na_analise) AS fora_analise,
                    count(*) FILTER (WHERE motivo_fora_analise LIKE 'Data de criação%%') AS fora_carga_inicial,
                    count(*) FILTER (WHERE motivo_fora_analise LIKE 'Fechamento anterior%%') AS fora_aging_negativo,
                    count(*) FILTER (WHERE motivo_fora_analise LIKE 'Arquivada%%') AS fora_arquivadas,
                    count(*) FILTER (WHERE fonte_data_fechamento LIKE '%%sem FechamentoReal') AS sharepoint_sem_fechamento_real,
                    count(*) FILTER (WHERE andamento_do_odoo) AS sharepoint_andamento_odoo,
                    count(*) FILTER (WHERE fechada_no_odoo) AS sharepoint_fechada_odoo,
                    count(*) FILTER (WHERE base_origem = 'Odoo' AND vertical IS NOT NULL) AS odoo_vertical_da_equipe,
                    count(*) FILTER (WHERE base_origem = 'Odoo' AND vertical IS NULL) AS odoo_vertical_inferida,
                    count(DISTINCT responsavel) AS grafias_responsavel,
                    count(DISTINCT responsavel_chave) AS pessoas,
                    count(*) FILTER (WHERE responsavel IS DISTINCT FROM responsavel_unificado) AS linhas_responsavel_ajustado,
                    count(*) FILTER (WHERE tipo_oferta LIKE 'Outros%%') AS tipo_outros,
                    count(*) FILTER (WHERE cliente_recorrente) AS cliente_recorrente,
                    count(*) FILTER (WHERE responsavel = 'Administrator') AS responsavel_administrator,
                    count(*) FILTER (WHERE codigo_norm IS NULL) AS sem_codigo,
                    count(*) FILTER (WHERE codigo_norm IN (
                        SELECT codigo_norm FROM b WHERE codigo_norm IS NOT NULL GROUP BY 1 HAVING count(*) > 1)) AS codigo_repetido,
                    count(*) FILTER (WHERE coalesce(valor, 0) = 0) AS valor_zerado,
                    count(*) FILTER (WHERE valor > 50000000) AS valor_acima_50mi,
                    coalesce(sum(valor) FILTER (WHERE valor > 50000000), 0) AS soma_acima_50mi,
                    coalesce(sum(valor), 0) AS soma_total
                FROM b
            """)
            return {k: float(v) if hasattr(v, "as_tuple") else v for k, v in cur.fetchone().items()}
