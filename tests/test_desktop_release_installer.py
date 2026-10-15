from __future__ import annotations

import hashlib
import json
import os
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "desktop" / "release"
sys.path.insert(0, str(RELEASE))

from make_manifest import load_descriptor, manifest  # noqa: E402
from render_installer import read_public_key, render  # noqa: E402


class DesktopReleaseInstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="reasoning-installer-test-")
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.key = self.root / "release-key"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.key)],
            check=True,
        )
        self.public_key = read_public_key(self.key.with_suffix(".pub"))
        self.linux_artifacts = {
            "arm64": self.root / "reasoning-arm64.AppImage",
            "x64": self.root / "reasoning-x64.AppImage",
        }
        self._write_appimage(self.linux_artifacts["arm64"], machine=0xB7)
        self._write_appimage(self.linux_artifacts["x64"], machine=0x3E)
        self.linux_artifact = self.linux_artifacts["x64"]
        self.macos_artifact = self.root / "reasoning.zip"
        with zipfile.ZipFile(self.macos_artifact, "w") as archive:
            self._write_zip_file(
                archive,
                "Reasoning.app/Contents/Info.plist",
                "<?xml version='1.0'?><plist><dict><key>CFBundleExecutable</key><string>reasoning</string></dict></plist>",
            )
            self._write_zip_file(
                archive,
                "Reasoning.app/Contents/MacOS/reasoning",
                "fixture\n",
                executable=True,
            )
        self.test_bin = self.root / "test-bin"
        self.test_bin.mkdir()
        self._write_test_command(
            "uname",
            """#!/bin/sh
case "$1" in
    -s)
        case "$REASONING_FIXTURE_OS" in
            darwin) printf 'Darwin\\n' ;;
            linux) printf 'Linux\\n' ;;
            *) printf '%s\\n' "$REASONING_FIXTURE_OS" ;;
        esac
        ;;
    -m)
        case "$REASONING_FIXTURE_ARCH" in
            arm64) printf 'arm64\\n' ;;
            x64) printf 'x86_64\\n' ;;
            *) printf '%s\\n' "$REASONING_FIXTURE_ARCH" ;;
        esac
        ;;
    *) exit 2 ;;
esac
""",
        )
        for command in ("codesign", "spctl"):
            self._write_test_command(command, "#!/bin/sh\nexit 0\n")
        self._write_test_command("plutil", "#!/bin/sh\nprintf 'reasoning\\n'\n")
        self._write_test_command(
            "lipo",
            """#!/bin/sh
if [ -n "${REASONING_FIXTURE_APP_ARCHS:-}" ]; then
    printf '%s\\n' "$REASONING_FIXTURE_APP_ARCHS"
elif [ "$REASONING_FIXTURE_ARCH" = arm64 ]; then
    printf 'arm64\\n'
else
    printf 'x86_64\\n'
fi
""",
        )
        self._write_test_command(
            "mv",
            """#!/bin/sh
set -eu
last=
for argument in "$@"; do last=$argument; done
if [ -n "${REASONING_COLLIDE_RELEASE_TARGET:-}" ] && [ "$last" = "$REASONING_COLLIDE_RELEASE_TARGET" ]; then
    mkdir -p "$last"
fi
case "${1:-}:${2:-}" in
    -f:-h|-f:-T)
        source=$3
        destination=$4
        if [ -L "$destination" ]; then /bin/rm "$destination"; fi
        /bin/mv "$source" "$destination"
        ;;
    *) /bin/mv "$@" ;;
esac
if [ -n "${REASONING_SIGNAL_RELEASE_TARGET:-}" ] && [ "$last" = "$REASONING_SIGNAL_RELEASE_TARGET" ]; then
    kill -TERM "$PPID"
fi
""",
        )
        self.manifest = self.root / "release.manifest"
        self.signature = self.root / "release.manifest.sig"
        self._write_manifest()
        self._sign()
        self.installer = self.root / "install.sh"
        self.installer.write_text(
            render(
                self.public_key,
                "https://release.test/release.manifest",
                "https://release.test/release.manifest.sig",
            ),
            encoding="utf-8",
        )
        self.installer.chmod(0o755)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _write_appimage(path: Path, *, machine: int) -> None:
        content = bytearray(64)
        content[0:4] = b"\x7fELF"
        content[4] = 2
        content[5] = 1
        content[6] = 1
        content[8:11] = b"AI\x02"
        content[18:20] = machine.to_bytes(2, "little")
        path.write_bytes(content)

    @staticmethod
    def _write_zip_file(
        archive: zipfile.ZipFile,
        name: str,
        content: str,
        *,
        executable: bool = False,
    ) -> None:
        entry = zipfile.ZipInfo(name)
        entry.create_system = 3
        entry.external_attr = (stat.S_IFREG | (0o755 if executable else 0o644)) << 16
        archive.writestr(entry, content)

    def _write_test_command(self, name: str, content: str) -> None:
        path = self.test_bin / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)

    def _records(self) -> list[dict[str, str]]:
        return [
            {
                "os": "darwin",
                "arch": arch,
                "kind": "macos-zip",
                "path": str(self.macos_artifact),
                "url": f"https://release.test/Reasoning-0.1.0-{arch}.zip",
            }
            for arch in ("arm64", "x64")
        ] + [
            {
                "os": "linux",
                "arch": arch,
                "kind": "linux-appimage",
                "path": str(self.linux_artifacts[arch]),
                "url": f"https://release.test/Reasoning-0.1.0-{arch}.AppImage",
            }
            for arch in ("arm64", "x64")
        ]

    def _write_manifest(
        self,
        records: list[dict[str, str]] | None = None,
        *,
        version: str = "0.1.0",
    ) -> None:
        descriptor = self.root / "descriptor.json"
        descriptor.write_text(
            json.dumps(
                {
                    "version": version,
                    "published_at": "2026-09-01T00:00:00Z",
                    "artifacts": records or self._records(),
                }
            ),
            encoding="utf-8",
        )
        version, published_at, artifacts = load_descriptor(descriptor)
        self.manifest.write_text(manifest(version, published_at, artifacts), encoding="utf-8")

    def _sign(self) -> None:
        self.signature.unlink(missing_ok=True)
        subprocess.run(
            [
                "ssh-keygen",
                "-Y",
                "sign",
                "-q",
                "-f",
                str(self.key),
                "-n",
                "reasoning-desktop",
                str(self.manifest),
            ],
            check=True,
        )
        generated = Path(str(self.manifest) + ".sig")
        generated.replace(self.signature)

    def _run(
        self,
        os_name: str,
        arch: str,
        artifact: Path,
        *arguments: str,
        check: bool = False,
        environment_overrides: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environment = {
            **os.environ,
            "HOME": str(self.home),
            "PATH": str(self.test_bin) + os.pathsep + os.environ["PATH"],
            "REASONING_FIXTURE_OS": os_name,
            "REASONING_FIXTURE_ARCH": arch,
        }
        environment.update(environment_overrides or {})
        return subprocess.run(
            [
                str(self.installer),
                "--offline",
                "--manifest",
                str(self.manifest),
                "--signature",
                str(self.signature),
                "--artifact",
                str(artifact),
                *arguments,
            ],
            check=check,
            capture_output=True,
            text=True,
            env=environment,
        )

    def test_manifest_is_deterministic_and_has_all_targets(self) -> None:
        first = self.manifest.read_bytes()
        self._write_manifest(list(reversed(self._records())))
        self.assertEqual(first, self.manifest.read_bytes())
        lines = self.manifest.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "reasoning-desktop-release-v1")
        self.assertEqual(sum(line.startswith("artifact ") for line in lines), 4)

    def test_manifest_requires_every_supported_target(self) -> None:
        descriptor = self.root / "incomplete.json"
        descriptor.write_text(
            json.dumps(
                {
                    "version": "0.1.0",
                    "published_at": "2026-09-01T00:00:00Z",
                    "artifacts": self._records()[:-1],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "every supported target"):
            load_descriptor(descriptor)

    def test_offline_linux_install_is_versioned_and_atomic(self) -> None:
        result = self._run("linux", "x64", self.linux_artifact, check=True)
        current = self.home / ".local" / "bin" / "reasoning"
        expected = self.home / ".local" / "lib" / "reasoning" / "0.1.0" / "reasoning.AppImage"
        self.assertTrue(current.is_symlink())
        self.assertEqual(current.resolve(), expected.resolve())
        self.assertEqual(expected.read_bytes(), self.linux_artifact.read_bytes())
        self.assertIn("Installed Reasoning 0.1.0", result.stdout)

    def test_online_linux_install_uses_verified_https_channel(self) -> None:
        fake_curl = self.root / "fake-curl"
        fake_curl.write_text(
            """#!/bin/sh
set -eu
output=
url=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --output) output=$2; shift 2 ;;
        https://*) url=$1; shift ;;
        *) shift ;;
    esac
done
case "$url" in
    */release.manifest) source=$REASONING_FIXTURE_ROOT/release.manifest ;;
    */release.manifest.sig) source=$REASONING_FIXTURE_ROOT/release.manifest.sig ;;
    *.AppImage) source=$REASONING_FIXTURE_ARTIFACT ;;
    *) exit 91 ;;
esac
cp "$source" "$output"
""",
            encoding="utf-8",
        )
        fake_curl.chmod(0o755)
        prefix = self.root / "online-prefix"
        environment = {
            **os.environ,
            "HOME": str(self.home),
            "PATH": str(self.test_bin) + os.pathsep + os.environ["PATH"],
            "REASONING_FIXTURE_OS": "linux",
            "REASONING_FIXTURE_ARCH": "x64",
            "REASONING_CURL": str(fake_curl),
            "REASONING_FIXTURE_ROOT": str(self.root),
            "REASONING_FIXTURE_ARTIFACT": str(self.linux_artifact),
        }
        result = subprocess.run(
            [str(self.installer), "--prefix", str(prefix)],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        current = prefix / "bin" / "reasoning"
        self.assertTrue(current.is_symlink())
        self.assertIn("Installed Reasoning 0.1.0", result.stdout)

    def test_offline_macos_install_is_versioned_and_atomic(self) -> None:
        self._run("darwin", "arm64", self.macos_artifact, check=True)
        current = self.home / "Applications" / "Reasoning.app"
        expected = self.home / "Applications" / "Reasoning Releases" / "0.1.0" / "Reasoning.app"
        self.assertTrue(current.is_symlink())
        self.assertEqual(current.resolve(), expected.resolve())
        self.assertTrue((expected / "Contents" / "MacOS" / "reasoning").is_file())

    def test_macos_upgrade_atomically_replaces_current_app_symlink(self) -> None:
        self._run("darwin", "arm64", self.macos_artifact, check=True)
        current = self.home / "Applications" / "Reasoning.app"
        prior = current.resolve()
        self._write_manifest(version="0.1.1")
        self._sign()

        self._run("darwin", "arm64", self.macos_artifact, check=True)

        expected = self.home / "Applications" / "Reasoning Releases" / "0.1.1" / "Reasoning.app"
        self.assertTrue(current.is_symlink())
        self.assertEqual(current.resolve(), expected.resolve())
        self.assertNotEqual(current.resolve(), prior)
        self.assertEqual(list(prior.glob(".reasoning-current.*")), [])

    def test_tampered_manifest_is_refused_before_install(self) -> None:
        self.manifest.write_text(
            self.manifest.read_text(encoding="utf-8").replace("0.1.0", "0.1.1"),
            encoding="utf-8",
        )
        result = self._run("linux", "x64", self.linux_artifact)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("signature verification failed", result.stderr)
        self.assertFalse((self.home / ".local").exists())

    def test_wrong_artifact_preserves_existing_current_link(self) -> None:
        self._run("linux", "x64", self.linux_artifact, check=True)
        current = self.home / ".local" / "bin" / "reasoning"
        original = os.readlink(current)
        damaged = self.root / "damaged.AppImage"
        damaged.write_bytes(self.linux_artifact.read_bytes() + b"tamper")
        result = self._run("linux", "x64", damaged)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("size mismatch", result.stderr)
        self.assertEqual(os.readlink(current), original)

    def test_wrong_digest_with_same_size_is_refused(self) -> None:
        damaged = self.root / "same-size.AppImage"
        damaged.write_bytes(b"X" * len(self.linux_artifact.read_bytes()))
        result = self._run("linux", "arm64", damaged)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SHA-256 mismatch", result.stderr)

    def test_manifest_duplicate_target_is_refused_even_when_signed(self) -> None:
        line = next(line for line in self.manifest.read_text(encoding="utf-8").splitlines() if line.startswith("artifact linux x64 "))
        with self.manifest.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
        self._sign()
        result = self._run("linux", "x64", self.linux_artifact)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("malformed, ambiguous", result.stderr)

    def test_archive_traversal_is_refused_and_current_is_unchanged(self) -> None:
        unsafe = self.root / "unsafe.zip"
        with zipfile.ZipFile(unsafe, "w") as archive:
            self._write_zip_file(
                archive,
                "Reasoning.app/Contents/MacOS/reasoning",
                "ok",
                executable=True,
            )
            self._write_zip_file(archive, "../escape", "bad")
        records = self._records()
        for record in records:
            if record["os"] == "darwin":
                record["path"] = str(unsafe)
        self._write_manifest(records)
        self._sign()
        result = self._run("darwin", "arm64", unsafe)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe or non-canonical paths, entry types, duplicates, or expanded size", result.stderr)
        self.assertFalse((self.home / "Applications" / "Reasoning.app").exists())

    def test_archive_symlink_entry_is_refused_before_extraction(self) -> None:
        unsafe = self.root / "symlink.zip"
        link = zipfile.ZipInfo("Reasoning.app")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(unsafe, "w") as archive:
            archive.writestr(link, "/Applications/Calculator.app")
        records = self._records()
        for record in records:
            if record["os"] == "darwin":
                record["path"] = str(unsafe)
        self._write_manifest(records)
        self._sign()
        result = self._run("darwin", "arm64", unsafe)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe or non-canonical paths, entry types, duplicates, or expanded size", result.stderr)
        self.assertFalse((self.home / "Applications" / "Reasoning.app").exists())

    def test_archive_duplicate_entry_is_refused_before_extraction(self) -> None:
        duplicate = self.root / "duplicate.zip"
        with zipfile.ZipFile(duplicate, "w") as archive:
            self._write_zip_file(archive, "Reasoning.app/Contents/Info.plist", "plist")
            self._write_zip_file(
                archive,
                "Reasoning.app/Contents/MacOS/reasoning",
                "first",
                executable=True,
            )
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                self._write_zip_file(
                    archive,
                    "Reasoning.app/Contents/MacOS/reasoning",
                    "second",
                    executable=True,
                )
        records = self._records()
        for record in records:
            if record["os"] == "darwin":
                record["path"] = str(duplicate)
        self._write_manifest(records)
        self._sign()
        result = self._run("darwin", "x64", duplicate)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe or non-canonical paths, entry types, duplicates, or expanded size", result.stderr)

    def test_archive_dot_segment_alias_is_refused_before_extraction(self) -> None:
        duplicate = self.root / "dot-segment-duplicate.zip"
        with zipfile.ZipFile(duplicate, "w") as archive:
            self._write_zip_file(archive, "Reasoning.app/Contents/Info.plist", "plist")
            self._write_zip_file(
                archive,
                "Reasoning.app/Contents/MacOS/reasoning",
                "first",
                executable=True,
            )
            self._write_zip_file(
                archive,
                "Reasoning.app/Contents/MacOS/./reasoning",
                "second",
                executable=True,
            )
        records = self._records()
        for record in records:
            if record["os"] == "darwin":
                record["path"] = str(duplicate)
        self._write_manifest(records)
        self._sign()

        result = self._run("darwin", "x64", duplicate)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe or non-canonical paths, entry types, duplicates, or expanded size", result.stderr)

    def test_archive_case_folded_alias_is_refused_before_extraction(self) -> None:
        duplicate = self.root / "case-folded-duplicate.zip"
        with zipfile.ZipFile(duplicate, "w") as archive:
            self._write_zip_file(archive, "Reasoning.app/Contents/Info.plist", "plist")
            self._write_zip_file(
                archive,
                "Reasoning.app/Contents/MacOS/reasoning",
                "fixture",
                executable=True,
            )
            self._write_zip_file(archive, "Reasoning.app/Contents/Resources/Manual.txt", "first")
            self._write_zip_file(archive, "Reasoning.app/Contents/Resources/manual.txt", "second")
        records = self._records()
        for record in records:
            if record["os"] == "darwin":
                record["path"] = str(duplicate)
        self._write_manifest(records)
        self._sign()

        result = self._run("darwin", "arm64", duplicate)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe or non-canonical paths, entry types, duplicates, or expanded size", result.stderr)

    def test_archive_empty_path_segment_is_refused_before_extraction(self) -> None:
        unsafe = self.root / "empty-segment.zip"
        with zipfile.ZipFile(unsafe, "w") as archive:
            self._write_zip_file(archive, "Reasoning.app/Contents/Info.plist", "plist")
            self._write_zip_file(
                archive,
                "Reasoning.app/Contents//MacOS/reasoning",
                "fixture",
                executable=True,
            )
        records = self._records()
        for record in records:
            if record["os"] == "darwin":
                record["path"] = str(unsafe)
        self._write_manifest(records)
        self._sign()

        result = self._run("darwin", "arm64", unsafe)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe or non-canonical paths, entry types, duplicates, or expanded size", result.stderr)

    def test_archive_non_ascii_raw_name_is_refused_before_extraction(self) -> None:
        unsafe = self.root / "non-ascii-name.zip"
        with zipfile.ZipFile(unsafe, "w") as archive:
            self._write_zip_file(archive, "Reasoning.app/Contents/Info.plist", "plist")
            self._write_zip_file(
                archive,
                "Reasoning.app/Contents/MacOS/reasoning",
                "fixture",
                executable=True,
            )
            self._write_zip_file(archive, "Reasoning.app/Contents/Resources/Résumé.txt", "unsafe")
        records = self._records()
        for record in records:
            if record["os"] == "darwin":
                record["path"] = str(unsafe)
        self._write_manifest(records)
        self._sign()
        extraction_marker = self.root / "extraction-attempted"
        self._write_test_command(
            "unzip",
            """#!/bin/sh
if [ "${1:-}" = -q ]; then : > "$REASONING_EXTRACTION_MARKER"; fi
exec /usr/bin/unzip "$@"
""",
        )

        result = self._run(
            "darwin",
            "arm64",
            unsafe,
            environment_overrides={"REASONING_EXTRACTION_MARKER": str(extraction_marker)},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe raw names or malformed central-directory entries", result.stderr)
        self.assertFalse(extraction_marker.exists())

    def test_archive_unicode_path_extra_field_is_refused_before_extraction(self) -> None:
        unsafe = self.root / "unicode-path-extra.zip"
        with zipfile.ZipFile(unsafe, "w") as archive:
            self._write_zip_file(archive, "Reasoning.app/Contents/Info.plist", "plist")
            self._write_zip_file(
                archive,
                "Reasoning.app/Contents/MacOS/reasoning",
                "fixture",
                executable=True,
            )
            entry = zipfile.ZipInfo("Reasoning.app/Contents/Resources/Manual.txt")
            entry.create_system = 3
            entry.external_attr = (stat.S_IFREG | 0o644) << 16
            entry.extra = struct.pack("<HH", 0x7075, 1) + b"x"
            archive.writestr(entry, "unsafe")
        records = self._records()
        for record in records:
            if record["os"] == "darwin":
                record["path"] = str(unsafe)
        self._write_manifest(records)
        self._sign()
        extraction_marker = self.root / "extraction-attempted"
        self._write_test_command(
            "unzip",
            """#!/bin/sh
if [ "${1:-}" = -q ]; then : > "$REASONING_EXTRACTION_MARKER"; fi
exec /usr/bin/unzip "$@"
""",
        )

        result = self._run(
            "darwin",
            "x64",
            unsafe,
            environment_overrides={"REASONING_EXTRACTION_MARKER": str(extraction_marker)},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe raw names or malformed central-directory entries", result.stderr)
        self.assertFalse(extraction_marker.exists())

    def test_term_after_stage_commit_preserves_prior_link_and_stops_activation(self) -> None:
        self._run("darwin", "arm64", self.macos_artifact, check=True)
        current = self.home / "Applications" / "Reasoning.app"
        original = os.readlink(current)
        self._write_manifest(version="0.1.1")
        self._sign()
        release_target = self.home / "Applications" / "Reasoning Releases" / "0.1.1"
        result = self._run(
            "darwin",
            "arm64",
            self.macos_artifact,
            environment_overrides={"REASONING_SIGNAL_RELEASE_TARGET": str(release_target)},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(os.readlink(current), original)
        releases = self.home / "Applications" / "Reasoning Releases"
        self.assertEqual(list(releases.glob(".reasoning-stage.*")), [])
        self.assertEqual(list(releases.glob(".reasoning-release-*.lock")), [])

    def test_release_directory_collision_never_activates_nested_stage(self) -> None:
        release_target = self.home / ".local" / "lib" / "reasoning" / "0.1.0"
        result = self._run(
            "linux",
            "x64",
            self.linux_artifact,
            environment_overrides={"REASONING_COLLIDE_RELEASE_TARGET": str(release_target)},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("release directory collision detected", result.stderr)
        self.assertFalse((self.home / ".local" / "bin" / "reasoning").exists())
        self.assertEqual(list(release_target.glob(".reasoning-stage.*")), [])

    def test_preexisting_version_symlink_is_never_reused(self) -> None:
        releases = self.home / ".local" / "lib" / "reasoning"
        releases.mkdir(parents=True)
        external = self.root / "external-release"
        external.mkdir()
        (releases / "0.1.0").symlink_to(external, target_is_directory=True)
        result = self._run("linux", "x64", self.linux_artifact)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("release path is not a regular directory", result.stderr)
        self.assertFalse((self.home / ".local" / "bin" / "reasoning").exists())

    def test_print_plan_verifies_without_installing(self) -> None:
        artifact = self.linux_artifacts["arm64"]
        result = self._run("linux", "arm64", artifact, "--version", "0.1.0", "--print-plan", check=True)
        self.assertIn("os=linux", result.stdout)
        self.assertIn("arch=arm64", result.stdout)
        self.assertEqual(
            hashlib.sha256(artifact.read_bytes()).hexdigest(),
            next(line.removeprefix("sha256=") for line in result.stdout.splitlines() if line.startswith("sha256=")),
        )
        self.assertFalse((self.home / ".local").exists())

    def test_unsupported_platform_is_refused(self) -> None:
        result = self._run("windows", "x64", self.linux_artifact)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("supports macOS and Linux only", result.stderr)

    def test_insecure_online_override_is_refused_before_curl(self) -> None:
        environment = {
            **os.environ,
            "HOME": str(self.home),
            "PATH": str(self.test_bin) + os.pathsep + os.environ["PATH"],
            "REASONING_FIXTURE_OS": "linux",
            "REASONING_FIXTURE_ARCH": "x64",
            "REASONING_CURL": "/usr/bin/false",
        }
        result = subprocess.run(
            [
                str(self.installer),
                "--manifest-url",
                "http://release.test/release.manifest",
                "--signature-url",
                "https://release.test/release.manifest.sig",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must use HTTPS", result.stderr)

    def test_oversized_manifest_is_refused_before_signature_work(self) -> None:
        self.manifest.write_bytes(b"x" * (1_048_576 + 1))
        result = self._run("linux", "x64", self.linux_artifact)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("release manifest exceeds", result.stderr)

    def test_template_has_no_downloaded_code_execution_escape_hatch(self) -> None:
        template = (RELEASE / "install.sh.in").read_text(encoding="utf-8")
        self.assertNotIn("eval ", template)
        self.assertNotIn("source ", template)
        self.assertNotIn(". $", template)
        self.assertIn("ssh-keygen -Y verify", template)
        self.assertLess(template.index("verify_signature"), template.index("select_record"))
        self.assertNotIn("TEST_MODE", template)
        self.assertNotIn("REASONING_TEST_", template)

    def test_renderer_has_no_validation_bypass_mode(self) -> None:
        with self.assertRaises(TypeError):
            render(
                self.public_key,
                "https://release.test/release.manifest",
                "https://release.test/release.manifest.sig",
                test_mode=True,
            )

    def test_public_key_and_urls_are_strict(self) -> None:
        with self.assertRaises(ValueError):
            read_public_key(self.key)
        with self.assertRaises(ValueError):
            render("ssh-ed25519 bad'key", "https://good.test/m", "https://good.test/s")
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            render(self.public_key, "http://bad.test/m", "https://good.test/s")
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            render(self.public_key, "https://good.test/*", "https://good.test/s")

    def test_linux_appimage_marker_and_architecture_are_enforced(self) -> None:
        missing_marker = self.root / "missing-marker.AppImage"
        content = bytearray(self.linux_artifacts["x64"].read_bytes())
        content[8:11] = b"bad"
        missing_marker.write_bytes(content)
        records = self._records()
        next(record for record in records if record["os"] == "linux" and record["arch"] == "x64")["path"] = str(missing_marker)
        self._write_manifest(records)
        self._sign()
        result = self._run("linux", "x64", missing_marker)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AppImage type-2 marker", result.stderr)

        records = self._records()
        next(record for record in records if record["os"] == "linux" and record["arch"] == "x64")["path"] = str(
            self.linux_artifacts["arm64"]
        )
        self._write_manifest(records)
        self._sign()
        result = self._run("linux", "x64", self.linux_artifacts["arm64"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("architecture does not match", result.stderr)

    def test_macos_selected_architecture_is_enforced(self) -> None:
        result = self._run(
            "darwin",
            "arm64",
            self.macos_artifact,
            environment_overrides={"REASONING_FIXTURE_APP_ARCHS": "x86_64"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not contain the selected arm64 architecture", result.stderr)

    def test_signed_manifest_url_glob_is_refused_without_expansion(self) -> None:
        text = self.manifest.read_text(encoding="utf-8")
        text = text.replace(
            "https://release.test/Reasoning-0.1.0-x64.AppImage",
            "https://release.test/*",
        )
        self.manifest.write_text(text, encoding="utf-8")
        self._sign()
        result = self._run("linux", "x64", self.linux_artifact)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("malformed, ambiguous", result.stderr)

    def test_stable_path_must_not_replace_an_existing_user_file(self) -> None:
        bin_dir = self.home / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        current = bin_dir / "reasoning"
        current.write_text("user-owned", encoding="utf-8")
        result = self._run("linux", "x64", self.linux_artifact)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("is not a symbolic link", result.stderr)
        self.assertEqual(current.read_text(encoding="utf-8"), "user-owned")


if __name__ == "__main__":
    unittest.main()
