import httpx
import pytest
from desk.llm import ModelClient, ModelError
from desk.store import Store


def client(tmp_path, handler):
    store = Store(tmp_path)
    store.save_model(
        {
            "base_url": "https://models.test/v1",
            "model": "multimodal",
            "vision_model": "multimodal",
            "vision_enabled": True,
            "api_key": "fixture-secret",
        }
    )
    return ModelClient(store, httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_transient_image_transport_error_recovers_without_changing_the_model(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.RemoteProtocolError("upstream disconnected")
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": "商品资料：75kg"}}]}
        )

    result = await client(tmp_path, handler).transcribe("fixture-base64", mime="image/jpeg")
    assert result == "商品资料：75kg" and len(requests) == 2
    assert requests[0].content == requests[1].content


@pytest.mark.asyncio
async def test_non_json_relay_failure_is_reported_as_response_error(tmp_path):
    with pytest.raises(ModelError, match="JSON"):
        await client(
            tmp_path,
            lambda r: httpx.Response(200, text="upstream error", headers={"content-type": "text/html"}),
        ).transcribe("fixture")


@pytest.mark.asyncio
async def test_text_content_blocks_from_multimodal_proxy_are_readable(tmp_path):
    response = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": [
                        {"type": "text", "text": "规格：75kg"},
                        {"type": "text", "text": "颜色：米白"},
                    ]
                },
            }
        ]
    }
    assert (
        await client(tmp_path, lambda r: httpx.Response(200, json=response)).transcribe("fixture")
        == "规格：75kg\n颜色：米白"
    )


@pytest.mark.asyncio
async def test_invalid_auth_is_not_retried_and_provider_message_is_not_exposed(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200, json={"error": {"code": "invalid_api_key", "message": "reflected-sensitive-key"}}
        )

    with pytest.raises(ModelError, match="invalid_api_key") as caught:
        await client(tmp_path, handler).transcribe("fixture")
    assert len(calls) == 1 and "reflected-sensitive-key" not in str(caught.value)


@pytest.mark.asyncio
async def test_transient_image_error_has_a_bounded_retry_count(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503)

    with pytest.raises(ModelError, match="HTTP 503"):
        await client(tmp_path, handler).transcribe("fixture")
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_reasoning_blocks_are_not_written_as_transcribed_text(tmp_path):
    body = {
        "choices": [
            {
                "message": {
                    "content": [
                        {"type": "reasoning", "text": "hidden thoughts"},
                        {"type": "text", "text": "Visible product facts"},
                    ]
                }
            }
        ]
    }
    result = await client(tmp_path, lambda r: httpx.Response(200, json=body)).transcribe("fixture")
    assert result == "Visible product facts"
