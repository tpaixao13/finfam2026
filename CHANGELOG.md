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

- "Usar o ID da conexão": registra no FinFam um banco já conectado pelo painel da Pluggy.
- Meu Pluggy: "Atualizar agora" relê contas e lançamentos (a Pluggy não aceita PATCH nesses itens;
  bancos novos adicionados no Meu Pluggy aparecem na mesma conexão).
- Meu Pluggy compartilha um banco por autorização: o FinFam não pede mais `avoidDuplicates`
  (a Pluggy recusava o 2º banco). Duplicata de verdade (mesmas contas) é recusada pelo FinFam.

- Ajustes com dados reais:
  - saída da conta só casa com despesa que não é de crédito, e compra do cartão só com despesa de crédito;
  - parcela k>1 usa a data em que entrou na fatura (a "data da compra original" do banco não é confiável)
    e casa com a mesma parcela do FinFam em até 70 dias;
  - "TOTAL DA FATURA ANTERIOR"/saldo anterior vai para Ignorados;
  - o que foi classificado sozinho é reclassificado na próxima leitura; o que você mexeu (Lançar,
    Ignorar, Revisar) fica como você deixou;
  - nome do banco por conexão (Meu Pluggy vem como "MeuPluggy"): vai nas despesas lançadas.

- **Lançar automaticamente** (por banco): o que chega vira despesa com a categoria sugerida e aparece em
  "Lançados" para corrigir. Duplicados e ignorados continuam fora. Compra pendente que o banco troca pela
  lançada (id novo) não é lançada duas vezes.
- O webhook dispara a leitura em segundo plano (avisos em rajada viram uma leitura só).
- **Admin › Dados da família** (substitui o "Recomeçar pelo banco", que saiu da tela das pessoas):
  - *Fazer backup*: tudo da família desde o início (despesas, rendas, cartões, investimentos, metas,
    bancos conectados), sem senhas nem chaves, em `.json.gz` guardado em `instance/backups` e baixável;
  - *Zerar despesas*: apaga todas as despesas da família (com parcelas, recorrências e lançamentos
    importados), faz backup antes, e os bancos conectados reimportam 1, 3, 6 ou 12 meses (ou nada);
  - *Restaurar*: troca as despesas atuais pelas do backup, exatamente como estavam (backup antes também);
  - confirmação digitando o nome da família.
- `python -m scripts.restore_expenses BACKUP.db.gz --email ... --desde ... [--aplicar]`: devolve, do backup do
  Telegram, despesas apagadas que não voltaram pelo banco (com cartão, fatura, parcelas e "pago").
- Limite do cartão vem do banco: cartão do FinFam ligado ao do banco recebe o limite a cada atualização
  (na conta de crédito, quando o limite é compartilhado). Mostra também limite disponível e os dias de
  fechamento e vencimento.
- "Criar cartão" a partir do cartão do banco: final, banco, limite, vencimento e melhor dia vêm da Pluggy.
- Fatura certa pelo banco: cada compra do cartão vai para a fatura calculada com os dias de fechamento e
  vencimento do banco (inclusive fecha num mês/vence no outro); o cartão ligado recebe do banco o
  vencimento e o melhor dia (= fechamento). Revisar importados mostra a fatura de cada compra.
- No cartão, o período escolhido conta por fatura: entram as compras das faturas que vencem a partir do
  início (a parcela de setembro que vence em outubro entra em "1 mês").
- Pagamento de fatura saindo da conta é conciliado: o FinFam acha a fatura de mesmo total (mês do
  pagamento, anterior ou seguinte) e a marca como paga; o pagamento não vira despesa.
- Faturas do banco (`GET /bills`, tabela `bank_bills`): a compra vai para a fatura que o banco diz
  (billId); a pendente, para a próxima em aberto. Funciona no Meu Pluggy, que não manda os dias de
  fechamento. O pagamento da conta liga à fatura do banco de mesmo total e vencimento próximo, e a
  conciliação é refeita depois de lançar as compras. Despesa já lançada fora da fatura certa é movida
  (se a descrição não foi editada) e volta a ficar não paga, para a conciliação refazer.
- Compra pendente sem billId vai para a fatura em aberto (não para a última fechada).
- Parcela do meio (k/n) lançada cria também k+1..n nas faturas seguintes (como compra parcelada lançada à
  mão); as parcelas que o banco lançar depois — ou outras do mesmo lote — viram "já no FinFam". Mudar de
  fatura uma parcela de série leva as seguintes junto.
- A fatura que o banco informa manda (align_to_bank): a cada leitura, toda compra do cartão lançada ou
  reconhecida vai para a fatura do seu billId, da mais antiga para a mais nova, e as parcelas seguintes
  da série se ajustam — o banco pode pular um mês (ex.: fatura de junho zerada no Santander). Lançar
  calcula a fatura na hora se as faturas do banco chegaram depois da classificação.
- Boleto que tem o total de uma fatura do banco (vencimento até 10 dias) é o pagamento dela: conciliado,
  não vira despesa (ex.: boleto de R$ 17,25 da fatura só com a anuidade).
- Sugestões: conta → PIX (boleto, débito automático, transferência) e Cartão de Débito só para compra no
  cartão de débito; categoria pela descrição e pela categoria do banco quando a família ainda não tem
  histórico/regras (prestação habitacional → Moradia, posto → Transporte, iFood → Alimentação...).
- Revisar importados: a categoria aceita uma nova (digitada), como no lançamento do site.
- Um cartão do banco com vários cartões (C6: físico, virtual e adicional chegam como um só): cada compra vai
  para o cartão do final que o banco informa (`creditCardMetadata.cardNumber`), entre os cartões de crédito
  da família do mesmo banco; compra no adicional fica no nome de quem tem o cartão.
- Dia de corte descoberto pelas compras com código de fatura (C6/Santander pelo Meu Pluggy não informam):
  vale se explica 90%+ delas; pendente sem código vai para a fatura do corte (o C6 deixou uma fatura
  fechada inteira como "pendente"). O cartão ligado recebe o melhor dia descoberto.
- Pagamento de fatura reconhecido também sem "pagamento/fatura" no texto, quando o valor bate com a fatura
  do banco e a descrição cita o banco do cartão ("C6 BANK", "BANCO SANTANDER") ou sai da conta do mesmo
  banco. Pagamento que já tinha sido lançado como despesa é desfeito (a fatura contava duas vezes).
- A mesma parcela da mesma compra é reconhecida com até R$ 0,10 de diferença (o banco põe o arredondamento
  na 1ª parcela: 166,68 + 166,66...) e fica com o valor do banco. Séries de parcelas duplicadas por isso
  (mesma loja, nº de parcelas e cartão) são juntadas na leitura, ficando as parcelas vindas do banco.
- Estornos e créditos no cartão (valor negativo que não é pagamento da fatura) chegam para revisar e entram
  como despesa negativa, abatendo a fatura; a categoria é a da compra original quando o FinFam a encontra.
- Saída da conta só casa com despesa PIX/débito (dinheiro e VR/VA não saem do banco).

**Etapa 4 — conferir fatura, aviso no Telegram e MCP** ✅
- "Conferir faturas com o banco" (Contas conectadas, por mês): para cada cartão conectado, total do banco
  (a fatura que ele informa ou a soma dos lançamentos que chegaram) × FinFam, e o que está só no banco
  (para revisar) ou só no FinFam (lançado à mão, parcela futura). `GET /pluggy/invoice-check`.
- Aviso no Telegram (bot central) quando uma leitura sozinha (webhook ou cron) traz lançamentos: quantos
  foram lançados automaticamente e quais estão para revisar, com o link. Liga/desliga por pessoa
  (`pluggy_configs.notify`, `PUT /pluggy/notify`). Quem está na página não recebe aviso.
- Bot: `/banco` e perguntas livres ("chegou algo do banco?", "o que falta revisar?", "a fatura do C6 bate
  com o banco?") — sem precisar de IA: bancos, o que falta revisar e banco × FinFam no mês.
- MCP: `importados_do_banco(situacao)` e `conferir_fatura_com_banco(mes, ano)` — leitura com token de API
  (`GET /pluggy/summary` e `/pluggy/invoice-check`); aprovar continua só pelo site.

**Depois da etapa 4**
- Investimentos dos bancos (`bank_investments`, `GET /pluggy/investments`): CDB, fundos etc. que o banco
  informa aparecem em Investimentos › "Nos bancos", com o saldo do banco, taxa e vencimento — só consulta,
  não entram na carteira lançada à mão. Resgatado some na leitura seguinte. Só quem conectou vê.
  O topo mostra o total investido (carteira + cripto + bancos) e o painel "Distribuição" separa por
  classe (Renda Fixa, Ações, Fundos...) ou por instituição, com a % de cada uma (`lib/allocation.ts`).
- C6: "DÉBITO DE CARTÃO" na conta é compra no débito (a Pluggy marca como pagamento de cartão).
- Despesas: atalho para selecionar as vencidas da página e marcar como pagas.
- Bot: `/openfinance` (igual a `/banco`), agora com saldo das contas e investimentos dos bancos; `/carteira`
  soma os investimentos dos bancos ao total.
- MCP: `investimentos()` (carteira + cripto + bancos, % por classe e por instituição) e `saldo_das_contas()`.
- Rendas: "Saldo nas contas" — saldo das contas correntes/poupança conectadas (`GET /pluggy/balances`, só consulta).
- Conferir faturas: "compras batem" quando as compras do banco para a fatura somam igual ao FinFam e só o
  total do banco tem crédito/ajuste (`items_match`).
- Revisão de segurança: webhook recusa segredo vazio, só confia no CF-Connecting-IP vindo da própria
  máquina, limita tentativas erradas por IP e não grava o token no log de acesso; restauração do backup só
  aceita lançamentos de contas da própria família; backup sem código de convite/chat do Telegram e com
  permissão 0600; `POST /rules/learn` só pelo site; MCP trata descrição do banco como dado.
- Revisão Unix das telas: `OpenFinance.tsx` dividido em `pages/open-finance/*`, `ReviewRows.tsx`,
  `BankInvestments.tsx`, `Allocation.tsx`; acessibilidade e celular (360 px sem rolagem lateral).
- Regras de categoria passam a ser preenchidas: em Revisar importados, trocar a categoria sugerida cria
  (ou corrige) a regra da palavra principal (`categorizer.keyword_of`, sem prefixos como DL/IFD/cidade);
  botão "Criar pelo histórico" (`POST /rules/learn`: palavra em 3+ despesas, 80%+ na mesma categoria);
  "Usos" conta cada despesa salva com a categoria da regra. "Aplicar às despesas" (`POST /rules/apply`, com
  prévia): recategoriza as despesas já lançadas — só as em "Outros" ou todas que alguma regra cobre.
- Cartão do banco: dia de fechamento digitado (`bank_accounts.manual_close_day`, `PUT /pluggy/accounts/{id}/close-day`)
  vale enquanto o banco não informa; o do banco sempre manda. `close_source`: bank | manual | inferred.

## 2026.1.0 — versão estável antes do Open Finance

API FastAPI + site React + MCP + bot do Telegram com IA da família, 2FA, investimentos
em outras moedas, backup pelo Telegram.
