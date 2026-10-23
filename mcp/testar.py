"""Teste rápido do MCP contra a API rodando (sem Inspector, sem Claude).

Uso (na pasta mcp, com a .venv ativada e a API rodando em outro terminal):
    python testar.py ffk_SEU_TOKEN

Só faz consultas — não lança nem altera nada.
Para testar um lançamento de verdade:  python testar.py ffk_SEU_TOKEN --lancar
"""
import os
import sys

if len(sys.argv) < 2 or not sys.argv[1].startswith('ffk_'):
    print('Informe o token: python testar.py ffk_SEU_TOKEN')
    sys.exit(1)

os.environ['FINFAM_API_TOKEN'] = sys.argv[1]
os.environ.setdefault('FINFAM_API_URL', 'http://127.0.0.1:8000/api/v1')

from finfam_mcp import server  # noqa: E402  (lê o token ao importar)

TESTES = [
    ('listas_de_referencia', {}),
    ('resumo_do_mes', {}),
    ('contas_a_pagar', {'dias': 7, 'max_itens': 5}),
    ('listar_cartoes', {}),
    ('gastos_por_categoria', {}),
    ('alertas', {}),
]
if '--lancar' in sys.argv:
    TESTES.append(('lancar_despesa', {'descricao': 'Teste MCP', 'valor': 1.0, 'forma_pagamento': 'PIX'}))

for nome, args in TESTES:
    print(f'\n===== {nome} {args or ""}')
    print(getattr(server, nome)(**args))
