#!/usr/bin/env python3
import json, os, re, subprocess, sys
from pathlib import Path

ENV_FILE = '/home/proxmox/remi-intelligence/.env'
TWITTER_CLI = '/home/proxmox/.local/bin/twitter'

def load_env():
    env = os.environ.copy()
    if Path(ENV_FILE).exists():
        for line in Path(ENV_FILE).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip()
    return env

def extract_tweet_id(input_str):
    match = re.search(r'/status/(\d+)', input_str)
    if match:
        return match.group(1)
    if input_str.strip().isdigit():
        return input_str.strip()
    return None

def fetch_tweet(url, env):
    try:
        result = subprocess.run(
            [TWITTER_CLI, 'tweet', url, '--json'],
            capture_output=True, text=True, env=env, timeout=30
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip() or "twitter-cli failed"}
        data = json.loads(result.stdout)
        # Extract just the main tweet (first item) and normalize
        tweet = data['data'][0]
        # Pull the main tweet fields
        main = {
            "id": tweet.get("id"),
            "author": tweet.get("author", {}).get("screenName", "unknown"),
            "author_name": tweet.get("author", {}).get("name", "unknown"),
            "text": tweet.get("text", ""),
            "created_at": tweet.get("createdAtISO"),
            "likes": tweet.get("metrics", {}).get("likes", 0),
            "retweets": tweet.get("metrics", {}).get("retweets", 0),
            "views": tweet.get("metrics", {}).get("views", 0),
            "url": url
        }
        return main
    except subprocess.TimeoutExpired:
        return {"error": "Timed out"}
    except json.JSONDecodeError as e:
        return {"error": f"JSON parse error: {e}"}
    except Exception as e:
        return {"error": str(e)}

def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: fetch_tweet.py <tweet_url_or_id>"}))
        sys.exit(1)
    raw = sys.argv[1]
    tweet_id = extract_tweet_id(raw)
    if not tweet_id:
        print(json.dumps({"error": f"Could not parse: {raw}"}))
        sys.exit(1)
    url = f"https://x.com/i/status/{tweet_id}"
    env = load_env()
    result = fetch_tweet(url, env)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if "error" in result:
        sys.exit(1)

if __name__ == "__main__":
    main()
