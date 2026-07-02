# Remi — CROO CAP Provider Agent

**A macro-intelligence provider agent for the [CROO Agent Protocol (CAP)](https://croo.network).**
Remi lists paid, callable macro-analysis services on CROO and fulfills them autonomously:
it connects over WebSocket, accepts negotiations, runs analysis on payment, and delivers
a formatted report back to the buyer — the full **negotiate → pay → deliver** lifecycle.

> This repository is the CAP provider surface of a larger, self-hosted macro & clinical
> intelligence system. The provider loop here is real and runnable; the data-fetch helpers
> ship as clearly-marked **sample-output stubs** that are wired to the live Remi pipeline in
> production (see [Architecture](#architecture-real-vs-sample)).

---

## Services offered

| Service | What the buyer receives |
|---|---|
| **Macro Regime Snapshot** | Current global-liquidity phase, Steno regime label, fiscal-dominance read, credit-spread / yield-curve / NFCI stress signals, and strategic takeaways |
| **Ticker Deep Dive** | Price & valuation (Mayer Multiple, P/E, growth), signal-conviction breakdown, catalysts, and an LLM-synthesized strategic assessment |
| **Sentiment Scan** | Bull/bear/neutral signal distribution for a ticker plus sector-velocity context |
| **Sentiment + Deep Dive** (premium) | The deep dive, cross-validated against tracked sentiment flow and sector context |
| **Weekly Macro Report** | Regime positioning, week-ahead triggers, and a positioning framework |

Everything is contextualized against a live macro-regime read (global-liquidity phase +
Steno regime label). Free long-form research is published to
[Remi and the Bogwizard](https://aestimaai.substack.com/) on Substack.

---

## What it is

Remi is the autonomous intelligence layer behind [Aestima](https://aestima.ai), a macro
intelligence platform. A continuous pipeline ingests macro sources, extracts themes and
signals, scores how fast each theme is accelerating, detects cross-ticker convergence, and
delivers regime-stamped analysis. This repo exposes that analysis as **agent-to-agent paid
services** over CROO's on-chain payment rails.

- **Sell** — macro regime snapshots, ticker reads, deep dives, and weekly briefings, priced
  and delivered per-order through CAP.
- **Publishing** — an autonomous macro thread pipeline posts to X as
  [@BogWizard_agent](https://x.com/BogWizard_agent), with a human-in-the-loop approval gate.

---

## Setup

**Requirements:** Python 3.11+, the CROO Agent Protocol SDK, and a CROO agent with services listed.

```bash
git clone https://github.com/mgwiazd1/remi-agent
cd remi-agent
pip install -r requirements.txt
```

Configure via environment variables (copy into a local `.env` — it is git-ignored, never commit it):

| Variable | Purpose |
|---|---|
| `CROO_SDK_KEY` | Your CROO agent SDK key (required) |
| `CROO_AGENT_ID` | Your CROO agent UUID — used to load the service map (required for live orders) |
| `CROO_API_URL` | CROO REST base URL (default `https://api.croo.network`) |
| `CROO_WS_URL` | CROO WebSocket URL (default `wss://api.croo.network/ws`) |
| `CROO_RPC_URL` | Base-network RPC URL (default `https://mainnet.base.org`) |
| `SUBSTACK_URL` | Optional: your public research URL, appended to delivered reports |

**Run the provider (live on CROO):**

```bash
python provider.py
```

**Try it offline (no CROO connection needed)** — runs the fulfill → format path against the
sample stubs and prints a report for each service:

```bash
python provider.py --demo
```

---

## SDK methods used

`connect_websocket`, `accept_negotiation`, `get_negotiation`, `get_order`, `list_orders`, `deliver_order`.

*Available in the CAP flow but not exercised by this text-delivery provider:*
`reject_negotiation` (this provider auto-accepts every negotiation) and `upload_file`
(deliveries use `DeliverableType.TEXT`, not file attachments).

---

## CAP integration notes

- **Connect** — `main()` builds a `Config(base_url, ws_url, rpc_url)`, instantiates
  `AgentClient`, loads the service map from CROO's public agent API, then opens the event
  stream with `connect_websocket()`.
- **Negotiate** — on `order_negotiation_created`, `handle_negotiation()` fetches the
  negotiation to read the buyer's `requirements`, caches them, and calls
  `accept_negotiation()`.
- **Fulfill on payment** — on `order_paid`, `handle_order_paid()` resolves the order and its
  cached requirements into a query, runs `fulfill_service()`, formats the result, and
  **delivers** via `deliver_order()` with a retry loop (CROO can need a few seconds to
  release the order lock after payment).
- **Delivery hygiene** — report text is transliterated to ASCII before delivery (the delivery
  endpoint rejects some non-ASCII), and requirements are parsed defensively from JSON or raw
  text.
- **Lifecycle logging** — `order_completed`, `order_rejected`, and `order_expired` events are
  logged for observability.

---

## Architecture: real vs sample

- **Real & runnable:** the entire CAP lifecycle — SDK calls, event handling,
  negotiation/order flow, ASCII-safe delivery with retries, and the report formatters.
- **Sample-output stubs:** the data-fetch helpers (`_get_aestima_context`,
  `_build_ticker_analysis`, `_get_market_data`, `_get_db_sentiment`, `_get_sector_velocity`,
  `_get_llm_analysis[_premium]`). In production these are wired to the live Remi pipeline
  (Aestima macro-regime feed, a SQLite signal store, market data, DeMark timing, and an LLM
  synthesis layer). Here they return realistic, correctly-shaped fixtures so a fresh clone
  delivers a plausible report end-to-end. Each stub is marked `SAMPLE OUTPUT`; swap the
  bodies for the live pipeline calls to go into production — the CAP flow is unchanged.

---

## License

MIT — see [LICENSE](LICENSE).
