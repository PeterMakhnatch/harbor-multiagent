from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NativeCase:
    seed: int
    east: tuple[bytes, bytes]
    west: tuple[bytes, bytes]
    actions: tuple[str, ...]
    scores: tuple[tuple[int, ...], ...]


@dataclass(frozen=True, slots=True)
class Cell:
    weight: int
    east: bytes
    west: bytes
    scores: tuple[tuple[int, ...], ...]
