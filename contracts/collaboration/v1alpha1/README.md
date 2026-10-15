# Public collaboration and contributed-runner contracts

This family defines the credential-free boundary between Reasoning Cloud and an
authenticated Desktop participating in one or more organizations. Every account
has one personal organization and may also belong to shared organizations. The
bootstrap record provides the exact organizations, effective roles and
permissions, and visible workspaces that a user may switch between.

Workspaces contain durable topics. Message pages are cursor-based, ordered, and
append-only; human conversation, assistant output, workflow status, and system
notices share the same visible history without making chat text the authority
for workflow or execution state.

Shared inference operations expose the full prompt, selected local or metered
route, assignment, progress, and provider-reported usage to workspace members.
This visible record is the execution authority; an assistant message is only the
durable conversation result.

A Desktop may explicitly contribute a local runner to an organization. The
runner advertises bounded capabilities and a local policy. Cloud can offer it a
short-lived lease containing a credential-free request, but provider login,
Codex state, filesystem authority, and approval decisions remain on the
contributor's machine. A lease is not transferable between runners or
organizations, and its request digest binds the exact execution request.

Organization conversation visibility and expensive execution authority remain
separate permissions. Ordinary workspace access never implies the ability to
contribute compute, run a swarm, deploy infrastructure, manage billing, or
change roles.
