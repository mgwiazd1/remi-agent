"""
Remi Narrative Intelligence — Scheduler
"""
import logging
import os
import sqlite3
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

from rss_poller import poll_all_feeds
from extraction_worker import run_extraction_worker
from obsidian_writer import write_all_completed

DB_PATH = os.getenv("DB_PATH", os.path.expanduser("~/remi-intelligence/remi_intelligence.db"))


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
        result = run_extraction_worker(max_docs=5)
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

        import requests
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "6625574871")

        lines = ["📊 *Daily Narrative Velocity Report*\n"]
        for label, score, mentions, flagged in rows:
            flag = "🔍 " if flagged else ""
            lines.append(f"{flag}*{label}*\n  v={score:.1f} | {mentions} mention(s)")

        text = "\n".join(lines)
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10
        )
        logger.info(f"Velocity report sent — {len(rows)} themes")
    except Exception as e:
        logger.error(f"Velocity report failed: {e}")


if __name__ == "__main__":
    scheduler = BlockingScheduler()

    # RSS poll every 6 hours
    scheduler.add_job(job_rss_poll, IntervalTrigger(hours=6),
                      id="rss_poll", max_instances=1, misfire_grace_time=600)

    # Extraction every 15 min
    scheduler.add_job(job_extraction, IntervalTrigger(hours=4),
                      id="extraction", max_instances=1, misfire_grace_time=120)

    # Daily report at 7am to personal chat
    scheduler.add_job(job_velocity_report, CronTrigger(hour=7, minute=0),
                      id="velocity_report")

    # Run extraction immediately on start to clear backlog

    logger.info("Scheduler starting — RSS every 6h, extraction every 4h, report at 7am")
