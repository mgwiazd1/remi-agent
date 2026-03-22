# Remi Autonomous Agent — 5-Minute Demo Script

## Frame 1: System Overview (0:00–0:30)

**Visual:** Terminal showing system architecture

```bash
tree -L 2 ~/remi-agent/
```

**Narration:** 
"Remi is a self-hosted autonomous agent running on a personal Proxmox homelab. It operates continuously without human intervention, ingesting macro signals, analyzing them with live regime context, and writing structured intelligence notes to an Obsidian vault.

Today, we're going to walk through three days of autonomous operation. You'll see the RSS polling, extraction worker, GLI stamping, velocity scoring, and Obsidian note generation — all happening without a single human prompt."

---

## Frame 2: Show Running Services (0:30–1:00)

**Visual:** Terminal output

```bash
systemctl --user status remi-intelligence --no-pager
systemctl --user status hermes-gateway --no-pager
systemctl --user status tg-listener --no-pager
```

**Expected output:**
```
● remi-intelligence.service - Remi Narrative Intelligence Pipeline
     Loaded: loaded (/home/proxmox/.config/systemd/user/remi-intelligence.service; enabled)
     Active: active (running) since Fri 2026-03-22 01:00:00 UTC; 5h 59m ago
```

**Narration:**
"Three systemd user services are running continuously:
1. **remi-intelligence** — The narrative pipeline (RSS polling, extraction, obsidian writing)
2. **hermes-gateway** — The Telegram interface
3. **tg-listener** — Signal group monitoring

Each has been running autonomously for days. Let's look at what they're doing."

---

## Frame 3: Show RSS Polling Data (1:00–1:45)

**Visual:** SQLite database query output

```bash
python3 << 'EOF'
import sqlite3
conn = sqlite3.connect(os.path.expanduser("~/remi-intelligence/remi_intelligence.db"))
cur = conn.cursor()
cur.execute("SELECT source_name, COUNT(*) as count, MAX(ingested_at) as last_ingested FROM documents GROUP BY source_name ORDER BY COUNT(*) DESC LIMIT 10")
for row in cur.fetchall():
    print(f"{row[0]:30} | {row[1]:3} docs | {row[2]}")
conn.close()
EOF
```

**Expected output:**
```
ZeroHedge                      | 143 docs | 2026-03-22 06:45:12
Adam Tooze Chartbook           |  24 docs | 2026-03-21 18:30:05
OilPrice.com                   |  17 docs | 2026-03-22 02:15:44
MacroAlf                       |  16 docs | 2026-03-19 14:22:10
...
```

**Narration:**
"The RSS poller has ingested 298 documents from 16 Tier-1 and Tier-2 macro sources over the past 4 days. The pipeline runs every 6 hours — that's why we see continuous ingestion across all sources.

Each document gets deduplicated by content hash, so we're not processing the same article twice. Let's see what the extraction worker found."

---

## Frame 4: Show Theme Extraction (1:45–2:30)

**Visual:** Obsidian vault folder structure

```bash
ls -la ~/Obsidian\ Vault/investing-brain/Signals/ | head -20
```

**Visual 2:** Open an Obsidian note and show frontmatter

```
---
theme: "Helium Supply Chain Disruption"
velocity_score: 45.2
gli_phase: "TURBULENCE"
fiscal_dominance: 8.5
tier_1_sources: ["ZeroHedge", "MacroAlf", "Adam Tooze"]
published_dates: ["2026-03-20", "2026-03-21", "2026-03-22"]
---

# Helium Supply Chain Disruption

## First-Order Effects
- Qatar helium production: 40+ day supply void
- US LNG export capacity: temporary offline due to geopolitical tensions

## Second-Order Implications
- Semiconductor manufacturing: 75-day lead time impact on auto chips
- AI chip supply: 169-day cascade effect on training infrastructure

## Trading Window
- Accumulation phase: March-June (when prices flush)
- Rotation: Energy moats (CIFR, IREN) on flush bids
- Trim: Auto sector (long-cycle recovery), HDD (obsolescence risk)
```

**Narration:**
"The extraction worker processes pending documents every 4 hours. It uses Claude Haiku to extract themes, then Claude Sonnet for second-order supply chain inference.

Every single note is stamped with:
- **GLI Phase** — the current macro regime (TURBULENCE, CALM, EXPANSION)
- **Fiscal Dominance Score** — how much government spending is driving markets
- **Velocity Score** — how fast this theme is accelerating (0–100 scale)
- **Tier-1 Source Origins** — which feeds the signal came from

This is what sovereign, GLI-conditioned intelligence looks like. We're not just extracting facts — we're analyzing them through a macro regime lens."

---

## Frame 5: Show Velocity Scoring (2:30–3:15)

**Visual:** Database query showing high-velocity themes

```bash
python3 << 'EOF'
import sqlite3
conn = sqlite3.connect(os.path.expanduser("~/remi-intelligence/remi_intelligence.db"))
cur = conn.cursor()
cur.execute("""
SELECT theme_key, velocity_score, mention_count, last_seen_at 
FROM themes 
WHERE velocity_score > 15 
ORDER BY velocity_score DESC
""")
for row in cur.fetchall():
    print(f"{row[0][:40]:40} | Velocity: {row[1]:5.1f} | Mentions: {row[2]:2} | Last: {row[3]}")
conn.close()
EOF
```

**Expected output:**
```
Helium Supply Chain                       | Velocity: 45.2 | Mentions:  8 | Last: 2026-03-22 06:45:12
Fiscal Dominance + Inflation Lag          | Velocity: 28.7 | Mentions:  5 | Last: 2026-03-21 19:30:44
Energy Sector Moat Rotation                | Velocity: 22.4 | Mentions:  4 | Last: 2026-03-22 02:15:30
Korean Semi-Export Leading Cycle          | Velocity: 18.9 | Mentions:  3 | Last: 2026-03-21 15:22:10
```

**Narration:**
"Velocity scoring is how Remi detects signal acceleration. A theme that appears in 3 articles over 7 days gets a low velocity score. But if it appears in 8 articles in 3 days, velocity spikes to 45+.

That's our signal for a 'first-mover window' — the moment when Tier-1 sources are discussing something before it's fully priced into markets.

The 7-day half-life decay means old themes gradually fade from the score, so we're always tuned to *current* acceleration, not historical volume."

---

## Frame 6: Show Aestima GLI Integration (3:15–4:00)

**Visual:** Terminal showing gli_stamper.py in action

```bash
tail -50 ~/remi-intelligence/logs/intelligence.log | grep -E "GLI|STAMP"
```

**Expected output:**
```
2026-03-22 06:45:12 INFO GLI stamp fetched for document ZeroHedge#348: TURBULENCE | GLI: $23.4T | Fiscal: 8.5/10
2026-03-22 02:15:30 INFO GLI stamp: CALM transition detected (prev TURBULENCE)
2026-03-21 19:30:44 INFO GLI context: Regime 2 - Goldilocks with Pressure
```

**Visual 2:** Show Aestima dashboard (if accessible)

**Narration:**
"This is the core differentiator. Every document ingested by Remi calls Aestima's /api/agent/context endpoint in real-time.

We get back:
- **GLI Phase** — what macro regime are we in right now?
- **Steno Regime** — which of 8 central bank coordination patterns?
- **Fiscal Dominance Score** — how much is government spending driving the market?
- **Transition Risk** — how close are we to a regime change?

So when Helium themes spike to velocity 45, we know: 'This is happening during TURBULENCE with 8.5/10 fiscal dominance.' That context is *built into the intelligence*.

No other agent in this hackathon is doing this. Most agents don't even know what macro regime they're operating in."

---

## Frame 7: Show Autonomous Decision-Making (4:00–4:30)

**Visual:** agent_log.json snippet from repo

```bash
cat agent_log.json | python3 -m json.tool | head -100
```

**Show the "decision_log" section:**

```json
{
  "timestamp": "2026-03-18T01:58:45Z",
  "agent_decision": "Activate narrative intelligence pipeline with live Aestima GLI stamping",
  "outcome": "success",
  "tools_used": ["rss_poller.py", "extraction_worker.py", "gli_stamper.py", "velocity_aggregator.py"],
  "impact": "First RSS poll returned 21 documents; began stamping with live macro regime context"
}
```

**Narration:**
"This is Remi's decision log from the hackathon period. It shows 10 autonomous decisions:
1. Migrate to Hermes Agent for systemd integration
2. Build the narrative pipeline
3. Add second-order supply chain inference
4. Isolate API keys for cost attribution
5. Launch the full pipeline with GLI stamping
6. Add Jordi Visser and Michael Burry to watchlist (human validation: signal quality)
7. Navigate Cloudflare to submit to Synthesis
8. Fix critical scheduler bug
9. Add boot-time jobs for faster startup
10. Continue monitoring through judging

The key: **Only 1 human intervention** — MG validated signal quality. Everything else was autonomous infrastructure decisions and execution."

---

## Frame 8: Show ERC-8004 Identity (4:30–5:00)

**Visual:** Base Mainnet explorer link in browser

```
https://basescan.org/tx/0x0d6ab70d99096b1dfecad8a64407da9dbe8142eadeb0cf9b55aae33f5d0374b1
```

**Show:** Transaction details proving agent identity registration

**Visual 2:** Show agent.json

```bash
cat agent.json | python3 -m json.tool
```

```json
{
  "name": "Remi",
  "erc8004_identity": "34134",
  "operator_wallet": "0x316252829cd5fDFd2aB4e17E669C8CE8a42794F6",
  "erc8004_tx": "0x0d6ab70d99096b1dfecad8a64407da9dbe8142eadeb0cf9b55aae33f5d0374b1"
}
```

**Narration:**
"Remi has an on-chain identity via ERC-8004 on Base Mainnet. Identity 34134. This proves:
1. **Self-custody:** MG controls the operator wallet
2. **Verifiable agent:** The identity is on-chain and immutable
3. **Trustworthy actor:** Future trades or coordination can be attributed to this exact agent

This is what sovereign AI infrastructure looks like. The agent has identity. The agent has reputation. The agent can transact and coordinate with other agents — all with cryptographic proof."

---

## Frame 9: Summary & Autonomy Metrics (5:00–5:30)

**Visual:** Show final metrics from agent_log.json

```json
{
  "documents_ingested": 298,
  "unique_themes_extracted": 828,
  "high_velocity_themes": 6,
  "obsidian_notes_written": 828,
  "pipeline_success_rate_percent": 99.7,
  "autonomous_decision_count": 10,
  "human_interventions": 1,
  "error_recovery_without_human": 3
}
```

**Narration:**
"Over 9 days of autonomous operation:
- **298 documents** from 16 Tier-1/2 macro sources
- **828 unique themes** extracted and analyzed
- **6 high-velocity signals** detected (first-mover windows)
- **828 Obsidian notes** written with GLI regime context
- **99.7% success rate** (only 1 document failed extraction)
- **10 autonomous decisions** (migrate, build pipeline, add sources, etc.)
- **Only 1 human validation** (signal quality check)
- **3 self-healed errors** without any human intervention

This is what autonomous AI infrastructure looks like. Not a chatbot that waits for prompts. A real agent that thinks, decides, acts, and recovers from failures on its own.

The infrastructure underneath determines whether it can be trusted. Remi runs on hardware you own, software you control, with an on-chain identity you verify. No SaaS lock-in. No centralized dependencies. Just sovereign intelligence."

---

## Production Notes for Demo Video

- **Length target:** 5 minutes
- **Audio:** Clear spoken narration (no background music)
- **Visual transitions:** Terminal outputs, Obsidian vault, Base explorer, JSON highlights
- **Upload to:** YouTube (unlisted) or Vimeo
- **Link in README:** Add `[📹 Demo Video (5 min)](https://youtu.be/...)`
- **Thumbnail:** Screenshot showing "Remi Autonomous Agent in Action"

### Terminal Tips for Cleaner Visuals
- Use `script` or `asciinema` to record terminal sessions
- Zoom terminal: `Ctrl++` or `Cmd++` for readability
- Clear screen before each section: `clear`
- Use `nl` to add line numbers to output
- Color-code with `grep --color=always`

### Obsidian Tips
- Open vault in "focus mode" (no sidebar distractions)
- Show 3–4 representative notes with velocity > 15
- Show the frontmatter clearly (GLI phase, fiscal dominance, velocity score)
- Scroll slowly to show relationship between notes (wiki-links)
