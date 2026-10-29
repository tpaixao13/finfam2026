// Formatos que /pluggy/* devolve (espelham backend/app/routers/pluggy.py).

export interface PluggyConfig {
  available: boolean; max_items: number | null; configured: boolean; client_id_masked: string
  status: 'ok' | 'error' | null; last_error: string | null; webhook: boolean; public_url_ok: boolean
  include_sandbox: boolean; connections: number; to_review: number; notify: boolean
  shared_from_name: string | null          // usando a chave dessa pessoa da família
  shared_with: (string | null)[]           // quem da família usa a minha chave
  family_key: { owner_name: string | null; client_id_masked: string } | null  // chave da família para usar
}
export type Saved = { message: string; config: PluggyConfig }

export interface BankAccount {
  id: number; type: 'BANK' | 'CREDIT'; subtype: string | null; name: string | null; last_digits: string | null
  currency: string; balance: number | null; credit_limit: number | null; card_id: number | null; card_label: string | null
  available_limit: number | null; close_day: number | null; due_day: number | null
  close_source: 'bank' | 'manual' | 'inferred' | 'card' | null; manual_close_day: number | null
}
export interface Connection {
  id: number; item_id: string; bank_name: string | null; connector_name: string | null; auto_import: boolean; bank_color: string | null; status: string | null
  execution_status: string | null; error: string | null; last_updated_at: string | null; accounts: BankAccount[]
  shared: boolean; mine: boolean; owner_name: string | null   // banco compartilhado com a família
}
