"""Categoria que o banco (Pluggy) dá a cada lançamento → categoria do FinFam.

A Pluggy classifica em inglês e em detalhe ("Groceries", "Eating out", "Taxi and ride-hailing").
Aqui: traduz para português e encaixa na categoria da família que mais se aproxima. Se a família
não tem nenhuma parecida, usa o nome em português (vira categoria nova, como a Pluggy separa).
"""
import unicodedata

# inglês (Pluggy) → (português, categoria do FinFam mais próxima ou None = fica com o nome em português)
PLUGGY: dict[str, tuple[str, str | None]] = {
    # alimentação
    'groceries': ('Supermercado', 'Alimentação'),
    'eating out': ('Restaurantes', 'Alimentação'),
    'food delivery': ('Delivery', 'Alimentação'),
    'food and drinks': ('Comida e bebida', 'Alimentação'),
    # transporte
    'transportation': ('Transporte', 'Transporte'),
    'taxi and ride-hailing': ('Táxi e apps', 'Transporte'),
    'public transportation': ('Transporte público', 'Transporte'),
    'car rental': ('Aluguel de carro', 'Transporte'),
    'bicycle': ('Bicicleta', 'Transporte'),
    'automotive': ('Automóvel', 'Transporte'),
    'gas stations': ('Posto de combustível', 'Transporte'),
    'parking': ('Estacionamento', 'Transporte'),
    'tolls and in vehicle payment': ('Pedágio', 'Transporte'),
    'vehicle ownership taxes and fees': ('IPVA e taxas do veículo', 'Transporte'),
    'vehicle maintenance': ('Manutenção do veículo', 'Transporte'),
    'traffic tickets': ('Multas de trânsito', 'Transporte'),
    # moradia
    'housing': ('Moradia', 'Moradia'),
    'rent': ('Aluguel', 'Moradia'),
    'utilities': ('Contas da casa', 'Moradia'),
    'water': ('Água', 'Moradia'),
    'electricity': ('Luz', 'Moradia'),
    'gas': ('Gás', 'Moradia'),
    'urban land and building tax': ('IPTU', 'Moradia'),
    'real estate financing': ('Financiamento imobiliário', 'Moradia'),
    'houseware': ('Casa e decoração', 'Moradia'),
    # saúde e beleza
    'healthcare': ('Saúde', 'Saúde'),
    'dentist': ('Dentista', 'Saúde'),
    'pharmacy': ('Farmácia', 'Saúde'),
    'optometry': ('Ótica', 'Saúde'),
    'hospital clinics and labs': ('Hospital, clínicas e exames', 'Saúde'),
    'health insurance': ('Plano de saúde', 'Saúde'),
    'wellness and fitness': ('Bem-estar', 'Saúde'),
    'gyms and fitness centers': ('Academia', 'Saúde'),
    'sports practice': ('Esportes', 'Lazer'),
    'wellness': ('Bem-estar', 'Beleza'),
    'personal care': ('Cuidados pessoais', 'Beleza'),
    # telefone e internet
    'telecommunications': ('Telefone e internet', 'Telefone'),
    'mobile': ('Celular', 'Telefone'),
    'internet': ('Internet', 'Internet'),
    'tv': ('TV por assinatura', 'Internet'),
    # educação
    'education': ('Educação', 'Educação'),
    'online courses': ('Cursos online', 'Educação'),
    'university': ('Faculdade', 'Educação'),
    'school': ('Escola', 'Educação'),
    'kindergarten': ('Creche', 'Educação'),
    'student loan': ('Financiamento estudantil', 'Educação'),
    'bookstore': ('Livraria', 'Educação'),
    # lazer
    'leisure': ('Lazer', 'Lazer'),
    'tickets': ('Ingressos', 'Lazer'),
    'stadiums and arenas': ('Estádios e arenas', 'Lazer'),
    'landmarks and museums': ('Passeios e museus', 'Lazer'),
    'cinema, theater and concerts': ('Cinema, teatro e shows', 'Lazer'),
    'gaming': ('Jogos', 'Lazer'),
    'video streaming': ('Streaming de vídeo', 'Lazer'),
    'music streaming': ('Streaming de música', 'Lazer'),
    'travel': ('Viagem', 'Lazer'),
    'airport and airlines': ('Passagens aéreas', 'Lazer'),
    'accommodation': ('Hospedagem', 'Lazer'),
    'mileage programs': ('Programas de milhas', 'Lazer'),
    'bus tickets': ('Passagens de ônibus', 'Transporte'),
    'lottery': ('Loteria', 'Lazer'),
    'gambling': ('Apostas', 'Lazer'),
    'online bet': ('Apostas online', 'Lazer'),
    # compras e serviços (sem categoria parecida no FinFam padrão: ficam em português)
    'shopping': ('Compras', None),
    'online shopping': ('Compras online', 'Compras'),
    'electronics': ('Eletrônicos', 'Compras'),
    'clothing': ('Vestuário', None),
    'kids and toys': ('Infantil e brinquedos', 'Compras'),
    'sports goods': ('Artigos esportivos', 'Compras'),
    'office supplies': ('Papelaria', 'Compras'),
    'pet supplies and vet': ('Pet', None),
    'services': ('Serviços', None),
    'digital services': ('Serviços digitais', 'Serviços'),
    'insurance': ('Seguros', None),
    'life insurance': ('Seguro de vida', 'Seguros'),
    'home insurance': ('Seguro residencial', 'Seguros'),
    'vehicle insurance': ('Seguro do veículo', 'Transporte'),
    'donations': ('Doações', None),
    'taxes': ('Impostos', None),
    'income taxes': ('Imposto de renda', 'Impostos'),
    'taxes on investments': ('Impostos sobre investimentos', 'Impostos'),
    'tax on financial operations': ('IOF', 'Impostos'),
    'bank fees': ('Tarifas bancárias', None),
    'account fees': ('Tarifa da conta', 'Tarifas bancárias'),
    'wire transfer fees and atm fees': ('Tarifas de transferência e saque', 'Tarifas bancárias'),
    'credit card fees': ('Tarifas do cartão', 'Tarifas bancárias'),
    'late payment and overdraft costs': ('Juros e multa por atraso', 'Tarifas bancárias'),
    'interests charged': ('Juros', 'Tarifas bancárias'),
    'loans': ('Empréstimos', None),
    'loans and financing': ('Empréstimos e financiamentos', 'Empréstimos'),
    'financing': ('Financiamentos', 'Empréstimos'),
    'vehicle financing': ('Financiamento do veículo', 'Transporte'),
    'legal obligations': ('Obrigações legais', None),
}

# Pluggy → só "Outros" (não diz nada útil sobre a compra)
_GENERIC = {'other', 'others', 'transfers', 'transfer - pix', 'transfer - ted', 'transfer - doc', 'transfer - bank slip',
            'transfer - cash', 'transfer - check', 'transfer - internal', 'third party transfer', 'cash'}

# FinFam → nomes parecidos que a família pode ter criado (encaixa nelas antes de criar outra)
_ALIASES = {
    'Compras': ('Compras', 'Shopping'),
    'Vestuário': ('Vestuário', 'Roupas', 'Vestuario'),
    'Serviços': ('Serviços', 'Servicos', 'Assinaturas'),
    'Pet': ('Pet', 'Pets', 'Animais'),
    'Seguros': ('Seguros', 'Seguro'),
    'Impostos': ('Impostos', 'Taxas e impostos'),
    'Tarifas bancárias': ('Tarifas bancárias', 'Tarifas', 'Taxas bancárias', 'Banco'),
    'Empréstimos': ('Empréstimos', 'Emprestimos', 'Financiamentos', 'Dívidas'),
    'Doações': ('Doações', 'Doacoes', 'Igreja', 'Dízimo'),
}


def _key(text: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKD', text or '') if not unicodedata.combining(c)).strip().lower()


def translate(pluggy_category: str | None) -> str | None:
    """Nome em português da categoria da Pluggy (para mostrar na revisão)."""
    hit = PLUGGY.get((pluggy_category or '').strip().lower())
    return hit[0] if hit else None


def to_finfam(pluggy_category: str | None, family_categories: list[str]) -> str | None:
    """Categoria do FinFam para o lançamento: a da família que mais se aproxima da Pluggy.
    Ordem: o nome em português já existe na família → a categoria padrão parecida (ou um apelido
    dela que a família use) → o nome em português (categoria nova). Sem pista útil → None."""
    name = (pluggy_category or '').strip().lower()
    if not name or name in _GENERIC or name not in PLUGGY:
        return None
    pt, base = PLUGGY[name]
    have = {_key(c): c for c in family_categories if c}
    if _key(pt) in have:
        return have[_key(pt)]
    target = base or pt
    for alias in _ALIASES.get(target, (target,)):
        if _key(alias) in have:
            return have[_key(alias)]
    return target  # padrão do FinFam (Alimentação, Transporte…) ou, sem parecida, o nome em português
