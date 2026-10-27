# CLAUDE.md

Guia para o Claude Code neste repositório (FinFam 2026).

## Estrutura

- `backend/` — FastAPI. Entrada `app/main.py` (`create_app`). Toda a API sob `/api/v1`; OpenAPI em `/api/docs`.
- `frontend/` — React 19 + Vite + TypeScript, sem roteador/gráficos externos (`src/lib/router.tsx`, `src/components/charts.tsx`).
- `mcp/` — servidor MCP (`finfam_mcp/server.py`) que só chama a API via HTTP com token `ffk_...`.
- `bot/` — bot central do Telegram (`finfam_bot/bot.py`), só biblioteca padrão, chama a API com `Bearer ffbot_<BOT_API_KEY>` + `X-Telegram-Chat`.

## Comandos

```bash
# API
cd backend && python -m venv .venv && .venv/Scripts/activate   # Windows
pip install -r requirements-dev.txt
python run.py                      # porta 8000, reload
pytest                             # testes da API
python -m scripts.seed_demo        # família demo: demo@finfam.app / demo12345

# Frontend
cd frontend && npm install && npm run dev     # porta 5173, proxy /api → 8000
npm run build && npm run typecheck

# Bot do Telegram (usa o backend/.env; testes sobem a API num banco temporário)
cd bot && python -m pytest
python -m finfam_bot               # precisa de TELEGRAM_BOT_TOKEN e BOT_API_KEY

# MCP
cd mcp && pip install -e . && pytest
FINFAM_API_TOKEN=ffk_... python -m finfam_mcp            # stdio
python -m finfam_mcp --http --port 8765                   # Streamable HTTP
```

## Backend — convenções

- **Sessão de banco:** `app/core/db.py` — `scoped_session` por request (ContextVar + `DBSessionMiddleware`). Services usam `db.session` e `Model.query` como no Flask original.
- **Contexto do usuário:** `Depends(get_ctx)` → `Ctx(user, tenant, members)`. Use `ctx.uids` para filtrar QUALQUER consulta (isolamento por família) e `ctx.member(user_id)` para validar ids de pessoas vindos do cliente.
  - `get_ctx_allow_expired` só para `/auth/me` e `/plans` (plano expirado → 402 nas demais).
  - `get_admin` para `/admin/*` (JWT com `typ=admin`).
  - `get_ctx_session` para conta/credenciais/tokens/chave de IA: recusa token de API e o bot.
  - Bot: `ffbot_<BOT_API_KEY>` + header `X-Telegram-Chat` → usuário do `TelegramLink`; `get_bot` nas rotas `/telegram/bot/*`.
- **Open Finance (Pluggy, ramo `pluggy` / versão 2026.2.0):** `services/pluggy.py` (cliente, API Key por Client ID em memória), `routers/pluggy.py` (chave POR PESSOA em `pluggy_configs`, só com login do site; webhook `/pluggy/webhook/<hook_token>` com cabeçalho `X-FinFam-Hook` e IP). O admin libera por família (`tenants.open_finance_enabled`). Bancos conectados em `bank_connections`/`bank_accounts` (só o dono vê; `card_id` liga ao cartão). Lançamentos do banco em `bank_transactions` (pending/duplicate/ignored/imported); regras em `services/open_finance.py` — só `approve` cria despesa, via `create_expense`. Backup/zerar/restaurar a família (admin): `services/family_backup.py` (backups em `instance/backups`). Ver `CHANGELOG.md`.
- **IA:** `ai_service` (um método por provedor, só urllib). A resposta da IA é sempre conferida em `to_draft` (cartão/pessoa da família, categoria válida) e nunca grava: o bot só lança depois do ✅.
- **Regras de negócio** ficam em `app/services/` sem depender de HTTP: `expense_service` (lançar/editar/parcelar/recorrência/contas a pagar), `card_service` (fatura, limite compartilhado por `CreditAccount`, VR/VA com saldo acumulado), `goal_service`, `insight_service`, `alert_service`, `categorizer`, `import_service`, `export_service`, `telegram`, `market`.
- **Serialização:** `app/services/serializers.py` — o formato JSON de cada entidade. O frontend espelha em `frontend/src/lib/types.ts`.
- **Schema:** idêntico ao FinFam original (para reaproveitar `gastos.db`) + tabelas `api_tokens`, `telegram_links`, `telegram_link_codes`, `ai_configs`. Mudanças de schema: adicionar o `ALTER TABLE` em `app/core/migrations.py` (idempotente) além do model.
- **Datas:** `Expense.year/month` = competência (mês da fatura no crédito); `purchase_month/year` = data real da compra. Vencimento real: `serializers.expense_due_date`.
- Datas UTC "naive" (`app.core.types.utcnow`), como no banco antigo.
- Erros de validação saem como `{"detail": "Campo: mensagem", "errors": [...]}`.

## Frontend — convenções

- `src/lib/api.ts` (fetch + token no localStorage), `src/lib/session.tsx` (usuário, toasts, modal de despesa global), `src/lib/hooks.ts` (`useApi`, `usePeriod` com `?month=&year=` na URL).
- Ações que alteram dados: `useAction()` mostra o toast com a `message` da API; depois chame `reload()`/`bumpData()`.
- Estilos em `src/styles.css` (tokens em `:root`). Ícones via `components/Icon.tsx` (nomes `bi-*` vindos do backend).

## GitHub

O hook em `.claude/settings.json` faz commit + push automático (para `origin main`) após cada edição feita pelo Claude, usando `$CLAUDE_PROJECT_DIR`. Se o diretório não for um repositório git, o hook não faz nada.
