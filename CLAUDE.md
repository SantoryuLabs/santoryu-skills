# Engineering Standards

Keep this file short. A rule earns its place only if it changes behavior.
Delete anything that stops being true.

**NEVER-STASH-CODE-FOR-TESTING-THERE-MIGHT-BE-ANOTHER-AGENTS**

## Environments

- Host shell is Windows: chain commands with `;` (not `&&`); no `head` — use
  `Select-Object -First N` or run the command inside the container.
- Everything that ships runs in Linux containers: bash syntax, POSIX paths (`/`),
  LF line endings, case-sensitive filenames.
- Rule of thumb: run by the developer on the host → Windows rules; runs inside or
  ships into a container → Linux rules. A fix that "works" on Windows must also
  hold inside the container.

## Core Principles

Simplicity > cleverness. Robustness > speed of delivery. Correctness > convenience.

- Prefer the simplest design that solves the problem. If a change adds complexity
  it doesn't need, reject it.
- Every abstraction and every dependency must justify its existence.
- Explicit over hidden: no silent assumptions, no implicit coupling, no magic.
- Measure before optimizing; never optimize speculatively.

## Root Cause, Not Workarounds

Before writing any fix, answer one question: **does this stop the bad state from
occurring, or does it only react to it after the fact?**

- A null check, try/catch, early return, or fallback wrapped around a symptom is
  a guard, not a fix. Fix the source — even if it lives in a different module
  than where the symptom appeared.
- A guard is acceptable only to enforce a real invariant ("this state must be
  unreachable"), never to paper over upstream logic.
- Sole exception: an urgent production mitigation. Label it temporary in the
  change itself and commit to the root-cause fix as the immediate next task.

## Investigate Before Changing

- Map the blast radius first: call sites, consumers, shared state, existing tests.
  No code changes during this phase.
- When Santoryu is in play, this investigation IS Cursor's plan run — review and
  spot-check its findings instead of mapping the blast radius yourself.
- If the problem turns out broader or narrower than reported, say so before
  touching code — never silently expand or shrink scope.

## Act Like a Senior Reviewer

If a request is flawed or unnecessary, say so — with the specific reason and a
concrete alternative. Don't refuse without a reason; don't comply silently.

## Planning

- Build a fresh plan each time, scoped to the current topic only. Don't carry
  over or restate details from a previous version of the plan.

## Code Quality

- Production standard from the first line: real naming, real error handling,
  no placeholders. "Make it work, then make it right" is not permitted.
- Remove dead code and commented-out blocks immediately.
- Fail loudly in development. In production: log with enough context to diagnose,
  return structured errors, never leak internals (stack traces, secrets, raw DB
  errors) to clients.

## Security

- No secrets in code or repo — environment variables / secrets manager only.
  Never log secrets, tokens, or PII.
- Validate all external input at the boundary before it reaches domain logic.
- Authorization is enforced server-side on every protected route.

## Testing & Gates

- Lint must pass: no ignored warnings, no disabled rules without justification.
- Always unit-test pure domain logic. Always add a regression test when fixing a
  bug — it must fail on the old code and pass on the fix.
- Don't test framework render details, third-party libraries, or throwaway
  scripts. Don't chase coverage — test the risky, the pure, and the just-fixed.

## Version Control

- Never commit. Not even when the work is done and reviewed — leave changes in the
  working tree and let the user commit. Only run `git commit` if the user asks for
  it in that same request, in plain words. "Fix X", "done", or a prior "you can
  commit" is not standing permission.

## Large JSON

- Never `Read`/`cat` a JSON that is large, minified, or of unknown size — one
  minified line can be megabytes, so a partial read still pulls all of it. Use the
  `query-json` command with `summary` / `find` / `list` / `get` / `grep`.

## Santoryu (Cursor + Opus orchestration)

usage: santoryu cursor [-h] [--list-models]
[--prompt PROMPT | --prompt-file PROMPT_FILE]
[--mode {plan,agent}] [--model MODEL] [--fast]
[--effort {low,medium,high}] [--cwd CWD] [--sandbox]
[--no-sandbox] [--no-guard]

- BINDING for repo work: any bug, feature, or refactor investigation STARTS with
  a Cursor plan run via the santoryu skill — follow its workflow exactly. Do NOT
  substitute Claude Code's own exploration subagents (Task / Explore agents),
  parallel greps, or speculative file reads. If `santoryu` fails, report the
  error and stop; no silent fallback. Sole exception: the user already pinpointed
  the exact change — skip the plan run and say so out loud.
- Hand Cursor the whole problem in ONE prompt: symptom + expected behavior,
  asking for findings -> root cause -> plan. No pre-decomposed search prompts
  ("find the menu code", "find the translation code"); deciding where to look is
  Cursor's job. file:line references are evidence inside the findings, never the
  deliverable.
- Opus owns the plan and the diff: review, refine, re-run Cursor with a sharper
  prompt if the analysis is thin — never rubber-stamp. The no-commit rule
  applies to Cursor's runs too.
- Fixing the _approach_ is not the same as fixing the _bug_. When the user is
  correcting how you work, change the behavior/rule — don't go execute the task.
