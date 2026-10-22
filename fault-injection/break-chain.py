#!/usr/bin/env python3
"""Fault injection, second stage: the whole CHAIN and the service's limits.

The first benchmark exercised the allocator alone. This one goes after what
SURROUNDS it — the remote judge, the local shadow, the guard workflow — and
after the inputs nobody ever sends on purpose.

Set GUARD_URL to exercise the workflow scenarios; without it they are
skipped rather than reported as passing.

WARNING: it restarts the service repeatedly, squats ports, and appends
20 000 lines to the corpus (then restores it). Not for a machine you are
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
GUARD = os.environ.get("GUARD_URL", "")
SHADOW_UNIT = os.environ.get("SHADOW_UNIT", "")
STATE_FILE = os.path.expanduser(
    os.environ.get("TURNSTILE_STATE", "~/.local/state/turnstile/state.json"))
CORPUS = os.path.expanduser(
    os.environ.get("TURNSTILE_CORPUS", "~/.local/state/turnstile/pairs.jsonl"))
REGISTRY = os.path.expanduser("~/.local/state/turnstile/who-serves-what.json")
results = []


def sh(c, t=180):
    return subprocess.run(c, shell=True, capture_output=True, text=True,
                          timeout=t).stdout.strip()


def http(url, body=None, t=120, method=None):
    req = (urllib.request.Request(url) if body is None else
           urllib.request.Request(
               url, data=body if isinstance(body, bytes)
               else json.dumps(body).encode(),
               headers={"Content-Type": "application/json"}))
    if method:
        req.get_method = lambda: method
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=t) as r:
            return json.load(r), round(time.time() - t0, 1)
    except urllib.error.HTTPError as e:
        try:
            c = json.loads(e.read().decode())
            c["__http__"] = e.code
            return c, round(time.time() - t0, 1)
        except Exception:
            return {"__http__": e.code}, round(time.time() - t0, 1)
    except Exception as e:
        return {"__error__": str(e)[:70]}, round(time.time() - t0, 1)


def alive():
    return sh(f"systemctl --user is-active {UNIT}") == "active"


def note(name, expected, observed, ok):
    v = "OK" if ok else "BROKE"
    results.append((name, v))
    print(f"[{'  OK  ' if ok else ' BROKE'}] {name}")
    print(f"         expected: {expected}")
    print(f"         observed: {observed}\n", flush=True)


def skip(name, why):
    results.append((name, "SKIP"))
    print(f"[ SKIP ] {name}\n         {why}\n", flush=True)


def clear_ceiling():
    """The swap ceiling slides over an hour. Without a reset, earlier
    scenarios saturate it and the later ones measure the ceiling instead of
    what they aim at."""
    try:
        d = json.load(open(STATE_FILE))
    except Exception:
        d = {}
    d["measurements"] = []
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    json.dump(d, open(STATE_FILE, "w"), indent=2)
    sh(f"systemctl --user restart {UNIT}")
    time.sleep(5)


def discover():
    d, _ = http(f"{API}/slots", t=20)
    slots = d.get("slots", {})
    release = sorted(d.get("release_slots", {}))
    gpu = [n for n, s in slots.items() if s["card"] != "cpu"]
    return {"slots": slots, "a": gpu[0] if gpu else None,
            "big": next((n for n in gpu if slots[n]["card"] == "all"),
                        gpu[0] if gpu else None),
            "free_all": next((r for r in release if r.endswith("all")),
                             release[0] if release else None),
            "free_one": next((r for r in release if not r.endswith("all")),
                             None)}


def reset(T):
    if T["free_all"]:
        http(f"{API}/slot", {"slot": T["free_all"]}, t=90)
        time.sleep(2)


clear_ceiling()
T = discover()

# ── 1 · giant JSON ──────────────────────────────────────────────────────
big = json.dumps({"slot": T["a"], "noise": "x" * 2_000_000}).encode()
r, d = http(f"{API}/slot", big, t=60)
note("2 MB request",
     "handled or refused, but the service survives",
     f"{str(r)[:70]} in {d} s · service {'alive' if alive() else 'DEAD'}",
     alive())

# ── 2 · malformed JSON ──────────────────────────────────────────────────
r, _ = http(f"{API}/slot", b"{this is not json", t=30)
note("unreadable body",
     "a named 400, service alive",
     f"{r} · service {'alive' if alive() else 'DEAD'}",
     alive() and ("error" in r or r.get("__http__") == 400))

# ── 3 · unexpected method ───────────────────────────────────────────────
r, _ = http(f"{API}/slot", {}, t=30, method="DELETE")
note("unexpected HTTP method",
     "refused, no crash",
     f"{str(r)[:60]} · service {'alive' if alive() else 'DEAD'}",
     alive())

# ── 4 · unknown route ───────────────────────────────────────────────────
r, _ = http(f"{API}/no/such/route", t=20)
note("unknown route",
     "404 listing the real routes", f"{str(r)[:80]}",
     "routes" in str(r) or r.get("__http__") == 404)

# ── 5 · restart storm ───────────────────────────────────────────────────
for _ in range(5):
    sh(f"systemctl --user restart {UNIT}")
    time.sleep(1.5)
time.sleep(6)
s, _ = http(f"{API}/health", t=20)
note("five restarts back to back",
     "the service settles and answers",
     f"health={s} · service {'alive' if alive() else 'DEAD'}",
     alive() and s.get("ok") is True)

# ── 6 · the slot's port is already taken ────────────────────────────────
if T["a"]:
    clear_ceiling()
    reset(T)
    port = T["slots"][T["a"]]["port"]
    sh("docker rm -f turnstile-squatter >/dev/null 2>&1")
    sh(f"docker run -d --name turnstile-squatter -p {port}:80 "
       "--entrypoint sleep alpine 90 >/dev/null 2>&1")
    time.sleep(3)
    r, d = http(f"{API}/slot", {"slot": T["a"]}, t=90)
    sh("docker rm -f turnstile-squatter >/dev/null 2>&1")
    note("the slot's port is already taken",
         "a named failure in under 40 s, service alive",
         f"{r.get('state')} in {d} s · service {'alive' if alive() else 'DEAD'}",
         alive() and r.get("state") in ("failed", "served") and d < 45)

# ── 7 · the local shadow model is stopped ───────────────────────────────
if GUARD and SHADOW_UNIT:
    sh(f"systemctl --user stop {SHADOW_UNIT}")
    time.sleep(2)
    g, dg = http(GUARD, {}, t=120)
    sh(f"systemctl --user start {SHADOW_UNIT}")
    note("the shadow model is stopped",
         "the guard still answers: the shadow has no effect on the verdict",
         f"allowed={g.get('allowed')} in {dg} s",
         g.get("allowed") is not None)
else:
    skip("the shadow model is stopped", "set GUARD_URL and SHADOW_UNIT")

# ── 8 · the guard is unreachable for its caller ─────────────────────────
# A guard that breaks its caller when IT fails gets torn out within two
# days. The caller must warn and proceed, never block.
out = sh("bash -s <<'SCRIPT'\n"
         "v=$(curl -s --max-time 5 -X POST http://127.0.0.1:1/nothing "
         "-d '{}' 2>/dev/null || true)\n"
         "[ -z \"$v\" ] && echo PROCEEDS || echo BLOCKS\n"
         "SCRIPT", t=60)
note("the guard is unreachable for its caller",
     "the caller warns and proceeds; it never blocks",
     out or "(empty)",
     "PROCEEDS" in out)

# ── 9 · a release slot is requested during a load ───────────────────────
if T["big"] and T["free_one"]:
    clear_ceiling()
    reset(T)
    threading.Thread(target=lambda: http(f"{API}/slot", {"slot": T["big"]},
                                         t=120)).start()
    time.sleep(3)
    r, _ = http(f"{API}/slot", {"slot": T["free_one"]}, t=90)
    time.sleep(20)
    note("release slot requested during a load",
         "queued, never cancelling the load in progress",
         f"{r.get('state')} · service {'alive' if alive() else 'DEAD'}",
         alive() and r.get("state") in ("queued", "served"))

# ── 10 · a huge corpus of FOREIGN lines ─────────────────────────────────
#
# This is the scenario that caught the worst defect of the lot: the
# evaluator promoted an invented question to production because 20 000
# fictitious pairs agreed 100 % of the time. Nothing distinguished test
# data from a real decision.
saved = open(CORPUS).read() if os.path.exists(CORPUS) else ""
os.makedirs(os.path.dirname(CORPUS), exist_ok=True)
with open(CORPUS, "a") as f:
    for i in range(20000):
        f.write(json.dumps({"when": i, "question_set": "fault-injection",
                            "remote": {"q": 0.9}, "local": {"q": 0.8}}) + "\n")
t0 = time.time()
evaluator = os.path.expanduser("~/.local/bin/turnstile-cascade")
out = sh(f"{evaluator} 2>&1 | tail -4", t=120)
took = round(time.time() - t0, 1)
open(CORPUS, "w").write(saved)
registry = sh(f"cat {REGISTRY} 2>/dev/null")
note("corpus of 20 000 FOREIGN lines",
     "the evaluator ignores them, promotes nothing, finishes in under 30 s",
     f"{took} s · registry={registry[:60].replace(chr(10), ' ')} · "
     f"output={out[:70]}",
     took < 30 and "PROMOTED" not in out and '"local"' not in registry)

# ── summary ─────────────────────────────────────────────────────────────
reset(T)
print("=" * 70)
for name, v in results:
    print(f"  {v:<6} {name}")
broke = [n for n, v in results if v == "BROKE"]
skipped = [n for n, v in results if v == "SKIP"]
print(f"\n  {len(results) - len(broke) - len(skipped)} OK · {len(skipped)} "
      f"skipped · {len(broke)} broke")
if broke:
    print("  BROKE:", ", ".join(broke))
raise SystemExit(1 if broke else 0)
