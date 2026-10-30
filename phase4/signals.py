from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional


AUDIT_KEYWORDS = [
    "gst", "taxable", "cgst", "sgst", "igst", "cess", "discount", "subtotal",
    "total", "reconciliation", "discrepancy", "hsn", "sac", "neft", "payment",
    "balance", "outstanding", "reverse charge", "interstate", "intrastate"
]

HEDGING_KEYWORDS = [
    "unclear", "uncertain", "assuming", "approximate", "cannot be determined",
    "insufficient", "missing", "possibly", "might be", "not provided"
]


def extract_signals(
    response: str,
    problem: Dict[str, Any],
    previous_response: Optional[str] = None,
) -> Dict[str, float]:
    """Extract informative numerical feature signals from an audit answer."""
    lower_resp = response.lower()
    words = re.findall(r'\w+', lower_resp)
    total_words = max(len(words), 1)

    # 1. Hedging Score
    hedging_count = sum(1 for kw in HEDGING_KEYWORDS if kw in lower_resp)
    hedging_score = min(hedging_count / 3.0, 1.0)

    # 2. Audit Keyword Density
    audit_kw_count = sum(1 for kw in AUDIT_KEYWORDS if kw in lower_resp)
    audit_kw_density = min(audit_kw_count / float(total_words), 0.5) * 2.0

    # 3. Numeric Density
    numbers = re.findall(r'\b\d+(?:\.\d+)?\b', response)
    numeric_density = min(len(numbers) / float(total_words), 0.5) * 2.0

    # 4. Citations count
    citation_matches = re.findall(r'prov_\w+', response)
    citation_score = min(len(citation_matches) / 2.0, 1.0)

    # 5. Length log
    length_log = math.log1p(len(response)) / 10.0

    # 6. Disagreement with previous layer
    disagreement_with_prior = 0.0
    if previous_response:
        prev_nums = set(re.findall(r'\b\d+(?:\.\d+)?\b', previous_response))
        curr_nums = set(numbers)
        if prev_nums and curr_nums:
            jaccard = len(prev_nums & curr_nums) / float(len(prev_nums | curr_nums))
            disagreement_with_prior = 1.0 - jaccard

    # 7. Explicit Abstention Indicator
    abstention_flag = 1.0 if any(h in lower_resp for h in ["cannot be established", "insufficient evidence", "not provided", "missing"]) else 0.0

    # 8. Stated Confidence heuristic
    confidence_match = re.search(r'confidence[:\s]+(\d+(?:\.\d+)?)', lower_resp)
    if confidence_match:
        try:
            val = float(confidence_match.group(1))
            stated_confidence = val if val <= 1.0 else val / 100.0
        except ValueError:
            stated_confidence = 0.85
    else:
        stated_confidence = max(0.5, 0.95 - (hedging_score * 0.4))

    return {
        "stated_confidence": stated_confidence,
        "hedging_score": hedging_score,
        "audit_kw_density": audit_kw_density,
        "numeric_density": numeric_density,
        "citation_score": citation_score,
        "length_log": length_log,
        "disagreement_with_prior": disagreement_with_prior,
        "abstention_flag": abstention_flag,
    }


def signal_vector(signals: Dict[str, float]) -> List[float]:
    """Convert signal dict to ordered feature vector for scikit-learn."""
    keys = [
        "stated_confidence",
        "hedging_score",
        "audit_kw_density",
        "numeric_density",
        "citation_score",
        "length_log",
        "disagreement_with_prior",
        "abstention_flag",
    ]
    return [signals.get(k, 0.0) for k in keys]
