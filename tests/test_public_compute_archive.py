"""Container archive handling without a daemon or network-capable test host."""

import hashlib
import io
import tarfile

import pytest

from reasoning_ledger.server.compute_archive import ArchiveError, collect_artifacts


def archive(records):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as output:
        for name, body, kind in records:
            entry = tarfile.TarInfo(name)
            entry.type, entry.mode, entry.size = kind, 0o755, len(body)
            if kind in {tarfile.SYMTYPE, tarfile.LNKTYPE}:
                entry.linkname = "../../outside"
                entry.size = 0
            output.addfile(entry, io.BytesIO(body) if kind == tarfile.REGTYPE else None)
    stream.seek(0)
    return stream


@pytest.fixture
def spool(tmp_path):
    directory = tmp_path / "spool"
    directory.mkdir(mode=0o700)
    return directory


def test_collects_flat_regular_files_with_hashes_without_preserving_executable_modes(spool):
    body = b"exact algebra output\n" * 2048
    data = archive([("out", b"", tarfile.DIRTYPE), ("out/result.txt", body, tarfile.REGTYPE), ("out/empty", b"", tarfile.REGTYPE)])
    result = collect_artifacts(data, spool, len(body))
    assert [row.name for row in result] == ["result.txt", "empty"]
    assert result[0].size_bytes == len(body) and result[0].digest == "sha256:" + hashlib.sha256(body).hexdigest()
    assert result[0].path.name == "0" and result[0].path.read_bytes() == body
    assert result[0].path.stat().st_mode & 0o777 == 0o600
    assert result[1].path.read_bytes() == b"" and result[1].digest == "sha256:" + hashlib.sha256(b"").hexdigest()


@pytest.mark.parametrize("name", ["result.txt", "./result.txt", "out/result.txt", "./out/result.txt"])
def test_accepts_contents_only_and_output_root_archives(spool, name):
    result = collect_artifacts(archive([(name, b"result", tarfile.REGTYPE)]), spool, 6)
    assert result[0].name == "result.txt" and result[0].path.read_bytes() == b"result"


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/absolute",
        "out/../../escape",
        "out/nested/file",
        "nested/file",
        "./../escape",
        "out/../escape",
        "a\\b",
        "x\r\nheader",
        "a" * 129,
        ".hidden",
    ],
)
def test_refuses_traversal_nested_and_unsafe_filenames_and_removes_only_its_spool_files(spool, tmp_path, name):
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"untouched")
    data = archive([("out/first.txt", b"accepted prefix", tarfile.REGTYPE), (name, b"unsafe", tarfile.REGTYPE)])
    with pytest.raises(ArchiveError, match="unsafe"):
        collect_artifacts(data, spool, 1000)
    assert list(spool.iterdir()) == [] and outside.read_bytes() == b"untouched"


@pytest.mark.parametrize(
    "kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.GNUTYPE_SPARSE]
)
def test_refuses_links_devices_fifos_and_sparse_records(spool, kind):
    data = archive([("dangerous", b"", kind)])
    with pytest.raises(ArchiveError):
        collect_artifacts(data, spool, 1024)
    assert list(spool.iterdir()) == []


def test_duplicate_normalized_filenames_are_rejected(spool):
    with pytest.raises(ArchiveError, match="duplicate"):
        collect_artifacts(archive([("result", b"one", tarfile.REGTYPE), ("out/result", b"two", tarfile.REGTYPE)]), spool, 100)
    assert list(spool.iterdir()) == []


def test_bytes_count_truncation_and_wire_padding_are_bounded(spool):
    with pytest.raises(ArchiveError, match="byte allowance"):
        collect_artifacts(archive([("result", b"too many bytes", tarfile.REGTYPE)]), spool, 1)
    assert list(spool.iterdir()) == []
    with pytest.raises(ArchiveError, match="count"):
        collect_artifacts(archive([(f"file-{index}", b"", tarfile.REGTYPE) for index in range(65)]), spool, 1)
    assert list(spool.iterdir()) == []
    data = archive([("result", b"result", tarfile.REGTYPE)]).getvalue()
    with pytest.raises(ArchiveError):
        collect_artifacts(io.BytesIO(data[:515]), spool, 10)
    assert list(spool.iterdir()) == []
    with pytest.raises(ArchiveError, match="wire allowance"):
        collect_artifacts(io.BytesIO(data + b"\x00" * (1024**2 + 10)), spool, 10)
    assert list(spool.iterdir()) == []


def test_destination_must_be_private_empty_and_not_a_symlink(spool, tmp_path):
    data = archive([("result", b"result", tarfile.REGTYPE)])
    (spool / "existing").write_bytes(b"keep")
    with pytest.raises(ArchiveError, match="empty"):
        collect_artifacts(data, spool, 100)
    assert (spool / "existing").read_bytes() == b"keep"
    alias = tmp_path / "alias"
    alias.symlink_to(spool)
    with pytest.raises(ArchiveError, match="private"):
        collect_artifacts(data, alias, 100)
    exposed = tmp_path / "exposed"
    exposed.mkdir(mode=0o755)
    with pytest.raises(ArchiveError, match="private"):
        collect_artifacts(data, exposed, 100)


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, 1024**3 + 1])
def test_invalid_limits_are_rejected(spool, limit):
    with pytest.raises(ArchiveError, match="allowance"):
        collect_artifacts(io.BytesIO(), spool, limit)


def test_extended_metadata_is_bounded_before_tarfile_allocates_it(spool):
    header = tarfile.TarInfo("metadata")
    header.type, header.size = tarfile.XHDTYPE, 65537
    with pytest.raises(ArchiveError, match="metadata"):
        collect_artifacts(io.BytesIO(header.tobuf() + b"\x00" * (66 * 1024)), spool, 1024**3)
    assert list(spool.iterdir()) == []


def test_nonzero_trailing_data_is_not_accepted_as_another_archive(spool):
    data = archive([("result", b"result", tarfile.REGTYPE)]).getvalue()
    with pytest.raises(ArchiveError, match="end marker"):
        collect_artifacts(io.BytesIO(data + b"untrusted trailing payload"), spool, 100)
    assert list(spool.iterdir()) == []


def test_pax_sparse_maps_are_rejected_before_tarfile_reads_payload_maps(spool):
    payload = b"22 GNU.sparse.major=1\n22 GNU.sparse.minor=0\n"
    header = tarfile.TarInfo("metadata")
    header.type, header.size = tarfile.XHDTYPE, len(payload)
    body = header.tobuf() + payload + b"\x00" * (512 - len(payload))
    with pytest.raises(ArchiveError, match="sparse"):
        collect_artifacts(io.BytesIO(body), spool, 1024**3)
    assert list(spool.iterdir()) == []
