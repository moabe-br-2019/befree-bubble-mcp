"""The crawler must record the styles elements actually reference.

["styles"] is not readable through the path API (hash chain resolves back to the
app root), so without this the style map stays empty and every project-style
default is skipped as unproven.
"""

from bubble_mcp.context.detector import collect_referenced_styles


def test_collects_style_ids_from_nested_elements() -> None:
    pages = [
        {
            "name": "index",
            "elements": {
                "bNav": {
                    "%s1": "Button_primary_",
                    "elements": {"bText": {"%s1": "Text_body_"}, "bPlain": {"%p": {}}},
                }
            },
        }
    ]

    styles = collect_referenced_styles(pages)

    assert set(styles) == {"Button_primary_", "Text_body_"}
    assert styles["Text_body_"]["%nm"] == "Text_body_"


def test_ignores_blank_and_non_string_references() -> None:
    assert collect_referenced_styles({"%s1": "  "}) == {}
    assert collect_referenced_styles({"%s1": {"nested": "x"}}) == {}
    assert collect_referenced_styles(None) == {}
