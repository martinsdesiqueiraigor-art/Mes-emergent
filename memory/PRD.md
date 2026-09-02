# ProFab MES/APS — PRD

## Problem statement (verbatim)
Melhorias no MES/APS (Flask existente) já em produção. Foco: adicionar relatórios, melhorar controle de apontamentos e refinar a área do supervisor, mantendo a stack atual (Flask + SQLite + Jinja).

## Arquitetura atual
- **Stack:** Flask + SQLite + Jinja2 (mono-arquivo `mes_aps.py`, ~2100 linhas)
- **Runtime:** Flask servido em `0.0.0.0:3000` via supervisor (slot `frontend`) — porta 8001 (backend FastAPI) não é usada.
- **DB:** `/app/backend/producao.db` (SQLite com WAL). Backups em `/app/backend/backups/`.
- **Credenciais demo:** ver `/app/memory/test_credentials.md`.

## Personas
- Operador de chão de fábrica (tablet/celular)
- Supervisor / PCP (desktop e mobile)

## Requisitos core (estáticos)
- Login operador/supervisor, apontamentos com paradas, cadastro de OPs, roteiros, desenhos, máquinas, backup, relatórios CSV.

## Implementado (2026-02)
### Fase 1 — Base técnica ✅
- `DEBUG` agora vem de env (`FLASK_DEBUG=false` por default)
- `SECRET_KEY` via env (`SECRET_KEY`); fallback random
- Senhas migradas para **bcrypt** com **upgrade transparente** de hashes SHA256 legado no primeiro login
- `PRAGMA journal_mode=WAL` + `foreign_keys=ON` em toda conexão
- Tabela `audit_log` criada + helper `log_audit()` (login registrado)
- Backup automático no startup + retenção configurável (`MAX_BACKUPS=30`)
- Paths absolutos para DB e diretório de backup

## Backlog priorizado
### Fase 2 — Controle de apontamentos (P0)
- Fechamento automático fim de turno (job + horário configurável)
- Validação de sucata > saldo com fluxo de aprovação
- Consolidação de 2+ operadores na mesma operação (soma produção)
- Fila de pendências de validação (novo endpoint + tela supervisor)
- Cronômetro visível durante apontamento (JS no template do operador)
- Comentário obrigatório em paradas ≥15min (configurável)
- Log de auditoria em todas as ações (não só login)

### Fase 3 — Relatórios (P0)
- Dashboard supervisor em tempo real (Chart.js)
- OEE por máquina/turno
- Produtividade por operador (peças/h, %sucata, %parada)
- Aging de OPs (dias em aberto vs prazo)
- Pareto de motivos de parada
- Filtros: período, máquina, operador, OP, cliente
- Exportação CSV (já parcial), Excel (openpyxl) e PDF (reportlab)

### Fase 4 — Melhorias supervisor (P1)
- Modais para CRUD (substituir formulários inline)
- Edição/exclusão soft de operador/máquina/desenho
- Reset de senha pelo supervisor
- Busca/filtros/paginação nas listas
- Backup agendado diário (cron interno) + tela de saúde do sistema
- Reabrir OP finalizada / Cancelar OP com justificativa

### Fase 5 — Polimento (P2)
- Toasts para pendências
- Refinos mobile
- Manual curto operador/supervisor
