#!/usr/bin/env python3
"""
Aestima GLI Chart Engine
========================
Macro / regime chart generation for Substack articles and intelligence dashboard.
Dark theme, amber accent, PNG output via matplotlib only.

Usage:
    source ~/remi-intelligence/.venv/bin/activate
    python -m scripts.chart_engine              # generate all charts
    python -c "from scripts.chart_engine import chart_cross_asset_ratio; print(chart_cross_asset_ratio('SILJ','SLV'))"
"""

from __future__ import annotations

import json
import os
import sys
import warnings
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
import yfinance as yf

try:
    import pandas_datareader.data as web
except Exception:
    web = None

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

OUTPUT_DIR = Path("/docker/obsidian/investing/Intelligence/Publishing/charts")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Aestima brand palette — teal primary, dark navy background
BG_COLOR = "#0A0E1A"
BG_AX_COLOR = "#111827"
TEAL = "#00D4AA"
TEAL_DIM = "#009977"
TEAL_GLOW = "#00FFCC"
BLUE_ACCENT = "#3B82F6"
CORAL = "#FF6B6B"
GOLD = "#F5A623"
WHITE = "#F9FAFB"
LIGHT_GRAY = "#D1D5DB"
MID_GRAY = "#6B7280"
GRID_COLOR = "#1F2937"
RED = "#EF4444"
GREEN = "#00D4AA"

plt.rcParams.update({
    "figure.facecolor": BG_COLOR,
    "axes.facecolor": BG_AX_COLOR,
    "axes.edgecolor": "#374151",
    "axes.labelcolor": LIGHT_GRAY,
    "text.color": WHITE,
    "xtick.color": LIGHT_GRAY,
    "ytick.color": LIGHT_GRAY,
    "grid.color": GRID_COLOR,
    "grid.linestyle": "-",
    "grid.linewidth": 0.3,
    "font.family": "sans-serif",
    "font.size": 11,
    "axes.titlesize": 15,
    "axes.labelsize": 12,
})

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _watermark(ax: plt.Axes) -> None:
    """Place 'Aestima' watermark bottom-right."""
    ax.text(
        0.98, 0.02, "Aestima",
        transform=ax.transAxes,
        fontsize=9, color=TEAL_DIM, alpha=0.5,
        ha="right", va="bottom", fontweight="bold",
    )


def _format_date_axis(ax: plt.Axes, which: str = "x") -> None:
    axis = ax.xaxis if which == "x" else ax.yaxis
    axis.set_major_formatter(mdates.DateFormatter("%b %y"))
    axis.set_major_locator(mdates.MonthLocator(interval=3))
    if which == "x":
        ax.tick_params(axis="x", rotation=45)


def _title_with_value(title: str, current_val: Optional[float], suffix: str = "") -> str:
    base = title
    if current_val is not None:
        base += f"  |  Now: {current_val:.2f}"
    if suffix:
        base += f"  {suffix}"
    return base


def _save_fig(fig: plt.Figure, name: str) -> str:
    path = OUTPUT_DIR / f"{name}.png"
    fig.savefig(str(path), dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return str(path)


def _fetch_yf(ticker: str, period: str = "2y") -> pd.DataFrame:
    """Download adjusted close from yfinance, return DataFrame with Date index."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = yf.download(ticker, period=period, progress=False, auto_adjust=True)
    if df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        # yfinance sometimes returns MultiIndex columns with single ticker
        df.columns = df.columns.get_level_values(0)
    return df


def _fetch_fred(series: str, start: datetime, end: datetime) -> pd.Series:
    """Fetch a FRED series via pandas_datareader or direct FRED API."""
    # Try pandas_datareader first
    if web is not None:
        try:
            s = web.DataReader(series, "fred", start, end)
            if isinstance(s, pd.DataFrame):
                s = s.iloc[:, 0]
            return s.dropna()
        except Exception:
            pass

    # Fallback: direct FRED API (public, no key needed for basic queries)
    try:
        import requests
        url = (
            f"https://fred.stlouisfed.org/graph/fredgraph.csv"
            f"?id={series}&cosd={start.strftime('%Y-%m-%d')}&coed={end.strftime('%Y-%m-%d')}"
        )
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200 and resp.text.strip():
            from io import StringIO
            df = pd.read_csv(StringIO(resp.text), index_col=0, parse_dates=True)
            if not df.empty:
                s = df.iloc[:, 0]
                s = pd.to_numeric(s, errors="coerce")
                return s.dropna()
    except Exception as exc:
        print(f"[chart_engine] FRED direct fetch failed for {series}: {exc}")

    return pd.Series(dtype=float)


# ---------------------------------------------------------------------------
# Chart: Yield Curve Spreads (2s10s, 10s3m)
# ---------------------------------------------------------------------------

def chart_yield_curve() -> str:
    """2y10y and 10y3m Treasury spreads from FRED."""
    end = datetime.now()
    start = end - timedelta(days=730)

    t10y2y = _fetch_fred("T10Y2Y", start, end)
    t10y3m = _fetch_fred("T10Y3M", start, end)

    if t10y2y.empty and t10y3m.empty:
        print("[chart_engine] No yield curve data available.")
        return ""

    fig, ax = plt.subplots(figsize=(12, 7))

    # Recession shading - NBER recessions (approximate via FRED USRECM)
    try:
        rec = _fetch_fred("USRECM", start, end)
        if not rec.empty:
            for date_val, val in rec.items():
                if val == 1:
                    ax.axvspan(date_val, date_val + timedelta(days=31),
                               color=CORAL, alpha=0.08)
    except Exception:
        pass

    if not t10y2y.empty:
        cur_2s10s = t10y2y.iloc[-1]
        ax.plot(t10y2y.index, t10y2y.values, color=TEAL, linewidth=1.5,
                label=f"10Y-2Y ({cur_2s10s:.2f}%)")
        ax.scatter([t10y2y.index[-1]], [cur_2s10s], color=TEAL, s=60, zorder=5)
    else:
        cur_2s10s = None

    if not t10y3m.empty:
        cur_10s3m = t10y3m.iloc[-1]
        ax.plot(t10y3m.index, t10y3m.values, color=BLUE_ACCENT, linewidth=1.5,
                label=f"10Y-3M ({cur_10s3m:.2f}%)")
        ax.scatter([t10y3m.index[-1]], [cur_10s3m], color=BLUE_ACCENT, s=60, zorder=5)
    else:
        cur_10s3m = None

    ax.axhline(0, color=CORAL, linewidth=0.8, linestyle="--", alpha=0.7, label="Inversion threshold")

    ax.set_title(_title_with_value("Yield Curve Spreads", cur_2s10s, "(% points)"))
    ax.set_ylabel("Spread (%)")
    ax.legend(loc="upper left", fontsize=10, facecolor=BG_AX_COLOR, edgecolor="#374151",
              labelcolor=LIGHT_GRAY)
    ax.grid(True, alpha=0.3)
    _format_date_axis(ax)
    _watermark(ax)

    return _save_fig(fig, "yield_curve_spreads")


# ---------------------------------------------------------------------------
# Chart: HY / IG Spreads (proxy via HYG / LQD)
# ---------------------------------------------------------------------------

def chart_hy_ig_spreads() -> str:
    """HY and IG spread proxies via HYG and LQD ETFs."""
    hyg = _fetch_yf("HYG", period="2y")
    lqd = _fetch_yf("LQD", period="2y")

    if hyg.empty or lqd.empty:
        print("[chart_engine] No HYG/LQD data available.")
        return ""

    # Compute rolling spread proxy: inverse price as yield proxy (lower price = wider spread)
    # More useful: compute the rate-of-change / drawdown from recent high as a spread proxy
    hyg_price = hyg["Close"]
    lqd_price = lqd["Close"]

    # Align dates
    common_idx = hyg_price.index.intersection(lqd_price.index)
    hyg_price = hyg_price.loc[common_idx]
    lqd_price = lqd_price.loc[common_idx]

    # Compute relative performance (drawdown from 252-day high as spread proxy)
    hyg_drawdown = (hyg_price / hyg_price.rolling(252, min_periods=20).max() - 1) * 1000  # bps
    lqd_drawdown = (lqd_price / lqd_price.rolling(252, min_periods=20).max() - 1) * 1000

    fig, ax = plt.subplots(figsize=(12, 7))

    cur_hyg = hyg_drawdown.iloc[-1]
    cur_lqd = lqd_drawdown.iloc[-1]

    ax.plot(hyg_drawdown.index, hyg_drawdown.values, color=TEAL, linewidth=1.5,
            label=f"HY Proxy (HYG): {cur_hyg:.0f}bps")
    ax.plot(lqd_drawdown.index, lqd_drawdown.values, color=BLUE_ACCENT, linewidth=1.5,
            label=f"IG Proxy (LQD): {cur_lqd:.0f}bps")

    ax.axhline(-300, color=CORAL, linewidth=1, linestyle="--", alpha=0.7,
               label="HY Stress (~300bps)")

    ax.scatter([hyg_drawdown.index[-1]], [cur_hyg], color=TEAL, s=60, zorder=5)
    ax.scatter([lqd_drawdown.index[-1]], [cur_lqd], color=BLUE_ACCENT, s=60, zorder=5)

    ax.set_title(_title_with_value("HY / IG Credit Spread Proxy", cur_hyg, "(drawdown bps from 1y high)"))
    ax.set_ylabel("Spread Proxy (bps)")
    ax.legend(loc="lower left", fontsize=10, facecolor=BG_AX_COLOR, edgecolor="#374151",
              labelcolor=LIGHT_GRAY)
    ax.grid(True, alpha=0.3)
    _format_date_axis(ax)
    _watermark(ax)

    return _save_fig(fig, "hy_ig_spreads")


# ---------------------------------------------------------------------------
# Chart: GLI Phase Timeline
# ---------------------------------------------------------------------------

def chart_gli_phase_timeline() -> str:
    """Current GLI phase indicator from Aestima API."""
    api_key = os.getenv("AESTIMA_AGENT_KEY", "")
    api_base = os.getenv("AESTIMA_API_BASE", "https://aestima.ai")

    phase_name = "Unknown"
    phase_score = None
    phase_details = {}

    if api_key:
        try:
            import subprocess
            # Refresh GLI
            subprocess.run(
                ["curl", "-s", "-X", "POST", "-H", f"X-Agent-Key: {api_key}",
                 f"{api_base}/api/gli/refresh"],
                capture_output=True, timeout=30,
            )
            # Fetch context
            result = subprocess.run(
                ["curl", "-s", "-H", f"X-Agent-Key: {api_key}",
                 f"{api_base}/api/agent/context"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                ctx = json.loads(result.stdout)
                # Try to extract GLI phase info from the context
                gli = ctx.get("gli", ctx.get("gli_phase", ctx.get("regime", {})))
                if isinstance(gli, dict):
                    phase_name = gli.get("phase", gli.get("name", phase_name))
                    phase_score = gli.get("score", gli.get("value"))
                    phase_details = gli
                elif isinstance(gli, str):
                    phase_name = gli
        except Exception as exc:
            print(f"[chart_engine] GLI API error: {exc}")

    # Build a stylized phase indicator chart
    phases = ["Expansion", "Peak", "Slowdown", "Contraction"]
    phase_colors = [GREEN, TEAL, GOLD, CORAL]

    try:
        idx = phases.index(phase_name) if phase_name in phases else -1
    except ValueError:
        idx = -1

    fig, ax = plt.subplots(figsize=(12, 5))

    # Draw phase bar
    bar_y = 0.5
    for i, (ph, col) in enumerate(zip(phases, phase_colors)):
        x_start = i
        alpha = 1.0 if i == idx else 0.25
        ax.barh(bar_y, 1, left=x_start, height=0.6, color=col, alpha=alpha,
                edgecolor=BG_COLOR, linewidth=2)
        ax.text(x_start + 0.5, bar_y, ph, ha="center", va="center",
                fontsize=13, fontweight="bold" if i == idx else "normal",
                color=WHITE if i == idx else MID_GRAY)

    # Arrow indicator
    if idx >= 0:
        arrow_x = idx + 0.5
        ax.annotate("▼", xy=(arrow_x, bar_y + 0.45), fontsize=18,
                     ha="center", va="bottom", color=phase_colors[idx])

    # Score display
    score_text = f"GLI Score: {phase_score:.2f}" if phase_score is not None else "GLI Score: N/A"
    ax.text(0.5, -0.15, f"Current Phase: {phase_name}  |  {score_text}",
            transform=ax.transAxes, ha="center", fontsize=13, color=TEAL)

    # Phase details if available
    if phase_details:
        detail_lines = [f"{k}: {v}" for k, v in phase_details.items()
                        if k not in ("phase", "name", "score", "value") and isinstance(v, (str, int, float))]
        if detail_lines:
            ax.text(0.5, -0.28, "  |  ".join(detail_lines[:4]),
                    transform=ax.transAxes, ha="center", fontsize=10, color=MID_GRAY)

    ax.set_xlim(-0.1, len(phases) - 0.9)
    ax.set_ylim(-0.3, 1.3)
    ax.set_title(f"GLI Regime Phase  |  {datetime.now().strftime('%B %Y')}", color=WHITE)
    ax.axis("off")
    _watermark(ax)

    return _save_fig(fig, "gli_phase_timeline")


# ---------------------------------------------------------------------------
# Chart: PPI / CPI Trajectory
# ---------------------------------------------------------------------------

def chart_ppi_cpi_trajectory() -> str:
    """PPI and CPI YoY% from FRED."""
    end = datetime.now()
    start = end - timedelta(days=730)

    cpi = _fetch_fred("CPIAUCSL", start, end)
    ppi = _fetch_fred("PPIACO", start, end)

    if cpi.empty and ppi.empty:
        print("[chart_engine] No CPI/PPI data available.")
        return ""

    # YoY % change
    cpi_yoy = cpi.pct_change(12) * 100 if len(cpi) > 12 else pd.Series(dtype=float)
    ppi_yoy = ppi.pct_change(12) * 100 if len(ppi) > 12 else pd.Series(dtype=float)

    fig, ax = plt.subplots(figsize=(12, 7))

    cur_cpi = cpi_yoy.iloc[-1] if not cpi_yoy.empty else None
    cur_ppi = ppi_yoy.iloc[-1] if not ppi_yoy.empty else None

    if not cpi_yoy.empty:
        ax.plot(cpi_yoy.index, cpi_yoy.values, color=TEAL, linewidth=1.8,
                label=f"CPI YoY ({cur_cpi:.1f}%)" if cur_cpi is not None else "CPI YoY")
        if cur_cpi is not None:
            ax.scatter([cpi_yoy.index[-1]], [cur_cpi], color=TEAL, s=60, zorder=5)

    if not ppi_yoy.empty:
        ax.plot(ppi_yoy.index, ppi_yoy.values, color=BLUE_ACCENT, linewidth=1.8,
                label=f"PPI YoY ({cur_ppi:.1f}%)" if cur_ppi is not None else "PPI YoY")
        if cur_ppi is not None:
            ax.scatter([ppi_yoy.index[-1]], [cur_ppi], color=BLUE_ACCENT, s=60, zorder=5)

    ax.axhline(2, color=GREEN, linewidth=0.8, linestyle=":", alpha=0.5, label="Fed 2% target")

    ax.set_title(_title_with_value("PPI / CPI Trajectory", cur_cpi, "(YoY %)"))
    ax.set_ylabel("Year-over-Year (%)")
    ax.legend(loc="upper left", fontsize=10, facecolor=BG_AX_COLOR, edgecolor="#374151",
              labelcolor=LIGHT_GRAY)
    ax.grid(True, alpha=0.3)
    _format_date_axis(ax)
    _watermark(ax)

    return _save_fig(fig, "ppi_cpi_trajectory")


# ---------------------------------------------------------------------------
# Chart: NFCI (National Financial Conditions Index)
# ---------------------------------------------------------------------------

def chart_nfici() -> str:
    """NFCI from FRED — positive = tight, negative = loose."""
    end = datetime.now()
    start = end - timedelta(days=730)

    nfci = _fetch_fred("NFCI", start, end)

    if nfci.empty:
        print("[chart_engine] No NFCI data available.")
        return ""

    fig, ax = plt.subplots(figsize=(12, 7))

    cur_val = nfci.iloc[-1]

    # Color fill: positive = red (tight), negative = green (loose)
    ax.fill_between(nfci.index, nfci.values, 0,
                     where=nfci.values >= 0, color=CORAL, alpha=0.15, interpolate=True)
    ax.fill_between(nfci.index, nfci.values, 0,
                     where=nfci.values < 0, color=GREEN, alpha=0.15, interpolate=True)
    ax.plot(nfci.index, nfci.values, color=TEAL, linewidth=1.8)

    ax.axhline(0, color=WHITE, linewidth=0.6, linestyle="--", alpha=0.5)
    ax.scatter([nfci.index[-1]], [cur_val], color=TEAL, s=60, zorder=5)

    ax.text(0.02, 0.95, "Tight (stress)", transform=ax.transAxes, fontsize=9,
            color=CORAL, alpha=0.7, va="top")
    ax.text(0.02, 0.05, "Loose (accommodative)", transform=ax.transAxes, fontsize=9,
            color=GREEN, alpha=0.7, va="bottom")

    ax.set_title(_title_with_value("National Financial Conditions Index (NFCI)", cur_val))
    ax.set_ylabel("NFCI (std dev from mean)")
    ax.grid(True, alpha=0.3)
    _format_date_axis(ax)
    _watermark(ax)

    return _save_fig(fig, "nfici")


# ---------------------------------------------------------------------------
# Chart: Oil / Gold Overlay
# ---------------------------------------------------------------------------

def chart_oil_gold() -> str:
    """WTI crude oil and gold, dual axis, 1 year."""
    oil = _fetch_yf("CL=F", period="1y")
    gold = _fetch_yf("GC=F", period="1y")

    if oil.empty and gold.empty:
        print("[chart_engine] No oil/gold data available.")
        return ""

    fig, ax1 = plt.subplots(figsize=(12, 7))

    if not oil.empty:
        oil_close = oil["Close"]
        cur_oil = oil_close.iloc[-1]
        ax1.plot(oil_close.index, oil_close.values, color=TEAL, linewidth=1.5,
                 label=f"WTI Crude (${cur_oil:.2f})")
        ax1.scatter([oil_close.index[-1]], [cur_oil], color=TEAL, s=60, zorder=5)
        ax1.set_ylabel("WTI Crude (USD)", color=TEAL)
        ax1.tick_params(axis="y", labelcolor=TEAL)
    else:
        cur_oil = None

    ax2 = ax1.twinx()
    if not gold.empty:
        gold_close = gold["Close"]
        cur_gold = gold_close.iloc[-1]
        ax2.plot(gold_close.index, gold_close.values, color=BLUE_ACCENT, linewidth=1.5,
                 label=f"Gold (${cur_gold:.2f})")
        ax2.scatter([gold_close.index[-1]], [cur_gold], color=BLUE_ACCENT, s=60, zorder=5)
        ax2.set_ylabel("Gold (USD)", color=BLUE_ACCENT)
        ax2.tick_params(axis="y", labelcolor=BLUE_ACCENT)
    else:
        cur_gold = None

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left",
               fontsize=10, facecolor=BG_AX_COLOR, edgecolor="#374151", labelcolor=LIGHT_GRAY)

    title_val = f"Oil: ${cur_oil:.0f}  Gold: ${cur_gold:.0f}" if cur_oil and cur_gold else "Oil & Gold"
    ax1.set_title(f"Crude Oil vs Gold  |  {title_val}")
    ax1.grid(True, alpha=0.3)
    _format_date_axis(ax1)
    _watermark(ax2)

    return _save_fig(fig, "oil_gold_overlay")


# ---------------------------------------------------------------------------
# Chart: Cross-Asset Ratio
# ---------------------------------------------------------------------------

def chart_cross_asset_ratio(ticker1: str, ticker2: str, period: str = "1y") -> str:
    """
    Generic ratio chart: ticker1 / ticker2.
    Examples: SILJ/SLV, TLT/SPX, HYG/LQD.
    """
    df1 = _fetch_yf(ticker1, period=period)
    df2 = _fetch_yf(ticker2, period=period)

    if df1.empty or df2.empty:
        print(f"[chart_engine] No data for {ticker1} or {ticker2}.")
        return ""

    p1 = df1["Close"]
    p2 = df2["Close"]

    common = p1.index.intersection(p2.index)
    if len(common) < 5:
        print(f"[chart_engine] Not enough overlapping data for {ticker1}/{ticker2}.")
        return ""

    p1 = p1.loc[common]
    p2 = p2.loc[common]
    ratio = p1 / p2

    fig, ax = plt.subplots(figsize=(12, 7))

    cur_val = ratio.iloc[-1]
    mean_val = ratio.mean()

    ax.plot(ratio.index, ratio.values, color=TEAL, linewidth=1.8,
            label=f"{ticker1}/{ticker2}")
    ax.axhline(mean_val, color=MID_GRAY, linewidth=1, linestyle="--", alpha=0.6,
               label=f"Mean: {mean_val:.4f}")
    ax.scatter([ratio.index[-1]], [cur_val], color=TEAL, s=70, zorder=5,
               edgecolors=WHITE, linewidths=1.5)

    # Shading above/below mean
    ax.fill_between(ratio.index, ratio.values, mean_val,
                     where=ratio.values >= mean_val, color=GREEN, alpha=0.08, interpolate=True)
    ax.fill_between(ratio.index, ratio.values, mean_val,
                     where=ratio.values < mean_val, color=CORAL, alpha=0.08, interpolate=True)

    start_str = ratio.index[0].strftime("%b %Y")
    end_str = ratio.index[-1].strftime("%b %Y")
    ax.set_title(f"{ticker1}/{ticker2} Ratio  |  {start_str} – {end_str}  |  Current: {cur_val:.4f}")
    ax.set_ylabel(f"{ticker1} / {ticker2}")
    ax.legend(loc="upper left", fontsize=10, facecolor=BG_AX_COLOR, edgecolor="#374151",
              labelcolor=LIGHT_GRAY)
    ax.grid(True, alpha=0.3)
    _format_date_axis(ax)
    _watermark(ax)

    return _save_fig(fig, f"ratio_{ticker1}_{ticker2}".lower())


# ---------------------------------------------------------------------------
# Main: generate all charts
# ---------------------------------------------------------------------------

def generate_all() -> list[str]:
    """Generate all charts and return list of file paths."""
    results = []
    generators = [
        ("Yield Curve", chart_yield_curve),
        ("HY/IG Spreads", chart_hy_ig_spreads),
        ("GLI Phase", chart_gli_phase_timeline),
        ("PPI/CPI", chart_ppi_cpi_trajectory),
        ("NFCI", chart_nfici),
        ("Oil/Gold", chart_oil_gold),
    ]
    for name, func in generators:
        try:
            print(f"[chart_engine] Generating {name}...")
            path = func()
            if path:
                results.append(path)
                print(f"  -> {path}")
            else:
                print(f"  -> SKIPPED (no data)")
        except Exception as exc:
            print(f"  -> ERROR: {exc}")

    # Cross-asset ratios
    for t1, t2 in [("SILJ", "SLV"), ("TLT", "SPY"), ("HYG", "LQD")]:
        try:
            print(f"[chart_engine] Generating ratio {t1}/{t2}...")
            path = chart_cross_asset_ratio(t1, t2)
            if path:
                results.append(path)
                print(f"  -> {path}")
        except Exception as exc:
            print(f"  -> ERROR: {exc}")

    print(f"\n[chart_engine] Done. {len(results)} charts generated.")
    return results


if __name__ == "__main__":
    paths = generate_all()
    for p in paths:
        print(p)
