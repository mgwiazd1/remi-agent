# REMI — Theme Sector Taxonomy + Consolidation Spec
**Date:** April 16, 2026  
**Priority:** P0 — push pipeline is live and forwarding junk to Aestima right now  
**Reason:** 15 of 21 themes on the Aestima dashboard are the same Iran/Hormuz/oil story restated differently. Zero fed, crypto, AI, FX coverage. We're missing alpha.

---

## TWO PROBLEMS, NOT ONE

### Problem 1: No sector classification
The extraction prompt produces flat `theme_key` slugs with no subject categorization. Top 20 by mentions = whatever dominates the feed = geopolitical noise.

### Problem 2: No theme consolidation (the bigger problem)
The LLM generates a NEW `theme_key` for every article's slightly different framing of the same event. Evidence from the live dashboard right now:

| Theme | Mentions | What it actually is |
|-------|----------|-------------------|
| Geopolitical Risk Premium Deflation In Energy | 12 | Iran/Hormuz oil story |
| Geopolitical Risk & Regional Escalation | 9 | Iran/Hormuz oil story |
| Middle East Ceasefire Negotiations & Infrastr... | 8 | Iran/Hormuz oil story |
| Strait Of Hormuz Closure Impact | 8 | Iran/Hormuz oil story |
| Strait Of Hormuz Flow Collapse And Sanctions... | 8 | Iran/Hormuz oil story |
| Geopolitical Risk & Energy Crisis Response | 7 | Iran/Hormuz oil story |
| Strait Of Hormuz Closure Risk & Energy Supply... | 7 | Iran/Hormuz oil story |
| U.S. Iran Military Escalation And War Justifi... | 7 | Iran/Hormuz oil story |
| Geopolitical Premium In Oil Markets | 5 | Iran/Hormuz oil story |
| Middle East Conflict Driven Oil Price Surge | 5 | Iran/Hormuz oil story |

That's **10 themes that should be 1 theme** with ~76 combined mentions and a massive velocity score. Instead the signal is fragmented across 10 slugs, each looking modestly active.

Meanwhile "Private Credit Fragility" sits at 3 mentions — but that might be a genuinely early, high-alpha signal that's getting drowned out.

**Both problems must be fixed together.** Sector tagging without consolidation just labels the same 10 geopolitical themes as "geopolitical." Consolidation without sector tagging still pushes an unbalanced list.

---

## ARCHITECTURE: THEME ANCHORS + SECTOR TAXONOMY

### The concept: "Theme Anchors"
Instead of letting the LLM freely generate theme keys, the extraction prompt receives a list of **existing active theme anchors** and must either:
1. **Match** the extracted theme to an existing anchor (incrementing its mention count), or
2. **Create** a new anchor only if the theme is genuinely novel

This is the key architectural change. The prompt goes from "invent a theme_key" to "here are the 30 active themes — does this article belong to one of them, or is it something new?"

### How it works in the extraction flow:

```
Document arrives for extraction
    │
    ▼
Query DB: SELECT theme_key, theme_label, sector FROM themes
          WHERE last_seen_at > datetime('now', '-14 days')
          AND mention_count >= 2
          ORDER BY velocity_score DESC LIMIT 40
    │
    ▼
Inject active anchors into THEME_EXTRACTION_PROMPT
    │
    ▼
LLM extracts themes, MUST either:
  - Return an existing theme_key from the anchor list (match)
  - Return a NEW theme_key only if truly novel (create)
  - Classify each as a sector from closed taxonomy
    │
    ▼
Post-processing:
  - Validate sector against VALID_SECTORS
  - If theme_key matches existing anchor → upsert (increment mentions)
  - If theme_key is new → insert with sector
  - Junk guard: mention_count >= 2 before vault write (existing)
```

---

## SECTOR TAXONOMY (closed set — LLM must pick exactly one)

```python
SECTOR_TAXONOMY = [
    "geopolitical",      # wars, sanctions, trade policy, territorial disputes, regime changes
    "macro",             # GDP, employment, inflation prints, central bank policy (non-Fed)
    "fed",               # Fed-specific: rates, QT, RRP, SOFR, dot plots, Powell signals
    "credit",            # HY spreads, IG, CLOs, bank stress, lending standards, defaults
    "commodities",       # oil, gas, metals, agriculture, mining, supply chains
    "crypto",            # BTC, ETH, DeFi, stablecoins, regulation, on-chain flows
    "ai",                # semiconductors, LLMs, data centers, compute buildout, AI policy
    "equities",          # single stocks, sectors, earnings, buybacks, positioning
    "fiscal",            # government spending, deficits, debt ceiling, Treasury issuance
    "fx",                # dollar, DXY, EM currencies, carry trades, intervention
]
```

**Design choices:**
- `fed` separate from `macro` — Fed is the single most impactful variable in Remi's thesis universe
- `fiscal` separate from `macro` — fiscal dominance is a core Aestima metric
- `commodities` covers full supply chain: energy, metals, agriculture, mining, shipping
- 10 sectors = enough granularity for dashboard tabs without fragmentation
- If a theme spans sectors, LLM picks the **primary driver** (the causal trigger)

---

## STEP 1 — BUILD ANCHOR INJECTION INTO EXTRACTION PROMPT

### 1A. Query active anchors before each extraction

In `extraction_worker.py` (or wherever the extraction loop runs), before calling `llm_extractor.extract_themes()`:

```python
def get_active_anchors(db_path: str, limit: int = 40) -> list[dict]:
    """Pull current active theme anchors for prompt injection."""
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT theme_key, theme_label, sector
        FROM themes
        WHERE last_seen_at > datetime('now', '-14 days')
          AND mention_count >= 2
        ORDER BY velocity_score DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def format_anchors_for_prompt(anchors: list[dict]) -> str:
    """Format anchor list for LLM prompt injection."""
    if not anchors:
        return "No active themes yet. Create new theme keys as needed."
    
    lines = ["ACTIVE THEME ANCHORS (reuse these theme_keys when the article covers the same topic):"]
    for a in anchors:
        sector = a.get("sector", "macro")
        lines.append(f'  - "{a["theme_key"]}" [{sector}]: {a["theme_label"]}')
    lines.append("")
    lines.append("If this article covers a topic already in the list above, REUSE that theme_key exactly.")
    lines.append("Only create a NEW theme_key if the topic is genuinely not covered by any existing anchor.")
    return "\n".join(lines)
```

### 1B. Modify THEME_EXTRACTION_PROMPT

Insert the anchor block into the prompt. The modified prompt structure:

```python
THEME_EXTRACTION_PROMPT = """You are a macro investment research analyst.

Analyze the following document and extract structured intelligence.

DOCUMENT SOURCE: {source_name} (Tier {source_tier})
CURRENT GLI CONTEXT:
  Phase: {gli_phase}
  GLI Value: ${gli_value_bn}B
  Steno Regime: {steno_regime}
  Fiscal Dominance Score: {fiscal_score}/10
  Transition Risk: {transition_risk}/10

{active_anchors_block}

DOCUMENT CONTENT:
{content}

---

Return a JSON object with this exact structure:

{{
  "themes": [
    {{
      "theme_key": "lowercase-hyphenated-key",
      "theme_label": "Human Readable Label",
      "sector": "geopolitical|macro|fed|credit|commodities|crypto|ai|equities|fiscal|fx",
      "summary": "2-3 sentence description of this theme as discussed in the document",
      "facts": [
        "Specific data point, number, or verifiable claim (no hedging language)"
      ],
      "opinions": [
        "Forecast, estimate, price target, or speculative claim"
      ],
      "tickers_mentioned": ["TICKER1", "TICKER2"],
      "sentiment": "bullish|bearish|neutral|mixed",
      "key_quote": "Single most important sentence from the document on this theme"
    }}
  ],
  "overall_document_summary": "2-3 sentence summary of the entire document",
  "narrative_saturation": "low|medium|high",
  "regime_alignment": "aligned|divergent|neutral",
  "regime_alignment_rationale": "One sentence explaining alignment"
}}

RULES:
- THEME CONSOLIDATION IS CRITICAL: If this article discusses a topic already covered by an
  active anchor above, you MUST reuse that exact theme_key. Do NOT create a near-duplicate.
  Example: if "iran-hormuz-oil-supply-disruption" exists, do NOT create
  "strait-of-hormuz-closure-impact" or "geopolitical-risk-energy-crisis" — reuse the anchor.
- Only create a new theme_key when the article covers a genuinely NOVEL topic not in the anchors.
- sector: classify each theme into exactly ONE sector from this closed list:
  geopolitical, macro, fed, credit, commodities, crypto, ai, equities, fiscal, fx
  Pick the PRIMARY driver. If a theme spans sectors, choose the sector of the causal trigger.
  Example: "China bans rare earth exports" → geopolitical (policy action is the catalyst)
  Example: "Fed holds rates, signals data-dependent" → fed
  Example: "HY spreads blow out on bank stress" → credit
  Example: "NVIDIA earnings crush, data center demand" → ai (not equities — AI buildout is the theme)
- Facts: actual data, earnings numbers, official statistics, confirmed events
- Opinions: any forecast, target, valuation, or speculative scenario
- Extract 1-5 themes maximum — quality over quantity
- narrative_saturation: low = niche/early, medium = gaining traction, high = widely discussed
- regime_alignment: does the document's thesis support or contradict the current GLI regime?
- Return only valid JSON, no preamble or markdown fences"""
```

### 1C. Thread anchors through the extraction call

Wherever `extract_themes()` is called:

```python
# Before:
result = await extract_themes(content, source_name, source_tier, gli_context)

# After:
anchors = get_active_anchors(db_path)
anchors_block = format_anchors_for_prompt(anchors)
result = await extract_themes(content, source_name, source_tier, gli_context, anchors_block)
```

The `extract_themes()` function signature needs an `active_anchors_block` parameter that gets formatted into the prompt.

**Token cost:** 40 anchors × ~15 tokens each = ~600 extra tokens per extraction call. Negligible.

---

## STEP 2 — POST-EXTRACTION VALIDATION

After the LLM returns themes, validate both sector and theme_key:

```python
VALID_SECTORS = {"geopolitical", "macro", "fed", "credit", "commodities", "crypto", "ai", "equities", "fiscal", "fx"}

def normalize_sector(raw: str) -> str:
    """Normalize LLM-returned sector to valid taxonomy value."""
    cleaned = raw.strip().lower().replace(" ", "_")
    if cleaned in VALID_SECTORS:
        return cleaned
    ALIASES = {
        "geo": "geopolitical", "geopolitics": "geopolitical", "politics": "geopolitical",
        "rates": "fed", "federal_reserve": "fed", "monetary_policy": "fed",
        "bonds": "credit", "fixed_income": "credit", "spreads": "credit",
        "oil": "commodities", "energy": "commodities", "metals": "commodities", "agriculture": "commodities",
        "bitcoin": "crypto", "defi": "crypto", "blockchain": "crypto",
        "semiconductor": "ai", "semiconductors": "ai", "chips": "ai", "compute": "ai",
        "stocks": "equities", "earnings": "equities",
        "deficit": "fiscal", "spending": "fiscal", "treasury": "fiscal",
        "dollar": "fx", "currency": "fx", "currencies": "fx",
    }
    return ALIASES.get(cleaned, "macro")

def validate_extracted_theme(theme: dict, anchor_keys: set[str]) -> dict:
    """Validate and normalize a single extracted theme."""
    # Normalize sector
    theme["sector"] = normalize_sector(theme.get("sector", "macro"))
    
    # Normalize theme_key format
    key = theme["theme_key"].strip().lower()
    key = re.sub(r'[^a-z0-9-]', '-', key)
    key = re.sub(r'-+', '-', key).strip('-')
    theme["theme_key"] = key
    
    return theme
```

---

## STEP 3 — DB MIGRATION

### SQLite:
```sql
ALTER TABLE themes ADD COLUMN sector TEXT DEFAULT 'macro';
CREATE INDEX IF NOT EXISTS idx_themes_sector ON themes(sector);
```

### Update theme upsert logic:
Include `sector` in INSERT. **First-seen sector sticks** — don't overwrite on subsequent mentions:

```python
# Pseudocode for the upsert:
INSERT INTO themes (theme_key, theme_label, sector, first_seen_at, last_seen_at, mention_count, ...)
VALUES (?, ?, ?, ?, ?, 1, ...)
ON CONFLICT(theme_key) DO UPDATE SET
    last_seen_at = excluded.last_seen_at,
    mention_count = mention_count + 1,
    -- sector NOT updated on conflict — first classification sticks
    ...
```

---

## STEP 4 — ONE-TIME CONSOLIDATION OF EXISTING THEMES

The existing ~1,900+ themes have massive duplication. Before backfilling sectors, consolidate them.

### 4A. Identify consolidation groups

Run a Consuela batch job that takes all theme_keys with mention_count >= 2 and groups them by semantic similarity. This can be done cheaply with Consuela (local, free):

```python
CONSOLIDATION_PROMPT = """You are a theme deduplication engine.

Here are {n} theme labels from a macro intelligence database. Many of them describe 
the SAME underlying topic with slightly different wording.

Group them into clusters where each cluster = one real-world theme.
For each cluster, pick the BEST theme_key as the canonical anchor.

THEMES:
{theme_list}

Return JSON:
{{
  "clusters": [
    {{
      "canonical_key": "iran-hormuz-oil-supply-disruption",
      "canonical_label": "Iran/Hormuz Oil Supply Disruption", 
      "sector": "geopolitical",
      "merged_keys": [
        "strait-of-hormuz-closure-impact",
        "geopolitical-risk-premium-deflation-in-energy",
        "geopolitical-risk-and-regional-escalation",
        "middle-east-conflict-driven-oil-price-surge",
        ...
      ]
    }},
    ...
  ]
}}

Rules:
- Two themes are the SAME if they describe the same real-world event, policy, or trend
- "Strait of Hormuz Closure Impact" and "Geopolitical Risk & Energy Crisis Response" = SAME (both about Iran/oil)
- "Private Credit Fragility" and "Private Credit Redemption Wave" = SAME (both about PE/credit stress)
- "Fed Rate Pause Signal" and "PBOC RRR Cut" = DIFFERENT (different central banks)
- Pick the most specific, descriptive key as canonical
- Assign each cluster a sector from: geopolitical, macro, fed, credit, commodities, crypto, ai, equities, fiscal, fx
"""
```

### 4B. Execute the merge

After getting consolidation clusters, run a migration:

```python
async def execute_theme_consolidation(clusters: list[dict], db_path: str):
    """Merge duplicate themes into canonical anchors."""
    import sqlite3
    conn = sqlite3.connect(db_path)
    
    for cluster in clusters:
        canonical = cluster["canonical_key"]
        canonical_label = cluster["canonical_label"]
        sector = cluster["sector"]
        merged = cluster["merged_keys"]
        
        if not merged:
            # Solo theme — just set sector
            conn.execute(
                "UPDATE themes SET sector = ? WHERE theme_key = ?",
                (sector, canonical)
            )
            continue
        
        # Sum up mention counts from all merged keys
        placeholders = ",".join("?" * len(merged))
        total_mentions = conn.execute(f"""
            SELECT COALESCE(SUM(mention_count), 0) FROM themes 
            WHERE theme_key IN ({placeholders})
        """, merged).fetchone()[0]
        
        # Get earliest first_seen and latest last_seen across merged keys
        all_keys = [canonical] + merged
        all_placeholders = ",".join("?" * len(all_keys))
        bounds = conn.execute(f"""
            SELECT MIN(first_seen_at), MAX(last_seen_at), MAX(velocity_score)
            FROM themes WHERE theme_key IN ({all_placeholders})
        """, all_keys).fetchone()
        
        # Upsert canonical theme
        conn.execute("""
            INSERT INTO themes (theme_key, theme_label, sector, first_seen_at, last_seen_at, 
                               mention_count, velocity_score)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(theme_key) DO UPDATE SET
                sector = excluded.sector,
                mention_count = mention_count + excluded.mention_count,
                last_seen_at = MAX(last_seen_at, excluded.last_seen_at),
                velocity_score = MAX(velocity_score, excluded.velocity_score)
        """, (canonical, canonical_label, sector, bounds[0], bounds[1], total_mentions, bounds[2]))
        
        # Repoint document_themes from merged keys to canonical
        canonical_id = conn.execute(
            "SELECT id FROM themes WHERE theme_key = ?", (canonical,)
        ).fetchone()[0]
        
        for old_key in merged:
            old_row = conn.execute(
                "SELECT id FROM themes WHERE theme_key = ?", (old_key,)
            ).fetchone()
            if old_row:
                conn.execute(
                    "UPDATE document_themes SET theme_id = ? WHERE theme_id = ?",
                    (canonical_id, old_row[0])
                )
                # Delete the merged theme row
                conn.execute("DELETE FROM themes WHERE id = ?", (old_row[0],))
        
        logger.info(f"Consolidated {len(merged)} themes into '{canonical}' [{sector}], total mentions: {total_mentions}")
    
    conn.commit()
    conn.close()
```

### 4C. Clean up vault files

After DB consolidation:
1. Delete vault THEME files for merged keys
2. Regenerate the canonical theme's vault file with updated stats and sector frontmatter
3. Ensure files are owned by `proxmox` user

```bash
# Example: after merging 10 Hormuz variants into "iran-hormuz-oil-supply-disruption"
# Delete the old files:
rm /docker/obsidian/investing/Intelligence/Themes/THEME_strait-of-hormuz-closure-impact.md
rm /docker/obsidian/investing/Intelligence/Themes/THEME_geopolitical-risk-premium-deflation-in-energy.md
# ... etc

# The obsidian_writer will regenerate the canonical file on next mention
```

---

## STEP 5 — UPDATE OBSIDIAN WRITER

In `obsidian_writer.py`, add `sector:` to THEME note frontmatter:

```yaml
---
type: theme
theme_key: {theme_key}
sector: {sector}
first_seen: {first_seen}
...
---
```

---

## STEP 6 — SECTOR-BALANCED PUSH TO AESTIMA

Replace the flat top-20 push with sector-balanced selection:

```python
async def get_sector_balanced_themes(db_path: str, max_total: int = 20) -> list[dict]:
    """
    Pull top themes balanced across sectors.
    
    Strategy: 
    1. Get top 2 per sector (guarantees diversity)
    2. Fill remaining slots by raw velocity_score across all sectors
    3. Cap at max_total
    """
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    sectors = ["geopolitical", "macro", "fed", "credit", "commodities", 
               "crypto", "ai", "equities", "fiscal", "fx"]
    
    selected_keys = set()
    results = []
    
    # Step 1: top 2 per sector
    for sector in sectors:
        rows = conn.execute("""
            SELECT theme_key AS label, theme_label AS display_name,
                   mention_count AS mentions_7d, 
                   last_seen_at AS latest_mention, velocity_score, sector
            FROM themes 
            WHERE sector = ? 
              AND last_seen_at > datetime('now', '-7 days')
              AND mention_count >= 2
            ORDER BY velocity_score DESC 
            LIMIT 2
        """, (sector,)).fetchall()
        
        for r in rows:
            results.append(dict(r))
            selected_keys.add(r["label"])
    
    # Step 2: fill remaining with top velocity across all sectors
    remaining = max_total - len(results)
    if remaining > 0:
        placeholders = ",".join("?" * len(selected_keys)) if selected_keys else "'__none__'"
        fillers = conn.execute(f"""
            SELECT theme_key AS label, theme_label AS display_name,
                   mention_count AS mentions_7d,
                   last_seen_at AS latest_mention, velocity_score, sector
            FROM themes
            WHERE last_seen_at > datetime('now', '-7 days')
              AND mention_count >= 2
              AND theme_key NOT IN ({placeholders})
            ORDER BY velocity_score DESC
            LIMIT ?
        """, (*selected_keys, remaining)).fetchall()
        
        results.extend(dict(r) for r in fillers)
    
    conn.close()
    return results[:max_total]
```

### Push payload includes sector:
```python
payload = {
    "themes": [
        {
            "label": "iran-hormuz-oil-supply-disruption",
            "display_name": "Iran/Hormuz Oil Supply Disruption",
            "sector": "geopolitical",
            "mentions_7d": 76,  # consolidated from 10 duplicate themes
            "velocity_score": 92.3,
            "latest_mention": "2026-04-16T14:00:00Z"
        },
        {
            "label": "private-credit-redemption-stress",
            "display_name": "Private Credit Redemption Stress",
            "sector": "credit",
            "mentions_7d": 6,  # consolidated from 2 duplicate themes
            "velocity_score": 34.1,
            "latest_mention": "2026-04-16T14:00:00Z"
        },
        # ... balanced across sectors
    ],
    "pushed_at": "2026-04-16T19:00:00Z"
}
```

**Aestima side:** POST endpoint needs to accept `sector` and `display_name` fields. If it strictly validates, add `sector TEXT` and `display_name TEXT` to `remi_intel_themes` table. Next CC1 session.

---

## STEP 7 — AESTIMA DASHBOARD (CC2 follow-on)

Once sector data flows:
- Sector tabs: All | Geopol | Macro | Fed | Credit | Commodities | Crypto | AI | Equities | Fiscal | FX
- Sector pill/badge on each theme row (color-coded)
- Sector distribution mini-chart
- `display_name` renders instead of raw `theme_key` slug

---

## IMPLEMENTATION ORDER

| # | Step | Where | Blocking? | Effort |
|---|------|-------|-----------|--------|
| 1 | DB migration: add `sector` column | SQLite | Yes | 5 min |
| 2 | Build anchor query + prompt injection | `extraction_worker.py` | Yes | 30 min |
| 3 | Modify extraction prompt | `llm_extractor.py` | Yes | 15 min |
| 4 | Add `normalize_sector()` validation | `llm_extractor.py` | Yes | 10 min |
| 5 | One-time consolidation of existing themes | Script + Consuela | No* | 1 hr |
| 6 | Sector-balanced push function | `aestima_push.py` | Yes | 20 min |
| 7 | Update obsidian writer frontmatter | `obsidian_writer.py` | No | 10 min |
| 8A | Profile summary in dossier API | `remi_dashboard_api.py` | No | 15 min |
| 8B | Deep dive → Aestima Research push | CC1 + `signals_group_listener.py` | No | 1 hr |
| 9 | Aestima endpoint: accept sector field | CC1 session | No | 10 min |
| 10 | Dashboard sector tabs + Research display | CC2 session | No | 2 hr |

**Critical path:** 1 → 2 → 3 → 4 → 6 → then activate push.  
Step 5 (consolidation) is marked "No*" because new extractions will consolidate correctly after steps 2-3 ship. But running it cleans the historical mess and immediately improves the pushed data.

---

## WHAT THE DASHBOARD SHOULD LOOK LIKE AFTER

Instead of 21 rows of geopolitical noise, Aestima's Research tab should show something like:

```
🟠 GEOPOLITICAL
  Iran/Hormuz Oil Supply Disruption          76 mentions   velocity: 92.3   2h ago
  Strategic Realignment & Hardening Alliances  3 mentions   velocity: 12.1   11h ago

🔵 CREDIT  
  Private Credit Redemption Stress             6 mentions   velocity: 34.1   2h ago

🟢 COMMODITIES
  Global LNG Export Collapse                   4 mentions   velocity: 18.7   3d ago
  War-Driven Energy & Logistics Disruption     3 mentions   velocity: 11.2   5d ago

🟡 FED
  (nothing this week — feed gap, add sources)

🟣 CRYPTO
  (nothing this week — feed gap, add sources)

⚪ AI
  (nothing this week — feed gap, add sources)
```

Now you can see at a glance: Iran/Hormuz is the dominant story (correctly consolidated), but credit stress is emerging, and you have zero coverage on fed/crypto/AI — which is a feed curation problem to fix separately.

---

## STEP 8 — PROFILE + DEEP DIVE CONTENT FLOW TO AESTIMA

The theme push (Steps 1-7) handles the **narrative intelligence layer**. But Remi also generates two types of **ticker-level research content** that Aestima should consume:

### Current state (what exists)

| Component | Status | Where |
|-----------|--------|-------|
| `/profile {TICKER}` | Live | Remi pulls Aestima modules (04, 06, 08) + vault context → GLM-5 synthesis → intel dashboard |
| `/deepdive {TICKER}` | Live | Same but 6 modules (02, 03, 04, 06, 08, 09) + extended sections |
| `analysis_post_builder.py` | Live | GLM-5 synthesis engine producing subscriber-readable posts |
| `ticker_analysis_posts` table | Live | PostgreSQL on Remi dashboard, stores posts with pending/approved/killed status |
| Dossier API | Live | `GET :8501/api/watchlist/dossier/{ticker}` — Aestima pulls this for module prompt injection |
| `pushed_to_aestima` column | Exists | Boolean on `ticker_analysis_posts` — never set to true yet |

### What should flow to Aestima

Two separate flows, matching the two content types:

#### Flow A: Profile → Aestima Analysis Engine Context

**Purpose:** When Remi runs `/profile LMAT`, the GLM-5 synthesis produces a compact analysis (Why We're Looking, The Business, The Numbers, The Chart, The Verdict). This should feed back into Aestima's analysis engine as **enriched context** — similar to how the dossier currently feeds in, but with the synthesized profile instead of raw watchlist data.

**Mechanism:** Extend the existing dossier API response to include the latest approved profile post.

```python
# In remi-dashboard-api, update GET /api/watchlist/dossier/{ticker}
# Current response:
{
    "ticker": "PROP",
    "on_watchlist": true,
    "dossier_telegram": "...",
    "dossier_prompt": "=== REMI TICKER DOSSIER: PROP ===\n...",
    "intelligence": { "themes": [...], ... }
}

# New response — add profile_summary:
{
    "ticker": "PROP",
    "on_watchlist": true,
    "dossier_telegram": "...",
    "dossier_prompt": "=== REMI TICKER DOSSIER: PROP ===\n...",
    "intelligence": { "themes": [...], ... },
    "profile_summary": {
        "content": "## Why We're Looking\n...",  # latest approved profile post_content
        "conviction_score": 7,
        "gli_phase": "TURBULENCE",
        "created_at": "2026-04-16T14:00:00Z",
        "stale": false  # true if > 7 days old
    }
}
```

**Aestima side (`remi_client.py`):** Already fetches the dossier endpoint. Extend it to check for `profile_summary` and append a trimmed version to the dossier prompt injection:

```python
# In engine.py prompt assembly, after the existing dossier block:
if dossier.get("profile_summary") and not dossier["profile_summary"]["stale"]:
    prompt += f"\n=== REMI PROFILE ANALYSIS ({ticker}) ===\n"
    prompt += dossier["profile_summary"]["content"][:2000]  # cap to prevent prompt bloat
    prompt += f"\nConviction: {dossier['profile_summary']['conviction_score']}/10"
    prompt += f"\n=== END REMI PROFILE ===\n"
```

**Effect:** Every Aestima module run for a ticker that has an approved Remi profile now sees both the static dossier (thesis, catalysts, risks) AND the synthesized profile (business quality, chart setup, earnings analysis). The analysis engine gets Remi's full picture.

**Remi-side implementation:**
```python
# In remi_dashboard_api.py, in the dossier endpoint handler:
def get_dossier(ticker):
    # ... existing dossier assembly ...
    
    # Add latest approved profile
    profile = db.execute("""
        SELECT post_content, conviction_score, gli_phase, created_at
        FROM ticker_analysis_posts
        WHERE ticker = %s AND analysis_type = 'profile' AND status = 'approved'
        ORDER BY created_at DESC LIMIT 1
    """, (ticker,)).fetchone()
    
    if profile:
        age_hours = (datetime.utcnow() - profile["created_at"]).total_seconds() / 3600
        result["profile_summary"] = {
            "content": profile["post_content"],
            "conviction_score": profile["conviction_score"],
            "gli_phase": profile["gli_phase"],
            "created_at": profile["created_at"].isoformat(),
            "stale": age_hours > 168  # 7 days
        }
    
    return result
```

#### Flow B: Deep Dive → Aestima Research Section (publish)

**Purpose:** When Remi runs `/deepdive LMAT` and the post is approved, it should publish to Aestima's Research tab as a full research article. This is the **subscriber-facing content** — the kind of thing you'd put on a Substack or research portal.

**Mechanism:** New POST endpoint on Aestima, called when a deep dive is approved.

**New Aestima endpoint (CC1 build):**
```
POST /api/agent/remi-intel/research
Auth: X-Agent-Key (agent_pro)
```

**Payload:**
```json
{
    "ticker": "LMAT",
    "company": "LeMaitre Vascular",
    "analysis_type": "deep_dive",
    "title": "LeMaitre Vascular — Niche Surgical Play With Pricing Power",
    "content": "## Why We're Looking\n...\n## The Verdict\n...",
    "conviction_score": 7,
    "gli_phase": "TURBULENCE",
    "steno_regime": "GOLDILOCKS",
    "modules_used": ["02", "03", "04", "06", "08", "09"],
    "sector": "equities",
    "published_at": "2026-04-16T18:00:00Z"
}
```

**Response:**
```json
{
    "status": "ok",
    "research_id": "<uuid>",
    "published": true
}
```

**Aestima DB (migration):**
```sql
CREATE TABLE remi_intel_research (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticker VARCHAR(20) NOT NULL,
    company VARCHAR(255),
    analysis_type VARCHAR(50) DEFAULT 'deep_dive',
    title VARCHAR(500),
    content TEXT NOT NULL,
    conviction_score INTEGER,
    gli_phase VARCHAR(50),
    steno_regime VARCHAR(50),
    modules_used JSONB,
    sector VARCHAR(50),
    published_at TIMESTAMPTZ,
    received_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(ticker, published_at)  -- prevent duplicate pushes
);
CREATE INDEX idx_remi_research_ticker ON remi_intel_research(ticker);
CREATE INDEX idx_remi_research_sector ON remi_intel_research(sector);
```

**Remi-side trigger — wire into approve flow:**

In `signals_group_listener.py`, the `/approve {TICKER}` handler currently:
1. Marks the post as approved
2. Adds to watchlist
3. Queues thesis eval

Add step 3.5: if the approved post is a `deep_dive`, push to Aestima Research:

```python
# In the approve handler, after marking approved:
if post["analysis_type"] == "deep_dive":
    await push_deep_dive_to_aestima(post)

async def push_deep_dive_to_aestima(post: dict):
    """Push approved deep dive to Aestima Research section."""
    payload = {
        "ticker": post["ticker"],
        "company": post["company"],
        "analysis_type": "deep_dive",
        "title": f"{post['company']} — Deep Dive Analysis",
        "content": post["post_content"],
        "conviction_score": post["conviction_score"],
        "gli_phase": post["gli_phase"],
        "steno_regime": post.get("steno_regime"),
        "modules_used": list(post.get("module_data", {}).keys()),
        "sector": classify_ticker_sector(post["ticker"]),  # or from watchlist
        "published_at": datetime.now(timezone.utc).isoformat(),
    }
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{AESTIMA_BASE}/api/agent/remi-intel/research",
                headers={"X-Agent-Key": AESTIMA_KEY, "Content-Type": "application/json"},
                json=payload,
            )
            if resp.status_code == 200:
                # Mark as pushed
                db.execute(
                    "UPDATE ticker_analysis_posts SET pushed_to_aestima = true WHERE id = %s",
                    (post["id"],)
                )
                logger.info(f"Deep dive for {post['ticker']} published to Aestima Research")
            else:
                logger.warning(f"Aestima research push failed: {resp.status_code}")
    except Exception as e:
        logger.warning(f"Aestima research push error: {e}")
```

**Aestima Research tab display (CC2):**
- Research section shows published deep dives as full-length articles
- Grouped by sector or chronological
- Each article shows: ticker, conviction badge, GLI phase at time of writing, modules used
- Clickable to expand full subscriber-readable analysis
- "Remi Intelligence" branding to distinguish from Aestima's native module outputs

### Summary: The Three Aestima Push Channels

After this spec is fully implemented, Remi pushes three types of intelligence to Aestima:

| Channel | What | Endpoint | Cadence | Blocking? |
|---------|------|----------|---------|-----------|
| **Themes** | Top 20 sector-balanced themes | `POST /api/agent/remi-intel/themes` | Every 4h | Steps 1-7 of this spec |
| **Convergence** | Risk-on/off signal alignment | `POST /api/agent/remi-intel/convergence` | Event-driven | Already in handoff |
| **Research** | Approved deep dive articles | `POST /api/agent/remi-intel/research` | On `/approve` | Step 8B (new endpoint needed) |

Plus the existing **pull** channel that now includes profile context:

| Channel | What | Endpoint | Cadence |
|---------|------|----------|---------|
| **Dossier** | Thesis + intel + profile summary | `GET :8501/api/watchlist/dossier/{ticker}` | On every Aestima module run |

### Implementation for Step 8

| Sub-step | What | Where | Effort |
|----------|------|-------|--------|
| 8A | Add `profile_summary` to dossier API response | `remi_dashboard_api.py` | 15 min |
| 8B-1 | Create research POST endpoint on Aestima | CC1 session | 30 min |
| 8B-2 | Create `remi_intel_research` table migration | CC1 session | 10 min |
| 8B-3 | Wire approve handler to push deep dives | `signals_group_listener.py` | 20 min |
| 8B-4 | Research tab display | CC2 session | 1 hr |

---

## FEED COMPOSITION CHECK (if sector imbalance persists)

After consolidation + sector tagging, run:
```sql
SELECT sector, COUNT(*) as theme_count, 
       ROUND(AVG(velocity_score), 1) as avg_velocity
FROM themes 
WHERE last_seen_at > datetime('now', '-7 days')
  AND mention_count >= 2
GROUP BY sector 
ORDER BY theme_count DESC;
```

If sectors have 0 themes, the problem is feed composition. Candidate RSS additions:
- **Fed/Credit:** Fed speeches RSS, FRED blog, BIS quarterly, Moody's credit outlook
- **AI:** SemiAnalysis, The Information, Import AI, Stratechery
- **Crypto:** The Block, Bankless, Messari
- **FX:** BIS FX committee papers, central bank speeches
- **Equities:** Earnings Whispers, Validea, Finviz RSS

---

## STEP 9 — ENGAGEMENT-WEIGHTED VELOCITY

### Problem
All mentions are treated equally. A ZeroHedge article with 500 retweets and a random RSS blurb with 0 engagement have the same velocity weight. High-engagement content is where the narrative is actually catching fire — retweets, likes, views, replies are signals of narrative saturation that should amplify velocity scoring.

### Current state
- X Scout (`x_scout.py` line 105-106) already captures `retweets` and `views` per tweet
- These fields are used during processing but **never persisted** to SQLite
- The `documents` table has no engagement columns
- `velocity_scorer.py` weights by source tier and recency only — no engagement factor

### 9A. Persist engagement data

**DB migration:**
```sql
ALTER TABLE documents ADD COLUMN retweets INTEGER DEFAULT 0;
ALTER TABLE documents ADD COLUMN views INTEGER DEFAULT 0;
ALTER TABLE documents ADD COLUMN likes INTEGER DEFAULT 0;
ALTER TABLE documents ADD COLUMN replies INTEGER DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_documents_engagement ON documents(retweets DESC);
```

**Wire into ingestion path:**
Wherever X Scout tweets get inserted into `documents` (likely in `extraction_worker.py` or `signals_group_listener.py`), pass through the engagement fields:

```python
# Find the INSERT INTO documents statement for X Scout content
# Add retweets, views, likes, replies to the INSERT columns
# These come from the tweet dict that x_scout.fetch_tweets() returns
```

Also capture `likes` and `replies` in x_scout.py if not already:
```python
# In x_scout.py fetch_tweets(), add:
"likes": int(t.get("favorite_count", t.get("likes", 0))),
"replies": int(t.get("reply_count", t.get("replies", 0))),
```

### 9B. Engagement-weighted velocity scoring

**In `velocity_scorer.py`, add an engagement multiplier:**

```python
def engagement_multiplier(retweets: int, views: int, likes: int = 0) -> float:
    """
    Boost velocity contribution based on social engagement.
    
    Tiers:
      - Viral (1000+ RTs or 100K+ views): 3.0x
      - High engagement (100+ RTs or 10K+ views): 2.0x  
      - Moderate (10+ RTs or 1K+ views): 1.5x
      - Normal: 1.0x (no boost)
    
    This is a multiplier on the existing tier_weight * recency_weight contribution.
    """
    if retweets >= 1000 or views >= 100_000:
        return 3.0
    elif retweets >= 100 or views >= 10_000:
        return 2.0
    elif retweets >= 10 or views >= 1_000:
        return 1.5
    return 1.0
```

**Modify the velocity calculation loop:**
```python
# Current:
contribution = tier_w * recency_w * 10

# New:
engagement_w = engagement_multiplier(m.get("retweets", 0), m.get("views", 0), m.get("likes", 0))
contribution = tier_w * recency_w * engagement_w * 10
```

This means a Tier-1 source tweet with 500 retweets gets 2x the velocity contribution of the same source with 0 retweets. A viral tweet (1000+ RTs) from a Tier-3 source can outweigh a quiet Tier-1 post — which is correct, because the crowd response IS the signal.

### 9C. Push engagement data to Aestima

Add `top_engagement` field to theme push payload — the highest-engagement article for each theme:

```python
# In the theme push payload, add per theme:
{
    "label": "iran-hormuz-oil-supply-disruption",
    "sector": "geopolitical",
    "mentions_7d": 76,
    "velocity_score": 92.3,
    "top_article": {
        "title": "Iran threatens Hormuz closure as sanctions bite",
        "source": "ZeroHedge",
        "retweets": 847,
        "views": 124000,
        "url": "https://..."
    }
}
```

This lets the Aestima dashboard show "most engaged article" per theme — the one people are actually reacting to.

---

## STEP 10 — SECTOR-LEVEL VELOCITY (trend detection)

### Problem
Theme-level velocity shows individual topic momentum. But there's a higher-order signal: **sector-level acceleration**. If energy themes went from 5 total mentions last week to 35 this week, something structural is happening in energy — regardless of which specific theme is driving it. The dashboard should surface sector trends.

### 10A. Sector velocity table

**New SQLite table:**
```sql
CREATE TABLE sector_velocity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sector TEXT NOT NULL,
    period_start TEXT NOT NULL,          -- ISO date
    period_end TEXT NOT NULL,
    theme_count INTEGER DEFAULT 0,       -- unique themes active in this period
    mention_count INTEGER DEFAULT 0,     -- total mentions across all themes
    avg_velocity REAL DEFAULT 0.0,       -- average velocity score
    max_velocity REAL DEFAULT 0.0,       -- peak velocity (hottest theme)
    top_theme_key TEXT,                  -- key of highest-velocity theme
    engagement_total INTEGER DEFAULT 0,  -- sum of retweets+views across sector
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(sector, period_start)
);
CREATE INDEX IF NOT EXISTS idx_sector_velocity_sector ON sector_velocity(sector);
```

### 10B. Sector velocity calculator (scheduler job)

```python
async def calculate_sector_velocity():
    """
    Run daily (or every 4h with theme push). Calculates per-sector:
    - 7-day mention count and theme count
    - 7-day-ago comparison (delta)
    - Sector acceleration flag when delta > 50%
    """
    conn = sqlite3.connect(DB_PATH)
    
    sectors = ["geopolitical", "macro", "fed", "credit", "energy",
               "metals", "agriculture", "crypto", "ai", "equities", "fiscal", "fx"]
    
    for sector in sectors:
        # Current 7-day window
        current = conn.execute("""
            SELECT COUNT(DISTINCT theme_key) as theme_count,
                   SUM(mention_count) as mention_count,
                   ROUND(AVG(velocity_score), 1) as avg_velocity,
                   MAX(velocity_score) as max_velocity
            FROM themes
            WHERE sector = ? AND last_seen_at > datetime('now', '-7 days')
              AND mention_count >= 2
        """, (sector,)).fetchone()
        
        # Prior 7-day window (8-14 days ago)
        prior = conn.execute("""
            SELECT SUM(mention_count) as mention_count
            FROM themes
            WHERE sector = ? 
              AND last_seen_at BETWEEN datetime('now', '-14 days') AND datetime('now', '-7 days')
              AND mention_count >= 2
        """, (sector,)).fetchone()
        
        current_mentions = current[1] or 0
        prior_mentions = prior[0] or 0
        
        # Sector acceleration detection
        if prior_mentions > 0:
            acceleration = (current_mentions - prior_mentions) / prior_mentions
        else:
            acceleration = 1.0 if current_mentions > 0 else 0.0
        
        if acceleration > 0.5:
            logger.info(f"SECTOR ACCELERATION: {sector} up {acceleration:.0%} week-over-week "
                       f"({prior_mentions} → {current_mentions} mentions)")
        
        # Persist
        conn.execute("""
            INSERT INTO sector_velocity (sector, period_start, period_end, 
                                        theme_count, mention_count, avg_velocity, max_velocity)
            VALUES (?, datetime('now', '-7 days'), datetime('now'),
                    ?, ?, ?, ?)
            ON CONFLICT(sector, period_start) DO UPDATE SET
                mention_count = excluded.mention_count,
                theme_count = excluded.theme_count,
                avg_velocity = excluded.avg_velocity,
                max_velocity = excluded.max_velocity
        """, (sector, current[0] or 0, current_mentions, current[2] or 0, current[3] or 0))
    
    conn.commit()
    conn.close()
```

### 10C. Sector velocity in push payload

Add a `sector_summary` block to the theme push:

```python
# Alongside the themes array, push sector-level stats:
payload = {
    "themes": [...],
    "sector_summary": [
        {
            "sector": "energy",
            "mentions_7d": 240,
            "mentions_prior_7d": 85,
            "acceleration": 1.82,  # +182% week-over-week
            "theme_count": 64,
            "avg_velocity": 42.1,
            "status": "accelerating"  # accelerating / stable / cooling
        },
        {
            "sector": "credit",
            "mentions_7d": 8,
            "mentions_prior_7d": 2,
            "acceleration": 3.0,  # +300% — early signal!
            "theme_count": 4,
            "avg_velocity": 17.4,
            "status": "accelerating"
        },
        ...
    ],
    "pushed_at": "2026-04-16T19:00:00Z"
}
```

**This is where you find alpha.** If credit goes from 2 mentions to 8 mentions in a week (+300%), that's a sector-level early warning that something is brewing in credit markets — even though no single credit theme has broken into the top 10. The sector velocity catches what individual theme velocity misses.

### 10D. Aestima dashboard sector display

The Research tab can show:

```
SECTOR VELOCITY (7d vs prior 7d)

🔴 ENERGY        240 mentions  ↑182%  ACCELERATING
🔴 CREDIT          8 mentions  ↑300%  ACCELERATING  ← EARLY SIGNAL
🟢 GEOPOLITICAL  700 mentions  ↓ 12%  COOLING
🟡 MACRO          45 mentions  → flat  STABLE
⚪ CRYPTO          21 mentions  → flat  STABLE
⚪ AI              10 mentions  → flat  STABLE
```

The color and direction arrow instantly tell you where narrative attention is shifting — which sectors are heating up (potential alpha) and which are cooling down (priced in).

### 10E. Sector sentiment drift (narrative regime change detection)

**Why this matters:** Velocity tells you a sector is getting attention. Sentiment drift tells you the *direction* of that attention is changing. Energy can have high velocity in both directions — but if sentiment flips from 60% bullish to 80% bearish in a week, the narrative regime is turning. Price often lags narrative by days or weeks. This is the early warning.

**Data already available:** Every `document_themes` row has a `sentiment` field (bullish/bearish/neutral/mixed) from the extraction prompt. This just needs to be aggregated by sector over time windows.

**Add to sector_velocity table:**
```sql
ALTER TABLE sector_velocity ADD COLUMN sentiment_bullish_pct REAL DEFAULT 0.0;
ALTER TABLE sector_velocity ADD COLUMN sentiment_bearish_pct REAL DEFAULT 0.0;
ALTER TABLE sector_velocity ADD COLUMN sentiment_mixed_pct REAL DEFAULT 0.0;
ALTER TABLE sector_velocity ADD COLUMN sentiment_drift TEXT;  -- 'turning_bullish' / 'turning_bearish' / 'stable' / 'diverging'
ALTER TABLE sector_velocity ADD COLUMN prior_bullish_pct REAL DEFAULT 0.0;
ALTER TABLE sector_velocity ADD COLUMN prior_bearish_pct REAL DEFAULT 0.0;
```

**Sentiment drift calculator (add to `calculate_sector_velocity()`):**

```python
def calculate_sentiment_drift(conn, sector: str) -> dict:
    """
    Calculate sentiment distribution for a sector across two time windows.
    Returns current vs prior sentiment mix and drift classification.
    """
    # Current 7-day window
    current = conn.execute("""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN dt.sentiment = 'bullish' THEN 1 ELSE 0 END) as bullish,
            SUM(CASE WHEN dt.sentiment = 'bearish' THEN 1 ELSE 0 END) as bearish,
            SUM(CASE WHEN dt.sentiment = 'mixed' THEN 1 ELSE 0 END) as mixed,
            SUM(CASE WHEN dt.sentiment = 'neutral' THEN 1 ELSE 0 END) as neutral
        FROM document_themes dt
        JOIN themes t ON dt.theme_id = t.id
        WHERE t.sector = ?
          AND dt.extracted_at > datetime('now', '-7 days')
    """, (sector,)).fetchone()
    
    # Prior 7-day window (8-14 days ago)
    prior = conn.execute("""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN dt.sentiment = 'bullish' THEN 1 ELSE 0 END) as bullish,
            SUM(CASE WHEN dt.sentiment = 'bearish' THEN 1 ELSE 0 END) as bearish
        FROM document_themes dt
        JOIN themes t ON dt.theme_id = t.id
        WHERE t.sector = ?
          AND dt.extracted_at BETWEEN datetime('now', '-14 days') AND datetime('now', '-7 days')
    """, (sector,)).fetchone()
    
    # Calculate percentages
    c_total = current[0] or 1  # avoid division by zero
    c_bull_pct = round((current[1] or 0) / c_total * 100, 1)
    c_bear_pct = round((current[2] or 0) / c_total * 100, 1)
    c_mixed_pct = round((current[3] or 0) / c_total * 100, 1)
    
    p_total = prior[0] or 1
    p_bull_pct = round((prior[1] or 0) / p_total * 100, 1)
    p_bear_pct = round((prior[2] or 0) / p_total * 100, 1)
    
    # Classify drift
    bull_shift = c_bull_pct - p_bull_pct
    bear_shift = c_bear_pct - p_bear_pct
    
    if bull_shift > 15 and bear_shift < -10:
        drift = "turning_bullish"
    elif bear_shift > 15 and bull_shift < -10:
        drift = "turning_bearish"
    elif bull_shift > 10 and bear_shift > 10:
        drift = "diverging"  # both bull and bear rising = contested narrative
    else:
        drift = "stable"
    
    return {
        "bullish_pct": c_bull_pct,
        "bearish_pct": c_bear_pct,
        "mixed_pct": c_mixed_pct,
        "prior_bullish_pct": p_bull_pct,
        "prior_bearish_pct": p_bear_pct,
        "drift": drift,
        "bull_shift": round(bull_shift, 1),
        "bear_shift": round(bear_shift, 1),
    }
```

**Drift classifications:**
- `turning_bullish` — bullish sentiment rising >15pp, bearish falling >10pp. The narrative is swinging positive.
- `turning_bearish` — bearish sentiment rising >15pp, bullish falling >10pp. The narrative is souring.
- `diverging` — both bullish AND bearish rising (neutral/mixed dropping). The sector is becoming contested — strong opinions forming on both sides. This often precedes a big move.
- `stable` — sentiment mix roughly unchanged week-over-week.

**The `diverging` state is particularly valuable.** When you see a sector where both bulls and bears are getting louder, that's a contested narrative — consensus hasn't formed yet. That's where the thesis opportunity is, because one side will be wrong.

**Add sentiment to sector_summary push payload:**

```python
{
    "sector": "energy",
    "mentions_7d": 240,
    "acceleration": 1.82,
    "status": "accelerating",
    "sentiment": {
        "bullish_pct": 35.0,
        "bearish_pct": 55.0,
        "mixed_pct": 10.0,
        "drift": "turning_bearish",
        "bull_shift": -18.5,  # was 53.5% last week
        "bear_shift": +22.0   # was 33.0% last week
    }
}
```

**Aestima dashboard display — enhanced:**

```
SECTOR INTELLIGENCE (7d vs prior 7d)

🔴 ENERGY        240 mentions  ↑182%  ACCELERATING  📉 TURNING BEARISH (55% bear, was 33%)
🔴 CREDIT          8 mentions  ↑300%  ACCELERATING  ⚡ DIVERGING (bull+bear both rising)
🟢 GEOPOLITICAL  700 mentions  ↓ 12%  COOLING       → STABLE (70% bearish, unchanged)
🟡 MACRO          45 mentions  → flat  STABLE        📈 TURNING BULLISH (60% bull, was 42%)
⚪ CRYPTO          21 mentions  → flat  STABLE        → STABLE
⚪ AI              10 mentions  → flat  STABLE        → STABLE
```

Now you can read this at a glance: energy is getting MORE attention AND the sentiment is souring — that's a narrative regime change. Credit is accelerating AND diverging — contested thesis, alpha opportunity. Macro is stable in volume but turning bullish — the crowd is getting optimistic on the economy even though nobody's writing more about it.

**Engagement-weighted sentiment (Step 9 + 10E combined):**

Once engagement data is flowing (Step 9A done), weight the sentiment aggregation by engagement too:

```python
# Instead of COUNT(*) for sentiment, weight by engagement:
SUM(CASE WHEN dt.sentiment = 'bullish' 
    THEN (1.0 + LOG(1 + COALESCE(d.retweets, 0) + COALESCE(d.likes, 0))) 
    ELSE 0 END) as bullish_weighted
```

This means a bearish article with 500 retweets counts more toward the bearish percentage than a bearish article with 2 retweets. The crowd's *amplification* of a sentiment direction is the real signal, not just the article count.

---

## UPDATED IMPLEMENTATION ORDER

| # | Step | Where | Blocking? | Effort |
|---|------|-------|-----------|--------|
| 1 | DB migration: add `sector` column | SQLite | ✅ Done | — |
| 2 | Build anchor query + prompt injection | `extraction_worker.py` | ✅ Done | — |
| 3 | Modify extraction prompt | `llm_extractor.py` | ✅ Done | — |
| 4 | Add `normalize_sector()` validation | `llm_extractor.py` | ✅ Done | — |
| 5 | One-time theme consolidation | Script + GLM-4.7 | ✅ Done | — |
| 6 | Sector-balanced push function | `aestima_push.py` | ✅ Done | — |
| 6b | Split commodities → energy/metals/agriculture | `llm_extractor.py` + `aestima_push.py` | ✅ Done | — |
| 7 | Update obsidian writer frontmatter | `obsidian_writer.py` | No | 10 min |
| 8A | Profile summary in dossier API | `remi_dashboard_api.py` | No | 15 min |
| 8B | Deep dive → Aestima Research push | CC1 + `signals_group_listener.py` | No | 1 hr |
| 9A | Persist engagement data (retweets/views) | `documents` table + ingestion path | ✅ Done | — |
| 9B | Engagement-weighted velocity scoring | `velocity_scorer.py` | ✅ Done | — |
| 9C | Top engagement article in push payload | `aestima_push.py` | No | 15 min |
| 10A | Sector velocity table + calculator | New module + scheduler | No | 45 min |
| 10B | Sector velocity in push payload | `aestima_push.py` | No | 15 min |
| 10C | Aestima dashboard sector display | CC2 frontend | No | 1 hr |
| 10D | Sector sentiment drift calculator | `sector_velocity` module | No | 30 min |
| 10E | Sentiment drift in push payload + dashboard | `aestima_push.py` + CC2 frontend | No | 30 min |
| 10F | Engagement-weighted sentiment aggregation | `sector_velocity` module | No | 15 min |
| 11 | Aestima endpoint: accept sector field | CC1 session | No | 10 min |
| 12 | Dashboard sector tabs + Research display | CC2 session | No | 2 hr |

---

*Spec: April 16-17, 2026*  
*Prerequisite for: Clean Aestima push activation + Research publishing*  
*Depends on: llm_extractor.py, extraction_worker.py, themes table, aestima_push.py, analysis_post_builder.py, velocity_scorer.py, x_scout.py*  
*Supersedes: remi-theme-sector-taxonomy-spec.md (v1, sector-only)*  
*CC1 work needed: Steps 8B-1, 8B-2, 11 (research endpoint + sector field acceptance)*  
*CC2 work needed: Steps 9C, 10 (sector velocity + sentiment drift), Step 12 (dashboard)*
