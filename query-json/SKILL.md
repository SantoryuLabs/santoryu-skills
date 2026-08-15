---
name: query-json
description: Use BEFORE reading a JSON file that is large, minified, or of unknown size — build results, project/test fixtures, API or DB dumps, exports, lock files, profiler output. Reading such a file directly blows the context window; a single minified line can be megabytes. Provides file summary, schema tree, deep search for an object by field value, listing of any array-of-objects, dot-path extraction, and line-numbered raw grep. Trigger on "what's in this json", "find X in the json", "which fields does this file have", "why does this record have Y", or any time a .json path appears in the task and its size is not already known to be small.
---

# query-json — inspect big JSON without paying for it in context

A JSON file you cannot afford to read is still a file you need answers from. This
skill answers the usual questions — what is in here, where is X, give me this one
value — while printing kilobytes instead of megabytes.

Nothing in it is domain-specific. `find` deep-walks arbitrary JSON, `list` groups
any array-of-objects by its structural path, and `summary` infers shape rather
than matching known key names. It is pure stdlib Python — no dependencies.

## Hard rules

1. **Never `Read`, `cat`, or `Get-Content` a large JSON file.** Not "just the first
   part" either: these files are frequently minified onto one line, so any partial
   read still pulls the whole thing.
2. **Start with `summary`** when you don't know the file. It is cheap and tells you
   which of the other commands to reach for.
3. **A path printed by `find` or `list` is a `get` argument.** Multiple hits → pick
   the right `path` and re-run `get <path>` rather than widening the output.
4. **Prefer `find` over `grep` for lookups.** On minified JSON, `grep` returns
   truncated snippets with no structure; `find` returns the actual object.
5. **`grep` is the fallback for a file that will not parse** — it never calls
   `json.loads`, so it survives malformed or oversized input.

## Commands

```bash
{{CMD}} summary <file>
{{CMD}} schema  <file> [--depth N]
{{CMD}} find    <file> <query> [--key KEY] [--contains] [--under PATH] [--limit N] [--fields A,B] [--full]
{{CMD}} list    <file> [--under PATH] [--field FIELD] [--limit N]
{{CMD}} get     <file> <dotpath> [--full]
{{CMD}} grep    <file> <pattern> [--limit N]
{{CMD}} install
```

`--json` is available on every query command for machine-readable output.

### Which command for which goal

| Goal | Command |
|---|---|
| I don't know this file at all | `summary` |
| What fields exist, how deep does it go | `schema --depth 3` |
| Find one record by its name/id | `find` ← **the usual answer** |
| List every name/id in some array | `list --under <path>` |
| Pull one specific value | `get <dotpath>` |
| Line number in the raw file | `grep` |
| The file won't parse | `grep` |

### `find` — locate an object by a field value

Deep-walks every object in the file and matches on one field (`name` by default).

```bash
# exact match on `name`
{{CMD}} find build_result.json B_42

# match a different field
{{CMD}} find package-lock.json 4.17.21 --key version

# substring when you only know part of the value
{{CMD}} find build_result.json 147 --contains

# keep the output tiny
{{CMD}} find build_result.json B_42 --fields name,sectionSize,length,connections

# scope the search when the file has history/undo/snapshot branches
{{CMD}} find build_result.json 147 --contains --under members
```

Output is the JSON `path` of each hit followed by the object itself. Capped at
`--limit` (default 20) — a deep search over arbitrary JSON can match thousands of
nodes, and the total is always reported.

Because the walk covers the *whole* file, it also reaches snapshot, history, and
undo branches that reuse the same field names. When hits look redundant, that is
the cause — narrow with `--under members`, `--under pages[].members`, or whichever
subtree `summary` showed you.

### `list` — every array-of-objects, grouped by structure

Array indices collapse, so sibling arrays report as one deduplicated group:
`pages[0].members.beams` and `pages[7].members.beams` become `pages[].members.beams`.

```bash
# every array of objects in the file, with their `name` values
{{CMD}} list build_result.json

# just one subtree
{{CMD}} list build_result.json --under members.beams

# list by a different field
{{CMD}} list openapi.json --field operationId
```

### `get` — extract one value by dot path

```bash
{{CMD}} get build_result.json members.beams[0].sectionSize
{{CMD}} get build_result.json pages[0].members.columns
```

A wrong path prints the available sibling keys, so a failed `get` still makes
progress. Paths are `key`, `key[0]`, and `key[0][1]`; a key containing a literal
dot cannot be addressed this way.

### `grep` — raw text, with line numbers

```bash
{{CMD}} grep build_result.json "147_0_beam"
```

Never parses the file. Long lines are truncated around the match and annotated
with the true line length.

## Output trimming

`find` and `get` replace any single field larger than `--max-field-chars`
(default 400) with a placeholder that still describes it:

```
"drawings": "<list[412] trimmed>",
"geometry": "<dict keys=[x, y, z, rotation, ...] trimmed>"
```

This is size-based, not name-based, so it adapts to unfamiliar JSON and keeps a
field that happens to be small. `--full` disables it — use it only after you know
the object is small.

## Windows notes

Chain commands with `;`, not `&&`. There is no `head` — pipe to
`Select-Object -First N`.

## Install

```bash
{{CMD}} install
```

Copies this `SKILL.md` into `~/.claude/skills/query-json/`, stamping the script's
absolute path. The script stays in `santoryu-cursor/query-json/`. Re-run it after
moving or editing anything here, otherwise the installed copy goes stale.
