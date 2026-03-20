# Remi

> A self-sovereign AI agent operating at the bleeding edge of two domains — clinical medicine and macro finance.

---

## What is Remi?

Remi is a fully self-hosted, autonomous AI agent running on a personal Proxmox homelab. He serves a pulmonary and critical care physician as both a clinical second brain and a macro financial intelligence system — deployed entirely without dependence on centralized AI platforms.

He is not a chatbot. He is infrastructure.

---

## The Problem

A physician-investor operating at the intersection of critical care medicine and macro finance faces a unique information problem:

- **Clinical:** Evidence-based medicine moves fast. Guidelines update. Edge cases do not fit textbooks. Mid-shift, there is no time to search PubMed. OpenEvidence gives generic answers. No tool remembers your patient's evolving clinical picture.
- **Financial:** The macro signal-to-noise ratio is catastrophic. Tier-1 research is paywalled. The crowd prices first-order effects immediately. Second and third-order supply chain implications — the ones that actually generate alpha — require synthesis across geopolitics, commodities, and liquidity regimes simultaneously.

The same cognitive architecture that traces a pathogen through inflammatory cascades to differential diagnoses also traces a fertilizer supply disruption through ammonia feedstock to crop margin compression. Second-order reasoning is domain-agnostic.

One sovereign agent. Two languages. No platform lock-in.

---

## Architecture
```
Proxmox Homelab (gwiz) — i7-8700K, 62GB RAM, 500GB NVMe
└── Deb-Docs-Media VM (192.168.1.100)
    ├── Hermes Agent Gateway (systemd user service)
    │   ├── Clinical Telegram Chat — vault queries, case notes, inbox
    │   └── Investing Telegram Chat — signals, Pablo collaboration
    ├── Narrative Intelligence Pipeline (systemd user service)
    │   ├── RSS Poller — 8 feeds, every 6h
    │   ├── Extraction Worker — Haiku themes, Sonnet second-order — every 4h
    │   ├── GLI Stamper — live Aestima regime context at ingestion
    │   ├── Velocity Scorer — theme acceleration tracking
    │   └── Obsidian Writer — structured .md notes with frontmatter
    ├── Signal Listener (systemd user service)
    │   └── Telethon userbot — Telegram group monitoring
    ├── Scheduled Jobs (cron)
    │   ├── 7am — autonomous signal digest to Telegram
    │   ├── Every 2min — inbox PDF watcher
    │   ├── Every 30min — clinical inbox auto-processor
    │   └── 3am — NAS vault backup via rsync
    ├── Obsidian Clinical Vault (CouchDB LiveSync)
    │   └── 400MB+, 19 PCCM domains, 135 wiki-links, 4-device sync
    └── Obsidian Investing Vault
        └── SQLite narrative intelligence DB + structured theme notes
```

---

## Core Capabilities

### Clinical Intelligence
- Vault queries mid-shift via Telegram — ARDS, VAP, bronchoscopy, ECMO management
- Evidence-based differential generation conditioned on evolving clinical picture
- Knows what it does not know — identifies missing evidence and asks for it
- Inbox pipeline — drop a PDF, Remi extracts findings, creates structured note, syncs to all devices in 60 seconds
- Clinical knowledge graph — 135 wiki-links across 19 PCCM domains

### Macro Financial Intelligence
- GLI-conditioned analysis — every document stamped with live Global Liquidity Index phase, Steno regime, fiscal dominance score
- Narrative velocity scoring — tracks theme acceleration across Tier-1 sources
- Second-order inference — surfaces supply chain implications before mainstream financial media
- Autonomous 7am signal digest from monitored Telegram groups
- 8-feed RSS pipeline, theme-classified, written to structured Obsidian investing vault notes

### Agent Autonomy
- Fully self-hosted — Proxmox homelab, zero SaaS dependencies for core functionality
- Persistent across reboots — systemd user services with linger
- Separate API keys per workstream — clinical and investing costs isolated and attributable
- On-chain identity via ERC-8004 on Base Mainnet
- x402-ready architecture — GLI data product designed for machine-to-machine micropayment delivery

---

## Stack

| Component | Technology |
|---|---|
| Agent Framework | Hermes Agent (Nous Research) |
| Primary Model | Claude Haiku 4.5 (default) / Sonnet 4.6 (second-order inference) |
| Telegram Interface | Hermes Gateway + Telethon userbot |
| Clinical Vault | Obsidian + CouchDB LiveSync |
| Investing Vault | Obsidian + SQLite narrative intelligence DB |
| Narrative Pipeline | APScheduler + feedparser + Claude API |
| GLI Integration | Aestima.ai (self-hosted) via agent service token |
| Infrastructure | Proxmox, Debian VMs, Docker Compose, systemd |
| External Access | Cloudflare Tunnel |
| On-Chain Identity | ERC-8004 on Base Mainnet |

---

## Repository Structure
```
remi-agent/
├── remi-intelligence/           # Narrative Intelligence Pipeline
│   ├── src/
│   │   ├── main.py              # Scheduler — RSS 6h, extraction 4h, report 7am
│   │   ├── rss_poller.py        # 8-feed RSS ingestion with content hash dedup
│   │   ├── extraction_worker.py # Document queue processor
│   │   ├── llm_extractor.py     # Haiku extraction, Sonnet second-order inference
│   │   ├── gli_stamper.py       # Aestima GLI regime stamp at ingestion time
│   │   ├── obsidian_writer.py   # Structured .md note generation with frontmatter
│   │   ├── velocity_scorer.py   # Theme velocity scoring (0-100, 7-day half-life decay)
│   │   └── db/schema.py         # SQLite schema
│   └── config/
│       ├── rss_feeds.json       # Tier-1/2 feed watchlist with weights
│       └── account_taxonomy.json # X account watchlist (Phase 2)
├── scripts/
│   ├── signal-digest.sh         # 7am autonomous signal brief
│   ├── inbox-autoprocess.sh     # 8h clinical PDF auto-processor
│   └── inbox-watcher.sh         # PDF arrival notifier
├── listeners/
│   └── group-listener.py        # Telethon userbot — Telegram signal monitoring
├── vault/
│   ├── CLAUDE.md                # Vault instructions + inbox pipeline
│   └── SKILL-GRAPH.md           # Clinical knowledge map (19 PCCM domains)
├── soul/
│   └── SOUL.md                  # Remi persona and values
└── README.md
```

---

## How It Works — Clinical
```
Physician: "What does my vault say about anticoagulation before bronchoscopy on ECMO?"

Telegram message
  → Hermes gateway
    → Claude searches Obsidian vault markdown files
      → Synthesizes across PCCM notes, UWorld cases, attached studies
        → Returns evidence-based summary with source links
          → If evidence thin: "I need more on X — drop a paper in the inbox"

Total time: ~8 seconds. Mid-shift. No browser. No login.
```

---

## How It Works — Macro
```
Steno Research publishes a new regime analysis

RSS poller ingests (every 6h)
  → Extraction worker processes (every 4h)
    → GLI stamp attached: TURBULENCE / Regime 2 / Fiscal 7.9/10
      → Haiku extracts themes, facts, opinions
        → Sonnet runs second-order supply chain inference on flagged themes
          → Velocity score computed
            → Obsidian investing vault note written
              → Telegram alert if velocity threshold crossed

Result: "Fertilizer Supply Disruption — velocity 23.4 (+12.1 vs 7d)
  Second-order: Ammonia feedstock (CF, NTR)
  Third-order: Crop margin compression, food inflation lag 90d
  [T1 first-mover window — low narrative saturation]"
```

---

## Thesis

The infrastructure underneath autonomous agents determines whether they can be trusted. Remi runs on hardware you own, software you control, and keys you hold. His clinical knowledge never leaves your server. His investing signals are conditioned on your own GLI engine. His on-chain identity is yours.

This is what sovereign AI infrastructure looks like for a single person.

---

## Roadmap

- **Phase 1 (Live):** Clinical vault queries, narrative intelligence pipeline, signal digest
- **Phase 2:** X account monitoring via twitter-cli (no API cost), BogWizard integration
- **Phase 3:** GLI data product via x402 micropayments — machine-to-machine delivery to quant funds

---

## Built With

- [Hermes Agent](https://github.com/nous-research/hermes) — Nous Research
- [Anthropic Claude](https://anthropic.com) — Haiku 4.5 + Sonnet 4.6
- [Aestima.ai](https://aestima.ai) — GLI engine
- [Obsidian](https://obsidian.md) + [LiveSync](https://github.com/vrtmrz/obsidian-livesync)
- [Telethon](https://github.com/LonamiWebs/Telethon)

---

*Remi. A man of many hats. Built on a homelab. Running at the bleeding edge.*

## GLI Integration — Aestima Platform

Remi's narrative intelligence pipeline is conditioned by live macro regime data from **Aestima** — a separate, custom-built multi-asset sentiment intelligence platform running on the same Proxmox host.

At every document ingestion, Remi calls Aestima's `/api/agent/context` endpoint to stamp the document with:
- **GLI Phase** — TURBULENCE / CALM / EXPANSION / TROUGH / SPECULATION
- **Steno 8-regime label** — e.g. "Goldilocks with Pressure"
- **Fiscal dominance score** — 0–10
- **Transition risk score** — 0–10

This means every investment theme note in the Obsidian vault knows what the macro regime was at the moment of ingestion. A theme that emerged during TURBULENCE is treated differently than one that emerged during EXPANSION.

**This is the core differentiator.** No other agent in this hackathon is conditioning narrative intelligence on a live, custom-built GLI engine.

The integration code lives in `remi-intelligence/src/gli_stamper.py`.
