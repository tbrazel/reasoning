# Maintainer release process

Reasoning is distributed as source for users to build and deploy. This process does not replace the
real-system gates in [release status](release-status.md).

## Prepare

1. Start from `main` with no uncommitted or untracked files.
2. Choose a Semantic Versioning version and update `pyproject.toml`,
   `web/package.json`, `desktop/package.json`, their lockfiles, and the FastAPI
   version in `src/reasoning_ledger/server/api.py`.
3. Move the relevant `CHANGELOG.md` entries into a dated version section.
4. Update current documentation and the release-status evidence. Do not turn an
   unrun real-provider gate into a passing claim.
5. Regenerate lockfiles only with the repository's package managers and review
   dependency and license changes.
6. Review fetched history and the exact tracked candidate before making source
   public. The [public source audit](../.github/workflows/public-source-audit.yml)
   scans fetched Git refs with checksum-pinned Gitleaks and exact reviewed
   non-secret value/path pairs in `.gitleaks.toml`. Default rules remain enabled;
   changed values and other paths remain scanned. Also review private-data exposure and dependency
   advisories; neither is established by a passing credential scan.

## Verify

```sh
make check
```

The ordinary Python suite includes the first-organization deployment fixtures;
`uv run pytest --deployment -q` is the faster focused selection for deployment
work. Optional scientific campaigns remain separately selected with `--science`.
Run applicable browser, installed-desktop, provider, compute-image, and real-host
gates listed by the changed components. Record what actually ran in the release
notes.

Commit the final source, then construct the release from that clean commit:

```sh
make build
make inspect
```

The bundle inspection must report `dirty: false`, the intended source revision,
the current database schema, and the expected installation features. Retain the
bundle SHA-256 and release ID; they are different identities.

## Publish

1. Create a signed tag matching the version.
2. Publish source with license/notice material. Users build their server bundle
   and desktop clients themselves; no Python package or desktop binary publication is required.
3. Publish release notes containing upgrade instructions, data/schema changes,
   compatibility notes, known limitations, and completed acceptance gates.
4. Exercise the documented clone, build, deploy, upgrade, and bot workflows from a fresh checkout before announcing the release.
5. Enable GitHub private vulnerability reporting when the repository becomes
   public and confirm the path described in `SECURITY.md` works.

## Tag verification

Release tags use SSH signatures. Download `release-signers` from the release
assets through GitHub, inspect its signer identity and public key, and verify:

```sh
git -c gpg.format=ssh -c gpg.ssh.allowedSignersFile=/path/to/release-signers tag --verify v0.1.0
```

The public key is not an SSH login credential. Keep the signing private key
outside the repository, restricted to the maintainer. A valid signature binds
the tag to that key; verify the key through the project's trusted GitHub release
or an independent maintainer channel. This does not imply GitHub-account key
registration or a reproducible build attestation.

Do not publish provider credentials, operator state, SSH material, owner inputs,
backup keys, setup tokens, private catalogs, or live database contents.
