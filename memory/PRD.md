# ProFab MES/APS — PRD

## Stack
Flask + SQLite (WAL) + Jinja2 mono-arquivo em `/app/backend/mes_aps.py`. Servido em `0.0.0.0:3000`. DB `/app/backend/producao.db`. Credenciais `/app/memory/test_credentials.md`.

## Implementado

### Fase 1 (2026-02) ✅
DEBUG/SECRET_KEY via env, bcrypt com upgrade transparente SHA256, WAL, `audit_log`, backup no startup.

### Fase 2 — Apontamentos (2026-02) ✅
Justificativa obrigatória para refugo > saldo (vai p/ fila `pendente`), cronômetro visível JS, fechamento automático fim de turno, fila de validação em `/supervisor/fila` (aprovar/rejeitar aplica desconto de saldo).

### Fase 3 — Relatórios (2026-02) ✅
`/supervisor/relatorios` — OEE por máquina, Pareto paradas, Aging OPs, Produtividade por operador, com filtros período+máquina.

### Painel de Saúde (2026-02) ✅
`/supervisor/saude` — tamanho DB, apontamentos abertos, pendentes aprovação, último backup, config editável.

### Fase 4 — Modais + Busca + Paginação (2026-02) ✅
- `/supervisor/gerenciar` — CRUD de operadores/máquinas/desenhos via modais, busca unificada por nome/código/matrícula, reset de senha em um clique com modal pré-preenchido.
- `/supervisor/historico` — Lista paginada de apontamentos (25/pág) com filtros por texto, data, status; navegação Anterior/Próxima preservando querystring.
- Rotas backend: reset senha, soft delete operador, cancelar/reabrir OP com justificativa.

## Backlog
- Modais de EDIÇÃO (hoje há add + reset; falta editar campos gerais dos mestres).
- Backup agendado (APScheduler ou cron).
- Exportação Excel/PDF (libs instaladas).
- Toasts de pendências em tempo real (poll ou SSE).
- Manual curto operador/supervisor.
