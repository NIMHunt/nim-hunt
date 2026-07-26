from __future__ import annotations

import constants as const
import database as schema


def test_remainder_refund_type_and_active_unique_index_are_stable():
    assert const.TRANS_TYPE_REMAINDER_REFUND == 12
    query = schema.TRANS_INDEX_SPOT_ACTIVE_REMAINDER_REFUND_UNIQUE_QUERY
    assert "CREATE UNIQUE INDEX IF NOT EXISTS" in query
    assert str(const.TRANS_TYPE_REMAINDER_REFUND) in query
    assert str(const.TRANS_STATUS_FAILED) in query
