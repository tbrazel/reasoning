"""Read-only scaling observations/decisions; no real cloud or computer algebra."""

import asyncio
import copy
import json
import time
from pathlib import Path

import httpx
import pytest
from test_public_compute_ui import ORIGIN, headers
from test_public_compute_ui import fixture as _http_fixture
from test_public_compute_worker import fixture as _compute_fixture
from test_public_worker_cloud_retirement import DeletionProvider
from test_public_worker_deployment import IMAGE, REGISTRATION, TOKEN
from test_public_worker_provisioning import apply
from test_public_worker_provisioning import worker_cloud as worker_cloud

from reasoning_ledger.deployment import cli, scaling
from reasoning_ledger.deployment.digitalocean import DigitalOcean
from reasoning_ledger.deployment.host import InstallError
from reasoning_ledger.deployment.private import read_document, write_document
from reasoning_ledger.server.compute_catalog import DEFAULT_RESOURCES, ComputeImage
from reasoning_ledger.server.compute_store import ComputeStore
from reasoning_ledger.server.compute_worker import write_state
from reasoning_ledger.server.store import StoreError

http_fixture = _http_fixture
compute_fixture = _compute_fixture


def database(fixture):
    with fixture.state.store.transaction() as db:
        return {
            table: [tuple(row) for row in db.execute("SELECT * FROM " + table)]
            for table in ("compute_workers", "compute_jobs", "compute_events", "audit_events")
        }


def test_snapshot_is_atomic_readonly_secret_free_and_preserves_expired_attempt(compute_fixture):
    fixture = compute_fixture
    first = fixture.enqueue()
    fixture.state.claim(fixture.registration["token"])
    fixture.enqueue("second")
    with fixture.state.store.transaction(write=True) as db:
        db.execute("UPDATE compute_jobs SET lease_expires_at=0 WHERE id=?", (first["id"],))
    before = database(fixture)
    snapshot = fixture.state.scaling_snapshot(fixture.user, fixture.org)
    assert snapshot["jobs"][0]["status"] == "running" and snapshot["jobs"][0]["lease_expires_at"] == 0
    assert snapshot["jobs"][1]["runnable"] is True
    assert snapshot["workers"][0]["idle_since_at"] == fixture.registration["created_at"]
    encoded = json.dumps(snapshot)
    for secret in (
        "print('nonexecuting fixture')",
        fixture.registration["token"],
        "token_hash",
        "lease_hash",
        "attempt_id",
        "requested_by",
        "topic_id",
    ):
        assert secret not in encoded
    assert database(fixture) == before
    scaling.validate_snapshot(snapshot, fixture.org, int(time.time()))


@pytest.mark.parametrize("change", ["disabled-image", "quota", "membership", "catalog"])
def test_snapshot_revalidates_queue_admission_without_settling_it(compute_fixture, change):
    fixture = compute_fixture
    fixture.enqueue()
    if change == "disabled-image":
        fixture.state.set_policy(fixture.user, fixture.org, [], 10000, 2)
    elif change == "quota":
        fixture.state.set_policy(fixture.user, fixture.org, ["fixture"], 0, 2)
    elif change == "membership":
        # Another admin remains able to inspect a requester's removed write grant.
        invitation = fixture.state.store.invite(fixture.user, fixture.org, "scaler@example.org", "admin", 3600)
        administrator = fixture.state.store.accept_invitation(
            invitation["token"], user_id=None, name="Scaler", password="long fixture password"
        )
        with fixture.state.store.transaction(write=True) as db:
            db.execute("UPDATE memberships SET role='viewer' WHERE user_id=?", (fixture.user,))
        before = database(fixture)
        snapshot = fixture.state.scaling_snapshot(administrator["user_id"], fixture.org)
        assert snapshot["jobs"][0]["runnable"] is False and database(fixture) == before
        return
    else:
        old = fixture.state.images["fixture"].record()
        old["reference"] = "registry.example/cpu@sha256:" + "e" * 64
        fixture.state.images["fixture"] = ComputeImage.parse(old)
    before = database(fixture)
    snapshot = fixture.state.scaling_snapshot(fixture.user, fixture.org)
    assert snapshot["jobs"][0]["runnable"] is False and snapshot["jobs"][0]["status"] == "queued"
    assert database(fixture) == before


def test_worker_idle_time_tracks_settlement_not_poll_heartbeat(compute_fixture, monkeypatch):
    fixture = compute_fixture
    start = int(time.time())
    fixture.enqueue()
    claimed = fixture.state.claim(fixture.registration["token"])
    monkeypatch.setattr(time, "time", lambda: start + 5)
    finished = fixture.state.finish(
        fixture.registration["token"], claimed["id"], claimed["attempt_id"], claimed["lease"], "succeeded", 1000, 0, None
    )
    monkeypatch.setattr(time, "time", lambda: start + 25)
    assert fixture.state.claim(fixture.registration["token"]) is None
    snapshot = fixture.state.scaling_snapshot(fixture.user, fixture.org)
    assert snapshot["workers"][0]["last_seen_at"] == start + 25
    assert snapshot["workers"][0]["idle_since_at"] == finished["finished_at"] == start + 5


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_snapshot_requires_administration(compute_fixture, role):
    fixture = compute_fixture
    with fixture.state.store.transaction(write=True) as db:
        db.execute("UPDATE memberships SET role=? WHERE user_id=?", (role, fixture.user))
    with pytest.raises(StoreError) as error:
        fixture.state.scaling_snapshot(fixture.user, fixture.org)
    assert error.value.status == 403


def test_snapshot_does_not_leak_other_tenant_or_retired_history(compute_fixture):
    fixture = compute_fixture
    fixture.enqueue()
    other = fixture.state.store.create_organization(fixture.user, "Other")["id"]
    assert fixture.state.scaling_snapshot(fixture.user, other)["jobs"] == []
    assert fixture.state.scaling_snapshot(fixture.user, other)["workers"] == []
    fixture.state.drain_self(fixture.registration["token"])
    fixture.state.retire_worker(fixture.registration["token"], True)
    assert fixture.state.scaling_snapshot(fixture.user, fixture.org)["workers"] == []
    with pytest.raises(StoreError) as error:
        fixture.state.scaling_snapshot("f" * 32, fixture.org)
    assert error.value.status == 404


def test_snapshot_keeps_revoked_busy_workers_and_does_not_expire_them(compute_fixture):
    fixture = compute_fixture
    fixture.enqueue()
    fixture.state.claim(fixture.registration["token"])
    fixture.state.change_worker(fixture.user, fixture.org, fixture.registration["id"], revoke=True)
    snapshot = fixture.state.scaling_snapshot(fixture.user, fixture.org)
    assert snapshot["jobs"][0]["status"] == "running"
    assert snapshot["workers"][0]["revoked_at"] is not None
    scaling.validate_snapshot(snapshot, fixture.org, int(time.time()))


def test_scaling_snapshot_survives_store_restart(compute_fixture):
    fixture = compute_fixture
    fixture.enqueue()
    snapshot = fixture.state.scaling_snapshot(fixture.user, fixture.org)
    restarted = ComputeStore(fixture.state.store, fixture.state.images)
    again = restarted.scaling_snapshot(fixture.user, fixture.org)
    assert {key: value for key, value in again.items() if key != "observed_at"} == {
        key: value for key, value in snapshot.items() if key != "observed_at"
    }


def test_snapshot_http_authorizes_human_admin_not_worker_credential(http_fixture):
    _, app, owner, _, root = http_fixture
    registration = app.state.compute.register_worker(
        owner["user_id"], owner["organization_id"], "Separate", ["protocol-fixture"], DEFAULT_RESOURCES
    )

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
            assert (await client.get(root + "/compute-scaling-snapshot", headers=headers(owner))).status_code == 200
            assert (await client.get(root + "/compute-scaling-snapshot")).status_code == 401
            assert (
                await client.get(root + "/compute-scaling-snapshot", headers={"authorization": "Bearer " + registration["token"]})
            ).status_code == 401
            assert (
                await client.get("/api/organizations/" + "f" * 32 + "/compute-scaling-snapshot", headers=headers(owner))
            ).status_code == 404

    asyncio.run(check())


@pytest.fixture
def decision(compute_fixture):
    fixture = compute_fixture
    fixture.enqueue()
    now = int(time.time())
    snapshot = fixture.state.scaling_snapshot(fixture.user, fixture.org)
    fleet = {
        "format": 1,
        "server_url": "https://reasoning.example",
        "organization_id": fixture.org,
        "minimum_workers": 0,
        "maximum_workers": 4,
        "maximum_hourly_usd": "0.20",
        "idle_seconds": 60,
        "workers": [],
    }
    owned = [
        {
            "worker_id": fixture.registration["id"],
            "plan_id": "c" * 64,
            "phase": "unprovisioned",
            "resources": dict(DEFAULT_RESOURCES),
            "image_ids": ["fixture"],
            "catalog": copy.deepcopy(snapshot["images"]),
            "hourly_usd": "0",
            "provision_hourly_usd": "0.035",
            "account_identity": "user:" + "a" * 40,
        }
    ]
    return fleet, snapshot, owned, now


def result(decision, **changes):
    fleet, snapshot, owned, now = decision
    return scaling.decide(fleet, snapshot, owned, now=changes.get("now", now))


def test_partial_demand_preview_does_not_authorize_idle_retirement(decision):
    online(decision)
    snapshot = decision[1]
    snapshot.update(format=2, queued_remaining=10)
    snapshot["jobs"][0]["runnable"] = False
    report = result(decision)
    assert report["actions"] == [] and report["uncovered_demand"]


def online(decision, *, idle=100):
    _, snapshot, owned, now = decision
    worker = snapshot["workers"][0]
    worker.update(created_at=now - idle, idle_since_at=now - idle, last_seen_at=now)
    owned[0].update(phase="active", hourly_usd="0.035")
    return worker


def test_queue_matching_proposes_exact_slot_under_aggregate_caps_and_never_executes(decision):
    before = copy.deepcopy(decision)
    report = result(decision)
    assert report["actions"] == [{"action": "provision", "worker_id": decision[2][0]["worker_id"], "plan_id": "c" * 64}]
    assert report["projected_owned_hourly_usd"] == "0.035" and report["covered_concurrency_slots"] == 1
    assert report["mode"] == "advisory" and not report["creates_resources"] and not report["destroys_resources"]
    assert decision == before
    assert result(decision)["decision_id"] == report["decision_id"]


def test_advisory_identity_binds_fleet_policy_and_observations(decision):
    first = result(decision)
    decision[0]["idle_seconds"] += 1
    second = result(decision)
    assert first["decision_id"] != second["decision_id"] and first["fleet_sha256"] != second["fleet_sha256"]
    assert first["snapshot_sha256"] == second["snapshot_sha256"] and first["inventory_sha256"] == second["inventory_sha256"]


@pytest.mark.parametrize("cap,value", [("maximum_workers", 0), ("maximum_hourly_usd", "0"), ("maximum_hourly_usd", "0.034999")])
def test_scaling_respects_exact_count_and_decimal_budget(decision, cap, value):
    decision[0][cap] = value
    report = result(decision)
    assert report["actions"] == [] and report["uncovered_demand"]


def test_exact_decimal_ceiling_allows_equal_quote(decision):
    decision[0]["maximum_hourly_usd"] = "0.035"
    assert result(decision)["actions"][0]["action"] == "provision"


@pytest.mark.parametrize(
    "resource,value", [("cpu", 2), ("memory_bytes", 2 * 1024**3), ("disk_bytes", 512 * 1024**2), ("wall_time_seconds", 121)]
)
def test_more_small_workers_do_not_satisfy_one_oversized_job(decision, resource, value):
    decision[1]["jobs"][0]["resources"][resource] = value
    assert result(decision)["actions"] == [] and result(decision)["uncovered_demand"]


def test_matching_uses_augmenting_paths_and_one_job_per_worker():
    jobs = [{"image_id": name, "resources": DEFAULT_RESOURCES} for name in ("a", "b")]
    workers = [
        {"id": "flexible", "image_ids": ["a", "b"], "resources": DEFAULT_RESOURCES},
        {"id": "a-only", "image_ids": ["a"], "resources": DEFAULT_RESOURCES},
    ]
    assert scaling.coverage(jobs, workers) == 2
    assert scaling.coverage(jobs, workers[:1]) == 1
    assert scaling.coverage(jobs * 2, workers) == 2


def test_existing_fresh_compatible_capacity_prevents_duplicate_vm(decision):
    online(decision)
    assert result(decision)["actions"] == [] and result(decision)["covered_concurrency_slots"] == 1


def test_foreign_worker_capacity_counts_but_foreign_vm_is_never_retired(decision):
    slot_worker = decision[1]["workers"][0]
    foreign = copy.deepcopy(slot_worker)
    foreign.update(id="e" * 32, last_seen_at=decision[3])
    decision[1]["workers"].append(foreign)
    assert result(decision)["actions"] == []
    decision[1]["jobs"] = []
    assert result(decision)["actions"] == []


@pytest.mark.parametrize("phase", ["provisioning", "uncertain", "retiring"])
def test_inflight_or_uncertain_resources_reserve_cost_and_hold_fleet(decision, phase):
    decision[2][0].update(phase=phase, hourly_usd="0.035")
    report = result(decision)
    assert report["actions"] == [] and report["reserved_owned_hourly_usd"] == "0.035"
    assert report["reconcile_workers"] == [decision[2][0]["worker_id"]]


@pytest.mark.parametrize("change", ["stale", "unseen", "draining", "revoked", "catalog", "resources"])
def test_stale_or_drifted_managed_capacity_does_not_trigger_paid_replacement(decision, change):
    worker = online(decision)
    if change == "stale":
        worker["last_seen_at"] = decision[3] - 46
    elif change == "unseen":
        worker["last_seen_at"] = None
    elif change == "draining":
        worker["draining"] = 1
    elif change == "revoked":
        worker["revoked_at"] = decision[3]
    elif change == "catalog":
        decision[2][0]["catalog"][0]["name"] = "changed"
    else:
        worker["resources"]["cpu"] = 2
    report = result(decision)
    assert report["actions"] == [] and report["reconcile_workers"]


@pytest.mark.parametrize("status", ["running", "outcome_unknown"])
def test_unsettled_attempts_remain_busy_even_after_lease_expiry(decision, status):
    online(decision)
    snapshot = decision[1]
    snapshot["jobs"][0].update(status=status, worker_id=snapshot["workers"][0]["id"], runnable=False, lease_expires_at=0, deadline_at=0)
    snapshot["max_active_jobs"] = 1
    report = result(decision)
    assert report["available_concurrency_slots"] == 0 and not report["actions"]


def test_idle_retirement_is_only_advisory_and_never_discards_needed_capacity(decision):
    worker = online(decision)
    assert result(decision)["actions"] == []
    decision[1]["jobs"] = []
    assert result(decision)["actions"] == [
        {"action": "retire", "worker_id": worker["id"], "plan_id": "c" * 64, "requires_fresh_host_checks": True}
    ]
    decision[0]["minimum_workers"] = 1
    assert result(decision)["actions"] == []


def test_recently_used_worker_is_not_retired(decision):
    online(decision, idle=59)
    decision[1]["jobs"] = []
    assert result(decision)["actions"] == []


def test_warm_minimum_is_bounded_and_budget_limited(decision):
    decision[1]["jobs"] = []
    decision[0]["minimum_workers"] = 1
    assert result(decision)["actions"][0]["action"] == "provision"
    decision[0]["maximum_hourly_usd"] = "0"
    report = result(decision)
    assert report["minimum_unmet"] and report["actions"] == []


def test_disabled_queued_job_never_drives_paid_scale_up(decision):
    decision[1]["jobs"][0]["runnable"] = False
    report = result(decision)
    assert report["actions"] == [] and report["policy_blocked_jobs"] == [decision[1]["jobs"][0]["id"]]


def test_fleet_ceiling_accounts_for_all_proposed_workers_together(decision):
    fleet, snapshot, owned, _ = decision
    second = copy.deepcopy(snapshot["workers"][0])
    second["id"] = "e" * 32
    snapshot["workers"].append(second)
    second_slot = copy.deepcopy(owned[0])
    second_slot.update(worker_id=second["id"], plan_id="e" * 64)
    owned.append(second_slot)
    second_job = copy.deepcopy(snapshot["jobs"][0])
    second_job["id"] = "d" * 32
    snapshot["jobs"].append(second_job)
    fleet["maximum_hourly_usd"] = "0.06"
    report = result(decision)
    assert len(report["actions"]) == 1 and report["projected_owned_hourly_usd"] == "0.035" and report["uncovered_demand"]
    fleet["maximum_hourly_usd"] = "0.070"
    report = result(decision)
    assert len(report["actions"]) == 2 and report["projected_owned_hourly_usd"] == "0.070" and not report["uncovered_demand"]


def test_last_future_compatible_worker_is_kept_when_concurrency_is_full(decision):
    online(decision)
    snapshot = decision[1]
    busy = copy.deepcopy(snapshot["workers"][0])
    busy["id"] = "e" * 32
    snapshot["workers"].append(busy)
    running = copy.deepcopy(snapshot["jobs"][0])
    running.update(id="e" * 32, status="running", worker_id=busy["id"], runnable=False, lease_expires_at=0, deadline_at=0)
    snapshot["jobs"].append(running)
    snapshot["max_active_jobs"] = 1
    assert result(decision)["available_concurrency_slots"] == 0 and result(decision)["actions"] == []


def test_closed_vm_is_not_assumed_ready_from_recent_server_poll(decision):
    online(decision)
    decision[2][0].update(phase="closed", hourly_usd="0")
    assert result(decision)["covered_concurrency_slots"] == 0 and result(decision)["actions"] == []


@pytest.mark.parametrize("change", ["stale", "future", "tenant", "boolean", "extra", "program", "duplicate", "worker", "bad-resource"])
def test_snapshot_validator_refuses_stale_malformed_or_secret_bearing_payload(decision, change):
    snapshot = decision[1]
    if change == "stale":
        snapshot["observed_at"] -= 31
    elif change == "future":
        snapshot["observed_at"] += 6
    elif change == "tenant":
        snapshot["organization_id"] = "f" * 32
    elif change == "boolean":
        snapshot["format"] = True
    elif change == "extra":
        snapshot["secret"] = TOKEN
    elif change == "program":
        snapshot["jobs"][0]["program"] = "private-source"
    elif change == "duplicate":
        snapshot["workers"].append(copy.deepcopy(snapshot["workers"][0]))
    elif change == "worker":
        snapshot["workers"][0]["organization_id"] = "f" * 32
    else:
        snapshot["jobs"][0]["resources"]["cpu"] = True
    with pytest.raises(InstallError, match="snapshot"):
        result(decision)


@pytest.fixture
def cloud_fleet(worker_cloud):
    remote, _, _, _, saved, _, _, directory = worker_cloud
    root = directory.parent
    registration, catalog = root / "registration.json", root / "catalog.json"
    write_state(registration, REGISTRATION)
    write_state(catalog, [IMAGE])
    fleet = {
        "format": 1,
        "server_url": ORIGIN,
        "organization_id": REGISTRATION["organization_id"],
        "minimum_workers": 1,
        "maximum_workers": 2,
        "maximum_hourly_usd": "0.10",
        "idle_seconds": 60,
        "workers": [{"plan": saved["plan_file"], "registration": str(registration), "catalog": str(catalog), "state_dir": str(directory)}],
    }
    path = root / "fleet.json"
    write_document(path, fleet, new=True)
    return fleet, path, worker_cloud


def test_fleet_load_and_live_inventory_are_readonly_and_quote_bound(cloud_fleet):
    _, path, cloud = cloud_fleet
    fleet, profiles = scaling.load_fleet(path)
    directory = cloud[7]
    assert not directory.exists()
    owned = scaling.inventory(cloud[0].client, profiles)
    assert owned[0]["phase"] == "unprovisioned" and owned[0]["hourly_usd"] == "0"
    assert not cloud[0].writes and not directory.exists()
    assert fleet["minimum_workers"] == 1


@pytest.mark.parametrize(
    "change", ["duplicate", "tenant", "path", "directory", "budget", "minimum", "boolean", "catalog", "token", "extra"]
)
def test_fleet_local_validation_refuses_unsafe_or_unbound_slots(cloud_fleet, change):
    fleet, path, cloud = cloud_fleet
    if change == "duplicate":
        fleet["workers"].append(copy.deepcopy(fleet["workers"][0]))
    elif change == "tenant":
        fleet["organization_id"] = "f" * 32
    elif change == "path":
        fleet["workers"][0]["state_dir"] = "relative"
    elif change == "directory":
        cloud[7].mkdir(mode=0o755)
    elif change == "budget":
        fleet["maximum_hourly_usd"] = "NaN"
    elif change == "minimum":
        fleet["minimum_workers"] = 3
    elif change == "boolean":
        fleet["maximum_workers"] = True
    elif change == "catalog":
        image = copy.deepcopy(IMAGE)
        image["reference"] = "registry.example.org/research/cpu@sha256:" + "0" * 64
        write_state(Path(fleet["workers"][0]["catalog"]), [image])
    elif change == "token":
        record = {**REGISTRATION, "token": "y" * 64}
        write_state(Path(fleet["workers"][0]["registration"]), record)
    else:
        fleet["workers"][0]["secret"] = TOKEN
    write_document(path, fleet)
    with pytest.raises(InstallError):
        scaling.load_fleet(path)


@pytest.mark.parametrize("change", ["account", "price", "size", "key", "unmanaged"])
def test_inventory_drift_never_creates_resources(cloud_fleet, change):
    _, path, cloud = cloud_fleet
    _, profiles = scaling.load_fleet(path)
    remote = cloud[0]
    if change == "account":
        remote.account["uuid"] = "b" * 40
    elif change == "price":
        remote.size["price_hourly"] = 0.04
    elif change == "size":
        remote.size["memory"] = 8192
    elif change == "key":
        remote.key["public_key"] = "ssh-ed25519 changed-key"
    else:
        remote.tags.append({"name": profiles[0].marker})
    with pytest.raises(InstallError):
        scaling.inventory(remote.client, profiles)
    assert not remote.writes


@pytest.mark.parametrize("change", [None, "missing-firewall", "pending-firewall", "lost-vm-receipt", "retiring"])
def test_inventory_tracks_owned_inflight_capacity_without_adopting_or_mutating(cloud_fleet, change):
    _, path, cloud = cloud_fleet
    apply(cloud)
    remote, directory = cloud[0], cloud[7]
    remote.client.close()
    provider = DeletionProvider(remote, directory)
    remote.client = DigitalOcean(remote.token, transport=httpx.MockTransport(provider.handle))
    if change == "missing-firewall":
        remote.firewalls.clear()
    elif change == "pending-firewall":
        remote.firewalls[0]["status"] = "waiting"
    elif change == "lost-vm-receipt":
        state = read_document(directory / "digitalocean.json")
        state["resources"]["droplet"] = {"status": "request_pending", "id": None}
        write_document(directory / "digitalocean.json", state)
    elif change == "retiring":
        write_document(directory / "digitalocean-retirement.json", {"format": 1}, new=True)
    before = (directory / "digitalocean.json").read_bytes()
    _, profiles = scaling.load_fleet(path)
    owned = scaling.inventory(remote.client, profiles)
    expected = {
        None: "active",
        "missing-firewall": "uncertain",
        "pending-firewall": "provisioning",
        "lost-vm-receipt": "uncertain",
        "retiring": "retiring",
    }[change]
    assert owned[0]["phase"] == expected and owned[0]["hourly_usd"] == "0.035"
    assert not provider.deletions and (directory / "digitalocean.json").read_bytes() == before
    profiles[0]._validate_state()


@pytest.mark.parametrize("failure", ["redirect", "auth", "timeout", "malformed", "duplicate", "oversized"])
def test_snapshot_client_is_fixed_https_get_no_redirect_and_secret_safe(failure):
    def remote(request):
        assert request.method == "GET" and request.url.host == "chat.example.org" and request.url.scheme == "https"
        assert request.url.path == "/api/organizations/" + "b" * 32 + "/compute-scaling-snapshot" and request.content == b""
        assert request.headers["authorization"] == "Bearer " + TOKEN
        if failure == "timeout":
            raise httpx.ReadTimeout(TOKEN, request=request)
        if failure == "redirect":
            return httpx.Response(302, headers={"location": "https://attacker.example/"})
        if failure == "auth":
            return httpx.Response(403, json={"secret": TOKEN})
        if failure == "oversized":
            return httpx.Response(200, content=b"x" * (256 * 1024 + 1))
        return httpx.Response(200, content=b'{"format":1,"format":2}' if failure == "duplicate" else TOKEN)

    with pytest.raises(InstallError) as error:
        scaling.read_snapshot(ORIGIN, "b" * 32, TOKEN, transport=httpx.MockTransport(remote))
    assert TOKEN not in str(error.value)


def test_public_scale_plan_cli_writes_private_expiring_advice_without_cloud_mutation(cloud_fleet, monkeypatch, capsys):
    fleet, path, cloud = cloud_fleet
    root = path.parent
    session, token, output = root / "session", root / "provider-token", root / "decision.json"
    write_state(session, {})
    session.write_text(TOKEN)
    write_state(token, {})
    token.write_text(cloud[0].token)
    worker = {
        **{key: value for key, value in REGISTRATION.items() if key != "token"},
        "retired_at": None,
        "retirement_id": None,
        "idle_since_at": 1,
    }
    snapshot = {
        "format": 1,
        "organization_id": fleet["organization_id"],
        "observed_at": int(time.time()),
        "max_active_jobs": 2,
        "images": [IMAGE],
        "jobs": [],
        "workers": [worker],
    }

    def remote(origin, organization, credential):
        assert origin == ORIGIN and organization == fleet["organization_id"] and credential == TOKEN
        return snapshot

    monkeypatch.setattr(scaling, "read_snapshot", remote)
    monkeypatch.setattr(cli, "DigitalOcean", lambda _: cloud[0].client)
    assert (
        cli.main(
            [
                "do-worker-scale-plan",
                "--fleet",
                str(path),
                "--session-file",
                str(session),
                "--token-file",
                str(token),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report == read_document(output) and report["actions"][0]["action"] == "provision"
    assert output.stat().st_mode & 0o777 == 0o600 and not cloud[0].writes and not cloud[7].exists()
    assert TOKEN not in json.dumps(report) and cloud[0].token not in json.dumps(report)


def test_fleet_builder_validates_and_saves_without_provider_or_credential_output(cloud_fleet, monkeypatch, capsys):
    fleet, path, _ = cloud_fleet
    output = path.parent / "confirmed-fleet.json"
    monkeypatch.setattr(cli, "DigitalOcean", lambda _: pytest.fail("fleet builder must not access the provider"))
    entry = fleet["workers"][0]
    argv = [
        "do-worker-fleet",
        "--server-url",
        ORIGIN,
        "--organization-id",
        fleet["organization_id"],
        "--maximum-workers",
        "2",
        "--maximum-hourly-usd",
        "0.10",
        "--minimum-workers",
        "1",
        "--idle-seconds",
        "60",
        "--slot",
        *entry.values(),
        "--output",
        str(output),
    ]
    assert cli.main(argv) == 0 and read_document(output) == fleet
    assert TOKEN not in capsys.readouterr().out and output.stat().st_mode & 0o777 == 0o600
    before = output.read_bytes()
    with pytest.raises(SystemExit) as error:
        cli.main(argv)
    assert error.value.code == 2 and output.read_bytes() == before
