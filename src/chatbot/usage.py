"""Bounded chat usage with optional explicitly configured model pricing."""
import math
import os
import time


class ChatLimitError(ValueError):
    """An enforced usage budget, distinct from a model configuration failure."""


def limit(name, default):
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive.")
    return value


class UsageMeter:
    def __init__(self, turn):
        self.turn = turn
        self.started = time.monotonic()
        self.data = turn.setdefault("usage", {"model_calls": 0, "input_tokens": 0, "output_tokens": 0,
            "reported_usage_calls": 0, "tool_executions": 0, "model_seconds": 0.0, "tool_seconds": 0.0, "estimated_cost": None})

    def before_model(self):
        if self.data["model_calls"] >= limit("PLANT_CHAT_MODEL_CALL_LIMIT", 10):
            raise ChatLimitError("Chat model-call budget reached.")
        if self.data["input_tokens"] + self.data["output_tokens"] >= limit("PLANT_CHAT_TOKEN_LIMIT", 64000):
            raise ChatLimitError("Chat token budget reached.")
        self.data["model_calls"] += 1

    def record_model(self, reply, elapsed=0):
        self.data["model_seconds"] += round(elapsed, 3)
        usage = getattr(reply, "usage_metadata", None) or {}
        if all(type(usage.get(key)) is int for key in ("input_tokens", "output_tokens")):
            self.data["reported_usage_calls"] += 1
        for key in ("input_tokens", "output_tokens"):
            value = usage.get(key)
            if type(value) is int and value >= 0:
                self.data[key] += value

    def finish(self):
        self.data["total_seconds"] = round(time.monotonic() - self.started, 3)
        rates = [os.getenv("PLANT_CHAT_INPUT_COST_PER_MILLION"), os.getenv("PLANT_CHAT_OUTPUT_COST_PER_MILLION")]
        if all(rates) and self.data['reported_usage_calls'] == self.data['model_calls']:
            try:
                values = list(map(float, rates))
                if all(math.isfinite(v) and v >= 0 for v in values):
                    self.data["estimated_cost"] = sum(self.data[k] * rate / 1_000_000 for k, rate in
                        zip(("input_tokens", "output_tokens"), values))
            except ValueError:
                pass
        return self.data
