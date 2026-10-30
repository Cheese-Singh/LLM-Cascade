from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class ModelCallRecord:
    """Record of a single model call with provider-verified token counts and latencies."""
    model: str
    role: str  # "tier_generator", "runtime_judge", "offline_judge"
    tier: Optional[str] = None  # e.g. "layer_1", "layer_2", etc.
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    wall_latency_ms: float = 0.0
    engine_total_duration_ns: Optional[int] = None
    retries: int = 0
    error: Optional[str] = None
    execution_mode: str = "genuine_ollama_call"  # or "offline_deterministic", "live_eval"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # Explicitly tag whether token metrics were provider-reported or unavailable
        d["tokens_provider_reported"] = (self.prompt_tokens is not None and self.completion_tokens is not None)
        return d


@dataclass
class CascadeResourceTracker:
    """
    Tracks and aggregates all resource consumption (tokens, latency, retries, errors)
    across tier generators and evaluators/judges during cascade execution.
    """
    calls: List[ModelCallRecord] = field(default_factory=list)
    start_time: float = field(default_factory=time.perf_counter)
    end_time: Optional[float] = None

    def record_call(self, record: ModelCallRecord) -> None:
        self.calls.append(record)

    def close(self) -> None:
        if self.end_time is None:
            self.end_time = time.perf_counter()

    @property
    def end_to_end_wall_latency_ms(self) -> float:
        end = self.end_time if self.end_time is not None else time.perf_counter()
        return round((end - self.start_time) * 1000, 2)

    def aggregate_by_role(self, role: str) -> Dict[str, Any]:
        relevant = [c for c in self.calls if c.role == role]
        prompt_known = [c.prompt_tokens for c in relevant if c.prompt_tokens is not None]
        comp_known = [c.completion_tokens for c in relevant if c.completion_tokens is not None]
        tot_known = [c.total_tokens for c in relevant if c.total_tokens is not None]

        return {
            "call_count": len(relevant),
            "prompt_tokens": sum(prompt_known) if prompt_known else None,
            "completion_tokens": sum(comp_known) if comp_known else None,
            "total_tokens": sum(tot_known) if tot_known else None,
            "latency_ms": round(sum(c.wall_latency_ms for c in relevant), 2),
            "retries": sum(c.retries for c in relevant),
            "failures": sum(1 for c in relevant if c.error is not None),
        }

    def get_summary(self) -> Dict[str, Any]:
        tier_agg = self.aggregate_by_role("tier_generator")
        judge_agg = self.aggregate_by_role("runtime_judge")
        offline_judge_agg = self.aggregate_by_role("offline_judge")

        all_prompt = [c.prompt_tokens for c in self.calls if c.prompt_tokens is not None]
        all_comp = [c.completion_tokens for c in self.calls if c.completion_tokens is not None]
        all_tot = [c.total_tokens for c in self.calls if c.total_tokens is not None]

        return {
            "total_calls": len(self.calls),
            "generator_calls": tier_agg["call_count"],
            "runtime_judge_calls": judge_agg["call_count"],
            "offline_judge_calls": offline_judge_agg["call_count"],
            "prompt_tokens": sum(all_prompt) if all_prompt else None,
            "completion_tokens": sum(all_comp) if all_comp else None,
            "total_tokens": sum(all_tot) if all_tot else None,
            "tier_model_latency_ms": tier_agg["latency_ms"],
            "runtime_judge_latency_ms": judge_agg["latency_ms"],
            "end_to_end_wall_latency_ms": self.end_to_end_wall_latency_ms,
            "total_retries": sum(c.retries for c in self.calls),
            "total_failures": sum(1 for c in self.calls if c.error is not None),
            "cost_reported": False,
            "cost_notes": "Model pricing not verified for local Ollama endpoints; no cost estimated.",
            "call_details": [c.to_dict() for c in self.calls],
        }
