# FinFam 2026

Controle financeiro familiar — nova versão do FinFam com **backend e frontend separados** e um **servidor MCP**, para que assistentes (Claude Desktop, um bot do Telegram com IA etc.) consultem e lancem despesas pela mesma API do site.

```
finfam2026/
├── backend/    API REST (FastAPI + SQLAlchemy) — /api/v1, docs em /api/docs
├── frontend/   SPA (React + Vite + TypeScript) — só fala com a API
├── mcp/        Servidor MCP (Python) — ferramentas que chamam a API com um token
└── deploy/     systemd + script de atualização (Raspberry Pi / Linux)
```

```
 Navegador ──► frontend (React) ──┐
                                  ├──► API /api/v1 (FastAPI) ──► SQLite (gastos.db)
 Bot / Claude ──► MCP (finfam_mcp) ┘        ▲
                                            └── lembrete diário do Telegram (a cada minuto)
```

## Rodando no Windows (desenvolvimento)

Pré-requisitos: Python 3.11+ e Node 20+.

**1. API** (terminal 1):
```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
copy ..\.env.example .env      # ajuste se quiser
python run.py                  # http://127.0.0.1:8000/api/docs
```
Na primeira subida é criado o admin (`admin@finfam.app`); se `ADMIN_INITIAL_PASSWORD` estiver vazia, a senha gerada aparece no terminal.

Dados de exemplo (opcional): `python -m scripts.seed_demo` → login `demo@finfam.app` / `demo12345`.

**2. Frontend** (terminal 2):
```powershell
cd frontend
npm install
npm run dev                    # http://localhost:5173 (encaminha /api para a porta 8000)
```

**Testes:** `cd backend; pytest` e `cd mcp; pytest`.

## Usando o banco do FinFam antigo

O schema é o mesmo, então basta copiar o arquivo:

1. Copie `Controledegastos\instance\gastos.db` para `finfam2026\backend\instance\gastos.db`.
2. Em `backend\.env`, use a **mesma `SECRET_KEY`** do `.env` antigo — os tokens do Telegram estão criptografados com ela. Os logins (senhas) funcionam com qualquer chave.
3. Suba a API. As migrações rodam sozinhas e também corrigem um problema do banco antigo: o índice único de e-mail tinha se perdido e o admin era recriado a cada reinício (o seu banco tem 187 cópias de `admin@finfam.app`). Fica só o admin mais antigo, que é o que o login antigo já usava.

> Teste com uma cópia antes de apontar para o banco de produção.

## Produção (um processo só)

`npm run build` gera `frontend/dist`; a API serve esse build automaticamente (rotas que não começam com `/api`). Assim, em produção basta o `uvicorn` (veja `deploy/finfam2026.service` e `deploy/update.sh`). Defina `FINFAM_ENV=production` e uma `SECRET_KEY` forte.

## Autenticação

- **Site:** `POST /api/v1/auth/login` devolve um JWT (`Authorization: Bearer <jwt>`), válido por 7 dias (`ACCESS_TOKEN_MINUTES`).
- **Integrações (MCP/bot):** em *Perfil e acesso → Tokens de API* gere um token `ffk_...`. Ele age em nome do usuário, só na família dele, e pode ser revogado. Só o hash fica no banco.
- **Admin:** `/admin` no site, `POST /api/v1/admin/login` na API (token separado do de usuário).

## MCP (bot do Telegram / Claude Desktop)

Veja [`mcp/README.md`](mcp/README.md). Resumo para o Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "finfam": {
      "command": "C:\\caminho\\finfam2026\\mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "finfam_mcp"],
      "env": {
        "FINFAM_API_URL": "http://127.0.0.1:8000/api/v1",
        "FINFAM_API_TOKEN": "ffk_..."
      }
    }
  }
}
```

## O que mudou em relação ao FinFam original

| | Original | 2026 |
|---|---|---|
| Arquitetura | Flask monolito, páginas Jinja | API FastAPI + SPA React + MCP |
| Login | Sessão/cookie + CSRF | JWT (site) e tokens de API (integrações) |
| Regras de negócio | Parte nas rotas, presa ao formulário | Em `services/`, reutilizadas pelo site e pelo MCP |
| Contas a pagar | Só no lembrete do Telegram | Endpoint `/expenses/bills` + tela própria |
| Alertas de limite | Limite do cartão isolado | Limite compartilhado da conta (igual à tela de cartões) |
| SSL externo (BCB/CoinGecko) | Verificação desligada | Ligada; `INSECURE_EXTERNAL_SSL=1` só se precisar |
| Agendador | APScheduler | Tarefa assíncrona da própria API (sem dependência) |
| Admin duplicado | Recriado a cada reinício | Corrigido + índice único de e-mail |
| Gráficos | Chart.js | SVG próprio (sem dependência) |

Tudo o que existia continua: despesas (à vista, parceladas, recorrentes), rendas fixas/variáveis com dia útil, cartões crédito/débito/VR/VA com limite compartilhado e fatura consolidada, metas, regras de categoria, importação (C6 PDF, OFX, Nubank, CSV, planilha FinFam), exportação xlsx/pdf, investimentos com IR/IOF e Selic, alertas, insights, Telegram, planos/trial e painel admin.
