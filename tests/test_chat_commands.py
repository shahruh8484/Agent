import json

from fbadsagent.web.chat_commands import (
    MAX_CAMPAIGNS_PER_COMMAND,
    parse_launch_command,
)
from tests.conftest import FakeLLM


def test_parse_launch_command_recognizes_launch_request():
    llm = FakeLLM(
        response=json.dumps(
            {
                "is_launch_command": True,
                "product_name": "Glycofort",
                "count": 2,
                "daily_budget": 5,
            }
        )
    )

    command = parse_launch_command(llm, "запусти 2 рк на Glycofort с 5 долларами бюджета")

    assert command is not None
    assert command.product_name == "Glycofort"
    assert command.count == 2
    assert command.daily_budget == 5


def test_parse_launch_command_returns_none_for_normal_message():
    llm = FakeLLM(
        response=json.dumps(
            {"is_launch_command": False, "product_name": "", "count": 1, "daily_budget": None}
        )
    )

    command = parse_launch_command(llm, "what's my CPC been like this week?")

    assert command is None


def test_parse_launch_command_returns_none_on_bad_json():
    llm = FakeLLM(response="not json at all")

    command = parse_launch_command(llm, "hello")

    assert command is None


def test_parse_launch_command_caps_count():
    llm = FakeLLM(
        response=json.dumps(
            {"is_launch_command": True, "product_name": "Glycofort", "count": 50, "daily_budget": None}
        )
    )

    command = parse_launch_command(llm, "launch 50 campaigns for Glycofort")

    assert command.count == MAX_CAMPAIGNS_PER_COMMAND


def test_parse_launch_command_defaults_count_to_one():
    llm = FakeLLM(
        response=json.dumps(
            {"is_launch_command": True, "product_name": "Glycofort", "count": 0, "daily_budget": None}
        )
    )

    command = parse_launch_command(llm, "launch Glycofort")

    assert command.count == 1
