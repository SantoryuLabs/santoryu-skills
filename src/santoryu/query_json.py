"""Query large JSON files without loading them into an AI agent's context.

Any JSON that is big, minified, or of unknown size will blow the context window if
read directly. This tool answers the usual questions -- what's in here, where is X,
give me this one value -- while printing only kilobytes.

Nothing here is domain-specific: `find` deep-walks arbitrary JSON, `list` groups any
array-of-objects by its structural path, and `summary` infers shape rather than
matching known key names.

Usage:
  query-json summary <file>
  query-json schema  <file> [--depth N]
  query-json find    <file> <query> [--key KEY] [--contains] [--limit N]
  query-json list    <file> [--under PATH] [--field FIELD]
  query-json get     <file> <dotpath>
  query-json grep    <file> <pattern>

Registering this as a Claude Code skill is `santoryu install`, which installs
every skill the package ships.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterator

# Output may carry arbitrary JSON text; the Windows console defaults to cp1252 and
# would raise UnicodeEncodeError on the first non-Latin-1 character.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_MAX_FIELD_CHARS = 400
DEFAULT_FIND_LIMIT = 20
DEFAULT_LIST_VALUES = 60
SCALAR_TYPES = (str, int, float, bool)


# --------------------------------------------------------------------------- #
# Loading and size formatting
# --------------------------------------------------------------------------- #


# utf-8-sig, not utf-8: JSON written by PowerShell, .NET, and most Windows editors
# carries a UTF-8 BOM, which json.loads rejects outright. utf-8-sig strips a BOM if
# present and behaves identically when it is not.
JSON_ENCODING = "utf-8-sig"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding=JSON_ENCODING))


def _fmt_size(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} MB"
    if n >= 1_000:
        return f"{n / 1_000:.1f} KB"
    return f"{n} B"


# --------------------------------------------------------------------------- #
# Generic traversal
# --------------------------------------------------------------------------- #


def walk_nodes(data: Any) -> Iterator[tuple[str, Any]]:
    """Yield (path, node) for every dict and list in `data`, in document order.

    Iterative on purpose: arbitrary JSON can nest deeper than the interpreter's
    recursion limit, and a RecursionError here would take down the whole query.
    Paths use the same syntax `resolve_path` consumes, so any path printed by
    `find`/`list` can be handed straight back to `get`.
    """
    stack: list[tuple[str, Any]] = [("", data)]
    while stack:
        path, node = stack.pop()
        if isinstance(node, dict):
            yield path, node
            children = [(f"{path}.{k}" if path else str(k), v) for k, v in node.items()]
        elif isinstance(node, list):
            yield path, node
            children = [(f"{path}[{i}]", v) for i, v in enumerate(node)]
        else:
            continue
        # Reversed, because a stack pops last-in-first-out and callers expect
        # results in the order the keys appear in the file.
        for child in reversed(children):
            stack.append(child)


def normalize_path(path: str) -> str:
    """Collapse array indices so sibling arrays share one structural path.

    `pages[3].members.beams` and `pages[7].members.beams` both become
    `pages[].members.beams`.
    """
    return re.sub(r"\[\d+\]", "[]", path)


def _under_matches(norm: str, under: str) -> bool:
    """Whether a normalized path sits at or below the normalized `under` path."""
    return norm == under or norm.startswith(under + ".") or norm.startswith(under + "[")


# --------------------------------------------------------------------------- #
# Dot-path resolution
# --------------------------------------------------------------------------- #


def _parse_path_segment(segment: str) -> tuple[str, list[int]]:
    m = re.fullmatch(r"([^\[\]]+)((?:\[\d+\])*)", segment)
    if not m:
        raise ValueError(f"invalid path segment: {segment!r}")
    indices = [int(i) for i in re.findall(r"\[(\d+)\]", m.group(2))]
    return m.group(1), indices


def resolve_path(data: Any, path: str) -> Any:
    if not path or path == ".":
        return data
    current = data
    for raw_seg in path.split("."):
        key, indices = _parse_path_segment(raw_seg)
        if not isinstance(current, dict):
            raise KeyError(f"cannot access {key!r} on {type(current).__name__} at {path}")
        if key not in current:
            raise KeyError(f"key {key!r} not found; available: {list(current.keys())}")
        current = current[key]
        for idx in indices:
            if not isinstance(current, list):
                raise KeyError(f"{key} is not a list (got {type(current).__name__})")
            if idx < 0 or idx >= len(current):
                raise IndexError(f"{key}[{idx}] out of range (length {len(current)})")
            current = current[idx]
    return current


def path_hint(data: Any, path: str) -> str:
    """Best-effort 'did you mean' context for a failed `get`."""
    try:
        resolve_path(data, path)
        return ""
    except (KeyError, IndexError, ValueError) as exc:
        hint = str(exc)
        parts = path.split(".")
        if len(parts) > 1:
            try:
                parent = resolve_path(data, ".".join(parts[:-1]))
            except (KeyError, IndexError, ValueError):
                return hint
            if isinstance(parent, dict):
                hint += f"; parent keys: {list(parent.keys())}"
            elif isinstance(parent, list):
                hint += f"; parent is list length {len(parent)}"
        return hint


# --------------------------------------------------------------------------- #
# Size-based trimming
# --------------------------------------------------------------------------- #


def measure(value: Any, limit: int) -> int:
    """Approximate serialized size of `value`, abandoning the walk past `limit`.

    Serializing a subtree just to learn it is too big to print defeats the point
    of this tool, so the count stops as soon as the threshold is exceeded.
    """
    stack = [value]
    total = 0
    while stack:
        if total > limit:
            return total
        v = stack.pop()
        if isinstance(v, str):
            total += len(v) + 2
        elif v is None or isinstance(v, bool):
            total += 5
        elif isinstance(v, (int, float)):
            total += len(repr(v))
        elif isinstance(v, list):
            total += 2 + max(0, len(v) - 1)
            stack.extend(v)
        elif isinstance(v, dict):
            total += 2 + max(0, len(v) - 1)
            for k, sub in v.items():
                total += len(str(k)) + 3
                stack.append(sub)
        else:
            total += len(str(v))
    return total


def _placeholder(value: Any) -> str:
    if isinstance(value, list):
        return f"<list[{len(value)}] trimmed>"
    if isinstance(value, dict):
        keys = list(value.keys())
        shown = ", ".join(str(k) for k in keys[:6])
        more = ", ..." if len(keys) > 6 else ""
        return f"<dict keys=[{shown}{more}] trimmed>"
    if isinstance(value, str):
        return f"<str len={len(value)} trimmed>"
    return "<trimmed>"


def trim(obj: dict, *, full: bool = False, fields: list[str] | None = None,
         max_field_chars: int = DEFAULT_MAX_FIELD_CHARS) -> dict:
    """Replace oversized top-level fields with a placeholder describing them.

    Size-based rather than name-based: it adapts to unfamiliar JSON, and it keeps
    a field that happens to be small instead of dropping it because of its name.
    """
    source = obj if fields is None else {k: obj[k] for k in fields if k in obj}
    if full:
        return dict(source)
    out: dict = {}
    for k, v in source.items():
        # Strings are measured like any container: an embedded blob, SVG, or
        # stack trace is exactly the kind of field that blows up the output.
        if v is None or isinstance(v, (int, float, bool)):
            out[k] = v
        elif measure(v, max_field_chars) > max_field_chars:
            out[k] = _placeholder(v)
        else:
            out[k] = v
    return out


# --------------------------------------------------------------------------- #
# summary
# --------------------------------------------------------------------------- #


def _array_counts(value: Any) -> dict[str, int]:
    """For a dict whose values include arrays, the length of each array."""
    if not isinstance(value, dict):
        return {}
    return {k: len(v) for k, v in value.items() if isinstance(v, list) and v}


def _describe_scalar(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False) if isinstance(value, str) else repr(value)
    return text if len(text) <= 80 else text[:77] + "..."


def summarize(data: Any, path: Path) -> str:
    lines: list[str] = [f"file: {path}", f"size: {_fmt_size(path.stat().st_size if path.exists() else 0)}"]

    if not isinstance(data, dict):
        if isinstance(data, list):
            lines.append(f"root: list[{len(data)}]")
            lines.extend(_summarize_list("root", data, indent="  "))
        else:
            lines.append(f"root: {type(data).__name__} = {_describe_scalar(data)}")
        return "\n".join(lines)

    lines.append(f"top_keys: {list(data.keys())}")

    # Containers first: the collections and their counts are what the caller came
    # for. Bare scalars go last so the structure is visible without scrolling.
    scalars: list[str] = []
    for key, value in data.items():
        if isinstance(value, list):
            lines.append(f"{key}: list[{len(value)}]")
            lines.extend(_summarize_list(key, value, indent="  "))
        elif isinstance(value, dict):
            counts = _array_counts(value)
            if counts:
                lines.append(f"{key}: dict of {len(counts)} arrays")
                for sub, n in counts.items():
                    lines.append(f"  {sub}: {n}")
            else:
                lines.append(f"{key}: dict keys={list(value.keys())[:12]}")
        else:
            scalars.append(f"  {key}: {_describe_scalar(value)}")

    if scalars:
        lines.append(f"scalars ({len(scalars)}):")
        lines.extend(scalars)
    return "\n".join(lines)


def _summarize_list(key: str, items: list, indent: str) -> list[str]:
    dicts = [x for x in items if isinstance(x, dict)]
    if not dicts:
        return []
    lines: list[str] = []
    keys: list[str] = []
    for d in dicts:
        for k in d:
            if k not in keys:
                keys.append(k)
    lines.append(f"{indent}[*] keys={keys[:15]}")

    # A list of objects that each hold a dict-of-arrays (pages[i].members.beams,
    # chapters[i].sections.figures, ...) only makes sense as a total.
    aggregate: dict[str, dict[str, int]] = {}
    for d in dicts:
        for k, v in d.items():
            for sub, n in _array_counts(v).items():
                aggregate.setdefault(k, {})
                aggregate[k][sub] = aggregate[k].get(sub, 0) + n
    for k, counts in aggregate.items():
        lines.append(f"{indent}{key}[].{k} (total across {len(dicts)}):")
        for sub, n in sorted(counts.items()):
            lines.append(f"{indent}  {sub}: {n}")
    return lines


# --------------------------------------------------------------------------- #
# schema
# --------------------------------------------------------------------------- #


def walk_schema(obj: Any, prefix: str = "", depth: int = 0, max_depth: int = 3) -> list[str]:
    if depth > max_depth:
        return []
    lines: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else k
            if isinstance(v, list):
                lines.append(f"{p}: list[{len(v)}]")
                if v and isinstance(v[0], dict):
                    lines.append(f"  [0] keys={list(v[0].keys())[:12]}")
                    if depth < max_depth:
                        lines.extend(walk_schema(v[0], f"{p}[0]", depth + 1, max_depth))
            elif isinstance(v, dict):
                lines.append(f"{p}: dict keys={list(v.keys())[:12]}")
                if depth < max_depth:
                    lines.extend(walk_schema(v, p, depth + 1, max_depth))
            else:
                lines.append(f"{p}: {type(v).__name__}")
    elif isinstance(obj, list) and obj:
        lines.append(f"{prefix or 'root'}: list[{len(obj)}]")
        if isinstance(obj[0], dict):
            lines.append(f"  [0] keys={list(obj[0].keys())[:12]}")
            if depth < max_depth:
                lines.extend(walk_schema(obj[0], f"{prefix}[0]" if prefix else "[0]", depth + 1, max_depth))
    return lines


# --------------------------------------------------------------------------- #
# find
# --------------------------------------------------------------------------- #


def _value_matches(value: Any, query: str, *, contains: bool) -> bool:
    if value is None or isinstance(value, (dict, list)):
        return False
    text = str(value)
    if contains:
        return query.lower() in text.lower()
    return text.lower() == query.lower()


def find_objects(data: Any, query: str, *, key: str = "name", contains: bool = False,
                 under: str | None = None) -> list[tuple[str, dict]]:
    """Every object anywhere in `data` whose `key` field matches `query`.

    `under` restricts the search to one structural subtree. A whole-file walk also
    reaches history, undo, and snapshot branches that happen to reuse the same
    field names, so scoping is the way to cut that noise.
    """
    under_norm = normalize_path(under) if under else None
    hits = []
    for path, node in walk_nodes(data):
        if not isinstance(node, dict):
            continue
        if under_norm and not _under_matches(normalize_path(path), under_norm):
            continue
        if _value_matches(node.get(key), query, contains=contains):
            hits.append((path, node))
    return hits


# --------------------------------------------------------------------------- #
# list
# --------------------------------------------------------------------------- #


def list_arrays(data: Any, *, under: str | None = None,
                field: str = "name") -> list[dict]:
    """Group every array-of-objects by its structural path.

    Sibling arrays under different indices collapse into one group, so
    `pages[0..N].members.beams` reports a single deduplicated set of names.
    """
    under_norm = normalize_path(under) if under else None
    groups: dict[str, dict] = {}
    for path, node in walk_nodes(data):
        if not isinstance(node, list) or not node:
            continue
        if not any(isinstance(x, dict) for x in node):
            continue
        norm = normalize_path(path)
        if under_norm and not _under_matches(norm, under_norm):
            continue
        group = groups.setdefault(norm, {"path": norm, "arrays": 0, "count": 0,
                                         "values": [], "_seen": set()})
        group["arrays"] += 1
        group["count"] += len(node)
        for item in node:
            if not isinstance(item, dict):
                continue
            value = item.get(field)
            if value is None or isinstance(value, (dict, list)):
                continue
            text = str(value)
            if text not in group["_seen"]:
                group["_seen"].add(text)
                group["values"].append(text)
    for group in groups.values():
        del group["_seen"]
    return sorted(groups.values(), key=lambda g: g["path"])


# --------------------------------------------------------------------------- #
# grep
# --------------------------------------------------------------------------- #


def grep_file(path: Path, pattern: str, limit: int = 50, max_line_chars: int = 240) -> list[str]:
    """Line-numbered raw search. Never parses, so it survives huge or broken files."""
    lines_out: list[str] = []
    needle = pattern.lower()
    with path.open(encoding=JSON_ENCODING, errors="replace") as f:
        for lineno, line in enumerate(f, 1):
            if needle not in line.lower():
                continue
            text = line.rstrip()
            if len(text) > max_line_chars:
                idx = text.lower().find(needle)
                start = max(0, idx - 80)
                end = min(len(text), idx + len(pattern) + 80)
                snippet = text[start:end]
                if start > 0:
                    snippet = "..." + snippet
                if end < len(text):
                    snippet = snippet + "..."
                text = f"{snippet}  [truncated, line has {len(line)} chars]"
            lines_out.append(f"{lineno}: {text}")
            if len(lines_out) >= limit:
                break
    return lines_out


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _add_json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Output as JSON")


def _add_trim_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--full", action="store_true", help="Disable size-based trimming")
    parser.add_argument("--max-field-chars", type=int, default=DEFAULT_MAX_FIELD_CHARS,
                        help=f"Trim fields larger than this (default: {DEFAULT_MAX_FIELD_CHARS})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="query-json",
        description="Query large JSON files without loading them into an agent's context.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_summary = sub.add_parser("summary", help="File overview: top-level shape and counts")
    p_summary.add_argument("file", type=Path)
    _add_json_flag(p_summary)

    p_schema = sub.add_parser("schema", help="Path tree with array lengths and sample keys")
    p_schema.add_argument("file", type=Path)
    p_schema.add_argument("--depth", type=int, default=3, help="Max traversal depth (default: 3)")
    _add_json_flag(p_schema)

    p_find = sub.add_parser("find", help="Find objects anywhere in the file by a field value")
    p_find.add_argument("file", type=Path)
    p_find.add_argument("query", help="Value to match")
    p_find.add_argument("--key", default="name", help="Field to match on (default: name)")
    p_find.add_argument("--contains", action="store_true", help="Substring match instead of exact")
    p_find.add_argument("--under", help="Restrict to a structural path, e.g. members or pages[].members")
    p_find.add_argument("--limit", type=int, default=DEFAULT_FIND_LIMIT,
                        help=f"Max hits to print (default: {DEFAULT_FIND_LIMIT})")
    p_find.add_argument("--fields", help="Comma-separated fields to include")
    _add_trim_flags(p_find)
    _add_json_flag(p_find)

    p_list = sub.add_parser("list", help="Group arrays of objects by structural path")
    p_list.add_argument("file", type=Path)
    p_list.add_argument("--under", help="Restrict to a structural path, e.g. members.beams")
    p_list.add_argument("--field", default="name", help="Field to list (default: name)")
    p_list.add_argument("--limit", type=int, default=DEFAULT_LIST_VALUES,
                        help=f"Max values per group (default: {DEFAULT_LIST_VALUES})")
    _add_json_flag(p_list)

    p_get = sub.add_parser("get", help="Extract value by dot path")
    p_get.add_argument("file", type=Path)
    p_get.add_argument("path", help="Dot path, e.g. members.beams[0].name")
    _add_trim_flags(p_get)
    _add_json_flag(p_get)

    p_grep = sub.add_parser("grep", help="Raw-text search with line numbers (never parses)")
    p_grep.add_argument("file", type=Path)
    p_grep.add_argument("pattern", help="Substring to search for")
    p_grep.add_argument("--limit", type=int, default=50, help="Max matching lines (default: 50)")
    _add_json_flag(p_grep)

    return parser


def _cmd_grep(args) -> int:
    matches = grep_file(args.file, args.pattern, limit=args.limit)
    if args.json:
        print(json.dumps(matches, indent=2))
    elif matches:
        print("\n".join(matches))
    else:
        print(f"no matches for {args.pattern!r}")
    return 0


def _cmd_summary(args, data) -> int:
    out = summarize(data, args.file)
    if args.json:
        print(json.dumps({"file": str(args.file), "summary": out.splitlines()}, indent=2))
    else:
        print(out)
    return 0


def _cmd_schema(args, data) -> int:
    lines = walk_schema(data, max_depth=args.depth)
    print(json.dumps(lines, indent=2) if args.json else "\n".join(lines))
    return 0


def _cmd_find(args, data) -> int:
    fields = [f.strip() for f in args.fields.split(",")] if args.fields else None
    hits = find_objects(data, args.query, key=args.key, contains=args.contains, under=args.under)
    if not hits:
        mode = "contains" if args.contains else "exact"
        scope = f" under {args.under!r}" if args.under else ""
        print(f"no {mode} matches for {args.query!r} on key {args.key!r}{scope}")
        print(f"hint: try --contains, another --key, drop --under, or: list {args.file}")
        return 1

    shown = hits[: args.limit]
    trimmed = [
        {"path": path, "object": trim(obj, full=args.full, fields=fields,
                                      max_field_chars=args.max_field_chars)}
        for path, obj in shown
    ]
    if args.json:
        print(json.dumps(trimmed, indent=2, ensure_ascii=False))
    else:
        for i, entry in enumerate(trimmed):
            if i:
                print()
            print(f"path: {entry['path']}")
            print(json.dumps(entry["object"], indent=2, ensure_ascii=False))
        if len(hits) > len(shown):
            print(f"\n{len(hits)} matches, {len(shown)} shown. Raise --limit, or use get <path>.")
        elif len(hits) > 1:
            print(f"\n{len(hits)} matches. Use get <path> to fetch a specific one.")
    return 0


def _cmd_list(args, data) -> int:
    groups = list_arrays(data, under=args.under, field=args.field)
    if not groups:
        target = f" under {args.under!r}" if args.under else ""
        print(f"no arrays of objects found{target}")
        return 1
    if args.json:
        payload = [{**g, "values": g["values"][: args.limit]} for g in groups]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    for group in groups:
        spread = f" in {group['arrays']} arrays" if group["arrays"] > 1 else ""
        print(f"{group['path']}  ({group['count']} items{spread})")
        values = group["values"]
        if not values:
            print(f"  <no {args.field!r} field>")
            continue
        print("  " + ", ".join(values[: args.limit]))
        if len(values) > args.limit:
            print(f"  ... {len(values) - args.limit} more (raise --limit)")
    return 0


def _cmd_get(args, data) -> int:
    try:
        value = resolve_path(data, args.path)
    except (KeyError, IndexError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        hint = path_hint(data, args.path)
        if hint:
            print(f"hint: {hint}", file=sys.stderr)
        return 1
    if isinstance(value, dict):
        value = trim(value, full=args.full, max_field_chars=args.max_field_chars)
    if args.json or not isinstance(value, SCALAR_TYPES):
        print(json.dumps(value, indent=2, ensure_ascii=False))
    else:
        print(value)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    path: Path = args.file
    if not path.is_file():
        print(f"error: file not found: {path}", file=sys.stderr)
        return 1

    # grep is the only command that must survive an unparseable file, so it runs
    # before the file is read into memory.
    if args.command == "grep":
        return _cmd_grep(args)

    try:
        data = load_json(path)
    except json.JSONDecodeError as exc:
        print(f"error: {path} is not valid JSON: {exc}", file=sys.stderr)
        print("hint: use `grep` -- it does not parse the file", file=sys.stderr)
        return 1

    handlers = {
        "summary": _cmd_summary,
        "schema": _cmd_schema,
        "find": _cmd_find,
        "list": _cmd_list,
        "get": _cmd_get,
    }
    return handlers[args.command](args, data)


if __name__ == "__main__":
    raise SystemExit(main())
