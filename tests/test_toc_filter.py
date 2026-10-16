from govparse.toc_filter import classify_pages


def test_toc_and_continuation():
    rows = "\n".join(f"사업 {i} ····· {i+1}" for i in range(10))
    result = classify_pages(["목 차\n" + rows, rows, "사업목적\n기업에 자금을 지원한다."])
    assert [p["action"] for p in result] == ["drop", "drop", "keep"]


def test_mixed_body_is_held():
    rows = "\n".join(f"사업 {i} ····· {i+1}" for i in range(10))
    assert classify_pages(["목차\n" + rows + "\n이 사업은 중소기업의 기술개발을 지원하고 연구비와 사업화 비용을 지급한다."])[0]["action"] == "hold"


def test_table_heading_and_scanned_not_deleted():
    pages = ["목차 작성 지원사업\n2025 2026 증감\n지원금 100 200 100", "", "목차\n사업 하나 12\n사업 둘 19"]
    assert all(p["action"] != "drop" for p in classify_pages(pages))


def test_unseeded_leaders_and_body_number_lists_held():
    rows = "\n".join(f"항목 {i} ····· {i+1}" for i in range(10))
    assert classify_pages([rows])[0]["action"] == "hold"
    assert classify_pages(["항목 10\n항목 20\n항목 30"])[0]["action"] == "keep"


def test_embedded_toc_not_just_front_matter():
    rows = "\n".join(f"사업 {i} ····· {i+1}" for i in range(10))
    result = classify_pages(["본문 설명"] * 20 + ["차례\n" + rows, "다음 본문"])
    assert [p["source_page"] for p in result if p["action"] == "drop"] == [21]


def test_blank_interrupts_continuation():
    rows = "\n".join(f"사업 {i} ····· {i+1}" for i in range(10))
    assert [p["action"] for p in classify_pages(["목차\n" + rows, "", rows])] == ["drop", "hold", "hold"]
