from types import SimpleNamespace

import numpy as np

from sbt.pulse import _caps


def test_missing_mktcap_filled_from_previous_day():
    # 한 종목 3일: 마지막 날(야후 임시) 시총이 비어 있음 → 전날 시총 × 종가 변화
    panel = SimpleNamespace(cols={"mktcap": np.array([100.0, 110.0, np.nan]), "close": np.array([10.0, 11.0, 12.1])},
                            pos=np.array([0, 1, 2]))
    out = _caps(panel, np.array([1, 2]))
    assert out[0] == 110.0 and abs(out[1] - 121.0) < 1e-9
