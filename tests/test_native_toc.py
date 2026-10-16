from govparse.native_toc import classify_paragraphs

def rows(texts):
    return [{"anchor": {"section": "s", "ordinal": i}, "text": t} for i,t in enumerate(texts)]

def test_heading_seed_and_body_boundary():
    data=rows(["목 차", "", "[1권]", "기업 지원\t12", "기술 지원\t20", "창업 지원\t30", "사업목적", "기업 지원\t12"])
    out=classify_paragraphs(data)
    assert [x["exclude_from_chunking"] for x in out] == [True,True,True,True,True,True,False,False]

def test_crossref_alone_not_toc():
    data=rows(["본문 참조 12"]); data[0]["page_crossref"]=True
    assert not classify_paragraphs(data)[0]["exclude_from_chunking"]

def test_no_cross_section_and_short_false_heading():
    data=rows(["차례", "항목\t2", "다른 항목\t3", "다음\t4"])
    data[2]["anchor"]["section"]="other"
    assert not any(x["exclude_from_chunking"] for x in classify_paragraphs(data))

def test_body_mentions_and_single_item_preserved():
    assert not any(x["exclude_from_chunking"] for x in classify_paragraphs(rows(["목차", "사업\t20", "본문입니다"])))


def test_hwpx_nested_paragraph_not_duplicated(tmp_path):
    from zipfile import ZipFile
    from govparse.native_toc import read_hwpx
    f=tmp_path/"sample.hwpx"
    with ZipFile(f,"w") as z:
        z.writestr("Contents/section0.xml", '<root><p id="0"><run><t>부모</t><tbl><p id="0"><run><t>자식<tab/>12</t></run></p></tbl></run></p></root>')
    result=read_hwpx(f)
    assert [p["text"] for p in result] == ["부모", "자식\t12"]
    assert result[0]["anchor"] != result[1]["anchor"]


def test_volume_separator_and_pageless_heading():
    data=rows(["목차", "-  -", "( 1 권 )", "Ⅰ. 총괄표\t", "하나\t1", "둘\t2", "셋\t3", "사업목적", "다음\t4"])
    result=classify_paragraphs(data)
    assert all(x["exclude_from_chunking"] for x in result[:7])
    assert not any(x["exclude_from_chunking"] for x in result[7:])

def test_bridge_does_not_swallow_next_body_heading():
    result=classify_paragraphs(rows(["목차", "하나\t1", "둘\t2", "셋\t3", "Ⅰ. 총괄표\t", "기업을 지원한다."]))
    assert not result[4]["exclude_from_chunking"]

def test_budget_rows_not_toc_entries():
    result=classify_paragraphs(rows(["목차", "사업\t2025\t2026", "대상\t100\t200", "지원\t300\t400"]))
    assert not any(x["exclude_from_chunking"] for x in result)

def test_container_detection_ignores_suffix(tmp_path):
    from govparse.native_toc import detect_format
    from zipfile import ZipFile
    p=tmp_path/"wrong.hwp"
    with ZipFile(p,"w") as z:z.writestr("Contents/section0.xml", "<root/>")
    assert detect_format(p)=="hwpx"
    q=tmp_path/"other.hwpx";q.write_bytes(b"not a document")
    import pytest
    with pytest.raises(ValueError): detect_format(q)


def test_short_subheadings_between_dense_runs():
    data=rows(["목차", "하나\t1", "둘\t2", "셋\t3"]+[""]*14+["세 입", "일반회계", "가\t4", "나\t5", "다\t6", "본문입니다."])
    out=classify_paragraphs(data)
    assert all(p["exclude_from_chunking"] for p in out[:-1])
    assert not out[-1]["exclude_from_chunking"]

def test_prose_blocks_bridge():
    data=rows(["목차", "하나\t1", "둘\t2", "셋\t3", "지원한다", "가\t4", "나\t5", "다\t6"])
    assert not any(p["exclude_from_chunking"] for p in classify_paragraphs(data)[4:])


def test_comma_separated_short_heading_bridge():
    out=classify_paragraphs(rows(["목차","하나\t1","둘\t2","셋\t3","공통부서","기본경비, 인건비","가\t4","나\t5","다\t6","총괄표"]))
    assert all(p["exclude_from_chunking"] for p in out[:-1])
    assert not out[-1]["exclude_from_chunking"]


def test_run_uses_container_and_preserves_source(tmp_path):
    from zipfile import ZipFile
    from govparse.native_toc import run
    import json
    f=tmp_path/"actually_hwpx.hwp"
    xml='<root>'+''.join('<p><run><t>'+t+'</t></run></p>' for t in ['목차','하나<tab/>1','둘<tab/>2','셋<tab/>3','본문입니다.'])+'</root>'
    with ZipFile(f,'w') as z:z.writestr('Contents/section0.xml',xml)
    before=f.read_bytes()
    manifest=run(f,tmp_path/'result')
    assert manifest['actual_format']=='hwpx' and manifest['suffix_mismatch']
    assert manifest['excluded']==4 and f.read_bytes()==before
    body=[json.loads(s) for s in (tmp_path/'result/body.jsonl').read_text().splitlines()]
    assert [r['text'] for r in body]==['본문입니다.']
