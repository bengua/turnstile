#!/usr/bin/env python3
"""Fault injection: provoke, observe, record what gives way.

No theory. Every scenario is REALLY triggered on the machine, and we watch
what the system does. What it does badly becomes a fix.

This benchmark discovers its own targets from /slots, so it runs against any
installation. It needs a working Docker and at least two GPU slots to be
meaningful; it degrades gracefully with fewer.

WARNING: it stops and starts your containers, kills the service, and
corrupts its state file on purpose. Do not run it against something you are
relying on right now.
"""
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request

API = os.environ.get("TURNSTILE_API", "http://127.0.0.1:8130")
UNIT = os.environ.get("TURNSTILE_UNIT", "turnstiled")
STATE_FILE = os.path.expanduser(
    os.environ.get("TURNSTILE_STATE", "~/.local/state/turnstile/state.json"))
CORPUS = os.path.expanduser(
    os.environ.get("TURNSTILE_CORPUS", "~/.local/state/turnstile/pairs.jsonl"))
results = []


def sh(c, t=120):
    return subprocess.run(c, shell=True, capture_output=True, text=True,
                          timeout=t).stdout.strip()


def api(path, body=None, t=120):
    url = API + path
    req = (urllib.request.Request(url) if body is None else
           urllib.request.Request(url, data=json.dumps(body).encode(),
                                  headers={"Content-Type": "application/json"}))
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=t) as r:
            return json.load(r), round(time.time() - t0, 1)
    except urllib.error.HTTPError as e:
        # A NAMED refusal arrives with an error code: 400, 507. The body
        # carries the reason, and that is what we want to judge — not the
        # code alone. Without this, the benchmark called a service broken
        # while it was answering correctly.
        try:
            body = json.loads(e.read().decode())
            body["__http__"] = e.code
            return body, round(time.time() - t0, 1)
        except Exception:
            return {"__error__": f"HTTP {e.code}"}, round(time.time() - t0, 1)
    except Exception as e:
        return {"__error__": str(e)[:80]}, round(time.time() - t0, 1)


def alive():
    return sh(f"systemctl --user is-active {UNIT}") == "active"


def note(name, expected, observed, verdict):
    results.append((name, verdict))
    mark = {"OK": "  OK  ", "BROKE": " BROKE", "CHECK": " CHECK"}[verdict]
    print(f"[{mark}] {name}")
    print(f"         expected: {expected}")
    print(f"         observed: {observed}\n", flush=True)


# ── discover the targets ────────────────────────────────────────────────

def discover():
    d, _ = api("/slots", t=20)
    slots = d.get("slots", {})
    release = sorted(d.get("release_slots", {}))
    gpu = [n for n, s in slots.items() if s["card"] != "cpu"]
    one_card = [n for n in gpu if slots[n]["card"].isdigit()]
    all_cards = [n for n in gpu if slots[n]["card"] == "all"]
    return {
        "slots": slots,
        "free_all": next((r for r in release if r.endswith("all")),
                         release[0] if release else None),
        "a": one_card[0] if one_card else (gpu[0] if gpu else None),
        "b": (all_cards[0] if all_cards
              else (one_card[1] if len(one_card) > 1 else None)),
        "big": all_cards[0] if all_cards else (one_card[0] if one_card else None),
    }


def reset(t):
    if t["free_all"]:
        api("/slot", {"slot": t["free_all"]}, t=90)
        time.sleep(2)


def clear_ceiling():
    """The swap ceiling slides over an hour: without a reset, earlier passes
    of this benchmark saturate it and every later scenario measures the
    ceiling instead of what it aims at."""
    try:
        d = json.load(open(STATE_FILE))
    except Exception:
        d = {}
    d["measurements"] = []
    d.setdefault("counters", {})
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    json.dump(d, open(STATE_FILE, "w"), indent=2)
    sh(f"systemctl --user restart {UNIT}")
    time.sleep(5)


clear_ceiling()
T = discover()
print(f"targets: single-card={T['a']} · split={T['b']} · release={T['free_all']}\n")
if not T["a"]:
    print("no GPU slot in the map — nothing meaningful to break")
    raise SystemExit(0)


# ── 1 · slot that does not exist ────────────────────────────────────────
r, _ = api("/slot", {"slot": "slot-that-does-not-exist"})
note("nonexistent slot",
     "clean refusal, state=unknown, nothing touched", r,
     "OK" if r.get("state") == "unknown" else "BROKE")

# ── 2 · missing field ───────────────────────────────────────────────────
r, _ = api("/slot", {})
note("request with no slot field",
     "400 with the list of slots", r,
     "OK" if "error" in r or "__error__" in r else "BROKE")

# ── 3 · weights gone from under it ──────────────────────────────────────
#
# The slot must NOT already be serving, or the API answers already_serving
# and never attempts the load this scenario is about. That is how an earlier
# pass reported a broken system when only the benchmark's starting state was
# wrong.
reset(T)
sh(f"docker stop {T['a']}", t=90)
time.sleep(2)
slot = T["slots"][T["a"]]
folder = (slot["weights"] if slot["weights"].startswith("/")
          else os.path.expanduser(f"~/models/{slot['weights']}"))
weights = os.path.join(folder, os.path.basename(slot["model"] or ""))
if slot["model"] and os.path.exists(weights):
    hidden = weights + ".hidden"
    os.rename(weights, hidden)
    r, d = api("/slot", {"slot": T["a"]}, t=90)
    os.rename(hidden, weights)
    note("model weights deleted underneath",
         "clean failure in under 35 s, service still alive",
         f"{r.get('state')} in {d} s · service {'alive' if alive() else 'DEAD'}",
         "OK" if r.get("state") == "failed" and alive() and d < 40 else "BROKE")
else:
    note("model weights deleted underneath", "—",
         f"skipped: {weights} not found", "CHECK")

# ── 4 · two concurrent requests ─────────────────────────────────────────
if T["b"]:
    reset(T)
    out = []
    for s in (T["a"], T["b"]):
        threading.Thread(target=lambda s=s: out.append(
            (s, api("/slot", {"slot": s}, t=90)[0]))).start()
        time.sleep(0.2)
    time.sleep(40)
    states = {s: r.get("state") for s, r in out}
    note("two concurrent requests",
         "one served, the other queued — never two loads at once", states,
         "OK" if sorted(states.values()) == ["queued", "served"] else "CHECK")

# ── 5 · container killed after being declared serving ───────────────────
reset(T)
api("/slot", {"slot": T["a"]}, t=90)
time.sleep(3)
sh(f"docker kill {T['a']}")
time.sleep(14)          # the probe runs every 10 s
e, _ = api("/state")
status = (e.get("slots_live", {}).get(T["a"]) or {}).get("status", "absent")
c, _ = api("/counters")
note("container killed after being served",
     "the probe sees it vanish AND the declared slot is cleared",
     f"status={status} · declared_slot={e.get('declared_slot')} · "
     f"divergences={c.get('divergences_seen')}",
     "OK" if status == "absent" and not e.get("declared_slot") else "BROKE")

# ── 6 · corrupted state file ────────────────────────────────────────────
saved = open(STATE_FILE).read() if os.path.exists(STATE_FILE) else "{}"
open(STATE_FILE, "w").write("{this is not json")
sh(f"systemctl --user restart {UNIT}")
time.sleep(6)
s, _ = api("/health")
note("corrupted state file",
     "restarts on an empty state rather than refusing to start",
     f"health={s} · service {'alive' if alive() else 'DEAD'}",
     "OK" if s.get("ok") and alive() else "BROKE")
open(STATE_FILE, "w").write(saved)
sh(f"systemctl --user restart {UNIT}")
time.sleep(5)

# ── 7 · the map is read at startup ──────────────────────────────────────
r, _ = api("/slots")
before = len(r.get("slots", {}))
note("map read at startup", "the slots are loaded", f"{before} slots",
     "OK" if before >= 2 else "BROKE")

# ── 8 · a hand-started container holding the target card ────────────────
reset(T)
# A container that LASTS: an earlier version exited at once and proved
# nothing.
sh("docker rm -f turnstile-intruder >/dev/null 2>&1")
image = sh("docker ps -a --filter name=%s --format '{{.Image}}'" % T["a"]) \
    or "alpine"
sh(f"docker run -d --name turnstile-intruder --entrypoint sleep {image} 120 "
   ">/dev/null 2>&1")
time.sleep(4)
e, _ = api("/state")
manual = e.get("manual_containers", [])
seen = "turnstile-intruder" in manual
# And it must SURVIVE a swap aimed at the same card.
api("/slot", {"slot": T["a"]}, t=90)
time.sleep(3)
survives = "turnstile-intruder" in sh('docker ps --format "{{.Names}}"')
sh("docker rm -f turnstile-intruder >/dev/null 2>&1")
note("container started by hand",
     "reported as manual AND survives a swap",
     f"seen={seen} · survives={survives} · manual={manual}",
     "OK" if seen and survives else "BROKE")

# ── 9 · the service is killed during a load ─────────────────────────────
reset(T)
threading.Thread(target=lambda: api("/slot", {"slot": T["big"]}, t=120)).start()
time.sleep(4)
sh(f"systemctl --user kill -s KILL {UNIT}")
time.sleep(12)
up = alive()
s, _ = api("/health")
c, _ = api("/counters")
note("service KILLED mid-load",
     "restarts on its own, the state stays coherent, no orphan container",
     f"service {'restarted' if up else 'DEAD'} · health={s.get('ok')} · "
     f"swaps={c.get('swaps_succeeded')}/{c.get('swaps_requested')}",
     "OK" if up and s.get("ok") else "BROKE")

# ── 10 · OSCILLATION: alternating requests, back to back ────────────────
if T["b"]:
    reset(T)
    t0, seq = time.time(), []
    for i in range(6):
        r, _ = api("/slot", {"slot": T["a"] if i % 2 == 0 else T["b"]}, t=120)
        seq.append(r.get("state"))
    took = round(time.time() - t0)
    note("forced oscillation: 6 alternating requests",
         "the service holds, no orphan container, both cards coherent",
         f"states={seq} in {took} s · service {'alive' if alive() else 'DEAD'}",
         "OK" if alive() and all(
             e in ("served", "queued", "already_serving", "rate_limited")
             for e in seq) else "BROKE")

# ── 11 · LOAD: ten concurrent requests for the SAME slot ────────────────
reset(T)
got = []
threads = [threading.Thread(target=lambda: got.append(
    api("/slot", {"slot": T["a"]}, t=120)[0].get("state"))) for _ in range(10)]
for t in threads:
    t.start()
for t in threads:
    t.join(timeout=140)
note("ten concurrent requests for the SAME slot",
     "one swap, the nine others served or queued, service alive, none silent",
     f"states={sorted({str(x) for x in got})} · n={len(got)} · "
     f"service {'alive' if alive() else 'DEAD'}",
     "OK" if alive() and len(got) == 10 and None not in got else "BROKE")

# ── 12 · FULL DISK: the corpus can no longer be written ─────────────────
saved_corpus = open(CORPUS).read() if os.path.exists(CORPUS) else ""
os.makedirs(os.path.dirname(CORPUS), exist_ok=True)
# Making the file read-only has the same effect as a full disk.
open(CORPUS, "a").close()
os.chmod(CORPUS, 0o444)
r, _ = api("/pair", {"when": "test", "remote": {}, "local": None})
still = alive()
os.chmod(CORPUS, 0o644)
open(CORPUS, "w").write(saved_corpus)
note("corpus not writable (full disk)",
     "a NAMED refusal (507 + reason), not a dropped connection",
     f"response={r} · service {'alive' if still else 'DEAD'}",
     "OK" if still and ("error" in r or r.get("archived") is False) else "BROKE")

# ── 13 · the map is re-read on every request ────────────────────────────
r1, _ = api("/slots")
n1 = len(r1.get("slots", {}))
api("/slot", {"slot": T["a"]}, t=90)
r3, _ = api("/slots")
n3 = len(r3.get("slots", {}))
note("map re-read on every request",
     "the slot count stays coherent after a swap",
     f"{n1} slots before, {n3} after",
     "OK" if n1 == n3 >= 2 else "BROKE")

# ── 14 · RATE CEILING: a looping client must be stopped ─────────────────
if T["b"]:
    reset(T)
    stopped_at = None
    seq = []
    for i in range(34):
        r, _ = api("/slot", {"slot": T["a"] if i % 2 == 0 else T["b"]}, t=120)
        seq.append(r.get("state"))
        if r.get("state") == "rate_limited":
            stopped_at = i + 1
            break
    note("rate ceiling on frequency",
         "past 30 swaps an hour, the turnstile refuses instead of obeying",
         f"stopped at request {stopped_at or 'NEVER'} · last={seq[-1]} · "
         f"service {'alive' if alive() else 'DEAD'}",
         "OK" if stopped_at and alive() else "BROKE")

# ── summary ─────────────────────────────────────────────────────────────
reset(T)
print("=" * 70)
for name, v in results:
    print(f"  {v:<6} {name}")
broke = [n for n, v in results if v == "BROKE"]
check = [n for n, v in results if v == "CHECK"]
print(f"\n  {len(results) - len(broke) - len(check)} OK · {len(check)} to check "
      f"· {len(broke)} broke")
if broke:
    print("  BROKE:", ", ".join(broke))
if check:
    print("  CHECK:", ", ".join(check))
raise SystemExit(1 if broke else 0)
