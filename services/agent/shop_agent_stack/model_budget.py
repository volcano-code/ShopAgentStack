"""Per-run model-call admission using provider reports, never estimated billing.

The threshold controls whether another request may start; it is not a hard
cost/token cap. Input size, in-flight usage, prices, and remote billing vary.
"""
from dataclasses import dataclass

MAX_MODEL_REQUESTS = 8
MIN_PROMPT_HEADROOM = 1024
MIN_USEFUL_OUTPUT = 128


class ModelBudgetExceeded(ValueError):
    """A safe redacted control error without provider or user content."""


@dataclass(frozen=True)
class Ticket:
    index: int
    output_limit: int


class ModelBudget:
    def __init__(self, reported_threshold=16000):
        if type(reported_threshold) is not int or reported_threshold <= MIN_PROMPT_HEADROOM + MIN_USEFUL_OUTPUT:
            raise ValueError("Invalid model admission threshold")
        self.reported_threshold = reported_threshold
        self.attempts = 0
        self.reported_tokens = 0
        self.unknown_usage_attempts = 0
        self._settled = set()

    def admit(self, requested_output):
        if type(requested_output) is not int or not 1 <= requested_output <= 4096:
            raise ModelBudgetExceeded("模型输出请求预算无效")
        if self.attempts >= MAX_MODEL_REQUESTS:
            raise ModelBudgetExceeded("本轮模型请求次数已达预算，请缩小问题范围")
        allowance = self.reported_threshold - self.reported_tokens - MIN_PROMPT_HEADROOM
        if allowance < MIN_USEFUL_OUTPUT:
            raise ModelBudgetExceeded("模型已报告用量接近本轮上限，请缩小问题范围")
        self.attempts += 1
        return Ticket(self.attempts, min(requested_output, allowance))

    def settle(self, ticket, usage):
        if ticket.index in self._settled or ticket.index > self.attempts or ticket.index < 1:
            raise ValueError("Model request accounting already completed or invalid")
        self._settled.add(ticket.index)
        value = usage.get("total_tokens") if isinstance(usage, dict) else None
        known = type(value) is int and value > 0
        if known:
            self.reported_tokens += value
        else:
            self.unknown_usage_attempts += 1
        return {
            "call_index": ticket.index,
            "max_output_tokens": ticket.output_limit,
            "usage_known": known,
            "reported_tokens": value if known else 0,
            "reported_tokens_known_sum": self.reported_tokens,
            "unknown_usage_attempts": self.unknown_usage_attempts,
        }
