// Tipos das respostas da API (espelham app/services/serializers.py).

export interface User { id: number; name: string; email: string | null; avatar_url: string | null; color?: string; two_factor_enabled?: boolean }

export interface Tenant {
  id: number; name: string; plan: string; plan_label: string; member_limit: number
  expires_at: string | null; days_left: number | null; active: boolean; extra_members: number
}

export interface Me { user: User; tenant: Tenant | null; members: User[]; alert_count: number; via_api_token: boolean }

export interface Series { type: 'parcelado' | 'recorrente'; group_id: number; number: number; total: number; label: string }

export interface Expense {
  id: number; user_id: number; user_name: string | null; description: string; amount: number
  category: string; payment_method: string; bank: string | null; day: number; month: number; year: number
  purchase_month: number; purchase_year: number; purchase_date: string; due_date: string
  status: 'pago' | 'pendente' | 'vencida'; paid: boolean; card_id: number | null; card_label: string | null
  series: Series | null; created_at: string | null
}

export interface Insight {
  type: 'danger' | 'warning' | 'success' | 'info'; icon: string; title: string; message: string
  value?: string | null; link?: string
}

export interface Alert {
  type: 'danger' | 'warning' | 'success' | 'info'; icon: string; title: string; message: string
  action_label?: string | null; action_url?: string | null; key: string
}

export interface Goal {
  id: number; user_id: number; title: string; type: string; type_label: string; emoji: string
  category: string | null; target_amount: number; start_date: string; due_date: string | null
  completed_at: string | null; current: number; remaining: number; pct: number; pct_used: number
  over_amount: number; status: string; status_label: string; status_color: string; coaching: string
  forecast: string | null; days_remaining: number | null
}

export interface Card {
  id: number; user_id: number; owner_name: string | null; label: string; nickname: string | null
  last_digits: string; bank: string | null; due_day: number; best_buy_day: number
  credit_limit: number | null; effective_limit: number | null; card_type: string; type_label: string
  supports_credit: boolean; supports_debit: boolean; is_virtual: boolean; is_additional: boolean
  monthly_amount: number | null; renewal_day: number | null; account_id: number | null; account_label: string | null
}

export interface VrBalance { monthly: number; carry_over: number; available: number; spent: number; remaining: number; pct_used: number }

export interface CardUsage {
  total: number; credit_total: number; debit_total: number; limit: number | null; pct: number | null
  available: number | null; vr_balance: VrBalance | null; account_cards: number
  payment_status: { paid_total: number; pending_total: number; total: number; status: string } | null
  status: string; gradient: string; text_color: string
}

export interface Income {
  id: number; user_id: number; user_name: string | null; year: number; month: number; amount: number
  company: string | null; income_type: 'fixa' | 'variavel'; payment_day: number | null
  payment_day_type: 'fixo' | 'util' | null; received: boolean; group_id: number | null; expected_date: string | null
}

export interface Investment {
  id: number; user_id: number; user_name: string | null; description: string | null; amount: number
  investment_type: string; annual_rate: number; crypto_coin: string | null; crypto_buy_price: number | null
  currency: string; fx_buy_rate: number | null
  year: number; month: number; created_at: string | null
  daily_yield?: { gross: number; net: number; iof_rate: number; ir_rate: number; exempt: boolean; foreign?: boolean }
  // só na carteira (GET /investments): valores em reais
  invested_brl?: number; value_brl?: number; fx_gain?: number; fx_rate?: number | null; fx_is_live?: boolean
}

export interface Meta {
  categories: string[]; payment_methods: string[]; banks: string[]; bank_payment_methods: string[]
  months: string[]; investment_types: string[]; crypto_coins: { id: string; label: string }[]
  currencies: { code: string; name: string; symbol: string }[]
  variable_income_sources: string[]; goal_types: { id: string; label: string; emoji: string }[]
  category_colors: Record<string, string>; payment_colors: Record<string, string>
}

export interface Message { message: string }
