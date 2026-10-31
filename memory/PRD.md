# ProFab MES/APS — PRD

## Stack
Flask + SQLite (WAL) + Jinja2 mono-arquivo em `/app/backend/mes_aps.py`. Servido em `0.0.0.0:3000`. DB `/app/backend/producao.db`. Credenciais: `/app/memory/test_credentials.md`. Playbook de teste de auth: `/app/auth_testing.md`.

## Implementado

### Fase 1 (2026-02) ✅
DEBUG/SECRET_KEY via env, bcrypt com upgrade transparente SHA256, WAL, `audit_log`, backup no startup.

### Fase 2 — Apontamentos (2026-02) ✅
Justificativa obrigatória para refugo > saldo (fila `pendente`), cronômetro visível JS, fechamento automático fim de turno, fila de validação em `/supervisor/fila`.

### Fase 3 — Relatórios (2026-02) ✅
`/supervisor/relatorios` — OEE, Pareto, Aging, Produtividade, filtros período+máquina, export CSV/XLSX/PDF.

### Painel de Saúde (2026-02) ✅
Métricas do sistema, config editável, alerta vermelho se backup >24h + backup automático via before_request.

### Fase 4 — Modais + Busca + Paginação (2026-02) ✅
`/supervisor/gerenciar` (CRUD+edição modais, busca, reset senha), `/supervisor/historico` (paginado), `/supervisor/auditoria`.

### Google Sign-In — Emergent managed OAuth (2026-09) ✅
Fluxo completo com vínculo email→matrícula, OWNER auto-supervisor, cookie 7d, before_request global.

### Anexos de Desenho + Badge + Manual (2026-09) ✅
- **Anexos**: tabela `op_anexos`; upload em `/supervisor/op/<num_op>/anexo` (PDF/PNG/JPG/WEBP/GIF, máx 20MB) via **Emergent Object Storage** (`put_object`/`get_object` com re-init em 403/404); stream em `/anexo/<num_op>` (inline). Supervisor anexa em ⚙️ Gerenciar → 📎 Anexos; operador visualiza no card "📎 Desenho da OP" com dropdown de OPs.
- **Badge tempo real**: `/pendencias/count` (JSON) + JS poll 30s no BASE_TEMPLATE → badge vermelho pulsante no link "Fila" com contagem de pendentes.
- **Manual**: `/manual` (login_required) — passo a passo de acesso (Google/matrícula), rotina do operador, rotina do supervisor/PCP, dicas. Link "📖 Manual" no menu.

## Backlog
- UX de vínculo Google em modal (rota pronta: /supervisor/vincular-google)
- Toasts de pendências em tempo real (SSE)
- Miniatura do desenho na tela do operador (hoje abre em nova aba)
