from govparse.table_use import classify


def grid(*texts):
    return {
        "rows": 2,
        "cols": len(texts) // 2,
        "cells": [{"blocks": [{"kind": "paragraph", "text": t}]} for t in texts],
    }


def test_flow_connector_surrounded_by_stages():
    assert classify(grid("접수", "➩", "결과", "월요일", "", "금요일")) == "V"


def test_regular_data_grid_and_blank_form():
    assert classify(grid("연도", "관측소", "2024", "12")) == "D"
    assert classify(grid("회사", "", "주소", "")) == "D"


def test_long_explanatory_cells_with_grid_labels():
    assert classify(grid("항목", "설명", "신청 대상의 적용 조건. " * 14, "참여 기간의 산정 조건. " * 9)) == "M"
