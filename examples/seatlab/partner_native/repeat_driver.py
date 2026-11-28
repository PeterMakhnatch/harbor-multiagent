from pathlib import Path

from ...native_evidence import Record
from . import diagnose, repeat_binding


def compare(
    first: Record,
    first_reg: Record,
    second: Record,
    second_reg: Record,
    *,
    roots: list[Path] | None,
    repeat_roots: list[Path] | None,
) -> Record:
    repeat_binding.compatible(first, first_reg, second, second_reg)
    if roots is None and repeat_roots is None:
        from . import rehearse

        check, left = rehearse.run_observed(first, first_reg)
        repeated, right = rehearse.run_observed(second, second_reg)
        if not (check["matched_prediction"] and repeated["matched_prediction"]):
            raise ValueError("a bundled control did not meet its physical predictions")
        return repeat_binding.panel(
            first, first_reg, left, second, second_reg, right, source=diagnose.SCRIPTED
        )
    if not roots or not repeat_roots:
        raise ValueError("each native registration needs its own trial roots")
    first_dirs = {path.resolve(strict=True) for path in roots}
    second_dirs = {path.resolve(strict=True) for path in repeat_roots}
    if any(
        a.is_relative_to(b) or b.is_relative_to(a)
        for a in first_dirs
        for b in second_dirs
    ):
        raise ValueError("native repeat roots must be separate directories")
    left = diagnose.read_native(first, first_reg, roots)
    right = diagnose.read_native(second, second_reg, repeat_roots)
    return repeat_binding.panel(
        first, first_reg, left, second, second_reg, right, source=diagnose.NATIVE
    )
