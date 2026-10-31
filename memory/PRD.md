# ProFab MES/APS — PRD

## Stack
Flask + SQLite (WAL) + Jinja2 mono-arquivo em `/app/backend/mes_aps.py`. Servido em `0.0.0.0:3000`. DB `/app/backend/producao.db`. Credenciais: `/app/memory/test_credentials.md`. Playbook de teste de auth: `/app/auth_testing.md`.

## Implementado

### Fase 1 (2026-02) ✅
DEBUG/SECRET_KEY via env, bcrypt com upgrade transparente SHA256, WAL, `audit_log`, backup no startup.

### Fase 2 — Apontamentos (2026-02) ✅
Justificativa obrigatória para refugo > saldo (fila `pendente`), cronômetro visível JS, fechamento automático fim de turno, fila de validação em `/supervisor/fila`.

### Fase 3 — Relatórios (2026-02) ✅
`/supervisor/relatorios` — OEE por máquina, Pareto paradas, Aging OPs, Produtividade por operador, filtros período+máquina.

### Painel de Saúde (2026-02) ✅
`/supervisor/saude` — tamanho DB, apontamentos abertos, pendentes aprovação, último backup, config editável, alerta vermelho se backup >24h.

### Fase 4 — Modais + Busca + Paginação (2026-02) ✅
`/supervisor/gerenciar` (CRUD+edição em modais, busca, reset senha, vínculo Google), `/supervisor/historico` (paginado, filtros), `/supervisor/auditoria` (log de mudanças com diff).

### Exportação + Backup agendado (2026-09) ✅
`/supervisor/export/<kind>/{xlsx,pdf}` (8 endpoints), botões nos relatórios, `before_request` de backup automático >24h.

### Google Sign-In — Emergent managed OAuth (2026-09) ✅
- Botão "Entrar com Google" no login → `auth.emergentagent.com` com redirect dinâmico `window.location.origin + '/auth/google/landing'` (sem hardcode, com comentário REMINDER no código)
- `/auth/google/landing`: página JS processa fragment `#session_id` e chama `POST /auth/google/session`
- `/auth/google/session`: troca server-side via `demobackend.emergentagent.com/auth/v1/env/oauth/session-data` (header `X-Session-ID`), resolve matrícula via `google_vinculos` (email→matrícula), OWNER_EMAIL (martinsdesiqueiraigor@gmail.com) auto-provisionado como supervisor
- Sessão: `user_sessions` (token, expiry 7d timezone-aware) + cookie `session_token` httpOnly/secure/samesite=None
- `@app.before_request load_session_from_cookie()`: restaura sessão Flask do cookie em TODAS as rotas
- Logout: limpa sessão Flask + cookie + registro em `user_sessions`
- E-mails sem vínculo: 403 com instrução para o supervisor vincular em Gerenciar
- Validado: cookie de teste abre todas as páginas protegidas (operador, pcp, supervisor, fila, gerenciar, saúde), logout remove sessão do DB, sem cookie → redirect /login

## Backlog
- Tela/UX para o supervisor vincular e-mails Google (rota pronta, falta UI no gerenciar)
- Toasts de pendências em tempo real (poll ou SSE)
- Manual curto operador/supervisor
- Anexo de arquivo no desenho (object storage)
