# Remi — System Architecture

## Infrastructure
- Proxmox bare-metal host (i7-8700K, 62GB RAM)
- Deb-Docs-Media VM (192.168.1.100, Debian)
- 6TB NAS mirrored storage

## Core Components
- **Hermes Agent** — systemd user service, Haiku default, Sonnet for inference
- **Narrative Intelligence Pipeline** — APScheduler, 6h RSS, 4h extraction
- **Clinical Vault** — 400MB Obsidian vault, CouchDB LiveSync, 4-device sync
- **Signal Listener** — Telethon userbot, real-time Telegram group monitoring

## Data Flow
Telegram → Hermes → Claude API → Obsidian vault
RSS feeds → SQLite → Haiku extraction → GLI stamp → Obsidian investing vault
Signal group → Telethon → classification → 7am brief → MG + Pablo

## On-Chain Identity
- ERC-8004 on Base Mainnet
- Wallet: 0x316252829cd5fDFd2aB4e17E669C8CE8a42794F6
- Registration tx: 0x0d6ab70d99096b1dfecad8a64407da9dbe8142eadeb0cf9b55aae33f5d0374b1
