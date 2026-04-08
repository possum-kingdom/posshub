#!/bin/bash
# ============================================================
# PossHub — Mac Mini Server Setup
# ============================================================
# Run this on your Mac mini to:
#   1. Start PossHub on boot (port 3000)
#   2. Auto-sync repos every 5 minutes
#   3. Make it available on your local network
#
# Usage:
#   chmod +x setup-mac-mini.sh
#   ./setup-mac-mini.sh
# ============================================================

set -e

POSSHUB_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_DIR="$HOME/Library/LaunchAgents"
POSSHUB_PLIST="$PLIST_DIR/com.posshub.server.plist"
SYNC_PLIST="$PLIST_DIR/com.posshub.sync.plist"
LOG_DIR="$POSSHUB_DIR/logs"

echo ""
echo "  ____                 __  __      __"
echo " / __ \\____  _________/ / / /_  __/ /_"
echo "/ /_/ / __ \\/ ___/ ___/ /_/ / / / / __ \\"
echo "/ ____/ /_/ (__  |__  ) __  / /_/ / /_/ /"
echo "/_/    \\____/____/____/_/ /_/\\__,_/_.___/"
echo ""
echo "  Mac Mini Server Setup"
echo "  No Microsoft nastiness. 100% opossum powered."
echo ""

# Create log directory
mkdir -p "$LOG_DIR"

# --- Step 1: Make PossHub listen on all interfaces ---
echo "[1/4] Configuring PossHub for network access..."

# Check if already configured for 0.0.0.0
if grep -q 'HOST = "127.0.0.1"' "$POSSHUB_DIR/posshub.py"; then
    sed -i '' 's/HOST = "127.0.0.1"/HOST = "0.0.0.0"/' "$POSSHUB_DIR/posshub.py"
    echo "  -> Bound to 0.0.0.0 (all interfaces)"
else
    echo "  -> Already configured"
fi

# --- Step 2: Create LaunchAgent for PossHub server ---
echo "[2/4] Installing PossHub server LaunchAgent..."

mkdir -p "$PLIST_DIR"
cat > "$POSSHUB_PLIST" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.posshub.server</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>${POSSHUB_DIR}/posshub.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${POSSHUB_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/posshub.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/posshub-error.log</string>
</dict>
</plist>
PLIST

echo "  -> Created $POSSHUB_PLIST"

# --- Step 3: Create LaunchAgent for sync ---
echo "[3/4] Installing sync LaunchAgent (every 5 minutes)..."

cat > "$SYNC_PLIST" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.posshub.sync</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>${POSSHUB_DIR}/sync.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${POSSHUB_DIR}</string>
    <key>StartInterval</key>
    <integer>300</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/sync.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/sync-error.log</string>
</dict>
</plist>
PLIST

echo "  -> Created $SYNC_PLIST"

# --- Step 4: Load the agents ---
echo "[4/4] Loading LaunchAgents..."

launchctl unload "$POSSHUB_PLIST" 2>/dev/null || true
launchctl unload "$SYNC_PLIST" 2>/dev/null || true
launchctl load "$POSSHUB_PLIST"
launchctl load "$SYNC_PLIST"

echo "  -> Agents loaded"

# --- Get network info ---
IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "unknown")
HOSTNAME=$(hostname)

echo ""
echo "============================================================"
echo "  PossHub is live!"
echo ""
echo "  Local:    http://localhost:3000"
echo "  Network:  http://${IP}:3000"
echo "  Hostname: http://${HOSTNAME}:3000"
echo ""
echo "  Repos sync every 5 minutes automatically."
echo "  Logs:     ${LOG_DIR}/"
echo ""
echo "  To stop:"
echo "    launchctl unload ~/Library/LaunchAgents/com.posshub.server.plist"
echo "    launchctl unload ~/Library/LaunchAgents/com.posshub.sync.plist"
echo ""
echo "  No Microsoft nastiness. Just possums."
echo "============================================================"
echo ""
