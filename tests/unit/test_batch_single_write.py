"""A batch of N commands must reach Bubble as one /appeditor/write call.

Each command used to post its own payload, so a 19-element page cost 19 round
trips. Bubble accepts any number of changes in a single write.
"""

from bubble_mcp.aria_dispatch import merge_write_payloads


def test_merge_concatenates_changes_and_keeps_envelope() -> None:
    merged = merge_write_payloads(
        [
            {"appname": "demo", "app_version": "test", "changes": [{"path_array": ["a"]}]},
            {"appname": "demo", "app_version": "test", "changes": [{"path_array": ["b"]}, {"path_array": ["c"]}]},
        ]
    )

    assert merged is not None
    assert merged["appname"] == "demo"
    assert merged["app_version"] == "test"
    assert [change["path_array"][0] for change in merged["changes"]] == ["a", "b", "c"]


def test_merge_ignores_payloads_without_changes() -> None:
    merged = merge_write_payloads([{"appname": "demo"}, {"appname": "demo", "changes": [{"path_array": ["a"]}]}])

    assert merged is not None
    assert len(merged["changes"]) == 1


def test_merge_returns_none_when_nothing_usable() -> None:
    assert merge_write_payloads([]) is None
    assert merge_write_payloads([{"appname": "demo"}]) is None
