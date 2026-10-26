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

**Etapa 2 — conectar bancos e ver contas e cartões** ✅
- Botão "Conectar banco" abre o widget Pluggy Connect (a senha do banco vai direto para a
  Pluggy). `POST /pluggy/connect-token` gera o token de 30 min com o webhook e o id da pessoa.
- `POST /pluggy/items` registra o banco conferindo na conta Pluggy da própria pessoa;
  `GET /pluggy/items` lista bancos com contas (saldo) e cartões (fatura atual e limite).
- Cada cartão do banco é ligado a um cartão do FinFam (sozinho pelos 4 últimos dígitos,
  ou escolhendo na tela): `PUT /pluggy/accounts/{id}`.
- "Atualizar agora", "Reconectar" (senha recusada) e "Desconectar" (apaga na Pluggy e aqui).
- O webhook anota a situação do banco (`item/*`) e a próxima listagem relê o que mudou.
- Remover a chave ou trocar de aplicação Pluggy desconecta os bancos.
- Limite de bancos por pessoa definido pelo admin. Tabelas novas: `bank_connections`, `bank_accounts`.
- Nova variável: `PLUGGY_INCLUDE_SANDBOX` (bancos de teste no Connect; `0` em produção real).

**Próximas etapas**
3. Importação: duplicados, parcelas, categoria e a caixa "Revisar importados".
4. Conferência de fatura, aviso no bot e ferramenta no MCP.

## 2026.1.0 — versão estável antes do Open Finance

API FastAPI + site React + MCP + bot do Telegram com IA da família, 2FA, investimentos
em outras moedas, backup pelo Telegram.
