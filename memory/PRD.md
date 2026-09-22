# ProFab MES/APS — PRD

## Stack
Flask + SQLite (WAL) + Jinja2 mono-arquivo em `/app/backend/mes_aps.py`. Servido em `0.0.0.0:3000`. DB `/app/backend/producao.db`. Credenciais `/app/memory/test_credentials.md`.

## Implementado

### Fase 1 (2026-02) ✅
DEBUG/SECRET_KEY via env, bcrypt com upgrade transparente SHA256, WAL, `audit_log`, backup no startup.

### Fase 2 — Apontamentos (2026-02) ✅
Justificativa obrigatória para refugo > saldo (fila `pendente`), cronômetro visível JS, fechamento automático fim de turno, fila de validação em `/supervisor/fila`.

### Fase 3 — Relatórios (2026-02) ✅
`/supervisor/relatorios` — OEE por máquina, Pareto paradas, Aging OPs, Produtividade por operador, com filtros período+máquina.

### Painel de Saúde (2026-02) ✅
`/supervisor/saude` — tamanho DB, apontamentos abertos, pendentes aprovação, último backup, config editável.

### Fase 4 — Modais + Busca + Paginação (2026-02) ✅
`/supervisor/gerenciar` (CRUD operadores/máquinas/desenhos, busca, reset senha), `/supervisor/historico` (paginado 25/pág, filtros).

### Fase 4.5 — Edição + Backup + Export (2026-09) ✅
- **Edição em modais**: `POST /supervisor/operador/editar/<matricula>`, `/supervisor/maquina/editar/<codigo>`, `/supervisor/desenho/editar/<numero>` — cada mudança registrada em `audit_log` com diff (antes→depois). Modais pre-preenchidos via `fillEdit()` JS.
- **Auditoria**: `/supervisor/auditoria` — log paginado (50/pág) de todas as ações.
- **Backup agendado**: `@app.before_request check_scheduled_backup()` — se último backup tiver >24h, roda `criar_backup()` automaticamente (check a cada 5 min, não a cada request). Aviso vermelho `alert-backup` no painel de saúde quando atrasado.
- **Export XLSX/PDF**: `/supervisor/export/<kind>/<fmt>` (kind: oee, pareto, aging, produtividade; fmt: xlsx, pdf). openpyxl para Excel, reportlab (SimpleDocTemplate+Table) para PDF com header estilizado e zebra rows. 8 botões de download na página de relatórios.

## Backlog
- Toasts de pendências em tempo real (poll ou SSE)
- Manual curto operador/supervisor
- Integração file storage para desenhos técnicos (PDF/imagem anexada ao desenho)
