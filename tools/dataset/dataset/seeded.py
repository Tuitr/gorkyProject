"""Values drawn from the seed.

Each value depends only on the seed and its label, never on how many values were drawn before it,
so adding a task or a student does not shift the numbers of the others. SHA-256 gives the same
result on every Python version, unlike the methods of random.Random other than random().
"""

import hashlib
from collections.abc import Sequence


class SeededDraws:
    def __init__(self, seed: int) -> None:
        self.seed = seed

    def number(self, label: str) -> int:
        digest = hashlib.sha256(f"{self.seed}/{label}".encode()).digest()
        return int.from_bytes(digest[:8], "big")

    def integer(self, label: str, low: int, high: int) -> int:
        """An integer from low to high inclusive."""
        if low > high:
            raise ValueError(f"empty range {low}..{high}")
        return low + self.number(label) % (high - low + 1)

    def choice[T](self, label: str, options: Sequence[T]) -> T:
        return options[self.integer(label, 0, len(options) - 1)]

    def shuffled[T](self, label: str, options: Sequence[T]) -> list[T]:
        order = sorted(range(len(options)), key=lambda index: self.number(f"{label}/{index}"))
        return [options[index] for index in order]
