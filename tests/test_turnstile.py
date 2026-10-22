"""Unit tests for the parts that can be checked without a machine.

Deliberately NOT the fault-injection benchmarks. Those live in
`fault-injection/`, they kill services and stop containers, and they must
never run in CI. What is tested here is the pure logic: parsing the map,
choosing a load estimate, deriving the release slots, and reading a server
log. Those four are where a silent mistake would make the allocator confident
and wrong.
"""
import importlib.util
import os
import sys
import tempfile

import pytest

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "bin", "turnstiled")


@pytest.fixture(scope="module")
def ts():
    """Load the service as a module.

    It has no .py extension on purpose — it is a command, not a library — so
    it is loaded by path. Importing it probes for a GPU tool and finds none
    in CI, which is exactly the "no vendor tool" path we want exercised.
    """
    spec = importlib.util.spec_from_loader(
        "turnstiled", importlib.machinery.SourceFileLoader("turnstiled", BIN))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["turnstiled"] = mod
    spec.loader.exec_module(mod)
    return mod


# ── the map ─────────────────────────────────────────────────────────────

MAP = """\
# a comment, and a blank line follow

assistant-c1|8095|1|my-9b|--host 0.0.0.0 --port 8095 -m /models/a.gguf -c 32768
big-x2|8120|all|my-27b|--port 8120 -m /models/b.gguf -c 26624 -ts 1,1
fallback|8084|cpu|my-3b|--port 8084 -m /models/c.gguf -c 8192
too|few|fields
"""


def test_map_parses_every_field(ts, monkeypatch, tmp_path):
    f = tmp_path / "containers.map"
    f.write_text(MAP)
    monkeypatch.setattr(ts, "MAP", str(f))
    slots = ts.read_map()

    assert set(slots) == {"assistant-c1", "big-x2", "fallback"}, \
        "comments, blank lines and short lines must all be skipped"

    a = slots["assistant-c1"]
    assert a["port"] == 8095
    assert a["card"] == "1"
    assert a["model"] == "/models/a.gguf"
    assert a["context_requested"] == 32768

    assert slots["big-x2"]["card"] == "all"
    assert slots["fallback"]["card"] == "cpu"


def test_unreadable_map_returns_empty_not_crash(ts, monkeypatch):
    monkeypatch.setattr(ts, "MAP", "/nonexistent/containers.map")
    assert ts.read_map() == {}


# ── the estimator ───────────────────────────────────────────────────────

def test_estimator_keeps_the_seed_until_enough_points(ts):
    prof = (1, False)
    few = [{"profile": [1, False], "s_per_gb": 9.9}] * (ts.MIN_POINTS - 1)
    assert ts.seconds_per_gb(prof, few) == ts.SEEDS[prof], \
        "below MIN_POINTS the measured value must not be trusted"


def test_estimator_replaces_the_seed_once_measured(ts):
    prof = (1, False)
    enough = [{"profile": [1, False], "s_per_gb": 0.5}] * ts.MIN_POINTS
    assert ts.seconds_per_gb(prof, enough) == 0.5


def test_estimator_uses_the_median_not_the_mean(ts):
    # One pathological load must not move the estimate. A mean would.
    prof = (1, False)
    pts = [{"profile": [1, False], "s_per_gb": v}
           for v in (0.4, 0.4, 0.5, 0.5, 90.0)]
    assert ts.seconds_per_gb(prof, pts) == 0.5


def test_estimator_separates_profiles(ts):
    pts = [{"profile": [2, True], "s_per_gb": 0.03}] * ts.MIN_POINTS
    assert ts.seconds_per_gb((1, False), pts) == ts.SEEDS[(1, False)], \
        "points measured on two cards must not inform the one-card estimate"


def test_unknown_profile_falls_back_rather_than_raising(ts):
    assert ts.seconds_per_gb((7, False), []) > 0


# ── the release slots ───────────────────────────────────────────────────

def test_release_slots_one_per_card_plus_one_for_all(ts, monkeypatch):
    monkeypatch.setattr(ts, "CARD_INDEX", {"aaaa": "0", "bbbb": "1"})
    slots = ts._release_slots()
    assert set(slots) == {"free-c0", "free-c1", "free-all"}
    assert slots["free-all"]["footprint"] == "all"
    assert slots["free-c1"]["footprint"] == "1"


def test_single_card_gets_no_free_all(ts, monkeypatch):
    monkeypatch.setattr(ts, "CARD_INDEX", {"aaaa": "0"})
    assert set(ts._release_slots()) == {"free-c0"}


def test_no_card_at_all_yields_no_release_slot(ts, monkeypatch):
    monkeypatch.setattr(ts, "CARD_INDEX", {})
    assert ts._release_slots() == {}


# ── reading the server log ──────────────────────────────────────────────
#
# This is the fourth fact — the one that catches a 3B answering on a port
# everyone believes carries a 27B.

LOG_READY = """\
common_params_print_info: n_ctx = 32768
load_model: loading model '/models/the-real-one.gguf'
load_model: initializing
llama_context: n_ctx_slot = 16384
main: server is listening on http://0.0.0.0:8095
"""


def test_log_yields_stage_model_and_granted_context(ts):
    stage, model, context = ts.stage_and_model(LOG_READY)
    assert stage == "ready"
    assert model == "/models/the-real-one.gguf"
    assert context == 16384, "the context GRANTED, not the one requested"


def test_partial_log_reports_the_stage_reached(ts):
    stage, model, context = ts.stage_and_model(
        "common_params_print_info: n_ctx = 32768\n"
        "load_model: loading model '/models/x.gguf'\n")
    assert stage == "reading_weights"
    assert model == "/models/x.gguf"
    assert context is None


def test_empty_log_asserts_nothing(ts):
    assert ts.stage_and_model("") == (None, None, None)


# ── state file ──────────────────────────────────────────────────────────

def test_corrupt_state_loads_as_empty_rather_than_raising(ts, monkeypatch):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        f.write("{this is not json")
        path = f.name
    monkeypatch.setattr(ts, "STATE_FILE", path)
    state = ts.load_state()
    os.unlink(path)
    assert state["slot"] is None
    assert state["counters"]["swaps_requested"] == 0


def test_load_state_backfills_counters_added_later(ts, monkeypatch, tmp_path):
    # An old state file must not make a new counter raise a KeyError.
    f = tmp_path / "state.json"
    f.write_text('{"slot": "x", "counters": {"swaps_requested": 3}}')
    monkeypatch.setattr(ts, "STATE_FILE", str(f))
    state = ts.load_state()
    assert state["counters"]["swaps_requested"] == 3
    assert state["counters"]["requests_total"] == 0


# ── remembering the previous slot ───────────────────────────────────────
#
# This is what lets a game hand a card back without a second program
# holding the answer in a file of its own.

def test_previous_slot_is_recorded_when_leaving(ts):
    state = {"slot": "coder-c1"}
    ts.remember_previous(state, "free-c0")
    assert state["previous_slot"] == "coder-c1"


def test_going_back_to_the_same_slot_does_not_overwrite_memory(ts):
    # Asking twice for the slot already serving must not make it its own
    # predecessor — that would turn `restore` into a no-op forever.
    state = {"slot": "coder-c1", "previous_slot": "big-x2"}
    ts.remember_previous(state, "coder-c1")
    assert state["previous_slot"] == "big-x2"


def test_nothing_recorded_from_a_blank_state(ts):
    state = {"slot": None}
    ts.remember_previous(state, "coder-c1")
    assert "previous_slot" not in state


def test_restore_resolves_to_the_remembered_slot(ts):
    assert ts.resolve("restore", {"previous_slot": "coder-c1"}) == "coder-c1"


def test_restore_with_no_memory_resolves_to_nothing(ts):
    assert ts.resolve("restore", {}) is None


def test_a_normal_name_passes_through_untouched(ts):
    assert ts.resolve("coder-c1", {"previous_slot": "big-x2"}) == "coder-c1"


def test_take_then_restore_round_trip(ts):
    """The game sequence, in full: serving → free → back to serving."""
    state = {"slot": "coder-c1"}
    ts.remember_previous(state, "free-c0")     # the game takes the card
    state["slot"] = "free-c0"
    assert ts.resolve("restore", state) == "coder-c1"   # the game quits


def test_restore_to_a_slot_already_serving_is_a_reuse_not_a_load(ts):
    """The defect this ordering fixes.

    `restore` used to reach the already-serving test under its own name,
    match nothing, and go on to load a container that was already up —
    reporting a 30 s failure on a slot that was serving the whole time. The
    alias has to become a real slot name before anything inspects it.
    """
    state = {"slot": "free-c0", "previous_slot": "coder-c1"}
    assert ts.resolve("restore", state) == "coder-c1", \
        "the name the rest of the request sees must be the real slot"


def test_a_zero_cost_request_still_moves_the_declaration(ts):
    """Being satisfied instantly is still a slot change.

    Observed on the machine: a game handed its card back, the slot was
    serving correctly, and `declared_slot` still read `free-c0` — because
    the request had been answered without anyone writing down what it
    meant. Nothing had failed, and the state lied anyway.
    """
    state = {"slot": "free-c0", "previous_slot": "coder-c1"}
    # what the handler does when the wanted slot is already serving
    wanted = "coder-c1"
    if state.get("slot") != wanted:
        ts.remember_previous(state, wanted)
        state["slot"] = wanted
    assert state["slot"] == "coder-c1"
    assert state["previous_slot"] == "free-c0", \
        "and the slot we left becomes the one to go back to"


# ── the measurement, and the fifth fact ─────────────────────────────────
#
# Every slot in this fleet already ran with `--metrics`, and nothing read it.
# What follows exercises the reader and the one judgment built on it: a card
# holding memory says SOMETHING is loaded, throughput says it is THIS slot.

METRICS = """\
# HELP llamacpp:prompt_tokens_total Number of prompt tokens processed.
# TYPE llamacpp:prompt_tokens_total counter
llamacpp:prompt_tokens_total 711054
llamacpp:prompt_seconds_total 174.876
llamacpp:tokens_predicted_total 13103
llamacpp:tokens_predicted_seconds_total 97.789
llamacpp:n_decode_total 6348
llamacpp:n_tokens_max 5122
llamacpp:prompt_tokens_seconds 4066.05
llamacpp:predicted_tokens_seconds 133.993
llamacpp:requests_processing 0
llamacpp:requests_deferred 0
llamacpp:n_busy_slots_per_decode 1
"""


def _serve(body, port_holder):
    """A real HTTP server on a real ephemeral port. No double: the reader uses
    urllib, so an imitation could drift from it without anyone noticing."""
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/metrics":
                self.send_response(404); self.end_headers(); return
            b = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    port_holder.append(srv.server_address[1])
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_metrics_reads_what_the_server_publishes(ts):
    ports = []
    srv = _serve(METRICS, ports)
    try:
        d = ts.metrics(ports[0])
    finally:
        srv.shutdown()
    assert d is not None
    # 13103 / 97.789 = 134.0 — the cumulative rate, not the instantaneous one.
    assert d["generation_tok_s_cumul"] == 134.0
    assert d["generation_tok_s_instant"] == 133.993
    assert d["prompt_tok_s_cumul"] == 4066.0
    assert d["contexte_le_plus_large_servi"] == 5122
    assert d["requetes_differees"] == 0
    assert d[ts.TOKENS_KEY] == 13103


def test_metrics_is_none_when_nothing_answers(ts):
    # An unbound port, and no port at all: both are "no measurement", which is
    # a state of its own and must never read as zero.
    assert ts.metrics(1) is None
    assert ts.metrics(None) is None


def test_metrics_is_none_on_a_body_that_is_not_metrics(ts):
    ports = []
    srv = _serve("<html>not metrics at all</html>", ports)
    try:
        assert ts.metrics(ports[0]) is None
    finally:
        srv.shutdown()


def test_the_fifth_fact_accepts_a_card_rate(ts):
    measure = {"generation_tok_s_cumul": 134.0, ts.TOKENS_KEY: 13103}
    assert ts.rate_credible(measure, "1") is True


def test_the_fifth_fact_refuses_a_cpu_rate_on_a_declared_card(ts):
    """The failure this fact exists for: a model that fell back to the CPU
    keeps a port that answers and a green probe. Measured here: 12 tok/s on a
    slot declared on card 1."""
    measure = {"generation_tok_s_cumul": 12.0, ts.TOKENS_KEY: 4000}
    assert ts.rate_credible(measure, "1") is False


def test_a_streamed_moe_at_its_warm_rate_is_on_the_card(ts):
    """Flash-Next on the 5080, experts read from the SSD: ~35 tok/s warm, ~26 cumulated after its cold start."""
    assert ts.rate_credible({"generation_tok_s_cumul": 26.0, ts.TOKENS_KEY: 1200}, "0") is True


def test_a_cold_first_request_does_not_decide_alone(ts):
    assert ts.rate_credible({"generation_tok_s_cumul": 12.7, ts.TOKENS_KEY: 207}, "0") is None


def test_the_fifth_fact_judges_nothing_it_cannot_judge(ts):
    slow = {"generation_tok_s_cumul": 12.0, ts.TOKENS_KEY: 4000}
    # A slot that IS the CPU is not at fault for running at CPU speed.
    assert ts.rate_credible(slow, "cpu") is None
    # No measurement at all.
    assert ts.rate_credible(None, "1") is None
    # Too few tokens to mean anything: a slot that just started.
    assert ts.rate_credible({"generation_tok_s_cumul": 5.0,
                              ts.TOKENS_KEY: 12}, "1") is None
    # Metrics present but no rate yet.
    assert ts.rate_credible({"generation_tok_s_cumul": None,
                              ts.TOKENS_KEY: 900}, "1") is None


def test_the_state_announces_its_version(ts):
    """A service that cannot say its version does not let you know whether the
    machine runs what the repository describes. Paid for on 30/09/2026: a tool
    ran two hours in a hand-installed version while the repository carried
    another, and nothing anywhere could say so."""
    assert hasattr(ts, "VERSION")
    assert ts.VERSION.count(".") == 2, "semver: major.minor.patch"
    state = ts.observe({})
    assert state["version"] == ts.VERSION


def test_the_package_and_the_daemon_announce_the_same_version(ts):
    """Two places carry the version, and on 02/10/2026 they disagreed: the
    daemon said 0.2.0 while pyproject.toml still said 0.1.0. The constant
    exists so the machine can say what it runs; a second number that drifts
    from it brings back the doubt it was meant to remove."""
    import pathlib
    import re
    py = (pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
    assert re.search(r'^version = "([^"]+)"$', py, re.MULTILINE).group(1) == ts.VERSION


# ── the two tokens ──────────────────────────────────────────────────────────
# Until 02/10/2026 nothing tested the authentication at all, and one token did
# everything: every caller that only read /state could also empty a card.


@pytest.fixture
def api(ts, monkeypatch):
    """A real server on a free loopback port, with both tokens set."""
    import http.client
    import threading
    monkeypatch.setattr(ts, "TOKEN", "rw-secret")
    monkeypatch.setattr(ts, "READ_TOKEN", "ro-secret")
    srv = ts.Server(("127.0.0.1", 0), ts.make_handler([{}], ts.load_state()))
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def call(method, path, token=None):
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        c.request(method, path, body=b"{}" if method == "POST" else None, headers=headers)
        return c.getresponse().status

    yield call
    srv.shutdown()
    srv.server_close()


def test_reading_without_a_token_is_refused(api):
    assert api("GET", "/health") == 401


def test_the_read_token_reads(api):
    assert api("GET", "/health", "ro-secret") == 200


def test_the_read_token_cannot_write(api):
    # /nope, not /slot: a route that would 404 once authorised, so a pass
    # here can only mean the token was refused before any swap was attempted.
    assert api("POST", "/nope", "ro-secret") == 401


def test_the_read_write_token_reads_and_writes(api):
    assert api("GET", "/health", "rw-secret") == 200
    assert api("POST", "/nope", "rw-secret") == 404


def test_a_wrong_token_is_refused_both_ways(api):
    assert api("GET", "/health", "guess") == 401
    assert api("POST", "/nope", "guess") == 401


def test_a_slot_without_a_container_stops_nothing(ts, monkeypatch):
    # 02/10/2026: the holder of card 1 was stopped, then `docker start` failed
    # on a container never created, and the card was left empty.
    cmds = []

    def fake_sh(cmd, timeout=20):
        cmds.append(cmd)
        if cmd.startswith("docker ps -a"):
            return "Q-9B-c1\\nQ-14B-c0"
        if cmd.startswith("docker ps"):
            return "Q-9B-c1"
        return ""
    monkeypatch.setattr(ts, "sh", fake_sh)
    slots = {"Q-9B-c1": {"card": "1"}, "Q-27B-c1": {"card": "1"}}
    state = {"counters": {}, "measurements": []}
    out = ts.swap("Q-27B-c1", slots, state)
    assert out["state"] == "no_container" and "nothing was stopped" in out["reason"]
    assert not any(c.startswith(("docker stop", "docker start")) for c in cmds)
    assert "Q-27B-c1" not in ts.IN_FLIGHT


# ── card guests ─────────────────────────────────────────────────────────

@pytest.fixture
def docker(ts, monkeypatch, tmp_path):
    """A fake Docker: running/existing sets, and the commands it was given."""
    g = tmp_path / "guests.map"
    g.write_text("# the house's voice\nvoice-c0|0\nbad-line\nvoice-cpu|cpu\n")
    monkeypatch.setattr(ts, "GUESTS", str(g))
    monkeypatch.setattr(ts, "RELEASE_SLOTS", {"free-c0": {"footprint": "0", "purpose": "game"},
                                              "free-all": {"footprint": "all", "purpose": "render"}})
    world = {"running": set(), "existing": {"voice-c0", "assistant-c1", "big-c0", "big-x2"}, "cmds": []}

    def sh(cmd, timeout=20):
        world["cmds"].append(cmd)
        verb, name = cmd.split()[1], cmd.split()[-1]
        (world["running"].add if verb == "start" else world["running"].discard)(name)
        return ""
    monkeypatch.setattr(ts, "sh", sh)
    monkeypatch.setattr(ts, "running_containers", lambda: set(world["running"]))
    monkeypatch.setattr(ts, "existing_containers", lambda: set(world["existing"]))
    monkeypatch.setattr(ts, "save_state", lambda s: None)
    monkeypatch.setattr(ts, "memory_used", lambda: [0, 0])
    monkeypatch.setattr(ts.time, "sleep", lambda s: None)
    slots = {"assistant-c1": {"card": "1"}, "big-c0": {"card": "0"}, "big-x2": {"card": "all"}}
    return world, slots


def test_guests_map_keeps_only_card_guests(ts, docker):
    assert ts.read_guests() == {"voice-c0": "0"}


def test_a_guest_starts_when_its_card_holds_nothing(ts, docker):
    world, slots = docker
    world["running"] = {"assistant-c1"}
    assert ts.wake_guests(slots, {"slot": "assistant-c1"}) == ["voice-c0"]


def test_a_guest_stays_off_while_its_card_is_released_to_a_game(ts, docker):
    _world, slots = docker
    assert ts.wake_guests(slots, {"slot": "free-c0"}) == []
    assert ts.wake_guests(slots, {"slot": "free-all"}) == []


def test_a_guest_stays_off_while_a_slot_holds_its_card(ts, docker):
    world, slots = docker
    world["running"] = {"big-x2"}
    assert ts.wake_guests(slots, {"slot": "big-x2"}) == []


def test_releasing_the_card_stops_its_guest_first(ts, docker):
    world, slots = docker
    world["running"] = {"voice-c0", "assistant-c1"}
    state = {"counters": ts.empty_counters(), "slot": "assistant-c1"}
    r = ts.release("free-c0", slots, state)
    assert world["cmds"][0] == "docker stop voice-c0" and "voice-c0" not in world["running"]
    assert r["state"] == "served" and "voice-c0" in r["stopped"]
    assert "docker start voice-c0" not in world["cmds"]       # released to the game: it stays off


def test_a_slot_loading_on_the_card_stops_the_guest_before_it_starts(ts, docker, monkeypatch):
    world, slots = docker
    world["running"] = {"voice-c0"}
    monkeypatch.setattr(ts, "read_log_since", lambda n, t: "")
    monkeypatch.setattr(ts, "stage_and_model", lambda log: ("ready", None, None))
    monkeypatch.setattr(ts, "size_gb", lambda n, s: 1)
    monkeypatch.setattr(ts, "profile", lambda n, s: (1, False))
    state = {"counters": ts.empty_counters(), "measurements": []}
    assert ts.swap("big-c0", slots, state)["state"] == "served"
    assert world["cmds"].index("docker stop voice-c0") < world["cmds"].index("docker start big-c0")
    assert "voice-c0" not in world["running"]


def test_a_guest_stays_beside_a_slot_it_shares_the_card_with(ts, docker, monkeypatch):
    """The voice and the light coder share card 0; a big slot or a game still stop the voice."""
    world, slots = docker
    with open(ts.GUESTS, "w") as f:
        f.write("voice-c0|0|light-c0\n")
    slots = {**slots, "light-c0": {"card": "0"}}
    world["existing"].add("light-c0")
    world["running"] = {"voice-c0"}
    monkeypatch.setattr(ts, "read_log_since", lambda n, t: "")
    monkeypatch.setattr(ts, "stage_and_model", lambda log: ("ready", None, None))
    monkeypatch.setattr(ts, "size_gb", lambda n, s: 1)
    monkeypatch.setattr(ts, "profile", lambda n, s: (1, False))
    state = {"counters": ts.empty_counters(), "measurements": []}
    assert ts.swap("light-c0", slots, state)["state"] == "served"
    assert "docker stop voice-c0" not in world["cmds"] and {"voice-c0", "light-c0"} <= world["running"]
    assert ts.read_guests() == {"voice-c0": "0"}                       # the third field changes nothing else
    world["running"] = {"light-c0"}                                       # the voice stopped for any reason:
    assert ts.wake_guests(slots, {"slot": "light-c0"}) == ["voice-c0"]   # the shared slot does not keep it off
    assert ts.swap("big-c0", slots, state)["state"] == "served"
    assert "docker stop voice-c0" in world["cmds"]                        # any other slot on card 0 still evicts it


def test_after_a_game_every_slot_the_release_stopped_comes_back(ts, docker):
    """Card 1 serves the 27B, card 0 the light coder beside the voice: a game takes card 0, then leaves."""
    world, slots = docker
    with open(ts.GUESTS, "w") as f:
        f.write("voice-c0|0|light-c0\n")
    slots = {**slots, "light-c0": {"card": "0"}}
    world["existing"].add("light-c0")
    world["running"] = {"assistant-c1", "light-c0", "voice-c0"}
    state = {"counters": ts.empty_counters(), "slot": "assistant-c1"}
    ts.release("free-c0", slots, state)
    assert world["running"] == {"assistant-c1"} and state["released_slots"] == ["light-c0"]
    assert ts.bring_back(slots, state) == ["light-c0"]
    assert ts.wake_guests(slots, {"slot": "assistant-c1"}) == ["voice-c0"]
    assert world["running"] == {"assistant-c1", "light-c0", "voice-c0"} and "released_slots" not in state


def test_bring_back_leaves_the_card_a_new_slot_took(ts, docker):
    world, slots = docker
    world["running"] = {"big-c0"}
    state = {"released_slots": ["big-c0", "assistant-c1"]}
    world["running"] = set()
    assert ts.bring_back(slots, state, skip_card="0") == ["assistant-c1"]


def test_restore_to_a_slot_already_serving_wakes_the_guests(ts, docker, monkeypatch):
    """After a game: `restore` costs no swap (the model never left), but card 0 is free again."""
    import http.client
    import json as _json
    import threading
    world, slots = docker
    world["running"] = {"assistant-c1"}
    monkeypatch.setattr(ts, "TOKEN", "rw-secret")
    monkeypatch.setattr(ts, "read_map", lambda: slots)
    monkeypatch.setattr(ts, "observe", lambda s: {"slots_live": {"assistant-c1": {"status": "serving"}}})
    state = {"counters": ts.empty_counters(), "slot": "free-c0", "previous_slot": "assistant-c1"}
    srv = ts.Server(("127.0.0.1", 0), ts.make_handler([slots], state))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        c.request("POST", "/slot", body=_json.dumps({"slot": "restore"}),
                  headers={"Authorization": "Bearer rw-secret", "Content-Type": "application/json"})
        assert _json.loads(c.getresponse().read())["state"] == "already_serving"
    finally:
        srv.shutdown()
        srv.server_close()
    assert state["slot"] == "assistant-c1"
    assert "docker start voice-c0" in world["cmds"]


# ── stand-ins ───────────────────────────────────────────────────────────

@pytest.fixture
def standin(ts, docker, monkeypatch, tmp_path):
    """Card 0 serves the big model, card 1 the small one and the voice; a game takes card 0."""
    world, slots = docker
    with open(ts.GUESTS, "w") as f:
        f.write("small-c1|1\nvoice-c1|1|small-c1\n")
    m = tmp_path / "standins.map"
    m.write_text("# release|slot\nfree-c0|assistant-c1\n")
    monkeypatch.setattr(ts, "STANDINS", str(m))
    slots = {**slots, "small-c1": {"card": "1"}}
    world["existing"] |= {"small-c1", "voice-c1"}
    world["running"] = {"big-c0", "small-c1", "voice-c1"}
    return world, slots


def test_standins_map_reads_release_and_slot(ts, standin):
    assert ts.read_standins() == {"free-c0": "assistant-c1"}


def test_a_game_starts_the_standin_on_the_other_card(ts, standin):
    world, slots = standin
    state = {"counters": ts.empty_counters(), "slot": "big-c0"}
    r = ts.release("free-c0", slots, state)
    assert r["state"] == "served" and r["standin"] == "assistant-c1"
    assert world["running"] == {"assistant-c1"}, "the big model left for the game, card 1's guests yielded"
    assert world["cmds"].index("docker stop small-c1") < world["cmds"].index("docker start assistant-c1")
    assert state["slot"] == "free-c0" and state["standin"] == "assistant-c1"
    assert state["released_slots"] == ["big-c0"]


def test_no_standin_configured_changes_nothing(ts, standin, monkeypatch):
    world, slots = standin
    monkeypatch.setattr(ts, "STANDINS", "/nonexistent/standins.map")
    state = {"counters": ts.empty_counters(), "slot": "big-c0"}
    r = ts.release("free-c0", slots, state)
    assert r["standin"] is None and "standin" not in state
    assert world["running"] == {"small-c1", "voice-c1"}


def test_after_the_game_the_standin_goes_and_card_1_gets_its_guests_back(ts, standin, monkeypatch):
    import http.client
    import json as _json
    import threading
    world, slots = standin
    state = {"counters": ts.empty_counters(), "measurements": [], "slot": "big-c0"}
    ts.release("free-c0", slots, state)
    monkeypatch.setattr(ts, "TOKEN", "rw-secret")
    monkeypatch.setattr(ts, "read_map", lambda: slots)
    monkeypatch.setattr(ts, "observe", lambda s: {"slots_live": {}})
    monkeypatch.setattr(ts, "read_log_since", lambda n, t: "")
    monkeypatch.setattr(ts, "stage_and_model", lambda log: ("ready", None, None))
    monkeypatch.setattr(ts, "size_gb", lambda n, s: 1)
    monkeypatch.setattr(ts, "profile", lambda n, s: (1, False))
    srv = ts.Server(("127.0.0.1", 0), ts.make_handler([slots], state))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        c.request("POST", "/slot", body=_json.dumps({"slot": "restore"}),
                  headers={"Authorization": "Bearer rw-secret", "Content-Type": "application/json"})
        assert _json.loads(c.getresponse().read())["slot"] == "big-c0"
    finally:
        srv.shutdown()
        srv.server_close()
    assert world["running"] == {"big-c0", "small-c1", "voice-c1"}
    assert "standin" not in state and state["slot"] == "big-c0"


def test_asking_for_the_standin_itself_keeps_it(ts, standin):
    world, slots = standin
    state = {"counters": ts.empty_counters(), "slot": "big-c0"}
    ts.release("free-c0", slots, state)
    assert ts.dismiss_standin(state, keep="assistant-c1") == "assistant-c1"
    assert "assistant-c1" in world["running"]


# ── load deadline ───────────────────────────────────────────────────────

def test_a_split_model_is_measured_across_all_its_shards(ts, tmp_path):
    for i in (1, 2, 3):
        (tmp_path / f"big-0000{i}-of-00003.gguf").write_bytes(b"x" * 1024)
    slot = {"model": "/models/big-00001-of-00003.gguf", "weights": str(tmp_path)}
    assert ts.size_gb("big", slot) == 3 * 1024 / 2**30


def test_a_big_model_gets_a_longer_load_deadline(ts):
    assert ts.load_deadline(7) == ts.FAIL_AFTER            # a 9B keeps the flat floor
    assert ts.load_deadline(116) == 116 * ts.WORST_S_PER_GB  # Flash-Next: about 4 minutes


def test_the_queue_empties_once_nothing_is_loading(ts):
    """A request that arrived during a load must not leave /state "busy" for ever."""
    ts.IN_FLIGHT["slot-a"] = {"started": 0, "size_gb": 1, "profile": []}
    ts.QUEUE.append("slot-b")
    try:
        assert ts.pending_queue() == ["slot-b"]          # behind a load in flight: still waiting
    finally:
        ts.IN_FLIGHT.clear()
    assert ts.pending_queue() == []                      # the load ended: nobody is behind it
    assert ts.QUEUE == []


def test_a_strata_log_reaches_ready_and_its_size_hint_sets_the_deadline(ts, tmp_path, monkeypatch):
    log = ("[strata] starting the engine: reading the model's weights ...\n[strata] loading the experts into RAM (about 38 GB)\n"
           "[strata] filling the GPU's expert cache (6742 experts, 8.68 GiB of VRAM) ...\n")
    assert ts.stage_and_model(log)[0] == "initialising"
    assert ts.stage_and_model(log + "ready: http://127.0.0.1:8129/v1 (OpenAI: /v1/chat/completions)\n")[0] == "ready"   # as written, no prefix
    assert ts.stage_and_model(log + "[strata] almost ready: http://x\n")[0] == "initialising"
    m = tmp_path / "containers.map"
    m.write_text("s-strata-c0|8129|0|W|strata --serve --context 32768 --size-gb 38\n")
    monkeypatch.setattr(ts, "MAP", str(m))
    slot = ts.read_map()["s-strata-c0"]
    assert slot["size_gb"] == 38.0 and ts.size_gb("s-strata-c0", slot) == 38.0
    assert ts.load_deadline(ts.size_gb("s-strata-c0", slot)) == 76.0          # not the flat 30 s that called Strata failed


def test_a_declared_slot_is_brought_back_once_after_a_reboot_and_not_after_a_mere_restart(ts):
    state = {"slot": "s-c1"}
    assert ts.restore_after_boot(state, "boot-A", 16.0)                                  # just booted, never restored in this boot
    state["boot_id"] = "boot-A"
    assert not ts.restore_after_boot(state, "boot-A", 40.0)                              # once per boot
    assert not ts.restore_after_boot({"slot": "s-c1", "boot_id": "boot-A"}, "boot-A", 3000.0)
    assert not ts.restore_after_boot({"slot": "s-c1"}, "boot-B", 20000.0)                # the daemon restarted long after the boot
    assert ts.restore_after_boot({"slot": "s-c1", "boot_id": "boot-A"}, "boot-B", 30.0)  # a new boot restores again
    assert not ts.restore_after_boot({"slot": "s-c1"}, "", 10.0)                         # unknown boot: do nothing
