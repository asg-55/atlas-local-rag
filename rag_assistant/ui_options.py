from __future__ import annotations


CONTEXT_SIZES = (8192, 16384, 32768, 65536)


def normalized_context_limit(model_limit: int | None) -> int:
    try:
        limit = int(model_limit or CONTEXT_SIZES[0])
    except (TypeError, ValueError):
        limit = CONTEXT_SIZES[0]
    return max(CONTEXT_SIZES[0], limit)


def context_size_options(model_limit: int | None) -> list[int]:
    """Return a non-empty, valid list of context sizes for the model UI."""
    limit = normalized_context_limit(model_limit)
    return [value for value in CONTEXT_SIZES if value <= limit]
