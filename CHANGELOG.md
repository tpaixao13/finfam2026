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

**Etapa 3 — lançamentos do banco e "Revisar importados"** ✅
- Ao conectar, a pessoa escolhe quanto do passado trazer: 1 (padrão), 3, 6 ou 12 meses.
- Cada saída de dinheiro vira um item em `bank_transactions`, nunca uma despesa sozinha:
  - **Para revisar**: com categoria sugerida (regras e histórico) e forma de pagamento (PIX/débito/crédito);
  - **Já no FinFam**: mesmo valor e data perto (e mesma parcela) de algo lançado à mão, pelo bot ou importado;
  - **Ignorados**: pagamento de fatura, transferência entre contas suas, aplicações;
  - entradas (salário, estornos, pagamento recebido no cartão) não entram.
- "Lançar" usa a mesma regra do site: compra parcelada lança as N parcelas no cartão ligado
  (na fatura certa); conta corrente entra como paga. Dá para editar descrição e categoria antes.
- A parcela seguinte que chega depois é reconhecida como "Já no FinFam".
- O webhook `transactions/*` avisa; a lista relê o banco ao abrir. Compra pendente que o banco troca some sozinha.
- `python -m scripts.pluggy_sync` (cron de hora em hora) relê quem ficou sem novidade há 24 h.
- Desconectar apaga os lançamentos vindos do banco; as despesas aprovadas continuam.
- Tabela nova: `bank_transactions`; colunas `bank_connections.import_from` e `tx_synced_at`.
- Lançamentos lidos pelo `GET /v2/transactions` (cursor): a Pluggy desligou o `/transactions` antigo
  (410) nas aplicações de produção. Se só os lançamentos falharem, o banco fica salvo e a leitura tenta de novo.

**Próximas etapas**
4. Conferência de fatura, aviso no bot e ferramenta no MCP.

## 2026.1.0 — versão estável antes do Open Finance

API FastAPI + site React + MCP + bot do Telegram com IA da família, 2FA, investimentos
em outras moedas, backup pelo Telegram.
