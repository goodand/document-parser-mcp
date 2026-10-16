from govparse.census_rules import match_name,extend_toc

def test_projection():
 assert match_name('K-sensor (R&D)','K-Sensor (R&D)')
 assert match_name('지역·산업','지역산업')
 assert match_name('민간투자 기반','민간투자\x01기반')
 assert not match_name('지원대상','지원제외대상')
 assert not match_name('지원(융자)','지원(보조)')
 assert not match_name('예산 2026','예산 2025')

def rows(texts):return [{'text':t,'anchor':{'section':'S','ordinal':i},'exclude_from_chunking':False} for i,t in enumerate(texts)]
def test_dense_toc():
 x=rows(['- 순 서 -']+[f'({i}) 사업이름\t{i*10}' for i in range(1,7)]+['사업목적 본문'])
 y=extend_toc(x)
 assert all(p['exclude_from_chunking'] for p in y[1:7]);assert not y[7]['exclude_from_chunking']
 assert not any(p['exclude_from_chunking'] for p in x)
def test_budget_and_single_entry_kept():
 for texts in [['(1) 사업\t100'],[f'({i}) 사업\t100\t200' for i in range(6)],[f'({i}) 사업\t{100-i}' for i in range(6)]]:
  assert not any(p['exclude_from_chunking'] for p in extend_toc(rows(texts)))

def test_cell_segments_do_not_cross_items():
 from govparse.census_rules import cell_segments
 text='․첫 사업\n지원\n․둘째 사업\n육성\n'
 parts=cell_segments(text)
 assert len(parts)==2
 assert match_name('첫 사업 지원',parts[0]['text'])
 assert not any(match_name('지원 둘째',p['text']) for p in parts)
 assert ''.join(p['text'] for p in parts)==text


def test_unheaded_budget_like_run_is_held():
 x=rows([f'({i}) 사업별 금액\t{i*100}' for i in range(6)])
 y=extend_toc(x)
 assert not any(p['exclude_from_chunking'] for p in y)
 assert all(p['toc_status']=='hold_unseeded_numbered_page_run' for p in y)
