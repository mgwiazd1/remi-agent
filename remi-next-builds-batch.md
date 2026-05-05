# Remi — Next Build Batch
**Date:** April 15, 2026 (evening)
**Context:** After vision pipeline + vault triage system shipped. Five builds queued in priority order.

---

## BUILD ORDER & DEPENDENCIES

```
Build 1 — Hermes Gateway Vision Fix        (1 session, reuses vision_relay.py)
    ↓ (independent)
Build 2 — GLI Phase Transition Fix          (1 session, debug + rewire)
    ↓ (independent)
Build 3 — Pick Approval → Watchlist          (0.5 session, small wire job)
    ↓ (independent)
Build 4 — Aestima Bidirectional Integration  (1-2 sessions, API work)
    ↓ (independent)
Build 5 — Cross-Book Synthesis               (1 session, Sonnet call)
```

All 5 are independent — can be built in any order. Recommend: 3 first (smallest win), then 1, 2, 4, 5.

---

# BUILD 1 — Hermes Gateway Vision Fix

## PROBLEM
Photos dropped in the investing **group** work (signals listener handles them via `vision_relay.py`). Photos sent to Remi's **DM** fail silently — Hermes gateway sees the photo but GLM-5 can't process images, and the gateway has no vision relay.

## SOLUTION
Patch the Hermes gateway's platform handler (`~/.hermes/hermes-agent/gateway/platforms/base.py`) to detect photos, download them, call `vision_relay.describe_image()`, and prepend the description to the message text before forwarding to GLM-5.

This is the same patch as the EPUB fix (April 12) — upstream edit, lost on Hermes update. Document in a maintenance note for future reference.

## BUILD STEPS

### Step 1: Read the gateway's platform base
```bash
cat ~/.hermes/hermes-agent/gateway/platforms/base.py | grep -n "photo\|image\|media\|download" | head -20
```

Find where messages are received and forwarded to the agent. The SUPPORTED_DOCUMENT_TYPES allowlist (patched for EPUB) is nearby — use that as an anchor.

### Step 2: Add photo detection and vision relay
In the message handler, add logic:

```python
# Pseudocode — match the actual patterns in base.py
async def handle_message(self, update):
    msg = update.message
    
    # EXISTING: text and document handling
    
    # NEW: photo handling
    if msg.photo:
        try:
            # Import the pipeline's vision relay
            import sys
            sys.path.insert(0, '/home/proxmox/remi-intelligence/src')
            from vision_relay import describe_image
            
            # Download via Telegram Bot API
            photo = msg.photo[-1]  # largest size
            file_info = await self.bot.get_file(photo.file_id)
            import tempfile, os
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp_path = tmp.name
            await file_info.download_to_drive(tmp_path)
            
            # Call vision relay (handles VRAM swap)
            caption = msg.caption or ""
            image_type = "chart"  # default for investing
            if any(kw in caption.lower() for kw in ["cxr", "xray", "x-ray", "ct ", "mri"]):
                image_type = "medical"
            
            description = describe_image(image_path=tmp_path, image_type=image_type)
            
            if description.startswith("ERROR:"):
                # Fall through to normal gateway handling with an error note
                msg_text = f"[Vision pipeline failed: {description}] User caption: {caption}"
            else:
                # Prepend description to message text
                msg_text = (
                    f"[User sent an image. Gemma E4B description:]\n"
                    f"{description}\n\n"
                    f"[User caption: {caption}]"
                )
            
            # Replace message text and forward to agent
            msg.text = msg_text
            os.unlink(tmp_path)
        except Exception as e:
            logger.error(f"Gateway vision relay failed: {e}")
            # Fall through to existing handling
    
    # EXISTING: forward to agent
```

### Step 3: Document the upstream edit
```bash
cat >> ~/.hermes/UPSTREAM_PATCHES.md << 'EOF'
## base.py photo handler (2026-04-15)
Added photo detection + vision_relay integration in gateway platform handler.
Lost on Hermes update. If photos in DM stop working, re-apply from:
`/home/proxmox/remi-intelligence/specs/remi-next-builds-batch.md`
EOF
```

### Step 4: Restart and test
```bash
systemctl --user restart hermes-gateway.service
# Send a chart directly to @Gwizzlybear_Remibot DM
# Should see VRAM swap in logs, vision description in reply
```

### Step 5: Update SOUL.md
```bash
sed -i 's/Photos in DMs are NOT handled by the vision pipeline.*for now./Photos in DMs ARE handled via the gateway vision relay. Treat them the same as group photos./' ~/.hermes/SOUL.md
systemctl --user restart hermes-gateway.service
```

---

# BUILD 2 — GLI Phase Transition Fix

## PROBLEM
Alerts have been disabled since April 13. Root cause: `calm→calm` was triggering as a "transition" — flagged 3 alerts in 6 minutes. The fix was a blanket disable with `stamp.phase_changed = False` hardcoded.

## SOLUTION
Three-part fix:
1. Debug the false positive in the delta endpoint logic
2. Wire phase as a single line in the morning brief (always present, not an alert)
3. Rebuild transition detection with 24h suppression to prevent spam

## BUILD STEPS

### Step 1: Find the false positive
```bash
# Check where phase_changed gets set
grep -n "phase_changed" ~/remi-intelligence/src/gli_stamper.py

# Check Aestima's delta response — is it returning phase_changed=true when it shouldn't?
curl -s -H "X-Agent-Key: $AESTIMA_AGENT_KEY" https://aestima.ai/api/agent/context/delta | python3 -m json.tool | head -30
```

Two possibilities:
- Aestima endpoint returns `phase_changed: true` for same-phase deltas (Aestima bug — report to CC1)
- Our code checks `new_phase != old_phase` incorrectly (ours to fix)

### Step 2: Remove the hardcoded disable
```bash
# Find the DISABLED comment blocks in gli_stamper.py
grep -n "DISABLED\|# logger.error" ~/remi-intelligence/src/gli_stamper.py
# Restore them properly (see Step 4)
```

### Step 3: Add 24h alert suppression
Add a `last_phase_alert_at` field to the phase state tracking:

```python
# In gli_stamper.py — around the phase state management
from datetime import datetime, timezone, timedelta

ALERT_SUPPRESSION_HOURS = 24

def _should_alert_on_phase(old_phase: str, new_phase: str, last_alert_at: str | None) -> bool:
    """Genuine transition + not recently alerted."""
    if not old_phase or not new_phase:
        return False
    if old_phase == new_phase:
        return False
    if last_alert_at:
        try:
            last = datetime.fromisoformat(last_alert_at.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) - last < timedelta(hours=ALERT_SUPPRESSION_HOURS):
                return False
        except Exception:
            pass
    return True
```

### Step 4: Morning brief phase line
In `main.py` — the morning brief assembly function:

```python
# Fetch current phase from Aestima stamp
try:
    from gli_stamper import fetch_gli_stamp
    stamp = fetch_gli_stamp()
    phase = stamp.gli_phase if stamp else "unknown"
    since = _get_phase_since(phase)  # helper: fetch from phase state
    brief_lines.append(f"📊 GLI Phase: {phase.upper()} (since {since})")
except Exception as e:
    logger.warning(f"Failed to add GLI phase to brief: {e}")
```

### Step 5: Rebuild genuine transition alert
Re-enable the alert send, but guarded by `_should_alert_on_phase()`:

```python
if _should_alert_on_phase(old_phase, new_phase, last_alert_at):
    alert_msg = (
        f"🚨 *GLI PHASE TRANSITION*\n"
        f"{old_phase.upper()} → {new_phase.upper()}\n"
        f"Transition risk: {stamp.transition_risk}/10\n"
        f"Fiscal dominance: {stamp.fiscal_score}/10"
    )
    send_investing_alert(alert_msg)
    _update_phase_state(new_phase, alert_sent=True, alert_at=datetime.now(timezone.utc).isoformat())
```

### Step 6: Verify and restart
```bash
python3 -c "import ast; ast.parse(open('/home/proxmox/remi-intelligence/src/gli_stamper.py').read()); print('SYNTAX OK')"
systemctl --user restart remi-intelligence.service

# Watch the logs for next stamper run
journalctl --user -u remi-intelligence.service -f | grep -i "gli\|phase"
```

---

# BUILD 3 — Pick Approval → Watchlist Auto-Add

## PROBLEM
When MG approves a pick via `/pick approve` or `/approve TICKER`, the pick is marked approved in DB but doesn't automatically add to the watchlist. Requires a manual `/watch add` step.

## SOLUTION
On approval, auto-populate `watchlist.json` with the ticker, thesis, and metadata from the approved pick. Trigger a thesis eval so Aestima gets the ticker in its module cache.

## BUILD STEPS

### Step 1: Find the approval handler
```bash
grep -n "pick_approve\|approve.*pick\|/approve\|pick.*approved" ~/remi-intelligence/src/*.py
```

Likely in `signals_group_listener.py` (for Telegram command) and `picks_engine.py` (for the DB update).

### Step 2: Add auto-watchlist on approval
In the approval handler, after the DB status update:

```python
def approve_pick(pick_id: int, approved_by: str) -> str:
    """Mark pick approved + auto-add to watchlist + queue thesis eval."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "UPDATE remi_picks SET status='approved', approved_by=?, approved_at=? WHERE id=?",
        (approved_by, datetime.now().isoformat(), pick_id)
    )
    cur.execute(
        "SELECT ticker, pick_direction, thesis, conviction_score FROM remi_picks WHERE id=?",
        (pick_id,)
    )
    row = cur.fetchone()
    conn.commit()
    conn.close()
    
    if not row:
        return f"❌ Pick #{pick_id} not found"
    
    ticker, direction, thesis, conviction = row
    
    # Auto-add to watchlist
    from watchlist_manager import add_ticker_from_pick
    watchlist_result = add_ticker_from_pick(
        ticker=ticker,
        direction=direction,
        thesis_summary=thesis[:500] if thesis else "",
        conviction=conviction,
        source="pick_approval",
    )
    
    # Queue thesis eval (fire and forget)
    try:
        from watchlist_manager import run_thesis_eval
        import asyncio
        asyncio.create_task(run_thesis_eval(ticker, force_refresh=True))
    except Exception as e:
        logger.warning(f"Thesis eval queue failed: {e}")
    
    return (
        f"✅ Pick #{pick_id} ({ticker}) approved\n"
        f"📋 {watchlist_result}\n"
        f"🔬 Thesis eval queued"
    )
```

### Step 3: Add `add_ticker_from_pick()` to watchlist_manager.py
```python
def add_ticker_from_pick(ticker: str, direction: str, thesis_summary: str, 
                         conviction: float, source: str) -> str:
    """Add a ticker to watchlist from an approved pick. Skip if already present."""
    watchlist = load_watchlist()
    ticker = ticker.upper().strip()
    
    if ticker in watchlist["tickers"]:
        return f"{ticker} already on watchlist — no change"
    
    watchlist["tickers"][ticker] = {
        "direction": direction,
        "thesis_summary": thesis_summary,
        "conviction_at_add": conviction,
        "source": source,
        "added_at": datetime.now().isoformat(),
        "target_return": "2X",  # default
        "horizon_years": 2,  # default
        "last_eval_date": None,
        "last_eval_gli_phase": None,
    }
    save_watchlist(watchlist)
    return f"Added {ticker} to watchlist ({direction}, conviction {conviction:.1f})"
```

### Step 4: Test and verify
```bash
# Create a test pick, approve it, verify watchlist updated
sqlite3 ~/remi-intelligence/remi_intelligence.db "SELECT ticker, status FROM remi_picks WHERE status='approved' ORDER BY id DESC LIMIT 3"
cat ~/remi-intelligence/config/watchlist.json | python3 -m json.tool | head -20

systemctl --user restart signals-listener.service
```

---

# BUILD 4 — Aestima Bidirectional Integration

## CURRENT STATE
Remi reads Aestima via `/api/agent/context` and `/api/agent/context/delta`. Aestima can read Remi's dossier via `/api/watchlist/dossier/{ticker}` on the Remi dashboard.

**Missing:** Remi doesn't push narrative velocity findings into Aestima. The dashboard shows GLI/liquidity metrics but doesn't know what themes are trending in Remi's pipeline.

## SOLUTION
Two push channels from Remi → Aestima:

1. **Theme velocity snapshot** — every 4h, Remi posts top trending themes + mention counts to Aestima
2. **Convergence alerts** — when 3+ velocity signals align, Remi pushes the convergence event to Aestima for dashboard display

## BUILD STEPS

### Step 1: Aestima endpoint (requires CC1 coordination)
Aestima needs new endpoints. Work with CC1 to build:

- `POST /api/agent/remi-intel/themes` — accepts top N trending themes
- `POST /api/agent/remi-intel/convergence` — accepts convergence events

Auth: existing `X-Agent-Key` header.

### Step 2: Remi side — theme velocity push
Add to `velocity_aggregator.py` or new `aestima_push.py`:

```python
import httpx
import os
import sqlite3

AESTIMA_BASE = os.environ.get("AESTIMA_BASE_URL", "https://aestima.ai")
AGENT_KEY = os.environ.get("AESTIMA_AGENT_KEY", "")

def push_theme_velocity_to_aestima() -> bool:
    """Push top 20 trending themes (last 7 days) to Aestima."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT t.theme_label, COUNT(*) as mentions, 
               MAX(dt.extracted_at) as latest
        FROM document_themes dt
        JOIN themes t ON dt.theme_id = t.id
        WHERE dt.extracted_at > datetime('now', '-7 days')
        GROUP BY t.theme_label
        ORDER BY mentions DESC
        LIMIT 20
    """)
    themes = [
        {"label": row[0], "mentions_7d": row[1], "latest_mention": row[2]}
        for row in cur.fetchall()
    ]
    conn.close()
    
    try:
        resp = httpx.post(
            f"{AESTIMA_BASE}/api/agent/remi-intel/themes",
            json={"themes": themes, "pushed_at": datetime.now().isoformat()},
            headers={"X-Agent-Key": AGENT_KEY},
            timeout=30,
        )
        if resp.status_code == 200:
            logger.info(f"Pushed {len(themes)} themes to Aestima")
            return True
        else:
            logger.warning(f"Aestima theme push failed: HTTP {resp.status_code}")
    except Exception as e:
        logger.error(f"Aestima theme push error: {e}")
    return False
```

### Step 3: Convergence push
When `velocity_aggregator._detect_convergence()` fires with 3+ signals, also push:

```python
def push_convergence_to_aestima(convergence_data: dict) -> bool:
    """Push convergence event to Aestima."""
    try:
        resp = httpx.post(
            f"{AESTIMA_BASE}/api/agent/remi-intel/convergence",
            json={
                "signal_count": convergence_data["count"],
                "signals": convergence_data["signals"],
                "direction": convergence_data["direction"],
                "detected_at": datetime.now().isoformat(),
            },
            headers={"X-Agent-Key": AGENT_KEY},
            timeout=30,
        )
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"Aestima convergence push error: {e}")
        return False
```

### Step 4: Schedule the theme push
In `main.py`:

```python
from aestima_push import push_theme_velocity_to_aestima

def job_aestima_theme_push():
    push_theme_velocity_to_aestima()

scheduler.add_job(job_aestima_theme_push, IntervalTrigger(hours=4),
    id="aestima_theme_push", replace_existing=True,
    misfire_grace_time=3600)
```

### Step 5: Hand Aestima side to CC1
Write a one-page spec for CC1 covering:
- Endpoint schemas (matches what Remi sends in Step 2 & 3)
- Dashboard view: "Remi Intelligence — Trending Themes" section
- Database table: `remi_intel_themes` and `remi_intel_convergence`
- Migration number (next in sequence)

---

# BUILD 5 — Cross-Book Synthesis

## CURRENT STATE
Books processed: Lynch (20 chapters, 100% extraction), ICU Book (reprocessed via Consuela), Dalio Big Debt Crises (9 chapters, completed). Each book produces FRAMEWORK_*.md and EPISODE_*.md files in vault.

**Missing:** No synthesis across books. Lynch's "Charmin Syndrome" (stocks people love but nobody buys) maps to Dalio's "beautiful deleveraging" concept — same underlying insight about market irrationality. Currently these live in separate silos.

## SOLUTION
Monthly job that reads all FRAMEWORK and EPISODE files across all books, finds conceptual connections, and writes a cross-book synthesis note.

Claude Sonnet handles this — it's rare, high-value synthesis work per the local inference roadmap's "Specialist" tier.

## BUILD STEPS

### Step 1: Create `cross_book_synthesizer.py`
```python
"""
cross_book_synthesizer.py — Monthly cross-book thematic synthesis.
Runs first Sunday of each month at 10am. Reads all FRAMEWORK and EPISODE notes,
asks Claude Sonnet to find connections, writes SYNTHESIS_YYYY-MM.md.
"""

import os, sys, json, logging
from pathlib import Path
from datetime import datetime
from anthropic import Anthropic

logger = logging.getLogger(__name__)

VAULT = "/docker/obsidian/investing/Intelligence"
BOOKS_DIR = Path(VAULT) / "Books"
SYNTHESIS_DIR = Path(VAULT) / "Books" / "Synthesis"

def collect_book_content() -> dict:
    """Gather all FRAMEWORK and EPISODE files, grouped by book."""
    content_by_book = {}
    for md in BOOKS_DIR.rglob("FRAMEWORK_*.md"):
        book = md.parent.name if md.parent.name != "Books" else "uncategorized"
        content_by_book.setdefault(book, []).append({
            "type": "framework",
            "name": md.stem,
            "content": md.read_text(errors="replace")[:3000],  # cap
        })
    for md in BOOKS_DIR.rglob("EPISODE_*.md"):
        book = md.parent.name if md.parent.name != "Books" else "uncategorized"
        content_by_book.setdefault(book, []).append({
            "type": "episode",
            "name": md.stem,
            "content": md.read_text(errors="replace")[:3000],
        })
    return content_by_book

SYNTHESIS_PROMPT = """You are synthesizing insights across multiple investing books.

Below are frameworks and historical episodes extracted from {n_books} different books.

For each book, identify the core mental models and how they connect to each other.
Then find CROSS-BOOK connections — where an insight in Book A maps to a concept in Book B.

Focus on:
1. Same underlying principle expressed differently (e.g., Lynch's "Charmin Syndrome" ≈ Dalio's "beautiful deleveraging")
2. Concepts that reinforce each other
3. Concepts that CONTRADICT each other (these are high-value — forces the reader to think)
4. Temporal patterns: which concepts apply in which market regimes

Output structure:
## Core Frameworks by Book
### [Book Name]
- Framework: description (1 line)

## Cross-Book Connections
### Connection 1: [Name]
- Book A: [specific framework]
- Book B: [specific framework]
- Why they connect: [2-3 sentences]
- Portfolio implication: [how this helps investing decisions]

## Contradictions
- Book A says X. Book B says Y. When does each apply?

## Regime Applicability
- In Expansion: these frameworks dominate
- In Turbulence: these frameworks dominate
- In Contraction: these frameworks dominate

---

BOOKS:
{book_content}
"""

def synthesize(content_by_book: dict) -> str:
    """Call Claude Sonnet for cross-book synthesis."""
    client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    
    # Build prompt content
    book_sections = []
    for book, items in content_by_book.items():
        section = f"\n### {book}\n"
        for item in items[:30]:  # cap items per book
            section += f"\n**{item['type'].upper()}: {item['name']}**\n{item['content']}\n"
        book_sections.append(section)
    
    prompt = SYNTHESIS_PROMPT.format(
        n_books=len(content_by_book),
        book_content="\n\n".join(book_sections)
    )
    
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}]
    )
    return resp.content[0].text

def write_synthesis(synthesis_text: str) -> Path:
    SYNTHESIS_DIR.mkdir(exist_ok=True, parents=True)
    date_str = datetime.now().strftime("%Y-%m")
    filepath = SYNTHESIS_DIR / f"SYNTHESIS_{date_str}.md"
    
    header = (
        f"---\n"
        f"date: {datetime.now().strftime('%Y-%m-%d')}\n"
        f"type: cross_book_synthesis\n"
        f"model: claude-sonnet-4-6\n"
        f"---\n\n"
        f"# Cross-Book Synthesis — {datetime.now().strftime('%B %Y')}\n\n"
    )
    filepath.write_text(header + synthesis_text)
    import subprocess
    subprocess.run(["chown", "proxmox:proxmox", str(filepath)], capture_output=True)
    return filepath

def main():
    logger.info("Cross-book synthesis starting")
    content = collect_book_content()
    if len(content) < 2:
        logger.info(f"Only {len(content)} book(s), skipping synthesis")
        return
    
    synthesis = synthesize(content)
    filepath = write_synthesis(synthesis)
    logger.info(f"Synthesis written to {filepath}")
    
    # Notify via Telegram
    from telegram_sender import send_investing_alert
    send_investing_alert(
        f"📚 *Cross-Book Synthesis — {datetime.now().strftime('%B %Y')}*\n\n"
        f"New synthesis covering {len(content)} books. "
        f"Available in vault: `Books/Synthesis/{filepath.name}`"
    )

if __name__ == "__main__":
    main()
```

### Step 2: Schedule (first Sunday of month, 10am)
In `main.py`:

```python
from cross_book_synthesizer import main as cross_book_synth

def job_cross_book_synthesis():
    try:
        cross_book_synth()
    except Exception as e:
        logger.error(f"Cross-book synthesis failed: {e}")

scheduler.add_job(
    job_cross_book_synthesis,
    CronTrigger(day="1-7", day_of_week="sun", hour=10, minute=0),
    id="cross_book_synth", replace_existing=True,
    misfire_grace_time=3600,
)
```

### Step 3: Manual first run
```bash
cd ~/remi-intelligence/src && python3 cross_book_synthesizer.py
cat /docker/obsidian/investing/Intelligence/Books/Synthesis/SYNTHESIS_$(date +%Y-%m).md | head -50
```

---

## BUILD ORDER SUMMARY

| # | Build | Session Size | Priority |
|---|-------|--------------|----------|
| 3 | Pick approval → watchlist | 0.5 session | Start here (small win) |
| 1 | Hermes gateway vision | 1 session | High daily value |
| 2 | GLI phase transition | 1 session | Unlocks disabled alerts |
| 4 | Aestima bidirectional | 1-2 sessions | Needs CC1 coordination |
| 5 | Cross-book synthesis | 1 session | Ready once Dalio is in vault |

---

*Spec: April 15, 2026 (evening)*
*Five builds queued. Start with Build 3 for a quick win.*
*Previous: remi-vault-triage-system-spec.md*
