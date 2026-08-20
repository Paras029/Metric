"""Generic list-batching helper used wherever LLM calls need to process items in groups."""
from typing import Iterator, List, TypeVar

T = TypeVar("T")


def chunks(items: List[T], size: int) -> Iterator[List[T]]:
    """Yield successive slices of `items`, each up to `size` long."""
    for i in range(0, len(items), size):
        yield items[i:i + size]
