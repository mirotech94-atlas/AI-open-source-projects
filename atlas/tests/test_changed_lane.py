"""The changed-files lane picks the tests a change can reach (ops/test_changed.py)."""
from __future__ import annotations

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "test_changed_lane_tool", ROOT / "ops" / "test_changed.py")
lane = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lane)


def test_a_changed_test_file_selects_itself():
    assert lane.select(["tests/test_image_pins.py"]) == ["tests/test_image_pins.py"]


def test_the_map_file_routes_build_files_to_the_pin_tests():
    for path in ("constraints.txt", "Dockerfile", ".dockerignore"):
        assert "tests/test_image_pins.py" in lane.select([path])


def test_a_module_selects_the_tests_that_import_it():
    picked = lane.select(["atlas/kernel/executor.py"])
    assert picked, "executor has importers"
    for name in picked:
        text = (ROOT / name).read_text(errors="replace")
        assert "executor" in text


def test_the_schema_selects_the_postgres_files():
    picked = lane.select(["db/schema.sql"])
    assert "tests/test_decision_pg.py" in picked


def test_a_file_nothing_imports_selects_nothing():
    # Built in two halves: a literal path here would be a mention of itself.
    assert lane.select(["docs/" + "no-test-names-this.md"]) == []


def test_a_helper_selects_the_tests_that_import_it():
    picked = lane.select(["tests/acceptance_support.py"])
    assert picked and all(p.startswith("tests/test_") for p in picked)
