"""One interface, two backends. The router picks a tier; get_backend maps tier -> backend."""
import json
import time
from dataclasses import dataclass

import anthropic
import httpx

import config


@dataclass
class LLMResult:
    text: str
    model: str            # model that actually answered (may differ after a server-side fallback)
    requested_model: str
    tier: str
    in_tokens: int
    out_tokens: int
    latency_s: float
    cost_usd: float


class LLMBackend:
    tier: str

    def complete(self, prompt: str, system: str = "", max_tokens: int = 2048,
                 schema: dict | None = None) -> LLMResult:
        """schema: JSON schema to constrain the output (structured outputs)."""
        raise NotImplementedError


class OllamaBackend(LLMBackend):
    tier = "small"

    def __init__(self, model=config.SMALL_MODEL, url=config.OLLAMA_URL):
        self.model, self.url = model, url

    def complete(self, prompt, system="", max_tokens=2048, schema=None):
        if schema:
            # This Ollama build returns "structured output is unavailable" for qwen3.5,
            # so the schema goes in the prompt and callers validate + retry.
            prompt += ("\n\nRespond with ONLY a JSON object (no prose) whose values follow this JSON schema. "
                       "Fill in real values; do NOT repeat the schema itself:\n" + json.dumps(schema))
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        body = {"model": self.model, "messages": msgs, "stream": False, "think": False,
                "options": {"num_predict": max_tokens, "temperature": 0}}
        t = time.perf_counter()
        r = httpx.post(f"{self.url}/api/chat", timeout=300, json=body)
        r.raise_for_status()
        d = r.json()
        return LLMResult(d["message"]["content"], d.get("model", self.model), self.model, self.tier,
                         d.get("prompt_eval_count", 0), d.get("eval_count", 0),
                         time.perf_counter() - t, 0.0)


def _strict(schema):
    """Structured outputs need additionalProperties:false on every object."""
    if isinstance(schema, dict):
        schema = {k: _strict(v) for k, v in schema.items()}
        if schema.get("type") == "object":
            schema["additionalProperties"] = False
    elif isinstance(schema, list):
        schema = [_strict(v) for v in schema]
    return schema


class AnthropicBackend(LLMBackend):
    tier = "big"

    def __init__(self, model=config.BIG_MODEL):
        self.model = model
        self.client = anthropic.Anthropic()

    def complete(self, prompt, system="", max_tokens=16000, schema=None):
        kw = {}
        if schema:
            kw["output_config"] = {"format": {"type": "json_schema", "schema": _strict(schema)}}
        t = time.perf_counter()
        # Server-side fallback: if a safety classifier declines, the API reruns on a fallback model.
        msg = self.client.beta.messages.create(
            model=self.model, max_tokens=max_tokens,
            system=system or anthropic.NOT_GIVEN,
            messages=[{"role": "user", "content": prompt}],
            betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kw,
        )
        # A refusal is returned (and its tokens billed/logged) as empty text: callers treat it as invalid output.
        text = "" if msg.stop_reason == "refusal" else "".join(b.text for b in msg.content if b.type == "text")
        u = msg.usage
        return LLMResult(text, msg.model, self.model, self.tier, u.input_tokens, u.output_tokens,
                         time.perf_counter() - t,
                         config.cost(msg.model, u.input_tokens, u.output_tokens, fallback_model=self.model))


_backends: dict[str, LLMBackend] = {}


def get_backend(tier: str) -> LLMBackend:
    if tier not in _backends:
        _backends[tier] = OllamaBackend() if tier == "small" else AnthropicBackend()
    return _backends[tier]


def parse_json(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        a, b = text.find("{"), text.rfind("}")
        try:
            return json.loads(text[a:b + 1]) if a >= 0 < b else None
        except json.JSONDecodeError:
            return None


def validate(obj, schema) -> bool:
    """Minimal checker for our own schemas: types, required keys, enums."""
    t = schema.get("type")
    if t == "object":
        return (isinstance(obj, dict) and all(k in obj for k in schema.get("required", []))
                and all(validate(obj[k], s) for k, s in schema.get("properties", {}).items() if k in obj))
    if t == "array":
        return isinstance(obj, list) and all(validate(i, schema.get("items", {})) for i in obj)
    if "enum" in schema and obj not in schema["enum"]:
        return False
    if t == "string":
        return isinstance(obj, str)
    if t == "number":
        return isinstance(obj, (int, float)) and not isinstance(obj, bool)
    return True


if __name__ == "__main__":
    import sys
    s = {"type": "object", "required": ["n", "k"], "properties": {
        "n": {"type": "number"}, "k": {"type": "string", "enum": ["a", "b"]}}}
    assert validate({"n": 1, "k": "a"}, s) and not validate({"n": 1, "k": "c"}, s) and not validate({"n": 1}, s)
    assert parse_json('sure: {"n": 2, "k": "b"} done') == {"n": 2, "k": "b"} and parse_json("nope") is None
    for tier in sys.argv[1:] or ["small", "big"]:
        r = get_backend(tier).complete('Return {"n": 7, "k": "b"}', max_tokens=1024, schema=s)
        print(f"[{tier}] {r.model} (requested {r.requested_model}): {r.text.strip()!r} valid={validate(parse_json(r.text), s)} "
              f"in={r.in_tokens} out={r.out_tokens} {r.latency_s:.2f}s ${r.cost_usd:.6f}")
