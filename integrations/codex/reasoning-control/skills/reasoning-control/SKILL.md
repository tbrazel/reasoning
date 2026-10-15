---
name: reasoning-control
description: "Operate a running Reasoning workbench through its control MCP: create projects and chats, run or steer Codex and native turns, run workflows, wait across long operations, inspect ledgers, artifacts, and usage, and recover safely from failures. Use for autonomous or unattended mathematical-research campaigns, Reasoning-versus-Codex comparisons, and any request to use Reasoning without driving its browser interface."
---

# Reasoning Control

Treat Reasoning as the durable source of research state. Keep orchestration in
the outer Codex session and put mathematical claims, evidence, computations,
reviews, and reports in the Reasoning project.

## Start or resume work

1. Call `reasoning_status`. Stop if the requested executor is not ready.
2. Call `reasoning_project_list` before creating anything. Reuse the named
   campaign unless the task explicitly calls for isolation.
3. Create separate chats for distinct providers or independent reviewers.
   A Codex thread is provider-bound and must never be switched in place.
4. Start a bounded turn and retain its `operation_id` immediately.
5. Wait with a finite timeout. The wait survives loss of the browser or SSH
   tunnel and returns durable checkpoints and transcript entries.
6. Read the ledger, artifacts, and usage after every terminal stage before
   deciding the next action.

Use these explicit routes:

- Codex with ChatGPT authentication: `executor_id=codex`,
  `model_provider=openai`.
- Codex through OpenRouter: `executor_id=codex`,
  `model_provider=openrouter`.
- Reasoning Native through OpenRouter: `executor_id=reasoning-native`,
  `model_provider=openrouter`.

## Boundaries and recovery

- Do not call a generated construction a counterexample. Require a locked
  statement and negation, complete witness, checked hypotheses, attached
  evidence, independent verification, and established ledger support closure.
- Distinguish `failed`, `interrupted`, `timed_out`, `approval_needed`, and
  `busy`. Never hide one as an empty successful result.
- After `interrupted`, inspect the transcript, ledger, and artifacts before
  starting a recovery turn. Do not blindly repeat a possibly billable call.
- After `approval_needed`, either use a route whose sandbox policy does not
  require that action or ask the user. Do not broaden permissions implicitly.
- Use `reasoning_turn_steer` only to change an active turn. Start a new turn for
  additional work after completion.
- Interrupt or cancel live work only when the user requests it or when a
  stated campaign budget or stop condition requires it.

## Comparative research

For native-versus-Codex evaluation, hold the statement, sources, requested
output schema, and budgets fixed. Use independent chats, record executor,
provider, requested model, usage, failures, and intervention count, then place
the comparison report in project artifacts. Prefer replayable failures over
general impressions when proposing a Reasoning improvement.

## Unattended loop

Repeat only while the campaign has a bounded next action and a stop condition.
After each iteration, persist surviving candidates, eliminated candidates with
reasons, missing evidence or tools, and the next experiment. Stop broad search
when no new candidate, invariant, source, or tool gap appears; spend the
remaining budget on verification and a self-contained report.
