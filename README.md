# Santoryu — setup and usage

Three blades, one mind. Opus (Claude Code) plans and reviews; two fast "blades" —
a repo-aware Cursor agent and a raw OpenAI-compatible model — do the mechanical
work. The goal: cut the number of slow iterations.

The package ships two Claude Code skills, `santoryu` and `query-json`, and puts
a console command on PATH for each.

## Install

```bash
pipx install git+https://github.com/farukkaradas/santoryu-skills.git
```

From a local checkout, `pipx install .` (or `pip install .`) does the same.
Python 3.10+; dependencies come with the package.

Then give the Cursor blade a key — export `CURSOR_API_KEY=crsr_...` (User API
key from Cursor Dashboard -> API Keys), or write it into `~/.santoryu/.env`:

```
CURSOR_API_KEY=crsr_...
```

The key lives there, never inside the package and never in the skills dir.

Register the skills with Claude Code:

```bash
santoryu install
```

That copies each packaged `SKILL.md` into `~/.claude/skills/<name>/` verbatim.
No filesystem path is written into the installed copy, so moving, renaming, or
deleting this checkout cannot break the install. It is idempotent and overwrites
a stale earlier install. Restart Claude Code to pick the skills up.

Check the model ids on your account (Grok 4.5 may show up here):

```bash
santoryu cursor --list-models
```

> The `fast` blade is currently disabled (`FAST_ENABLED = False` in
> `src/santoryu/cli.py`); only the `cursor` blade is exposed. Flip the flag to
> re-enable it.

## Flow (plan -> review -> apply)

- Opus writes the spec + acceptance criteria.
- Cursor produces a plan:   `santoryu cursor --prompt-file plan.txt --mode plan`
- Opus reviews the plan and turns it into the final plan.
- Apply:
  - mechanical/bulky -> `santoryu cursor --prompt-file final.txt --mode agent`
  - small/risky      -> Opus does it itself.
- Opus does the final review and delivers.

## query-json

A standalone command for inspecting JSON too large, minified, or unknown to read
directly:

```bash
query-json summary <file>
query-json find <file> <value> [--key KEY] [--contains]
query-json get <file> members.beams[0].sectionSize
```

## Development

```bash
pip install -e .
python -m unittest discover -s tests
```

## Notes
- The cursor blade has auto-review on by default: the fast model's file/shell
  actions are gated for review. `--sandbox` is stricter, `--no-guard` turns it
  off (not recommended).
- Runs on native Windows (no WSL): the cursor blade uses the SDK's async API,
  sidestepping the sync bridge's `select()`-on-pipe limitation. Linux and macOS
  are unaffected.
