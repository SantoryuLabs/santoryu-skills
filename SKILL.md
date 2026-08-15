---
name: santoryu
description: Mandatory orchestration for repo work. Opus plans and reviews while a fast Cursor agent (Composer 2.5 / Grok via the Cursor Python SDK) or an OpenAI-compatible endpoint does the heavy exploration, drafting, and mechanical implementation. Use for ANY investigation of a bug, feature, or refactor in a codebase — a plain bug report is enough to trigger; the user does not need to mention Cursor or santoryu. Also trigger when the user mentions Cursor / Composer / Grok / santoryu, wants grunt work delegated to a faster model, or wants to compare Opus's output against a faster model's.
---

# Santoryu — three-blade orchestration

Three blades, one swordsman. Opus wields two fast blades — a repo-aware Cursor
agent and a raw OpenAI-compatible model — and is itself the third: the mind that
plans, reviews, and decides. A blade never decides.

Opus is accurate but slow; the fast blades are quick but sloppier. The win is not
making any single answer faster — it's cutting the number of _slow_ iterations.
The fast blade absorbs the convergence rounds (seconds each); Opus spends its
time only at the two ends: a sharp spec up front, a real review at the end.

## Hard rules — read before anything else

1. **Repo investigations START with a Cursor plan run.** Any bug, feature, or
   refactor task on a codebase begins with `santoryu.py cursor --mode plan`.
   Do NOT substitute Claude Code's own exploration subagents (Task / Explore
   agents), parallel greps, or speculative file reads for this step — those are
   exactly the habits this skill overrides.
2. **One delegation, not several.** Hand Cursor the whole problem in a single
   prompt. Don't pre-decompose into scoped search prompts ("find the menu
   code", "find the translation code") — deciding where to look is Cursor's
   job. Pre-decomposition keeps the analysis in Opus and demotes Cursor to
   grep.
3. **No silent fallback.** If santoryu.py errors, report the error to the user
   and stop. Never quietly revert to self-exploration.
4. **Sole exception:** a trivial task where the user has already pinpointed the
   exact change (a one-liner, a named typo). Skip the plan run — and say
   explicitly that you're skipping it and why.

## The blades

Both live in one script, `santoryu.py`, as subcommands.

**`santoryu.py cursor`** — runs a real Cursor agent (Composer 2.5 / Grok) with
repo awareness, tools, and the user's Cursor config. Auth is the user's
`CURSOR_API_KEY`. Supports `--mode plan` (explore/audit/plan, no edits) and
`--mode agent` (implement changes). Use this blade whenever the task involves
the actual codebase.

**`santoryu.py fast`** — a one-shot chat call to any OpenAI-compatible endpoint
(xAI/Grok direct, OpenRouter, OpenAI). No repo awareness, no tools — prompt in,
text out. Use it for raw drafting that doesn't need the repo. Requires
`FAST_ENABLED = True` at the top of the script (flip it once if it's off) and
one of `XAI_API_KEY` / `OPENROUTER_API_KEY` / `OPENAI_API_KEY`.

## Install

Register the skill with Claude Code:

```bash
py "C:\Users\faruk\santoryu-cursor\santoryu.py" install
```

This copies `SKILL.md` into `~/.claude/skills/santoryu/`, stamped with the
script's absolute path; the script and its `.env` stay in the repo, so the
Cursor key never lands in the skills dir. Dependencies are NOT installed by this
command — run `pip install -r requirements.txt` yourself. Re-run `install` after
moving the repo (it re-stamps the path). Edit the repo copy of SKILL.md, not the
installed copy — `install` overwrites the installed copy.

## Prerequisites

**`cursor` blade:** `pip install -r requirements.txt` (Python 3.10+), and
`export CURSOR_API_KEY=crsr_...` (User API key from Cursor Dashboard -> API
Keys) — or drop it in the repo-local `.env`. Billing follows the user's own
Cursor plan. Discover which model ids the account has (Grok may appear post-xAI
integration):

```bash
py "C:\Users\faruk\santoryu-cursor\santoryu.py" cursor --list-models
```

Runs on native Windows (no WSL): the `cursor` blade uses the SDK's async API,
sidestepping the sync bridge's `select()`-on-pipe limitation.

**`fast` blade:** `FAST_ENABLED = True` in the script, plus one provider key
(see above). Check `santoryu.py fast --help` for provider/model overrides.

## Workflow (explore -> plan -> review -> apply)

The division of labor: Cursor does the heavy lifting — scans the repo, produces
an audit, hunts for the root cause, and proposes a plan. Opus owns that plan:
analyzes it, refines it, re-runs Cursor if the analysis missed, and decides who
implements.

### 1. Spec + acceptance criteria (Opus)

Read the task. Write a concise spec: deliverable, constraints, and **acceptance
criteria** — a checklist to grade against later. This is load-bearing: clear
criteria make the fast blade's rounds converge toward the right target. Vague
criteria make speed run in the wrong direction. Don't skip this.

### 2. Delegate the heavy lifting (Cursor, plan mode)

**Hand Cursor the PROBLEM, not a search list.** The delegation prompt states the
symptom and expected behavior (plus the spec from step 1) and asks Cursor to
explore on its own and return a diagnosis + plan. Where to look is Cursor's job
to figure out — that is the exploration being delegated.

The litmus test: if the deliverable you're asking for is _code locations_
("find and report where X is handled, with file paths and line numbers"), the
prompt is wrong — rewrite it. File:line references are demanded as _evidence_
inside the findings, never as the deliverable itself. Orienting hints (likely
keywords, subsystem names) are fine as optional extras, never as the task.

Prompt template:

```
Problem: <symptom, as the user reported it>
Expected: <expected behavior>
Spec / acceptance criteria: <from step 1>
Repo: <path>

Explore the repo yourself and return:
1. Findings — what the relevant subsystem(s) actually do today, with
   file:line evidence
2. Root cause — your hypothesis, argued from that evidence
3. Plan — a step-by-step fix proposal (do not edit anything)

Optional hints (don't limit yourself to these): <keywords/areas, if any>
```

```bash
py "C:\Users\faruk\santoryu-cursor\santoryu.py" cursor --prompt-file plan_prompt.txt --mode plan
```

Capture the findings + plan from stdout. (Write prompt files to a scratch
location; on native Windows, a repo-relative file or `%TEMP%` both work.)

**While the plan run is out, Opus does not explore the repo in parallel** — no
Task/Explore subagents, no grep, no speculative file reading. That's the work
being delegated; doing it twice erases the speed win. Opus opens code only in
step 3, to spot-check the specific evidence Cursor cited.

### 3. Review and refine (Opus)

Grade Cursor's findings and plan against your acceptance criteria. Spot-check
the cited evidence — open the exact files/lines Cursor references to verify the
claims — but don't launch your own fresh exploration.

**Spot-checking requires a plan to check.** If the run returned no usable plan
(empty, truncated, or thin output — e.g. a one-line interim note despite a
large `out=` token count on stderr), that is a re-run signal or a runner-bug
report to the user — never a license to explore. "Spot-check first, re-run if
needed" is the wrong order. Check `source=` on the stderr line: if it says
`final` and the text is thin, the runner's conversation fallback may not have
triggered — tell the user before burning another run.

**If Cursor returns questions instead of (or alongside) a plan:** answer them
yourself from the spec and conversation context, embed the answers in a new
prompt, and re-run `--mode plan`. If a question needs the user's judgment
(intent, product decision, preference), ask the user first, then relay. A
question round counts toward the two-round iteration cap.

Mark what's missing, mis-ordered, or risky. If the root-cause analysis looks
wrong or thin, re-run step 2 with a sharper prompt instead of patching over it.
Then rewrite the plan into a **final plan** — this is where Opus's judgment
earns its keep. Never rubber-stamp the fast blade's plan; the review step is
what fuses speed with quality.

### 4. Apply — Opus decides who implements

Rule of thumb:

- **Mechanical + bulky** (many files, repetitive edits, boilerplate) -> delegate
  back to Cursor:
  ```bash
  py "C:\Users\faruk\santoryu-cursor\santoryu.py" cursor --prompt-file final_plan.txt --mode agent
  ```
  Then Opus reviews the diff.
- **Small, subtle, or high-risk** -> Opus implements directly. A round trip
  would cost more than it saves.

By default the runner enables **auto-review** so Cursor's tool calls
(shell/edit/write) are gated, not run blind — Opus stays in control of the
working tree. Use `--sandbox` for stricter confinement, or `--no-guard` to
disable gating (not recommended). The no-commit rule applies to Cursor's runs
too: no `git commit` unless the user asked in that same request.

### 5. Final review (Opus)

Opus does the last read against the acceptance criteria and delivers. Ship
Opus's judgment, never the fast blade's raw output.

## Iteration discipline

Cap the loop. If two fast-blade rounds don't converge on a criterion, Opus
finishes that part itself — endless round trips defeat the purpose. As Cursor's
models improve, both the number of convergence rounds and the amount Opus has to
fix trend down, so this skill gets more favorable over time.

## When NOT to delegate

Only the trivial-task exception in the Hard rules: the user has already
pinpointed the exact change and a plan run would cost more than the change
itself. Skip out loud, never silently. Everything else — exploration, audits,
boilerplate, long first drafts — goes through the workflow.

## Safety notes

- The fast blade's output is untrusted. Review before applying edits or running
  its code. Keep auto-review on (default) for the Cursor blade.
- Each runner call is stateless (one prompt = one process); include all needed
  context every time. Opus owns the loop from outside.
- Cursor SDK tool-call arg/result schemas are explicitly unstable (per Cursor's
  docs). The runner only reads the final assistant text (`result.result` /
  `.text()`), which is stable.
