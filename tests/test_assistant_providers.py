from __future__ import annotations

from dataclasses import replace
import json
import unittest

import requests

from photometry_app.assistant.messages import ChatMessage, MessageRole, StopReason, ToolCall, ToolSpec
from photometry_app.assistant.providers import (
    PROVIDER_IDS,
    ProviderConfig,
    ProviderError,
    create_provider,
    normalize_provider_id,
    normalize_reasoning_effort,
    provider_requires_api_key,
)
from photometry_app.assistant.providers.gemini import gemini_schema


SECRET_KEY = "sk-secret-do-not-leak"

LIST_FOLDER_TOOL = ToolSpec(
    name="list_folder_images",
    description="List supported images in a folder.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Folder to list."},
            "recursive": {"type": "boolean", "default": True},
        },
        "required": ["path"],
        "additionalProperties": False,
    },
)


class FakeResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    def __init__(self, *responses: FakeResponse | Exception) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, object]] = []

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        return self._respond("post", url, kwargs)

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        return self._respond("get", url, kwargs)

    def _respond(self, method: str, url: str, kwargs: dict[str, object]) -> FakeResponse:
        self.requests.append({"method": method, "url": url, **kwargs})
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _config(provider_id: str, *, api_key: str | None = SECRET_KEY, base_url: str = "", model: str = "test-model") -> ProviderConfig:
    defaults = {
        "openai_compatible": "https://api.openai.com/v1",
        "anthropic": "https://api.anthropic.com",
        "gemini": "https://generativelanguage.googleapis.com",
    }
    return ProviderConfig(provider_id=provider_id, model=model, api_key=api_key, base_url=base_url or defaults[provider_id])


def _openai_tool_response() -> dict[str, object]:
    return {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_abc",
                            "type": "function",
                            "function": {"name": "list_folder_images", "arguments": '{"path": "C:/Data/M13"}'},
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 120, "completion_tokens": 18},
    }


def _openai_text_response(text: str) -> dict[str, object]:
    return {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": text}}]}


COMPATIBLE_SERVER_URL = "https://llm.example.com/v1"


def _responses_tool_response(*, encrypted: bool = True) -> dict[str, object]:
    reasoning: dict[str, object] = {"type": "reasoning", "id": "rs_1", "summary": []}
    if encrypted:
        reasoning["encrypted_content"] = "opaque-reasoning"
    return {
        "status": "completed",
        "output": [
            reasoning,
            {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_abc",
                "name": "list_folder_images",
                "arguments": '{"path": "C:/Data/M13"}',
                "status": "completed",
            },
        ],
        "usage": {"input_tokens": 150, "output_tokens": 30},
    }


def _responses_text_response(text: str) -> dict[str, object]:
    return {
        "status": "completed",
        "output": [
            {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": text, "annotations": []}]}
        ],
    }


def _anthropic_tool_response() -> dict[str, object]:
    return {
        "content": [
            {"type": "text", "text": "Let me look."},
            {"type": "tool_use", "id": "toolu_1", "name": "list_folder_images", "input": {"path": "C:/Data/M13"}},
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 90, "output_tokens": 12},
    }


def _anthropic_text_response(text: str) -> dict[str, object]:
    return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}


def _gemini_tool_response() -> dict[str, object]:
    return {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [
                        {"text": "internal reasoning", "thought": True},
                        {
                            "functionCall": {"name": "list_folder_images", "args": {"path": "C:/Data/M13"}},
                            "thoughtSignature": "opaque-signature",
                        },
                    ],
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {"promptTokenCount": 70, "candidatesTokenCount": 9},
    }


def _gemini_text_response(text: str) -> dict[str, object]:
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}]}


_SCRIPTED_RESPONSES = {
    "openai_compatible": (_responses_tool_response, _responses_text_response, ""),
    "openai_compatible (chat completions)": (_openai_tool_response, _openai_text_response, COMPATIBLE_SERVER_URL),
    "anthropic": (_anthropic_tool_response, _anthropic_text_response, ""),
    "gemini": (_gemini_tool_response, _gemini_text_response, ""),
}


class ScriptedConversationTest(unittest.TestCase):
    """The same tool-using exchange must work identically through every adapter."""

    def test_tool_round_trip_through_every_provider(self) -> None:
        self.assertEqual({name.split(" ")[0] for name in _SCRIPTED_RESPONSES}, set(PROVIDER_IDS))
        for name, (tool_response, text_response, base_url) in _SCRIPTED_RESPONSES.items():
            provider_id = name.split(" ")[0]
            with self.subTest(provider=name):
                session = FakeSession(
                    FakeResponse(200, tool_response()),
                    FakeResponse(200, text_response("The folder has 42 light frames.")),
                )
                provider = create_provider(_config(provider_id, base_url=base_url), session=session)
                history = [ChatMessage.user("What's in C:/Data/M13?")]

                first = provider.complete(system="You are CAst's guide.", messages=history, tools=[LIST_FOLDER_TOOL])
                self.assertEqual(first.stop_reason, StopReason.TOOL_CALLS)
                self.assertEqual(len(first.message.tool_calls), 1)
                call = first.message.tool_calls[0]
                self.assertEqual(call.name, "list_folder_images")
                self.assertEqual(call.arguments, {"path": "C:/Data/M13"})
                self.assertEqual(call.arguments_error, "")
                self.assertTrue(call.call_id)

                history.append(first.message)
                history.append(ChatMessage.tool_result(call, json.dumps({"light_frames": 42})))
                second = provider.complete(system="You are CAst's guide.", messages=history, tools=[LIST_FOLDER_TOOL])
                self.assertEqual(second.stop_reason, StopReason.END)
                self.assertEqual(second.message.text, "The folder has 42 light frames.")
                self.assertEqual(second.message.tool_calls, ())
                self.assertEqual(len(session.requests), 2)


class OpenAICompatibleProviderTest(unittest.TestCase):
    def test_request_shape_and_follow_up_messages(self) -> None:
        session = FakeSession(FakeResponse(200, _openai_tool_response()), FakeResponse(200, _openai_text_response("done")))
        provider = create_provider(_config("openai_compatible", base_url=COMPATIBLE_SERVER_URL), session=session)
        first = provider.complete(system="sys", messages=[ChatMessage.user("hi")], tools=[LIST_FOLDER_TOOL])

        request = session.requests[0]
        self.assertEqual(request["url"], "https://llm.example.com/v1/chat/completions")
        self.assertEqual(request["headers"]["Authorization"], f"Bearer {SECRET_KEY}")
        self.assertEqual(request["json"]["messages"][0], {"role": "system", "content": "sys"})
        self.assertEqual(request["json"]["tools"][0]["function"]["name"], "list_folder_images")
        self.assertEqual(first.input_tokens, 120)
        self.assertEqual(first.output_tokens, 18)

        call = first.message.tool_calls[0]
        provider.complete(
            system="sys",
            messages=[ChatMessage.user("hi"), first.message, ChatMessage.tool_result(call, '{"ok": true}')],
        )
        follow_up = session.requests[1]["json"]["messages"]
        self.assertEqual(follow_up[2]["tool_calls"][0]["id"], "call_abc")
        self.assertEqual(json.loads(follow_up[2]["tool_calls"][0]["function"]["arguments"]), {"path": "C:/Data/M13"})
        self.assertEqual(follow_up[3], {"role": "tool", "tool_call_id": "call_abc", "content": '{"ok": true}'})
        self.assertNotIn("tools", session.requests[1]["json"])

    def test_invalid_tool_arguments_are_reported_not_raised(self) -> None:
        payload = _openai_tool_response()
        payload["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "{not json"
        provider = create_provider(_config("openai_compatible", base_url=COMPATIBLE_SERVER_URL), session=FakeSession(FakeResponse(200, payload)))
        call = provider.complete(system="", messages=[ChatMessage.user("hi")]).message.tool_calls[0]
        self.assertEqual(call.arguments, {})
        self.assertIn("not valid JSON", call.arguments_error)

    def test_local_server_works_without_key_but_remote_does_not(self) -> None:
        session = FakeSession(FakeResponse(200, _openai_text_response("hello")))
        local = create_provider(_config("openai_compatible", api_key=None, base_url="http://localhost:11434/v1"), session=session)
        self.assertEqual(local.complete(system="", messages=[ChatMessage.user("hi")]).message.text, "hello")
        self.assertNotIn("Authorization", session.requests[0]["headers"])
        self.assertEqual(session.requests[0]["url"], "http://localhost:11434/v1/chat/completions")

        remote = create_provider(_config("openai_compatible", api_key=None), session=FakeSession())
        with self.assertRaisesRegex(ProviderError, "API key"):
            remote.complete(system="", messages=[ChatMessage.user("hi")])

    def test_missing_model_asks_user_to_choose_one(self) -> None:
        provider = create_provider(_config("openai_compatible", model=" "), session=FakeSession())
        with self.assertRaisesRegex(ProviderError, "Choose .* model"):
            provider.complete(system="", messages=[ChatMessage.user("hi")])

    def test_list_models(self) -> None:
        session = FakeSession(FakeResponse(200, {"data": [{"id": "b-model"}, {"id": "a-model"}, {"id": "b-model"}]}))
        provider = create_provider(_config("openai_compatible"), session=session)
        self.assertEqual(provider.list_models(), ["a-model", "b-model"])
        self.assertEqual(session.requests[0]["url"], "https://api.openai.com/v1/models")


class OpenAIResponsesTest(unittest.TestCase):
    def test_openai_itself_uses_the_responses_api(self) -> None:
        session = FakeSession(FakeResponse(200, _responses_tool_response()), FakeResponse(200, _responses_text_response("done")))
        provider = create_provider(_config("openai_compatible"), session=session)
        first = provider.complete(system="sys", messages=[ChatMessage.user("hi")], tools=[LIST_FOLDER_TOOL])

        request = session.requests[0]
        self.assertEqual(request["url"], "https://api.openai.com/v1/responses")
        self.assertEqual(request["headers"]["Authorization"], f"Bearer {SECRET_KEY}")
        body = request["json"]
        self.assertEqual(body["instructions"], "sys")
        self.assertEqual(body["input"], [{"role": "user", "content": "hi"}])
        self.assertIs(body["store"], False)
        self.assertEqual(body["include"], ["reasoning.encrypted_content"])
        self.assertEqual(body["tools"][0]["type"], "function")
        self.assertEqual(body["tools"][0]["name"], "list_folder_images")
        self.assertEqual(body["tools"][0]["parameters"], LIST_FOLDER_TOOL.parameters)
        self.assertEqual(first.stop_reason, StopReason.TOOL_CALLS)
        self.assertEqual(first.message.tool_calls[0].call_id, "call_abc")
        self.assertEqual((first.input_tokens, first.output_tokens), (150, 30))

        call = first.message.tool_calls[0]
        second = provider.complete(system="sys", messages=[ChatMessage.user("hi"), first.message, ChatMessage.tool_result(call, '{"ok": true}')])
        replay = session.requests[1]["json"]["input"]
        self.assertEqual(replay[1], _responses_tool_response()["output"][0])
        self.assertEqual(replay[2]["id"], "fc_1")
        self.assertEqual(replay[3], {"type": "function_call_output", "call_id": "call_abc", "output": '{"ok": true}'})
        self.assertEqual(second.message.text, "done")
        self.assertEqual(second.stop_reason, StopReason.END)

    def test_unencrypted_reasoning_is_not_replayed(self) -> None:
        session = FakeSession(FakeResponse(200, _responses_tool_response(encrypted=False)), FakeResponse(200, _responses_text_response("done")))
        provider = create_provider(_config("openai_compatible"), session=session)
        first = provider.complete(system="", messages=[ChatMessage.user("hi")], tools=[LIST_FOLDER_TOOL])
        call = first.message.tool_calls[0]
        provider.complete(system="", messages=[ChatMessage.user("hi"), first.message, ChatMessage.tool_result(call, "{}")])
        replay = session.requests[1]["json"]["input"]
        self.assertEqual([item.get("type") for item in replay[1:]], ["function_call", "function_call_output"])
        self.assertNotIn("id", replay[1])

    def test_history_from_another_api_is_rebuilt(self) -> None:
        session = FakeSession(FakeResponse(200, _responses_text_response("ok")))
        provider = create_provider(_config("openai_compatible"), session=session)
        call = ToolCall("call_1", "list_folder_images", {"path": "C:/Data"})
        history = [ChatMessage.user("hi"), ChatMessage.assistant("Looking.", (call,)), ChatMessage.tool_result(call, "{}")]
        provider.complete(system="", messages=history)
        self.assertEqual(
            session.requests[0]["json"]["input"][1:],
            [
                {"role": "assistant", "content": "Looking."},
                {"type": "function_call", "call_id": "call_1", "name": "list_folder_images", "arguments": '{"path": "C:/Data"}'},
                {"type": "function_call_output", "call_id": "call_1", "output": "{}"},
            ],
        )

    def test_server_that_asks_for_responses_is_retried_there(self) -> None:
        refusal = {
            "error": {
                "message": "Function tools with reasoning_effort are not supported for gpt-5.6-sol in /v1/chat/completions. "
                "To use function tools, use /v1/responses or set reasoning_effort to 'none'."
            }
        }
        session = FakeSession(
            FakeResponse(400, refusal),
            FakeResponse(200, _responses_text_response("hello")),
            FakeResponse(200, _responses_text_response("again")),
        )
        provider = create_provider(_config("openai_compatible", base_url=COMPATIBLE_SERVER_URL), session=session)
        self.assertEqual(provider.complete(system="", messages=[ChatMessage.user("hi")], tools=[LIST_FOLDER_TOOL]).message.text, "hello")
        self.assertEqual(provider.complete(system="", messages=[ChatMessage.user("hi")]).message.text, "again")
        self.assertEqual(
            [request["url"] for request in session.requests],
            [f"{COMPATIBLE_SERVER_URL}/chat/completions", f"{COMPATIBLE_SERVER_URL}/responses", f"{COMPATIBLE_SERVER_URL}/responses"],
        )

    def test_other_bad_requests_are_not_retried(self) -> None:
        session = FakeSession(FakeResponse(400, {"error": {"message": "Unknown parameter."}}))
        provider = create_provider(_config("openai_compatible", base_url=COMPATIBLE_SERVER_URL), session=session)
        with self.assertRaisesRegex(ProviderError, "Unknown parameter"):
            provider.complete(system="", messages=[ChatMessage.user("hi")])
        self.assertEqual(len(session.requests), 1)

    def test_model_without_encrypted_reasoning_is_retried_without_it(self) -> None:
        session = FakeSession(
            FakeResponse(400, {"error": {"message": "Encrypted content is not supported with this model."}}),
            FakeResponse(200, _responses_text_response("hello")),
            FakeResponse(200, _responses_text_response("again")),
        )
        provider = create_provider(_config("openai_compatible"), session=session)
        self.assertEqual(provider.complete(system="", messages=[ChatMessage.user("hi")]).message.text, "hello")
        provider.complete(system="", messages=[ChatMessage.user("hi")])
        self.assertIn("include", session.requests[0]["json"])
        self.assertNotIn("include", session.requests[1]["json"])
        self.assertNotIn("include", session.requests[2]["json"])

    def test_cut_off_and_failed_responses(self) -> None:
        cut_off = {**_responses_text_response("Partial"), "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}
        failed = {"status": "failed", "output": [], "error": {"code": "server_error", "message": "Something broke."}}
        session = FakeSession(FakeResponse(200, cut_off), FakeResponse(200, failed))
        provider = create_provider(_config("openai_compatible"), session=session)
        response = provider.complete(system="", messages=[ChatMessage.user("hi")])
        self.assertEqual((response.message.text, response.stop_reason), ("Partial", StopReason.MAX_TOKENS))
        with self.assertRaisesRegex(ProviderError, "Something broke"):
            provider.complete(system="", messages=[ChatMessage.user("hi")])


class AnthropicProviderTest(unittest.TestCase):
    def test_request_shape_merges_tool_results_into_one_user_turn(self) -> None:
        session = FakeSession(FakeResponse(200, _anthropic_text_response("ok")))
        provider = create_provider(_config("anthropic"), session=session)
        first_call = ToolCall(call_id="toolu_1", name="list_folder_images", arguments={"path": "a"})
        second_call = ToolCall(call_id="toolu_2", name="list_folder_images", arguments={"path": "b"})
        history = [
            ChatMessage.user("compare a and b"),
            ChatMessage.assistant("", (first_call, second_call)),
            ChatMessage.tool_result(first_call, "A result"),
            ChatMessage.tool_result(second_call, "failed", is_error=True),
        ]
        provider.complete(system="sys", messages=history, tools=[LIST_FOLDER_TOOL])

        request = session.requests[0]
        self.assertEqual(request["url"], "https://api.anthropic.com/v1/messages")
        self.assertEqual(request["headers"]["x-api-key"], SECRET_KEY)
        self.assertEqual(request["headers"]["anthropic-version"], "2023-06-01")
        body = request["json"]
        self.assertEqual(body["system"], "sys")
        self.assertGreater(body["max_tokens"], 0)
        self.assertEqual(body["tools"][0]["input_schema"], LIST_FOLDER_TOOL.parameters)
        self.assertEqual([row["role"] for row in body["messages"]], ["user", "assistant", "user"])
        self.assertEqual([block["type"] for block in body["messages"][1]["content"]], ["tool_use", "tool_use"])
        results = body["messages"][2]["content"]
        self.assertEqual([block["tool_use_id"] for block in results], ["toolu_1", "toolu_2"])
        self.assertEqual([block["is_error"] for block in results], [False, True])

    def test_assistant_content_is_replayed_verbatim(self) -> None:
        session = FakeSession(FakeResponse(200, _anthropic_tool_response()), FakeResponse(200, _anthropic_text_response("ok")))
        provider = create_provider(_config("anthropic"), session=session)
        first = provider.complete(system="", messages=[ChatMessage.user("hi")])
        self.assertEqual(first.message.text, "Let me look.")
        call = first.message.tool_calls[0]
        provider.complete(system="", messages=[ChatMessage.user("hi"), first.message, ChatMessage.tool_result(call, "x")])
        self.assertEqual(session.requests[1]["json"]["messages"][1]["content"], _anthropic_tool_response()["content"])

    def test_missing_key_is_reported_before_any_request(self) -> None:
        session = FakeSession()
        provider = create_provider(_config("anthropic", api_key=""), session=session)
        with self.assertRaisesRegex(ProviderError, "Anthropic API key"):
            provider.complete(system="", messages=[ChatMessage.user("hi")])
        self.assertEqual(session.requests, [])


class GeminiProviderTest(unittest.TestCase):
    def test_request_shape_and_schema_cleaning(self) -> None:
        session = FakeSession(FakeResponse(200, _gemini_text_response("ok")))
        provider = create_provider(_config("gemini", model="models/gemini-test"), session=session)
        provider.complete(system="sys", messages=[ChatMessage.user("hi")], tools=[LIST_FOLDER_TOOL])

        request = session.requests[0]
        self.assertEqual(request["url"], "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent")
        self.assertEqual(request["headers"]["x-goog-api-key"], SECRET_KEY)
        self.assertNotIn(SECRET_KEY, request["url"])
        body = request["json"]
        self.assertEqual(body["systemInstruction"], {"parts": [{"text": "sys"}]})
        parameters = body["tools"][0]["functionDeclarations"][0]["parameters"]
        self.assertNotIn("additionalProperties", parameters)
        self.assertNotIn("default", parameters["properties"]["recursive"])
        self.assertEqual(parameters["required"], ["path"])

    def test_thought_parts_are_hidden_but_signatures_are_replayed(self) -> None:
        session = FakeSession(FakeResponse(200, _gemini_tool_response()), FakeResponse(200, _gemini_text_response("ok")))
        provider = create_provider(_config("gemini"), session=session)
        first = provider.complete(system="", messages=[ChatMessage.user("hi")])
        self.assertEqual(first.message.text, "")
        self.assertEqual(first.stop_reason, StopReason.TOOL_CALLS)
        call = first.message.tool_calls[0]

        provider.complete(
            system="",
            messages=[ChatMessage.user("hi"), first.message, ChatMessage.tool_result(call, '{"light_frames": 42}')],
        )
        contents = session.requests[1]["json"]["contents"]
        self.assertEqual(contents[1]["role"], "model")
        self.assertEqual(contents[1]["parts"][1]["thoughtSignature"], "opaque-signature")
        self.assertEqual(
            contents[2]["parts"][0]["functionResponse"],
            {"name": "list_folder_images", "response": {"light_frames": 42}},
        )

    def test_plain_text_tool_results_and_errors_are_wrapped(self) -> None:
        session = FakeSession(FakeResponse(200, _gemini_text_response("ok")))
        provider = create_provider(_config("gemini"), session=session)
        call = ToolCall(call_id="c1", name="list_folder_images", arguments={})
        provider.complete(
            system="",
            messages=[ChatMessage.user("hi"), ChatMessage.assistant("", (call,)), ChatMessage.tool_result(call, "Folder not found", is_error=True)],
        )
        response = session.requests[0]["json"]["contents"][2]["parts"][0]["functionResponse"]["response"]
        self.assertEqual(response, {"error": {"content": "Folder not found"}})

    def test_blocked_prompt_raises_readable_error(self) -> None:
        session = FakeSession(FakeResponse(200, {"promptFeedback": {"blockReason": "SAFETY"}}))
        provider = create_provider(_config("gemini"), session=session)
        with self.assertRaisesRegex(ProviderError, "blocked the request \\(SAFETY\\)"):
            provider.complete(system="", messages=[ChatMessage.user("hi")])

    def test_list_models_keeps_generate_content_models(self) -> None:
        session = FakeSession(
            FakeResponse(
                200,
                {
                    "models": [
                        {"name": "models/gemini-chat", "supportedGenerationMethods": ["generateContent"]},
                        {"name": "models/text-embedding", "supportedGenerationMethods": ["embedContent"]},
                    ]
                },
            )
        )
        provider = create_provider(_config("gemini"), session=session)
        self.assertEqual(provider.list_models(), ["gemini-chat"])

    def test_gemini_schema_recurses_into_items(self) -> None:
        cleaned = gemini_schema(
            {"type": "array", "items": {"type": "object", "properties": {"x": {"type": "number", "exclusiveMinimum": 0}}, "additionalProperties": False}}
        )
        self.assertEqual(cleaned, {"type": "array", "items": {"type": "object", "properties": {"x": {"type": "number"}}}})


class ReasoningEffortTest(unittest.TestCase):
    def _request(self, provider_id: str, effort: str, *, base_url: str = "", model: str = "test-model") -> dict[str, object]:
        responses = {
            "openai_compatible": _openai_text_response("ok") if base_url else _responses_text_response("ok"),
            "anthropic": _anthropic_text_response("ok"),
            "gemini": _gemini_text_response("ok"),
        }
        session = FakeSession(FakeResponse(200, responses[provider_id]))
        config = replace(_config(provider_id, base_url=base_url, model=model), reasoning_effort=effort)
        create_provider(config, session=session).complete(system="", messages=[ChatMessage.user("hi")])
        return session.requests[0]["json"]

    def test_default_sends_no_reasoning_settings(self) -> None:
        self.assertNotIn("reasoning", self._request("openai_compatible", "default"))
        self.assertNotIn("reasoning_effort", self._request("openai_compatible", "default", base_url=COMPATIBLE_SERVER_URL))
        anthropic = self._request("anthropic", "default")
        self.assertNotIn("thinking", anthropic)
        self.assertEqual(anthropic["max_tokens"], 4096)
        self.assertNotIn("thinkingConfig", self._request("gemini", "default")["generationConfig"])
        self.assertEqual(normalize_reasoning_effort("EXTREME"), "default")

    def test_openai_effort(self) -> None:
        self.assertEqual(self._request("openai_compatible", "high")["reasoning"], {"effort": "high"})
        self.assertEqual(self._request("openai_compatible", "low", base_url=COMPATIBLE_SERVER_URL)["reasoning_effort"], "low")

    def test_claude_effort_becomes_a_thinking_budget(self) -> None:
        body = self._request("anthropic", "medium")
        self.assertEqual(body["thinking"], {"type": "enabled", "budget_tokens": 8192})
        self.assertGreater(body["max_tokens"], 8192)

    def test_gemini_effort_uses_budget_for_2_5_and_level_after(self) -> None:
        old = self._request("gemini", "high", model="gemini-2.5-pro")["generationConfig"]
        self.assertEqual(old["thinkingConfig"], {"thinkingBudget": 24576})
        self.assertGreater(old["maxOutputTokens"], 24576)
        new = self._request("gemini", "low", model="gemini-3-pro")["generationConfig"]
        self.assertEqual(new["thinkingConfig"], {"thinkingLevel": "low"})


class ProviderErrorTest(unittest.TestCase):
    def test_rejected_key_message_is_readable_and_never_contains_the_key(self) -> None:
        for provider_id in PROVIDER_IDS:
            with self.subTest(provider=provider_id):
                session = FakeSession(FakeResponse(401, {"error": {"message": "Incorrect API key provided"}}))
                provider = create_provider(_config(provider_id), session=session)
                with self.assertRaises(ProviderError) as caught:
                    provider.complete(system="", messages=[ChatMessage.user("hi")])
                message = str(caught.exception)
                self.assertIn("rejected the API key", message)
                self.assertIn("Incorrect API key provided", message)
                self.assertNotIn(SECRET_KEY, message)
                self.assertEqual(caught.exception.status_code, 401)
                self.assertFalse(caught.exception.retryable)

    def test_rate_limit_and_server_errors_are_retryable(self) -> None:
        for status in (429, 503):
            with self.subTest(status=status):
                provider = create_provider(_config("anthropic"), session=FakeSession(FakeResponse(status, None)))
                with self.assertRaises(ProviderError) as caught:
                    provider.complete(system="", messages=[ChatMessage.user("hi")])
                self.assertTrue(caught.exception.retryable)

    def test_network_failure_names_the_host(self) -> None:
        session = FakeSession(requests.ConnectionError("boom"))
        provider = create_provider(_config("openai_compatible", base_url="http://localhost:1234/v1"), session=session)
        with self.assertRaisesRegex(ProviderError, "Could not reach .* localhost:1234"):
            provider.complete(system="", messages=[ChatMessage.user("hi")])

    def test_timeout_is_retryable(self) -> None:
        provider = create_provider(_config("gemini"), session=FakeSession(requests.Timeout("slow")))
        with self.assertRaises(ProviderError) as caught:
            provider.complete(system="", messages=[ChatMessage.user("hi")])
        self.assertTrue(caught.exception.retryable)


class ProviderRegistryTest(unittest.TestCase):
    def test_normalize_provider_id_falls_back_to_default(self) -> None:
        self.assertEqual(normalize_provider_id(" Anthropic "), "anthropic")
        self.assertEqual(normalize_provider_id("unknown"), "openai_compatible")
        self.assertEqual(normalize_provider_id(None), "openai_compatible")

    def test_key_requirement(self) -> None:
        self.assertFalse(provider_requires_api_key("openai_compatible", "http://127.0.0.1:1234/v1"))
        self.assertTrue(provider_requires_api_key("openai_compatible", "https://api.openai.com/v1"))
        self.assertTrue(provider_requires_api_key("gemini", ""))

    def test_messages_default_to_user_role(self) -> None:
        self.assertEqual(ChatMessage.user("x").role, MessageRole.USER)


if __name__ == "__main__":
    unittest.main()
