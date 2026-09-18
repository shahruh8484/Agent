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


def test_anthropic_provider_generate_with_images(mocker, tmp_path):
    image_path = tmp_path / "screenshot.png"
    image_path.write_bytes(b"fake-png-bytes")

    fake_block = mocker.Mock()
    fake_block.text = "Headline: Never Miss a Beat"
    fake_response = mocker.Mock()
    fake_response.content = [fake_block]
    fake_client = mocker.Mock()
    fake_client.messages.create.return_value = fake_response
    mocker.patch("anthropic.Anthropic", return_value=fake_client)

    provider = AnthropicProvider(Settings(anthropic_api_key="test-key"))
    result = provider.generate_with_images("system", "describe this", [str(image_path)])

    assert result == "Headline: Never Miss a Beat"
    call_kwargs = fake_client.messages.create.call_args.kwargs
    content = call_kwargs["messages"][0]["content"]
    assert content[0]["type"] == "image"
    assert content[0]["source"]["media_type"] == "image/png"
    assert content[-1] == {"type": "text", "text": "describe this"}


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


def test_openai_provider_generate_with_images(mocker, tmp_path):
    image_path = tmp_path / "screenshot.png"
    image_path.write_bytes(b"fake-png-bytes")

    fake_message = mocker.Mock()
    fake_message.content = "Headline: Never Miss a Beat"
    fake_choice = mocker.Mock()
    fake_choice.message = fake_message
    fake_response = mocker.Mock()
    fake_response.choices = [fake_choice]
    fake_client = mocker.Mock()
    fake_client.chat.completions.create.return_value = fake_response
    mocker.patch("openai.OpenAI", return_value=fake_client)

    provider = OpenAIProvider(Settings(openai_api_key="test-key"))
    result = provider.generate_with_images("system", "describe this", [str(image_path)])

    assert result == "Headline: Never Miss a Beat"
    call_kwargs = fake_client.chat.completions.create.call_args.kwargs
    content = call_kwargs["messages"][1]["content"]
    assert content[0] == {"type": "text", "text": "describe this"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


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


def test_gemini_provider_generate_with_images(mocker, tmp_path):
    image_path = tmp_path / "screenshot.png"
    image_path.write_bytes(b"fake-png-bytes")

    fake_response = mocker.Mock()
    fake_response.text = "Headline: Never Miss a Beat"
    fake_client = mocker.Mock()
    fake_client.models.generate_content.return_value = fake_response
    mocker.patch("google.genai.Client", return_value=fake_client)

    provider = GeminiProvider(Settings(gemini_api_key="test-key"))
    result = provider.generate_with_images("system", "describe this", [str(image_path)])

    assert result == "Headline: Never Miss a Beat"
    call_kwargs = fake_client.models.generate_content.call_args.kwargs
    assert len(call_kwargs["contents"]) == 2


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
