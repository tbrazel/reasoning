# Security policy

## Supported versions

Security fixes target version 0.1.0 and the current `main` branch. Older revisions
may receive no security updates. Release notes specify support for later versions.

## Report a vulnerability privately

Do not open a public issue for a suspected vulnerability or include secrets,
personal data, exploit details, or live host information in public discussion.

Use GitHub's **Security → Report a vulnerability** flow for this repository. If
that option is unavailable, privately contact a maintainer identified in
[GOVERNANCE.md](GOVERNANCE.md) through the contact method on their GitHub
profile. You may ask publicly for a private contact channel, but do not disclose
the vulnerability in that request.

Include, when available:

- the affected revision or release;
- the component and deployment model;
- reproduction steps or a minimal proof of concept;
- the security impact and required privileges; and
- whether any credential, provider account, or live installation may be exposed.

Maintainers will acknowledge the report, establish a private remediation path,
and coordinate disclosure after a fix is available. Response times are best
effort until the project publishes a formal service-level policy.

## Credential exposure

If a DigitalOcean, OpenRouter, Modal, Resend, bot, worker, scaler, session, or
SSH credential may have leaked, revoke or rotate it at its authority first. Do
not wait for a code fix. Preserve non-secret receipts and logs needed to
reconcile uncertain cloud or billing effects.

The project cannot revoke credentials or recover provider charges on an
operator's behalf.

## Scope

Security reports can cover the public server, deployment tooling, upgrade and
recovery paths, desktop client, browser application, bots, compute workers,
contracts, or the local workbench. Vulnerabilities in third-party services
should also be reported to the affected provider.
