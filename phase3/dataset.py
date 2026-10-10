from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.request import Request
from urllib.request import urlopen

from phase3.config import (
    SEED,
    TATQA_DATA_ROOT,
)


TATQA_BASE_URL = (
    "https://raw.githubusercontent.com/"
    "NExTplusplus/TAT-QA/master/dataset_raw"
)

SPLIT_FILES = {
    "train": "tatqa_dataset_train.json",
    "validation": "tatqa_dataset_dev.json",
    "test": "tatqa_dataset_dev.json",
    "dev": "tatqa_dataset_dev.json",
}


def _download_split(
    split: str,
) -> Path:
    if split not in SPLIT_FILES:
        raise ValueError(
            f"Unsupported split '{split}'. "
            f"Available splits: {list(SPLIT_FILES)}"
        )

    TATQA_DATA_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    filename = SPLIT_FILES[split]
    path = TATQA_DATA_ROOT / filename

    if path.exists():
        return path

    url = f"{TATQA_BASE_URL}/{filename}"

    print(
        f"Downloading TAT-QA {split} split..."
    )
    print(
        f"URL: {url}"
    )

    request = Request(
        url,
        headers={
            "User-Agent": "LLM-Cascade/Phase3"
        },
    )

    with urlopen(request) as response:
        data = response.read()

    path.write_bytes(data)

    print(
        f"Saved {len(data):,} bytes to {path}"
    )

    return path


def load_tatqa_split(
    split: str,
) -> list[dict[str, Any]]:
    path = _download_split(split)

    with path.open(
        "r",
        encoding="utf-8",
    ) as handle:
        data = json.load(handle)

    if not isinstance(data, list):
        raise ValueError(
            f"Expected a list in {path}, "
            f"got {type(data).__name__}"
        )

    # Document-level split for validation and test from dev to avoid cross-question leakage
    if split == "validation":
        split_point = len(data) // 2
        return data[:split_point]
    if split == "test":
        split_point = len(data) // 2
        return data[split_point:]

    return data


def _normalize_table(
    table_data: Any,
) -> list[list[str]]:
    if isinstance(
        table_data,
        dict,
    ):
        table_data = table_data.get(
            "table",
            [],
        )

    if not isinstance(
        table_data,
        list,
    ):
        return []

    normalized = []

    for row in table_data:
        if isinstance(
            row,
            list,
        ):
            normalized.append(
                [
                    str(cell)
                    for cell in row
                ]
            )
        else:
            normalized.append(
                [str(row)]
            )

    return normalized


def _normalize_paragraphs(
    paragraphs: Any,
) -> list[str]:
    if not isinstance(
        paragraphs,
        list,
    ):
        return []

    normalized = []

    for paragraph in paragraphs:
        if isinstance(
            paragraph,
            dict,
        ):
            text = paragraph.get(
                "text",
                "",
            )
        else:
            text = paragraph

        if text is not None:
            normalized.append(
                str(text)
            )

    return normalized


def normalize_tatqa_record(
    record: dict[str, Any],
    question_record: dict[str, Any],
    document_index: int,
    question_index: int,
    split: str,
) -> dict[str, Any]:
    question = question_record.get(
        "question"
    )

    if not question:
        raise ValueError(
            f"TAT-QA document {document_index}, "
            f"question {question_index} in split "
            f"'{split}' has no question."
        )

    question_id = question_record.get(
        "uid"
    )

    if question_id:
        record_id = str(
            question_id
        )
    else:
        record_id = (
            f"tatqa_{split}_"
            f"{document_index:06d}_"
            f"{question_index:03d}"
        )

    return {
        "id": record_id,
        "question_id": question_id,
        "domain": "finance",
        "dataset": "tatqa",
        "split": split,
        "prompt": str(question),
        "table": _normalize_table(
            record.get("table")
        ),
        "paragraphs": _normalize_paragraphs(
            record.get("paragraphs")
        ),
        "answer": question_record.get(
            "answer"
        ),
        "answer_type": question_record.get(
            "answer_type"
        ),
        "answer_from": question_record.get(
            "answer_from"
        ),
        "derivation": question_record.get(
            "derivation"
        ),
        "scale": question_record.get(
            "scale"
        ),
        "rel_paragraphs": question_record.get(
            "rel_paragraphs"
        ),
        "req_comparison": question_record.get(
            "req_comparison",
            False,
        ),
        "order": question_record.get(
            "order"
        ),
        "document_index": document_index,
        "question_index": question_index,
    }


def get_tatqa_split(
    split: str,
    subset_size: int | None = None,
    seed: int = SEED,
) -> list[dict[str, Any]]:
    raw_records = load_tatqa_split(
        split
    )

    normalized = []

    for document_index, record in enumerate(
        raw_records,
        start=1,
    ):
        questions = record.get(
            "questions",
            [],
        )

        if not isinstance(
            questions,
            list,
        ):
            raise ValueError(
                f"TAT-QA document {document_index} "
                f"in split '{split}' has invalid questions."
            )

        for question_index, question_record in enumerate(
            questions,
            start=1,
        ):
            normalized.append(
                normalize_tatqa_record(
                    record=record,
                    question_record=question_record,
                    document_index=document_index,
                    question_index=question_index,
                    split=split,
                )
            )

    if subset_size is None:
        return normalized

    if subset_size <= 0:
        raise ValueError(
            "subset_size must be positive."
        )

    if subset_size >= len(normalized):
        return normalized

    return select_tatqa_subset(
        normalized,
        subset_size,
        seed,
    )


def has_nonempty_gold_answer(
    record: dict[str, Any],
) -> bool:
    answer = record.get("answer")

    if answer is None:
        return False

    if isinstance(answer, str):
        return bool(answer.strip())

    if isinstance(answer, (list, dict)):
        return bool(answer)

    return True


def select_additional_training_records(
    records: list[dict[str, Any]],
    size: int,
    excluded_ids: set[str],
    seed: int = SEED,
) -> list[dict[str, Any]]:
    if size <= 0:
        raise ValueError(
            "size must be positive."
        )

    candidates = [
        record
        for record in records
        if has_nonempty_gold_answer(record)
        and str(record.get("id", "")) not in excluded_ids
    ]

    unique_candidates = {}
    for record in candidates:
        record_id = str(record.get("id", ""))
        if record_id:
            unique_candidates.setdefault(record_id, record)

    candidates = list(unique_candidates.values())

    if len(candidates) < size:
        raise ValueError(
            f"Requested {size} additional training questions, "
            f"but only {len(candidates)} unique questions have "
            "non-empty gold answers and are not already collected."
        )

    return select_tatqa_subset(
        candidates,
        size,
        seed,
    )


def select_tatqa_subset(
    records: list[dict[str, Any]],
    size: int,
    seed: int = SEED,
) -> list[dict[str, Any]]:
    if size <= 0:
        raise ValueError(
            "size must be positive."
        )

    if size >= len(records):
        return list(records)

    groups = defaultdict(list)

    for record in records:
        answer_type = str(
            record.get(
                "answer_type",
                "unknown",
            )
        )

        groups[
            answer_type
        ].append(record)

    rng = random.Random(seed)

    for group in groups.values():
        rng.shuffle(group)

    group_names = sorted(
        groups.keys()
    )

    selected = []

    base = size // len(
        group_names
    )

    remainder = size % len(
        group_names
    )

    for index, group_name in enumerate(
        group_names
    ):
        take = base + (
            1
            if index < remainder
            else 0
        )

        selected.extend(
            groups[group_name][
                :take
            ]
        )

    if len(selected) < size:
        selected_ids = {
            record["id"]
            for record in selected
        }

        remaining = [
            record
            for record in records
            if record["id"]
            not in selected_ids
        ]

        rng.shuffle(
            remaining
        )

        selected.extend(
            remaining[
                :size - len(selected)
            ]
        )

    rng.shuffle(
        selected
    )

    selected_ids = {
        record["id"]
        for record in selected
    }

    return [
        record
        for record in records
        if record["id"]
        in selected_ids
    ]


def get_tatqa_statistics(
    split: str | None = None,
) -> dict[str, int]:
    if split is not None:
        return {
            split: len(
                get_tatqa_split(
                    split
                )
            )
        }

    return {
        name: len(
            get_tatqa_split(name)
        )
        for name in SPLIT_FILES
    }


def get_tatqa_subset_statistics() -> dict[str, int]:
    from phase3.config import (
        TATQA_SUBSET_SIZES,
    )

    return {
        split: len(
            get_tatqa_split(
                split,
                subset_size=size,
            )
        )
        for split, size in (
            TATQA_SUBSET_SIZES.items()
        )
    }


def get_phase3_finance_data(
    split: str,
    subset_size: int | None = None,
) -> list[dict[str, Any]]:
    return get_tatqa_split(
        split,
        subset_size=subset_size,
    )


def get_train_data(
    subset_size: int | None = None,
) -> list[dict[str, Any]]:
    return get_tatqa_split(
        "train",
        subset_size=subset_size,
    )


def get_validation_data(
    subset_size: int | None = None,
) -> list[dict[str, Any]]:
    return get_tatqa_split(
        "validation",
        subset_size=subset_size,
    )


def get_test_data(
    subset_size: int | None = None,
) -> list[dict[str, Any]]:
    return get_tatqa_split(
        "test",
        subset_size=subset_size,
    )


def shuffle_training_data(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    shuffled = list(records)

    rng = random.Random(
        SEED
    )

    rng.shuffle(
        shuffled
    )

    return shuffled