# Santoryu — setup and usage

Three blades, one mind. Opus (Claude Code) plans and reviews; two fast "blades" —
a repo-aware Cursor agent and a raw OpenAI-compatible model — do the mechanical
work. The goal: cut the number of slow iterations.

## Setup (local, with Claude Code)

1. Dependencies + Cursor key:
   ```bash
   pip install -r requirements.txt   # cursor-sdk + python-dotenv (Python 3.10+)
   export CURSOR_API_KEY=crsr_...     # Cursor Dashboard -> API Keys
   ```
   Or drop `CURSOR_API_KEY=crsr_...` into a repo-local `.env` (auto-loaded).

2. Register the skill with Claude Code:
   ```bash
   python3 santoryu.py install       # copies SKILL.md into ~/.claude/skills/santoryu/
   ```
   The script stays in this repo; only `SKILL.md` (stamped with the script's
   absolute path) is installed, so the key never leaves the repo.

3. See the model ids on your account (Grok 4.5 may show up here):
   ```bash
   python3 santoryu.py cursor --list-models
   ```

> The `fast` blade is currently disabled (`FAST_ENABLED = False` in the script);
> only the `cursor` blade is exposed. Flip the flag to re-enable it.

## Flow (plan -> review -> apply)

- Opus writes the spec + acceptance criteria.
- Cursor produces a plan:   `santoryu.py cursor --prompt-file plan.txt --mode plan`
- Opus reviews the plan and turns it into the final plan.
- Apply:
  - mechanical/bulky -> `santoryu.py cursor --prompt-file final.txt --mode agent`
  - small/risky      -> Opus does it itself.
- Opus does the final review and delivers.

## Notes
- The cursor blade has auto-review on by default: the fast model's file/shell
  actions are gated for review. `--sandbox` is stricter, `--no-guard` turns it
  off (not recommended).
- Real runs happen on your machine with your own `CURSOR_API_KEY` — this package
  was authored in a container; Opus drives the actual execution locally.
