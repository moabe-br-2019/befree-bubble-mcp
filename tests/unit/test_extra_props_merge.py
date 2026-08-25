"""Builder calls must not receive extra_props twice.

_apply_common_surface_kwargs moves border_style into kwargs["extra_props"].
Call sites that also pass extra_props= explicitly (create_button,
create_repeating_group, the HTML input pipeline) raised
"got multiple values for keyword argument 'extra_props'" for any element
created with a border.
"""

import inspect

from bubble_mcp.aria_runtime.bubble_cli import BubbleCLI


def test_merge_extra_props_folds_kwargs_entry() -> None:
    kwargs = {"extra_props": {"%bos": "solid"}, "font_size": 15}

    merged = BubbleCLI._merge_extra_props(kwargs, {"nonant_alignment": "center"})

    assert merged == {"nonant_alignment": "center", "%bos": "solid"}
    assert "extra_props" not in kwargs
    assert kwargs["font_size"] == 15


def test_merge_extra_props_without_kwargs_entry() -> None:
    kwargs: dict = {}

    assert BubbleCLI._merge_extra_props(kwargs, {"a": 1}) == {"a": 1}
    assert BubbleCLI._merge_extra_props(kwargs) == {}


def test_builder_call_sites_merge_before_passing_extra_props() -> None:
    for method_name in ("create_button", "create_repeating_group"):
        source = inspect.getsource(getattr(BubbleCLI, method_name))
        assert "self._merge_extra_props(kwargs, extra_props)" in source, method_name
