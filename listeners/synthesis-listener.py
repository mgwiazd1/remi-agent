"""
Synthesis Hackathon Group Listener
Monitors the Synthesis group for judge questions
Uses existing Telethon session from Hermes gateway
"""
import asyncio
import logging
import os
import json
from datetime import datetime
from telethon import TelegramClient, events
from telethon.tl.types import PeerChannel

# Load from Hermes .env
env_path = os.path.expanduser("~/.hermes/.env")
env = {}
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                env[key.strip()] = val.strip().strip('"\'')

SYNTHESIS_GROUP_ID = -1003792889924
M_CHAT_ID = int(env.get("TELEGRAM_CHAT_ID", "6625574871"))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.expanduser("~/remi-intelligence/logs/synthesis_listener.log")),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class SynthesisListener:
    """Listen for Synthesis group messages about Remi"""
    
    def __init__(self, client: TelegramClient):
        self.client = client
        self.keywords = ["remi", "mgwiazd1", "erc-8004", "judge", "question"]
        self.state_file = os.path.expanduser("~/remi-intelligence/logs/synthesis_state.json")
        self.load_state()
    
    def load_state(self):
        """Load last checked message ID"""
        self.state = {}
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file) as f:
                    self.state = json.load(f)
            except:
                pass
    
    def save_state(self):
        """Save state"""
        with open(self.state_file, 'w') as f:
            json.dump(self.state, f)
    
    async def on_message(self, event):
        """Handle new group messages"""
        try:
            msg = event.message
            if not msg.text:
                return
            
            msg_lower = msg.text.lower()
            
            # Check if relevant
            is_relevant = any(k in msg_lower for k in self.keywords)
            
            if is_relevant:
                sender = await event.get_sender()
                sender_name = getattr(sender, 'first_name', '') or getattr(sender, 'username', 'User')
                
                alert = f"""🚨 SYNTHESIS JUDGE QUESTION

From: {sender_name}
Time: {msg.date.strftime('%Y-%m-%d %H:%M:%S UTC')}

{msg.text[:400]}

⚠️ CHECK SYNTHESIS: Judge question about your submission
Reply in the group ASAP if needed.
"""
                logger.warning(f"ALERT: {alert}")
                
                # Send DM to M
                try:
                    await self.client.send_message(M_CHAT_ID, alert)
                    logger.info("✓ Alert sent to M")
                except Exception as e:
                    logger.error(f"Failed to send alert: {e}")
            
            # Log all messages for context
            logger.debug(f"[{sender_name}] {msg.text[:100]}")
            
        except Exception as e:
            logger.error(f"Error in on_message: {e}")
    
    async def start(self):
        """Start listening"""
        logger.info("Synthesis listener started")
        # Register handler
        # This would be done by adding to client handlers


async def main():
    """Test listener"""
    logger.info("Synthesis Listener initialized (use with existing Hermes gateway)")


if __name__ == "__main__":
    asyncio.run(main())
