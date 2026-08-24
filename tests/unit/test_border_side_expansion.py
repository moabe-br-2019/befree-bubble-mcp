"""Shared border props must reach the per-side keys when four_border_style is on.

Bubble reads border_style_/border_width_/border_color_<side> once
four_border_style is true and ignores the shared %bos/%bw/%bc trio, so a button
created with border_style/border_width/border_color rendered with no border.
"""

from bubble_mcp.aria_runtime.bubble_sdk import ElementBuilder


def test_shared_border_is_mirrored_to_each_side() -> None:
    body = ElementBuilder().button(
        name="btn_mobile",
        label="Download",
        border_style="solid",
        border_width=1,
        border_color="#E4E4E4",
        all_4_borders=True,
    )
    props = body["%p"]

    assert props["four_border_style"] is True
    for side in ("top", "right", "bottom", "left"):
        assert props[f"border_style_{side}"] == "solid"
        assert props[f"border_width_{side}"] == 1
        assert props[f"border_color_{side}"] == "#E4E4E4"


def test_explicit_side_values_win() -> None:
    body = ElementBuilder().button(
        name="btn",
        label="Go",
        border_style="solid",
        border_width=1,
        border_color="#E4E4E4",
        all_4_borders=True,
        border_width_top=4,
    )

    assert body["%p"]["border_width_top"] == 4
    assert body["%p"]["border_width_bottom"] == 1


def test_shared_border_untouched_without_four_border_style() -> None:
    body = ElementBuilder().button(name="btn", label="Go", border_style="solid", border_width=1)
    props = body["%p"]

    assert props["%bos"] == "solid"
    assert "border_style_top" not in props
