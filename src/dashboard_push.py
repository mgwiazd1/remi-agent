import os
import json
import logging
from datetime import datetime
from contextlib import contextmanager

try:
    import psycopg2
    from psycopg2.extras import Json
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

logger = logging.getLogger("dashboard_push")
DASHBOARD_DB_URL = os.getenv("DASHBOARD_DATABASE_URL")


def _get_conn():
    if not HAS_PSYCOPG2 or not DASHBOARD_DB_URL:
        return None
    try:
        return psycopg2.connect(DASHBOARD_DB_URL)
    except Exception as e:
        logger.error(f"Dashboard DB connection failed: {e}")
        return None


def push_velocity_snapshot(signals: list, convergence_count: int, level: str, alert_fired: bool):
    conn = _get_conn()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO velocity_snapshots (signals, convergence_count, convergence_level, alert_fired) VALUES (%s, %s, %s, %s)",
            (Json(signals), convergence_count, level, alert_fired)
        )
        conn.commit()
    except Exception as e:
        logger.error(f"push_velocity_snapshot failed: {e}")
        conn.rollback()
    finally:
        conn.close()


def push_gli_phase(gli_phase, gli_value_trn=None, transition_risk_score=None, fiscal_dominance_score=None, steno_regime=None, hy_spread_bps=None, ig_spread_bps=None, sofr_spread_bps=None, yield_curve_10_2=None, phase_changed=False, previous_phase=None):
    conn = _get_conn()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO gli_phases (gli_phase, gli_value_trn, transition_risk_score, fiscal_dominance_score, steno_regime, hy_spread_bps, ig_spread_bps, sofr_spread_bps, yield_curve_10_2, phase_changed, previous_phase)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (gli_phase, gli_value_trn, transition_risk_score, fiscal_dominance_score, steno_regime, hy_spread_bps, ig_spread_bps, sofr_spread_bps, yield_curve_10_2, phase_changed, previous_phase))
        conn.commit()
    except Exception as e:
        logger.error(f"push_gli_phase failed: {e}")
        conn.rollback()
    finally:
        conn.close()


def push_signal(source, source_name, title, summary, content_preview, tier, clusters, gli_phase):
    conn = _get_conn()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO signal_feed (source, source_name, title, summary, content_preview, tier, clusters, gli_phase_at_ingest)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (source, source_name, title, summary, content_preview, tier, clusters, gli_phase))
        conn.commit()
    except Exception as e:
        logger.error(f"push_signal failed: {e}")
        conn.rollback()
    finally:
        conn.close()


def push_morning_brief(brief_date, content, velocity_table, top_themes, gli_phase):
    conn = _get_conn()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO morning_briefs (brief_date, content, velocity_table, top_themes, gli_phase)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (brief_date) DO UPDATE SET content = EXCLUDED.content, velocity_table = EXCLUDED.velocity_table, top_themes = EXCLUDED.top_themes, gli_phase = EXCLUDED.gli_phase, generated_at = NOW()
        """, (brief_date, content, velocity_table, top_themes, gli_phase))
        conn.commit()
    except Exception as e:
        logger.error(f"push_morning_brief failed: {e}")
        conn.rollback()
    finally:
        conn.close()


def push_theme(theme_key, theme_label, mention_count, velocity_score, velocity_delta, is_flagged, clusters, gli_phase):
    conn = _get_conn()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO themes (theme_key, theme_label, first_seen_at, last_seen_at, mention_count, velocity_score, velocity_delta, is_flagged, clusters, gli_phase_at_emergence)
            VALUES (%s, %s, NOW(), NOW(), %s, %s, %s, %s, %s, %s)
            ON CONFLICT (theme_key) DO UPDATE SET last_seen_at = NOW(), mention_count = EXCLUDED.mention_count, velocity_score = EXCLUDED.velocity_score, velocity_delta = EXCLUDED.velocity_delta, is_flagged = EXCLUDED.is_flagged
        """, (theme_key, theme_label, mention_count, velocity_score, velocity_delta, is_flagged, clusters, gli_phase))
        conn.commit()
    except Exception as e:
        logger.error(f"push_theme failed: {e}")
        conn.rollback()
    finally:
        conn.close()


def push_document(source_name, source_type, title, content_text, content_hash, tier, clusters, themes, gli_phase, steno_regime, published_at, obsidian_path=None):
    conn = _get_conn()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO documents (source_name, source_type, title, content_text, content_hash, tier, clusters, themes, gli_phase, steno_regime, published_at, obsidian_path)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (content_hash) DO NOTHING
        """, (source_name, source_type, title, content_text, content_hash, tier, clusters, themes, gli_phase, steno_regime, published_at, obsidian_path))
        conn.commit()
    except Exception as e:
        logger.error(f"push_document failed: {e}")
        conn.rollback()
    finally:
        conn.close()
