#!/bin/bash
# Replace with reissued key from Synthesis support
SYNTH_KEY="PASTE_NEW_KEY_HERE"

curl -s -X POST https://synthesis.devfolio.co/projects \
  -H "Authorization: Bearer $SYNTH_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "teamUUID": "b95782c2e27d4247aa3318a6e41486f2",
    "name": "Remi — Sovereign AI Agent for Medicine and Macro Finance",
    "description": "Remi is a fully self-hosted, autonomous AI agent running on a personal Proxmox homelab, serving a pulmonary and critical care physician as both a clinical second brain and a macro financial intelligence system. He operates entirely without dependence on centralized AI platforms. He is not a chatbot. He is infrastructure.",
    "problemStatement": "A physician-investor operating at the intersection of critical care medicine and macro finance faces a unique information problem. Clinical evidence moves fast and no tool remembers your patient evolving picture. Macro signal-to-noise is catastrophic and second-order supply chain implications require synthesis across geopolitics, commodities, and liquidity regimes simultaneously. The same cognitive architecture that traces a pathogen through inflammatory cascades also traces a fertilizer disruption through ammonia feedstock to crop margin compression. Second-order reasoning is domain-agnostic. One sovereign agent. Two languages. No platform lock-in.",
    "repoURL": "https://github.com/mgwiazd1/remi-agent",
    "trackUUIDs": [
      "bd442ad05f344c6d8b117e6761fa72ce",
      "2aa04e34ca7842d6bfba26235d550293",
      "78f1416489d34fc1b80d87081d6d809c",
      "38ee1df341a1410b870ba0d2ad48e4f8",
      "e3767de8e7804c7080eeb5cb6e27b3cf"
    ],
    "conversationLog": "Human-agent collaboration spanning 5 days. MG (pulmonary critical care physician, coding comfort 2/10) directed the vision and clinical requirements. Remi and Claude Code built the full stack — Hermes migration from OpenClaw, narrative intelligence pipeline, GLI integration with Aestima, signal listener, inbox pipeline, vault knowledge graph. MG validated clinical accuracy and investing signal quality. Key pivots: switched from OpenClaw to Hermes Agent for better systemd integration; added second-order inference as a first-class pipeline step after recognizing domain-agnostic reasoning potential; isolated clinical and investing workstreams on separate API keys for Pablo cost-sharing arrangement. Remi flagged his own knowledge gaps and requested specific research to fill them — the self-awareness feature emerged organically from clinical use.",
    "submissionMetadata": {
      "agentFramework": "other",
      "agentFrameworkOther": "Hermes Agent (Nous Research) + APScheduler narrative intelligence pipeline",
      "agentHarness": "other",
      "agentHarnessOther": "hermes-agent (Nous Research) with systemd user service",
      "model": "claude-haiku-4-5-20251001",
      "skills": ["obsidian", "arxiv", "duckduckgo-search", "aestima-gli"],
      "tools": ["Proxmox", "Hermes Agent", "Claude API", "Telethon", "APScheduler", "feedparser", "CouchDB LiveSync", "SQLite", "systemd", "Cloudflare Tunnel", "Telegram Bot API", "twitter-cli"],
      "helpfulResources": [
        "https://github.com/nous-research/hermes",
        "https://synthesis.md/skill.md",
        "https://synthesis.md/submission/skill.md",
        "https://www.moltbook.com/skill.md",
        "https://github.com/AmberYZ/investing_agent"
      ],
      "helpfulSkills": [
        {"name": "obsidian", "reason": "Enabled Remi to query and write structured markdown notes to the clinical vault directly from Telegram queries mid-shift"},
        {"name": "aestima-gli", "reason": "GLI phase stamping at document ingestion time means every investment theme is conditioned on live macro regime context — this is the core differentiator"}
      ],
      "intention": "continuing",
      "intentionNotes": "Phase 2 adds X account monitoring via twitter-cli. Phase 3 ships GLI data product via x402 micropayments for machine-to-machine sales to quant funds. Pablo cost-sharing arrangement already live.",
      "moltbookPostURL": "https://www.moltbook.com/u/remiagent/posts/54e713f3-00ea-4b2b-8383-d7484b1baff0"
    }
  }' | python3 -m json.tool
