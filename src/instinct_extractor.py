"""
Instinct Extractor — Identifies repeating causal reasoning patterns across themes.
When the same second-order chain appears in 2+ themes, it becomes a named instinct
with confidence scoring. Instincts are referenced as priors in future extractions.
"""

import os
import re
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Tuple
from collections import Counter

logger = logging.getLogger(__name__)

THEMES_DIR = "/docker/obsidian/investing/Intelligence/Themes"
INSTINCTS_PATH = "/docker/obsidian/investing/Intelligence/_meta/INSTINCTS.md"


def extract_causal_chains(theme_content: str) -> List[Dict]:
    """Extract second-order causal chains from a theme file."""
    chains = []
    
    # Look for Second-Order Implications section
    so_match = re.search(r'## Second-Order Implications\n(.*?)(?=\n##|\Z)', theme_content, re.DOTALL)
    if not so_match:
        return chains
    
    so_text = so_match.group(1).strip()
    if "Not yet computed" in so_text or not so_text:
        return chains
    
    # Extract bullet points as chains
    for line in so_text.split("\n"):
        line = line.strip().lstrip("- ")
        if len(line) > 20 and ("→" in line or "->" in line or "leads to" in line.lower()):
            chains.append({"chain": line, "length": len(line)})
    
    return chains


def scan_all_chains() -> List[Tuple[str, str, List[Dict]]]:
    """Scan all theme files for causal chains."""
    results = []
    themes_path = Path(THEMES_DIR)
    
    if not themes_path.exists():
        return results
    
    for f in themes_path.glob("THEME_*.md"):
        try:
            content = f.read_text()
            chains = extract_causal_chains(content)
            if chains:
                # Get theme key from frontmatter
                key_match = re.search(r'theme_key:\s*(\S+)', content)
                theme_key = key_match.group(1) if key_match else f.stem
                results.append((theme_key, f.name, chains))
        except Exception as e:
            logger.warning(f"Failed to scan {f.name}: {e}")
    
    return results


def find_repeating_patterns(all_chains: List[Tuple]) -> List[Dict]:
    """Find causal patterns that appear across multiple themes."""
    # Normalize chains for comparison — extract key nouns/concepts
    chain_index = {}
    
    for theme_key, filename, chains in all_chains:
        for chain in chains:
            text = chain["chain"].lower()
            # Simple keyword extraction for grouping
            keywords = set(re.findall(r'\b[a-z]{4,}\b', text))
            # Remove common words
            keywords -= {"this", "that", "with", "from", "into", "will", "could", "would", "leads", "through"}
            
            frozen = frozenset(keywords)
            if frozen not in chain_index:
                chain_index[frozen] = []
            chain_index[frozen].append({
                "theme": theme_key,
                "chain_text": chain["chain"],
                "keywords": keywords
            })
    
    # Filter to patterns appearing in 2+ themes
    instincts = []
    for keywords, occurrences in chain_index.items():
        unique_themes = set(o["theme"] for o in occurrences)
        if len(unique_themes) >= 2:
            instincts.append({
                "keywords": sorted(keywords),
                "occurrence_count": len(unique_themes),
                "themes": sorted(unique_themes),
                "example_chain": occurrences[0]["chain_text"],
                "confidence": min(len(unique_themes) / 5.0, 1.0)
            })
    
    instincts.sort(key=lambda x: x["occurrence_count"], reverse=True)
    return instincts


def write_instincts(instincts: List[Dict]):
    """Write instincts file to vault."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    
    lines = [f"# Remi Instincts — Auto-Derived Reasoning Patterns\n"]
    lines.append(f"*Last updated: {today}*\n")
    lines.append(f"*{len(instincts)} patterns detected across themes*\n")
    lines.append("")
    
    for i, inst in enumerate(instincts[:20], 1):
        conf_bar = "█" * int(inst["confidence"] * 10) + "░" * (10 - int(inst["confidence"] * 10))
        lines.append(f"## Instinct {i}")
        lines.append(f"**Confidence:** [{conf_bar}] {inst['confidence']:.0%}")
        lines.append(f"**Seen in:** {inst['occurrence_count']} themes — {', '.join(inst['themes'][:5])}")
        lines.append(f"**Pattern:** {inst['example_chain']}")
        lines.append(f"**Keywords:** {', '.join(inst['keywords'][:8])}")
        lines.append("")
    
    output = "\n".join(lines)
    
    os.makedirs(os.path.dirname(INSTINCTS_PATH), exist_ok=True)
    with open(INSTINCTS_PATH, "w") as f:
        f.write(output)
    
    import shutil
    shutil.chown(INSTINCTS_PATH, user="proxmox", group="proxmox")
    
    logger.info(f"Wrote {len(instincts)} instincts to {INSTINCTS_PATH}")
    return output


def run_instinct_extraction() -> str:
    """Main entry point — scan themes, find patterns, write instincts."""
    logger.info("Starting instinct extraction...")
    all_chains = scan_all_chains()
    logger.info(f"Scanned {len(all_chains)} themes with causal chains")
    
    if not all_chains:
        logger.warning("No causal chains found in any themes")
        return ""
    
    instincts = find_repeating_patterns(all_chains)
    logger.info(f"Found {len(instincts)} repeating patterns")
    
    if not instincts:
        logger.info("No patterns repeat across themes yet — need more data")
        return ""
    
    return write_instincts(instincts)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run_instinct_extraction()
    print(result)
