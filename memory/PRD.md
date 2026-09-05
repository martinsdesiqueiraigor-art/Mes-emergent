# ProFab MES/APS — PRD

## Stack
Flask + SQLite (WAL) + Jinja2 mono-arquivo (`/app/backend/mes_aps.py`, ~2400 linhas).
Servido em `0.0.0.0:3000` via supervisor slot `frontend`. DB em `/app/backend/producao.db`.
Credenciais: `/app/memory/test_credentials.md`.

## Implementado

### Fase 1 (2026-02) ✅
- DEBUG/SECRET_KEY via env, bcrypt com upgrade transparente SHA256, WAL+foreign_keys, tabela `audit_log`, backup no startup.

### Fase 2 — Controle de apontamentos (2026-02) ✅
- Coluna `justificativa_sucata`, `status_aprovacao`, `auto_fechado`, `aprovador`, `data_hora_aprovacao` em `apontamentos`.
- Refugo > saldo residual: bloqueia sem justificativa, marca `pendente` e não desconta saldo até aprovação.
- Fechamento automático de fim de turno (`POST /supervisor/auto-close`).
- Cronômetro visível em cada apontamento ativo (JS setInterval, cor emerald).
- Fila de validação (`/supervisor/fila`) com aprovar/rejeitar por linha; aprovação aplica desconto no saldo.
- Tabela `configuracoes` (fim de turno, min stoppage min, oee target, retenção backup).

### Fase 3 — Relatórios (2026-02) ✅
- `/supervisor/relatorios` com filtros date_from/date_to/machine:
  - **OEE por máquina** (Disponibilidade × Performance × Qualidade)
  - **Pareto de paradas** com % isolado e cumulativo
  - **Aging de OPs** com destaque para overdue (>7 dias)
  - **Produtividade por operador** (peças, refugo, %refugo, peças/h)
- Exportação CSV mantida do legado.

### Fase 4 — Supervisor (parcial) 🚧
- `POST /supervisor/operador/reset/<matricula>` (reset senha)
- `POST /supervisor/operador/excluir/<matricula>` (soft delete)
- `POST /supervisor/op/cancelar/<num_op>` (com justificativa)
- `POST /supervisor/op/reabrir/<num_op>` (com justificativa)
- Navegação nova (Fila, Relat., Saúde) integrada ao BASE_TEMPLATE.

### Painel de Saúde ✅
- `/supervisor/saude`: tamanho DB, apontamentos abertos, pendentes aprovação, último backup, total backups.
- Formulário de configurações (fim de turno, min stoppage, OEE, retenção backup).

## Backlog
### Fase 4 restante (P1)
- Substituir formulários inline do SUPERVISOR_TEMPLATE por modais Shadcn-style
- Busca + paginação nas listas grandes (operadores, apontamentos, histórico)
- Botões edição/reset direto nas listas de mestres
- Backup agendado diário (cron interno / APScheduler)

### Fase 5 — Polimento (P2)
- Toasts JS para pendências novas (poll ou SSE)
- Refinos mobile finais
- Manual curto operador/supervisor
- Exportação Excel/PDF (openpyxl+reportlab já instalados)
