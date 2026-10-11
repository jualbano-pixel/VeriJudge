"""All model calls belong in this module. Stub mode never uses the network."""
from copy import deepcopy
import json
from typing import Protocol

import httpx
from jsonschema import validate


class LLM(Protocol):
    def complete(self, system: str, user: str, json_schema: dict) -> dict: ...


class StubLLM:
    def __init__(self, responses=None, scenario="default"):
        self.responses = deepcopy(responses if responses is not None else {"default": {"ok": True}})
        self.scenario = scenario
        self.calls = []

    def complete(self, system: str, user: str, json_schema: dict) -> dict:
        self.calls.append({"system": system, "user": user, "json_schema": json_schema})
        response = deepcopy(self.responses[self.scenario])
        validate(instance=response, schema=json_schema)
        return response


class OpenAILLM:
    """Small structured-output adapter for the existing LLM protocol."""

    def __init__(self, api_key: str, model: str, client=None):
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required when MODEL is not stub")
        self.api_key = api_key
        self.model = model
        self.client = client or httpx.Client(timeout=45, trust_env=True)

    def complete(self, system: str, user: str, json_schema: dict) -> dict:
        response = self.client.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": "Bearer " + self.api_key},
            json={
                "model": self.model,
                "instructions": system,
                "input": user,
                "text": {"format": {"type": "json_schema", "name": "veryjudge_result",
                                    "strict": False, "schema": json_schema}},
            },
        )
        response.raise_for_status()
        payload = response.json()
        text = payload.get("output_text")
        if text is None:
            text = next((part.get("text") for item in payload.get("output", [])
                         for part in item.get("content", []) if part.get("type") == "output_text"), None)
        if not text:
            raise ValueError("model returned no structured output")
        result = json.loads(text)
        validate(instance=result, schema=json_schema)
        return result
