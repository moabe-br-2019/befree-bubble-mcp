"""The ledger is append-only; the open state of each element is the fold of its lines."""

from __future__ import annotations

from pathlib import Path

from bubble_mcp.execution.tester_ids import ledger

P = ["%p3", "pg", "%el", "a"]


def _id(batch: str, old: object, new: object, status: str = "applied") -> dict:
    return {"kind": "id", "batch_id": batch, "app_id": "app", "app_version": "test",
            "pointer": P, "old_body": old, "new_body": new, "status": status}


def test_entries_are_appended_and_read_back(tmp_path: Path) -> None:
    ledger.append("p", _id("b1", None, "x"), config_dir=tmp_path)

    entries = ledger.read_entries("p", config_dir=tmp_path)

    assert entries[0]["batch_id"] == "b1"
    assert "at" in entries[0]
    assert ledger.ledger_path("p", tmp_path) == tmp_path / "tester" / "p" / "id-ledger.jsonl"


def test_a_torn_line_is_skipped(tmp_path: Path) -> None:
    ledger.append("p", _id("b1", None, "x"), config_dir=tmp_path)
    with ledger.ledger_path("p", tmp_path).open("a", encoding="utf-8") as handle:
        handle.write('{"kind": "id", "batch')

    assert len(ledger.read_entries("p", config_dir=tmp_path)) == 1


def test_two_changes_restore_to_the_first_old_value(tmp_path: Path) -> None:
    ledger.append("p", _id("b1", None, "x"), config_dir=tmp_path)
    ledger.append("p", _id("b2", "x", "y"), config_dir=tmp_path)

    [change] = ledger.open_changes("p", config_dir=tmp_path)

    assert change["old_body"] is None
    assert change["new_body"] == "y"
    assert change["batch_ids"] == ["b1", "b2"]


def test_failed_and_pending_lines_are_not_open(tmp_path: Path) -> None:
    ledger.append("p", _id("b1", None, "x", status="pending"), config_dir=tmp_path)
    ledger.append("p", _id("b2", None, "x", status="failed"), config_dir=tmp_path)

    assert ledger.open_changes("p", config_dir=tmp_path) == []


def test_a_restored_pointer_is_closed_and_filters_work(tmp_path: Path) -> None:
    ledger.append("p", _id("b1", None, "x"), config_dir=tmp_path)
    assert ledger.open_changes("p", batch_id="other", config_dir=tmp_path) == []
    assert len(ledger.open_changes("p", pointers=[P], config_dir=tmp_path)) == 1

    ledger.append("p", {"kind": "restore", "pointer": P, "batch_id": "b1"}, config_dir=tmp_path)

    assert ledger.open_changes("p", config_dir=tmp_path) == []


def test_expose_flips_open_until_restored(tmp_path: Path) -> None:
    flip = {"kind": "expose_id", "batch_id": "b1", "app_id": "app", "app_version": "test",
            "old": False, "new": True, "status": "applied"}
    ledger.append("p", flip, config_dir=tmp_path)
    assert len(ledger.open_expose_flips("p", config_dir=tmp_path)) == 1

    ledger.append("p", {"kind": "restore", "expose_id": True, "batch_id": "b1"}, config_dir=tmp_path)

    assert ledger.open_expose_flips("p", config_dir=tmp_path) == []


def test_append_recovers_from_torn_line(tmp_path: Path) -> None:
    """If a process dies mid-write, the next append must not glue to the torn line."""
    ledger.append("p", _id("b1", None, "x"), config_dir=tmp_path)
    # Simulate killed process: write a torn fragment without newline
    with ledger.ledger_path("p", tmp_path).open("a", encoding="utf-8") as handle:
        handle.write('{"kind": "id", "batch')
    # Append should prepend a newline to separate from the torn fragment
    ledger.append("p", _id("b2", "x", "y"), config_dir=tmp_path)

    entries = ledger.read_entries("p", config_dir=tmp_path)

    assert len(entries) == 2
    assert entries[0]["batch_id"] == "b1"
    assert entries[1]["batch_id"] == "b2"
