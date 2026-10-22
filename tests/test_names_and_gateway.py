"""The two commands that keep names honest: `turnstile-names` derives a slot's
name from what its container carries, `turnstile-litellm` turns the map into a
gateway config. Both are run for real here — as subprocesses on a fixture map,
or loaded by path for the pure derivation — never just imported and admired.
"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAMES = os.path.join(ROOT, "bin", "turnstile-names")
GATEWAY = os.path.join(ROOT, "bin", "turnstile-litellm")


@pytest.fixture(scope="module")
def names():
    loader = importlib.machinery.SourceFileLoader("turnstile_names", NAMES)
    spec = importlib.util.spec_from_loader("turnstile_names", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def container(image, cmd, env=()):
    return {"Config": {"Image": image, "Cmd": cmd, "Env": list(env)}}


UUIDS = {"GPU-aaaa": "0", "GPU-bbbb": "1"}


def test_name_comes_from_the_weights_file_engine_and_card(names):
    c = container("ghcr.io/ggml-org/llama.cpp:server-cuda",
                  ["-m", "/models/Q3.8-heretic-9BQ8.gguf", "-ngl", "99"],
                  ["CUDA_VISIBLE_DEVICES=GPU-bbbb"])
    assert names.derive(c, UUIDS) == "Q3.8-heretic-9BQ8-llamacpp-c1"


def test_a_split_model_is_named_without_its_shard_counter(names):
    c = container("llama-q4mtp:2026-09-14",
                  ["-m", "/models/Q3.8-flashnextH2-177BIQ3XXS-00001-of-00005.gguf"],
                  ["CUDA_VISIBLE_DEVICES=GPU-aaaa"])
    assert names.derive(c, UUIDS) == "Q3.8-flashnextH2-177BIQ3XXS-llama-q4mtp-c0"


def test_kvmem_image_is_named_kvmem(names):
    c = container("kvmem-llama:13.4.1", ["-m", "/models/X.gguf"],
                  ["CUDA_VISIBLE_DEVICES=GPU-aaaa"])
    assert names.derive(c, UUIDS) == "X-KVMem-c0"


def test_no_visible_device_means_every_card(names):
    c = container("ghcr.io/ggml-org/llama.cpp:server-cuda", ["-m", "/m/Y.gguf"])
    assert names.derive(c, UUIDS) == "Y-llamacpp-x2"


def test_zero_gpu_layers_means_cpu_whatever_the_env(names):
    c = container("ghcr.io/ggml-org/llama.cpp:server-cuda",
                  ["-m", "/m/Z.gguf", "-ngl", "0"], ["CUDA_VISIBLE_DEVICES=GPU-aaaa"])
    assert names.derive(c, UUIDS) == "Z-llamacpp-cpu"


def test_unknown_image_keeps_its_own_name(names):
    c = container("llama-q4mtp:2026-09-14", ["-m", "/m/F.gguf"])
    assert names.derive(c, UUIDS) == "F-llama-q4mtp-x2"


def test_changing_the_water_changes_the_name(names):
    """The whole point: same vase, different model, different name."""
    before = container("ghcr.io/ggml-org/llama.cpp:server-cuda", ["-m", "/m/A-9B.gguf"])
    after = container("ghcr.io/ggml-org/llama.cpp:server-cuda", ["-m", "/m/A-27B.gguf"])
    assert names.derive(before, UUIDS) != names.derive(after, UUIDS)


def test_no_weights_argument_derives_nothing(names):
    assert names.derive(container("img", ["--port", "1"]), UUIDS) is None


MAP = """\
# comment

A-llamacpp-c1|8095|1|A|-m /models/A.gguf -ngl 99 -c 196608 --port 8095
B-KVMem-x2|9120|all|B|-m /models/B.gguf -c 131072
not|a|slot
"""


def run_gateway(tmp_path, text):
    f = tmp_path / "containers.map"
    f.write_text(text)
    env = dict(os.environ, TURNSTILE_MAP=str(f))
    return subprocess.run([sys.executable, GATEWAY], capture_output=True,
                          text=True, env=env, check=False)


def test_gateway_has_one_entry_per_slot_named_like_the_slot(tmp_path):
    r = run_gateway(tmp_path, MAP)
    assert r.returncode == 0, r.stderr
    assert r.stdout.count("- model_name:") == 2
    assert '- model_name: "A-llamacpp-c1"' in r.stdout
    assert 'api_base: "http://127.0.0.1:8095/v1"' in r.stdout
    assert 'api_base: "http://127.0.0.1:9120/v1"' in r.stdout
    assert 'model: "openai//models/B.gguf"' in r.stdout
    assert "max_input_tokens: 196608" in r.stdout


def test_gateway_declares_no_fallback_and_no_alias(tmp_path):
    r = run_gateway(tmp_path, MAP)
    assert "fallbacks" not in r.stdout
    assert "router_settings" not in r.stdout


def test_gateway_refuses_an_empty_map(tmp_path):
    r = run_gateway(tmp_path, "# nothing\n")
    assert r.returncode != 0
    assert r.stdout == ""


def test_gateway_refuses_duplicate_names(tmp_path):
    r = run_gateway(tmp_path, MAP + "A-llamacpp-c1|8096|1|A|-m /models/A.gguf\n")
    assert r.returncode != 0


OPENFOX = os.path.join(ROOT, "integrations", "turnstile-openfox")

FOX = {
    "providers": [
        {"id": "direct-8095", "url": "http://127.0.0.1:8095/v1", "backend": "llamacpp",
         "models": [{"id": "/models/old.gguf", "selected": True}]},
        {"id": "remote", "url": "https://api.example.com/v1", "backend": "openai",
         "models": []},
    ],
    "activeProviderId": "direct-8095",
    "defaultModelSelection": "direct-8095//models/old.gguf",
}


def run_openfox(tmp_path, map_text, config_text):
    m = tmp_path / "containers.map"
    m.write_text(map_text)
    c = tmp_path / "config.json"
    c.write_text(config_text)
    env = dict(os.environ, TURNSTILE_MAP=str(m), OPENFOX_CONFIG=str(c))
    r = subprocess.run([sys.executable, OPENFOX], capture_output=True, text=True,
                       env=env, check=False)
    return r, c


def test_openfox_goes_through_the_gateway_and_keeps_the_selected_port(tmp_path):
    r, c = run_openfox(tmp_path, MAP, json.dumps(FOX))
    assert r.returncode == 0, r.stderr
    d = json.loads(c.read_text())
    ids = [p["id"] for p in d["providers"]]
    assert ids == ["turnstile", "remote"]
    gw = d["providers"][0]
    assert gw["url"] == "http://127.0.0.1:4000/v1"
    assert [m["id"] for m in gw["models"]] == ["A-llamacpp-c1", "B-KVMem-x2"]
    assert gw["models"][0]["contextWindow"] == 196608 * 11 // 12 == 180224
    assert d["activeProviderId"] == "turnstile"
    assert d["defaultModelSelection"] == "turnstile/A-llamacpp-c1"


def test_openfox_drops_the_debris_left_after_the_object(tmp_path):
    r, c = run_openfox(tmp_path, MAP, json.dumps(FOX) + '\n}t": {"type": 1}\n')
    assert r.returncode == 0, r.stderr
    json.loads(c.read_text())


def test_openfox_refuses_an_empty_map(tmp_path):
    r, c = run_openfox(tmp_path, "# none\n", json.dumps(FOX))
    assert r.returncode != 0
    assert json.loads(c.read_text()) == FOX


def test_openfox_removes_a_local_provider_on_a_port_the_map_does_not_know(tmp_path):
    fox = json.loads(json.dumps(FOX))
    fox["providers"].append({"id": "dead-8094", "url": "http://127.0.0.1:8094/v1",
                             "backend": "llamacpp", "models": []})
    r, c = run_openfox(tmp_path, MAP, json.dumps(fox))
    assert r.returncode == 0, r.stderr
    assert [p["id"] for p in json.loads(c.read_text())["providers"]] == ["turnstile", "remote"]
