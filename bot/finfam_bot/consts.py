"""Constantes compartilhadas pelo bot."""

TTL = 30 * 60  # rascunhos e lançamentos guiados expiram em 30 min
CREDIT, DEBIT = 'Cartão de Crédito', 'Cartão de Débito'
METHODS = ['PIX', DEBIT, CREDIT, 'Dinheiro', 'VR', 'VA']
METHOD_LABEL = {'PIX': 'PIX', DEBIT: 'Débito', CREDIT: 'Crédito', 'Dinheiro': 'Dinheiro', 'VR': 'VR', 'VA': 'VA'}
INSTALLMENTS = [1, 2, 3, 4, 5, 6, 10, 12]

BANK_METHODS = {'PIX', DEBIT, CREDIT}  # formas em que o site pede o banco (se não houver cartão)
