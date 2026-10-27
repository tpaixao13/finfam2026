// Distribuição dos investimentos por classe ou por instituição (carteira lançada à mão + bancos).

export interface Holding { klass: string; place: string; value: number }
export interface Slice { label: string; count: number; value: number; share: number }

const FIXED = /tesouro|cdb|lci|lca|cri|cra|poupan|deb[eê]nture|renda fixa/i

/** Classe de um investimento lançado no FinFam (pelo tipo escolhido no formulário). */
export function manualClass(type: string, crypto: boolean): string {
  if (crypto || /cripto/i.test(type)) return 'Cripto'
  if (FIXED.test(type)) return 'Renda Fixa'
  if (/fii/i.test(type)) return 'Fundos imobiliários'
  if (/fundo|multimercado/i.test(type)) return 'Fundos'
  if (/a[cç][oõ]es/i.test(type)) return 'Ações'
  return 'Outros'
}

/** Classe de um investimento que o banco informa (tipo e subtipo da Pluggy). */
export function bankClass(type: string | null, subtype: string | null): string {
  const t = (type ?? '').toUpperCase(), s = (subtype ?? '').toUpperCase()
  if (t === 'FIXED_INCOME' || s === 'TREASURY' || s === 'SAVINGS') return 'Renda Fixa'
  if (s === 'REAL_ESTATE_FUND') return 'Fundos imobiliários'
  if (t === 'EQUITY' || t === 'ETF' || ['STOCK', 'ETF', 'BDR'].includes(s)) return 'Ações'
  if (t === 'MUTUAL_FUND' || s.endsWith('_FUND')) return 'Fundos'
  if (t === 'SECURITY' || s === 'PENSION') return 'Previdência'
  return 'Outros'
}

/** Agrupa e ordena do maior para o menor, com a fatia de cada um no total. */
export function allocate(items: Holding[], by: 'klass' | 'place'): Slice[] {
  const total = items.reduce((s, i) => s + i.value, 0)
  const groups = new Map<string, { count: number; value: number }>()
  for (const i of items) {
    const g = groups.get(i[by]) ?? { count: 0, value: 0 }
    g.count += 1
    g.value += i.value
    groups.set(i[by], g)
  }
  return [...groups.entries()]
    .map(([label, g]) => ({ label, ...g, share: total > 0 ? g.value / total : 0 }))
    .sort((a, b) => b.value - a.value)
}
