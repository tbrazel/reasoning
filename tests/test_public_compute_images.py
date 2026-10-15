"""Public image recipes/installed metadata and synthetic OCI corruption fixtures.

No test in this file executes Docker or claims actual installed CAS capability.
"""

import copy
import fnmatch
import gzip
import importlib.util
import io
import json
import re
import tarfile
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from reasoning_ledger.image_release import (
    CONFIG,
    INDEX,
    MANIFEST,
    SOURCE_PATHS,
    OCIArchive,
    build_image,
    digest,
    inspect_image,
    json_body,
    main,
    validate_recipe,
    verify_builder,
)
from reasoning_ledger.server.compute_catalog import DEFAULT_RESOURCES, load_catalog
from reasoning_ledger.server.compute_docker import ENVIRONMENT, verify_evidence

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "contracts/executor/v1alpha1/image-capabilities.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_bytes())
SPEC = importlib.util.spec_from_file_location("research_image_metadata", ROOT / "images/research-cpu/metadata.py")
METADATA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(METADATA)
LOCK = validate_recipe(ROOT)
REVISION = "b" * 40
TIMESTAMP = "2026-10-01T00:00:00Z"


def image_ci_trigger_patterns():
    workflow = (ROOT / ".github/workflows/compute-images.yml").read_text()
    paths = workflow.split("  pull_request:\n", 1)[1].split("\npermissions:", 1)[0]
    return re.findall(r"^\s+- '([^']+)'$", paths, re.MULTILINE)


def test_image_ci_covers_every_embedded_recipe_input_and_execution_tool():
    patterns = image_ci_trigger_patterns()
    inputs = [path + "/example-input" if (ROOT / path).is_dir() else path for path in SOURCE_PATHS]
    inputs += [
        "images/lean-mathlib/install.py", "src/reasoning_ledger/image_release.py",
        "src/reasoning_ledger/image_context.py", "src/reasoning_ledger/image_ci.py",
        "src/reasoning_ledger/image_acceptance.py", "src/reasoning_ledger/server/compute_docker.py",
        "src/reasoning_ledger/server/compute_worker.py", ".github/ci/apt-network.conf",
        ".github/workflows/compute-images.yml",
    ]
    assert all(any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns) for path in inputs)


@pytest.mark.parametrize("path", [
    "src/reasoning_ledger/image_spaces.py", "src/reasoning_ledger/image_review.py",
    "src/reasoning_ledger/image_sources.py", "src/reasoning_ledger/image_provenance.py",
    "src/reasoning_ledger/image_publication.py", "src/reasoning_ledger/compute_publish.py",
    "images/publication/review-key.pem", "images/compute-images.json",
    ".github/workflows/compute-publish.yml", ".github/workflows/compute-combine.yml",
])
def test_publication_only_changes_do_not_rebuild_compute_binaries(path):
    assert not any(fnmatch.fnmatchcase(path, pattern) for pattern in image_ci_trigger_patterns())


def encode(body):
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def documents():
    packages = [
        {"kind": "deb", "name": "bash", "version": "fixture", "source": "bash", "source_version": "fixture"},
        {"kind": "python", "name": "sage", "version": "fixture"},
    ]
    return METADATA.documents(LOCK, LOCK["versions"], packages, REVISION, TIMESTAMP)


def tar(entries):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w") as archive:
        for path, body in entries:
            info = path if isinstance(path, tarfile.TarInfo) else tarfile.TarInfo(path)
            if body is not None:
                info.size = len(body)
            archive.addfile(info, io.BytesIO(body) if body is not None else None)
    return result.getvalue()


def write_oci(
    tmp_path,
    *,
    layers=None,
    change_config=None,
    change_caps=None,
    change_inventory=None,
    indexed=True,
    extra_root=None,
    change_descriptor=None,
    change_blob=None,
):
    capabilities, inventory = documents()
    if change_caps:
        change_caps(capabilities)
    if change_inventory:
        change_inventory(inventory)
    if layers is None:
        layers = [
            tar([("etc/reasoning/capabilities.json", encode(capabilities)), ("usr/share/reasoning/sbom.spdx.json", encode(inventory))])
        ]
    blobs = {}

    def blob(body, media_type, **extras):
        identifier = digest(body)
        blobs["blobs/sha256/" + identifier[7:]] = body
        return {"digest": identifier, "size": len(body), "mediaType": media_type, **extras}

    config = {
        "os": "linux",
        "architecture": "amd64",
        "rootfs": {"type": "layers", "diff_ids": [digest(layer) for layer in layers]},
        "config": {
            "User": "10002:10002",
            "WorkingDir": "/",
            "Cmd": ["/bin/sleep", "60"],
            "Entrypoint": [],
            "Env": [f"{key}={value}" for key, value in ENVIRONMENT.items()],
            "Labels": {"org.opencontainers.image.revision": REVISION, "org.reasoning.executor.contract": "1.0.0-alpha.1"},
        },
    }
    if change_config:
        change_config(config)
    manifest = {
        "schemaVersion": 2,
        "mediaType": MANIFEST,
        "config": blob(encode(config), CONFIG),
        "layers": [blob(gzip.compress(layer, mtime=0), "application/vnd.oci.image.layer.v1.tar+gzip") for layer in layers],
    }
    runtime = blob(encode(manifest), MANIFEST, platform={"os": "linux", "architecture": "amd64"})
    release = blob(encode({"schemaVersion": 2, "mediaType": INDEX, "manifests": [runtime]}), INDEX) if indexed else runtime
    if change_descriptor:
        change_descriptor(release)
    if change_blob:
        change_blob(blobs, runtime, manifest)
    entries = [
        ("oci-layout", encode({"imageLayoutVersion": "1.0.0"})),
        ("index.json", encode({"schemaVersion": 2, "manifests": [release]})),
    ]
    entries.extend(sorted(blobs.items()))
    if extra_root:
        entries.extend(extra_root)
    path = tmp_path / "image.tar"
    path.write_bytes(tar(entries))
    return path, release, runtime


def test_recipe_pins_actual_public_stack_and_distinct_worker_users():
    assert LOCK["platform"] == "linux/amd64"
    recipe = (ROOT / "images/research-cpu/Dockerfile").read_text()
    assert "USER 10002:10002" in recipe and "--uid 10001" in recipe
    assert not re.search(r"\bHOME=", recipe) and "apt-get" not in recipe
    assert "pip install --no-deps --no-cache-dir" in recipe
    assert recipe.count("--no-log-init") == 2 and recipe.count("chage --lastday") == 2
    assert not any(word in recipe.lower() for word in ("ssh=", "secret="))
    assert "COPY --from=noether-source . /work/noether" in recipe
    assert "--locked" in recipe and "RUN_NATIVE_TESTS=0" in recipe
    assert LOCK["noether"]["commit"] in recipe
    assert any("Noether uses its pinned default CPU profile" in line for line in documents()[0]["restrictions"])


def test_noether_builder_releases_scratch_in_checked_wheel_layer():
    recipe = (ROOT / "images/research-cpu/Dockerfile").read_text()
    step = recipe.split("RUN test \"$SOURCE_DATE_EPOCH\" -gt 0", 1)[1].split("\n\nFROM ", 1)[0]
    assert "\nRUN " not in step
    assert step.index("scripts/check-wheel.sh") < step.index("rm -rf /work/noether/target")
    for path in ("/work/noether/.venv", "/work/noether/.deps", "/usr/local/cargo/registry",
                 "/usr/local/cargo/git", "/root/.cache/uv"):
        assert path in step[step.index("rm -rf /work/noether/target"):]
    assert "/wheelhouse" not in step.split("rm -rf", 1)[1].split("&&", 1)[0]


def test_observed_metadata_is_deterministic_schema_valid_and_not_a_license_claim():
    capabilities, sbom = documents()
    assert documents() == (capabilities, sbom)
    Draft202012Validator(SCHEMA, format_checker=FormatChecker()).validate(capabilities)
    assert capabilities["paths"]["output"] == "/work/out"
    assert {point for tool in capabilities["tools"] for point in tool["entry_points"]} == {
        "python3", "sage", "gap", "gp", "julia", "lean", "/opt/reasoning/noether_worker.py",
    }
    assert len(sbom["packages"]) == 7
    assert all(package["licenseConcluded"] == "NOASSERTION" for package in sbom["packages"])
    assert "not a complete filesystem scan" in sbom["documentComment"]


def test_rust_runtime_notices_are_exact_release_bytes_and_do_not_claim_static_link_closure(tmp_path):
    import hashlib

    lock = copy.deepcopy(LOCK)
    runtime = lock["noether"]["rust_runtime"]
    for name in runtime["notices"]:
        body = ("fixed fixture notice " + name).encode()
        (tmp_path / name).write_bytes(body)
        runtime["notices"][name] = hashlib.sha256(body).hexdigest()
    METADATA.verify_rust_notices(lock, tmp_path)
    _, inventory = METADATA.documents(lock, lock["versions"], [], REVISION, TIMESTAMP)
    item = next(package for package in inventory["packages"] if package["name"] == "rust-standard-library-runtime")
    detail = json.loads(item["comment"])
    assert detail["kind"] == "compiler-runtime" and detail["closure_verified"] is False
    assert detail["compiler_image"] == lock["noether"]["rust_image"]
    assert detail["version"] == "1.94.0" and item["licenseConcluded"] == "NOASSERTION"
    (tmp_path / "LICENSE-MIT").write_bytes(b"incorrect release")
    with pytest.raises(ValueError, match="SHA256 differs"):
        METADATA.verify_rust_notices(lock, tmp_path)
    (tmp_path / "LICENSE-MIT").unlink()
    with pytest.raises(ValueError, match="absent, linked or oversized"):
        METADATA.verify_rust_notices(lock, tmp_path)


def test_rust_runtime_inputs_bind_same_source_delivery_and_pinned_compiler_notice_paths():
    runtime = LOCK["noether"]["rust_runtime"]
    source_inputs = json.loads((ROOT / "images/source-inputs.json").read_bytes())
    artifacts = {artifact["id"]: artifact for artifact in source_inputs["artifacts"]}
    for field, identity in (("compiler_distribution", "rust-compiler-notices"), ("source_distribution", "rust-standard-library-source")):
        assert runtime[field]["sha256"] == artifacts[identity]["sha256"]
        assert runtime[field]["url"] == artifacts[identity]["url"]
    recipe = (ROOT / "images/research-cpu/Dockerfile").read_text()
    source = "/usr/local/rustup/toolchains/1.94.0-x86_64-unknown-linux-gnu/share/doc/rust/COPYRIGHT-library.html"
    assert "COPY --from=rust-toolchain " + source in recipe
    assert len(runtime["notices"]) == 3 and runtime["notice_directory"] == "/usr/share/reasoning/licenses/rust-1.94.0"


@pytest.mark.parametrize("field", ["notice_directory", "compiler_distribution", "source_distribution", "notices"])
def test_recipe_refuses_missing_rust_runtime_review_inputs(tmp_path, field):
    directory = tmp_path / "images/research-cpu"
    directory.mkdir(parents=True)
    lock = copy.deepcopy(LOCK)
    lock["noether"]["rust_runtime"].pop(field)
    (directory / "inputs.json").write_text(json.dumps(lock))
    (directory / "Dockerfile").write_text((ROOT / "images/research-cpu/Dockerfile").read_text())
    with pytest.raises(ValueError, match="Rust runtime"):
        validate_recipe(tmp_path)


def test_noether_inventory_retains_native_and_actual_rust_closure_without_license_conclusion(tmp_path):
    from types import SimpleNamespace

    sboms = tmp_path / "noether.dist-info/sboms"
    sboms.mkdir(parents=True)
    names = ["native-libraries.cyclonedx.json", "noether-python.cyclonedx.json"]
    native = [{"name": name, "version": "fixture", "bom-ref": "pkg:generic/" + name} for name in ("FLINT", "GMP", "MPFR")]
    rust = [{"name": "noether", "version": "0.3.1", "bom-ref": "urn:noether:crate:noether@0.3.1", "licenses": [{"expression": "MIT"}]}]
    for name, components in zip(names, (native, rust), strict=True):
        (sboms / name).write_text(json.dumps({"bomFormat": "CycloneDX", "components": components}))
    distribution = SimpleNamespace(
        files=[Path("noether.dist-info/sboms") / name for name in names], locate_file=lambda file: tmp_path / file,
    )
    packages = METADATA.noether_inventory(distribution)
    assert len(packages) == 4
    assert {record["kind"] for record in packages} == {"noether-native", "noether-rust"}
    _, inventory = METADATA.documents(LOCK, LOCK["versions"], packages, REVISION, TIMESTAMP)
    assert all(record["licenseConcluded"] == "NOASSERTION" for record in inventory["packages"])
    distribution.files.pop()
    with pytest.raises(ValueError, match="required installed dependency inventory"):
        METADATA.noether_inventory(distribution)


def test_noether_inventory_refuses_incomplete_native_closure(tmp_path):
    from types import SimpleNamespace

    path = Path("noether.dist-info/sboms/native-libraries.cyclonedx.json")
    (tmp_path / path).parent.mkdir(parents=True)
    (tmp_path / path).write_text(json.dumps({"bomFormat": "CycloneDX", "components": [
        {"name": "GMP", "version": "fixture", "bom-ref": "pkg:generic/gmp"},
    ]}))
    distribution = SimpleNamespace(files=[path], locate_file=lambda file: tmp_path / file)
    with pytest.raises(ValueError, match="does not cover"):
        METADATA.noether_inventory(distribution)


@pytest.mark.parametrize("language", LOCK["versions"])
def test_generation_refuses_unobserved_or_mismatched_installed_versions(language):
    versions = LOCK["versions"].copy()
    versions[language] = "wrong"
    with pytest.raises(ValueError, match="installed runtimes"):
        METADATA.documents(LOCK, versions, [], REVISION, TIMESTAMP)


@pytest.mark.parametrize("revision,timestamp", [("short", TIMESTAMP), (REVISION, "bad"), (REVISION, "2026-10-01T00:00:00+03:00")])
def test_metadata_refuses_unpinned_revision_or_nonutc_time(revision, timestamp):
    with pytest.raises(ValueError):
        METADATA.documents(LOCK, LOCK["versions"], [], revision, timestamp)


@pytest.mark.parametrize("indexed", [False, True])
def test_oci_inspection_binds_publishable_digest_and_actual_embedded_files_without_execution_claims(tmp_path, indexed):
    path, release, runtime = write_oci(tmp_path, indexed=indexed)
    report, evidence = inspect_image(path, SCHEMA, "registry.example.org/reasoning/research-cpu")
    assert report["image"]["reference"].endswith("@" + release["digest"])
    assert report["platform_manifest_digest"] == runtime["digest"]
    assert report["image"]["capabilities_digest"] == digest(evidence["etc/reasoning/capabilities.json"])
    assert report["execution_verified"] is report["license_source_audit_verified"] is report["signature_verified"] is False
    assert (
        main(
            [
                "inspect",
                "--archive",
                str(path),
                "--schema",
                str(SCHEMA_PATH),
                "--repository",
                "registry.example.org/cpu",
                "--output",
                str(tmp_path / "checked"),
                "--write-approval-catalog",
            ]
        )
        == 0
    )
    images = load_catalog(tmp_path / "checked/compute-images.json")
    for language in images["research-cpu"].languages:
        verify_evidence(
            evidence["etc/reasoning/capabilities.json"],
            evidence["usr/share/reasoning/sbom.spdx.json"],
            images["research-cpu"],
            DEFAULT_RESOURCES,
            language,
        )


def test_inspection_does_not_implicitly_approve_and_never_overwrites(tmp_path):
    path, _, _ = write_oci(tmp_path)
    args = [
        "inspect",
        "--archive",
        str(path),
        "--schema",
        str(SCHEMA_PATH),
        "--repository",
        "registry.example.org/cpu",
        "--output",
        str(tmp_path / "checked"),
    ]
    assert main(args) == 0
    assert not (tmp_path / "checked/compute-images.json").exists()
    with pytest.raises(SystemExit):
        main(args)


@pytest.mark.parametrize(
    "field,value",
    [("User", "root"), ("Volumes", {"/tmp": {}}), ("WorkingDir", "/work"), ("Entrypoint", ["sh"]), ("Cmd", ["sleep", "60"]), ("Env", [])],
)
def test_oci_config_drift_is_rejected(tmp_path, field, value):
    path, _, _ = write_oci(tmp_path, change_config=lambda config: config["config"].update({field: value}))
    with pytest.raises(ValueError, match="image config"):
        inspect_image(path, SCHEMA, "registry.example.org/cpu")


@pytest.mark.parametrize(
    "change",
    [
        lambda config: config.update(architecture="arm64"),
        lambda config: config["rootfs"].update(diff_ids=["sha256:" + "a" * 64]),
        lambda config: config["rootfs"].update(type="wrong"),
        lambda config: config["config"]["Labels"].update({"org.opencontainers.image.revision": "a" * 40}),
    ],
)
def test_image_platform_rootfs_and_revision_are_bound_to_evidence(tmp_path, change):
    path, _, _ = write_oci(tmp_path, change_config=change)
    with pytest.raises(ValueError):
        inspect_image(path, SCHEMA, "registry.example.org/cpu")


@pytest.mark.parametrize(
    "change",
    [
        lambda caps: caps.update(schema_id="old.executor.image-capabilities"),
        lambda caps: caps.update(extra="uncontracted"),
        lambda caps: caps.update(build_timestamp="not a timestamp"),
    ],
)
def test_embedded_capabilities_must_pass_complete_current_schema(tmp_path, change):
    path, _, _ = write_oci(tmp_path, change_caps=change)
    with pytest.raises((ValidationError, ValueError)):
        inspect_image(path, SCHEMA, "registry.example.org/cpu")


@pytest.mark.parametrize(
    "descriptor_change",
    [lambda value: value.update(size=True), lambda value: value.update(size=1), lambda value: value.update(digest="sha256:" + "f" * 64)],
)
def test_descriptor_size_and_hash_are_verified(tmp_path, descriptor_change):
    path, _, _ = write_oci(tmp_path, change_descriptor=descriptor_change)
    with pytest.raises(ValueError):
        inspect_image(path, SCHEMA, "registry.example.org/cpu")


@pytest.mark.parametrize("which", ["manifest", "config", "layer"])
def test_every_selected_oci_blob_is_hashed(tmp_path, which):
    def corrupt(blobs, runtime, manifest):
        descriptor = {"manifest": runtime, "config": manifest["config"], "layer": manifest["layers"][0]}[which]
        name = "blobs/sha256/" + descriptor["digest"][7:]
        blobs[name] = b"x" + blobs[name][1:]

    path, _, _ = write_oci(tmp_path, change_blob=corrupt)
    with pytest.raises(ValueError, match="digest"):
        inspect_image(path, SCHEMA, "registry.example.org/cpu")


@pytest.mark.parametrize("name", ["../escape", "/absolute", "unexpected", "oci-layout"])
def test_oci_root_refuses_unsafe_duplicate_and_unknown_files(tmp_path, name):
    path, _, _ = write_oci(tmp_path, extra_root=[(name, b"bad")])
    with pytest.raises(ValueError):
        OCIArchive(path)


@pytest.mark.parametrize("target", ["etc/reasoning/capabilities.json", "etc/reasoning", "etc"])
def test_evidence_symlinks_and_ancestor_links_are_not_followed(tmp_path, target):
    capabilities, sbom = documents()
    link = tarfile.TarInfo(target)
    link.type, link.linkname = tarfile.SYMTYPE, "/elsewhere"
    layers = [
        tar([("etc/reasoning/capabilities.json", encode(capabilities)), ("usr/share/reasoning/sbom.spdx.json", encode(sbom)), (link, None)])
    ]
    path, _, _ = write_oci(tmp_path, layers=layers)
    with pytest.raises(ValueError):
        inspect_image(path, SCHEMA, "registry.example.org/cpu")


@pytest.mark.parametrize("whiteout", ["etc/reasoning/.wh.capabilities.json", "etc/.wh.reasoning", "etc/.wh..wh..opq", ".wh..wh..opq"])
def test_whiteouts_remove_lower_layer_evidence_including_ancestors(tmp_path, whiteout):
    capabilities, sbom = documents()
    layers = [
        tar([("etc/reasoning/capabilities.json", encode(capabilities)), ("usr/share/reasoning/sbom.spdx.json", encode(sbom))]),
        tar([(whiteout, b"")]),
    ]
    path, _, _ = write_oci(tmp_path, layers=layers)
    with pytest.raises(ValueError, match="lacks"):
        inspect_image(path, SCHEMA, "registry.example.org/cpu")


def test_whiteouts_only_affect_lower_layers_not_same_layer_new_files(tmp_path):
    capabilities, sbom = documents()
    layers = [
        tar([("etc/reasoning/capabilities.json", encode(capabilities))]),
        tar(
            [
                ("etc/reasoning/capabilities.json", encode(capabilities)),
                ("etc/.wh..wh..opq", b""),
                ("usr/share/reasoning/sbom.spdx.json", encode(sbom)),
            ]
        ),
    ]
    path, _, _ = write_oci(tmp_path, layers=layers)
    inspect_image(path, SCHEMA, "registry.example.org/cpu")


def test_large_extended_header_refused_before_payload_allocation(tmp_path):
    header = tarfile.TarInfo("extended")
    header.type, header.size = tarfile.XHDTYPE, 65537
    path = tmp_path / "large.tar"
    path.write_bytes(header.tobuf() + bytes(1024))
    with pytest.raises(ValueError, match="extended header"):
        OCIArchive(path)


@pytest.mark.parametrize("body", [b'{"key":1,"key":2}', b'{"key":NaN}', b"[]"])
def test_evidence_json_refuses_duplicates_nonfinite_and_nonobjects(body):
    with pytest.raises(ValueError):
        json_body(body)


@pytest.mark.parametrize(
    "change",
    [
        lambda inventory: inventory.update(packages="not a package array"),
        lambda inventory: inventory["packages"].append(inventory["packages"][0]),
        lambda inventory: inventory["packages"][0].update(name=""),
        lambda inventory: inventory["relationships"][0].update(relatedSpdxElement="SPDXRef-missing"),
    ],
)
def test_syntactically_valid_but_incomplete_or_unlinked_inventory_is_refused(tmp_path, change):
    path, _, _ = write_oci(tmp_path, change_inventory=change)
    with pytest.raises(ValueError, match="SPDX"):
        inspect_image(path, SCHEMA, "registry.example.org/cpu")


def test_locked_buildkit_local_single_node_is_required():
    text = (
        "Name: reasoning-images\nDriver: docker-container\nNodes:\nName: reasoning-images0\n"
        f"Endpoint: unix:///var/run/docker.sock\nStatus: running\nBuildKit version: {LOCK['buildkit_version']}\n"
    )
    assert verify_builder(text, "reasoning-images", LOCK) == "buildx_buildkit_reasoning-images0"
    for old, new in (
        ("running", "stopped"),
        (LOCK["buildkit_version"], "v0.1.0"),
        ("unix:///var/run/docker.sock", "ssh://remote"),
        ("docker-container", "docker"),
    ):
        with pytest.raises(ValueError):
            verify_builder(text.replace(old, new), "reasoning-images", LOCK)
    with pytest.raises(ValueError):
        verify_builder(text + "Name: another-node\n", "reasoning-images", LOCK)


def test_dirty_source_fails_before_any_docker_or_output_creation(monkeypatch, tmp_path):
    calls = []

    def command(args, **kwargs):
        calls.append(args)
        return b" M source.py\n"

    monkeypatch.setattr("reasoning_ledger.image_release.command", command)
    with pytest.raises(ValueError, match="clean committed"):
        build_image(ROOT, tmp_path / "build", "reasoning-images")
    assert calls == [["git", "status", "--porcelain", "--untracked-files=all"]]
    assert not (tmp_path / "build").exists()


def test_compare_requires_original_build_identities_and_refuses_drift(tmp_path):
    path, _, _ = write_oci(tmp_path)
    report, _ = inspect_image(path, SCHEMA, "localhost/reasoning/research-cpu")
    report.update(build_tools={"fixture": "not actual BuildKit"}, source_date_epoch=1)
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    first.write_bytes(encode(report))
    second.write_bytes(encode(report))
    assert main(["compare", str(first), str(second)]) == 0
    changed = copy.deepcopy(report)
    changed["layer_digests"] = ["sha256:" + "f" * 64]
    second.write_bytes(encode(changed))
    with pytest.raises(SystemExit):
        main(["compare", str(first), str(second)])
    first.write_bytes(b"{}")
    second.write_bytes(b"{}")
    with pytest.raises(SystemExit):
        main(["compare", str(first), str(second)])


def test_candidate_build_stages_only_committed_inputs_and_checks_buildx_digest_metadata(monkeypatch, tmp_path):
    calls = []
    node = (
        "Name: reasoning-images\nDriver: docker-container\nNodes:\nName: reasoning-images0\n"
        f"Endpoint: unix:///var/run/docker.sock\nStatus: running\nBuildKit version: {LOCK['buildkit_version']}\n"
    )

    def command(args, **kwargs):
        calls.append(args)
        if args[:2] == ["git", "status"]:
            return b""
        if args[:2] == ["git", "rev-parse"]:
            return REVISION.encode()
        if args[:2] == ["git", "show"]:
            assert args[-1] == REVISION
            return b"1790812800"
        if args[-2:] == ["buildx", "version"]:
            return LOCK["buildx_version"].encode()
        if "buildx" in args and "inspect" in args:
            assert "--format" not in args
            return node.encode()
        assert args[-1] == "buildx_buildkit_reasoning-images0"
        return LOCK["buildkit_image"].encode()

    output = tmp_path / "candidate"

    def run(args, **kwargs):
        if args[:2] == ["git", "archive"]:
            assert args[2] == REVISION
            entries = []
            for name in args[3:]:
                path = ROOT / name
                if path.is_dir():
                    entries.extend(
                        (file.relative_to(ROOT).as_posix(), file.read_bytes())
                        for file in path.rglob("*")
                        if file.is_file() and "__pycache__" not in file.parts
                    )
                else:
                    entries.append((name, path.read_bytes()))
            kwargs["stdout"].write(tar(entries))
            return
        assert args[:3] == ["docker", "--host", "unix:///var/run/docker.sock"]
        assert {"--no-cache", "--provenance=false", "--sbom=false"} <= set(args)
        assert not {"--push", "--secret", "--ssh", "--load"} & set(args)
        context = Path(args[-1])
        assert not (context / ".git").exists()
        assert [file.relative_to(context / "src").as_posix() for file in (context / "src").rglob("*") if file.is_file()] == [
            "reasoning_ledger/agents/noether_worker.py"]
        assert args[args.index("--build-context") + 1] == "noether-source=" + str(context / "noether-source")
        assert (context / "images/research-cpu/runtime/python3").exists()
        archive, release, _ = write_oci(output)
        archive.rename(output / "research-cpu.oci.tar")
        report, _ = inspect_image(output / "research-cpu.oci.tar", SCHEMA, "localhost/reasoning/research-cpu")
        (output / "buildx-metadata.json").write_bytes(
            encode(
                {
                    "containerimage.digest": release["digest"],
                    "containerimage.config.digest": report["config_digest"],
                }
            )
        )

    monkeypatch.setattr("reasoning_ledger.image_release.command", command)
    monkeypatch.setattr("reasoning_ledger.image_release.subprocess.run", run)
    def noether_source(lock, output, **options):
        assert lock["commit"] == LOCK["noether"]["commit"]
        output.mkdir()
        return output
    monkeypatch.setattr("reasoning_ledger.image_release.stage_noether", noether_source)
    build_image(ROOT, output, "reasoning-images")
    report = json.loads((output / "image-evidence.json").read_bytes())
    assert report["build_tools"]["buildkit_image"] == LOCK["buildkit_image"]
    assert report["execution_verified"] is False
    assert not (output / "compute-images.json").exists()
    assert all(call[:1] != ["docker"] or call[1:3] == ["--host", "unix:///var/run/docker.sock"] for call in calls)


def test_image_workflow_retains_installed_public_review_evidence_not_private_state():
    workflow = (ROOT / ".github/workflows/compute-images.yml").read_text()
    artifact_paths = re.findall(r"^\s+(\$\{\{ runner.temp \}\}/reasoning-images/[^\n]+)$", workflow, re.MULTILINE)
    assert artifact_paths == [
        "${{ runner.temp }}/reasoning-images/candidate/image-evidence.json",
        "${{ runner.temp }}/reasoning-images/candidate/capabilities.json",
        "${{ runner.temp }}/reasoning-images/candidate/sbom.spdx.json",
        "${{ runner.temp }}/reasoning-images/acceptance/acceptance.json",
    ]
    assert "permissions:\n  contents: read" in workflow
    assert 'test "${RUNNER_ENVIRONMENT:-}" = github-hosted' in workflow
    assert workflow.index('test "${RUNNER_ENVIRONMENT:-}" = github-hosted') < workflow.index("sudo rm -rf")
    assert "/opt/hostedtoolcache" in workflow
    assert workflow.index("sudo rm -rf") < workflow.index("uses: astral-sh/setup-uv") < workflow.index("run: uv sync --locked")
    assert 'df -h "$RUNNER_TEMP" /var/lib/docker' in workflow
    assert "if: success() && github.event_name == 'workflow_dispatch' && inputs.retain_candidate" in workflow
    assert "path: ${{ runner.temp }}/reasoning-images/candidate/${{ matrix.profile }}.oci.tar" in workflow
    assert "--push" not in workflow and "packages: write" not in workflow
    assert "sudo install -m 0644 .github/ci/apt-network.conf /etc/apt/apt.conf.d/99-reasoning-network" in workflow
    bounds = (ROOT / ".github/ci/apt-network.conf").read_text()
    assert 'Acquire::Retries "1";' in bounds
    assert 'Acquire::http::Timeout "10";' in bounds
    assert 'Acquire::https::Timeout "20";' in bounds


def test_publication_installs_python_tools_after_disposable_cache_cleanup():
    workflow = (ROOT / ".github/workflows/compute-publish.yml").read_text()
    assert workflow.index('test "${RUNNER_ENVIRONMENT:-}" = github-hosted') < workflow.index("sudo rm -rf")
    assert workflow.index("sudo rm -rf") < workflow.index("uses: astral-sh/setup-uv") < workflow.index("uv sync --locked")
    assert (workflow.index("Check signed provenance and anonymous source delivery")
            < workflow.index("Publish only the signed reviewed digest"))
    assert "--recipe-from-bundle" in workflow
    assert "ref: ${{ inputs.source_revision }}" not in workflow
    assert "reasoning_ledger.image_hosted request" in workflow


@pytest.mark.parametrize("copy_fails", [False, True])
@pytest.mark.parametrize("retain_archive", [False, True])
def test_disposable_image_ci_frees_builder_before_pull_and_archive_only_after_copy(monkeypatch, tmp_path, copy_fails, retain_archive):
    from reasoning_ledger import image_ci

    events = []
    monkeypatch.setattr(image_ci, "disk_status", lambda *args, **kwargs: None)
    archive = tmp_path / "candidate/research-cpu.oci.tar"

    def build(root, output, builder, profile):
        events.append("export")
        output.mkdir()
        archive.write_bytes(b"nonexecuting transport fixture")

    def inspect(*args):
        assert archive.exists()
        events.append("inspect")
        return {"image": {"reference": "127.0.0.1:5000/reasoning/research-cpu@sha256:" + "a" * 64}}, {}

    def run(args):
        if "buildx" in args:
            events.append("create" if "create" in args else "remove-builder")
        elif args[0] == "skopeo":
            events.append("copy")
            assert archive.exists()
            if copy_fails:
                raise ValueError("fixture copy failed")
        elif "pull" in args:
            assert archive.exists() == retain_archive
            events.append("pull")
        elif "run" in args:
            events.append("registry")
        else:
            events.append("remove-registry")

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url):
            from types import SimpleNamespace

            return SimpleNamespace(status_code=200)

    monkeypatch.setattr(image_ci, "validate_recipe", lambda root: {"buildkit_image": "nonexecuting fixture"})
    monkeypatch.setattr(image_ci, "build_image", build)
    monkeypatch.setattr(image_ci, "inspect_image", inspect)
    monkeypatch.setattr(image_ci, "run", run)
    monkeypatch.setattr(image_ci.httpx, "Client", Client)
    monkeypatch.setattr(image_ci, "write_evidence", lambda *args: None)
    monkeypatch.setattr(image_ci, "verify", lambda *args: events.append("verify"))
    if copy_fails:
        with pytest.raises(ValueError, match="fixture copy failed"):
            image_ci.verify_candidate(ROOT, tmp_path, retain_archive=retain_archive)
        assert archive.exists()
        assert events == ["create", "export", "remove-builder", "registry", "inspect", "copy", "remove-registry"]
    else:
        image_ci.verify_candidate(ROOT, tmp_path, retain_archive=retain_archive)
        assert archive.exists() == retain_archive
        assert events == ["create", "export", "remove-builder", "registry", "inspect", "copy", "pull", "verify", "remove-registry"]


@pytest.mark.parametrize("profile", ["research-cpu", "lean-mathlib"])
def test_image_disk_preflight_refuses_before_creating_builder(monkeypatch, tmp_path, profile, capsys):
    from types import SimpleNamespace

    from reasoning_ledger import image_ci

    minimum = image_ci.MINIMUM_FREE_BYTES[profile]
    monkeypatch.setattr(image_ci, "validate_recipe", lambda root: {})
    monkeypatch.setattr(image_ci.shutil, "disk_usage", lambda path: SimpleNamespace(free=minimum - 1, total=64 * 1024**3))
    monkeypatch.setattr(image_ci, "run", lambda args: pytest.fail("must not create a builder"))
    with pytest.raises(ValueError, match="free before starting"):
        image_ci.verify_candidate(ROOT, tmp_path, profile)
    assert json.loads(capsys.readouterr().out)["free_bytes"] == minimum - 1


def test_image_disk_report_after_export_failure_keeps_original_exception(monkeypatch, tmp_path, capsys):
    from types import SimpleNamespace

    from reasoning_ledger import image_ci

    commands = []
    monkeypatch.setattr(image_ci, "validate_recipe", lambda root: {"buildkit_image": "fixture"})
    monkeypatch.setattr(image_ci.shutil, "disk_usage", lambda path: SimpleNamespace(free=64 * 1024**3, total=128 * 1024**3))
    monkeypatch.setattr(image_ci, "run", commands.append)

    def fail(*args, **kwargs):
        raise ValueError("export failed")

    monkeypatch.setattr(image_ci, "build_image", fail)
    with pytest.raises(ValueError, match="export failed"):
        image_ci.verify_candidate(ROOT, tmp_path)
    assert [json.loads(line)["phase"] for line in capsys.readouterr().out.splitlines()] == [
        "before-build", "after-acceptance-cleanup",
    ]
    assert commands[-1][-2:] == ["rm", "reasoning-image-acceptance"]
