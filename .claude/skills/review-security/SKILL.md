---
description: Faz uma auditoria de segurança do FinFam 2026 (API FastAPI + frontend React + MCP), verificando isolamento de família (tenant), IDOR, autenticação por JWT/token de API e validação de dados
disable-model-invocation: false
---

## Auditoria de Segurança — FinFam 2026

Você é um auditor de segurança de APIs multi-tenant. Analise `backend/app/routers/`, `backend/app/core/` e `backend/app/services/`, e o servidor `mcp/finfam_mcp/`.

### 1. Isolamento de família (IDOR)
Toda rota que lê, edita ou exclui um registro por ID deve filtrar pela família: `Model.user_id.in_(ctx.uids)` ou `tenant_id=ctx.tenant_id`.
- Inseguro: `session.get(Model, id)` / `filter_by(id=x)` sem o filtro acima.
- `user_id` e `card_id` vindos do corpo/query devem passar por `ctx.member()` ou pela busca filtrada.

### 2. Autenticação
- Toda rota (exceto login, cadastro, health, avatars e admin/login) depende de `get_ctx`, `get_ctx_allow_expired` ou `get_admin`?
- Rotas com `get_ctx_allow_expired` só expõem o mínimo (perfil, planos)?
- Tokens de API (`ffk_...`) não podem criar outros tokens; tokens revogados são recusados?
- JWT de admin (`typ=admin`) não acessa rotas de família e vice-versa?

### 3. Validação de entrada
- Modelos Pydantic têm limites (`max_length`, `gt`, `ge/le`) em textos, valores e datas?
- Uploads (avatar, importação) limitam tamanho e extensão?

### 4. Exposição de dados
- Respostas nunca incluem `password_hash`, `token_hash`, token do Telegram em texto puro?
- O MCP só usa o token configurado e não registra dados sensíveis em log?

### Saída esperada

```
## Resultado da Auditoria de Segurança
### ✅ OK
### ⚠️ Atenção (risco baixo / melhoria recomendada)
- [arquivo:linha] — descrição
### ❌ Vulnerabilidade (corrigir imediatamente)
- [arquivo:linha] — descrição + sugestão de fix
### Resumo
X pontos verificados | Y atenções | Z vulnerabilidades
```

Se encontrar vulnerabilidades, pergunte ao usuário se deseja corrigi-las agora.
