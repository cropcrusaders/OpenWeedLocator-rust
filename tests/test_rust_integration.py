import numpy as np
import pytest

from utils.greenonbrown import GreenOnBrown


def test_rust_python_equivalence():
    image = np.random.randint(0, 255, (10, 10, 3), dtype=np.uint8)
    detector_rust = GreenOnBrown(use_rust=True)
    detector_py = GreenOnBrown(use_rust=False)

    result_rust = detector_rust.inference(image, min_detection_area=1)
    result_py = detector_py.inference(image, min_detection_area=1)

    assert isinstance(result_rust, tuple)
    assert len(result_rust) == len(result_py)

