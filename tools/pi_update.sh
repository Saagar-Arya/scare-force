#!/usr/bin/env bash
# One-step Pi update, run from the Mac clone of scare-force:
#
#     tools/pi_update.sh <pi-ip> [user]          # user defaults to scareforce
#
# Gets the Pi onto GitHub main without losing anything tested on it:
#   1. fetches origin/main on the Mac and sends it to the Pi over SSH (the Pi needs no GitHub login)
#   2. on the Pi: stops if the controller is running; snapshots the current state, including
#      uncommitted edits to tracked files and new files under scare/, on pi/pre-demo-<stamp>
#   3. on the Pi: makes pi/demo-<stamp> from main and merges the snapshot into it. Where both
#      changed the same lines, main wins (-X ours); Pi-only edits (e.g. motion tuning) are kept.
#      If the merge still fails, it aborts and leaves the Pi on the snapshot (the tested state).
#   4. on the Pi: checks: code compiles, config loads, the sound library lists its clips, the
#      model files exist; prints the settings that matter for the demo
#   5. fetches both Pi branches back to the Mac as refs pi/* so the Pi-only work can go to GitHub
#
# It never deletes, resets or pushes to GitHub, never reboots, and does not start the controller.
# Undo on the Pi: git -C ~/scare-force switch pi/pre-demo-<stamp>
set -euo pipefail
IP="${1:?usage: tools/pi_update.sh <pi-ip> [user]}"; USER_AT="${2:-scareforce}@$IP"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"; cd "$REPO_DIR"
STAMP="$(date +%Y%m%d-%H%M)"
SSH=(ssh -o ConnectTimeout=8 -o BatchMode=yes "$USER_AT")

echo "== 1. Mac: fetch GitHub main, send it to the Pi"
git fetch -q origin main
echo "   origin/main = $(git log -1 --format='%h %s' origin/main)"
"${SSH[@]}" true || { echo "Can't SSH to $USER_AT. Same network? On the Pi: hostname -I"; exit 1; }
git push -q "ssh://$USER_AT/~/scare-force" "origin/main:refs/remotes/mac/main" --force

echo "== 2-4. Pi: snapshot, merge, check"
set +e
"${SSH[@]}" "STAMP=$STAMP bash -s" <<'PI'
set -euo pipefail
cd "${REPO:-$HOME/scare-force}"
if pgrep -af "scare\.controller" >/dev/null; then
  echo "!! scare.controller is running; stop it (Ctrl+C / q) and run this again. Nothing changed."; exit 1
fi
FROM="$(git rev-parse --abbrev-ref HEAD)"
echo "   Pi was on $FROM at $(git log -1 --format='%h %s')"
git switch -q -c "pi/pre-demo-$STAMP"
git add -u
git ls-files --others --exclude-standard -- scare | xargs -r git add --
if ! git diff --cached --quiet; then
  git -c user.name="Scare Force Pi" -c user.email="pi@scare-force.local" commit -q -m "pi: snapshot of the tested state before the demo update ($STAMP)"
  echo "   snapshot: committed the Pi's uncommitted edits:"; git show --stat --format= HEAD | sed 's/^/     /'
else
  echo "   snapshot: no uncommitted edits to tracked files"
fi
OTHER="$(git ls-files --others --exclude-standard --directory | grep -v "^scare/" | head -20 || true)"
[ -n "$OTHER" ] && { echo "   left as they are (untracked, outside scare/):"; echo "$OTHER" | sed 's/^/     /'; }
BASE="$(git merge-base HEAD mac/main)"
echo "   on the Pi but not on main (vs merge base):"; git diff --stat "$BASE" HEAD | sed 's/^/     /'
# untracked files that main now tracks (e.g. model files copied by hand) would block the switch:
# move them aside, never delete
CLASH="$(comm -12 <(git ls-files --others --exclude-standard | sort) <(git ls-tree -r --name-only mac/main | sort))"
if [ -n "$CLASH" ]; then
  ASIDE="$HOME/scare-force-untracked-$STAMP"; echo "   moved aside to $ASIDE (main tracks these):"
  echo "$CLASH" | while read -r f; do mkdir -p "$ASIDE/$(dirname "$f")"; mv "$f" "$ASIDE/$f"; echo "     $f"; done
fi
git switch -q -c "pi/demo-$STAMP" mac/main
if git -c user.name="Scare Force Pi" -c user.email="pi@scare-force.local" merge -q --no-edit -X ours "pi/pre-demo-$STAMP" -m "pi: main + the Pi's tested work (main wins conflicts) ($STAMP)"; then
  echo "   merged: Pi is on pi/demo-$STAMP = main + Pi-only edits"
  echo "   differs from main in:"; git diff --stat mac/main HEAD | sed 's/^/     /'
else
  git merge --abort || true
  git switch -q "pi/pre-demo-$STAMP"
  echo "!! merge failed; Pi left on pi/pre-demo-$STAMP (the tested state). Ask Claude to merge by hand."; exit 2
fi
PY=python3; for c in .venv/bin/python venv/bin/python; do [ -x "$c" ] && PY="$c" && break; done
echo "== checks ($PY)"
$PY -m compileall -q scare && echo "   compile ok" || echo "!! compile failed"
$PY - <<'EOF' || echo "!! config check failed"
from scare import load_config, ROOT
c = load_config()
d, ctl, s, sv = c["detector"], c["controller"], c["sound"], c["servos"]
for k in ("model", "labels"):
    p = ROOT / d[k]; print(f"   detector.{k}: {d[k]}", "ok" if p.exists() else "MISSING")
print(f"   trigger labels {ctl['labels']}, min_confidence {ctl['min_confidence']}, confirm_frames {ctl['confirm_frames']}")
print(f"   scare {ctl['scare_seconds']} s +/- {ctl.get('end_window_s')}, cooldown {ctl['cooldown_seconds']} +/- {ctl.get('cooldown_jitter_s', 0)} s")
print(f"   sound level {s['level']}%, mute_idle {s.get('mute_idle')}, skip_types {s.get('skip_types')}")
print("   servos:", {k: v for k, v in sv.items() if k != "channels"})
print("   light:", {k: v for k, v in c.get("light", {}).items() if not isinstance(v, dict)})
EOF
$PY -m scare.sound_library 2>&1 | sed -n '1,3p' | sed 's/^/   /' || true
missing=$($PY - <<'EOF'
from scare.sound_library import Library
from scare import ROOT
print(" ".join(p.name for p in Library.load(ROOT / "scare/sounds/library.yaml").clips_for() if not p.exists()))
EOF
) || missing="(check failed)"
[ -z "$missing" ] && echo "   all generic clips present" || echo "!! missing WAVs: $missing"
echo "   undo: git -C ~/scare-force switch pi/pre-demo-$STAMP"
PI
rc=$?
set -e

echo "== 5. Mac: fetch the Pi's branches (refs pi/*)"
git fetch -q "ssh://$USER_AT/~/scare-force" "+refs/heads/pi/*:refs/remotes/pi/*" && git branch -r | grep " pi/" | sed 's/^/   /'
echo "Done. Next on the Pi: .venv/bin/python -m scare.sound (listen), then the controller."
exit $rc
