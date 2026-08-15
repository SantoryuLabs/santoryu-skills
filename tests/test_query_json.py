"""Unit tests for query_json's pure logic.

Stdlib unittest on purpose: this tool ships with no dependencies of its own, and
its tests should not add one. Run with:

    python -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from santoryu import query_json as qj  # noqa: E402


class EncodingTests(unittest.TestCase):
    """Windows tools emit UTF-8 with a BOM; plain utf-8 decoding rejects it."""

    def _write(self, text: str, encoding: str) -> Path:
        tmp = Path(tempfile.mkdtemp()) / "fixture.json"
        tmp.write_text(text, encoding=encoding)
        return tmp

    def test_loads_json_written_with_a_bom(self):
        path = self._write('{"name": "B_1"}', "utf-8-sig")
        self.assertEqual(qj.load_json(path), {"name": "B_1"})

    def test_still_loads_json_without_a_bom(self):
        path = self._write('{"name": "B_1"}', "utf-8")
        self.assertEqual(qj.load_json(path), {"name": "B_1"})

    def test_grep_does_not_leak_the_bom_into_output(self):
        path = self._write('{"name": "B_1"}', "utf-8-sig")
        self.assertEqual(qj.grep_file(path, "name"), ['1: {"name": "B_1"}'])

    def test_non_ascii_content_survives_round_trip(self):
        path = self._write('{"name": "kiriş_çelik"}', "utf-8-sig")
        self.assertEqual(qj.load_json(path)["name"], "kiriş_çelik")


class ResolvePathTests(unittest.TestCase):
    data = {"members": {"beams": [{"name": "B_1"}, {"name": "B_2"}]},
            "grid": [[1, 2], [3, 4]],
            "count": 7}

    def test_returns_root_for_empty_path(self):
        self.assertIs(qj.resolve_path(self.data, ""), self.data)
        self.assertIs(qj.resolve_path(self.data, "."), self.data)

    def test_walks_keys_and_indices(self):
        self.assertEqual(qj.resolve_path(self.data, "members.beams[1].name"), "B_2")
        self.assertEqual(qj.resolve_path(self.data, "count"), 7)

    def test_supports_chained_indices(self):
        self.assertEqual(qj.resolve_path(self.data, "grid[1][0]"), 3)

    def test_missing_key_lists_available_siblings(self):
        with self.assertRaises(KeyError) as ctx:
            qj.resolve_path(self.data, "members.columns")
        self.assertIn("beams", str(ctx.exception))

    def test_index_out_of_range(self):
        with self.assertRaises(IndexError):
            qj.resolve_path(self.data, "members.beams[9]")

    def test_traversing_into_a_scalar(self):
        with self.assertRaises(KeyError):
            qj.resolve_path(self.data, "count.nope")

    def test_path_hint_reports_parent_keys(self):
        hint = qj.path_hint(self.data, "members.columns")
        self.assertIn("beams", hint)


class WalkTests(unittest.TestCase):
    def test_yields_document_order(self):
        data = {"a": {"x": 1}, "b": [{"y": 2}]}
        paths = [p for p, _ in qj.walk_nodes(data)]
        self.assertEqual(paths, ["", "a", "b", "b[0]"])

    def test_deep_nesting_does_not_recurse(self):
        # Deeper than the default recursion limit: a recursive walker dies here.
        depth = 5000
        node: dict = {"name": "bottom"}
        for _ in range(depth):
            node = {"child": node}
        hits = qj.find_objects(node, "bottom")
        self.assertEqual(len(hits), 1)
        self.assertTrue(hits[0][0].endswith("child"))

    def test_normalize_path_collapses_indices(self):
        self.assertEqual(qj.normalize_path("pages[3].members.beams"), "pages[].members.beams")
        self.assertEqual(qj.normalize_path("grid[1][0]"), "grid[][]")


class FindTests(unittest.TestCase):
    data = {
        "members": {"beams": [{"name": "B_1"}, {"name": "B_2"}]},
        "pages": [{"number": 1, "members": {"beams": [{"name": "B_1"}, {"name": "b_10"}]}}],
        "piecemarks": {"beams": [{"name": "B_1", "qty": 3}]},
    }

    def test_finds_every_occurrence_across_the_tree(self):
        paths = [p for p, _ in qj.find_objects(self.data, "B_1")]
        self.assertEqual(paths, [
            "members.beams[0]",
            "pages[0].members.beams[0]",
            "piecemarks.beams[0]",
        ])

    def test_match_is_case_insensitive(self):
        self.assertEqual(len(qj.find_objects(self.data, "b_2")), 1)

    def test_contains_matches_substrings(self):
        names = [obj["name"] for _, obj in qj.find_objects(self.data, "_1", contains=True)]
        self.assertEqual(sorted(names), ["B_1", "B_1", "B_1", "b_10"])

    def test_exact_does_not_match_substrings(self):
        self.assertEqual(qj.find_objects(self.data, "_1"), [])

    def test_key_selects_a_different_field(self):
        hits = qj.find_objects(self.data, "3", key="qty")
        self.assertEqual([p for p, _ in hits], ["piecemarks.beams[0]"])

    def test_container_values_never_match(self):
        data = {"a": {"name": ["B_1"]}, "b": {"name": {"x": "B_1"}}}
        self.assertEqual(qj.find_objects(data, "B_1"), [])

    def test_under_restricts_the_search_to_a_subtree(self):
        hits = qj.find_objects(self.data, "B_1", under="pages[].members")
        self.assertEqual([p for p, _ in hits], ["pages[0].members.beams[0]"])

    def test_under_accepts_an_unindexed_prefix(self):
        hits = qj.find_objects(self.data, "B_1", under="pages")
        self.assertEqual([p for p, _ in hits], ["pages[0].members.beams[0]"])

    def test_under_excludes_nested_noise(self):
        # A history/undo branch reusing the same field names must stay out of scope.
        data = {"members": {"beams": [{"name": "B_1"}]},
                "history": [{"payload": {"updates": [{"name": "B_1"}]}}]}
        hits = qj.find_objects(data, "B_1", under="members")
        self.assertEqual([p for p, _ in hits], ["members.beams[0]"])

    def test_returns_all_hits_so_caller_can_report_the_total(self):
        data = {"items": [{"name": "x"} for _ in range(100)]}
        self.assertEqual(len(qj.find_objects(data, "x")), 100)


class TrimTests(unittest.TestCase):
    def test_small_fields_survive(self):
        obj = {"name": "B_1", "geometry": {"x": 1, "y": 2}}
        self.assertEqual(qj.trim(obj), obj)

    def test_oversized_list_is_replaced_by_a_describing_placeholder(self):
        obj = {"name": "B_1", "drawings": ["segment-" + str(i) for i in range(200)]}
        out = qj.trim(obj)
        self.assertEqual(out["name"], "B_1")
        self.assertEqual(out["drawings"], "<list[200] trimmed>")

    def test_oversized_dict_placeholder_names_its_keys(self):
        obj = {"blob": {f"k{i}": "v" * 50 for i in range(20)}}
        self.assertIn("k0", qj.trim(obj)["blob"])
        self.assertIn("trimmed", qj.trim(obj)["blob"])

    def test_long_string_is_trimmed(self):
        out = qj.trim({"text": "x" * 5000})
        self.assertEqual(out["text"], "<str len=5000 trimmed>")

    def test_full_bypasses_trimming(self):
        obj = {"drawings": list(range(500))}
        self.assertEqual(qj.trim(obj, full=True), obj)

    def test_fields_selects_a_subset(self):
        obj = {"name": "B_1", "length": 12, "shape": "W"}
        self.assertEqual(qj.trim(obj, fields=["name", "shape"]), {"name": "B_1", "shape": "W"})

    def test_fields_ignores_absent_keys(self):
        self.assertEqual(qj.trim({"name": "B_1"}, fields=["name", "nope"]), {"name": "B_1"})

    def test_threshold_is_configurable(self):
        obj = {"items": [1, 2, 3, 4, 5]}
        self.assertEqual(qj.trim(obj, max_field_chars=1)["items"], "<list[5] trimmed>")
        self.assertEqual(qj.trim(obj, max_field_chars=10_000)["items"], [1, 2, 3, 4, 5])


class MeasureTests(unittest.TestCase):
    def test_stops_counting_once_past_the_limit(self):
        # 1M ints would be expensive to size exactly; the walk must abandon early.
        self.assertGreater(qj.measure(list(range(1_000_000)), 100), 100)

    def test_booleans_are_not_treated_as_ints(self):
        self.assertEqual(qj.measure(True, 100), 5)


class ListArraysTests(unittest.TestCase):
    data = {
        "members": {"beams": [{"name": "B_1"}, {"name": "B_2"}]},
        "pages": [
            {"members": {"beams": [{"name": "B_1"}, {"name": "B_3"}]}},
            {"members": {"beams": [{"name": "B_1"}, {"name": "B_4"}]}},
        ],
    }

    def _group(self, path, **kwargs):
        return next(g for g in qj.list_arrays(self.data, **kwargs) if g["path"] == path)

    def test_sibling_arrays_collapse_into_one_group(self):
        group = self._group("pages[].members.beams")
        self.assertEqual(group["arrays"], 2)
        self.assertEqual(group["count"], 4)

    def test_values_are_deduplicated_across_the_group(self):
        self.assertEqual(self._group("pages[].members.beams")["values"], ["B_1", "B_3", "B_4"])

    def test_under_restricts_to_a_subtree(self):
        groups = qj.list_arrays(self.data, under="members.beams")
        self.assertEqual([g["path"] for g in groups], ["members.beams"])

    def test_under_does_not_match_a_path_prefix_of_a_different_key(self):
        data = {"beams": [{"name": "a"}], "beamsExtra": [{"name": "b"}]}
        self.assertEqual([g["path"] for g in qj.list_arrays(data, under="beams")], ["beams"])

    def test_field_selects_a_different_key(self):
        data = {"routes": [{"operationId": "getUser"}, {"operationId": "putUser"}]}
        groups = qj.list_arrays(data, field="operationId")
        self.assertEqual(groups[0]["values"], ["getUser", "putUser"])

    def test_arrays_of_scalars_are_skipped(self):
        self.assertEqual(qj.list_arrays({"tags": ["a", "b"]}), [])

    def test_objects_without_the_field_still_count(self):
        groups = qj.list_arrays({"items": [{"id": 1}, {"id": 2}]})
        self.assertEqual(groups[0]["count"], 2)
        self.assertEqual(groups[0]["values"], [])


class SummaryShapeTests(unittest.TestCase):
    def test_dict_of_arrays_reports_per_key_counts(self):
        lines = qj.summarize({"members": {"beams": [1, 2, 3], "columns": [1]}}, _MissingPath())
        self.assertIn("members: dict of 2 arrays", lines)
        self.assertIn("  beams: 3", lines)
        self.assertIn("  columns: 1", lines)

    def test_list_of_dicts_reports_key_union_and_aggregates(self):
        data = {"pages": [
            {"number": 1, "members": {"beams": [1, 2]}},
            {"number": 2, "members": {"beams": [3], "columns": [4]}},
        ]}
        lines = qj.summarize(data, _MissingPath())
        self.assertIn("pages: list[2]", lines)
        self.assertIn("keys=['number', 'members']", lines)
        self.assertIn("  pages[].members (total across 2):", lines)
        self.assertIn("    beams: 3", lines)
        self.assertIn("    columns: 1", lines)

    def test_scalar_top_level_values_are_shown(self):
        self.assertIn("version: 4", qj.summarize({"version": 4}, _MissingPath()))

    def test_containers_are_listed_before_scalars(self):
        out = qj.summarize({"version": 4, "pages": [{"n": 1}]}, _MissingPath())
        self.assertLess(out.index("pages: list[1]"), out.index("scalars (1):"))

    def test_long_scalar_values_are_truncated(self):
        out = qj.summarize({"blob": "x" * 500}, _MissingPath())
        self.assertIn("...", out)
        self.assertNotIn("x" * 200, out)

    def test_root_list_is_handled(self):
        self.assertIn("root: list[2]", qj.summarize([{"name": "a"}, {"name": "b"}], _MissingPath()))


class _MissingPath:
    """Stands in for a Path in summary tests, which never touch the filesystem."""

    def exists(self) -> bool:
        return False

    def __str__(self) -> str:
        return "<fixture>"


if __name__ == "__main__":
    unittest.main()
