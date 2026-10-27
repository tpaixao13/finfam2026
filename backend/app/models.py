"""Models SQLAlchemy.

O schema é o MESMO do FinFam original (tabelas e colunas), então um `gastos.db`
existente pode ser usado diretamente. Tabelas novas: `api_tokens`, `telegram_links`,
`telegram_link_codes`, `ai_configs`, `pluggy_configs`, `bank_connections`, `bank_accounts` e `bank_transactions`
(criadas sozinhas ao subir a API).
"""
import calendar
from datetime import datetime, date as _date, timedelta

from sqlalchemy import (Boolean, Column, Date, DateTime, ForeignKey, Integer,
                        Numeric, String, Text)
from sqlalchemy.orm import relationship

from app.core.types import utcnow
from app.core.db import Base
from app.core.security import hash_password, verify_password


# ── Feriados nacionais (para "N-ésimo dia útil") ─────────────────────────────

def _easter(year: int) -> _date:
    """Data da Páscoa (algoritmo de Butcher)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return _date(year, month, day)


def br_holidays(year: int) -> set:
    easter = _easter(year)
    holidays = {
        _date(year, 1, 1), _date(year, 4, 21), _date(year, 5, 1), _date(year, 9, 7),
        _date(year, 10, 12), _date(year, 11, 2), _date(year, 11, 15), _date(year, 12, 25),
        easter - timedelta(days=48), easter - timedelta(days=47),   # Carnaval
        easter - timedelta(days=2),                                  # Sexta-feira Santa
        easter + timedelta(days=60),                                 # Corpus Christi
    }
    if year >= 2024:
        holidays.add(_date(year, 11, 20))  # Consciência Negra
    return holidays


# ── Planos ───────────────────────────────────────────────────────────────────

PLAN_LIMITS = {'trial': 1, 'mensal': 1, 'anual': 2, 'vitalicio': 4}
PLAN_LABELS = {'trial': 'Trial', 'mensal': 'Mensal', 'anual': 'Anual', 'vitalicio': 'Vitalício'}


class Tenant(Base):
    __tablename__ = 'tenants'
    __table_args__ = {'sqlite_autoincrement': True}

    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False)
    code = Column(Text, nullable=False, unique=True)
    created_at = Column(DateTime, default=utcnow)
    telegram_enabled = Column(Boolean, default=False)
    telegram_token = Column(Text, nullable=True)
    telegram_chat_id = Column(Text, nullable=True)
    telegram_hour = Column(Integer, default=8)
    telegram_minute = Column(Integer, default=0)
    telegram_last_sent = Column(Date, nullable=True)
    trial_expires_at = Column(DateTime, nullable=True)
    plan = Column(Text, default='trial', nullable=False)
    extra_members = Column(Integer, default=0, nullable=False)
    # Open Finance (Pluggy): liberado pelo admin, por família
    open_finance_enabled = Column(Boolean, default=False, server_default='0', nullable=False)
    open_finance_max_items = Column(Integer, nullable=True)  # bancos por pessoa; None = sem limite

    users = relationship('User', backref='tenant', lazy='dynamic', cascade='all, delete-orphan')
    category_rules = relationship('CategoryRule', lazy='dynamic', cascade='all, delete-orphan')
    ai_config = relationship('AiConfig', uselist=False, cascade='all, delete-orphan')

    @property
    def trial_active(self) -> bool:
        return self.trial_expires_at is None or utcnow() <= self.trial_expires_at

    @property
    def trial_days_left(self) -> int | None:
        if self.trial_expires_at is None:
            return None
        return max(0, (self.trial_expires_at - utcnow()).days)

    @property
    def member_limit(self) -> int:
        return PLAN_LIMITS.get(self.plan or 'trial', 1) + (self.extra_members or 0)

    @property
    def plan_label(self) -> str:
        return PLAN_LABELS.get(self.plan or 'trial', 'Trial')


class User(Base):
    __tablename__ = 'users'
    __table_args__ = {'sqlite_autoincrement': True}

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey('tenants.id'), nullable=True)
    name = Column(Text, nullable=False)
    email = Column(Text, nullable=True, unique=True)
    password_hash = Column(Text, nullable=True)
    avatar = Column(Text, nullable=True)
    is_admin = Column(Boolean, default=False)
    last_seen = Column(DateTime, nullable=True)
    # verificação em duas etapas (TOTP): segredo criptografado; bloqueio após 3 códigos errados
    totp_secret = Column(Text, nullable=True)
    totp_enabled = Column(Boolean, default=False, server_default='0', nullable=False)
    totp_failures = Column(Integer, default=0, server_default='0', nullable=False)
    totp_locked = Column(Boolean, default=False, server_default='0', nullable=False)
    totp_last_step = Column(Integer, nullable=True)  # impede reusar o mesmo código

    salaries = relationship('Salary', backref='user', lazy='dynamic', cascade='all, delete-orphan')
    expenses = relationship('Expense', backref='user', lazy='dynamic', cascade='all, delete-orphan')
    installment_groups = relationship('InstallmentGroup', backref='user', lazy='dynamic',
                                      cascade='all, delete-orphan')
    recurring_groups = relationship('RecurringGroup', backref='user', lazy='dynamic',
                                    cascade='all, delete-orphan')
    investments = relationship('Investment', backref='user', lazy='dynamic', cascade='all, delete-orphan')
    credit_cards = relationship('CreditCard', backref='owner', lazy='dynamic',
                                cascade='all, delete-orphan', foreign_keys='CreditCard.user_id')
    api_tokens = relationship('ApiToken', backref='user', lazy='dynamic', cascade='all, delete-orphan')
    # sem backref: só garantem que nada do usuário fique órfão ao excluí-lo
    goals = relationship('Goal', lazy='dynamic', cascade='all, delete-orphan')
    credit_accounts = relationship('CreditAccount', lazy='dynamic', cascade='all, delete-orphan')
    salary_groups = relationship('SalaryGroup', lazy='dynamic', cascade='all, delete-orphan')
    telegram_links = relationship('TelegramLink', lazy='dynamic', cascade='all, delete-orphan')
    telegram_codes = relationship('TelegramLinkCode', lazy='dynamic', cascade='all, delete-orphan')
    pluggy_config = relationship('PluggyConfig', uselist=False, cascade='all, delete-orphan')
    bank_connections = relationship('BankConnection', lazy='dynamic', cascade='all, delete-orphan')

    def set_password(self, password: str) -> None:
        self.password_hash = hash_password(password)

    def change_password(self, password: str) -> int:
        """Troca a senha e desliga tudo que dava acesso sem ela: sessões (pelo `fp` do JWT),
        tokens de API e Telegram conectado. Devolve quantos acessos foram desligados."""
        self.set_password(password)
        tokens = self.api_tokens.filter_by(revoked=False).update({'revoked': True}, synchronize_session=False)
        chats = self.telegram_links.delete(synchronize_session=False)
        self.telegram_codes.delete(synchronize_session=False)
        return tokens + chats

    def check_password(self, password: str) -> bool:
        return verify_password(self.password_hash, password)


class ApiToken(Base):
    """Token de API de longa duração (para o servidor MCP / bot do Telegram).
    Só o hash SHA-256 é guardado; o token em texto puro aparece uma única vez."""
    __tablename__ = 'api_tokens'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    name = Column(Text, nullable=False)
    token_hash = Column(String(64), nullable=False, unique=True)
    prefix = Column(String(16), nullable=False)
    created_at = Column(DateTime, default=utcnow)
    last_used_at = Column(DateTime, nullable=True)
    revoked = Column(Boolean, default=False, nullable=False)


class TelegramLink(Base):
    """Conversa do Telegram vinculada a uma pessoa (bot central do FinFam)."""
    __tablename__ = 'telegram_links'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    chat_id = Column(String(32), nullable=False, unique=True)
    username = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    user = relationship('User', overlaps='telegram_links')


class TelegramLinkCode(Base):
    """Código de uso único (15 min) do link t.me/<bot>?start=<código>. Guarda só o hash."""
    __tablename__ = 'telegram_link_codes'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    code_hash = Column(String(64), nullable=False, unique=True)
    expires_at = Column(DateTime, nullable=False)


class AiConfig(Base):
    """Provedor de IA da família, com a chave dela (criptografada)."""
    __tablename__ = 'ai_configs'

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey('tenants.id'), nullable=False, unique=True)
    provider = Column(String(20), nullable=False)          # anthropic | gemini | openai
    model = Column(Text, nullable=True)
    base_url = Column(Text, nullable=True)                 # só para compatíveis com OpenAI
    api_key = Column(Text, nullable=False)                 # encrypt_field(...)
    enabled = Column(Boolean, default=True, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class PluggyConfig(Base):
    """Conta Pluggy de UMA pessoa (cada membro usa a própria chave). O segredo e o
    segredo do webhook ficam criptografados; `hook_token` identifica a pessoa na URL do webhook."""
    __tablename__ = 'pluggy_configs'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, unique=True)
    client_id = Column(String(64), nullable=False)
    client_secret = Column(Text, nullable=False)          # encrypt_field(...)
    hook_token = Column(String(48), nullable=False, unique=True)
    hook_secret = Column(Text, nullable=False)            # encrypt_field(...)
    webhook_id = Column(String(64), nullable=True)        # id do webhook na conta Pluggy da pessoa
    status = Column(String(20), nullable=False, default='ok')  # ok | error
    last_error = Column(Text, nullable=True)
    consent_at = Column(DateTime, nullable=False, default=utcnow)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class BankConnection(Base):
    """Um banco conectado por uma pessoa (um "item" da Pluggy)."""
    __tablename__ = 'bank_connections'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    item_id = Column(String(64), nullable=False, unique=True)
    bank_name = Column(Text, nullable=True)               # nome que a Pluggy dá (ex.: "MeuPluggy")
    display_name = Column(Text, nullable=True)            # nome escolhido pela pessoa (ex.: "Caixa")
    bank_color = Column(String(12), nullable=True)
    status = Column(String(32), nullable=True)            # UPDATED | UPDATING | LOGIN_ERROR | OUTDATED | WAITING_...
    execution_status = Column(String(48), nullable=True)
    error_message = Column(Text, nullable=True)
    last_updated_at = Column(DateTime, nullable=True)     # última atualização do banco na Pluggy
    needs_sync = Column(Boolean, default=False, nullable=False)  # webhook avisou novidade: reler contas
    auto_import = Column(Boolean, default=False, nullable=False)  # lançar sozinho o que chega (sem revisar)
    import_from = Column(Date, nullable=True)             # desde quando trazer lançamentos (escolha ao conectar)
    tx_synced_at = Column(DateTime, nullable=True)        # última leitura dos lançamentos
    created_at = Column(DateTime, default=utcnow)

    accounts = relationship('BankAccount', backref='connection', lazy='dynamic', cascade='all, delete-orphan')

    @property
    def label(self) -> str:
        return self.display_name or self.bank_name or 'Banco'


class BankAccount(Base):
    """Conta corrente/poupança ou cartão de crédito de uma conexão, ligável a um cartão do FinFam."""
    __tablename__ = 'bank_accounts'

    id = Column(Integer, primary_key=True)
    connection_id = Column(Integer, ForeignKey('bank_connections.id'), nullable=False, index=True)
    pluggy_account_id = Column(String(64), nullable=False, unique=True)
    type = Column(String(16), nullable=False)             # BANK | CREDIT
    subtype = Column(String(32), nullable=True)           # CHECKING_ACCOUNT | SAVINGS_ACCOUNT | CREDIT_CARD
    name = Column(Text, nullable=True)
    number = Column(String(32), nullable=True)
    balance = Column(Numeric(14, 2), nullable=True)
    currency = Column(String(3), nullable=True)
    credit_limit = Column(Numeric(14, 2), nullable=True)
    card_id = Column(Integer, ForeignKey('credit_cards.id', ondelete='SET NULL'), nullable=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    transactions = relationship('BankTransaction', backref='account', lazy='dynamic', cascade='all, delete-orphan')


class BankTransaction(Base):
    """Lançamento que chegou do banco e espera revisão. Nunca vira despesa sozinho.

    status: pending (revisar) | duplicate (já estava no FinFam) | ignored | imported
    """
    __tablename__ = 'bank_transactions'

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey('bank_accounts.id'), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    pluggy_id = Column(String(64), nullable=False, unique=True)
    date = Column(Date, nullable=False)                   # data (fuso de Brasília) do lançamento
    purchase_date = Column(Date, nullable=True)           # compra original (parcelas)
    description = Column(Text, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)       # sempre positivo (saída de dinheiro)
    installment_number = Column(Integer, nullable=True)
    total_installments = Column(Integer, nullable=True)
    total_amount = Column(Numeric(12, 2), nullable=True)
    pluggy_category = Column(Text, nullable=True)
    payment_hint = Column(String(16), nullable=True)      # PIX | BOLETO | TED ... (contas)
    bank_status = Column(String(12), nullable=True)       # PENDING | POSTED
    status = Column(String(12), nullable=False, default='pending', index=True)
    reason = Column(Text, nullable=True)
    expense_id = Column(Integer, ForeignKey('expenses.id', ondelete='SET NULL'), nullable=True)
    created_at = Column(DateTime, default=utcnow)
    reviewed_at = Column(DateTime, nullable=True)


class SalaryGroup(Base):
    __tablename__ = 'salary_groups'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime, default=utcnow)

    salaries = relationship('Salary', backref='salary_group', lazy='dynamic',
                            foreign_keys='Salary.salary_group_id')


class Salary(Base):
    __tablename__ = 'salaries'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    salary_group_id = Column(Integer, ForeignKey('salary_groups.id'), nullable=True)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    company = Column(Text, nullable=True)
    income_type = Column(Text, nullable=False, default='fixa')
    payment_day = Column(Integer, nullable=True)
    payment_day_type = Column(Text, nullable=True)  # 'fixo' | 'util'
    received = Column(Boolean, default=True)

    def expected_payment_date(self) -> _date | None:
        """Data prevista de pagamento (dia fixo ou N-ésimo dia útil, sem fins de semana e feriados)."""
        if not self.payment_day or not self.payment_day_type:
            return None
        last_day = calendar.monthrange(self.year, self.month)[1]
        if self.payment_day_type == 'util':
            holidays = br_holidays(self.year)
            count = 0
            for d in range(1, last_day + 1):
                dt = _date(self.year, self.month, d)
                if dt.weekday() < 5 and dt not in holidays:
                    count += 1
                    if count == self.payment_day:
                        return dt
            return None
        return _date(self.year, self.month, min(self.payment_day, last_day))


class InstallmentGroup(Base):
    __tablename__ = 'installment_groups'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    description = Column(Text, nullable=False)
    total_amount = Column(Numeric(12, 2), nullable=False)
    num_installments = Column(Integer, nullable=False)
    bank = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow)

    installments = relationship('Expense', backref='installment_group', lazy='dynamic',
                                cascade='all, delete-orphan', foreign_keys='Expense.installment_group_id')


class RecurringGroup(Base):
    __tablename__ = 'recurring_groups'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    description = Column(Text, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    num_recurrences = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=utcnow)

    recurrences = relationship('Expense', backref='recurring_group', lazy='dynamic',
                               cascade='all, delete-orphan', foreign_keys='Expense.recurring_group_id')


class Expense(Base):
    __tablename__ = 'expenses'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    description = Column(Text, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    category = Column(Text, nullable=False)
    payment_method = Column(Text, nullable=False)
    bank = Column(Text, nullable=True)
    # year/month = competência (mês da FATURA para cartão de crédito)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    day = Column(Integer, nullable=False)
    # mês/ano real da compra quando difere da fatura (fechamento do cartão)
    purchase_month = Column(Integer, nullable=True)
    purchase_year = Column(Integer, nullable=True)
    installment_group_id = Column(Integer, ForeignKey('installment_groups.id'), nullable=True)
    installment_number = Column(Integer, nullable=True)
    recurring_group_id = Column(Integer, ForeignKey('recurring_groups.id'), nullable=True)
    recurring_number = Column(Integer, nullable=True)
    card_id = Column(Integer, ForeignKey('credit_cards.id'), nullable=True)
    paid = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow)

    @property
    def display_month(self) -> int:
        return self.purchase_month or self.month

    @property
    def display_year(self) -> int:
        return self.purchase_year or self.year


class CreditAccount(Base):
    """Linha de crédito: cartões do mesmo banco que compartilham o limite."""
    __tablename__ = 'credit_accounts'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    bank = Column(Text, nullable=True)
    label = Column(Text, nullable=True)
    credit_limit = Column(Numeric(12, 2), nullable=True)
    created_at = Column(DateTime, default=utcnow)

    cards = relationship('CreditCard', backref='account', lazy='dynamic', foreign_keys='CreditCard.account_id')

    @property
    def display_label(self) -> str:
        return self.label or self.bank or 'Conta de Crédito'


class CreditCard(Base):
    __tablename__ = 'credit_cards'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    account_id = Column(Integer, ForeignKey('credit_accounts.id'), nullable=True)
    nickname = Column(Text, nullable=True)
    last_digits = Column(String(4), nullable=False)
    bank = Column(Text, nullable=True)
    due_day = Column(Integer, nullable=False)
    best_buy_day = Column(Integer, nullable=False)
    credit_limit = Column(Numeric(12, 2), nullable=True)
    card_type = Column(String(10), nullable=False, default='credit')  # credit|debit|vr|va
    supports_credit = Column(Boolean, default=False, nullable=False)
    supports_debit = Column(Boolean, default=False, nullable=False)
    is_virtual = Column(Boolean, default=False, nullable=False)
    is_additional = Column(Boolean, default=False, nullable=False)
    monthly_amount = Column(Numeric(12, 2), nullable=True)
    renewal_day = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    expenses = relationship('Expense', backref='credit_card', lazy='dynamic', foreign_keys='Expense.card_id')

    @property
    def effective_limit(self) -> float | None:
        """Limite real: o da conta (compartilhado) se houver, senão o do cartão."""
        if self.account is not None and self.account.credit_limit:
            return float(self.account.credit_limit)
        return float(self.credit_limit) if self.credit_limit else None

    @property
    def is_vr_va(self) -> bool:
        return (self.card_type or '') in ('vr', 'va')

    @property
    def type_label(self) -> str:
        if self.is_vr_va:
            return self.card_type.upper()
        sc, sd = bool(self.supports_credit), bool(self.supports_debit)
        if sc and sd:
            return 'Crédito/Débito'
        return 'Débito' if sd else 'Crédito'

    @property
    def label(self) -> str:
        name = self.nickname or self.bank or 'Cartão'
        if self.is_virtual:
            suffix = '(Virtual)'
        elif self.is_additional:
            suffix = '(Adicional)'
        else:
            suffix = f'({self.type_label})'
        return f'{name} •••• {self.last_digits} {suffix}'


class Goal(Base):
    __tablename__ = 'goals'

    TYPES = {
        'saving': ('💰', 'Economizar'),
        'spending_limit': ('📉', 'Limite de gastos'),
        'debt_reduction': ('💳', 'Quitar dívida'),
    }

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    title = Column(Text, nullable=False)
    type = Column(Text, nullable=False)
    target_amount = Column(Numeric(12, 2), nullable=False)
    category = Column(Text, nullable=True)
    due_date = Column(Date, nullable=True)
    start_date = Column(Date, nullable=False)
    created_at = Column(DateTime, default=utcnow)
    completed_at = Column(DateTime, nullable=True)
    active = Column(Boolean, default=True, nullable=False)

    @property
    def emoji(self) -> str:
        return self.TYPES.get(self.type, ('🎯', ''))[0]

    @property
    def type_label(self) -> str:
        return self.TYPES.get(self.type, ('', self.type))[1]


class CategoryRule(Base):
    """Regra: se a descrição contém `keyword` → `category`."""
    __tablename__ = 'category_rules'

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey('tenants.id'), nullable=False)
    keyword = Column(Text, nullable=False)
    category = Column(Text, nullable=False)
    match_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=utcnow)


class Investment(Base):
    __tablename__ = 'investments'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    description = Column(Text, nullable=True)
    amount = Column(Numeric(12, 2), nullable=False)
    investment_type = Column(Text, nullable=False)
    annual_rate = Column(Numeric(6, 2), nullable=False)
    crypto_coin = Column(Text, nullable=True)
    crypto_buy_price = Column(Numeric(18, 8), nullable=True)
    currency = Column(String(3), nullable=True)          # None/BRL = real; senão `amount` está nessa moeda
    fx_buy_rate = Column(Numeric(14, 6), nullable=True)  # R$ por unidade da moeda na compra
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=utcnow)
