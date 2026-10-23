// Gráficos em SVG puro: barras agrupadas, linhas e rosca. Leves e sem dependências.
import { useLayoutEffect, useRef, useState } from 'react'
import { brl, compact } from '../lib/format'

export interface ChartSeries { label: string; data: number[]; color: string }

function niceMax(v: number): number {
  if (v <= 0) return 1
  const exp = Math.pow(10, Math.floor(Math.log10(v)))
  const n = v / exp
  const step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 4 ? 4 : n <= 5 ? 5 : n <= 8 ? 8 : 10
  return step * exp
}

function Legend({ series }: { series: { label: string; color: string }[] }) {
  if (series.length < 2) return null
  return (
    <ul className="legend">
      {series.map((s) => <li key={s.label}><i style={{ background: s.color }} />{s.label}</li>)}
    </ul>
  )
}

interface XYProps { labels: string[]; series: ChartSeries[]; height?: number; ariaLabel: string }

/** Largura real do contêiner, para o SVG não escalar texto. */
function useWidth() {
  const ref = useRef<HTMLElement>(null)
  const [w, setW] = useState(640)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    setW(Math.max(280, el.clientWidth))
    const ro = new ResizeObserver((entries) => setW(Math.max(280, Math.round(entries[0].contentRect.width))))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, w] as const
}
const PAD = { top: 12, right: 8, bottom: 26, left: 48 }

function Grid({ max, height, W }: { max: number; height: number; W: number }) {
  const ticks = [0, 0.25, 0.5, 0.75, 1]
  const ih = height - PAD.top - PAD.bottom
  return (
    <g className="chart-grid">
      {ticks.map((t) => {
        const y = PAD.top + ih - t * ih
        return (
          <g key={t}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y} y2={y} />
            <text x={PAD.left - 6} y={y + 4} textAnchor="end">{compact(max * t)}</text>
          </g>
        )
      })}
    </g>
  )
}

export function BarChart({ labels, series, height = 240, ariaLabel }: XYProps) {
  const [ref, W] = useWidth()
  const [hover, setHover] = useState<number | null>(null)
  const max = niceMax(Math.max(0, ...series.flatMap((s) => s.data)))
  const iw = W - PAD.left - PAD.right
  const ih = height - PAD.top - PAD.bottom
  const group = iw / Math.max(1, labels.length)
  const gap = Math.min(8, group * 0.2)
  const barW = Math.max(2, (group - gap * 2) / Math.max(1, series.length) - 2)
  const every = Math.max(1, Math.ceil(64 / group))
  return (
    <figure className="chart" ref={ref}>
      <svg viewBox={`0 0 ${W} ${height}`} role="img" aria-label={ariaLabel} onMouseLeave={() => setHover(null)}>
        <Grid max={max} height={height} W={W} />
        {labels.map((label, i) => {
          const x0 = PAD.left + i * group + gap
          return (
            <g key={label} onMouseEnter={() => setHover(i)}>
              <rect x={PAD.left + i * group} y={PAD.top} width={group} height={ih} fill="transparent" />
              {hover === i && <rect className="chart-hover" x={PAD.left + i * group} y={PAD.top} width={group} height={ih} />}
              {series.map((s, j) => {
                const h = (s.data[i] / max) * ih
                return <rect key={s.label} x={x0 + j * (barW + 2)} y={PAD.top + ih - h} width={barW}
                  height={Math.max(0, h)} rx={3} style={{ fill: s.color }} />
              })}
              {i % every === 0 && <text className="chart-x" x={PAD.left + i * group + group / 2} y={height - 8} textAnchor="middle">{label}</text>}
            </g>
          )
        })}
      </svg>
      {hover !== null && (
        <div className="chart-tip">
          <b>{labels[hover]}</b>
          {series.map((s) => <span key={s.label}><i style={{ background: s.color }} />{s.label}: {brl(s.data[hover])}</span>)}
        </div>
      )}
      <Legend series={series} />
    </figure>
  )
}

export function LineChart({ labels, series, height = 220, ariaLabel }: XYProps) {
  const [ref, W] = useWidth()
  const [hover, setHover] = useState<number | null>(null)
  const all = series.flatMap((s) => s.data)
  const max = niceMax(Math.max(0, ...all))
  const iw = W - PAD.left - PAD.right
  const ih = height - PAD.top - PAD.bottom
  const x = (i: number) => PAD.left + (labels.length <= 1 ? iw / 2 : (i / (labels.length - 1)) * iw)
  const y = (v: number) => PAD.top + ih - (v / max) * ih
  const every = Math.max(Math.ceil(labels.length / 10), Math.ceil(64 / (iw / Math.max(1, labels.length - 1))))
  return (
    <figure className="chart" ref={ref}>
      <svg viewBox={`0 0 ${W} ${height}`} role="img" aria-label={ariaLabel} onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect()
          const px = ((e.clientX - rect.left) / rect.width) * W
          const i = Math.round(((px - PAD.left) / iw) * (labels.length - 1))
          setHover(Math.max(0, Math.min(labels.length - 1, i)))
        }}>
        <Grid max={max} height={height} W={W} />
        {labels.map((l, i) => i % every === 0 && (
          <text key={l + i} className="chart-x" x={x(i)} y={height - 8} textAnchor="middle">{l}</text>
        ))}
        {series.map((s) => (
          <polyline key={s.label} fill="none" style={{ stroke: s.color }} strokeWidth={2.5} strokeLinejoin="round"
            points={s.data.map((v, i) => `${x(i)},${y(v)}`).join(' ')} />
        ))}
        {hover !== null && (
          <g>
            <line className="chart-cursor" x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={PAD.top + ih} />
            {series.map((s) => <circle key={s.label} cx={x(hover)} cy={y(s.data[hover])} r={4} style={{ fill: s.color }} />)}
          </g>
        )}
      </svg>
      {hover !== null && (
        <div className="chart-tip">
          <b>{labels[hover]}</b>
          {series.map((s) => <span key={s.label}><i style={{ background: s.color }} />{s.label}: {brl(s.data[hover])}</span>)}
        </div>
      )}
      <Legend series={series} />
    </figure>
  )
}

export function Donut({ labels, data, colors, centerLabel, ariaLabel }:
  { labels: string[]; data: number[]; colors: string[]; centerLabel?: string; ariaLabel: string }) {
  const [hover, setHover] = useState<number | null>(null)
  const total = data.reduce((a, b) => a + b, 0)
  const R = 70, r = 46, C = 90
  let angle = -Math.PI / 2
  const arcs = data.map((v, i) => {
    const frac = total > 0 ? v / total : 0
    const a0 = angle
    const a1 = angle + frac * Math.PI * 2
    angle = a1
    const large = a1 - a0 > Math.PI ? 1 : 0
    const p = (rad: number, a: number) => `${C + rad * Math.cos(a)},${C + rad * Math.sin(a)}`
    const d = frac >= 0.9999
      ? `M ${C - R},${C} a ${R},${R} 0 1,0 ${R * 2},0 a ${R},${R} 0 1,0 ${-R * 2},0 M ${C - r},${C} a ${r},${r} 0 1,1 ${r * 2},0 a ${r},${r} 0 1,1 ${-r * 2},0`
      : `M ${p(R, a0)} A ${R},${R} 0 ${large} 1 ${p(R, a1)} L ${p(r, a1)} A ${r},${r} 0 ${large} 0 ${p(r, a0)} Z`
    return { d, color: colors[i], i }
  })
  const shown = hover !== null ? { label: labels[hover], value: data[hover] } : { label: centerLabel ?? 'Total', value: total }
  return (
    <figure className="donut">
      <svg viewBox="0 0 180 180" role="img" aria-label={ariaLabel} onMouseLeave={() => setHover(null)}>
        {arcs.map((a) => (
          <path key={a.i} d={a.d} style={{ fill: a.color }} fillRule="evenodd" stroke="var(--surface)" strokeWidth={2}
            opacity={hover === null || hover === a.i ? 1 : 0.35} onMouseEnter={() => setHover(a.i)} />
        ))}
        <text x={C} y={C - 4} textAnchor="middle" className="donut-label">{shown.label}</text>
        <text x={C} y={C + 16} textAnchor="middle" className="donut-value">{brl(shown.value)}</text>
      </svg>
      <ul className="donut-legend">
        {labels.map((l, i) => (
          <li key={l} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
            <i style={{ background: colors[i] }} /><span>{l}</span>
            <b>{total > 0 ? Math.round((data[i] / total) * 100) : 0}%</b>
          </li>
        ))}
      </ul>
    </figure>
  )
}
