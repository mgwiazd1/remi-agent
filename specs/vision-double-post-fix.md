# SPEC: Fix Vision Double-Post
**Date:** 2026-04-22  
**Priority:** High  
**Files:** `signals_group_listener.py`, `~/.hermes/SOUL.md`  
**Goal:** Consolidate vision analysis into one Remi post. Currently Gemma E4B posts the analysis AND Hermes responds conversationally = two posts per image.

---

## ROOT CAUSE

Two independent paths both fire when a photo hits the group:

1. **Pipeline path** — `signals_group_listener.py` `handle_photo()` calls `vision_relay.describe_image()` → sends E4B analysis as a bot message
2. **Gateway path** — Hermes gateway sees the same event, the description gets injected into `event.text`, and Remi responds conversationally

The SOUL.md rule says "don't call vision_analyze" but doesn't silence Hermes from *responding* after the pipeline already posted.

---

## THE FIX — 2 STEPS

### STEP 1 — Read current state (no writes yet)

```bash
# Read the current handle_photo function
grep -n "handle_photo\|describe_image\|vision_relay\|reply_to\|event.reply\|client.send" \
  ~/remi-intelligence/src/signals_group_listener.py | head -40

# Read the vision pipeline section of SOUL.md
grep -n -A 20 "Vision Pipeline\|vision_analyze\|VISION" ~/.hermes/SOUL.md
```

Paste both outputs before proceeding. Do not write anything yet.

---

### STEP 2 — Patch `signals_group_listener.py`

**What to change in `handle_photo()`:**

1. After `vision_relay.describe_image()` returns the description, prefix it with `[VISION_ANALYSIS]: ` before injecting it into `event.text` (this is the tag SOUL.md will key off)
2. When sending the bot message to the group, send it as a **reply to the original photo message** using `reply_to=event.message.id`

**Pattern to find** (locate the exact lines via the grep output from Step 1, then apply):

```python
# BEFORE — somewhere in handle_photo(), the description is sent and injected:
# description = await asyncio.to_thread(vision_relay.describe_image, ...)
# event.text = description          ← injection (may be slightly different)
# await client.send_message(...)    ← the bot post

# AFTER:
# description = await asyncio.to_thread(vision_relay.describe_image, ...)
# event.text = f"[VISION_ANALYSIS]: {description}"   ← tagged injection
# await client.send_message(
#     GROUP_CHAT_ID,
#     description,
#     reply_to=event.message.id     ← thread it under the original photo
# )
```

**Use str_replace with the exact lines from the grep output — do not guess the surrounding context.**

After the edit, verify with:
```bash
python3 -c "import ast; ast.parse(open('signals_group_listener.py').read()); print('OK')"
```
Run from `~/remi-intelligence/src/`.

---

### STEP 3 — Patch `~/.hermes/SOUL.md`

Find the existing **Vision Pipeline** section and append this rule directly below it:

```markdown
### Vision Double-Post Suppression
When the incoming message text begins with `[VISION_ANALYSIS]:`, the pipeline has 
already analyzed and posted the image. **Do not respond.** Treat it as a read-only 
internal signal — process it for context if useful, but post nothing to the group.
This is a hard rule. The pipeline post IS the Remi response for image events.
```

Use str_replace targeting the end of the existing Vision Pipeline section to append cleanly.

After the edit:
```bash
grep -A 10 "Vision Double-Post" ~/.hermes/SOUL.md
```
Confirm the new block appears.

---

### STEP 4 — Restart services

```bash
# Restart the signals listener to pick up the Python change
systemctl --user restart signals-listener.service
systemctl --user status signals-listener.service

# Restart the Hermes investing gateway to reload SOUL.md
systemctl --user restart hermes-gateway.service
systemctl --user status hermes-gateway.service
```

Both must show `active (running)` before confirming done.

---

### STEP 5 — Verify

Drop a test chart image into the investing group. Expected behavior:
- ✅ One message appears — the E4B analysis, threaded as a reply under the photo
- ✅ Hermes does NOT post a second conversational response
- ✅ The pipeline message is visually attached to the original image (reply thread)

If Hermes still posts a second message, check `~/.hermes/logs/gateway.log` for whether `[VISION_ANALYSIS]:` is appearing in the received event text. If it's not there, the injection in `signals_group_listener.py` didn't land — re-read the file and verify.

---

## UPSTREAM PATCH NOTE

After this is working, document the SOUL.md change in `~/.hermes/UPSTREAM_PATCHES.md`:

```markdown
## Vision Double-Post Suppression (2026-04-22)
Added "Vision Double-Post Suppression" block to SOUL.md Vision Pipeline section.
Rule: messages prefixed with [VISION_ANALYSIS]: must not trigger a Hermes response.
Re-apply after any Hermes update that resets SOUL.md.
```
