"""Saved mathematical receipts are fixtures, not production-isolation acceptance."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from test_science_evidence import FIXTURES, install_saved_runner, sha

from reasoning_ledger.agents import heisenberg_level25_four_block_row0 as feasibility
from reasoning_ledger.agents import heisenberg_level25_four_block_row0_edge_indicator as edge
from reasoning_ledger.agents import heisenberg_level25_four_block_row0_kernel as kernel
from reasoning_ledger.agents import heisenberg_level25_four_block_row0_pair as pair
from reasoning_ledger.agents import heisenberg_level25_four_block_row0_pair_reuse as reuse
from reasoning_ledger.agents import heisenberg_level25_four_block_row0_rank as rank
from reasoning_ledger.agents import project_tools
from reasoning_ledger.agents.science_evidence import certified_receipt_json, profile_evidence_sha256, reviewed_profile_matches
from reasoning_ledger.agents.scientific import ScientificToolError
from reasoning_ledger.canonical import canonical_json

MODULES = {"feasibility": feasibility, "rank": rank, "kernel": kernel, "pair": pair, "reuse": reuse, "edge": edge}
SUFFIXES = {
    "feasibility": "row0_feasibility",
    "rank": "row0_label_unary_rank",
    "kernel": "row0_kernel_rank",
    "pair": "row0_fixed_fiber_pair_calibration",
    "reuse": "row0_pair_reuse",
    "edge": "row0_edge_indicator_rank",
}
FACTORIES = {
    name: getattr(module, "level25_heisenberg_augmented_four_block_" + SUFFIXES[name] + "_profile") for name, module in MODULES.items()
}
PARENTS = {"feasibility": "unary", "rank": "feasibility", "kernel": "rank", "pair": "kernel", "reuse": "pair", "edge": "reuse"}
PARENT_KEYS = {
    "feasibility": "unary",
    "rank": "row0_feasibility",
    "kernel": "label_unary_rank",
    "pair": "kernel_rank",
    "reuse": "pair_calibration",
    "edge": "pair_reuse",
}
TIMEOUTS = {"feasibility": 600, "rank": 180, "kernel": 180, "pair": 240, "reuse": 180, "edge": 120}


def native_output(name, replay=False):
    return (FIXTURES / ("row0_" + name + ("_replay" if replay else "") + "_v1.txt")).read_text(encoding="utf-8")


def python_output(name):
    return (FIXTURES / ("row0_" + name + "_python_v1.json")).read_text(encoding="utf-8")


def python_replay(name):
    stdout = python_output(name)
    if name in ("feasibility", "rank"):
        return json.loads(stdout)
    program = Path(MODULES[name].__file__).with_name(Path(MODULES[name].__file__).stem + "_replay.py").read_text(encoding="utf-8")
    return {
        **json.loads(stdout),
        "program_sha256": sha(program),
        "transcript_sha256": sha(stdout),
        "transcript": stdout,
        "runtime_address_space_limit_bytes": 67108864,
        "interpreter": "TEST-ONLY-RUNTIME-FIXTURE",
    }


def pair_replay_program():
    return Path(pair.__file__).with_name("heisenberg_level25_four_block_row0_pair_replay.cpp").read_text(encoding="utf-8")


def install_saved_row0(patch):
    install_saved_runner(patch)
    programs = {module._PROGRAM: (name, False) for name, module in MODULES.items()}
    programs[pair_replay_program()] = ("pair", True)

    def saved_run(program, **kwargs):
        name, replay = programs[program]
        assert kwargs["preserve_exact_transcript"] is True
        assert kwargs["timeout_seconds"] == TIMEOUTS[name]
        if name not in ("feasibility", "rank"):
            assert kwargs["runtime_address_space_bytes"] == 67108864
        else:
            assert "runtime_address_space_bytes" not in kwargs
        stdout = native_output(name, replay)
        return {
            "program_sha256": sha(program),
            "transcript_sha256": sha(stdout),
            "transcript": stdout,
            "protocol_version": 1,
            "compiler": "TEST-ONLY-RUNTIME-FIXTURE",
            "compiler_sha256": "0" * 64,
            "compiler_version": "TEST-ONLY-RUNTIME-FIXTURE",
            "binary_sha256": ("2" if replay else "1") * 64,
            **({"runtime_address_space_limit_bytes": 67108864} if name not in ("feasibility", "rank") else {}),
        }

    for name, module in MODULES.items():
        patch.setattr(module, "_run_source_hashed_program", saved_run)
        if name != "pair":
            method = (
                "_python_replay" if name in ("feasibility", "rank") else "_run_limited_python_replay" if name == "kernel" else "_run_replay"
            )
            patch.setattr(module, method, lambda name=name: python_replay(name))


@pytest.fixture(scope="module")
def profiles():
    with pytest.MonkeyPatch.context() as patch:
        install_saved_row0(patch)
        return {
            "unary": feasibility.level25_heisenberg_augmented_four_block_unary_convolution_profile(),
            **{name: factory() for name, factory in FACTORIES.items()},
        }


def expected(name):
    return getattr(
        MODULES[name],
        {
            "feasibility": "_ROW0_FEASIBILITY_EVIDENCE_SHA256",
            "rank": "_ROW0_LABEL_UNARY_RANK_EVIDENCE_SHA256",
            "kernel": "_ROW0_KERNEL_RANK_EVIDENCE_SHA256",
            "pair": "_ROW0_PAIR_EVIDENCE_SHA256",
            "reuse": "_ROW0_PAIR_REUSE_EVIDENCE_SHA256",
            "edge": "_ROW0_EDGE_INDICATOR_EVIDENCE_SHA256",
        }[name],
    )


@pytest.mark.parametrize("name", MODULES)
def test_saved_native_and_independent_results_have_successful_reviewed_scope(profiles, name):
    profile = profiles[name]
    assert profile["schema"].endswith(".v2")
    assert profile["certificate_verified"] is True
    assert profile["evidence_sha256"] == expected(name) == profile_evidence_sha256(profile)
    assert reviewed_profile_matches(profile, expected(name))
    assert certified_receipt_json(profile) == canonical_json(profile)
    assert profile["computation"]["transcript"] == native_output(name)
    assert profile["computation"]["transcript_sha256"] == sha(native_output(name))


@pytest.mark.parametrize("name", ["feasibility", "rank"])
def test_genuine_in_process_python_mathematics_matches_reviewed_fixture(name):
    assert MODULES[name]._python_replay() == python_replay(name)


@pytest.mark.parametrize("name,replay", [(name, False) for name in MODULES] + [("pair", True)])
def test_genuine_compiled_diagnostics_reproduce_full_reviewed_transcripts(tmp_path, name, replay):
    compiler = shutil.which("g++")
    if compiler is None:
        pytest.skip("g++ unavailable for trusted-source mathematical diagnostic")
    source = pair_replay_program() if replay else MODULES[name]._PROGRAM
    binary = tmp_path / "diagnostic"
    subprocess.run(
        [compiler, "-O3", "-std=c++20", "-x", "c++", "-", "-o", str(binary)],
        input=source,
        text=True,
        capture_output=True,
        check=True,
        timeout=120,
    )
    command = [str(binary)]
    if name not in ("feasibility", "rank"):
        command = ["prlimit", "--as=67108864:67108864", "--", *command]
    result = subprocess.run(command, text=True, capture_output=True, check=True, timeout=TIMEOUTS[name])
    assert not result.stderr
    assert result.stdout == native_output(name, replay)
    assert sha(source) == getattr(MODULES[name], "_REVIEWED_REPLAY_PROGRAM_SHA256" if replay else "_REVIEWED_PROGRAM_SHA256")
    assert sha(result.stdout) == getattr(MODULES[name], "_REVIEWED_REPLAY_TRANSCRIPT_SHA256" if replay else "_REVIEWED_TRANSCRIPT_SHA256")


@pytest.mark.parametrize("name", ["kernel", "reuse", "edge"])
def test_genuine_standalone_python_replays_reproduce_exact_output_under_original_limit(name):
    replay = Path(MODULES[name].__file__).with_name(Path(MODULES[name].__file__).stem + "_replay.py")
    interpreter = shutil.which("python3", path="/usr/local/bin:/usr/bin:/bin")
    assert interpreter
    result = subprocess.run(
        ["prlimit", "--as=67108864:67108864", "--", interpreter, "-I", "-S", str(replay)],
        text=True,
        capture_output=True,
        check=True,
        timeout=TIMEOUTS[name],
    )
    assert not result.stderr
    assert result.stdout == python_output(name)


def changed(value):
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, str):
        return ("0" if value[0] != "0" else "1") + value[1:] if len(value) == 64 else value + "unreviewed"
    if isinstance(value, list):
        result = copy.deepcopy(value)
        result[len(result) // 2] = changed(result[len(result) // 2])
        return result
    if value is None:
        return 1
    if isinstance(value, dict):
        return {**value, "unreviewed": True}
    raise AssertionError(value)


def recompute(profiles, monkeypatch, name, *, computation=None, replay=None, parent=None):
    module = MODULES[name]
    monkeypatch.setattr(
        module, "_run_program", lambda: copy.deepcopy(profiles[name]["computation"] if computation is None else computation)
    )
    independent_key = "independent_compiled_replay" if name == "pair" else "independent_python_replay"
    method = "_python_replay" if name in ("feasibility", "rank") else "_run_limited_python_replay" if name == "kernel" else "_run_replay"
    monkeypatch.setattr(module, method, lambda: copy.deepcopy(profiles[name][independent_key] if replay is None else replay))
    if parent is None:
        parent = profiles[PARENTS[name]]
    parent_function = (
        "level25_heisenberg_augmented_four_block_unary_convolution_profile"
        if name == "feasibility"
        else "level25_heisenberg_augmented_four_block_" + SUFFIXES[PARENTS[name]] + "_profile"
    )
    monkeypatch.setattr(module, parent_function, lambda: copy.deepcopy(parent))
    return FACTORIES[name]()


PARSED_CASES = [(name, field) for name, module in MODULES.items() for field in module._parse_transcript(native_output(name))]


@pytest.mark.parametrize("name,field", PARSED_CASES)
def test_every_cached_native_field_is_bound_to_raw_output(profiles, monkeypatch, name, field):
    computation = copy.deepcopy(profiles[name]["computation"])
    computation[field] = changed(computation[field])
    result = recompute(profiles, monkeypatch, name, computation=computation)
    assert result["certificate"]["program_and_transcript_match_reviewed_computation"] is False
    assert result["certificate_verified"] is False
    assert result["decision"]["status"] == "PROFILE_INVALID"


@pytest.mark.parametrize("name,field", PARSED_CASES)
def test_changed_rehashed_raw_native_output_is_not_reviewed_mathematics(profiles, monkeypatch, name, field):
    computation = copy.deepcopy(profiles[name]["computation"])
    stdout = computation["transcript"]
    line = next(line for line in stdout.splitlines() if line.startswith(field + "="))
    values = line.split("=", 1)[1].split(",")
    values[len(values) // 2] = str(int(values[len(values) // 2]) + 1)
    stdout = stdout.replace(line + "\n", field + "=" + ",".join(values) + "\n")
    computation.update(MODULES[name]._parse_transcript(stdout), transcript=stdout, transcript_sha256=sha(stdout))
    result = recompute(profiles, monkeypatch, name, computation=computation)
    assert result["certificate_verified"] is False
    assert result["decision"]["status"] == "PROFILE_INVALID"
    assert result["evidence_sha256"] != expected(name)


@pytest.mark.parametrize("name,field", PARSED_CASES)
def test_duplicate_transcript_fields_are_refused(name, field):
    stdout = native_output(name)
    line = next(line for line in stdout.splitlines() if line.startswith(field + "="))
    with pytest.raises(ScientificToolError, match="duplicate"):
        MODULES[name]._parse_transcript(stdout.replace("REASONING_END\n", line + "\nREASONING_END\n"))


REPLAY_CASES = [(name, field) for name in MODULES if name != "pair" for field in json.loads(python_output(name))]


@pytest.mark.parametrize("name,field", REPLAY_CASES)
def test_every_independent_python_claim_is_bound_to_reviewed_mathematics(profiles, monkeypatch, name, field):
    replay = copy.deepcopy(profiles[name]["independent_python_replay"])
    replay[field] = changed(replay[field])
    result = recompute(profiles, monkeypatch, name, replay=replay)
    assert result["certificate_verified"] is False
    assert result["decision"]["status"] == "PROFILE_INVALID"
    assert result["evidence_sha256"] != expected(name)


@pytest.mark.parametrize("field", pair._parse_transcript(native_output("pair", True)))
def test_every_cached_independent_compiled_pair_claim_is_bound(profiles, monkeypatch, field):
    replay = copy.deepcopy(profiles["pair"]["independent_compiled_replay"])
    replay[field] = changed(replay[field])
    result = recompute(profiles, monkeypatch, "pair", replay=replay)
    assert result["certificate"]["independent_program_and_transcript_match_reviewed_replay"] is False
    assert result["certificate_verified"] is False


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("field", ["compiler", "compiler_sha256", "compiler_version", "binary_sha256"])
def test_compiler_provenance_is_retained_without_invalidating_math(profiles, monkeypatch, name, field):
    computation = copy.deepcopy(profiles[name]["computation"])
    computation[field] = changed(computation[field])
    result = recompute(profiles, monkeypatch, name, computation=computation)
    assert result["certificate_verified"] is True
    assert result["evidence_sha256"] == expected(name)
    assert result["computation"][field] == computation[field]
    assert sha(canonical_json(result)) != sha(canonical_json(profiles[name]))


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("mutation", ["failed_certificate", "copied_seal", "rehashed_scope", "runtime_only"])
def test_parent_approval_checks_fresh_math_not_archived_raw_receipt(profiles, monkeypatch, name, mutation):
    parent = copy.deepcopy(profiles[PARENTS[name]])
    if mutation == "failed_certificate":
        parent["certificate_verified"] = False
    elif mutation in ("copied_seal", "rehashed_scope"):
        parent["request"]["unreviewed"] = True
        if mutation == "rehashed_scope":
            parent["evidence_sha256"] = profile_evidence_sha256(parent)
    else:
        parent["computation"]["compiler"] = "/another/compiler"
    result = recompute(profiles, monkeypatch, name, parent=parent)
    verified = mutation == "runtime_only"
    assert result["certificate_verified"] is verified
    if verified:
        assert result["evidence_sha256"] == expected(name)
        assert result[PARENT_KEYS[name] + "_receipt_sha256"] != profiles[name][PARENT_KEYS[name] + "_receipt_sha256"]
    else:
        assert result["decision"]["status"] == "PROFILE_INVALID"
        if name == "rank":
            assert result["rational_rank_conclusions"]["nonzero_bucket_orbit_ranks_are_exact"] is False
            assert result["rational_rank_conclusions"]["zero_bucket_lower_bound"] is None
        if name == "kernel":
            assert result["characteristic_zero_rank_conclusions"]["nonzero_character_orbit_ranks_are_exact"] is False
            assert result["characteristic_zero_rank_conclusions"]["zero_character_rank"] is None


@pytest.mark.parametrize("name", MODULES)
def test_each_invocation_obtains_a_new_prerequisite(profiles, monkeypatch, name):
    calls = []
    parent_name = PARENTS[name]
    parent = copy.deepcopy(profiles[parent_name])
    recompute(profiles, monkeypatch, name, parent=parent)
    parent_function = (
        "level25_heisenberg_augmented_four_block_unary_convolution_profile"
        if name == "feasibility"
        else "level25_heisenberg_augmented_four_block_" + SUFFIXES[parent_name] + "_profile"
    )

    def fresh_parent():
        calls.append(True)
        return copy.deepcopy(parent)

    monkeypatch.setattr(MODULES[name], parent_function, fresh_parent)
    assert FACTORIES[name]()["certificate_verified"] is True
    assert FACTORIES[name]()["certificate_verified"] is True
    assert len(calls) == 2


@pytest.mark.parametrize(
    "name,engine",
    [(name, "computation") for name in ("kernel", "pair", "reuse", "edge")]
    + [
        (name, "independent_compiled_replay" if name == "pair" else "independent_python_replay")
        for name in ("kernel", "pair", "reuse", "edge")
    ],
)
@pytest.mark.parametrize("limit", [0, 33554432, 67108865, 134217728])
def test_declared_hard_address_space_limits_cannot_be_weakened(profiles, monkeypatch, name, engine, limit):
    value = copy.deepcopy(profiles[name][engine])
    value["runtime_address_space_limit_bytes"] = limit
    result = recompute(profiles, monkeypatch, name, **({"computation": value} if engine == "computation" else {"replay": value}))
    assert result["certificate_verified"] is False
    assert result["decision"]["status"] == "PROFILE_INVALID"


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("mutation", ["certificate", "scope", "raw_body", "raw_body_rehashed", "missing_computation", "unknown_field"])
def test_failed_or_self_rehashed_evidence_cannot_write_a_report(profiles, monkeypatch, tmp_path, name, mutation):
    from test_local_science_tools import _registry

    profile = copy.deepcopy(profiles[name])
    if mutation == "certificate":
        profile["certificate_verified"] = False
    elif mutation == "scope":
        profile["not_certified"] = []
    elif mutation in ("raw_body", "raw_body_rehashed"):
        profile["computation"]["transcript"] += "\n"
        if mutation == "raw_body_rehashed":
            profile["computation"]["transcript_sha256"] = sha(profile["computation"]["transcript"])
    elif mutation == "missing_computation":
        del profile["computation"]["program_sha256"]
    else:
        profile["unreviewed"] = True
    if mutation != "missing_computation":
        profile["evidence_sha256"] = profile_evidence_sha256(profile)
    monkeypatch.setattr(project_tools, "level25_heisenberg_augmented_four_block_" + SUFFIXES[name] + "_profile", lambda: profile)
    with pytest.raises(ScientificToolError):
        _registry(tmp_path).invoke(
            "level25_heisenberg_augmented_four_block_" + SUFFIXES[name] + "_report",
            {"path": "research/refused.md", "terminal_marker": "END"},
        )
    assert not (tmp_path / "artifacts/research/refused.md").exists()


@pytest.mark.parametrize("name", ["kernel", "pair", "reuse", "edge"])
@pytest.mark.parametrize("rehashed", [False, True])
def test_copied_success_and_changed_independent_raw_body_cannot_write(profiles, name, rehashed):
    profile = copy.deepcopy(profiles[name])
    key = "independent_compiled_replay" if name == "pair" else "independent_python_replay"
    profile[key]["transcript"] += "\n"
    if rehashed:
        profile[key]["transcript_sha256"] = sha(profile[key]["transcript"])
    profile["evidence_sha256"] = profile_evidence_sha256(profile)
    with pytest.raises(ScientificToolError, match="report not written"):
        certified_receipt_json(profile)


@pytest.mark.parametrize("name", MODULES)
def test_valid_report_retains_raw_receipt_and_mathematical_identity(profiles, monkeypatch, tmp_path, name):
    from test_local_science_tools import _registry

    monkeypatch.setattr(
        project_tools, "level25_heisenberg_augmented_four_block_" + SUFFIXES[name] + "_profile", lambda: copy.deepcopy(profiles[name])
    )
    result = _registry(tmp_path).invoke(
        "level25_heisenberg_augmented_four_block_" + SUFFIXES[name] + "_report",
        {"path": "research/approved.md", "terminal_marker": "ROW0_END"},
    )
    text = (tmp_path / "artifacts/research/approved.md").read_text(encoding="utf-8")
    assert result["certificate_verified"] is True
    assert result["evidence_sha256"] == expected(name)
    assert result["receipt_sha256"] == sha(canonical_json(profiles[name]))
    assert result["evidence_sha256"] in text and result["receipt_sha256"] in text
    assert json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0]) == profiles[name]
    assert text.endswith("ROW0_END\n")


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("kind", ["profile", "report"])
def test_existing_production_assertions_with_explicit_saved_runtime(tmp_path, monkeypatch, name, kind):
    import test_local_science_tools as tests

    install_saved_row0(monkeypatch)
    test = getattr(tests, "test_level25_heisenberg_augmented_four_block_" + SUFFIXES[name] + "_" + kind)
    if kind == "profile":
        test()
    else:
        test(tmp_path)


@pytest.mark.parametrize("name", ["feasibility", "rank"])
def test_in_process_replays_do_not_claim_nonexistent_hard_limits(profiles, name):
    limits = profiles[name]["execution_limits"]
    assert limits["primary_hard_address_space_bytes"] is None
    assert limits["primary_wall_seconds"] == TIMEOUTS[name]
    assert limits["independent_python_replay"] == "in_process_without_a_separate_hard_memory_or_wall_limit"


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("field", ["program_sha256", "transcript_sha256", "transcript"])
def test_native_identity_fields_cannot_be_copied_without_exact_evidence(profiles, monkeypatch, name, field):
    computation = copy.deepcopy(profiles[name]["computation"])
    computation[field] = computation[field] + "\n" if field == "transcript" else changed(computation[field])
    result = recompute(profiles, monkeypatch, name, computation=computation)
    assert result["certificate_verified"] is False
    assert result["decision"]["status"] == "PROFILE_INVALID"


@pytest.mark.parametrize("name", ["kernel", "pair", "reuse", "edge"])
@pytest.mark.parametrize("field", ["program_sha256", "transcript_sha256", "transcript"])
def test_independent_execution_identity_fields_cannot_be_copied(profiles, monkeypatch, name, field):
    replay = copy.deepcopy(profiles[name]["independent_compiled_replay" if name == "pair" else "independent_python_replay"])
    replay[field] = replay[field] + "\n" if field == "transcript" else changed(replay[field])
    result = recompute(profiles, monkeypatch, name, replay=replay)
    assert result["certificate_verified"] is False
    assert result["decision"]["status"] == "PROFILE_INVALID"


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("key", ["computation", "request", "certificate", "engineering_caps", "not_certified", "decision"])
def test_all_profile_claims_remain_in_reviewed_scope(profiles, name, key):
    profile = copy.deepcopy(profiles[name])
    profile[key] = changed(profile[key])
    profile["evidence_sha256"] = profile_evidence_sha256(profile)
    assert reviewed_profile_matches(profile, expected(name)) is False
    with pytest.raises(ScientificToolError, match="report not written"):
        certified_receipt_json(profile)


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("mutation", ["frame", "end", "unknown_field", "nonintegral_field", "no_equals", "missing_field"])
def test_malformed_native_transcripts_are_refused(name, mutation):
    lines = native_output(name).splitlines()
    if mutation == "frame":
        lines[0] = "UNREVIEWED_FRAME"
    elif mutation == "end":
        lines[-1] = "UNREVIEWED_END"
    elif mutation == "unknown_field":
        lines.insert(-1, "unreviewed=1")
    elif mutation == "nonintegral_field":
        lines[1] = lines[1].split("=", 1)[0] + "=invalid"
    elif mutation == "no_equals":
        lines[1] = "invalid"
    else:
        del lines[1]
    with pytest.raises(ScientificToolError):
        MODULES[name]._parse_transcript("\n".join(lines) + "\n")


@pytest.mark.parametrize("name", ["kernel", "reuse", "edge"])
@pytest.mark.parametrize("mutation", ["none", "invalid_json", "non_object", "oversized", "stderr", "returncode", "timeout", "unavailable"])
def test_standalone_replay_wrapper_preserves_raw_output_and_refuses_failed_processes(monkeypatch, name, mutation):
    module = MODULES[name]
    replay_path = Path(module.__file__).with_name(Path(module.__file__).stem + "_replay.py")
    observed = []

    def wrapped(command, **kwargs):
        assert kwargs == {"ro": [replay_path], "network": False}
        observed.append(command)
        return command

    def run(command, **kwargs):
        assert command == observed[0]
        assert kwargs["timeout"] == TIMEOUTS[name]
        assert kwargs["check"] is False
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        if mutation == "timeout":
            raise subprocess.TimeoutExpired(command, TIMEOUTS[name])
        if mutation == "unavailable":
            raise OSError("TEST-ONLY-REPLAY-UNAVAILABLE")
        stdout = python_output(name)
        if mutation == "invalid_json":
            stdout = "{"
        elif mutation == "non_object":
            stdout = "[]\n"
        elif mutation == "oversized":
            stdout = " " * (2 * 1024**2 + 1)
        return subprocess.CompletedProcess(
            command, int(mutation == "returncode"), stdout=stdout, stderr="TEST-ONLY-REPLAY-FAILURE" if mutation == "stderr" else ""
        )

    monkeypatch.setattr(module.sandbox, "wrap", wrapped)
    monkeypatch.setattr(module.subprocess, "run", run)
    method = module._run_limited_python_replay if name == "kernel" else module._run_replay
    if mutation != "none":
        with pytest.raises(ScientificToolError):
            method()
    else:
        result = method()
        assert result["transcript"] == python_output(name)
        assert result["transcript_sha256"] == sha(result["transcript"])
        assert result["program_sha256"] == sha(replay_path.read_text())
        assert result["runtime_address_space_limit_bytes"] == 67108864
    assert observed
    assert any("67108864" in str(part) for part in observed[0])
    assert "-I" in observed[0] and "-S" in observed[0]
