import pytest

from fbadsagent.config import Settings
from fbadsagent.llm.provider import (
    AnthropicProvider,
    GeminiProvider,
    LLMError,
    OpenAIProvider,
    get_llm_provider,
)


def test_anthropic_provider_requires_api_key():
    with pytest.raises(LLMError):
        AnthropicProvider(Settings(anthropic_api_key=""))


def test_anthropic_provider_generate_success(mocker):
    fake_block = mocker.Mock()
    fake_block.text = "Hello from Claude"
    fake_response = mocker.Mock()
    fake_response.content = [fake_block]
    fake_client = mocker.Mock()
    fake_client.messages.create.return_value = fake_response
    mocker.patch("anthropic.Anthropic", return_value=fake_client)

    provider = AnthropicProvider(Settings(anthropic_api_key="test-key"))
    result = provider.generate("system prompt", "user prompt", max_tokens=100)

    assert result == "Hello from Claude"


def test_anthropic_provider_wraps_api_errors(mocker):
    fake_client = mocker.Mock()
    fake_client.messages.create.side_effect = RuntimeError("rate limited")
    mocker.patch("anthropic.Anthropic", return_value=fake_client)

    provider = AnthropicProvider(Settings(anthropic_api_key="test-key"))
    with pytest.raises(LLMError, match="rate limited"):
        provider.generate("system", "prompt")


def test_openai_provider_requires_api_key():
    with pytest.raises(LLMError):
        OpenAIProvider(Settings(openai_api_key=""))


def test_openai_provider_generate_success(mocker):
    fake_message = mocker.Mock()
    fake_message.content = "Hello from GPT"
    fake_choice = mocker.Mock()
    fake_choice.message = fake_message
    fake_response = mocker.Mock()
    fake_response.choices = [fake_choice]
    fake_client = mocker.Mock()
    fake_client.chat.completions.create.return_value = fake_response
    mocker.patch("openai.OpenAI", return_value=fake_client)

    provider = OpenAIProvider(Settings(openai_api_key="test-key"))
    result = provider.generate("system prompt", "user prompt", max_tokens=100)

    assert result == "Hello from GPT"


def test_openai_provider_wraps_api_errors(mocker):
    fake_client = mocker.Mock()
    fake_client.chat.completions.create.side_effect = RuntimeError("bad request")
    mocker.patch("openai.OpenAI", return_value=fake_client)

    provider = OpenAIProvider(Settings(openai_api_key="test-key"))
    with pytest.raises(LLMError, match="bad request"):
        provider.generate("system", "prompt")


def test_gemini_provider_requires_api_key():
    with pytest.raises(LLMError):
        GeminiProvider(Settings(gemini_api_key=""))


def test_gemini_provider_generate_success(mocker):
    fake_response = mocker.Mock()
    fake_response.text = "Hello from Gemini"
    fake_client = mocker.Mock()
    fake_client.models.generate_content.return_value = fake_response
    mocker.patch("google.genai.Client", return_value=fake_client)

    provider = GeminiProvider(Settings(gemini_api_key="test-key"))
    result = provider.generate("system prompt", "user prompt", max_tokens=100)

    assert result == "Hello from Gemini"


def test_gemini_provider_wraps_api_errors(mocker):
    fake_client = mocker.Mock()
    fake_client.models.generate_content.side_effect = RuntimeError("quota exceeded")
    mocker.patch("google.genai.Client", return_value=fake_client)

    provider = GeminiProvider(Settings(gemini_api_key="test-key"))
    with pytest.raises(LLMError, match="quota exceeded"):
        provider.generate("system", "prompt")


def test_get_llm_provider_dispatches_by_setting(mocker):
    mocker.patch("anthropic.Anthropic")
    mocker.patch("openai.OpenAI")
    mocker.patch("google.genai.Client")

    assert isinstance(
        get_llm_provider(Settings(llm_provider="anthropic", anthropic_api_key="k")),
        AnthropicProvider,
    )
    assert isinstance(
        get_llm_provider(Settings(llm_provider="openai", openai_api_key="k")), OpenAIProvider
    )
    assert isinstance(
        get_llm_provider(Settings(llm_provider="gemini", gemini_api_key="k")), GeminiProvider
    )


def test_get_llm_provider_unknown_raises():
    with pytest.raises(LLMError):
        get_llm_provider(Settings(llm_provider="unknown-provider"))
