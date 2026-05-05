# Vault Triage System — Consuela → Remi → MG
**Date:** April 15, 2026
**Philosophy:** Resident → Fellow → Attending. Consuela finds problems, Remi recommends actions, MG approves.

---

## THE HIERARCHY

```
┌─────────────────────────────────────────────────────┐
│  CONSUELA (Resident) — 2am nightly                  │
│  Scans vault. Detects issues. Never acts on them.   │
│  Writes triage report. Flags severity.              │
│  Output: Intelligence/_triage/TRIAGE_YYYY-MM-DD.md  │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│  REMI (Fellow) — 7am morning brief or on-demand     │
│  Reviews triage report. Adds recommendations.       │
│  "These two themes should merge because..."         │
│  "This orphan is junk — safe to delete because..."  │
│  "This broken link references a renamed file..."    │
│  Output: Telegram summary + enriched triage file    │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│  MG (Attending) — anytime via Telegram              │
│  /vault triage — see pending items                  │
│  /vault approve <id> — execute Remi's recommendation│
│  /vault reject <id> — dismiss with reason           │
│  /vault approve all — bulk approve                  │
│  Final authority. Nothing changes without sign-off. │
└─────────────────────────────────────────────────────┘
```

---

## FILE 1: Triage Report Format

**Location:** `Intelligence/_triage/TRIAGE_YYYY-MM-DD.md`
**Ownership:** `proxmox` user (for LiveSync)

```markdown
---
date: 2026-04-15
scanned_by: consuela
status: pending_review
items_total: 15
items_resolved: 0
---

## Vault Triage — April 15, 2026

### MERGE CANDIDATES (themes with >60% content overlap)

- [ ] **T001** | MERGE | `THEME_oil_supply_disruption.md` + `THEME_hormuz_blockade.md`
  - Overlap: 78% (shared tickers: CL, XLE, CANE; shared narrative: supply shock)
  - Recommendation: pending (Remi)
  - Status: pending

- [ ] **T002** | MERGE | `THEME_fed_liquidity_drain.md` + `THEME_sofr_stress.md`
  - Overlap: 65% (shared concepts: QT, reserves, funding stress)
  - Recommendation: pending (Remi)
  - Status: pending

### ORPHAN NOTES (no inbound links, <3 mentions in DB)

- [ ] **T003** | ORPHAN | `THEME_random_coin_pump.md`
  - Mentions in DB: 1
  - Last updated: 2026-03-20
  - Recommendation: pending (Remi)
  - Status: pending

- [ ] **T004** | ORPHAN | `THEME_japan_yield_curve.md`
  - Mentions in DB: 2
  - Last updated: 2026-03-28
  - Recommendation: pending (Remi)
  - Status: pending

### BROKEN LINKS (references to notes that don't exist)

- [ ] **T005** | BROKEN_LINK | `THEME_crypto_regulation.md` → `[[SEC_enforcement_timeline]]`
  - Target does not exist
  - Recommendation: pending (Remi)
  - Status: pending

### MISSING FRONTMATTER

- [ ] **T006** | FRONTMATTER | `SecondOrder/second_order_tariff_impact.md`
  - No YAML frontmatter block
  - Status: pending

### STALE THEMES (no new mentions in 30+ days)

- [ ] **T007** | STALE | `THEME_svb_contagion.md`
  - Last mention: 2026-02-15 (60 days ago)
  - Total mentions: 4
  - Recommendation: pending (Remi)
  - Status: pending
```

---

## FILE 2: Consuela Vault Hygiene Upgrade

**Modify:** `~/remi-intelligence/src/consuela_overnight.py` — replace current `vault_hygiene()` with active triage generation.

### What Consuela scans for (resident-level scut work):

**1. Merge candidates** — find THEME files with overlapping tickers and narrative keywords.
```python
def _detect_merge_candidates(vault_path: str) -> list:
    """Compare all THEME_*.md files for content overlap."""
    themes = {}
    for md in Path(vault_path).glob("Themes/THEME_*.md"):
        content = md.read_text(errors="replace")
        # Extract tickers mentioned
        tickers = set(re.findall(r'\b[A-Z]{2,5}\b', content))
        # Extract key phrases (simple: first 500 chars as fingerprint)
        themes[md.name] = {"path": md, "tickers": tickers, "content": content}

    candidates = []
    theme_names = list(themes.keys())
    for i, name_a in enumerate(theme_names):
        for name_b in theme_names[i+1:]:
            a, b = themes[name_a], themes[name_b]
            # Ticker overlap
            shared_tickers = a["tickers"] & b["tickers"]
            if len(shared_tickers) < 2:
                continue
            # Simple content similarity (Jaccard on word sets)
            words_a = set(a["content"].lower().split())
            words_b = set(b["content"].lower().split())
            overlap = len(words_a & words_b) / max(len(words_a | words_b), 1)
            if overlap > 0.3:  # 30% word overlap threshold
                candidates.append({
                    "type": "MERGE",
                    "files": [name_a, name_b],
                    "shared_tickers": list(shared_tickers)[:5],
                    "overlap_pct": round(overlap * 100),
                })
    return candidates
```

**2. Orphan notes** — no inbound wiki-links AND low mention count in DB.
```python
def _detect_orphans(vault_path: str, db_path: str) -> list:
    """Find notes with no inbound links and few DB mentions."""
    vault = Path(vault_path)
    all_notes = {f.stem: f for f in vault.rglob("*.md")}

    # Build inbound link map
    inbound = {name: 0 for name in all_notes}
    for md in vault.rglob("*.md"):
        content = md.read_text(errors="replace")
        links = re.findall(r'\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]', content)
        for lnk in links:
            target = lnk.strip()
            if target in inbound:
                inbound[target] += 1

    # Check DB mention counts for zero-inbound notes
    conn = sqlite3.connect(db_path)
    orphans = []
    for name, count in inbound.items():
        if count > 0:
            continue
        if name.startswith("_") or name in ("README", "INDEX", "Home"):
            continue
        # Check DB
        cur = conn.execute(
            "SELECT COUNT(*) FROM document_themes WHERE theme_label LIKE ?",
            (f"%{name.replace('THEME_', '')}%",)
        )
        db_mentions = cur.fetchone()[0]
        if db_mentions < 3:
            orphans.append({
                "type": "ORPHAN",
                "file": name,
                "path": str(all_notes[name].relative_to(vault)),
                "db_mentions": db_mentions,
                "last_modified": datetime.fromtimestamp(
                    all_notes[name].stat().st_mtime
                ).strftime("%Y-%m-%d"),
            })
    conn.close()
    return orphans
```

**3. Broken links** — wiki-links pointing to nonexistent notes.
```python
def _detect_broken_links(vault_path: str) -> list:
    """Find [[links]] to notes that don't exist."""
    vault = Path(vault_path)
    all_notes = {f.stem for f in vault.rglob("*.md")}
    broken = []
    for md in vault.rglob("*.md"):
        content = md.read_text(errors="replace")
        links = re.findall(r'\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]', content)
        for lnk in links:
            target = lnk.strip()
            if target not in all_notes:
                broken.append({
                    "type": "BROKEN_LINK",
                    "source": md.name,
                    "target": target,
                })
    return broken
```

**4. Stale themes** — THEME files with no new DB mentions in 30+ days.
```python
def _detect_stale(vault_path: str, db_path: str, stale_days: int = 30) -> list:
    """Find THEME files with no recent DB activity."""
    vault = Path(vault_path)
    conn = sqlite3.connect(db_path)
    stale = []
    for md in vault.glob("Themes/THEME_*.md"):
        theme_key = md.stem.replace("THEME_", "")
        cur = conn.execute(
            "SELECT MAX(created_at) FROM document_themes WHERE theme_label LIKE ?",
            (f"%{theme_key}%",)
        )
        row = cur.fetchone()
        last_mention = row[0] if row and row[0] else None
        if last_mention:
            try:
                last_dt = datetime.fromisoformat(last_mention.replace("Z", "+00:00"))
                days_ago = (datetime.now() - last_dt.replace(tzinfo=None)).days
                if days_ago > stale_days:
                    cur2 = conn.execute(
                        "SELECT COUNT(*) FROM document_themes WHERE theme_label LIKE ?",
                        (f"%{theme_key}%",)
                    )
                    total = cur2.fetchone()[0]
                    stale.append({
                        "type": "STALE",
                        "file": md.name,
                        "last_mention": last_mention[:10],
                        "days_ago": days_ago,
                        "total_mentions": total,
                    })
            except Exception:
                pass
    conn.close()
    return stale
```

**5. Missing frontmatter** — notes without `---` YAML block.
```python
def _detect_missing_frontmatter(vault_path: str) -> list:
    """Find .md files without YAML frontmatter."""
    vault = Path(vault_path)
    missing = []
    for md in vault.rglob("*.md"):
        if md.name.startswith("_"):
            continue
        content = md.read_text(errors="replace")
        if not content.strip().startswith("---"):
            missing.append({
                "type": "FRONTMATTER",
                "file": md.name,
                "path": str(md.relative_to(vault)),
            })
    return missing
```

### Write the triage report:
```python
def _write_triage_report(items: list, vault_path: str) -> str:
    """Write triage items to a dated markdown file in _triage/."""
    triage_dir = Path(vault_path) / "_triage"
    triage_dir.mkdir(exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    filepath = triage_dir / f"TRIAGE_{date_str}.md"

    # Group by type
    groups = {}
    for i, item in enumerate(items):
        t = item["type"]
        if t not in groups:
            groups[t] = []
        item["id"] = f"T{i+1:03d}"
        groups[t].append(item)

    lines = [
        "---",
        f"date: {date_str}",
        "scanned_by: consuela",
        "status: pending_review",
        f"items_total: {len(items)}",
        "items_resolved: 0",
        "---",
        "",
        f"## Vault Triage — {datetime.now().strftime('%B %d, %Y')}",
        "",
    ]

    type_labels = {
        "MERGE": "MERGE CANDIDATES",
        "ORPHAN": "ORPHAN NOTES",
        "BROKEN_LINK": "BROKEN LINKS",
        "STALE": "STALE THEMES",
        "FRONTMATTER": "MISSING FRONTMATTER",
    }

    for typ, label in type_labels.items():
        group = groups.get(typ, [])
        if not group:
            continue
        lines.append(f"### {label} ({len(group)})")
        lines.append("")
        for item in group[:20]:  # Cap at 20 per category
            if typ == "MERGE":
                lines.append(f"- [ ] **{item['id']}** | MERGE | `{item['files'][0]}` + `{item['files'][1]}`")
                lines.append(f"  - Overlap: {item['overlap_pct']}% | Shared tickers: {', '.join(item['shared_tickers'])}")
                lines.append(f"  - Recommendation: pending (Remi)")
            elif typ == "ORPHAN":
                lines.append(f"- [ ] **{item['id']}** | ORPHAN | `{item['file']}`")
                lines.append(f"  - DB mentions: {item['db_mentions']} | Last modified: {item['last_modified']}")
                lines.append(f"  - Recommendation: pending (Remi)")
            elif typ == "BROKEN_LINK":
                lines.append(f"- [ ] **{item['id']}** | BROKEN_LINK | `{item['source']}` → `[[{item['target']}]]`")
            elif typ == "STALE":
                lines.append(f"- [ ] **{item['id']}** | STALE | `{item['file']}`")
                lines.append(f"  - Last mention: {item['last_mention']} ({item['days_ago']}d ago) | Total: {item['total_mentions']}")
                lines.append(f"  - Recommendation: pending (Remi)")
            elif typ == "FRONTMATTER":
                lines.append(f"- [ ] **{item['id']}** | FRONTMATTER | `{item['path']}`")
            lines.append(f"  - Status: pending")
            lines.append("")
        lines.append("")

    filepath.write_text("\n".join(lines))
    # Ensure LiveSync can pick it up
    subprocess.run(["chown", "proxmox:proxmox", str(filepath)], capture_output=True)
    subprocess.run(["chown", "proxmox:proxmox", str(triage_dir)], capture_output=True)
    return str(filepath)
```

---

## FILE 3: Schedule Consuela Nightly

**Modify:** `~/remi-intelligence/src/main.py`

Add the import and scheduler job near the existing `add_job` block:

```python
# Import (at top with other imports)
from consuela_overnight import main as consuela_overnight_run

# Wrapper function (near other job_ functions)
def job_consuela_overnight():
    """Nightly vault triage + maintenance."""
    try:
        consuela_overnight_run()
    except Exception as e:
        logger.error(f"Consuela overnight failed: {e}")

# Add to scheduler (near other add_job calls)
scheduler.add_job(job_consuela_overnight, CronTrigger(hour=2, minute=0),
    id="consuela_overnight", name="consuela_overnight",
    replace_existing=True, misfire_grace_time=3600)
```

**Important:** This is a CronTrigger (2am), not an IntervalTrigger. NOT a boot job (per March 19 decision).

---

## FILE 4: Morning Brief Integration

**Modify:** morning brief generation in `main.py` or `pattern_detector.py`

Add one line to the morning brief that references last night's triage:

```python
# In the morning brief assembly, check for pending triage
triage_today = Path(VAULT_PATH) / "_triage" / f"TRIAGE_{datetime.now().strftime('%Y-%m-%d')}.md"
if triage_today.exists():
    content = triage_today.read_text()
    pending_count = content.count("Status: pending")
    if pending_count > 0:
        brief_lines.append(f"🧹 Consuela flagged {pending_count} vault items for triage — /vault triage to review")
```

---

## FILE 5: Telegram Commands — `/vault triage`

**Modify:** `~/remi-intelligence/src/signals_group_listener.py`

Add to the command handler:

```python
# In the command dispatch section
if raw.startswith("/vault"):
    parts = raw.split(maxsplit=2)
    subcmd = parts[1] if len(parts) > 1 else "help"

    if subcmd == "triage":
        # Find most recent triage file
        triage_dir = Path(VAULT_PATH) / "_triage"
        triage_files = sorted(triage_dir.glob("TRIAGE_*.md"), reverse=True)
        if not triage_files:
            await event.reply("No triage reports found.")
            return
        content = triage_files[0].read_text()
        pending = [l for l in content.split("\n") if "Status: pending" in l]
        # Build summary
        summary_lines = [f"🧹 *Vault Triage* — {triage_files[0].stem.replace('TRIAGE_', '')}"]
        summary_lines.append(f"{len(pending)} items pending review")
        # Count by type
        merges = content.count("| MERGE |")
        orphans = content.count("| ORPHAN |")
        stale = content.count("| STALE |")
        broken = content.count("| BROKEN_LINK |")
        fm = content.count("| FRONTMATTER |")
        if merges: summary_lines.append(f"  🔀 {merges} merge candidates")
        if orphans: summary_lines.append(f"  👻 {orphans} orphan notes")
        if stale: summary_lines.append(f"  ⏰ {stale} stale themes")
        if broken: summary_lines.append(f"  🔗 {broken} broken links")
        if fm: summary_lines.append(f"  📋 {fm} missing frontmatter")
        summary_lines.append("\nReply with item ID (e.g. T001) for Remi's recommendation")
        await event.reply("\n".join(summary_lines), parse_mode="markdown")
        return

    elif subcmd == "approve":
        item_id = parts[2].upper() if len(parts) > 2 else None
        if not item_id:
            await event.reply("Usage: `/vault approve T001` or `/vault approve all`")
            return
        # TODO: Remi executes the approved action
        await event.reply(f"✅ Approved {item_id} — Remi will execute.")
        return

    elif subcmd == "reject":
        item_id = parts[2].upper() if len(parts) > 2 else None
        if not item_id:
            await event.reply("Usage: `/vault reject T001`")
            return
        # Mark as rejected in triage file
        await event.reply(f"❌ Rejected {item_id}")
        return
```

### The Remi Layer (Fellow Review)

When MG replies with an item ID (e.g. "T001"), the signals listener passes it to Remi via GLM-5 with context:

```python
# When user sends a triage item ID
if re.match(r'^T\d{3}$', raw.strip().upper()):
    item_id = raw.strip().upper()
    # Load triage file, find item
    # Pass to GLM-5 with prompt:
    prompt = f"""You are reviewing a vault triage item found by Consuela.

Item {item_id}: {item_details}

Based on the current investing thesis, GLI regime, and vault structure:
1. What is your recommendation? (merge/archive/delete/keep/fix)
2. Why?
3. If merge: which file should be the canonical note?
4. If delete: is any content worth preserving elsewhere?

Be specific and decisive. MG will approve or reject your recommendation."""

    recommendation = _call_glm5(prompt)
    await event.reply(f"🩺 *Remi's Recommendation for {item_id}:*\n\n{recommendation}")
```

---

## BUILD ORDER

### Step 1: Upgrade consuela_overnight.py (hand to Remi)
Replace current `vault_hygiene()` with the detection functions + triage report writer from File 2. Keep existing YouTube and ICU tasks unchanged.

### Step 2: Add to scheduler (hand to Remi)
Add `job_consuela_overnight` to `main.py` scheduler as 2am CronTrigger per File 3.

### Step 3: Add `/vault triage` command (hand to Remi)
Add command handler to `signals_group_listener.py` per File 5.

### Step 4: Morning brief line (hand to Remi)
Add triage count to morning brief per File 4.

### Step 5: Test
```bash
# Run Consuela manually to generate first triage report
cd ~/remi-intelligence/src && python3 consuela_overnight.py

# Check the output
cat /docker/obsidian/investing/Intelligence/_triage/TRIAGE_$(date +%Y-%m-%d).md

# Test the command
# Drop "/vault triage" in the investing group
```

---

## WHAT THIS UNLOCKS

- Consuela scans nightly at 2am — finds mess, writes it up, sends summary to Dev Remi DM
- Morning brief tells MG "12 items flagged for triage"
- MG types `/vault triage` in the group — sees summary
- MG sends "T001" — Remi reads the merge candidate, checks against current thesis, recommends action
- MG types `/vault approve T001` — Remi executes the merge
- Nothing gets deleted or moved without MG's explicit approval
- Over time, the vault self-cleans: orphans archived, duplicates merged, broken links fixed, frontmatter standardized

---

*Spec: April 15, 2026*
*Hierarchy: Consuela (resident) → Remi (fellow) → MG (attending)*
*No vault changes without attending sign-off*
*Previous: remi-vision-relay-spec.md*
