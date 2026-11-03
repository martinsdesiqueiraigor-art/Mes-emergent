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
Métricas, config editável, alerta vermelho se backup >24h + backup automático via before_request.

### Fase 4 — Modais + Busca + Paginação (2026-02) ✅
`/supervisor/gerenciar` (CRUD+edição modais, busca, reset senha), `/supervisor/historico` (paginado), `/supervisor/auditoria`.

### Google Sign-In — Emergent managed OAuth (2026-09) ✅
Fluxo completo com vínculo email→matrícula, OWNER auto-supervisor, cookie 7d, before_request global.

### Anexos + Badge + Manual (2026-09) ✅
Anexos via Emergent Object Storage (`/supervisor/op/<num_op>/anexo`, stream `/anexo/<num_op>`), card "Desenho da OP" no operador, badge pulsante `/pendencias/count` (poll 30s), manual em `/manual`.

### Miniatura + Som + Gráficos (2026-10) ✅
- **Miniatura**: card do operador mostra `<img>` preview do desenho (troca no dropdown); PDF mostra ícone 📄 + botão abrir
- **Notificação sonora**: WebAudio beep quando pendências AUMENTAM na fila — apenas para sessões com papel supervisor (`session['papel']` setado no form-login e no hook de cookie)
- **Gráficos Chart.js** (CDN) nos relatórios: OEE (barras agrupadas), Pareto (barra+linha % acumulada, eixo y1), Aging (barras horizontais, vermelho=atrasada), Produtividade (peças/h + %refugo) — com tooltip e legenda, mantendo tabelas de dados


## 2026-11-03 — PCP acompanha OP fase a fase
- `/pcp`: coluna "Fases" com etiquetas por etapa (verde concluída, laranja em produção, cinza pendente) + botão "🎯 Fases"
- `/pcp/acompanhar/<num_op>`: progresso total (trabalho de todas as fases), saldo, cada etapa com status, operador/máquina ativos, peças boas/refugo, % da fase, pendências de validação e histórico dos últimos 40 apontamentos

## Backlog
- UI de vínculo Google em modal (rota pronta)
- Notificações SSE em vez de poll
- Login Google com imagem de perfil no cabeçalho
