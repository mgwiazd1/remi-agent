"""
Cross-Theme Synthesis — Weekly job that finds connections across high-velocity themes.
Reads theme index files, identifies clusters, writes synthesis to vault.
Runs Sunday 9am via scheduler.
"""

import os
import re
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

THEMES_DIR = "/docker/obsidian/investing/Intelligence/Themes"
HISTORY_DIR = "/docker/obsidian/investing/Intelligence/History"
OUTPUT_PATH = "/docker/obsidian/investing/Intelligence/_meta/CROSS_THEME_SYNTHESIS.md"
VELOCITY_THRESHOLD = 5.0
MAX_THEMES_FOR_SYNTHESIS = 25


def scan_high_velocity_themes() -> List[Dict]:
    """Read all theme files above velocity threshold."""
    themes = []
    themes_path = Path(THEMES_DIR)
    
    if not themes_path.exists():
        logger.warning(f"Themes directory not found: {THEMES_DIR}")
        return themes
    
    for f in themes_path.glob("THEME_*.md"):
        try:
            content = f.read_text()
            # Parse YAML frontmatter
            match = re.search(r'^---\n(.*?)\n---', content, re.DOTALL)
            if not match:
                continue
            
            frontmatter = match.group(1)
            theme = {"filename": f.name, "content": content}
            
            for line in frontmatter.split("\n"):
                if ":" in line:
                    key, val = line.split(":", 1)
                    key = key.strip()
                    val = val.strip().strip('"')
                    if key == "velocity_score":
                        theme[key] = float(val)
                    elif key == "mention_count":
                        theme[key] = int(val)
                    elif key == "tickers":
                        theme[key] = re.findall(r'"([^"]+)"', val)
                    else:
                        theme[key] = val
            
            vs = theme.get("velocity_score", 0)
            mc = theme.get("mention_count", 0)
            if vs >= VELOCITY_THRESHOLD or mc >= 3:
                themes.append(theme)
        except Exception as e:
            logger.warning(f"Failed to parse {f.name}: {e}")
    
    # Sort by velocity descending
    themes.sort(key=lambda x: x.get("velocity_score", 0), reverse=True)
    return themes[:MAX_THEMES_FOR_SYNTHESIS]


def find_ticker_overlaps(themes: List[Dict]) -> Dict[str, List[str]]:
    """Find tickers that appear in multiple themes — these are connection points."""
    ticker_themes = {}
    for t in themes:
        for ticker in t.get("tickers", []):
            if ticker not in ticker_themes:
                ticker_themes[ticker] = []
            ticker_themes[ticker].append(t.get("theme_key", t.get("filename", "unknown")))
    
    # Only keep tickers in 2+ themes
    return {k: v for k, v in ticker_themes.items() if len(v) >= 2}


def load_historical_episodes() -> List[Dict]:
    """Load episode notes for analog matching."""
    episodes = []
    history_path = Path(HISTORY_DIR)
    if not history_path.exists():
        return episodes
    
    for f in history_path.glob("EPISODE_*.md"):
        try:
            content = f.read_text()
            match = re.search(r'^---\n(.*?)\n---', content, re.DOTALL)
            if match:
                ep = {"filename": f.name, "content": content}
                for line in match.group(1).split("\n"):
                    if ":" in line:
                        key, val = line.split(":", 1)
                        ep[key.strip()] = val.strip().strip('"')
                episodes.append(ep)
        except Exception as e:
            logger.warning(f"Failed to parse episode {f.name}: {e}")
    return episodes


def write_synthesis(themes: List[Dict], overlaps: Dict, episodes: List[Dict]):
    """Write the cross-theme synthesis note."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    
    lines = [f"# Cross-Theme Synthesis — {today}\n"]
    lines.append(f"*Auto-generated from {len(themes)} high-velocity themes*\n")
    
    # Section 1: Top themes summary
    lines.append("## Active High-Velocity Themes\n")
    for t in themes[:10]:
        sentiment = t.get("sentiment", "neutral")
        tickers = ", ".join(t.get("tickers", [])[:5])
        lines.append(f"- **{t.get('theme_key', 'unknown')}** — velocity: {t.get('velocity_score', 0):.1f}, sentiment: {sentiment}, tickers: {tickers}")
    lines.append("")
    
    # Section 2: Ticker overlaps (connection points)
    if overlaps:
        lines.append("## Cross-Theme Connections (Shared Tickers)\n")
        for ticker, theme_list in sorted(overlaps.items(), key=lambda x: len(x[1]), reverse=True)[:15]:
            lines.append(f"- **{ticker}** appears in {len(theme_list)} themes: {', '.join(theme_list[:5])}")
        lines.append("")
    
    # Section 3: Regime context
    lines.append("## Questions for Next Week\n")
    lines.append("- Which of these themes are reinforcing each other?")
    lines.append("- Which themes contradict — and what resolves the contradiction?")
    lines.append("- What positions benefit from multiple themes converging?")
    lines.append("- What historical episode does this cluster most resemble?")
    lines.append("")
    
    # Section 4: Historical analogs
    if episodes:
        lines.append("## Available Historical Analogs\n")
        for ep in episodes:
            lines.append(f"- {ep.get('filename', '').replace('EPISODE_', '').replace('.md', '')} ({ep.get('period', 'unknown')}) — GLI analog: {ep.get('gli_analog', 'unknown')}")
        lines.append("")
    
    output = "\n".join(lines)
    
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        f.write(output)
    
    import shutil
    shutil.chown(OUTPUT_PATH, user="proxmox", group="proxmox")
    
    logger.info(f"Cross-theme synthesis written: {len(themes)} themes, {len(overlaps)} ticker overlaps")
    return output


def run_cross_theme_synthesis() -> str:
    """Main entry point — called by scheduler."""
    logger.info("Starting cross-theme synthesis...")
    themes = scan_high_velocity_themes()
    if not themes:
        logger.warning("No high-velocity themes found for synthesis")
        return ""
    
    overlaps = find_ticker_overlaps(themes)
    episodes = load_historical_episodes()
    return write_synthesis(themes, overlaps, episodes)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run_cross_theme_synthesis()
    print(result)
