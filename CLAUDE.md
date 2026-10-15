@AGENTS.md

## Claude Code

### Waiting on long-running work

Never end your turn to "wait for" an external event (a CI job, a
build or long test run you launched, a spawned session). No
notification will fire unless the harness itself is tracking a
background command you started — stopping with nothing tracked strands
the session until a human notices. Tie every wait to a process the
harness tracks, one of:

- a **blocking foreground command** — a sleep-and-recheck loop inside a
  single Bash call, with an explicit timeout and a defined
  give-up-and-report action; or
- a **`run_in_background` Bash command** — its exit re-invokes you.

Never detach work (`&`, `nohup`, fire-and-forget server-side jobs) and
then stop. If your final message would say "waiting for X" — don't
stop; poll.
