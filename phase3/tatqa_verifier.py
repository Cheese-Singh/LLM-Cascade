from __future__ import annotations

import math
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

from phase3.semantic_verifier import semantic_verify


NUMBER_PATTERN = re.compile(
    r"[-+]?(?:\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)"
    r"(?:[eE][-+]?\d+)?"
)

YEAR_PATTERN = re.compile(
    r"\b(?:19|20)\d{2}\b"
)

UNIT_PATTERN = re.compile(
    r"\b("
    r"trillion|"
    r"billion|"
    r"million|"
    r"thousand|"
    r"hundred|"
    r"percent|"
    r"percentage|"
    r"%|"
    r"basis\s+points?|"
    r"bps?"
    r")\b",
    re.IGNORECASE,
)

ANSWER_PATTERN = re.compile(
    r"(?:^|\n)\s*(?:ANSWER|FINAL ANSWER)\s*:\s*(.*?)(?="
    r"\n\s*CONFIDENCE\s*:|\Z)",
    re.IGNORECASE | re.DOTALL,
)

CONFIDENCE_PATTERN = re.compile(
    r"CONFIDENCE\s*:\s*[-+]?(?:\d+(?:\.\d+)?|\.\d+)",
    re.IGNORECASE,
)

YES_PATTERN = re.compile(
    r"^\s*(?:yes|y|true)\b",
    re.IGNORECASE,
)

NO_PATTERN = re.compile(
    r"^\s*(?:no|n|false)\b",
    re.IGNORECASE,
)

CURRENCY_PATTERN = r"[$€£¥₹]"

UNIT_MULTIPLIERS = {
    "hundred": 100.0,
    "thousand": 1_000.0,
    "million": 1_000_000.0,
    "billion": 1_000_000_000.0,
    "trillion": 1_000_000_000_000.0,
}

ROUNDING_DECIMAL_PLACES = 2


STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "per",
    "that",
    "the",
    "then",
    "there",
    "to",
    "was",
    "were",
    "will",
    "with",
}


SYNONYMS = {
    "increase": "increase",
    "increased": "increase",
    "increases": "increase",
    "increasing": "increase",
    "rise": "increase",
    "rises": "increase",
    "rose": "increase",
    "rising": "increase",
    "raise": "increase",
    "raised": "increase",
    "decrease": "decrease",
    "decreased": "decrease",
    "decreases": "decrease",
    "decreasing": "decrease",
    "decline": "decrease",
    "declines": "decrease",
    "declined": "decrease",
    "declining": "decrease",
    "fall": "decrease",
    "falls": "decrease",
    "fell": "decrease",
    "falling": "decrease",
    "reduce": "decrease",
    "reduced": "decrease",
    "reduces": "decrease",
    "reduction": "decrease",
    "grade": "decrease",
    "grading": "decrease",
    "graded": "decrease",
    "remain": "remain",
    "remains": "remain",
    "remaining": "remain",
    "stay": "remain",
    "stays": "remain",
    "stable": "remain",
    "unchanged": "remain",
    "constant": "remain",
    "same": "remain",
    "higher": "increase",
    "greater": "increase",
    "lower": "decrease",
    "less": "decrease",
    "before": "before",
    "prior": "before",
    "previously": "before",
    "after": "after",
    "later": "after",
    "thereafter": "after",
    "subsequently": "after",
    "approximately": "approximately",
    "about": "approximately",
    "roughly": "approximately",
}


def _normalize_unicode(text: str) -> str:
    text = unicodedata.normalize(
        "NFKC",
        text,
    )

    text = text.replace(
        "\u00a0",
        " ",
    )

    text = text.replace(
        "\u2011",
        "-",
    )

    text = text.replace(
        "\u2013",
        "-",
    )

    text = text.replace(
        "\u2014",
        "-",
    )

    text = text.replace(
        "\u2212",
        "-",
    )

    text = text.replace(
        "\u202f",
        " ",
    )

    return text


def _normalize_text(text: str) -> str:
    text = _normalize_unicode(
        str(text)
    )

    text = text.lower()

    text = re.sub(
        r"[%]",
        " percent ",
        text,
    )

    text = re.sub(
        r"[^a-z0-9.\-+%]+",
        " ",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def _canonical_word(word: str) -> str:
    word = word.lower()

    if word in SYNONYMS:
        return SYNONYMS[word]

    if word.endswith(
        "ies"
    ) and len(word) > 4:
        return (
            word[:-3]
            + "y"
        )

    if word.endswith(
        "ing"
    ) and len(word) > 5:
        base = word[:-3]

        if base.endswith(
            (
                "at",
                "in",
                "iz",
                "it",
            )
        ):
            return (
                base
                + "e"
            )

        return base

    if word.endswith(
        "ed"
    ) and len(word) > 4:
        base = word[:-2]

        if base in SYNONYMS:
            return SYNONYMS[base]

        return base

    if word.endswith(
        "s"
    ) and len(word) > 4:
        return word[:-1]

    return word


def _content_tokens(
    text: str,
) -> list[str]:
    normalized = _normalize_text(
        text
    )

    tokens = []

    for token in normalized.split():
        if token in STOPWORDS:
            continue

        canonical = _canonical_word(
            token
        )

        if canonical:
            tokens.append(
                canonical
            )

    return tokens


def _token_set(
    text: str,
) -> set[str]:
    return set(
        _content_tokens(text)
    )


def _sequence_similarity(
    first: str,
    second: str,
) -> float:
    return SequenceMatcher(
        None,
        _normalize_text(first),
        _normalize_text(second),
    ).ratio()


def _token_overlap(
    first: str,
    second: str,
) -> float:
    first_tokens = _token_set(
        first
    )

    second_tokens = _token_set(
        second
    )

    if (
        not first_tokens
        or not second_tokens
    ):
        return 0.0

    intersection = (
        first_tokens
        & second_tokens
    )

    return (
        len(intersection)
        / len(second_tokens)
    )


def _extract_years(
    text: str,
) -> set[int]:
    return {
        int(match.group(0))
        for match in YEAR_PATTERN.finditer(
            text
        )
    }


def _infer_context_sign(
    text: str,
    match: re.Match[str],
) -> tuple[int, str]:
    start = match.start()
    end = match.end()

    before = text[
        max(
            0,
            start - 40,
        ):
        start
    ]

    after = text[
        end:
        end + 50
    ]

    prefix = before.rstrip()
    suffix = after.lstrip()

    if re.search(
        rf"\(\s*{CURRENCY_PATTERN}\s*$",
        prefix,
    ) and re.match(
        r"^\)",
        suffix,
    ):
        return -1, "accounting_parentheses"

    if re.search(
        rf"-\s*{CURRENCY_PATTERN}\s*$",
        prefix,
    ):
        return -1, "explicit_negative_currency"

    if re.search(
        r"-\s*$",
        prefix,
    ):
        return -1, "explicit_negative"

    if re.search(
        r"\+\s*$",
        prefix,
    ):
        return 1, "explicit_positive"

    decrease_pattern = (
        r"\b("
        r"decrease|"
        r"decreased|"
        r"decline|"
        r"declined|"
        r"fall|"
        r"fell|"
        r"falling|"
        r"reduce|"
        r"reduced|"
        r"reduction|"
        r"down|"
        r"lower"
        r")\b"
    )

    increase_pattern = (
        r"\b("
        r"increase|"
        r"increased|"
        r"rise|"
        r"rose|"
        r"rising|"
        r"higher|"
        r"greater|"
        r"up"
        r")\b"
    )

    if re.search(
        decrease_pattern,
        before,
        flags=re.IGNORECASE,
    ) or re.search(
        decrease_pattern,
        after,
        flags=re.IGNORECASE,
    ):
        return -1, "decrease_context"

    if re.search(
        increase_pattern,
        before,
        flags=re.IGNORECASE,
    ) or re.search(
        increase_pattern,
        after,
        flags=re.IGNORECASE,
    ):
        return 1, "increase_context"

    return 1, "positive_default"


def _number_occurrences(
    text: str,
) -> list[dict[str, Any]]:
    occurrences = []

    normalized = _normalize_unicode(
        text
    )

    for match in NUMBER_PATTERN.finditer(
        normalized
    ):
        token = match.group(
            0
        ).replace(
            ",",
            "",
        )

        try:
            value = float(token)
        except ValueError:
            continue

        if not math.isfinite(value):
            continue

        context_sign, sign_reason = (
            _infer_context_sign(
                normalized,
                match,
            )
        )

        if value < 0:
            context_sign = -1

        value = abs(value) * context_sign

        after = normalized[
            match.end():
            match.end() + 40
        ]

        before = normalized[
            max(
                0,
                match.start() - 15,
            ):
            match.start()
        ]

        unit = None
        multiplier = 1.0

        unit_match = UNIT_PATTERN.search(
            after
        )

        if unit_match:
            unit = unit_match.group(
                1
            ).lower()

            multiplier = (
                UNIT_MULTIPLIERS.get(
                    unit,
                    1.0,
                )
            )

        is_percent = (
            "%"
            in after[:3]
            or "percent"
            in after[:10].lower()
            or "%"
            in before[-2:]
        )

        normalized_value = (
            value
            * multiplier
        )

        if is_percent:
            normalized_value = value

        occurrences.append(
            {
                "value": value,
                "normalized": normalized_value,
                "unit": unit,
                "percent": is_percent,
                "token": match.group(0),
                "sign_reason": sign_reason,
            }
        )

    return occurrences


def _rounded_value(
    value: float,
) -> float:
    return round(
        value,
        ROUNDING_DECIMAL_PLACES,
    )


def _decimal_places(
    token: str,
) -> int:
    token = token.strip()

    token = re.sub(
        r"[,\s]",
        "",
        token,
    )

    if "e" in token.lower():
        try:
            value = float(token)
        except ValueError:
            return 0

        if not math.isfinite(value):
            return 0

        text = format(
            value,
            "f",
        )

        if "." not in text:
            return 0

        return len(
            text.rstrip("0").split(
                ".",
                1,
            )[1]
        )

    if "." not in token:
        return 0

    return len(
        token.split(
            ".",
            1,
        )[1]
    )


def _percentage_representations(
    number: dict[str, Any],
) -> list[tuple[float, int, str]]:
    value = float(
        number["value"]
    )

    token = str(
        number.get(
            "token",
            "",
        )
    )

    places = _decimal_places(
        token
    )

    representations = []

    if number.get(
        "percent",
        False,
    ):
        representations.append(
            (
                value,
                places,
                "percent",
            )
        )

        representations.append(
            (
                value / 100.0,
                places + 2,
                "ratio",
            )
        )
    else:
        representations.append(
            (
                value,
                places,
                "ratio",
            )
        )

        if abs(value) <= 2.0:
            representations.append(
                (
                    value * 100.0,
                    max(
                        0,
                        places - 2,
                    ),
                    "percent",
                )
            )

    return representations


def _scale_factor(
    scale: Any,
) -> float:
    scale_text = str(
        scale or ""
    ).strip().lower()

    return UNIT_MULTIPLIERS.get(
        scale_text,
        1.0,
    )


def _resolve_scale(
    problem: dict[str, Any],
) -> tuple[str, str]:
    explicit_scale = str(
        problem.get(
            "scale",
            "",
        ) or ""
    ).strip().lower()

    if explicit_scale:
        if explicit_scale == "percentage":
            explicit_scale = "percent"

        return (
            explicit_scale,
            "dataset",
        )

    question = str(
        problem.get(
            "question",
            problem.get(
                "prompt",
                "",
            ),
        )
    )

    scale_patterns = [
        (
            "trillion",
            r"\btrillions?\b",
        ),
        (
            "billion",
            r"\bbillions?\b",
        ),
        (
            "million",
            r"\bmillions?\b",
        ),
        (
            "thousand",
            r"\bthousands?\b",
        ),
        (
            "percent",
            r"\bpercent(?:age)?\b|%",
        ),
    ]

    for scale_name, pattern in scale_patterns:
        if re.search(
            pattern,
            question,
            flags=re.IGNORECASE,
        ):
            return (
                scale_name,
                "question",
            )

    return (
        "",
        "none",
    )


def _numeric_representations(
    number: dict[str, Any],
    scale: str,
    expected: bool,
) -> list[tuple[float, int, str]]:
    token = str(
        number.get(
            "token",
            "",
        )
    )

    places = _decimal_places(
        token
    )

    value = float(
        number["value"]
    )

    representations = []

    if scale == "percent":
        if number.get(
            "percent",
            False,
        ):
            representations.append(
                (
                    value,
                    places,
                    "percent",
                )
            )
        else:
            representations.append(
                (
                    value,
                    places,
                    "percent",
                )
            )

            if not expected and abs(value) <= 2.0:
                representations.append(
                    (
                        value * 100.0,
                        places,
                        "ratio_to_percent",
                    )
                )

        return representations

    unit = str(
        number.get(
            "unit",
            "",
        ) or "",
    ).strip().lower()

    scale_factor = _scale_factor(
        scale
    )

    if unit in UNIT_MULTIPLIERS:
        base_value = (
            value
            * UNIT_MULTIPLIERS[unit]
        )

        representations.append(
            (
                base_value,
                places,
                "explicit_unit_base",
            )
        )

        if scale_factor != 1.0:
            representations.append(
                (
                    base_value / scale_factor,
                    places,
                    "explicit_unit_scale",
                )
            )

        return representations

    if expected:
        if scale_factor != 1.0:
            representations.append(
                (
                    value * scale_factor,
                    places,
                    "dataset_scale_base",
                )
            )

        representations.append(
            (
                value,
                places,
                "dataset_scale_value",
            )
        )

        return representations

    representations.append(
        (
            value,
            places,
            "candidate_base_value",
        )
    )

    if scale_factor != 1.0:
        representations.append(
            (
                value * scale_factor,
                places,
                "candidate_dataset_scale_base",
            )
        )

    return representations


def _rounded_numbers_equal(
    first: float,
    second: float,
    first_places: int,
    second_places: int,
) -> bool:
    comparison_places = min(
        first_places,
        second_places,
    )

    return math.isclose(
        round(
            first,
            comparison_places,
        ),
        round(
            second,
            comparison_places,
        ),
        rel_tol=1e-9,
        abs_tol=1e-9,
    )


def _percentage_rounding_match(
    expected_number: dict[str, Any],
    candidate_number: dict[str, Any],
    scale: Any = None,
) -> tuple[
    tuple[float, int, str],
    tuple[float, int, str],
] | None:
    expected_percent = bool(
        expected_number.get(
            "percent",
            False,
        )
    )

    candidate_percent = bool(
        candidate_number.get(
            "percent",
            False,
        )
    )

    expected_token = str(
        expected_number.get(
            "token",
            "",
        )
    )

    candidate_token = str(
        candidate_number.get(
            "token",
            "",
        )
    )

    expected_places = _decimal_places(
        expected_token
    )

    candidate_places = _decimal_places(
        candidate_token
    )

    expected_value = float(
        expected_number["value"]
    )

    candidate_value = float(
        candidate_number["value"]
    )

    scale_text = str(
        scale or ""
    ).strip().lower()

    if scale_text == "percentage":
        scale_text = "percent"

    percent_scale = (
        scale_text == "percent"
    )

    if (
        not expected_percent
        and candidate_percent
    ):
        expected_ratio = expected_value
        candidate_ratio = (
            candidate_value / 100.0
        )

        ratio_tolerance = (
            0.5
            * (
                10 ** (-expected_places)
            )
        )

        if abs(
            expected_ratio
            - candidate_ratio
        ) <= ratio_tolerance + 1e-12:
            return (
                (
                    expected_ratio,
                    expected_places,
                    "ratio",
                ),
                (
                    candidate_ratio,
                    candidate_places + 2,
                    "percent_to_ratio",
                ),
            )

    if (
        expected_percent
        and not candidate_percent
    ):
        expected_percent_value = (
            expected_value
        )

        candidate_percent_value = (
            candidate_value * 100.0
        )

        ratio_tolerance = (
            0.5
            * (
                10 ** (
                    -candidate_places
                )
            )
            * 100.0
        )

        if abs(
            expected_percent_value
            - candidate_percent_value
        ) <= ratio_tolerance + 1e-12:
            return (
                (
                    expected_percent_value,
                    expected_places,
                    "percent",
                ),
                (
                    candidate_percent_value,
                    candidate_places,
                    "ratio_to_percent",
                ),
            )

    if percent_scale and (
        not expected_percent
        and not candidate_percent
    ):
        if (
            abs(expected_value)
            <= 2.0
            or abs(candidate_value)
            <= 2.0
        ):
            expected_percent_value = (
                expected_value
                if abs(expected_value) > 2.0
                else expected_value * 100.0
            )

            candidate_percent_value = (
                candidate_value
                if abs(candidate_value) > 2.0
                else candidate_value * 100.0
            )

            expected_tolerance = (
                0.5
                * (
                    10 ** (-expected_places)
                )
                * (
                    100.0
                    if abs(expected_value)
                    <= 2.0
                    else 1.0
                )
            )

            candidate_tolerance = (
                0.5
                * (
                    10 ** (-candidate_places)
                )
                * (
                    100.0
                    if abs(candidate_value)
                    <= 2.0
                    else 1.0
                )
            )

            tolerance = max(
                expected_tolerance,
                candidate_tolerance,
            )

            if abs(
                expected_percent_value
                - candidate_percent_value
            ) <= tolerance + 1e-12:
                return (
                    (
                        expected_percent_value,
                        expected_places,
                        "percent_scale",
                    ),
                    (
                        candidate_percent_value,
                        candidate_places,
                        "percent_scale",
                    ),
                )

    if (
        not percent_scale
        and not expected_percent
        and not candidate_percent
        and abs(expected_value) <= 2.0
        and abs(candidate_value) <= 2.0
    ):
        expected_percent_value = (
            expected_value * 100.0
        )

        candidate_percent_value = (
            candidate_value * 100.0
        )

        tolerance = max(
            0.5
            * (
                10 ** (-expected_places)
            )
            * 100.0,
            0.5
            * (
                10 ** (-candidate_places)
            )
            * 100.0,
        )

        if abs(
            expected_percent_value
            - candidate_percent_value
        ) <= tolerance + 1e-12:
            return (
                (
                    expected_percent_value,
                    expected_places,
                    "ratio_to_percent",
                ),
                (
                    candidate_percent_value,
                    candidate_places,
                    "ratio_to_percent",
                ),
            )

    if (
        not percent_scale
        and (
            not expected_percent
            and not candidate_percent
        )
    ):
        if (
            abs(expected_value) <= 2.0
            and abs(candidate_value) > 2.0
        ):
            expected_percent_value = (
                expected_value * 100.0
            )

            tolerance = max(
                0.5
                * (
                    10 ** (-expected_places)
                )
                * 100.0,
                0.5
                * (
                    10 ** (-candidate_places)
                ),
            )

            if abs(
                expected_percent_value
                - candidate_value
            ) <= tolerance + 1e-12:
                return (
                    (
                        expected_percent_value,
                        expected_places,
                        "ratio_to_percent",
                    ),
                    (
                        candidate_value,
                        candidate_places,
                        "percent_scale",
                    ),
                )

        if (
            abs(candidate_value) <= 2.0
            and abs(expected_value) > 2.0
        ):
            candidate_percent_value = (
                candidate_value * 100.0
            )

            tolerance = max(
                0.5
                * (
                    10 ** (-candidate_places)
                )
                * 100.0,
                0.5
                * (
                    10 ** (-expected_places)
                ),
            )

            if abs(
                expected_value
                - candidate_percent_value
            ) <= tolerance + 1e-12:
                return (
                    (
                        expected_value,
                        expected_places,
                        "percent_scale",
                    ),
                    (
                        candidate_percent_value,
                        candidate_places,
                        "ratio_to_percent",
                    ),
                )

    return None


def _find_numeric_match(
    expected_number: dict[str, Any],
    candidate_number: dict[str, Any],
    scale: Any = None,
) -> tuple[
    tuple[float, int, str],
    tuple[float, int, str],
] | None:
    scale_text = str(
        scale or ""
    ).strip().lower()

    if scale_text == "percentage":
        scale_text = "percent"

    percentage_match = (
        _percentage_rounding_match(
            expected_number,
            candidate_number,
            scale_text,
        )
    )

    if percentage_match is not None:
        return percentage_match

    expected_representations = (
        _numeric_representations(
            expected_number,
            scale_text,
            expected=True,
        )
    )

    candidate_representations = (
        _numeric_representations(
            candidate_number,
            scale_text,
            expected=False,
        )
    )

    for expected_representation in expected_representations:
        for candidate_representation in candidate_representations:
            if _rounded_numbers_equal(
                expected_representation[0],
                candidate_representation[0],
                expected_representation[1],
                candidate_representation[1],
            ):
                return (
                    expected_representation,
                    candidate_representation,
                )

    if not scale_text:
        expected_percent = bool(
            expected_number.get(
                "percent",
                False,
            )
        )

        candidate_percent = bool(
            candidate_number.get(
                "percent",
                False,
            )
        )

        if expected_percent != candidate_percent:
            expected_repr = (
                _percentage_representations(
                    expected_number
                )
            )

            candidate_repr = (
                _percentage_representations(
                    candidate_number
                )
            )

            for expected_representation in expected_repr:
                for candidate_representation in candidate_repr:
                    if (
                        expected_representation[2]
                        != candidate_representation[2]
                    ):
                        continue

                    if _rounded_numbers_equal(
                        expected_representation[0],
                        candidate_representation[0],
                        expected_representation[1],
                        candidate_representation[1],
                    ):
                        return (
                            expected_representation,
                            candidate_representation,
                        )

    return None


def _numeric_values_compatible(
    expected_number: dict[str, Any],
    candidate_number: dict[str, Any],
    scale: Any = None,
) -> bool:
    return (
        _find_numeric_match(
            expected_number,
            candidate_number,
            scale,
        )
        is not None
    )


def _numbers_match(
    candidate: str,
    expected: str,
    scale: Any = None,
) -> bool:
    candidate_numbers = (
        _number_occurrences(
            candidate
        )
    )

    expected_numbers = (
        _number_occurrences(
            expected
        )
    )

    if not expected_numbers:
        return True

    if not candidate_numbers:
        return False

    unmatched = list(
        candidate_numbers
    )

    for expected_number in expected_numbers:
        found = False

        for index, candidate_number in enumerate(
            unmatched
        ):
            if _numeric_values_compatible(
                expected_number,
                candidate_number,
                scale,
            ):
                unmatched.pop(
                    index
                )

                found = True
                break

        if not found:
            return False

    return True


def _direction_tokens(
    text: str,
) -> set[str]:
    tokens = _content_tokens(
        text
    )

    return {
        token
        for token in tokens
        if token
        in {
            "increase",
            "decrease",
            "remain",
        }
    }


def _temporal_tokens(
    text: str,
) -> set[str]:
    tokens = _content_tokens(
        text
    )

    return {
        token
        for token in tokens
        if token
        in {
            "before",
            "after",
            "approximately",
        }
    }


def _semantic_span_match(
    candidate: str,
    expected: str,
) -> dict[str, Any]:
    candidate_normalized = _normalize_text(
        candidate
    )

    expected_normalized = _normalize_text(
        expected
    )

    if not expected_normalized:
        return {
            "passed": True,
            "score": 1.0,
            "reason": "empty_expected_span",
        }

    if (
        candidate_normalized
        == expected_normalized
    ):
        return {
            "passed": True,
            "score": 1.0,
            "reason": "exact_match",
        }

    if (
        expected_normalized
        in candidate_normalized
    ):
        return {
            "passed": True,
            "score": 0.98,
            "reason": "expected_span_contained",
        }

    if not _numbers_match(
        candidate,
        expected,
    ):
        return {
            "passed": False,
            "score": 0.0,
            "reason": "numeric_content_mismatch",
        }

    expected_directions = _direction_tokens(
        expected
    )

    candidate_directions = _direction_tokens(
        candidate
    )

    if (
        expected_directions
        and not expected_directions.issubset(
            candidate_directions
        )
    ):
        return {
            "passed": False,
            "score": 0.0,
            "reason": "direction_mismatch",
        }

    expected_years = _extract_years(
        expected
    )

    candidate_years = _extract_years(
        candidate
    )

    if (
        expected_years
        and not expected_years.issubset(
            candidate_years
        )
    ):
        return {
            "passed": False,
            "score": 0.0,
            "reason": "year_mismatch",
        }

    overlap = _token_overlap(
        candidate,
        expected,
    )

    similarity = _sequence_similarity(
        candidate,
        expected,
    )

    if overlap >= 0.55:
        return {
            "passed": True,
            "score": overlap,
            "reason": "semantic_token_overlap",
        }

    if similarity >= 0.70:
        return {
            "passed": True,
            "score": similarity,
            "reason": "semantic_string_similarity",
        }

    candidate_tokens = _token_set(
        candidate
    )

    expected_tokens = _token_set(
        expected
    )

    if (
        expected_tokens
        and expected_tokens.issubset(
            candidate_tokens
        )
    ):
        return {
            "passed": True,
            "score": 1.0,
            "reason": "semantic_token_subset",
        }

    return {
        "passed": False,
        "score": max(
            overlap,
            similarity,
        ),
        "reason": "insufficient_semantic_overlap",
    }


def _split_gold_spans(
    answer: Any,
) -> list[str]:
    if answer is None:
        return []

    if isinstance(
        answer,
        list,
    ):
        return [
            str(item).strip()
            for item in answer
            if str(item).strip()
        ]

    text = str(
        answer
    ).strip()

    if not text:
        return []

    return [
        text
    ]


def _multi_span_match(
    candidate: str,
    expected_spans: list[str],
    problem: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not expected_spans:
        return {
            "passed": False,
            "reason": "empty_gold_spans",
            "f1_by_span": [],
        }

    if problem is not None:
        return semantic_verify(
            problem,
            expected_spans,
            candidate,
        )

    span_results = []

    for expected_span in expected_spans:
        result = _semantic_span_match(
            candidate,
            expected_span,
        )

        span_results.append(
            result
        )

    passed_count = sum(
        1
        for result in span_results
        if result["passed"]
    )

    required_count = len(
        expected_spans
    )

    all_passed = (
        passed_count
        == required_count
    )

    return {
        "passed": all_passed,
        "reason": (
            "all_spans_semantically_matched"
            if all_passed
            else "one_or_more_spans_not_matched"
        ),
        "matched_spans": passed_count,
        "required_spans": required_count,
        "span_results": span_results,
        "scores": [
            result["score"]
            for result in span_results
        ],
    }


def _extract_answer_section(
    response: str,
) -> str:
    matches = ANSWER_PATTERN.findall(
        response
    )

    if not matches:
        return ""

    answer = matches[-1].strip()

    answer = CONFIDENCE_PATTERN.sub(
        "",
        answer,
    )

    return answer.strip()


def _fallback_answer_section(
    response: str,
) -> str:
    lines = [
        line.strip()
        for line in response.splitlines()
        if line.strip()
    ]

    filtered = []

    for line in lines:
        if CONFIDENCE_PATTERN.search(
            line
        ):
            continue

        filtered.append(
            line
        )

    if not filtered:
        return ""

    return filtered[-1]


def _clean_answer_text(
    text: str,
) -> str:
    text = CONFIDENCE_PATTERN.sub(
        "",
        text,
    )

    text = re.sub(
        r"^\s*(?:answer|final answer)\s*:\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    return text.strip()


def _classify_answer(
    answer: Any,
) -> str:
    if isinstance(
        answer,
        list,
    ):
        return "multi-span"

    if answer is None:
        return "text"

    text = str(
        answer
    ).strip()

    lowered = text.lower()

    if lowered in {
        "yes",
        "no",
        "true",
        "false",
    }:
        return "boolean"

    if "%" in text:
        return "numeric"

    if NUMBER_PATTERN.search(
        text
    ):
        return "numeric"

    return "text"


def _normalize_boolean(
    text: str,
) -> bool | None:
    cleaned = _clean_answer_text(
        text
    ).lower()

    if YES_PATTERN.search(
        cleaned
    ):
        return True

    if NO_PATTERN.search(
        cleaned
    ):
        return False

    return None


def _boolean_match(
    candidate: str,
    expected: str,
) -> dict[str, Any]:
    actual = _normalize_boolean(
        candidate
    )

    gold = _normalize_boolean(
        expected
    )

    if actual is None:
        return {
            "passed": False,
            "reason": "no_boolean_answer",
            "actual": None,
            "expected": gold,
        }

    if gold is None:
        return {
            "passed": False,
            "reason": "no_boolean_gold",
            "actual": actual,
            "expected": None,
        }

    return {
        "passed": (
            actual
            == gold
        ),
        "reason": (
            "boolean_match"
            if actual
            == gold
            else "boolean_mismatch"
        ),
        "actual": actual,
        "expected": gold,
    }


def _parse_number(
    text: str,
) -> float | None:
    numbers = _number_occurrences(
        text
    )

    if not numbers:
        return None

    return float(
        numbers[0]["normalized"]
    )


def _numeric_match(
    candidate_text: str,
    expected: Any,
    scale: Any = None,
    scale_source: str = "dataset",
) -> dict[str, Any]:
    candidate_numbers = (
        _number_occurrences(
            candidate_text
        )
    )

    expected_numbers = (
        _number_occurrences(
            str(expected)
        )
    )

    if not candidate_numbers:
        return {
            "passed": False,
            "candidate": None,
            "expected": expected,
            "scale": scale,
            "scale_source": scale_source,
            "reason": "no_numeric_answer_parsed",
        }

    if isinstance(
        expected,
        bool,
    ):
        return {
            "passed": False,
            "candidate": candidate_numbers[
                0
            ]["value"],
            "expected": expected,
            "scale": scale,
            "scale_source": scale_source,
            "reason": "boolean_gold_answer_used_with_numeric_verifier",
        }

    if not expected_numbers:
        return {
            "passed": False,
            "candidate": candidate_numbers[
                0
            ]["value"],
            "expected": expected,
            "scale": scale,
            "scale_source": scale_source,
            "reason": "gold_answer_is_not_numeric",
        }

    candidate_number = (
        candidate_numbers[0]
    )

    expected_number = (
        expected_numbers[0]
    )

    scale_text = str(
        scale or ""
    ).strip().lower()

    if scale_text == "percentage":
        scale_text = "percent"

    expected_raw = float(
        expected_number["value"]
    )

    candidate_raw = float(
        candidate_number["value"]
    )

    match = _find_numeric_match(
        expected_number,
        candidate_number,
        scale_text,
    )

    passed = match is not None

    if match is not None:
        matched_expected = match[0]
        matched_candidate = match[1]

        expected_normalized = (
            matched_expected[0]
        )

        candidate_normalized = (
            matched_candidate[0]
        )
    else:
        expected_representations = (
            _numeric_representations(
                expected_number,
                scale_text,
                expected=True,
            )
        )

        candidate_representations = (
            _numeric_representations(
                candidate_number,
                scale_text,
                expected=False,
            )
        )

        expected_normalized = (
            expected_representations[0][0]
            if expected_representations
            else expected_raw
        )

        candidate_normalized = (
            candidate_representations[0][0]
            if candidate_representations
            else candidate_raw
        )

        matched_expected = None
        matched_candidate = None

    candidate_token = str(
        candidate_number["token"]
    )

    expected_token = str(
        expected_number["token"]
    )

    candidate_decimal_places = (
        _decimal_places(
            candidate_token
        )
    )

    expected_decimal_places = (
        _decimal_places(
            expected_token
        )
    )

    comparison_decimal_places = min(
        candidate_decimal_places,
        expected_decimal_places,
    )

    rounded_candidate_raw = round(
        candidate_raw,
        comparison_decimal_places,
    )

    rounded_expected_raw = round(
        expected_raw,
        comparison_decimal_places,
    )

    rounded_candidate_normalized = round(
        candidate_normalized,
        comparison_decimal_places,
    )

    rounded_expected_normalized = round(
        expected_normalized,
        comparison_decimal_places,
    )

    raw_match = math.isclose(
        rounded_candidate_raw,
        rounded_expected_raw,
        rel_tol=1e-9,
        abs_tol=1e-9,
    )

    normalized_match = math.isclose(
        rounded_candidate_normalized,
        rounded_expected_normalized,
        rel_tol=1e-9,
        abs_tol=1e-9,
    )

    percentage_representation_match = (
        matched_expected is not None
        and matched_candidate is not None
        and (
            "percent"
            in matched_expected[2]
            or "percent"
            in matched_candidate[2]
        )
        and (
            matched_expected[2]
            != matched_candidate[2]
        )
    )

    percent_consistent = (
        scale_text != "percent"
        or candidate_number[
            "percent"
        ]
        or (
            not candidate_number[
                "percent"
            ]
            and abs(candidate_raw)
            <= 2.0
        )
    )

    return {
        "passed": passed,
        "candidate": candidate_raw,
        "candidate_normalized": candidate_normalized,
        "expected_raw": expected_raw,
        "expected_normalized": expected_normalized,
        "scale": scale,
        "scale_source": scale_source,
        "candidate_decimal_places": candidate_decimal_places,
        "expected_decimal_places": expected_decimal_places,
        "comparison_decimal_places": comparison_decimal_places,
        "rounded_candidate_raw": rounded_candidate_raw,
        "rounded_expected_raw": rounded_expected_raw,
        "rounded_candidate_normalized": rounded_candidate_normalized,
        "rounded_expected_normalized": rounded_expected_normalized,
        "raw_match": raw_match,
        "normalized_match": normalized_match,
        "percent_consistent": percent_consistent,
        "percentage_representation_match": percentage_representation_match,
        "candidate_sign_reason": candidate_number.get(
            "sign_reason"
        ),
        "expected_sign_reason": expected_number.get(
            "sign_reason"
        ),
        "matched_expected_representation": (
            matched_expected[2]
            if matched_expected is not None
            else None
        ),
        "matched_candidate_representation": (
            matched_candidate[2]
            if matched_candidate is not None
            else None
        ),
        "reason": (
            "numeric_match"
            if passed
            else "numeric_mismatch"
        ),
    }


def _count_match(
    problem: dict[str, Any],
    candidate: str,
    expected: Any,
) -> dict[str, Any]:
    expected_numbers = _number_occurrences(
        str(expected)
    )

    expected_count = None

    if expected_numbers:
        expected_count = int(
            round(
                expected_numbers[0][
                    "value"
                ]
            )
        )

    if expected_count is None:
        return {
            "passed": False,
            "reason": "count_gold_not_numeric",
        }

    years = sorted(
        _extract_years(
            candidate
        )
    )

    question = str(
        problem.get(
            "question",
            problem.get(
                "prompt",
                "",
            ),
        )
    )

    year_question = bool(
        re.search(
            r"\b("
            r"which\s+years?|"
            r"what\s+years?|"
            r"how\s+many\s+years?"
            r")\b",
            question,
            flags=re.IGNORECASE,
        )
    )

    candidate_numbers = (
        _number_occurrences(
            candidate
        )
    )

    if (
        year_question
        and years
    ):
        distinct_years = sorted(
            set(years)
        )

        if len(
            distinct_years
        ) == expected_count:
            return {
                "passed": True,
                "reason": "count_from_year_mentions",
                "expected_count": expected_count,
                "candidate_years": distinct_years,
                "candidate_count": len(
                    distinct_years
                ),
            }

    if candidate_numbers:
        explicit_count = int(
            round(
                candidate_numbers[0][
                    "value"
                ]
            )
        )

        if (
            explicit_count
            == expected_count
        ):
            return {
                "passed": True,
                "reason": "numeric_count_match",
                "expected_count": expected_count,
                "candidate_count": explicit_count,
            }

    return {
        "passed": False,
        "reason": "count_mismatch",
        "expected_count": expected_count,
        "candidate_years": sorted(
            set(years)
        ),
        "candidate_numbers": [
            number["value"]
            for number in candidate_numbers
        ],
    }


def _text_match(
    candidate: str,
    expected: str,
) -> dict[str, Any]:
    result = _semantic_span_match(
        candidate,
        expected,
    )

    return {
        "passed": result[
            "passed"
        ],
        "reason": result[
            "reason"
        ],
        "score": result[
            "score"
        ],
    }


def _semantic_match(
    problem: dict[str, Any],
    candidate: str,
    expected: Any,
) -> dict[str, Any]:
    return semantic_verify(
        problem,
        expected,
        candidate,
    )


def verify_tatqa(
    problem: dict[str, Any],
    response: str,
) -> dict[str, Any]:
    gold_answer = problem.get(
        "answer"
    )

    answer_type = str(
        problem.get(
            "answer_type",
            _classify_answer(
                gold_answer
            ),
        )
    ).lower()

    answer_section = _extract_answer_section(
        response
    )

    if not answer_section:
        answer_section = (
            _fallback_answer_section(
                response
            )
        )

    answer_section = _clean_answer_text(
        answer_section
    )

    result: dict[str, Any] = {
        "passed": False,
        "answer_type": answer_type,
        "gold_answer": gold_answer,
        "candidate_answer": answer_section,
    }

    if not answer_section:
        result[
            "reason"
        ] = "empty_candidate_answer"

        return result

    scale, scale_source = _resolve_scale(
        problem
    )

    result["scale"] = scale
    result["scale_source"] = scale_source

    if answer_type in {
        "yes_no",
        "boolean",
    }:
        check = _boolean_match(
            answer_section,
            str(
                gold_answer
            ),
        )

    elif answer_type in {
        "count",
        "counting",
    }:
        check = _count_match(
            problem,
            answer_section,
            gold_answer,
        )

    elif answer_type in {
        "arithmetic",
        "numeric",
        "percent",
        "float",
        "number",
    }:
        check = _numeric_match(
            answer_section,
            gold_answer,
            scale,
            scale_source,
        )

    elif answer_type in {
        "span",
        "text",
    }:
        gold_text = (
            gold_answer[0]
            if isinstance(gold_answer, list) and gold_answer
            else str(gold_answer)
        )
        rule_check = _text_match(
            answer_section,
            str(gold_text),
        )
        if rule_check.get("passed"):
            check = rule_check
        else:
            check = _semantic_match(
                problem,
                answer_section,
                gold_answer,
            )

    elif answer_type in {
        "multi-span",
        "multispan",
        "multi_span",
        "spans",
    }:
        gold_spans = (
            [str(s) for s in gold_answer]
            if isinstance(gold_answer, list)
            else [str(gold_answer)]
        )
        rule_check = _multi_span_match(
            candidate=answer_section,
            expected_spans=gold_spans,
            problem=None,
        )
        if rule_check.get("passed"):
            check = rule_check
        else:
            check = _semantic_match(
                problem,
                answer_section,
                gold_answer,
            )

    else:
        check = _semantic_match(
            problem,
            answer_section,
            gold_answer,
        )

    result.update(
        check
    )

    if not result[
        "passed"
    ]:
        result[
            "error"
        ] = result.get(
            "reason",
            "verification_failed",
        )

    return result


def verify(
    domain: str,
    problem: dict[str, Any],
    response: str,
) -> dict[str, Any]:
    if domain == "finance":
        return verify_tatqa(
            problem,
            response,
        )

    raise ValueError(
        f"Unsupported verification domain: {domain}"
    )