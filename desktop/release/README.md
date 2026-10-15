# Verified desktop release bootstrap

This directory defines the source of the macOS/Linux curl installer. No
production key, signed package, publication channel, or release is present in
the repository. Reasoning clients are built from source; the HTTPS URLs below
are operator examples, not a project-provided download service.

## Trust chain

1. The release owner generates an offline Ed25519 signing key and protects the
   private key outside the repository and CI logs.
2. `make_manifest.py` hashes clean-build artifacts and emits one deterministic,
   line-oriented manifest record for each supported OS/architecture.
3. The release owner signs the exact manifest bytes with the OpenSSH signature
   namespace `reasoning-desktop`.
4. `render_installer.py` embeds only the public key plus credential-free HTTPS
   manifest/signature URLs in `install.sh`.
5. The installer downloads and verifies the manifest signature before parsing
   any release field, then verifies the selected artifact's exact size and
   SHA-256 before an atomic, versioned user-level installation.

The online bootstrap is intended to be published at a stable HTTPS URL and used
as:

```sh
curl --proto '=https' --tlsv1.2 -fsSL https://downloads.example.org/install.sh | sh
```

Piping a script to `sh` still relies on HTTPS for the installer source. The
embedded trust root independently protects the release manifest and package
channel. Users needing a fully offline or separately audited flow download the
installer, signed manifest, signature, and artifact first, inspect the script,
then run:

```sh
./install.sh --offline \
  --manifest release.manifest \
  --signature release.manifest.sig \
  --artifact Reasoning-package \
  --print-plan
```

Remove `--print-plan` to install. macOS uses a versioned ZIP containing only
`Reasoning.app`; before extraction the installer rejects traversal,
non-canonical or case-colliding names, links, special entries, excessive entry
counts, unsupported ZIP metadata, and excessive expanded size. Raw
central-directory names must be printable ASCII, so locale replacement cannot
hide a path from validation. It then verifies the selected executable
architecture plus Gatekeeper/code-signing state. Linux uses a versioned
AppImage whose signed manifest, ELF class, AppImage marker, selected machine
architecture, size, and digest are verified. Per-version and stable-link locks
serialize concurrent installers; stable launch links move atomically only
after validation and staging succeed. A caught termination exits nonzero and
leaves the prior stable link unchanged unless the new link had already
committed successfully.

## Manifest and signing

Prepare a JSON descriptor with `version`, UTC `published_at`, and artifact
records containing `os`, `arch`, `kind`, local `path`, and public HTTPS `url`.
The four supported pairs are macOS (`darwin`) and Linux on `arm64` and `x64`;
their kinds are `macos-zip` and `linux-appimage` respectively.

```sh
python3 desktop/release/make_manifest.py release-descriptor.json \
  --output release.manifest

ssh-keygen -Y sign -f /secure/offline/reasoning-release-ed25519 \
  -n reasoning-desktop release.manifest

python3 desktop/release/render_installer.py \
  --public-key /secure/offline/reasoning-release-ed25519.pub \
  --manifest-url https://downloads.example.org/release.manifest \
  --signature-url https://downloads.example.org/release.manifest.sig \
  --output install.sh
```

The private key must never be passed to `render_installer.py`, stored in the
repository, or exposed to an ordinary packaging worker.

The renderer has no test or validation-bypass mode. Tests exercise the same
rendered installer as publication by substituting platform tools through an
isolated fixture `PATH`; no bypass flag or environment variable is compiled
into the output.

## Release gates still required

- Build every package from the exact clean release candidate on its target OS and
  architecture; retain toolchain and dependency provenance.
- Sign and notarize macOS artifacts with protected Apple credentials and verify
  Gatekeeper after download.
- Produce Linux AppImage and DEB artifacts; add distro/package signing if DEB is
  published outside the signed-manifest channel.
- Run the installed-app journey on macOS arm64/x64 and representative Linux
  Wayland/X11 systems.
- Publish manifest, signature, packages, and installer atomically to immutable
  version paths before updating the stable channel.
- Exercise rollback by restoring the prior signed manifest and stable installer;
  never rewrite an already published version's bytes.
- Keep an offline recovery key and publish a separately reviewed key-rotation
  installer containing both old and new trust roots during the compatibility
  window.

Production signing, notarization, upload, stable-channel change, or key rotation
requires a separate explicitly authorized release task.
