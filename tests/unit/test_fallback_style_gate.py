"""The hardcoded fallback styles must not be stamped into apps that lack them.

Bubble's default template ships Button_primary_button_ and friends. An app
without those styles stores a dangling %s1 and the editor reports
"<element> - None (Custom) is not a possible option" for every element created.
"""

from bubble_mcp.visual_defaults import enforce_visual_create_payload_quality, fallback_style_for_element


def test_fallback_requires_a_known_style() -> None:
    assert fallback_style_for_element("Button", {"styles": {}}) is None
    assert fallback_style_for_element("Button", None) is None
    assert fallback_style_for_element("Button", {"styles": {"Button_primary_button_": {}}}) == (
        "Button_primary_button_"
    )
    assert fallback_style_for_element(
        "Button", {"styles": {"bStyleId": {"%nm": "Button_primary_button_"}}}
    ) == "bStyleId"


def test_button_payload_keeps_no_style_when_app_lacks_it() -> None:
    body = {"%x": "Button", "%dn": "btn_start", "%p": {}}

    enforce_visual_create_payload_quality(body, metadata={"styles": {}})

    assert "%s1" not in body


def test_button_payload_gets_style_when_app_has_it() -> None:
    body = {"%x": "Button", "%dn": "btn_start", "%p": {}}

    enforce_visual_create_payload_quality(body, metadata={"styles": {"Button_primary_button_": {}}})

    assert body["%s1"] == "Button_primary_button_"


def test_falls_back_to_the_app_own_style_by_naming_convention() -> None:
    metadata = {
        "styles": {
            "Button_filled_light_primary_": {"%nm": "Button_filled_light_primary_"},
            "Text_body_14_": {"%nm": "Text_body_14_"},
        }
    }

    assert fallback_style_for_element("Button", metadata) == "Button_filled_light_primary_"
    assert fallback_style_for_element("Text", metadata) == "Text_body_14_"
    assert fallback_style_for_element("Dropdown", metadata) is None


def test_app_style_fallback_returns_storage_id_when_name_differs() -> None:
    metadata = {"styles": {"bCustomStyle": {"%nm": "Button_custom_primary_"}}}

    assert fallback_style_for_element("Button", metadata) == "bCustomStyle"
