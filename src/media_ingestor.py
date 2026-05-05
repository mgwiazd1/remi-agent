"""
Remi Media Intelligence Ingestor
Handles YouTube, podcast, and audio file transcription + extraction pipeline
"""
import argparse
import hashlib
import json
import logging
import os
import re
import sqlite3
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dotenv import load_dotenv

# Load environment
load_dotenv(os.path.expanduser("~/remi-intelligence/.env"))

# Logging setup
logger = logging.getLogger(__name__)

# Environment variables
DB_PATH = os.getenv("DB_PATH", os.path.expanduser("~/remi-intelligence/remi_intelligence.db"))
VAULT_PATH = os.getenv("OBSIDIAN_VAULT_PATH", "/docker/obsidian/investing/Intelligence")
CONFIG_PATH = os.path.expanduser("~/remi-intelligence/config")
LOG_PATH = os.path.expanduser("~/remi-intelligence/logs/intelligence.log")
TEMP_DIR = "/tmp/remi_media"

# Imports from existing pipeline
try:
    from llm_extractor import extract_themes, extract_themes_chunked, extract_second_order
    from gli_stamper import fetch_gli_stamp
    from obsidian_writer import write_document_note
except ImportError as e:
    logger.error(f"Failed to import pipeline modules: {e}")
    raise

# Third-party imports
try:
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api.formatters import TextFormatter
    import feedparser
    import requests
except ImportError as e:
    logger.error(f"Missing dependency: {e}")
    raise


# ============================================================================
# MEDIA NOTE WRITING
# ============================================================================

def write_media_note(document_id, job_id, transcript, themes_data, gli_stamp, media_url, title, media_type, transcript_method):
    try:
        import re
        media_vault = os.path.join(VAULT_PATH, "Media")
        os.makedirs(media_vault, exist_ok=True)
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        safe_title = re.sub(r'[^\w\s\-]', '', title or 'Untitled')[:60].strip()
        filename = f"{date_str} — {media_type.capitalize()} — {safe_title}.md"
        filepath = os.path.join(media_vault, filename)
        themes_section = ""
        for t in themes_data.get('themes', []):
            themes_section += f"\n### {t.get('theme_label', 'Unknown')}\n"
            for fact in t.get('facts', [])[:3]:
                themes_section += f"- **Fact:** {fact}\n"
            for opinion in t.get('opinions', [])[:2]:
                themes_section += f"- **Opinion:** {opinion}\n"
        note = f"---\ntype: media_transcript\nsource_type: {media_type}\nmedia_url: {media_url}\ntitle: {title or 'Unknown'}\ningested_at: {datetime.utcnow().isoformat()}\ngli_phase: {gli_stamp.gli_phase if gli_stamp else 'unavailable'}\nsteno_regime: {gli_stamp.steno_regime if gli_stamp else 'unavailable'}\ntranscript_method: {transcript_method}\ndocument_id: {document_id}\n---\n\n# {title or 'Media Transcript'}\n\n## Themes\n{themes_section or '_None_'}\n\n## Transcript\n\n{transcript}\n"
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(note)
        logger.info(f"Media note written: {filepath}")
        return filepath
    except Exception as e:
        logger.error(f"write_media_note failed: {e}")
        return None


# ============================================================================
# UTILITIES
# ============================================================================

def ensure_temp_dir():
    """Create temp directory if missing."""
    Path(TEMP_DIR).mkdir(parents=True, exist_ok=True)


def cleanup_temp_dir():
    """Delete files older than 1 hour from temp directory."""
    try:
        if not os.path.exists(TEMP_DIR):
            return
        now = datetime.utcnow().timestamp()
        for fname in os.listdir(TEMP_DIR):
            fpath = os.path.join(TEMP_DIR, fname)
            if os.path.isfile(fpath):
                mtime = os.path.getmtime(fpath)
                age_secs = now - mtime
                if age_secs > 3600:  # 1 hour
                    try:
                        os.remove(fpath)
                        logger.debug(f"Cleaned up temp file: {fname}")
                    except Exception as e:
                        logger.warning(f"Failed to clean {fname}: {e}")
    except Exception as e:
        logger.warning(f"Temp cleanup error: {e}")


def content_hash(text: str) -> str:
    """Generate SHA256 hash of content."""
    return hashlib.sha256(text.encode()).hexdigest()


def url_hash(url: str) -> str:
    """Generate SHA256 hash of URL."""
    return hashlib.sha256(url.encode()).hexdigest()


def is_duplicate_media(conn: sqlite3.Connection, chash: str) -> bool:
    """Check if media already processed by content_hash."""
    cur = conn.cursor()
    cur.execute("SELECT id FROM media_jobs WHERE content_hash = ?", (chash,))
    return cur.fetchone() is not None


def safe_filename(s: str, max_len: int = 60) -> str:
    """Create safe filename from string."""
    s = re.sub(r'[^\w\s\-]', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    s = s[:max_len]
    return s


# ============================================================================
# YOUTUBE TRANSCRIPT FETCHING
# ============================================================================

def extract_video_id(url: str) -> Optional[str]:
    """Extract video ID from various YouTube URL formats."""
    patterns = [
        r'(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/shorts\/)([^\&\?\/]+)',
        r'youtube\.com\/embed\/([^\&\?\/]+)',
        r'youtube\.com\/live\/([^\&\?\/]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    # Try as raw 11-char video ID
    if re.match(r'^[a-zA-Z0-9_\-]{11}$', url):
        return url
    return None


def fetch_youtube_transcript(url: str) -> Dict:
    """
    Fetch YouTube transcript using youtube-transcript-api.
    Falls back to whisper if captions disabled.
    
    Returns:
        {
            "transcript": "full text",
            "method": "transcript_api" | "whisper",
            "video_id": "...",
            "language": "en",
            "duration_secs": int or None,
            "error": None or error_msg
        }
    """
    result = {
        "transcript": "",
        "method": None,
        "video_id": None,
        "language": None,
        "duration_secs": None,
        "error": None
    }
    
    try:
        video_id = extract_video_id(url)
        if not video_id:
            result["error"] = f"Could not extract video ID from {url}"
            return result
        
        result["video_id"] = video_id
        
        # Try transcript API first (instant, no cost)
        try:
            ytt = YouTubeTranscriptApi()
            transcript_obj = ytt.fetch(video_id)
            transcript = [{"text": s.text, "start": s.start, "duration": s.duration} for s in transcript_obj]
            # Join all text parts
            text_parts = [entry['text'] for entry in transcript]
            full_text = ' '.join(text_parts)
            
            # Clean up artifacts
            full_text = re.sub(r'\[Music\]|\[Applause\]|\[Laughter\]|\[.*?\]', '', full_text)
            full_text = re.sub(r'\s+', ' ', full_text).strip()
            
            result["transcript"] = full_text
            result["method"] = "transcript_api"
            result["language"] = "en"
            logger.info(f"✅ Transcript fetched via API for {video_id}")
            return result
        
        except Exception as api_err:
            logger.warning(f"Transcript API failed ({video_id}): {type(api_err).__name__}. Trying whisper...")
        
        # Fallback: download audio via yt-dlp and transcribe with whisper
        logger.info(f"Downloading audio for {video_id} to transcribe with whisper...")
        ensure_temp_dir()
        
        audio_file = os.path.join(TEMP_DIR, f"{video_id}.mp3")
        try:
            # Download audio only
            cmd = [
                "yt-dlp",
                "-x",
                "--audio-format", "mp3",
                "--audio-quality", "5",
                "-o", audio_file,
                f"https://www.youtube.com/watch?v={video_id}"
            ]
            subprocess.run(cmd, check=True, capture_output=True, timeout=300)
            
            if not os.path.exists(audio_file):
                result["error"] = f"yt-dlp failed to download audio for {video_id}"
                return result
            
            # Transcribe with whisper
            transcript_text = transcribe_audio_file(audio_file)
            if transcript_text:
                result["transcript"] = transcript_text
                result["method"] = "whisper"
                logger.info(f"✅ Transcribed via whisper for {video_id}")
            else:
                result["error"] = f"Whisper transcription failed for {video_id}"
            
            return result
        
        except subprocess.TimeoutExpired:
            result["error"] = f"yt-dlp timeout downloading {video_id}"
            return result
        except Exception as e:
            result["error"] = f"Audio download/transcription failed: {e}"
            return result
        finally:
            # Clean up audio file immediately
            if os.path.exists(audio_file):
                try:
                    os.remove(audio_file)
                except Exception as e:
                    logger.warning(f"Failed to delete temp audio {audio_file}: {e}")
    
    except Exception as e:
        result["error"] = f"Unexpected error in fetch_youtube_transcript: {e}"
        logger.error(result["error"])
        return result


# ============================================================================
# AUDIO TRANSCRIPTION
# ============================================================================

def transcribe_audio_file(filepath: str) -> Optional[str]:
    """
    Transcribe audio file using faster-whisper (large-v3) with GPU/CPU fallback.
    
    Returns:
        Transcript string or None on error
    """
    try:
        from faster_whisper import WhisperModel
        
        if not os.path.exists(filepath):
            logger.error(f"Audio file not found: {filepath}")
            return None
        
        # File size check (< 200MB)
        file_size_mb = os.path.getsize(filepath) / (1024 * 1024)
        if file_size_mb > 200:
            logger.error(f"Audio file too large ({file_size_mb:.1f}MB > 200MB): {filepath}")
            return None
        
        # Try GPU first, fall back to CPU
        try:
            logger.info(f"Transcribing {filepath} with faster-whisper large-v3 (CUDA)...")
            model = WhisperModel("large-v3", device="cuda", compute_type="int8")
            logger.info("Loaded faster-whisper large-v3 on CUDA")
        except Exception as gpu_err:
            logger.warning(f"CUDA unavailable ({gpu_err}), falling back to CPU")
            model = WhisperModel("large-v3", device="cpu", compute_type="int8")
            logger.info("Loaded faster-whisper large-v3 on CPU")
        
        segments, info = model.transcribe(filepath, language="en")
        transcript = " ".join(segment.text for segment in segments).strip()
        
        if not transcript:
            logger.warning(f"Whisper returned empty transcript for {filepath}")
            return None
        
        logger.info(f"✅ Transcribed {filepath} ({len(transcript)} chars)")
        return transcript
    
    except Exception as e:
        logger.error(f"Whisper transcription error for {filepath}: {e}")
        return None


def transcribe_audio_url(audio_url: str, title: str = "audio") -> Optional[str]:
    """
    Download audio from URL and transcribe with whisper.
    Prefers yt-dlp (handles redirects, content-neg, partial content) over requests.
    
    Returns:
        Transcript string or None on error
    """
    ensure_temp_dir()
    temp_file = os.path.join(TEMP_DIR, f"{safe_filename(title)}.mp3")
    
    try:
        # Try yt-dlp first (robust against redirects, content negotiation, partial content)
        logger.info(f"Downloading audio from {audio_url[:60]}... (trying yt-dlp)")
        try:
            cmd = [
                "yt-dlp",
                "-x",
                "--audio-format", "mp3",
                "--audio-quality", "5",
                "-o", temp_file,
                audio_url
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            
            if result.returncode == 0:
                logger.info(f"✅ yt-dlp download succeeded for {audio_url[:60]}")
                download_succeeded = True
            else:
                logger.warning(f"yt-dlp failed (code {result.returncode}): {result.stderr[:200]}")
                download_succeeded = False
        
        except FileNotFoundError:
            logger.warning("yt-dlp not found in PATH, falling back to requests")
            download_succeeded = False
        except subprocess.TimeoutExpired:
            logger.warning("yt-dlp timeout, falling back to requests")
            download_succeeded = False
        
        # Fallback: requests if yt-dlp unavailable or failed
        if not download_succeeded:
            logger.info(f"Downloading audio from {audio_url[:60]}... (using requests fallback)")
            response = requests.get(audio_url, stream=True, timeout=30, allow_redirects=True)
            
            # Check size before downloading
            content_length = response.headers.get('content-length')
            if content_length:
                size_mb = int(content_length) / (1024 * 1024)
                if size_mb > 200:
                    logger.error(f"Audio too large ({size_mb:.1f}MB > 200MB)")
                    return None
            
            # Write to temp file with chunk size tracking
            bytes_written = 0
            with open(temp_file, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        bytes_written += len(chunk)
            
            if bytes_written == 0:
                logger.error(f"Download produced 0 bytes for {audio_url[:60]}")
                return None
        
        # Verify file exists and has minimum size
        if not os.path.exists(temp_file):
            logger.error(f"Downloaded file not found: {temp_file}")
            return None
        
        file_size_bytes = os.path.getsize(temp_file)
        if file_size_bytes < 102400:  # 100KB minimum
            logger.error(f"Downloaded file too small ({file_size_bytes} bytes < 100KB): likely corrupt/incomplete")
            return None
        
        logger.info(f"Downloaded file size: {file_size_bytes / 1024:.1f}KB")
        
        # Transcribe
        transcript = transcribe_audio_file(temp_file)
        return transcript
    
    except Exception as e:
        logger.error(f"Audio download/transcription error for {audio_url}: {e}")
        return None
    finally:
        # Clean up immediately
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except Exception:
                pass


# ============================================================================
# YOUTUBE CHANNEL CHECKING
# ============================================================================

def check_youtube_channel_for_new_videos(channel_config: Dict) -> List[Dict]:
    """
    Check YouTube channel for new videos using yt-dlp.
    
    Returns:
        List of new video dicts:
        [{
            "video_id": "...",
            "title": "...",
            "url": "https://www.youtube.com/watch?v=...",
            "published_at": "2026-03-29T...",
            "duration": 300  # seconds
        }]
    """
    new_videos = []
    channel_id = channel_config.get("channel_id")
    max_videos = channel_config.get("max_videos_per_check", 3)
    
    if not channel_id:
        logger.error(f"No channel_id in config for {channel_config.get('name')}")
        return []
    
    try:
        # Use yt-dlp to fetch recent videos without downloading
        channel_url = f"https://www.youtube.com/channel/{channel_id}"
        cmd = [
            "yt-dlp",
            "--flat-playlist",
            f"--playlist-end={max_videos}",
            "-j",  # JSON output
            channel_url
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            logger.error(f"yt-dlp failed for {channel_config.get('name')}: {result.stderr[:200]}")
            return []
        
        # Parse JSON output
        conn = sqlite3.connect(DB_PATH)
        for line in result.stdout.strip().split('\n'):
            if not line.strip():
                continue
            
            try:
                entry = json.loads(line)
                video_id = entry.get('id')
                title = entry.get('title', 'Unknown')
                url = f"https://www.youtube.com/watch?v={video_id}"
                
                # Check if already processed
                chash = url_hash(url)
                if is_duplicate_media(conn, chash):
                    logger.debug(f"Skipping already-processed video: {title}")
                    continue
                
                # Extract duration if available
                duration = entry.get('duration', 0)
                
                new_videos.append({
                    "video_id": video_id,
                    "title": title,
                    "url": url,
                    "published_at": entry.get('upload_date', datetime.utcnow().isoformat()[:10]),
                    "duration": duration
                })
            
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse yt-dlp JSON line: {line[:100]}")
                continue
        
        conn.close()
        logger.info(f"Found {len(new_videos)} new videos for {channel_config.get('name')}")
        return new_videos
    
    except subprocess.TimeoutExpired:
        logger.error(f"yt-dlp timeout for {channel_config.get('name')}")
        return []
    except Exception as e:
        logger.error(f"Error checking YouTube channel {channel_config.get('name')}: {e}")
        return []


# ============================================================================
# PODCAST FEED CHECKING
# ============================================================================

def check_podcast_feeds_for_new_episodes(feeds: List[Dict]) -> List[Dict]:
    """
    Check podcast feeds for new episodes with audio enclosures.
    
    Returns:
        List of new episode dicts:
        [{
            "title": "...",
            "podcast_name": "...",
            "audio_url": "...",
            "published_at": "2026-03-29T...",
            "duration": 3600  # seconds
        }]
    """
    new_episodes = []
    conn = sqlite3.connect(DB_PATH)
    
    for feed_config in feeds:
        if not feed_config.get("active", True):
            continue
        
        feed_name = feed_config.get("name")
        feed_url = feed_config.get("url")
        max_episodes = feed_config.get("max_episodes_per_check", 2)
        
        try:
            logger.debug(f"Polling podcast feed: {feed_name}")
            parsed = feedparser.parse(feed_url)
            entries = parsed.entries or []
            
            for entry in entries[:max_episodes]:
                # Look for audio enclosure
                audio_url = None
                for enclosure in entry.get('enclosures', []):
                    if enclosure.get('type', '').startswith('audio/'):
                        audio_url = enclosure.get('href')
                        break
                
                if not audio_url:
                    logger.debug(f"No audio enclosure in {feed_name}: {entry.get('title', 'Unknown')}")
                    continue
                
                # Check if already processed
                chash = url_hash(audio_url)
                if is_duplicate_media(conn, chash):
                    logger.debug(f"Skipping already-processed episode: {entry.get('title')}")
                    continue
                
                # Parse published date
                pub_date = entry.get('published', datetime.utcnow().isoformat())
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    pub_date = datetime(*entry.published_parsed[:6]).isoformat()
                
                new_episodes.append({
                    "title": entry.get('title', 'Unknown'),
                    "podcast_name": feed_name,
                    "audio_url": audio_url,
                    "published_at": pub_date,
                    "duration": entry.get('itunes_duration', 0)
                })
        
        except Exception as e:
            logger.error(f"Error polling podcast feed {feed_name}: {e}")
            continue
    
    conn.close()
    logger.info(f"Found {len(new_episodes)} new podcast episodes")
    return new_episodes


# ============================================================================
# AUDIO DROP PROCESSING
# ============================================================================

def process_audio_drop(filepath: str) -> Dict:
    """
    Process audio file dropped in watch directory.
    
    Returns:
        {
            "success": bool,
            "transcript": str or None,
            "title": str,
            "error": str or None
        }
    """
    result = {
        "success": False,
        "transcript": None,
        "title": None,
        "error": None
    }
    
    if not os.path.exists(filepath):
        result["error"] = f"File not found: {filepath}"
        return result
    
    try:
        # Extract filename as title
        title = os.path.splitext(os.path.basename(filepath))[0]
        result["title"] = title
        
        # Transcribe
        transcript = transcribe_audio_file(filepath)
        if transcript:
            result["transcript"] = transcript
            result["success"] = True
        else:
            result["error"] = "Transcription returned empty"
        
        return result
    
    except Exception as e:
        result["error"] = str(e)
        return result


# ============================================================================
# MEDIA JOB PROCESSING (PIPELINE TAIL)
# ============================================================================

def process_media_job(job_id: int) -> Dict:
    """
    Full media processing pipeline:
    1. Fetch transcript from media_jobs
    2. Call llm_extractor.extract_themes
    3. Call gli_stamper.fetch_gli_stamp
    4. Call obsidian_writer to write vault note
    
    Returns:
        {
            "success": bool,
            "document_id": int or None,
            "vault_path": str or None,
            "error": str or None
        }
    """
    result = {
        "success": False,
        "document_id": None,
        "vault_path": None,
        "error": None
    }
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    try:
        # Fetch job
        cur.execute("""
            SELECT id, media_type, title, transcript_text, media_url
            FROM media_jobs WHERE id = ?
        """, (job_id,))
        row = cur.fetchone()
        
        if not row:
            result["error"] = f"Media job {job_id} not found"
            conn.close()
            return result
        
        job_id_db, media_type, title, transcript, media_url = row
        
        if not transcript:
            result["error"] = f"No transcript available for job {job_id}"
            conn.close()
            return result
        
        # Determine source info from media_type
        source_name = f"{media_type.capitalize()} — {title or 'Unknown'}"
        source_tier = 2  # Default tier for media
        source_type = media_type  # youtube, podcast, audio_drop
        
        logger.info(f"Processing media job {job_id}: {source_name}")
        
        # STEP 1: Extract themes (chunked for long transcripts)
        try:
            gli_context = "GLI context available on extraction"
            transcript_len = len(transcript)
            if transcript_len > 12000:
                logger.info(f"Long transcript ({transcript_len} chars) — using chunked extraction")
                themes_data = extract_themes_chunked(
                    content=transcript,
                    source_name=source_name,
                    source_tier=source_tier,
                    gli_context=gli_context,
                )
            else:
                themes_data = extract_themes(
                    content=transcript,
                    source_name=source_name,
                    source_tier=source_tier,
                    gli_context=gli_context,
                )
            if not themes_data:
                result["error"] = "Theme extraction returned None"
                conn.close()
                return result
            logger.debug(f"Extracted {len(themes_data.get('themes', []))} themes")
        except Exception as e:
            result["error"] = f"Theme extraction failed: {e}"
            logger.error(result["error"])
            conn.close()
            return result
        
        # STEP 2: Get GLI stamp
        try:
            gli_stamp = fetch_gli_stamp()
            logger.debug(f"GLI stamp: {gli_stamp.gli_phase} @ ${gli_stamp.gli_value_bn}B")
        except Exception as e:
            logger.warning(f"GLI stamp fetch failed: {e} — using defaults")
            gli_stamp = None
        
        # STEP 3: Insert document record
        try:
            published_at = datetime.utcnow().isoformat()
            chash = content_hash(transcript)
            
            cur.execute("""
                INSERT OR IGNORE INTO documents
                (source_url, source_name, source_tier, source_type, title, content_text,
                 content_hash, published_at, ingested_at, gli_phase, gli_value_bn, steno_regime, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed')
            """, (
                media_url,
                source_name,
                source_tier,
                source_type,
                title,
                transcript,  # Full transcript — no truncation
                chash,
                published_at,
                datetime.utcnow().isoformat(),
                gli_stamp.gli_phase if gli_stamp else None,
                gli_stamp.gli_value_bn if gli_stamp else None,
                gli_stamp.steno_regime if gli_stamp else None
            ))
            conn.commit()
            document_id = cur.lastrowid
            logger.info(f"Created document record {document_id}")
            result["document_id"] = document_id
        except Exception as e:
            result["error"] = f"Document insertion failed: {e}"
            logger.error(result["error"])
            conn.close()
            return result
        
        # STEP 4: Write theme document records
        try:
            for theme in themes_data.get('themes', []):
                theme_key = theme.get('theme_key', 'unknown').lower()
                theme_label = theme.get('theme_label', 'Unknown Theme')
                
                # Get or create theme
                cur.execute("SELECT id FROM themes WHERE theme_key = ?", (theme_key,))
                theme_row = cur.fetchone()
                if theme_row:
                    theme_id = theme_row[0]
                    cur.execute("""
                        UPDATE themes SET last_seen_at = ?, mention_count = mention_count + 1
                        WHERE id = ?
                    """, (datetime.utcnow().isoformat(), theme_id))
                else:
                    cur.execute("""
                        INSERT INTO themes
                        (theme_key, theme_label, first_seen_at, last_seen_at,
                         gli_phase_at_emergence, steno_regime_at_emergence)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        theme_key,
                        theme_label,
                        datetime.utcnow().isoformat(),
                        datetime.utcnow().isoformat(),
                        gli_stamp.gli_phase if gli_stamp else None,
                        gli_stamp.steno_regime if gli_stamp else None
                    ))
                    theme_id = cur.lastrowid
                
                # Insert document-theme link
                cur.execute("""
                    INSERT INTO document_themes
                    (document_id, theme_id, facts, opinions, key_quotes, tickers_mentioned, sentiment, historical_analog)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    document_id,
                    theme_id,
                    json.dumps(theme.get('facts', [])),
                    json.dumps(theme.get('opinions', [])),
                    json.dumps([theme.get('key_quote', '')]) if theme.get('key_quote') else json.dumps([]),
                    json.dumps(theme.get('tickers_mentioned', [])),
                    theme.get('sentiment', 'neutral'),
                    theme.get('historical_analog', 'none')
                ))
            
            conn.commit()
            logger.info(f"Wrote {len(themes_data.get('themes', []))} theme records")
        except Exception as e:
            logger.error(f"Theme record insertion failed: {e}")
        
        # STEP 5: Call write_media_note
        try:
            vault_note_path = write_media_note(
                document_id=document_id, job_id=job_id, transcript=transcript,
                themes_data=themes_data, gli_stamp=gli_stamp, media_url=media_url,
                title=title, media_type=media_type, transcript_method="unknown"
            )
            if vault_note_path:
                result["vault_path"] = vault_note_path
                result["success"] = True
                logger.info(f"Vault note written: {vault_note_path}")
            else:
                logger.warning(f"write_media_note returned None for {document_id}")
        except Exception as e:
            result["error"] = f"Obsidian write failed: {e}"
            logger.error(result["error"])
        
        return result
    
    except Exception as e:
        result["error"] = f"Unexpected error in process_media_job: {e}"
        logger.error(result["error"])
        return result
    finally:
        conn.close()


# ============================================================================
# SCHEDULED JOBS (for APScheduler)
# ============================================================================

def job_youtube_check():
    """Scheduled job: check YouTube channels for new videos."""
    logger.info("=== YOUTUBE CHECK START ===")
    cleanup_temp_dir()
    
    try:
        config_path = os.path.join(CONFIG_PATH, "youtube_channels.json")
        if not os.path.exists(config_path):
            logger.warning(f"YouTube config not found: {config_path}")
            return {"new_videos": 0, "errors": 0}
        
        with open(config_path) as f:
            config = json.load(f)
        
        channels = config.get("channels", [])
        total_new = 0
        errors = 0
        
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        
        for channel_config in channels:
            if not channel_config.get("active", True):
                continue
            
            new_videos = check_youtube_channel_for_new_videos(channel_config)
            total_new += len(new_videos)
            
            # Insert media jobs
            for video in new_videos:
                try:
                    chash = url_hash(video['url'])
                    cur.execute("""
                        INSERT INTO media_jobs
                        (media_type, media_url, title, duration_secs, status)
                        VALUES (?, ?, ?, ?, 'pending')
                    """, (
                        'youtube',
                        video['url'],
                        video['title'],
                        video.get('duration', 0)
                    ))
                except Exception as e:
                    logger.error(f"Failed to insert media job for {video['title']}: {e}")
                    errors += 1
        
        conn.commit()
        conn.close()
        logger.info(f"YouTube check complete: {total_new} new videos, {errors} errors")
        return {"new_videos": total_new, "errors": errors}
    
    except Exception as e:
        logger.error(f"YouTube check failed: {e}")
        return {"new_videos": 0, "errors": 1}


def job_podcast_check():
    """Scheduled job: check podcast feeds for new episodes."""
    logger.info("=== PODCAST CHECK START ===")
    cleanup_temp_dir()
    
    try:
        config_path = os.path.join(CONFIG_PATH, "rss_feeds.json")
        if not os.path.exists(config_path):
            logger.warning(f"RSS config not found: {config_path}")
            return {"new_episodes": 0, "errors": 0}
        
        with open(config_path) as f:
            config = json.load(f)
        
        podcasts = config.get("podcasts", [])
        new_episodes = check_podcast_feeds_for_new_episodes(podcasts)
        
        # Insert media jobs
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        errors = 0
        
        for episode in new_episodes:
            try:
                chash = url_hash(episode['audio_url'])
                cur.execute("""
                    INSERT INTO media_jobs
                    (media_type, media_url, title, duration_secs, status)
                    VALUES (?, ?, ?, ?, 'pending')
                """, (
                    'podcast',
                    episode['audio_url'],
                    episode['title'],
                    episode.get('duration', 0)
                ))
            except Exception as e:
                logger.error(f"Failed to insert media job for {episode['title']}: {e}")
                errors += 1
        
        conn.commit()
        conn.close()
        logger.info(f"Podcast check complete: {len(new_episodes)} new episodes, {errors} errors")
        return {"new_episodes": len(new_episodes), "errors": errors}
    
    except Exception as e:
        logger.error(f"Podcast check failed: {e}")
        return {"new_episodes": 0, "errors": 1}


def job_audio_drop_watch():
    """Scheduled job: watch for audio files in watch/media/ directory."""
    logger.info("=== AUDIO DROP WATCH START ===")
    cleanup_temp_dir()
    
    try:
        watch_dir = os.path.expanduser("~/remi-intelligence/watch/media")
        if not os.path.exists(watch_dir):
            logger.debug(f"Watch directory doesn't exist: {watch_dir}")
            return {"processed": 0, "errors": 0}
        
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        processed = 0
        errors = 0
        
        # Check for audio files (mp3, m4a, wav, ogg, aac)
        audio_extensions = {'.mp3', '.m4a', '.wav', '.ogg', '.aac'}
        
        for filename in os.listdir(watch_dir):
            if not any(filename.lower().endswith(ext) for ext in audio_extensions):
                continue
            
            filepath = os.path.join(watch_dir, filename)
            if not os.path.isfile(filepath):
                continue
            
            try:
                logger.info(f"Processing audio drop: {filename}")
                
                # Process audio
                drop_result = process_audio_drop(filepath)
                if not drop_result['success']:
                    logger.warning(f"Audio drop failed: {drop_result['error']}")
                    errors += 1
                    continue
                
                # Insert media job
                chash = content_hash(drop_result['transcript'])
                cur.execute("""
                    INSERT INTO media_jobs
                    (media_type, media_url, title, transcript_text, status)
                    VALUES (?, ?, ?, ?, 'ready_for_extraction')
                """, (
                    'audio_drop',
                    filepath,
                    drop_result['title'],
                    drop_result['transcript']
                ))
                conn.commit()
                processed += 1
                
                # Delete audio file after processing
                try:
                    os.remove(filepath)
                    logger.debug(f"Deleted audio file: {filename}")
                except Exception as e:
                    logger.warning(f"Failed to delete {filename}: {e}")
            
            except Exception as e:
                logger.error(f"Error processing {filename}: {e}")
                errors += 1
        
        conn.close()
        logger.info(f"Audio drop watch complete: {processed} processed, {errors} errors")
        return {"processed": processed, "errors": errors}
    
    except Exception as e:
        logger.error(f"Audio drop watch failed: {e}")
        return {"processed": 0, "errors": 1}


def job_media_extraction_worker():
    """Process pending media_jobs — transcribe and write vault notes."""
    logger.info("=== MEDIA EXTRACTION WORKER START ===")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    # Get up to 3 pending jobs per run to avoid blocking the scheduler
    cur.execute("""
        SELECT id, media_type, media_url, title 
        FROM media_jobs 
        WHERE status = 'pending' 
        ORDER BY id ASC 
        LIMIT 3
    """)
    jobs = cur.fetchall()
    conn.close()
    
    processed = 0
    errors = 0
    
    for job_id, media_type, media_url, title in jobs:
        try:
            logger.info(f"Processing media job {job_id}: {title}")
            
            # Mark as processing
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("UPDATE media_jobs SET status='processing' WHERE id=?", (job_id,))
            conn.commit()
            conn.close()
            
            # Fetch transcript based on media_type
            if media_type == 'youtube':
                transcript_result = fetch_youtube_transcript(media_url)
                if not transcript_result or transcript_result.get('error'):
                    raise Exception(f"Transcript fetch failed: {transcript_result.get('error') if transcript_result else 'None returned'}")
                transcript = transcript_result['transcript']
                transcript_method = transcript_result.get('method', 'unknown')
            else:
                raise Exception(f"Unsupported media_type for worker: {media_type}")
            
            # Store transcript and process
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("UPDATE media_jobs SET transcript_text=?, status='ready_for_extraction' WHERE id=?",
                       (transcript, job_id))
            conn.commit()
            conn.close()
            
            # Run full pipeline
            result = process_media_job(job_id)
            
            if result['success']:
                conn = sqlite3.connect(DB_PATH)
                cur = conn.cursor()
                cur.execute("UPDATE media_jobs SET status='completed' WHERE id=?", (job_id,))
                conn.commit()
                conn.close()
                processed += 1
                logger.info(f"✅ Media job {job_id} complete: {result['vault_path']}")
            else:
                raise Exception(result.get('error', 'Unknown error'))
        
        except Exception as e:
            logger.error(f"Media job {job_id} failed: {e}")
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("UPDATE media_jobs SET status='failed', error_msg=? WHERE id=?",
                       (str(e), job_id))
            conn.commit()
            conn.close()
            errors += 1
    
    # Second loop: Process jobs that already have transcripts (ready_for_extraction)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, media_type, media_url, title 
        FROM media_jobs 
        WHERE status = 'ready_for_extraction' 
        ORDER BY id ASC 
        LIMIT 3
    """)
    ready_jobs = cur.fetchall()
    conn.close()
    
    for job_id, media_type, media_url, title in ready_jobs:
        try:
            logger.info(f"Processing ready_for_extraction job {job_id}: {title}")
            
            # Mark as processing
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("UPDATE media_jobs SET status='processing' WHERE id=?", (job_id,))
            conn.commit()
            conn.close()
            
            # Run extraction pipeline directly (transcript already exists)
            result = process_media_job(job_id)
            
            if result['success']:
                conn = sqlite3.connect(DB_PATH)
                cur = conn.cursor()
                cur.execute("UPDATE media_jobs SET status='completed' WHERE id=?", (job_id,))
                conn.commit()
                conn.close()
                processed += 1
                logger.info(f"✅ Ready job {job_id} complete: {result['vault_path']}")
            else:
                raise Exception(result.get('error', 'Unknown error'))
        
        except Exception as e:
            logger.error(f"Ready job {job_id} failed: {e}")
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("UPDATE media_jobs SET status='failed', error_msg=? WHERE id=?",
                       (str(e), job_id))
            conn.commit()
            conn.close()
            errors += 1
    
    logger.info(f"Media extraction worker complete: {processed} processed, {errors} errors")
    return {"processed": processed, "errors": errors}


# ============================================================================
# CLI INTERFACE
# ============================================================================

def extract_audio_from_feed(feed_url: str) -> Optional[Tuple[str, str]]:
    """
    Extract the most recent episode's audio URL from an RSS/Atom feed.
    
    Returns:
        Tuple of (audio_url, episode_title) or (None, None) on error
    """
    try:
        logger.info(f"Parsing feed: {feed_url}")
        parsed = feedparser.parse(feed_url)
        entries = parsed.entries or []
        
        if not entries:
            logger.error(f"No entries found in feed: {feed_url}")
            return None, None
        
        # Get most recent entry
        entry = entries[0]
        
        # Look for audio enclosure
        audio_url = None
        for enclosure in entry.get('enclosures', []):
            if enclosure.get('type', '').startswith('audio/'):
                audio_url = enclosure.get('href')
                break
        
        if not audio_url:
            logger.error(f"No audio enclosure found in first entry of feed: {feed_url}")
            return None, None
        
        episode_title = entry.get('title', 'Unknown')
        logger.info(f"✅ Extracted audio URL from feed: {episode_title}")
        return audio_url, episode_title
    
    except Exception as e:
        logger.error(f"Error parsing feed {feed_url}: {e}")
        return None, None


def process_media_adhoc(url: str) -> Dict:
    """
    Process media from URL ad-hoc (immediate transcription + extraction).
    
    Intelligently detects media type and routes accordingly:
    - Direct audio files (.mp3, .m4a, .wav, etc.) → transcribe_audio_url()
    - YouTube URLs → fetch_youtube_transcript()
    - RSS/Atom feeds → extract audio enclosure → transcribe_audio_url()
    - Local files → process_audio_drop()
    
    Used by Hermes skill and shell script.
    """
    result = {
        "success": False,
        "title": None,
        "transcript_method": None,
        "document_id": None,
        "vault_path": None,
        "error": None
    }
    
    try:
        # Route based on URL type
        
        # 1. Local file path
        if url.startswith('/') and os.path.exists(url):
            logger.info(f"Processing local audio file: {url}")
            drop_result = process_audio_drop(url)
            if not drop_result['success']:
                result["error"] = drop_result['error']
                return result
            
            title = drop_result['title']
            media_type = 'audio_drop'
            transcript = drop_result['transcript']
            transcript_method = 'whisper'
        
        # 2. YouTube URLs
        elif 'youtube.com' in url or 'youtu.be' in url:
            logger.info(f"Processing YouTube URL: {url}")
            transcript_result = fetch_youtube_transcript(url)
            if transcript_result['error']:
                result["error"] = transcript_result['error']
                return result
            
            title = f"YouTube — {transcript_result.get('video_id', 'unknown')}"
            media_type = 'youtube'
            transcript = transcript_result['transcript']
            transcript_method = transcript_result['method']
        
        # 3. Direct audio file URLs (by extension)
        elif any(url.lower().endswith(ext) for ext in ['.mp3', '.m4a', '.wav', '.ogg', '.aac']):
            logger.info(f"Processing direct audio file URL: {url}")
            transcript = transcribe_audio_url(url, "adhoc_audio")
            if not transcript:
                result["error"] = "Failed to transcribe audio URL"
                return result
            
            title = "Audio File"
            media_type = 'podcast'
            transcript_method = 'whisper'
        
        # 4. RSS/Atom feed URLs
        elif (url.lower().endswith(('.xml', '/feed', '/podcast')) or
              'substack.com/feed' in url or
              'feedburner' in url or
              'megaphone.fm' in url or
              'transistor.fm' in url or
              'anchor.fm' in url or
              'podbean.com' in url or
              'buzzsprout.com' in url or
              'rss' in url.lower()):
            logger.info(f"Processing podcast feed URL: {url}")
            audio_url, episode_title = extract_audio_from_feed(url)
            if not audio_url:
                result["error"] = "Failed to extract audio URL from feed"
                return result
            
            logger.info(f"Transcribing extracted episode: {episode_title}")
            transcript = transcribe_audio_url(audio_url, safe_filename(episode_title))
            if not transcript:
                result["error"] = "Failed to transcribe extracted audio"
                return result
            
            title = episode_title
            media_type = 'podcast'
            transcript_method = 'whisper'
        
        # 5. Default: try YouTube first, fall back to audio
        else:
            logger.info(f"Attempting to determine media type for: {url}")
            
            # Try YouTube first
            transcript_result = fetch_youtube_transcript(url)
            if not transcript_result['error']:
                logger.info("✅ Successfully processed as YouTube")
                title = f"YouTube — {transcript_result.get('video_id', 'unknown')}"
                media_type = 'youtube'
                transcript = transcript_result['transcript']
                transcript_method = transcript_result['method']
            else:
                # Fall back to audio URL
                logger.info("YouTube failed, trying as audio URL")
                transcript = transcribe_audio_url(url, "adhoc_audio")
                if not transcript:
                    result["error"] = "Failed to process as YouTube or audio URL"
                    return result
                
                title = "Audio File"
                media_type = 'podcast'
                transcript_method = 'whisper'
        
        result["title"] = title
        result["transcript_method"] = transcript_method
        
        # Insert media job and process
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        
        chash = content_hash(transcript)
        cur.execute("""
            INSERT INTO media_jobs
            (media_type, media_url, title, transcript_text, status)
            VALUES (?, ?, ?, ?, 'ready_for_extraction')
        """, (
            media_type,
            url,
            title,
            transcript
        ))
        conn.commit()
        job_id = cur.lastrowid
        conn.close()
        
        # Process the job
        job_result = process_media_job(job_id)
        result["success"] = job_result['success']
        result["document_id"] = job_result['document_id']
        result["vault_path"] = job_result['vault_path']
        if job_result['error']:
            result["error"] = job_result['error']
        
        return result
    
    except Exception as e:
        result["error"] = f"Unexpected error: {e}"
        logger.error(result["error"])
        return result


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Remi Media Intelligence Ingestor"
    )
    parser.add_argument("--url", type=str, help="Media URL (YouTube, podcast, audio)")
    parser.add_argument("--mode", type=str, choices=["adhoc", "scheduled"],
                        default="adhoc", help="Processing mode")
    parser.add_argument("--file", type=str, help="Local audio file path")
    
    args = parser.parse_args()
    
    if args.mode == "adhoc":
        if not args.url:
            print("Error: --url required for adhoc mode")
            return 1
        
        result = process_media_adhoc(args.url)
        print(json.dumps(result, indent=2))
        return 0 if result['success'] else 1
    
    elif args.mode == "scheduled":
        print("Running scheduled jobs...")
        youtube_result = job_youtube_check()
        podcast_result = job_podcast_check()
        audio_result = job_audio_drop_watch()
        
        print(json.dumps({
            "youtube": youtube_result,
            "podcasts": podcast_result,
            "audio_drop": audio_result
        }, indent=2))
        return 0
    
    else:
        print(f"Unknown mode: {args.mode}")
        return 1


if __name__ == "__main__":
    exit(main())
