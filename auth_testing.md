# Auth Testing Playbook (Emergent Google OAuth — adaptado para Flask/SQLite)

## Diferenças do playbook padrão (React/FastAPI/Mongo)
Este app é Flask + Jinja + SQLite. Adaptações feitas:
- Callback: `/auth/google/landing` (página server-rendered com JS que processa o fragment `#session_id`)
- Troca de sessão: `POST /auth/google/session` (server-side chama `demobackend.emergentagent.com/auth/v1/env/oauth/session-data` com header `X-Session-ID`)
- Sessão persistida em tabela SQLite `user_sessions` (email, matricula, session_token, expires_at ISO timezone-aware)
- Cookie `session_token` httpOnly/secure/samesite=None, validade 7 dias
- `login_required` faz fallback: flask session → cookie session_token → user_sessions
- Vínculo email→matrícula em `google_vinculos`; dono (OWNER_EMAIL) auto-provisionado como supervisor

## Passo 1 — Criar sessão de teste
```bash
sqlite3 /app/backend/producao.db "
INSERT INTO user_sessions (email, matricula, session_token, expires_at)
VALUES ('test.user@example.com', '1024', 'test_session_123',
        datetime('now', '+7 days'));"
```

## Passo 2 — Testar backend
```bash
curl http://localhost:3000/supervisor/fila -H "Cookie: session_token=test_session_123" -o /dev/null -w "%{http_code}\n"
curl http://localhost:3000/supervisor/gerenciar -H "Cookie: session_token=test_session_123" -o /dev/null -w "%{http_code}\n"
```

## Passo 3 — Teste browser
```python
await page.context.add_cookies([{
    "name": "session_token", "value": "test_session_123",
    "domain": "supervisor-dash-3.preview.emergentagent.com",
    "path": "/", "httpOnly": True, "secure": True, "sameSite": "None"
}])
await page.goto("https://supervisor-dash-3.preview.emergentagent.com/operador")
```

## Checklist
- [ ] `/auth/google/session` sem session_id → 400
- [ ] Cookie válido → página protegida abre (200)
- [ ] Cookie expirado → redirect para /login
- [ ] E-mail sem vínculo → 403 com mensagem clara
- [ ] OWNER_EMAIL → auto-vinculado como supervisor

## Limpeza
```bash
sqlite3 /app/backend/producao.db "DELETE FROM user_sessions WHERE email LIKE 'test.user%';"
```
