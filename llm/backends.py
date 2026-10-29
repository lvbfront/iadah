"""One interface, two backends. The router picks a tier; get_backend maps tier -> backend."""
import time
from dataclasses import dataclass

import anthropic
import httpx

import config


@dataclass
class LLMResult:
    text: str
    model: str
    tier: str
    in_tokens: int
    out_tokens: int
    latency_s: float
    cost_usd: float


class LLMBackend:
    tier: str

    def complete(self, prompt: str, system: str = "", max_tokens: int = 2048) -> LLMResult:
        raise NotImplementedError


class OllamaBackend(LLMBackend):
    tier = "small"

    def __init__(self, model=config.SMALL_MODEL, url=config.OLLAMA_URL):
        self.model, self.url = model, url

    def complete(self, prompt, system="", max_tokens=2048):
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        t = time.perf_counter()
        r = httpx.post(f"{self.url}/api/chat", timeout=300, json={
            "model": self.model, "messages": msgs, "stream": False, "think": False,
            "options": {"num_predict": max_tokens},
        })
        r.raise_for_status()
        d = r.json()
        return LLMResult(d["message"]["content"], self.model, self.tier,
                         d.get("prompt_eval_count", 0), d.get("eval_count", 0),
                         time.perf_counter() - t, 0.0)


class AnthropicBackend(LLMBackend):
    tier = "big"

    def __init__(self, model=config.BIG_MODEL):
        self.model = model
        self.client = anthropic.Anthropic()

    def complete(self, prompt, system="", max_tokens=16000):
        t = time.perf_counter()
        # Server-side fallback: if a safety classifier declines, the API reruns on a fallback model.
        msg = self.client.beta.messages.create(
            model=self.model, max_tokens=max_tokens,
            system=system or anthropic.NOT_GIVEN,
            messages=[{"role": "user", "content": prompt}],
            betas=["server-side-fallback-2026-07-01"],
            extra_body={"fallbacks": "default"},
        )
        if msg.stop_reason == "refusal":
            raise RuntimeError(f"refused: {msg.stop_details}")
        text = "".join(b.text for b in msg.content if b.type == "text")
        u = msg.usage
        return LLMResult(text, msg.model, self.tier, u.input_tokens, u.output_tokens,
                         time.perf_counter() - t, config.cost(self.model, u.input_tokens, u.output_tokens))


_backends: dict[str, LLMBackend] = {}


def get_backend(tier: str) -> LLMBackend:
    if tier not in _backends:
        _backends[tier] = OllamaBackend() if tier == "small" else AnthropicBackend()
    return _backends[tier]


if __name__ == "__main__":
    import sys
    tiers = sys.argv[1:] or ["small", "big"]
    for tier in tiers:
        r = get_backend(tier).complete("Reply with exactly: OK", max_tokens=1024)
        print(f"[{tier}] {r.model}: {r.text.strip()!r} in={r.in_tokens} out={r.out_tokens} "
              f"{r.latency_s:.2f}s ${r.cost_usd:.6f}")
