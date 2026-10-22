# Aging de Oportunidades Comerciais

Dashboard em `/aging`: oportunidades do CRM do Odoo, atualizadas de hora em hora, mais a base
histórica do SharePoint (até 2025), importada uma vez.

**Acesso:** `/aging` e `/api/aging/*` são **públicos** (sem login), para os colaboradores entrarem
pelo link de outra aplicação. A página não tem menu nem links para as outras telas do ApontaSAP,
que continuam exigindo login. A sincronização forçada (`/api/aging-admin/*`) exige login.

Dá para linkar já filtrado e numa aba: `/aging?aba=pipeline&vertical=P%26P&so_validas=true`.
- Filtros: `criada_de`, `criada_ate`, `vertical`, `tipo_oferta`, `responsavel`, `cliente`, `base_origem`, `busca` e `so_validas`.
- `aba`: `winrate`, `aging`, `pipeline`, `clientes`, `ofertas`, `time` ou `base`.

O dashboard abre no modo escuro, e o botão no topo alterna para o claro.

**Abas:**
- **Visão geral:** cards, Destaques (frases geradas pelos filtros) e evolução anual.
- **Win rate:** por ticket, por vertical, motivos de perda por vertical e por ano, estágio das perdas e sazonalidade.
- **Aging:** chance de ganho de quem ainda está aberta, conversão por safra, distribuição e quando as decisões acontecem.
- **Pipeline e previsão:** previsão de vitórias mês a mês pelo histórico, idade e estágio das abertas.
- **Clientes:** novo × recorrente e concentração do valor ganho.
- **Ofertas:** bolhas de win rate × ciclo, indicadores por tipo e mix por ano.
- **Time:** volume × conversão, mapa de win rate por trimestre e higiene do pipeline.
- **Base de dados:** tabela, exportação CSV e qualidade dos dados.

## Variáveis de ambiente

Usa as mesmas do resto da aplicação, sem nenhuma nova:

| Variável | Uso |
|---|---|
| `ODOO_URL`, `ODOO_DB`, `ODOO_API_KEY` | API JSON-2 do Odoo 19 (`/json/2/...`, header `Authorization: bearer`). O usuário da API key precisa de leitura em `crm.lead`, `crm.tag` e `res.partner`. |
| `POSTGRES_DSN` | Banco da aplicação |

## Instalação (uma vez por ambiente)

```bash
# 1. tabelas e view
docker exec -i sap_import_postgres psql -U sap_import -d sap_import -f - < db/migrations/0003_aging_oportunidades.sql

# 2. base histórica do SharePoint (export CSV ou Excel da lista; a pasta data/ fica fora do git)
docker cp data/historico_sharepoint.csv sap_import_app:/tmp/historico_sharepoint.csv
docker exec sap_import_app python scripts/importar_historico_aging.py /tmp/historico_sharepoint.csv

# 3. primeira sincronização com o Odoo (completa)
docker exec sap_import_app python scripts/sync_aging.py --completa
```

A importação é idempotente: rodar de novo atualiza as mesmas linhas (chave = coluna `ID` da lista), não duplica.

## Sincronização

Agendada no cron do container (`docker/crontab`):

- **Incremental, de hora em hora (minuto 5):** lê do Odoo só o que mudou (`write_date`) desde a última
  sync bem-sucedida, com 10 min de margem, paginado, com upsert.
- **Completa, todo dia às 03:30:** lê tudo e marca como excluída a oportunidade que sumiu do Odoo.

Um advisory lock no Postgres impede duas sincronizações ao mesmo tempo. Se a sync falhar, o
dashboard continua com o último dado gravado. O horário da última sync ok aparece no topo da tela
("Atualizado há X min"). Cada execução fica em `aging_sync_log`, e o log do cron em `logs/cron_aging.log`.

**Forçar agora:** `POST /api/aging-admin/sincronizar` (com login; `?completa=true` para a completa),
ou `docker exec sap_import_app python scripts/sync_aging.py [--completa]`.
Últimas execuções: `GET /api/aging-admin/syncs`.

## Regras (view `aging_base`)

- **Oportunidade que existe no SharePoint vale só a versão do SharePoint.** A cópia dela no Odoo é
  ignorada quando:
  - tem o mesmo código normalizado (`C24.0474` = `C24.474` → `24.474`) **e**
  - foi criada no Odoo até a migração (15/12/2025) **ou** tem o mesmo título.

  O "ou mesmo título" existe porque aditivos novos reaproveitam códigos antigos. Cópias sem código
  casam pelo título.
- **Exceção: estava aberta no SharePoint e seguiu no Odoo.** O SharePoint parou no tempo, então o
  andamento vem da cópia no Odoo:
  - o estágio atual;
  - se ela fechou depois de 15/12/2025: status, data de fechamento e motivo da perda.

  Criação, vertical, cliente, responsável e valor continuam vindo do SharePoint. Fechamentos na data
  da carga de migração (15/12/2025 18:24–18:27) são ignorados. Colunas `andamento_do_odoo` e
  `fechada_no_odoo` na view.
- **Oportunidade que só existe no Odoo** entra pelo Odoo.
- **Status do Odoo** vem de `won_status`:
  - `won` = ganha
  - `lost` = perdida
  - `pending` = aberta, ou **arquivada** se `active = False`, e nesse caso fica fora da análise.
- **Vertical:**
  - SharePoint: coluna `Vertical`.
  - Odoo: nome da equipe de vendas sem "Comercial ".
  - Equipe genérica (Sales): ajuste manual em `aging_ajuste`, senão a vertical predominante do
    responsável no SharePoint.
- **Responsável unificado:** o nome muda entre as bases ("Marcos Liebl" / "Marcos Vinicius Henrique
  Liebl", "Fábio Alves" / "Fábio Alves Ferreira"). Dois nomes são a mesma pessoa quando têm o mesmo
  primeiro nome e todas as palavras do mais curto estão no mais longo, ignorando acento, pontuação e
  "da/de/dos/Jr/Filho" (`aging_palavras_nome`). Vale o nome mais completo.
- **Tipo de oferta:** não existe campo nas bases; é classificado por palavras-chave no título
  (`aging_tipo_oferta`, primeira regra que casar). Títulos sem pista ficam em "Outros". Mudou uma
  regra? Rodar a migração de novo recalcula tudo.
- **Cliente recorrente:** o cliente já tinha uma vitória registrada antes de a oportunidade ser criada.
- **Previsão do pipeline (pelo histórico):** para cada aberta há X dias, olha as fechadas que passaram
  de X dias. A fração ganha é a chance, e quando elas foram ganhas dá o mês provável.
- **Cliente do Odoo** = empresa do contato (`commercial_partner_id`).
- **Aging** = (data de ganho/perda, ou hoje para as abertas) − data de criação, em dias corridos.
  É calculado na consulta, nunca gravado.
- **Faixas:** Negativo, 0-30, 31-60, 61-90, 91-180, 181-365, acima de 365 dias.
- **Fora da análise:** arquivadas, criação em 16/12/2019 (carga inicial do SharePoint) e aging negativo.
- **Exclusões e ajustes manuais:** tabela `aging_ajuste`.
  - `excluir_odoo` (id do `crm.lead`): já tem as 2 oportunidades de teste.
  - `vertical_responsavel` (nome → vertical): já tem João Battisti = ENG.

## Mapeamento do export do SharePoint

| Coluna do export | Campo interno |
|---|---|
| `ID` | `id_origem` (chave da importação) |
| `Código` | `codigo` / `codigo_norm` (`0` = sem código) |
| `Título` | `nome` |
| `Cliente` | `cliente` |
| `Responsavel` | `responsavel` |
| `Vertical` | `vertical` |
| `Estagio` | `status`: Ganha → ganha, Perdida → perdida, Budget/Firme/Negociação → aberta; também vai para `estagio` |
| `Criado` | `data_criacao` (data americana, `12/11/2025 5:32 PM`) |
| `FechamentoReal` (ou `Modificado` quando vazio) | `data_fechamento` + `fonte_data_fechamento` |
| `FechamentoEstimado` | `data_prevista_fechamento` |
| `ValFechado` (ganhas) ou `ValEstimado` | `valor` (`R$ 1,061,284.32`) |
| `Perdidas` | `motivo_perda` |
| `Chance` | `chance` |

## Código

- `src/odoo_json2.py`: cliente JSON-2, somente leitura.
- `src/aging/mapeamento.py`: Odoo e SharePoint → modelo interno.
- `src/aging/sync.py` e `src/aging/historico.py`: sincronização e importação.
- `src/aging/repositorio.py`: Postgres.
- `src/aging/indicadores.py`: filtros → SQL e agregações.
- `app_acompanhamento/api/aging.py`, `templates/aging.html` e `static/js/aging.js`: API e tela.
- `static/vendor/chartjs/`: Chart.js 4.5.1, com a procedência em `VERSION.txt`.
- **Testes:**
  - `tests/test_aging_mapeamento.py` e `tests/test_aging_indicadores.py` rodam sem banco.
  - `tests/test_aging_banco.py` precisa de `AGING_TEST_DSN` apontando para um banco **descartável**.
