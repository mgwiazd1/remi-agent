# REMI — RSS Feed Expansion List
**Date:** April 17, 2026  
**Purpose:** Fill sector coverage gaps identified by sector velocity analysis  
**Priority order:** Fed > Credit > Crypto > AI > FX > Equities > Agriculture

---

## PRIORITY 1 — FED (currently 1 theme, 2 mentions)

```json
{
  "name": "FedGuy (Joseph Wang)",
  "url": "https://fedguy.com/feed/",
  "tier": 1,
  "sector": "fed",
  "clusters": ["fed", "rates", "liquidity_plumbing", "qt"],
  "weight": 1.0,
  "note": "Ex-Fed OMTD trader. Best source on RRP, reserves, QT mechanics, SOFR. Directly feeds Aestima GLI methodology."
},
{
  "name": "Federal Reserve Speeches",
  "url": "https://www.federalreserve.gov/feeds/speeches.xml",
  "tier": 1,
  "sector": "fed",
  "clusters": ["fed", "monetary_policy"],
  "weight": 0.9,
  "note": "Official Fed speeches — primary source, no interpretation layer. Powell, Waller, Bowman signals."
},
{
  "name": "FRED Blog",
  "url": "https://fredblog.stlouisfed.org/feed/",
  "tier": 2,
  "sector": "fed",
  "clusters": ["fed", "macro", "data"],
  "weight": 0.7,
  "note": "St. Louis Fed data blog — economic data visualization and commentary. Good for data-driven theme extraction."
}
```

**X Scout additions:** `@FedGuy12` (Joseph Wang), `@NickTimiraos` (already in taxonomy — verify polling)

---

## PRIORITY 2 — CREDIT (currently 4 themes, 8 mentions)

```json
{
  "name": "Apollo Chief Economist (Torsten Slok)",
  "url": "https://www.apolloacademy.com/feed/",
  "tier": 1,
  "sector": "credit",
  "clusters": ["credit", "private_credit", "rates", "macro"],
  "weight": 0.95,
  "note": "Daily charts + commentary on credit conditions, HY spreads, PE flows. Apollo has direct visibility into private credit."
},
{
  "name": "Concoda",
  "url": "https://concoda.substack.com/feed",
  "tier": 2,
  "sector": "credit",
  "clusters": ["credit", "repo", "liquidity", "rates"],
  "weight": 0.8,
  "note": "Deep dives on repo markets, collateral chains, shadow banking. Complements FedGuy on plumbing."
}
```

**X Scout additions:** `@Tracy_Alloway` (Bloomberg Odd Lots, credit/macro), `@CreditMacro`

---

## PRIORITY 3 — CRYPTO (currently 10 themes, 21 mentions — X only, no RSS)

```json
{
  "name": "Bankless",
  "url": "https://bankless.com/feed",
  "tier": 2,
  "sector": "crypto",
  "clusters": ["crypto", "defi", "ethereum", "regulation"],
  "weight": 0.75,
  "note": "Moved from Substack to Ghost — /feed works. Broad crypto coverage, DeFi, protocol analysis."
},
{
  "name": "The Block",
  "url": "https://www.theblock.co/rss/all",
  "tier": 2,
  "sector": "crypto",
  "clusters": ["crypto", "regulation", "institutional", "on_chain"],
  "weight": 0.7,
  "note": "Institutional crypto news. Good for regulatory signals and on-chain flow coverage."
},
{
  "name": "CoinDesk",
  "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
  "tier": 3,
  "sector": "crypto",
  "clusters": ["crypto", "bitcoin", "regulation"],
  "weight": 0.5,
  "note": "High volume — use keyword filter similar to ZeroHedge. Good for velocity signal, not depth."
}
```

---

## PRIORITY 4 — AI (currently 4 themes, 10 mentions — zero sources)

```json
{
  "name": "SemiAnalysis (Dylan Patel)",
  "url": "https://semianalysis.substack.com/feed",
  "tier": 1,
  "sector": "ai",
  "clusters": ["ai", "semiconductors", "data_centers", "compute"],
  "weight": 0.95,
  "note": "Best semiconductor/AI infrastructure analysis. NVIDIA, TSMC, data center buildout. Paywalled — free posts still valuable."
},
{
  "name": "Fabricated Knowledge",
  "url": "https://www.fabricatedknowledge.com/feed",
  "tier": 2,
  "sector": "ai",
  "clusters": ["ai", "semiconductors", "supply_chain"],
  "weight": 0.8,
  "note": "Semiconductor industry deep dives. Supply chain, EDA tools, foundry dynamics."
},
{
  "name": "Semiconductor Engineering",
  "url": "https://semiengineering.com/feed/",
  "tier": 3,
  "sector": "ai",
  "clusters": ["ai", "semiconductors"],
  "weight": 0.5,
  "note": "Technical semiconductor news. High volume — keyword filter recommended (AI, HBM, advanced packaging, chiplet)."
}
```

**X Scout additions:** `@dylan522p` (Dylan Patel / SemiAnalysis), `@chipmakernews`

---

## PRIORITY 5 — FX (currently 1 theme, 2 mentions — zero sources)

```json
{
  "name": "Brad Setser - Follow the Money (CFR)",
  "url": "https://www.cfr.org/blog/Setser/feed",
  "tier": 1,
  "sector": "fx",
  "clusters": ["fx", "capital_flows", "trade_imbalances", "china_macro"],
  "weight": 1.0,
  "note": "Ex-Treasury, CFR senior fellow. Best English-language source on cross-border capital flows, reserve management, trade surplus dynamics. Directly relevant to GLI methodology."
}
```

**X Scout additions:** `@Brad_Setser`, `@adventures_fiat`

**Note:** CFR blog RSS may use a non-standard feed path. If `/feed` doesn't work, try:
- `https://www.cfr.org/blog/Setser.rss`
- `https://www.cfr.org/rss/blog/follow-money`
- Fall back to Tavily scraping on a daily schedule

---

## PRIORITY 6 — EQUITIES (currently 11 themes, 22 mentions)

```json
{
  "name": "Bison Interests (Josh Young)",
  "url": "https://bisoninterests.com/feed/",
  "tier": 2,
  "sector": "equities",
  "clusters": ["equities", "energy", "small_cap"],
  "weight": 0.8,
  "note": "Source of the PROP thesis. E&P, energy equities, activist positions."
}
```

**X Scout additions:** `@Josh_Young_1` (Bison Interests)

---

## PRIORITY 7 — AGRICULTURE (currently 1 theme, 2 mentions)

```json
{
  "name": "Gro Intelligence Blog",
  "url": "https://www.gro-intelligence.com/blog/feed",
  "tier": 2,
  "sector": "agriculture",
  "clusters": ["agriculture", "food_security", "commodities"],
  "weight": 0.7,
  "note": "Agricultural commodity data and analysis. Supply/demand models for grains, fertilizer, soft commodities."
}
```

---

## IMPLEMENTATION

### Add to rss_feeds.json:
```bash
cd ~/remi-intelligence
# Edit config/rss_feeds.json — add the feeds above to the "feeds" array
# Verify JSON is valid after editing:
python3 -c "import json; json.load(open('config/rss_feeds.json')); print('VALID')"
```

### Add X Scout accounts:
```bash
# Edit config/account_taxonomy.json — add new handles:
# @FedGuy12 (tier 1, fed)
# @dylan522p (tier 2, ai)
# @Tracy_Alloway (tier 2, credit)
# @Brad_Setser (tier 1, fx)
# @Josh_Young_1 (tier 2, equities)
# @adventures_fiat (tier 2, fx)
# @chipmakernews (tier 3, ai)
```

### Restart to pick up new feeds:
```bash
systemctl --user restart remi-intelligence
# First poll will run on boot — check logs:
journalctl --user -u remi-intelligence --since "2 min ago" | grep -i "rss\|poll\|feed"
```

### Verify after first poll cycle (6h):
```bash
sqlite3 remi_intelligence.db "
SELECT sector, COUNT(*) as n
FROM themes WHERE last_seen_at > datetime('now', '-1 day')
GROUP BY sector ORDER BY n DESC;
"
```

---

## EXPECTED IMPACT

| Sector | Before | After (projected) |
|--------|--------|--------------------|
| Fed | 1 theme | 5-10 themes (FedGuy + speeches + FRED) |
| Credit | 4 themes | 8-15 themes (Apollo + Concoda) |
| Crypto | 10 themes | 20-30 themes (Bankless + The Block + CoinDesk) |
| AI | 4 themes | 10-20 themes (SemiAnalysis + Fabricated Knowledge) |
| FX | 1 theme | 3-8 themes (Brad Setser) |
| Equities | 11 themes | 15-20 themes (Josh Young + earnings) |
| Agriculture | 1 theme | 3-5 themes (Gro Intelligence) |

The sector-balanced push will immediately benefit — instead of 6 geopolitical slots filling the remaining space after the per-sector top-2, you'll have real competition from fed, credit, and AI themes for those slots.

---

## FEEDS TO VERIFY MANUALLY

Some URLs may need adjustment. Test each with:
```bash
curl -s "<URL>" | head -5
# Should return XML/RSS. If 404 or HTML, the URL needs fixing.
```

Known risks:
- CFR blog RSS path may be non-standard
- Apollo Academy may redirect or require different feed path
- SemiAnalysis is paywalled — free posts deliver via RSS but paid content won't
- CoinDesk RSS path changed in 2025 — verify the `/arc/outboundfeeds/rss/` path works

---

*Feed expansion list: April 17, 2026*
*Add these to rss_feeds.json in a single Hermes session*
*Monitor sector distribution after 24-48h of polling*
