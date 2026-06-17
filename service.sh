#!/bin/bash
# Manage the LoadOptimizer Pro web server as a macOS LaunchAgent.
#   ./service.sh install     install + start (runs at login, auto-restarts if it dies)
#   ./service.sh uninstall   stop + remove
#   ./service.sh restart      restart now
#   ./service.sh status       show state/pid
#   ./service.sh logs         tail the log
set -e
LABEL="com.loadoptimizer.webapp"
DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PY="$(command -v python3 || echo /opt/homebrew/bin/python3)"
LOG="$DIR/../renders/loadoptimizer.launchd.log"
UID_="$(id -u)"

write_plist() {
  mkdir -p "$HOME/Library/LaunchAgents" "$(dirname "$LOG")"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$DIR/webapp.py</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>5</integer>
  <key>ProcessType</key><string>Background</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF
}

case "${1:-}" in
  install)
    write_plist
    lsof -nP -iTCP:8765 -sTCP:LISTEN -t 2>/dev/null | xargs kill -9 2>/dev/null || true
    launchctl bootout "gui/$UID_/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$UID_" "$PLIST"
    launchctl enable "gui/$UID_/$LABEL" 2>/dev/null || true
    echo "Installed and running -> http://localhost:8765" ;;
  uninstall)
    launchctl bootout "gui/$UID_/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"; echo "Uninstalled." ;;
  restart) launchctl kickstart -k "gui/$UID_/$LABEL"; echo "restarted" ;;
  status) launchctl print "gui/$UID_/$LABEL" 2>/dev/null | grep -E "state = |pid = " | head -3 || echo "not loaded" ;;
  logs) tail -n 40 "$LOG" 2>/dev/null || echo "no log yet" ;;
  *) echo "Usage: $0 install|uninstall|restart|status|logs" ;;
esac
