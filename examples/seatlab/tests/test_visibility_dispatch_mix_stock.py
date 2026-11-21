from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.visibility import dispatch_mix_plan as plan
from examples.seatlab.visibility import dispatch_mix_run as runner
from examples.seatlab.visibility import dispatch_mix_stock as stock


def test_unused_native_default_is_not_a_third_seat(tmp_path: Path) -> None:
    registered = runner.config(tmp_path.resolve(), plan.grid()[0])
    result: dict[str, Any] = {
        **registered,
        "agent": {"name": "oracle", "model_name": None, "kwargs": {}},
    }
    stock._check_configs(registered, result, registered)
    with pytest.raises(ValueError, match="unexpected_top_level_agent"):
        stock._check_configs(
            registered,
            {**result, "agent": {"name": "oracle", "model_name": "other"}},
            registered,
        )
    wrong = deepcopy(result)
    wrong["participants"][0]["model_name"] = "wrong/model"
    with pytest.raises(ValueError, match="native participant"):
        stock._check_configs(wrong, wrong, registered)
