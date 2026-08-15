#!/usr/bin/env python3
"""
santoryu.py — three-blade fast-model orchestration for Claude Code / Opus.

Opus stays the planner and reviewer (the mind). This CLI is the sword arm: it
delegates one prompt to a fast model and prints the reply to stdout. Two blades:

  cursor   Run a Cursor agent (Composer 2.5 / Grok) via cursor-sdk — repo-aware,
           tool-using, with plan/agent modes. Auth: CURSOR_API_KEY.
  fast     One-shot chat call to any OpenAI-compatible endpoint (xAI / OpenRouter
           / OpenAI). No repo awareness, pure stdlib. Auth: one *_API_KEY env var.

Both blades are stateless: one prompt = one process. Include all needed context
in every call. Metadata goes to stderr so stdout stays clean for capturing the
draft; Opus owns the plan -> review -> apply loop from the outside.

The `fast` blade is temporarily disabled (see FAST_ENABLED); only `cursor` is
exposed on the CLI. Its implementation is kept intact for easy re-enabling.

Usage:
  python3 santoryu.py install                 # register the skill (copies SKILL.md)
  python3 santoryu.py cursor --prompt-file plan.txt --mode plan
  python3 santoryu.py cursor --prompt "Apply the reviewed plan" --mode agent
  python3 santoryu.py cursor --list-models
"""
import argparse
import asyncio
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

# python-dotenv is optional: it may not be installed yet when bootstrapping via
# `santoryu.py install`. If present, auto-load the repo-local .env (which sits
# next to this script); otherwise fall back to real environment variables.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass


def die(msg: str, code: int = 1):
    sys.stderr.write(msg.rstrip() + "\n")
    sys.exit(code)


def emit(text):
    """Write text to stdout as UTF-8, regardless of the console's code page.

    Cursor's plans contain characters (e.g. U+2192 '->') that aren't in
    Windows' default cp1252; a plain print() then raises UnicodeEncodeError and
    loses the whole result even though the run succeeded. We reconfigure stdout
    to UTF-8 when possible, and fall back to writing encoded bytes through the
    buffer so the output survives on any host without the caller having to set
    PYTHONIOENCODING/PYTHONUTF8.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Python 3.7+
    except Exception:  # noqa: BLE001
        pass
    try:
        print(text)
    except UnicodeEncodeError:
        data = (text + "\n").encode("utf-8", "replace")
        try:
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
        except Exception:  # noqa: BLE001
            sys.stdout.write(text.encode("utf-8", "replace").decode("ascii", "replace") + "\n")


def read_prompt(prompt, prompt_file, allow_stdin):
    """Resolve the prompt from --prompt, --prompt-file, or stdin (if allowed)."""
    if prompt is not None:
        text = prompt
    elif prompt_file:
        with open(prompt_file, "r", encoding="utf-8") as f:
            text = f.read()
    elif allow_stdin:
        text = sys.stdin.read()
    else:
        die("ERROR: no prompt. Use --prompt, --prompt-file, or pipe via stdin.")
    if not text.strip():
        die("ERROR: empty prompt.")
    return text


# --------------------------------------------------------------------------- #
# Blade 1: cursor — repo-aware Cursor agent via cursor-sdk
#
# Uses the SDK's ASYNC API, not the sync one. The sync bridge reads the helper
# process's stderr pipe via select(), which on native Windows raises WinError
# 10038 (WinSock select accepts only sockets, never pipes). The async bridge
# reads the pipe with asyncio (ProactorEventLoop/IOCP on Windows, epoll on
# Linux), so this blade runs on Windows without WSL and on Linux unchanged.
#
# Flow is create -> send -> wait (NOT the one-shot AsyncAgent.prompt): prompt()
# returns only a RunResult and disposes the agent, so when Cursor leaves the
# real plan in earlier assistant messages (final text = a one-line interim note
# like "Creating the plan."), there is no way back to it. Holding the Run handle
# lets us recover the full text from run.conversation() when the final text is
# suspiciously thin relative to the tokens the run actually produced.
# --------------------------------------------------------------------------- #

# Thin-output heuristic: final text this short while the run generated this many
# output tokens means the plan almost certainly lives in earlier assistant
# messages, not in the final text.
THIN_TEXT_CHARS = 2000
THIN_MIN_OUT_TOKENS = 3000

# `--mode plan` runs on the SDK's agent runtime (not its "plan" runtime): the
# plan runtime emits its deliverable through a createPlan tool call whose text
# doesn't reliably reach run.result, whereas the agent runtime returns a normal
# text answer. To keep plan mode's read-only guarantee without that runtime, we
# (a) prepend this hard read-only instruction and (b) default the sandbox on, so
# writes are blocked structurally, not just by request. The prompt is the intent;
# the sandbox is the enforcement.
PLAN_READONLY_PREAMBLE = (
    "MODE: READ-ONLY INVESTIGATION. Do NOT edit, create, delete, or move any "
    "file. Do NOT run commands that mutate the repo or the system. Explore the "
    "codebase, then reply with the complete deliverable AS PLAIN TEXT in this "
    "answer — Findings (with file:line evidence), Root cause, and a step-by-step "
    "Plan. Do not defer the plan to a tool or a later turn; write it out now.\n\n"
    "---\n\n"
)


def load_sdk():
    try:
        import cursor_sdk

        return cursor_sdk
    except ImportError:
        die(
            "ERROR: cursor-sdk is not installed.\n"
            "  Run:  pip install cursor-sdk\n"
            "  (needs Python 3.10+; check with: python3 --version)"
        )


def require_cursor_key():
    if not os.environ.get("CURSOR_API_KEY"):
        die(
            "ERROR: CURSOR_API_KEY is not set.\n"
            "  Get a User API key from Cursor Dashboard -> API Keys, then:\n"
            "  export CURSOR_API_KEY=crsr_...\n"
            "  (This uses your own Cursor account and bills to your plan.)"
        )


def _model_is_grok(model_id):
    return "grok" in (model_id or "").lower()


def _model_is_grok_or_composer(model_id):
    """True when the model id is a Grok or Composer variant (fast always on)."""
    mid = (model_id or "").lower()
    return "grok" in mid or "composer" in mid


def _build_model_selection(sdk, model_id, force_fast=False, effort=None):
    """Build ModelSelection with default params for the chosen model.

    Defaults:
      - Grok / Composer: fast=true
      - Grok: effort=high (overridable via effort=)
    """
    params = []
    use_fast = force_fast or _model_is_grok_or_composer(model_id)
    if use_fast:
        params.append(sdk.ModelParameterValue(id="fast", value="true"))
    # Grok exposes effort={low,medium,high}; default high unless caller overrides.
    effective_effort = None
    if _model_is_grok(model_id):
        effective_effort = effort or "high"
        params.append(sdk.ModelParameterValue(id="effort", value=effective_effort))
    elif effort:
        effective_effort = effort
        params.append(sdk.ModelParameterValue(id="effort", value=effort))
    if params:
        return sdk.ModelSelection(id=model_id, params=params), use_fast, effective_effort
    return model_id, use_fast, effective_effort


def _get(obj, name):
    """Read a field from a dataclass or a plain mapping; None if absent."""
    if obj is None:
        return None
    value = getattr(obj, name, None)
    if value is None and isinstance(obj, dict):
        value = obj.get(name)
    return value


def _collect_assistant_text(turns):
    """Join the text of every assistantMessage step across all agent turns."""
    parts = []
    for turn in turns or ():
        if _get(turn, "type") != "agentConversationTurn":
            continue
        payload = _get(turn, "turn")
        for step in _get(payload, "steps") or ():
            if _get(step, "type") != "assistantMessage":
                continue
            text = _get(_get(step, "message"), "text")
            if text:
                parts.append(text)
    return "\n\n".join(parts).strip()


async def _cursor_list_models_async(sdk):
    client = await sdk.AsyncClient.launch_bridge(workspace=os.getcwd())
    try:
        return await client.list_models()
    finally:
        await client.aclose()


def cursor_list_models():
    sdk = load_sdk()
    require_cursor_key()
    try:
        models = asyncio.run(_cursor_list_models_async(sdk))
    except Exception as e:  # noqa: BLE001
        die(f"ERROR: could not list models: {e}")
    for m in models:
        params = getattr(m, "parameters", None) or []
        pnames = ",".join(getattr(p, "id", "?") for p in params)
        line = f"{m.id}"
        disp = getattr(m, "display_name", None)
        if disp and disp != m.id:
            line += f"  ({disp})"
        if pnames:
            line += f"  [params: {pnames}]"
        print(line)


FOLLOWUP_PROMPT = (
    "Your previous turn ended without printing the full deliverable as text. "
    "Reply now with the complete write-up as plain text — Findings (with "
    "file:line evidence), Root cause, and the step-by-step Plan — using no "
    "tool calls. Do not summarize or refer to earlier output; print the full "
    "content in this reply."
)


async def _drain_run_text(run, result):
    """Best text for one run: final text, else assistant text from conversation()."""
    text = (getattr(result, "result", None) or "").strip()
    usage = getattr(result, "usage", None)
    out_tokens = getattr(usage, "output_tokens", 0) if usage else 0
    if (not text) or (len(text) < THIN_TEXT_CHARS and out_tokens > THIN_MIN_OUT_TOKENS):
        try:
            collected = _collect_assistant_text(await run.conversation())
            if collected and len(collected) > len(text):
                return collected, "conversation"
        except Exception as conv_err:  # noqa: BLE001
            sys.stderr.write(f"[santoryu:cursor] conversation fallback failed: {conv_err}\n")
    return text, "final"


async def _cursor_run_async(sdk, prompt, agent_options, cwd, mode):
    """create -> send -> wait, with two recovery layers when the deliverable
    doesn't land in the final text:
      1. collect assistant text from run.conversation()
      2. (plan mode only) send a follow-up on the SAME agent asking it to print
         the full plan as plain text — plan mode emits its plan through tool/
         plan structures whose schemas are unstable, so instead of digging into
         those, we use the retained conversation context to have Cursor write
         the plan out as text.
    Returns (result, text, source)."""
    client = await sdk.AsyncClient.launch_bridge(workspace=cwd)
    try:
        # Pass a single AgentOptions object, not kwargs: the resource-namespace
        # create() rejects `mode` as a keyword (observed: "_AsyncAgentsResource
        # .create() got an unexpected keyword argument 'mode'"), while `mode`
        # is a first-class AgentOptions field.
        agent = await client.agents.create(agent_options)
        try:
            run = await agent.send(prompt)
            result = await run.wait()
            text, source = await _drain_run_text(run, result)

            # Layer 2: in plan mode the deliverable IS text; if we still don't
            # have a real write-up, ask the same agent to print it.
            if mode == "plan" and len(text) < THIN_TEXT_CHARS:
                sys.stderr.write(
                    f"[santoryu:cursor] plan text still thin ({len(text)} chars) — "
                    f"sending follow-up to print the full plan\n"
                )
                run2 = await agent.send(FOLLOWUP_PROMPT)
                result2 = await run2.wait()
                text2, _ = await _drain_run_text(run2, result2)
                u2 = getattr(result2, "usage", None)
                if u2 is not None:
                    sys.stderr.write(
                        f"[santoryu:cursor] followup "
                        f"in={getattr(u2, 'input_tokens', '?')} "
                        f"out={getattr(u2, 'output_tokens', '?')}\n"
                    )
                if len(text2) > len(text):
                    text, source = text2, "followup"
            return result, text, source
        finally:
            await agent.close()
    finally:
        await client.aclose()


def cursor_run(args):
    sdk = load_sdk()
    require_cursor_key()
    prompt = read_prompt(args.prompt, args.prompt_file, allow_stdin=not sys.stdin.isatty())

    # `plan` is a read-only investigation that returns text. We run it on the
    # agent runtime because the SDK's plan runtime buries its output in a
    # createPlan tool call; the read-only guarantee comes from the preamble +
    # sandbox below, not from the runtime. Both plan and agent use the agent
    # runtime; they differ only in the preamble and the sandbox default.
    plan_mode = args.mode == "plan"
    sdk_mode = "agent"
    if plan_mode:
        prompt = PLAN_READONLY_PREAMBLE + prompt

    # Build model selection. Grok/Composer always get fast=true; Grok also
    # defaults to effort=high. --fast forces the fast preset on any other model;
    # --effort overrides Grok's (or another model's) effort level.
    model, use_fast, effort = _build_model_selection(
        sdk, args.model, force_fast=args.fast, effort=getattr(args, "effort", None)
    )

    # Sandbox default: ON for plan (structural read-only enforcement), and
    # whenever --sandbox is passed. --no-sandbox opts out up front. If the host
    # doesn't support sandboxing, the SDK raises at run time; we catch that and
    # transparently retry without the sandbox (falling back to auto-review), so
    # the caller never has to discover --no-sandbox by hand.
    want_sandbox = args.sandbox or (plan_mode and not args.no_sandbox)

    def build_options(with_sandbox):
        local_kwargs = {"cwd": args.cwd}
        if with_sandbox:
            try:
                local_kwargs["sandbox_options"] = sdk.SandboxOptions(enabled=True)
            except AttributeError:
                local_kwargs["sandbox_options"] = {"enabled": True}
        if not args.no_guard and not with_sandbox:
            # Auto-review gates shell/edit/write instead of running them blind.
            local_kwargs["auto_review"] = True
        local = sdk.LocalAgentOptions(**local_kwargs)
        return sdk.AgentOptions(model=model, local=local, mode=sdk_mode)

    agent_options = build_options(want_sandbox)

    def is_sandbox_unsupported(err):
        msg = str(err).lower()
        return "sandbox" in msg and ("not supported" in msg or "not available" in msg)

    try:
        try:
            result, text, source = asyncio.run(_cursor_run_async(sdk, prompt, agent_options, args.cwd, args.mode))
        except Exception as e:  # noqa: BLE001
            if want_sandbox and is_sandbox_unsupported(e):
                sys.stderr.write(
                    "[santoryu:cursor] sandbox unsupported on this host — "
                    "retrying without sandbox (auto-review guard instead)\n"
                )
                want_sandbox = False
                agent_options = build_options(False)
                result, text, source = asyncio.run(_cursor_run_async(sdk, prompt, agent_options, args.cwd, args.mode))
            else:
                raise
    except Exception as e:  # noqa: BLE001
        rid = getattr(e, "request_id", None)
        extra = f" (request_id={rid})" if rid else ""
        die(f"ERROR from Cursor SDK{extra}: {e}")

    status = getattr(result, "status", "?")
    usage = getattr(result, "usage", None)
    if usage is not None:
        sys.stderr.write(
            f"[santoryu:cursor] status={status} mode={args.mode} source={source} "
            f"sandbox={want_sandbox} fast={use_fast} effort={effort} "
            f"in={getattr(usage, 'input_tokens', '?')} "
            f"out={getattr(usage, 'output_tokens', '?')} "
            f"total={getattr(usage, 'total_tokens', '?')}\n"
        )
    else:
        sys.stderr.write(
            f"[santoryu:cursor] status={status} mode={args.mode} source={source} "
            f"sandbox={want_sandbox} fast={use_fast} effort={effort}\n"
        )

    emit(text)

    if status not in ("finished",):
        # Non-fatal: still printed whatever text we got, but signal via exit code.
        sys.exit(2)


# --------------------------------------------------------------------------- #
# Blade 2: fast — one-shot OpenAI-compatible chat call (pure stdlib)
# --------------------------------------------------------------------------- #

# (env var, default base url, default model). First key found wins.
# FAST_* is the generic override slot, checked first.
PROVIDERS = [
    ("FAST_API_KEY", "https://api.x.ai/v1", "grok-4.5"),
    ("XAI_API_KEY", "https://api.x.ai/v1", "grok-4.5"),
    ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1", "x-ai/grok-4.5"),
    ("OPENAI_API_KEY", "https://api.openai.com/v1", "gpt-4.1-mini"),
]


def resolve_credentials(base_override, model_override):
    for env_name, base, model in PROVIDERS:
        key = os.environ.get(env_name)
        if key:
            return (
                key,
                base_override or os.environ.get("FAST_BASE_URL") or base,
                model_override or os.environ.get("FAST_MODEL") or model,
                env_name,
            )
    die(
        "ERROR: no API key found. Export one of: FAST_API_KEY, XAI_API_KEY, "
        "OPENROUTER_API_KEY, OPENAI_API_KEY.\n"
        "Example:  export XAI_API_KEY=xai-...."
    )


def fast_run(args):
    prompt = read_prompt(args.prompt, args.prompt_file, allow_stdin=args.stdin)
    key, base_url, model, provider = resolve_credentials(args.base_url, args.model)

    payload = {
        "model": model,
        "messages": [{"role": "system", "content": args.system}, {"role": "user", "content": prompt}],
        "temperature": args.temperature,
        "max_tokens": args.max_tokens,
    }

    url = base_url.rstrip("/") + "/chat/completions"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        die(f"ERROR: HTTP {e.code} from {provider} ({model}) at {url}\n{body}")
    except urllib.error.URLError as e:
        die(f"ERROR: could not reach {url}: {e.reason}")

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        die("ERROR: unexpected response shape:\n" + json.dumps(data, indent=2)[:2000])

    usage = data.get("usage", {})
    sys.stderr.write(
        f"[santoryu:fast] provider={provider} model={model} "
        f"prompt_tokens={usage.get('prompt_tokens','?')} "
        f"completion_tokens={usage.get('completion_tokens','?')}\n"
    )
    emit(content)


# --------------------------------------------------------------------------- #
# Blade 3: install — register the skill with Claude Code
#
# Only SKILL.md is copied into the Claude skills dir; the script (and its .env)
# stay in this repo. The copied SKILL.md is stamped with this script's absolute
# path so Claude invokes it from here, and no secret ever lands in the skills dir.
# --------------------------------------------------------------------------- #


def _skills_target_dir():
    base = os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude")
    return Path(base) / "skills" / "santoryu"


def install_run(args):
    here = Path(__file__).resolve().parent
    src_skill = here / "SKILL.md"
    if not src_skill.is_file():
        die(f"ERROR: SKILL.md not found next to santoryu.py ({src_skill}).")

    script_path = str((here / "santoryu.py").resolve())
    target_dir = _skills_target_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target_skill = target_dir / "SKILL.md"

    # Stamp the absolute script path into the copied SKILL.md: the script lives
    # in this repo, not beside the installed SKILL.md, so command examples must
    # carry its full path for Claude to invoke it from other working dirs.
    # Stamp with the launcher that actually exists on this host: sys.executable
    # is the interpreter running install, so it is guaranteed to work (on this
    # Windows host only `python` exists, not `python3`).
    launcher_cmd = f'"{sys.executable}"'
    text = src_skill.read_text(encoding="utf-8")
    for launcher in ("python3 santoryu.py", "python santoryu.py"):
        text = text.replace(launcher, f'{launcher_cmd} "{script_path}"')
    target_skill.write_text(text, encoding="utf-8")

    # Cursor API key status (already env/.env-loaded at import if present).
    env_file = here / ".env"
    if os.environ.get("CURSOR_API_KEY"):
        key_status = "OK (found in environment or repo .env)"
    elif env_file.is_file():
        key_status = f"present in {env_file} (auto-loaded at runtime)"
    else:
        key_status = f"MISSING — export CURSOR_API_KEY=crsr_... or add it to {env_file}"

    print(f"Installed SKILL.md -> {target_skill}")
    print(f"Script stays at    -> {script_path}")
    print(f"Cursor API key     -> {key_status}")
    print("Claude Code will discover the 'santoryu' skill on the next session.")
    print("Deps (if needed):   pip install -r requirements.txt")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

# The `fast` blade is temporarily disabled: its implementation is kept intact
# below but not exposed on the CLI. Flip this to True to re-enable it.
FAST_ENABLED = False


def build_parser():
    ap = argparse.ArgumentParser(
        prog="santoryu",
        description="Three-blade fast-model orchestration: delegate a prompt to a "
        "Cursor agent or an OpenAI-compatible model; Opus reviews.",
    )
    sub = ap.add_subparsers(dest="blade", required=True)

    # --- cursor blade ---
    c = sub.add_parser("cursor", help="Delegate one prompt to a Cursor agent (repo-aware).")
    c.add_argument("--list-models", action="store_true", help="List model ids available to your account, then exit.")
    csrc = c.add_mutually_exclusive_group()
    csrc.add_argument("--prompt", help="Prompt text passed inline.")
    csrc.add_argument("--prompt-file", help="Path to a file containing the prompt.")
    c.add_argument(
        "--mode",
        choices=["plan", "agent"],
        default="agent",
        help="plan = read-only investigation, returns findings+root cause+plan "
        "as text (sandbox on by default); agent = implement changes.",
    )
    c.add_argument("--model", default="grok-4.6", help="Model id (default grok-4.6). Use --list-models to discover.")
    c.add_argument(
        "--fast", action="store_true", help="Force the model's 'fast' preset. Already always on for grok/composer."
    )
    c.add_argument(
        "--effort",
        choices=["low", "medium", "high"],
        default=None,
        help="Thinking/effort level. Grok defaults to high; omit to keep that default.",
    )
    c.add_argument("--cwd", default=os.getcwd(), help="Workspace dir the agent runs against (default: current dir).")
    c.add_argument("--sandbox", action="store_true", help="Force SDK sandbox on (confine filesystem/shell/network).")
    c.add_argument(
        "--no-sandbox",
        action="store_true",
        help="Opt out of plan mode's default sandbox (e.g. host lacks sandbox support). "
        "Plan then relies on the read-only preamble + auto-review instead.",
    )
    c.add_argument("--no-guard", action="store_true", help="Disable auto-review gating (NOT recommended).")

    # --- fast blade (temporarily disabled; see FAST_ENABLED) ---
    if FAST_ENABLED:
        f = sub.add_parser("fast", help="One-shot call to an OpenAI-compatible model (no repo).")
        fsrc = f.add_mutually_exclusive_group(required=True)
        fsrc.add_argument("--prompt", help="Prompt text passed inline.")
        fsrc.add_argument("--prompt-file", help="Path to a file containing the prompt.")
        fsrc.add_argument("--stdin", action="store_true", help="Read prompt from stdin.")
        f.add_argument(
            "--system",
            default="You are a fast, capable drafting assistant. "
            "Produce complete, working output. Do not add commentary unless asked.",
            help="System prompt.",
        )
        f.add_argument("--model", help="Override model id.")
        f.add_argument("--base-url", help="Override base URL.")
        f.add_argument("--temperature", type=float, default=0.3)
        f.add_argument("--max-tokens", type=int, default=8000)

    # --- install blade ---
    sub.add_parser("install", help="Register the skill with Claude Code (copies SKILL.md only).")

    return ap


def main():
    args = build_parser().parse_args()

    if args.blade == "cursor":
        if args.list_models:
            cursor_list_models()
            return
        cursor_run(args)
    elif args.blade == "fast":
        fast_run(args)
    elif args.blade == "install":
        install_run(args)


if __name__ == "__main__":
    main()
