#!/usr/bin/env bash
# Run Scare Force at boot (systemd service), from the Mac clone of scare-force:
#
#     tools/pi_boot.sh <pi-ip> enable    # install + enable + start now; on every boot after that
#     tools/pi_boot.sh <pi-ip> disable   # stop it and don't start at boot (files stay)
#     tools/pi_boot.sh <pi-ip> status    # is it running, last 25 log lines
#     tools/pi_boot.sh <pi-ip> logs      # follow the log (Ctrl+C to leave)
#
# Needs the Pi user to have sudo without a password (Raspberry Pi OS's first user does); if not,
# it prints the commands to run on the Pi. The Pi is the detection team's: enable with their OK.
set -euo pipefail
IP="${1:?usage: tools/pi_boot.sh <pi-ip> enable|disable|status|logs [user]}"; CMD="${2:?enable|disable|status|logs}"
USER_AT="${3:-scareforce}@$IP"
HERE="$(cd "$(dirname "$0")" && pwd)"
SSH=(ssh -o ConnectTimeout=8 -o BatchMode=yes "$USER_AT")
"${SSH[@]}" true || { echo "Can't SSH to $USER_AT. Same network? On the Pi: hostname -I"; exit 1; }

case "$CMD" in
enable)
  "${SSH[@]}" "bash -s" < <(printf 'UNIT=%q\n' "$(cat "$HERE/scare-force.service")"; cat <<'PI'
set -euo pipefail
cd ~/scare-force
[ -x .venv/bin/python ] || { echo "!! ~/scare-force/.venv/bin/python not found"; exit 1; }
if pgrep -f "scare\.controller" >/dev/null && ! systemctl is-active --quiet scare-force; then
  echo "!! scare.controller is running by hand; stop it first (Ctrl+C / q). Nothing changed."; exit 1
fi
GROUPS_=$(for g in gpio video audio i2c render; do getent group $g >/dev/null && printf '%s ' $g; done)
printf '%s\n' "$UNIT" | sed -e "s|@USER@|$USER|g" -e "s|@HOME@|$HOME|g" -e "s|@GROUPS@|${GROUPS_% }|g" > /tmp/scare-force.service
if ! sudo -n true 2>/dev/null; then
  echo "!! sudo needs a password here. On the Pi run:"
  echo "   sudo cp /tmp/scare-force.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now scare-force"
  exit 1
fi
sudo cp /tmp/scare-force.service /etc/systemd/system/scare-force.service
sudo systemctl daemon-reload
sudo systemctl enable --now scare-force
echo "   enabled; waiting 25 s for it to come up..."; sleep 25
systemctl --no-pager --lines=0 status scare-force | sed -n '1,4p'
journalctl -u scare-force -b --no-pager -n 25 -o cat
PI
) ;;
disable) "${SSH[@]}" 'sudo -n systemctl disable --now scare-force && echo "   stopped; won'"'"'t start at boot" || echo "!! on the Pi: sudo systemctl disable --now scare-force"' ;;
status)  "${SSH[@]}" 'systemctl is-enabled scare-force 2>/dev/null | sed "s/^/   at boot: /"; systemctl --no-pager --lines=0 status scare-force 2>&1 | sed -n "1,4p"; journalctl -u scare-force -b --no-pager -n 25 -o cat 2>/dev/null' ;;
logs)    ssh -t -o ConnectTimeout=8 "$USER_AT" 'journalctl -u scare-force -f -o cat' ;;
*) echo "enable | disable | status | logs"; exit 1 ;;
esac
