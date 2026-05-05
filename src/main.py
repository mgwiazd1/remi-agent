"""
Remi Narrative Intelligence — Scheduler
"""
import logging
import os
import sqlite3
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/remi-intelligence/.env"))
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s %(message)s',
    handlers=[
        logging.FileHandler(os.path.expanduser("~/remi-intelligence/logs/intelligence.log")),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
import requests
from datetime import datetime

from rss_poller import poll_all_feeds
from extraction_worker import run_extraction_worker
from obsidian_writer import write_all_completed
from pattern_detector import get_pattern_signal
from market_velocity import poll_all_signals
from velocity_aggregator import check_velocity_convergence
from media_ingestor import job_youtube_check, job_podcast_check, job_audio_drop_watch, job_media_extraction_worker
from x_scout import run_x_scout
from telegram_sender import send_investing_alert
from cross_theme_synthesis import run_cross_theme_synthesis
from book_ingestor import job_book_watcher
from instinct_extractor import run_instinct_extraction
from knowledge_collector import run_full_harvest
from picks_engine import run_picks_cycle, format_weekly_digest
from consuela_overnight import main as consuela_overnight_run
from aestima_push import push_theme_velocity_to_aestima
from sector_velocity import calculate_sector_velocity

DB_PATH = os.getenv("DB_PATH", os.path.expanduser("~/remi-intelligence/remi_intelligence.db"))
INVESTING_GROUP_CHAT_ID = os.getenv("INVESTING_GROUP_CHAT_ID", "-1003857050116")
MG_CLINICAL_CHAT_ID = os.getenv("MG_CLINICAL_CHAT_ID", "6625574871")
PATTERN_LOG_PATH = os.path.expanduser("~/remi-intelligence/logs/pattern_signal.log")


def job_rss_poll():
    logger.info("=== RSS POLL START ===")
    try:
        result = poll_all_feeds()
        logger.info(f"RSS poll complete: {result}")
    except Exception as e:
        logger.error(f"RSS poll failed: {e}")


def job_extraction():
    logger.info("=== EXTRACTION WORKER START ===")
    try:
        result = run_extraction_worker()
        if result.get("processed", 0) > 0:
            conn = sqlite3.connect(DB_PATH)
            write_result = write_all_completed(conn)
            conn.close()
            logger.info(f"Obsidian writes: {write_result}")
        logger.info(f"Extraction complete: {result}")
    except Exception as e:
        logger.error(f"Extraction failed: {e}")


def job_velocity_report():
    logger.info("=== DAILY VELOCITY REPORT ===")
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            SELECT theme_label, velocity_score, mention_count, is_flagged
            FROM themes ORDER BY velocity_score DESC LIMIT 10
        """)
        rows = cur.fetchall()
        conn.close()

        if not rows:
            logger.info("No themes yet")
            return

        lines = ["📊 *Daily Narrative Velocity Report*\n"]

        # GLI Phase line (single line, shows transition if recent)
        try:
            from gli_stamper import get_phase_brief_line
            lines.append(f"🌍 {get_phase_brief_line()}\n")
        except Exception as e:
            logger.warning(f"Phase brief line failed: {e}")

        for label, score, mentions, flagged in rows:
            flag = "🔍 " if flagged else ""
            lines.append(f"{flag}*{label}*\n  v={score:.1f} | {mentions} mention(s)")

        text = "\n".join(lines)
        send_investing_alert(text)
        logger.info(f"Velocity report sent to investing group — {len(rows)} themes")
    except Exception as e:
        logger.error(f"Velocity report failed: {e}")


def job_pattern_signal():
    """Daily pattern signal analysis - runs at 7:30am"""
    logger.info("=== PATTERN SIGNAL ANALYSIS ===")
    
    # Setup dedicated logger for pattern signals
    pattern_logger = logging.getLogger("pattern_signal")
    if not pattern_logger.handlers:
        pattern_handler = logging.FileHandler(PATTERN_LOG_PATH)
        pattern_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        pattern_logger.addHandler(pattern_handler)
        pattern_logger.setLevel(logging.INFO)
    
    try:
        # Run pattern detection
        result = get_pattern_signal(DB_PATH, lookback_days=7)
        
        # Log full output with timestamp
        pattern_logger.info("=" * 60)
        pattern_logger.info(f"Pattern Signal Analysis - {datetime.utcnow().isoformat()}")
        pattern_logger.info("=" * 60)
        
        pattern_logger.info(f"Top Themes: {len(result['top_themes'])}")
        for theme in result['top_themes'][:5]:
            pattern_logger.info(
                f"  - {theme['theme_label']}: v={theme['velocity_score']:.2f}, "
                f"mentions={theme['mention_count']}, sources={theme['source_count']}"
            )
        
        pattern_logger.info(f"Convergence Themes: {len(result['convergence'])}")
        for theme in result['convergence'][:3]:
            pattern_logger.info(
                f"  - {theme['theme_label']}: {theme['source_count']} sources, "
                f"v={theme['velocity_score']:.2f}"
            )
        
        pattern_logger.info(f"Second-Order Inferences: {len(result['second_order'])}")
        for inf in result['second_order']:
            pattern_logger.info(f"  - {inf['trigger_theme']}")
        
        if result['regime_context']:
            gli = result['regime_context']
            pattern_logger.info(
                f"Regime: GLI={gli.get('gli_phase', 'N/A')}, "
                f"Value=${gli.get('gli_value_bn', 0):.0f}B"
            )
        
        pattern_logger.info(f"Summary: {result['summary_text']}")
        
        if result['errors']:
            pattern_logger.warning(f"Errors: {result['errors']}")
        
        # Format and send to Telegram
        message = _format_pattern_telegram_message(result)
        
        # Send to investing group only
        send_investing_alert(message)
        
        logger.info("Pattern signal job completed successfully")
        
    except Exception as e:
        # Never raise - just log
        logger.error(f"Pattern signal job error: {e}", exc_info=True)
        if 'pattern_logger' in locals():
            pattern_logger.error(f"Pattern signal failed: {e}", exc_info=True)


def job_market_velocity():
    """Poll market velocity signals and check for convergence - runs every 4 hours"""
    logger.info("=== MARKET VELOCITY POLL START ===")
    try:
        # Poll all signals
        poll_result = poll_all_signals()
        logger.info(f"Market velocity poll complete: {len(poll_result.get('signals', {}))} signals polled")
        
        # Check for convergence and send alerts if triggered
        convergence_result = check_velocity_convergence()
        if convergence_result.get("alert_sent"):
            logger.info(f"Velocity convergence alert sent: {convergence_result['convergence_direction']}")
        else:
            logger.info(f"Convergence check: {convergence_result.get('reason', 'N/A')}")
        
    except Exception as e:
        logger.error(f"Market velocity job failed: {e}", exc_info=True)


def job_x_scout_t1():
    """X Scout Tier 1 accounts - runs every 4 hours"""
    logger.info("=== X SCOUT T1 POLL START ===")
    try:
        result = run_x_scout(tier_filter="t1")
        logger.info(f"X Scout T1 complete: {result}")
    except Exception as e:
        logger.error(f"X Scout T1 failed: {e}", exc_info=True)


def job_x_scout_t2plus():
    """X Scout Tier 2+ accounts - runs every 12 hours"""
    logger.info("=== X SCOUT T2+ POLL START ===")
    try:
        result = run_x_scout(tier_filter="t2plus")
        logger.info(f"X Scout T2+ complete: {result}")
    except Exception as e:
        logger.error(f"X Scout T2+ failed: {e}", exc_info=True)


def job_cross_theme_synthesis():
    """Weekly cross-theme synthesis — Sunday 9am"""
    try:
        logger.info("Running weekly cross-theme synthesis...")
        result = run_cross_theme_synthesis()
        if result:
            logger.info("Cross-theme synthesis complete")
        else:
            logger.warning("Cross-theme synthesis produced no output")
    except Exception as e:
        logger.error(f"Cross-theme synthesis failed: {e}")


def job_instinct_extraction():
    """Weekly instinct extraction — Sunday 9:15am"""
    try:
        logger.info("Running weekly instinct extraction...")
        result = run_instinct_extraction()
        if result:
            logger.info("Instinct extraction complete")
        else:
            logger.info("Instinct extraction: no repeating patterns yet")
    except Exception as e:
        logger.error(f"Instinct extraction failed: {e}")


def job_knowledge_harvest():
    """Harvest ticker signals from themes, PDFs, and Aestima — every 15 minutes"""
    logger.info("=== KNOWLEDGE HARVEST START ===")
    try:
        total = run_full_harvest()
        logger.info(f"Knowledge harvest complete: {total} new signals")
    except Exception as e:
        logger.error(f"Knowledge harvest failed: {e}", exc_info=True)


def job_picks_cycle():
    """Score tickers and send high-conviction alerts — every 6 hours"""
    logger.info("=== PICKS CYCLE START ===")
    try:
        alerts = run_picks_cycle()
        for a in alerts:
            send_investing_alert(a["message"])
            logger.info(f"Pick alert sent: {a['ticker']} ({a['level']})")
        logger.info(f"Picks cycle complete: {len(alerts)} high-conviction alerts sent")
    except Exception as e:
        logger.error(f"Picks cycle failed: {e}", exc_info=True)


def job_weekly_picks_digest():
    """Weekly picks digest — Sunday 9:30am"""
    logger.info("=== WEEKLY PICKS DIGEST ===")
    try:
        digest = format_weekly_digest(include_emerging=True)
        send_investing_alert(digest)
        logger.info("Weekly picks digest sent to investing group")
    except Exception as e:
        logger.error(f"Weekly picks digest failed: {e}")


def job_consuela_overnight():
    """Nightly vault triage + maintenance — 2am, NOT a boot job."""
    try:
        consuela_overnight_run()
    except Exception as e:
        logger.error(f"Consuela overnight failed: {e}")


def _format_pattern_telegram_message(result: dict) -> str:
    """Format pattern signal result for Telegram"""
    lines = []
    lines.append("📈 *Morning Pattern Brief*")
    lines.append("")
    
    # Top convergence theme
    if result['convergence']:
        top_conv = result['convergence'][0]
        lines.append(f"🎯 *Key Signal*: {top_conv['theme_label']}")
        lines.append(f"  Sources: {top_conv['source_count']} | Velocity: {top_conv['velocity_score']:.1f}")
        lines.append("")
    
    # Regime context
    if result['regime_context']:
        gli = result['regime_context']
        lines.append("🌍 *Regime Context*")
        lines.append(f"  GLI: {gli.get('gli_phase', 'N/A')} | ${gli.get('gli_value_bn', 0):.0f}B")
        lines.append(f"  Fiscal: {gli.get('fiscal_score', 'N/A')}/10")
        lines.append("")
    
    # Investor brief
    lines.append("💡 *Investor Brief*")
    lines.append(result['summary_text'])
    lines.append("")
    
    # Additional themes
    if len(result['convergence']) > 1:
        lines.append("📊 *Other Signals*")
        for theme in result['convergence'][1:4]:
            lines.append(f"  • {theme['theme_label']} ({theme['source_count']} src)")
    
    # X Scout summary
    if result.get('x_scout_summary'):
        x = result['x_scout_summary']
        lines.append("")
        lines.append("📡 *X SCOUT (last 24h)*")
        lines.append("─" * 21)
        lines.append(f"Accounts polled: {x.get('t1_accounts_polled', 0)}")
        lines.append(f"New tweets ingested: {x.get('tweets_ingested', 0)}")
        lines.append(f"Themes extracted: {x.get('themes_extracted', 0)}")
        
        co_occurrences = x.get('co_occurrences', [])
        if co_occurrences:
            lines.append("")
            lines.append(f"🔭 Co-occurrences detected: {len(co_occurrences)}")
            for i, co in enumerate(co_occurrences[:3], 1):
                theme_label = co.get('theme_label', 'Unknown')
                sources = co.get('t1_sources', [])
                sources_str = ' + '.join(sources[:3]) if sources else 'N/A'
                lines.append(f"  {i}. {theme_label} — {sources_str}")
        else:
            lines.append("")
    
    # Vault triage summary (if Consuela ran overnight)
    try:
        triage_today = Path(
            "/docker/obsidian/investing/Intelligence/_triage"
        ) / f"TRIAGE_{datetime.now().strftime('%Y-%m-%d')}.md"
        if triage_today.exists():
            conn = sqlite3.connect(DB_PATH)
            pending_count = conn.execute(
                "SELECT COUNT(*) FROM triage_items "
                "WHERE report_date = ? AND status = 'pending'",
                (datetime.now().strftime('%Y-%m-%d'),)
            ).fetchone()[0]
            conn.close()
            if pending_count > 0:
                lines.append("")
                lines.append(
                    f"🧹 Consuela flagged {pending_count} vault items "
                    f"for triage — /vault triage to review")
    except Exception:
        pass
    
    return "\n".join(lines)


if __name__ == "__main__":
    scheduler = BlockingScheduler()

    # RSS poll every 6 hours
    scheduler.add_job(job_rss_poll, IntervalTrigger(hours=6),
                      id="rss_poll", max_instances=1, misfire_grace_time=600)

    # Extraction every 4 hours
    scheduler.add_job(job_extraction, IntervalTrigger(hours=4),
                      id="extraction", max_instances=1, misfire_grace_time=120)

    # Market velocity poll every 4 hours
    scheduler.add_job(job_market_velocity, IntervalTrigger(hours=4),
                      id="market_velocity", max_instances=1, misfire_grace_time=120)

    # X Scout — Tier 1 accounts every 4 hours
    scheduler.add_job(job_x_scout_t1, IntervalTrigger(hours=4),
                      id="x_scout_t1", max_instances=1, misfire_grace_time=600)

    # X Scout — Tier 2+ accounts every 12 hours
    scheduler.add_job(job_x_scout_t2plus, IntervalTrigger(hours=12),
                      id="x_scout_t2", max_instances=1, misfire_grace_time=600)

    # Daily report at 7am to personal chat
    scheduler.add_job(job_velocity_report, CronTrigger(hour=7, minute=0),
                      id="velocity_report")

    # Pattern signal at 7:30am (after velocity report)
    scheduler.add_job(job_pattern_signal, CronTrigger(hour=7, minute=30),
                      id="pattern_signal")

    # Weekly cross-theme synthesis — Sunday 9am
    scheduler.add_job(job_cross_theme_synthesis, CronTrigger(day_of_week="sun", hour=9, minute=0),
                      id="cross_theme_synthesis", max_instances=1)

    # Weekly instinct extraction — Sunday 9:15am (after cross-theme synthesis)
    scheduler.add_job(job_instinct_extraction, CronTrigger(day_of_week="sun", hour=9, minute=15),
                      id="instinct_extraction", max_instances=1)

    # Media Intelligence Pipeline — YouTube channel checker every 12 hours
    scheduler.add_job(job_youtube_check, IntervalTrigger(hours=12),
                      id="media_youtube_check", max_instances=1, misfire_grace_time=600)

    # Media Intelligence Pipeline — Podcast feed checker every 12 hours
    scheduler.add_job(job_podcast_check, IntervalTrigger(hours=12),
                      id="media_podcast_check", max_instances=1, misfire_grace_time=600)

    # Media Intelligence Pipeline — Audio drop watcher every 5 minutes
    scheduler.add_job(job_audio_drop_watch, IntervalTrigger(minutes=5),
                      id="media_audio_drop_watcher", max_instances=1)

    # Media Intelligence Pipeline — Media extraction worker every 15 minutes
    scheduler.add_job(job_media_extraction_worker, IntervalTrigger(minutes=15),
                      id="media_extraction_worker", max_instances=1, misfire_grace_time=300)

    # Knowledge harvest — every 15 minutes
    scheduler.add_job(job_knowledge_harvest, IntervalTrigger(minutes=15),
                      id="knowledge_harvest", max_instances=1, misfire_grace_time=120)

    # Picks scoring cycle — every 6 hours
    scheduler.add_job(job_picks_cycle, IntervalTrigger(hours=6),
                      id="picks_cycle", max_instances=1, misfire_grace_time=300)

    # Weekly picks digest — Sunday 9:30am (after instinct extraction)
    scheduler.add_job(job_weekly_picks_digest, CronTrigger(day_of_week="sun", hour=9, minute=30),
                      id="weekly_picks_digest", max_instances=1)

    # Book ingestion — check for new book PDFs every 30 minutes
    scheduler.add_job(job_book_watcher, IntervalTrigger(minutes=30),
                      id="book_watcher", max_instances=1, misfire_grace_time=300)

    # Consuela overnight vault triage — 2am nightly (NOT a boot job)
    scheduler.add_job(job_consuela_overnight, CronTrigger(hour=2, minute=0),
                      id="consuela_overnight", name="consuela_overnight",
                      replace_existing=True, misfire_grace_time=3600)

    # Aestima theme velocity push — every 4 hours
    def job_aestima_theme_push():
        logger.info("=== AESTIMA THEME PUSH ===")
        try:
            push_theme_velocity_to_aestima()
        except Exception as e:
            logger.error(f"Aestima theme push failed: {e}")

    scheduler.add_job(job_aestima_theme_push, IntervalTrigger(hours=4),
                      id="aestima_theme_push", replace_existing=True,
                      misfire_grace_time=3600)

    # Sector velocity + sentiment drift — every 4 hours (runs alongside theme push)
    def job_sector_velocity():
        logger.info("=== SECTOR VELOCITY ===")
        try:
            calculate_sector_velocity()
        except Exception as e:
            logger.error(f"Sector velocity calculation failed: {e}")

    scheduler.add_job(job_sector_velocity, IntervalTrigger(hours=4),
                      id="sector_velocity", replace_existing=True,
                      misfire_grace_time=3600)

    # Boot jobs — run immediately on start
    scheduler.add_job(job_rss_poll, "date", id="rss_poll_boot")
    scheduler.add_job(job_extraction, "date", id="extraction_boot")
    scheduler.add_job(job_market_velocity, "date", id="market_velocity_boot")
    scheduler.add_job(job_media_extraction_worker, "date", id="media_extraction_worker_boot")

    logger.info("Scheduler starting — RSS every 6h, extraction every 4h, market velocity every 4h, X Scout T1 every 4h / T2+ every 12h, velocity at 7am, pattern signal at 7:30am, YouTube/podcasts every 12h, audio drop every 5m, knowledge harvest every 15m, picks cycle every 6h, weekly picks digest Sunday 9:30am, book watcher every 30m, consuela overnight at 2am, Aestima theme push + sector velocity every 4h")
    scheduler.start()
