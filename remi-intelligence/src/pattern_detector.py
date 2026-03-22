import anthropic
import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/remi-intelligence/.env"))

from gli_stamper import fetch_gli_stamp

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
SONNET_MODEL = "claude-sonnet-4-6"


def get_pattern_signal(db_path: str, lookback_days: int = 7) -> Dict[str, Any]:
    """
    Analyze themes, convergence, and second-order inferences to generate investor signals.
    
    Args:
        db_path: Path to SQLite database
        lookback_days: Number of days to look back for theme analysis (default 7)
    
    Returns:
        Dict with keys:
        - top_themes: List of top 10 themes by velocity_score
        - convergence: List of themes in 2+ sources, sorted by source_count then velocity
        - second_order: List of second_order inferences for top 3 convergence themes
        - regime_context: Current GLI stamp context
        - summary_text: 3-4 sentence investor brief
        - errors: List of any errors encountered
    """
    
    result = {
        "top_themes": [],
        "convergence": [],
        "second_order": [],
        "regime_context": None,
        "summary_text": "",
        "errors": []
    }
    
    # Expand db_path if relative
    db_path = os.path.expanduser(db_path)
    
    try:
        # 1. Get top 10 themes by velocity_score from last N days
        result["top_themes"] = _get_top_themes(db_path, lookback_days)
    except Exception as e:
        logger.error(f"Error fetching top themes: {e}")
        result["errors"].append(f"Top themes query failed: {str(e)}")
    
    try:
        # 2. Find convergence themes (2+ distinct sources)
        result["convergence"] = _get_convergence_themes(db_path, lookback_days)
    except Exception as e:
        logger.error(f"Error fetching convergence themes: {e}")
        result["errors"].append(f"Convergence query failed: {str(e)}")
    
    try:
        # 3. Get second_order inferences for top 3 convergence themes
        if result["convergence"]:
            top_3_themes = [t["theme_label"] for t in result["convergence"][:3]]
            result["second_order"] = _get_second_order_inferences(db_path, top_3_themes)
    except Exception as e:
        logger.error(f"Error fetching second-order inferences: {e}")
        result["errors"].append(f"Second-order inferences query failed: {str(e)}")
    
    try:
        # 4. Fetch current GLI stamp
        gli_stamp = fetch_gli_stamp()
        result["regime_context"] = gli_stamp.to_dict()
    except Exception as e:
        logger.error(f"Error fetching GLI stamp: {e}")
        result["errors"].append(f"GLI stamp fetch failed: {str(e)}")
    
    try:
        # 5. Generate investor brief using Sonnet
        result["summary_text"] = _generate_investor_brief(
            result["top_themes"],
            result["convergence"],
            result["second_order"],
            result["regime_context"]
        )
    except Exception as e:
        logger.error(f"Error generating investor brief: {e}")
        result["errors"].append(f"Investor brief generation failed: {str(e)}")
    
    return result


def _get_top_themes(db_path: str, lookback_days: int) -> List[Dict[str, Any]]:
    """
    Query themes table: get top 10 by velocity_score from last N days.
    
    Fields: theme_label, velocity_score, mention_count, source_count, sources_list, gli_phase_at_emergence
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    cutoff_date = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()
    
    # Get top 10 themes by velocity_score from last N days
    query = """
    SELECT 
        t.id,
        t.theme_label,
        t.velocity_score,
        t.mention_count,
        t.gli_phase_at_emergence
    FROM themes t
    WHERE t.last_seen_at > ?
    ORDER BY t.velocity_score DESC
    LIMIT 10
    """
    
    cur.execute(query, (cutoff_date,))
    rows = cur.fetchall()
    
    # For each theme, count distinct sources
    results = []
    for row in rows:
        theme_id = row["id"]
        
        # Get source count and sources list
        source_query = """
        SELECT DISTINCT d.source_name
        FROM document_themes dt
        JOIN documents d ON dt.document_id = d.id
        WHERE dt.theme_id = ? AND d.ingested_at > ?
        """
        cur.execute(source_query, (theme_id, cutoff_date))
        sources = [s[0] for s in cur.fetchall()]
        
        results.append({
            "theme_label": row["theme_label"],
            "velocity_score": row["velocity_score"],
            "mention_count": row["mention_count"],
            "source_count": len(sources),
            "sources_list": ", ".join(sources) if sources else "",
            "gli_phase_at_emergence": row["gli_phase_at_emergence"]
        })
    
    conn.close()
    return results


def _get_convergence_themes(db_path: str, lookback_days: int) -> List[Dict[str, Any]]:
    """
    Find convergence: themes in 2+ distinct sources, sorted by source_count then velocity.
    
    Fields: theme_label, velocity_score, mention_count, source_count, sources_list, gli_phase_at_emergence
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    cutoff_date = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()
    
    # Get themes appearing in 2+ distinct sources
    query = """
    SELECT 
        t.id,
        t.theme_label,
        t.velocity_score,
        t.mention_count,
        t.gli_phase_at_emergence
    FROM themes t
    WHERE t.last_seen_at > ?
    ORDER BY t.velocity_score DESC
    """
    
    cur.execute(query, (cutoff_date,))
    rows = cur.fetchall()
    
    # Filter for 2+ sources and collect results
    results = []
    for row in rows:
        theme_id = row["id"]
        
        # Get distinct sources
        source_query = """
        SELECT DISTINCT d.source_name
        FROM document_themes dt
        JOIN documents d ON dt.document_id = d.id
        WHERE dt.theme_id = ? AND d.ingested_at > ?
        """
        cur.execute(source_query, (theme_id, cutoff_date))
        sources = [s[0] for s in cur.fetchall()]
        
        # Only include themes with 2+ sources
        if len(sources) >= 2:
            results.append({
                "theme_label": row["theme_label"],
                "velocity_score": row["velocity_score"],
                "mention_count": row["mention_count"],
                "source_count": len(sources),
                "sources_list": ", ".join(sources),
                "gli_phase_at_emergence": row["gli_phase_at_emergence"]
            })
    
    # Sort by source_count DESC, then velocity_score DESC
    results.sort(key=lambda x: (-x["source_count"], -x["velocity_score"]))
    
    conn.close()
    return results


def _get_second_order_inferences(db_path: str, theme_labels: List[str]) -> List[Dict[str, Any]]:
    """
    Get second_order inferences for specified themes.
    
    Fields: trigger_theme, primary_impact, second_order, third_order, gli_regime_context
    """
    if not theme_labels:
        return []
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    results = []
    
    for theme_label in theme_labels:
        # Find inferences for this theme by matching trigger_theme
        query = """
        SELECT 
            trigger_theme,
            primary_impact,
            second_order,
            third_order,
            gli_regime_context
        FROM second_order_inferences
        WHERE trigger_theme LIKE ?
        LIMIT 1
        """
        
        cur.execute(query, (f"%{theme_label}%",))
        row = cur.fetchone()
        
        if row:
            results.append({
                "trigger_theme": row["trigger_theme"],
                "primary_impact": row["primary_impact"],
                "second_order": row["second_order"],
                "third_order": row["third_order"],
                "gli_regime_context": row["gli_regime_context"]
            })
    
    conn.close()
    return results


def _generate_investor_brief(
    top_themes: List[Dict[str, Any]],
    convergence: List[Dict[str, Any]],
    second_order: List[Dict[str, Any]],
    regime_context: Optional[Dict[str, Any]]
) -> str:
    """
    Generate a 3-4 sentence plain English investor brief using Sonnet.
    """
    
    # Build context for the prompt
    top_themes_text = "\n".join([
        f"- {t['theme_label']} (velocity: {t['velocity_score']}, mentions: {t['mention_count']})"
        for t in top_themes[:5]
    ])
    
    convergence_text = "\n".join([
        f"- {t['theme_label']} ({t['source_count']} sources: {t['sources_list']})"
        for t in convergence[:3]
    ])
    
    second_order_text = "\n".join([
        f"- {s['trigger_theme']}: {s['primary_impact']} → {s['second_order']}"
        for s in second_order
    ])
    
    regime_text = ""
    if regime_context:
        regime_text = f"""
Current macro regime:
- GLI Phase: {regime_context.get('gli_phase', 'N/A')}
- GLI Value: ${regime_context.get('gli_value_bn', 0):.0f}B
- Steno Regime: {regime_context.get('steno_regime', 'N/A')}
- Fiscal Score: {regime_context.get('fiscal_score', 'N/A')}/10
- Transition Risk: {regime_context.get('transition_risk', 'N/A')}/10
"""
    
    prompt = f"""You are an expert macro investment analyst. Based on the following market intelligence, 
write a concise 3-4 sentence investor brief that synthesizes the key signals and actionable insights.

TOP THEMES (by velocity):
{top_themes_text}

CROSS-SOURCE CONVERGENCE:
{convergence_text}

SECOND-ORDER IMPLICATIONS:
{second_order_text}

{regime_text}

Write a clear, direct brief for institutional investors. Focus on:
1. The most material convergence signals
2. Implied market implications
3. How this fits the current macro regime
4. Actionable takeaway

Be specific and quantitative where possible. Avoid jargon. Keep to 3-4 sentences max."""

    try:
        response = client.messages.create(
            model=SONNET_MODEL,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.error(f"Sonnet brief generation error: {e}")
        # Fallback to simple summary
        if convergence:
            top_conv = convergence[0]
            return f"Key signal: {top_conv['theme_label']} showing cross-source convergence ({top_conv['source_count']} sources). Velocity: {top_conv['velocity_score']}. Monitor for secondary effects."
        else:
            return "Unable to generate brief - insufficient convergence data."


if __name__ == "__main__":
    # Test the function
    logging.basicConfig(level=logging.INFO)
    
    db_path = os.path.expanduser("~/remi-intelligence/remi_intelligence.db")
    
    print("Testing get_pattern_signal()...")
    result = get_pattern_signal(db_path, lookback_days=7)
    
    print("\n=== PATTERN SIGNAL RESULT ===\n")
    
    print(f"Top Themes ({len(result['top_themes'])}):")
    for i, theme in enumerate(result['top_themes'][:5], 1):
        print(f"  {i}. {theme['theme_label']} (velocity: {theme['velocity_score']}, sources: {theme['source_count']})")
    
    print(f"\nConvergence Themes ({len(result['convergence'])}):")
    for i, theme in enumerate(result['convergence'][:3], 1):
        print(f"  {i}. {theme['theme_label']} ({theme['source_count']} sources: {theme['sources_list'][:50]}...)")
    
    print(f"\nSecond-Order Inferences ({len(result['second_order'])}):")
    for i, inf in enumerate(result['second_order'], 1):
        print(f"  {i}. {inf['trigger_theme'][:40]}...")
    
    print(f"\nRegime Context:")
    if result['regime_context']:
        print(f"  GLI Phase: {result['regime_context'].get('gli_phase', 'N/A')}")
        print(f"  GLI Value: ${result['regime_context'].get('gli_value_bn', 0):.0f}B")
    
    print(f"\nInvestor Brief:")
    print(f"  {result['summary_text']}\n")
    
    if result['errors']:
        print(f"Errors ({len(result['errors'])}):")
        for err in result['errors']:
            print(f"  - {err}")
    else:
        print("✓ No errors - execution successful!")
