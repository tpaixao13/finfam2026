"""Dados de mercado: Selic (Banco Central) e cotações de cripto (CoinGecko), com cache."""
import json
import logging
import ssl
import time
import urllib.request

from app.core.config import settings

log = logging.getLogger('finfam.market')

_SELIC_URL = 'https://api.bcb.gov.br/dados/serie/bcdata.sgs.432/dados/ultimos/1?formato=json'
SELIC_FALLBACK = 14.75
# O CDI efetivo fica ~0,10 p.p. abaixo da Selic-meta.
CDI_SPREAD_PP = 0.10

_cache: dict = {}


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if settings.insecure_external_ssl:
        # Só para ambientes com certificados desatualizados (ex.: Raspberry Pi antigo).
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def fetch_json(url: str, cache_key: str, ttl: int = 3600):
    now = time.time()
    hit = _cache.get(cache_key)
    if hit and now - hit['ts'] < ttl:
        return hit['data']
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; FinFam/2026)',
            'Accept': 'application/json',
        })
        with urllib.request.urlopen(req, timeout=8, context=_ssl_context()) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:
        log.warning('Falha ao buscar %s: %s', url, exc)
        return None
    for key in [k for k, v in _cache.items() if now - v['ts'] > 7200]:
        del _cache[key]
    _cache[cache_key] = {'data': data, 'ts': now}
    return data


def selic_info() -> dict:
    data = fetch_json(_SELIC_URL, 'selic')
    if data:
        try:
            return {'rate': float(data[0]['valor'].replace(',', '.')), 'date': data[0]['data'], 'is_live': True}
        except (KeyError, IndexError, ValueError) as exc:
            log.warning('Resposta inesperada do BCB: %s', exc)
    return {'rate': SELIC_FALLBACK, 'date': None, 'is_live': False}


def cdi_rate(selic: float) -> float:
    return max(round(selic - CDI_SPREAD_PP, 2), 0)


def rate_suggestions(selic: float) -> dict:
    cdi = cdi_rate(selic)
    # Poupança: 0,5% a.m. + TR quando a Selic > 8,5% a.a. (Lei 12.703/2012)
    poupanca = round(((1.005 ** 12) - 1) * 100, 2) if selic > 8.5 else round(selic * 0.70, 2)
    return {
        'Tesouro Selic': round(selic, 2),
        'Tesouro IPCA+': round(selic * 0.60, 2),
        'Tesouro Prefixado': round(selic * 0.95, 2),
        'Poupança': poupanca,
        'CDB': round(cdi, 2),
        'LCI': round(cdi * 0.87, 2),
        'LCA': round(cdi * 0.87, 2),
        'CRI/CRA': round(cdi * 0.95, 2),
        'Debêntures': round(cdi * 1.05, 2),
        'COE': round(cdi * 0.90, 2),
        'Fundo de Renda Fixa': round(cdi * 0.95, 2),
        'Fundo Multimercado': round(cdi * 1.10, 2),
        'Ações': 0, 'FIIs': 0, 'Criptomoedas': 0, 'Outros': 0,
    }


def crypto_prices(coins: str) -> dict | None:
    return fetch_json(
        f'https://api.coingecko.com/api/v3/simple/price?ids={coins}&vs_currencies=brl&include_24hr_change=true',
        f'crypto_{coins}', ttl=300)


def crypto_price(coin: str) -> float | None:
    data = crypto_prices(coin)
    if data and coin in data:
        return float(data[coin]['brl'])
    return None


def crypto_history(coin: str, days: int) -> dict | None:
    return fetch_json(
        f'https://api.coingecko.com/api/v3/coins/{coin}/market_chart?vs_currency=brl&days={days}',
        f'history_{coin}_{days}', ttl=3600)
