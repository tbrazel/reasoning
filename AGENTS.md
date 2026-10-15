## Living normative documents stay cruft-free and fresh
Docs outside `docs/planning/` are living normative documents
— AGENTS.md, files, READMEs, etc.: they describe how things ARE, and code/docs
change against them atomically. Such a document must not accumulate narrative
about its own past: no change/supersession notes ("X was removed on <date>",
"formerly known as", "added by the Y rework"), no references to no-longer-extant
code or codenames, no provenance asides ("ported from Z"). Git history and
`docs/planning/` carry that record; the living doc concisely conveys
the current state of things and nothing else. (Planning docs are the
opposite: dated, append-friendly records whose status lines must say
honestly where reality has moved past them.)

The same documents must stay **fresh and code-verified**: a living doc
describes how the code IS *now*, so update it in the change that moves the
code, and verify its claims (counts, tables, type/module names, API, build
commands) against the source — never against a plan. Prefer **distilling**
normative content or genuine planned work into the right living doc over
pointing at a planning doc; a specific planning-file reference from a living
doc is allowed only for deep design/measurement detail with no living home.

The same discipline extends to **PR descriptions and commit messages**: a
reviewer should understand them without opening a planning doc, so any
codename used there carries its explanation inline — reference the relevant
planning docs, but as a supplement, never as the explanation itself.

## Instructions for Codex only
If you are Codex, end any commits with the line `Co-Authored-By: Codex (GPT [version]) <codex@openai.com>`.
Replace [version] with your version, e.g. "5.6-Sol". If you do not know your version, omit the parenthetical.
