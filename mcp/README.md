# FinFam MCP

Servidor [MCP](https://modelcontextprotocol.io) que expõe o FinFam como ferramentas para um assistente de IA. Ele não acessa o banco: tudo passa pela API (`FINFAM_API_URL`) com um token de API (`FINFAM_API_TOKEN`), então valem as mesmas regras e o mesmo isolamento por família do site.

## Instalação

```powershell
cd mcp
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

Gere o token no site: **Perfil e acesso → Tokens de API → Gerar token** (copie na hora; ele não é mostrado de novo).

## Rodando

- **stdio** (Claude Desktop, ou um bot que inicia o MCP como subprocesso):
  `FINFAM_API_TOKEN=ffk_... python -m finfam_mcp`
- **HTTP** (Streamable HTTP, para um bot em outro processo/máquina):
  `python -m finfam_mcp --http --host 127.0.0.1 --port 8765` → endpoint `http://127.0.0.1:8765/mcp`

> Em HTTP, o servidor usa o token do ambiente para todas as chamadas. Não exponha a porta na internet sem um proxy com autenticação.

## Ferramentas

| Ferramenta | O que faz |
|---|---|
| `resumo_do_mes` | Renda, gastos, sobra, cartão, por pessoa e pontos de atenção |
| `contas_a_pagar` | Em atraso, vencem hoje e próximos N dias (com IDs) |
| `lancar_despesa` | Lança compra à vista, parcelada ou recorrente; sugere categoria; aceita cartão por nome/dígitos |
| `marcar_como_pago` | Marca IDs como pagos (ou pendentes) |
| `pagar_fatura` | Quita a fatura de crédito do mês |
| `buscar_despesas` | Busca por texto, mês, categoria, pessoa, situação |
| `editar_despesa` / `excluir_despesa` | Ajusta ou remove um lançamento |
| `listar_cartoes` / `fatura_do_cartao` | Limite, fatura, melhor dia de compra, saldo VR/VA |
| `gastos_por_categoria` | Total por categoria no mês |
| `rendas_do_mes` / `registrar_renda` | Rendas recebidas e a receber |
| `alertas` / `metas` | Alertas ativos e progresso das metas |
| `listas_de_referencia` | Pessoas, categorias, formas de pagamento e cartões |

Exemplos do que um bot consegue fazer com isso:
- "quais contas vencem essa semana?" → `contas_a_pagar(dias=7)`
- "gastei 89,90 no ifood no nubank" → `lancar_despesa("iFood", 89.9, cartao="nubank")` (categoria sugerida pelas regras)
- "paguei a luz" → `contas_a_pagar` para achar o ID → `marcar_como_pago([id])`

## Próximo passo: o bot do Telegram

O bot precisa de um modelo de IA que decida qual ferramenta chamar. Dois caminhos:

1. **Bot + Claude API com MCP connector**: o bot (ex.: `python-telegram-bot`) envia a mensagem do usuário para a API da Anthropic informando o servidor MCP em HTTP (precisa de URL pública com HTTPS). O Claude chama as ferramentas e devolve a resposta, que o bot repassa.
2. **Bot com cliente MCP local**: o bot inicia `python -m finfam_mcp` (stdio), lista as ferramentas e as repassa como *tools* para o modelo; executa as chamadas que o modelo pedir e devolve o resultado.

Para mais de uma família no mesmo bot, guarde um token de API por chat do Telegram e inicie o MCP com o token daquele chat.
