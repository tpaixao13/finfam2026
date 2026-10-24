export const MONTHS = ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
  'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']
export const MONTHS_SHORT = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']

const brlFmt = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' })
const compactFmt = new Intl.NumberFormat('pt-BR', { notation: 'compact', maximumFractionDigits: 1 })

export function brl(value: number | null | undefined): string {
  return brlFmt.format(value ?? 0)
}

/** Valor em outra moeda (US$ 1.000,00, € 50,00...). */
export function money(value: number | null | undefined, currency = 'BRL'): string {
  if (currency === 'BRL') return brl(value)
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency, currencyDisplay: 'symbol' }).format(value ?? 0)
}

/** Cotação em R$ com 2 a 4 casas (ARS/JPY valem centavos). */
export function rate(value: number | null | undefined): string {
  const v = value ?? 0
  return 'R$ ' + v.toLocaleString('pt-BR', { minimumFractionDigits: v < 1 ? 4 : 2, maximumFractionDigits: v < 1 ? 4 : 2 })
}

export function compact(value: number): string {
  return compactFmt.format(value)
}

export function pct(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined) return '—'
  return `${value.toFixed(digits).replace('.', ',')}%`
}

/** 12.5 → "12,5" (campos de texto que aceitam vírgula). */
export function decimalBR(value: number): string {
  return String(value).replace('.', ',')
}

export function dateBR(iso: string | null | undefined): string {
  if (!iso) return '—'
  const [y, m, d] = iso.slice(0, 10).split('-')
  return `${d}/${m}/${y}`
}

export function dayMonth(iso: string | null | undefined): string {
  if (!iso) return '—'
  const [, m, d] = iso.slice(0, 10).split('-')
  return `${d}/${m}`
}

export function todayISO(): string {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

export function shiftMonth(month: number, year: number, offset: number): { month: number; year: number } {
  const idx = year * 12 + (month - 1) + offset
  return { month: (idx % 12) + 1, year: Math.floor(idx / 12) }
}

/** Converte "1.234,56" ou "1234.56" em número. */
export function parseMoney(input: string): number {
  const s = input.trim().replace(/\s|R\$/g, '')
  if (!s) return NaN
  const normalized = s.includes(',') ? s.replace(/\./g, '').replace(',', '.') : s
  return Number(normalized)
}

export function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]!.toUpperCase()).join('')
}
