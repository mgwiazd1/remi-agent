# Signal Listener

Telethon userbot monitoring the EngineeringRobo VIP Signals Telegram supergroup in real-time.

## What it does
- Watches all topic channels (BTC, Altcoins, DXY, Fear & Greed, etc.)
- Buffers all messages to `signal-buffer.json`
- Matches urgent keywords → immediate Telegram ping to MG
- 7am cron → Claude extracts signals → Remi sends morning brief to MG + Pablo

## Architecture
```
EngineeringRobo VIP (Telegram supergroup)
  → Telethon userbot (proxmox user account)
    → signal-buffer.json
    → urgent keywords → immediate alert
    → 7am → signal-digest.sh → Claude extraction → Telegram brief
```

## Auth
Cookie-based Telethon session at `/home/proxmox/.tg-signals-listener`
One-time auth — session persists across reboots via systemd user service.

## Service management
```bash
systemctl --user status tg-listener
systemctl --user restart tg-listener
journalctl --user -u tg-listener -f
```
