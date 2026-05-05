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

# Dashboard push integration
try:
    from dashboard_push import push_morning_brief, push_theme
    HAS_DASHBOARD = True
except ImportError:
    HAS_DASHBOARD = False

# X Scout co-occurrence integration
try:
    from x_co_occurrence import detect_co_occurrences
    HAS_X_CO_OCCURRENCE = True
except ImportError:
    HAS_X_CO_OCCURRENCE = False

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
SONNET_MODEL = "claude-sonnet-4-6"
INVESTING_GROUP_CHAT_ID = os.getenv("INVESTING_GROUP_CHAT_ID", "-1003857050116")


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
        "x_scout_summary": None,
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
    
    try:
        # 6. Get X scout summary for morning brief
        result["x_scout_summary"] = _get_x_scout_summary(db_path, lookback_hours=24)
    except Exception as e:
        logger.error(f"Error getting X scout summary: {e}")
        result["errors"].append(f"X scout summary failed: {str(e)}")
    
    # Push to dashboard (non-blocking)
    if HAS_DASHBOARD:
        try:
            # Push morning brief
            top_theme_labels = [t.get("theme_label") or "unknown" for t in result["top_themes"][:5]]
            gli_phase = result["regime_context"].get("gli_phase") if result["regime_context"] else None
            push_morning_brief(
                brief_date=datetime.utcnow().date(),
                content=result["summary_text"],
                velocity_table=json.dumps(result["top_themes"]),
                top_themes=top_theme_labels,
                gli_phase=gli_phase
            )
            
            # Push themes
            for theme in result["top_themes"]:
                push_theme(
                    theme_key=(theme.get("theme_label") or "unknown").lower().replace(" ", "_"),
                    theme_label=theme.get("theme_label"),
                    mention_count=theme.get("mention_count", 1),
                    velocity_score=theme.get("velocity_score", 0.0),
                    velocity_delta=theme.get("velocity_delta", 0.0),
                    is_flagged=theme.get("is_flagged", False),
                    clusters=theme.get("clusters", []),
                    gli_phase=gli_phase
                )
        except Exception as e:
            logger.error(f"Dashboard push failed: {e}")
    
    # Write improvement backlog (non-blocking)
    try:
        write_improvement_backlog(result)
    except Exception:
        pass
    
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


def _get_x_scout_summary(db_path: str, lookback_hours: int = 24) -> Dict[str, Any]:
    """
    Get X scout summary for the morning brief.
    
    Returns:
        Dict with:
        - t1_accounts_polled: count of T1 accounts polled
        - t2plus_accounts_polled: count of T2+ accounts polled
        - tweets_ingested: count of new tweets ingested
        - themes_extracted: count of themes from X tweets
        - co_occurrences: list of co-occurrence detections
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    cutoff_time = (datetime.utcnow() - timedelta(hours=lookback_hours)).isoformat()
    
    result = {
        "t1_accounts_polled": 0,
        "t2plus_accounts_polled": 0,
        "tweets_ingested": 0,
        "themes_extracted": 0,
        "co_occurrences": []
    }
    
    try:
        # Count accounts polled (simplified query)
        cur.execute("""
            SELECT COUNT(DISTINCT handle) as count
            FROM x_scout_state 
            WHERE last_poll_at > ?
        """, (cutoff_time,))
        
        row = cur.fetchone()
        if row:
            result["t1_accounts_polled"] = row[0] if row else 0
        
        # Count tweets ingested
        cur.execute("""
            SELECT COUNT(*) as count
            FROM documents 
            WHERE source_type = 'x_tweet' AND ingested_at > ?
        """, (cutoff_time,))
        
        row = cur.fetchone()
        if row:
            result["tweets_ingested"] = row[0] if row else 0
        
        # Count themes from X tweets
        cur.execute("""
            SELECT COUNT(DISTINCT dt.theme_id) as count
            FROM document_themes dt
            JOIN documents d ON dt.document_id = d.id
            WHERE d.source_type = 'x_tweet' AND d.ingested_at > ?
        """, (cutoff_time,))
        
        row = cur.fetchone()
        if row:
            result["themes_extracted"] = row[0] if row else 0
        
        # Get co-occurrences if available
        if HAS_X_CO_OCCURRENCE:
            try:
                co_occurrences = detect_co_occurrences(conn, window_hours=lookback_hours)
                result["co_occurrences"] = co_occurrences
            except Exception as e:
                logger.error(f"Error detecting co-occurrences: {e}")
        
    except Exception as e:
        logger.error(f"Error getting X scout summary: {e}")
    finally:
        conn.close()
    
    return result


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
        brief = response.content[0].text.strip()
        # Hard cap at 1500 chars — trim at last sentence boundary
        if len(brief) > 1500:
            brief = brief[:1500]
            last_period = brief.rfind('.')
            if last_period > 1000:
                brief = brief[:last_period + 1]
        return brief
    except Exception as e:
        logger.error(f"Sonnet brief generation error: {e}")
        # Fallback to simple summary
        if convergence:
            top_conv = convergence[0]
            return f"Key signal: {top_conv['theme_label']} showing cross-source convergence ({top_conv['source_count']} sources). Velocity: {top_conv['velocity_score']}. Monitor for secondary effects."
        else:
            return "Unable to generate brief - insufficient convergence data."


def write_improvement_backlog(result: Dict[str, Any]):
    """Write self-critique after morning brief to compound quality over time."""
    try:
        backlog_path = "/docker/obsidian/investing/Intelligence/_meta/improvement-backlog.md"
        os.makedirs(os.path.dirname(backlog_path), exist_ok=True)
        
        today = datetime.utcnow().strftime("%Y-%m-%d")
        
        # Identify noise (low velocity themes that made top 10)
        noise = [t["theme_label"] for t in result.get("top_themes", []) if t.get("velocity_score", 0) < 2.0]
        
        # Identify accelerating signals (high velocity)
        accelerating = [f"{t['theme_label']} (v={t['velocity_score']:.1f})" for t in result.get("top_themes", []) if t.get("velocity_score", 0) > 7.0]
        
        # Identify convergence themes (multi-source)
        converging = [f"{t['theme_label']} ({t['source_count']} sources)" for t in result.get("convergence", [])[:3]]
        
        # Count errors
        errors = result.get("errors", [])
        
        entry = f"\n## {today}\n"
        entry += f"- **Noise (velocity < 2.0):** {', '.join(noise) if noise else 'none'}\n"
        entry += f"- **Accelerating (velocity > 7.0):** {', '.join(accelerating) if accelerating else 'none'}\n"
        entry += f"- **Converging:** {', '.join(converging) if converging else 'none'}\n"
        entry += f"- **Pipeline errors:** {len(errors)} — {'; '.join(errors[:3]) if errors else 'clean run'}\n"
        
        with open(backlog_path, "a") as f:
            f.write(entry)
        
        # Fix ownership for LiveSync
        import shutil
        shutil.chown(backlog_path, user="proxmox", group="proxmox")
        
        logger.info(f"Wrote improvement backlog entry for {today}")
    except Exception as e:
        logger.error(f"Improvement backlog write failed: {e}")


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
