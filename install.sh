#!/usr/bin/env bash
# Installs the turnstile for the CURRENT USER. No root, no system files.
#
# Everything lands under ~/.local and ~/.config, and every unit is a systemd
# *user* unit. Uninstalling is `rm` plus `systemctl --user disable`.
set -euo pipefail

BIN="$HOME/.local/bin"
CFG="$HOME/.config/turnstile"
UNITS="$HOME/.config/systemd/user"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$BIN" "$CFG" "$UNITS" "$HOME/.local/state/turnstile"

echo "→ binaries into $BIN"
install -m 755 "$HERE"/bin/* "$BIN/"

echo "→ user units into $UNITS"
install -m 644 "$HERE"/systemd/* "$UNITS/"

for f in containers.map harness.map; do
  if [ -e "$CFG/$f" ]; then
    echo "  $CFG/$f already exists, left alone"
  else
    install -m 644 "$HERE/examples/$f" "$CFG/$f"
    echo "  $CFG/$f created from the example — EDIT IT"
  fi
done

systemctl --user daemon-reload

cat <<END

Installed. Nothing is running yet, on purpose.

  1. Describe your containers:
       \$EDITOR $CFG/containers.map

  2. Start the allocator:
       systemctl --user enable --now turnstiled
       turnstile status

  3. Keep a daily record — this is what answers "was it worth it" in a month:
       systemctl --user enable --now turnstile-record.timer

  4. Only once you trust it, let it follow your activity.
     It starts in propose-only mode and writes what it WOULD have done to
     ~/.local/state/turnstile/watch.jsonl. Read that for a few days first.
       systemctl --user enable --now turnstile-watch

If the API is reachable from your network, set a token:
  systemctl --user edit turnstiled     # Environment=TURNSTILE_TOKEN=...

END
