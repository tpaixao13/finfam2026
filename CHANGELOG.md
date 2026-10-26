# Changelog

## 2026.2.0 — Open Finance via Pluggy (em construção, ramo `pluggy`)

A partir desta versão o FinFam pode receber lançamentos e faturas direto dos bancos,
via Open Finance (Pluggy). É opcional: quem não usar continua lançando à mão,
pelo bot ou importando extrato, como antes.

**Etapa 1 — liberação e chave por pessoa** ✅
- Admin: coluna "Open Finance" por família (liberar/desligar + limite de bancos por pessoa).
- "Contas conectadas" (só aparece para famílias liberadas): cada PESSOA cadastra o
  Client ID / Client Secret da própria conta Pluggy, com consentimento (LGPD).
  O FinFam testa a chave e cria um webhook exclusivo na conta Pluggy da pessoa.
- `POST /api/v1/pluggy/webhook/<token>`: confere token, cabeçalho secreto e IP da Pluggy.
- Novas variáveis: `PUBLIC_URL`, `PLUGGY_WEBHOOK_IPS`.

**Próximas etapas**
2. Conectar banco (Pluggy Connect) e listar contas/cartões.
3. Importação: duplicados, parcelas, categoria e a caixa "Revisar importados".
4. Conferência de fatura, aviso no bot e ferramenta no MCP.

## 2026.1.0 — versão estável antes do Open Finance

API FastAPI + site React + MCP + bot do Telegram com IA da família, 2FA, investimentos
em outras moedas, backup pelo Telegram.
