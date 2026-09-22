"""Display-only US closing markets. Writes only data/sector-night.json."""
import json
import math
import os
from pathlib import Path
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

MARKETS = {'sox': ('^SOX', '반도체(SOX)'), 'technology': ('XLK', '미국 기술(XLK)'), 'financial': ('XLF', '미국 금융(XLF)'), 'energy': ('XLE', '미국 에너지(XLE)'), 'industrial': ('XLI', '미국 산업재(XLI)'), 'materials': ('XLB', '미국 소재(XLB)'), 'healthcare': ('XLV', '미국 헬스케어(XLV)'), 'discretionary': ('XLY', '미국 경기소비재(XLY)'), 'staples': ('XLP', '미국 필수소비재(XLP)'), 'utilities': ('XLU', '미국 유틸리티(XLU)'), 'nasdaq': ('^IXIC', 'NASDAQ'), 'sp500': ('^GSPC', 'S&P 500')}
STOCK_MAP = {'005930': ['sox', 'technology', 'nasdaq'], '005935': ['sox', 'technology', 'nasdaq'], '000660': ['sox', 'technology', 'nasdaq'], '006400': ['technology', 'materials', 'nasdaq'], '0117V0': ['sox', 'technology', 'nasdaq'], '395160': ['sox', 'technology', 'nasdaq'], '079550': ['industrial', 'sp500'], '267270': ['industrial', 'sp500'], '443060': ['industrial', 'sp500'], '010060': ['energy', 'materials', 'sp500'], '456040': ['materials', 'energy', 'sp500'], '298020': ['materials', 'industrial', 'sp500'], '006260': ['industrial', 'utilities', 'sp500'], '028050': ['industrial', 'sp500'], '278470': ['discretionary', 'staples', 'sp500'], '161890': ['staples', 'discretionary', 'sp500'], '237690': ['healthcare', 'nasdaq'], '036620': ['discretionary', 'sp500'], '336260': ['energy', 'industrial', 'nasdaq'], '034020': ['industrial', 'utilities', 'sp500'], '222080': ['technology', 'materials', 'nasdaq'], '011500': ['materials', 'sp500'], '023160': ['industrial', 'energy', 'sp500'], '214430': ['industrial', 'technology', 'nasdaq'], '009520': ['materials', 'sp500'], '356860': ['sox', 'technology', 'nasdaq'], '373220': ['technology', 'materials', 'nasdaq'], '086520': ['materials', 'technology', 'nasdaq'], '003670': ['materials', 'technology', 'nasdaq'], '105560': ['financial', 'sp500'], '055550': ['financial', 'sp500'], '086790': ['financial', 'sp500'], '005380': ['discretionary', 'industrial', 'sp500'], '000270': ['discretionary', 'industrial', 'sp500'], '012330': ['discretionary', 'industrial', 'sp500']}
NY = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")
OUTPUT = Path(__file__).resolve().parent / "data" / "sector-night.json"


def closing_quote(history, ticker, label, now):
    """Ignore an unfinished US session, even on a manual intraday run."""
    current = now.astimezone(NY)
    rows = []
    for stamp, value in history["Close"].dropna().items():
        date = stamp.tz_convert(NY).date() if stamp.tzinfo else stamp.date()
        if date > current.date() or (date == current.date() and current.time() < time(16, 15)):
            continue
        value = float(value)
        if not math.isfinite(value) or value <= 0:
            raise ValueError("Invalid closing price")
        rows.append((date, value))
    rows.sort()
    if len(rows) < 2:
        raise ValueError("Need two completed sessions")
    (prev_date, prev), (date, last) = rows[-2:]
    if (current.date() - date).days > 7:
        raise ValueError("Stale closing prices")
    return {"ticker": ticker, "label": label, "price": round(last, 4),
            "changeRate": round((last / prev - 1) * 100, 2),
            "sessionDate": date.isoformat(), "previousSessionDate": prev_date.isoformat(), "ok": True}


def collect(fetch_history, now):
    markets = {}
    for key, (ticker, label) in MARKETS.items():
        try:
            markets[key] = closing_quote(fetch_history(ticker), ticker, label, now)
        except Exception as exc:
            print(f"{ticker}: {exc}")
            markets[key] = {"ticker": ticker, "label": label, "ok": False,
                            "changeRate": None, "sessionDate": None}
    if not any(m["ok"] for m in markets.values()):
        raise RuntimeError("All markets failed; existing output preserved")
    return {"version": "night-sector-1", "updatedAt": now.isoformat(),
            "updatedAtKST": now.astimezone(KST).strftime("%Y-%m-%d %H:%M"),
            "markets": markets, "stockMap": STOCK_MAP}


def write_output(data):
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix(".json.tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        os.replace(temporary, OUTPUT)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    import yfinance as yf
    now = datetime.now(KST)
    def fetch_history(ticker):
        return yf.Ticker(ticker).history(period="1mo", interval="1d", auto_adjust=False, prepost=False, timeout=20)
    data = collect(fetch_history, now)
    write_output(data)
    print(f"Collected {sum(m['ok'] for m in data['markets'].values())}/{len(MARKETS)} markets")


if __name__ == "__main__":
    main()
