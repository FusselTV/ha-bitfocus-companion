"""The shipped blueprint has to stay loadable by Home Assistant."""

from __future__ import annotations

import pathlib

from homeassistant.components.automation.config import AUTOMATION_BLUEPRINT_SCHEMA
from homeassistant.components.blueprint.models import Blueprint
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template
from homeassistant.util.yaml import load_yaml_dict
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from .conftest import setup_entry

BLUEPRINT = (
    pathlib.Path(__file__).parent.parent
    / "blueprints/automation/bitfocus_companion/surface_screensaver.yaml"
)


def test_the_screensaver_blueprint_is_valid() -> None:
    """Home Assistant's own schema accepts it, with the documented inputs."""
    blueprint = Blueprint(
        load_yaml_dict(str(BLUEPRINT)),
        expected_domain="automation",
        schema=AUTOMATION_BLUEPRINT_SCHEMA,
    )
    assert set(blueprint.inputs) == {
        "screensaver_switches",
        "sleep_when",
        "sleep_delay",
        "wake_when",
    }

    # A sensor with an empty battery must not black out a studio, so unavailable and
    # unknown count as "someone is still there" in the trigger and in the condition.
    sleep_trigger = blueprint.data["triggers"][0]
    assert set(sleep_trigger["not_to"]) == {"on", "home", "unavailable", "unknown"}
    assert set(blueprint.data["variables"]["present_states"]) == {
        "on",
        "home",
        "unavailable",
        "unknown",
    }

    # Picking nothing has to mean every surface.
    section = blueprint.data["blueprint"]["input"]["surface_selection"]
    assert section["input"]["screensaver_switches"]["default"] == []


async def test_the_all_surfaces_template_picks_the_right_switches(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Render the blueprint's own expansion against real entities.

    Structure assertions cannot catch a wrong filter, so this runs the template
    Home Assistant would run and checks which entities come back.
    """
    await setup_entry(hass, mock_config_entry)
    blueprint = load_yaml_dict(str(BLUEPRINT))
    expansion = blueprint["variables"]["surfaces"]

    rendered = Template(expansion, hass).async_render(
        variables={"chosen_switches": []}, parse_result=False
    )

    assert "switch.test_surface_screensaver" in rendered
    assert "switch.front_of_house_screensaver" in rendered
    # The Enabled switch of a connection must never be dimmed.
    assert "switch.atem_enabled" not in rendered


async def test_picking_switches_bypasses_the_expansion(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """An explicit choice wins over "every surface"."""
    await setup_entry(hass, mock_config_entry)
    expansion = load_yaml_dict(str(BLUEPRINT))["variables"]["surfaces"]

    rendered = Template(expansion, hass).async_render(
        variables={"chosen_switches": ["switch.test_surface_screensaver"]},
        parse_result=False,
    )

    assert "front_of_house" not in rendered
