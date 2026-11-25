# Document Parser MCP

HWP·HWPX·PDF를 텍스트·문단·표·셀·시각 근거로 구조화하는 범용 MCP 서버입니다. HWP 계열의 원본 문단·셀 경계 보존과 Windows 한컴 변환을 지원합니다. 정부 예산서는 개발·평가에 사용한 적용 사례이며 입력 도메인 제한이 아닙니다.

Python 패키지와 실행 명령은 호환성을 위해 `govparse`, `govparse-mcp`를 유지합니다. 현재 버전은 `0.2.0`입니다. 작성자: `chatgpt_gpt6`(제품 연결·검증 기록). 재사용한 실험 코드의 출처는 `provenance/c3-source-reuse.json`에 기록했습니다.

## 데스크톱 앱

[GovParse Desktop](govparse-desktop/README.md)에서 HWP/HWPX 문서를 미리 보고 그림을 검토하거나 HWPX로 내보낼 수 있습니다. 기존 Windows x64 앱과 PySide6 미리보기 버전의 소스가 함께 있습니다. PySide6 화면 테스트는 Windows에서 실행했고 macOS 실기 검증은 남아 있습니다.

## 설치

Python 3.12 또는 3.13과 uv를 준비합니다. 저장소에서:

```powershell
uv sync --frozen
uv run govparse-mcp
```

마지막 명령은 stdio MCP 서버를 시작하므로 터미널이 입력을 기다리는 것이 정상입니다. 등록·환경 설정은 [MCP_CLIENT_SETUP.md](MCP_CLIENT_SETUP.md)를 따르세요. 개발 테스트는 `uv sync --frozen --group dev` 후 `uv run pytest tests -q`로 실행합니다.

| 기능 | 실행 조건 |
|---|---|
| HWP/HWPX 원본 문단·표·셀 구조 | `rhwp-python` |
| HWP 기본 C3 조합 | 패키지에 포함된 native decoder + pyhwp + rhwp + 변환 계보가 검증된 PDF |
| 편집 가능한 HWPX 본문 복원 | Windows/macOS, `restore_hwp_body` |
| PDF 구조 파싱 | opendataloader-pdf, Java 11 이상 |
| PDF 표 복구·페이지 이미지 | PyMuPDF |
| HWP/HWPX → PDF 변환 | Windows + 한컴오피스 + COM |
| 로컬 표 OCR 보조 | `uv sync --frozen --extra table-ocr`, 별도 한국어 OCR 모델 |

OCR 모델·한컴오피스·JDK 바이너리·문서 코퍼스는 이 저장소에 포함하지 않습니다. Java는 PATH 또는 `JAVA_HOME`으로 지정합니다. `doctor`의 환경 점검 성공과 실제 문서의 파싱 품질은 별도로 확인해야 합니다.

## 그림과 표 캡처에서 글자 읽기

**Desktop**에서는 문서를 열고 **그림 목록**에서 그림을 선택한 뒤 **이 그림 OCR**을 누릅니다. **텍스트 복사**로 결과를 옮길 수 있습니다. 이 기능에는 로컬 OCR 엔진과 모델이 필요합니다. [Desktop 사용 설명서](govparse-desktop/manual.html)

**MCP**에서는 `ocr_image`에 서버가 읽을 수 있는 이미지 경로를 전달합니다. 예를 들어 LM Studio에서 “govparse로 이 표 PNG의 글자를 읽고 금액과 단위를 보여 줘”라고 요청할 수 있습니다. 다음은 도구에 전달할 인수 예시입니다.

```json
{"image_path": "C:/documents/assets/table.png"}
```

`uv sync --frozen --extra table-ocr`로 OCR 의존성을 설치하고 한국어 모델을 준비합니다. 기본 모델 폴더는 데이터 루트 아래 `models/rapidocr`이며 `GOVPARSE_OCR_MODEL_DIR`로 지정할 수 있습니다. MCP는 기본적으로 긴 변을 1,024px 이하로 축소한 뒤 흰 여백 60px를 추가하고 글자 영역 좌표를 원본 픽셀 기준으로 반환합니다. `padding_px`는 0~256 범위에서 지정하며, `raw=true`로 원본 입력의 결과를 대조할 수 있습니다.

### 한국어 OCR 모델 선택 근거

중소벤처기업부 로고 1장과 한 실문서에서 추출한 표 4장으로 세 인식기를 비교했습니다. 긴 변을 최대 1,024px로 줄이고 흰 여백 60px를 붙인 뒤, 동일한 mobile 검출기로 얻은 글자 영역 29개를 각 인식기에 전달했습니다. 아래 시간은 CPU에서 각 이미지의 인식을 예열 후 3회 측정한 중앙값의 합계입니다. 모델 로딩과 검출 시간은 제외했습니다.

| 인식기 | 문자 오류 / 평가 문자 | 5장 인식 시간 |
|---|---:|---:|
| RapidOCR · PP-OCRv5 한국어 mobile | 1 / 117 | 0.53초 |
| ko-trocr | 3 / 117 | 9.67초 |
| EasyOCR korean_g2 | 8 / 117 | 1.00초 |

평가에는 한글·영문·숫자·쉼표를 포함했습니다. 이 표본에서 가장 정확하고 빨랐던 RapidOCR mobile을 기본으로 사용합니다. 1,024px는 모델 비교 실험의 입력 조건입니다. MCP의 축소 상한은 `max_side_px`로 변경할 수 있습니다. 큰 표의 작은 글자나 금액을 대조하려면 `max_side_px: null`로 여백만 적용하거나 `raw: true`로 축소와 여백을 모두 생략하세요.

## 사용 흐름

1. `doctor`로 사용할 수 있는 엔진을 확인합니다.
2. `prepare_corpus(source_dir="C:/documents")`로 문서 목록을 준비합니다.
3. `list_documents()`에서 정규화된 이름을 확인합니다.
4. HWP는 `convert_hwp` 후 `get_best_available(doc_name, output_kind="structure")`를 기본으로 사용합니다. `tables`와 기본 `rag`도 C3 조합을 실행합니다. HWPX 원본 구조는 `parse_hwp_source`, PDF 구조는 `parse_document`를 사용합니다.
5. `get_tables`, `get_visual_evidence`, `get_hwp_element`, `search_hwp_source`로 근거를 조회하고, 필요한 경우 RAG JSONL을 내보냅니다.

`extract_hwp_table_images(doc_name, list_only=true)`는 렌더하지 않고 중첩 표를 포함한 모든 표 ID를 연결 IR JSON에 표시합니다. 그다음 전체 렌더 또는 선택한 ID의 렌더를 호출할 수 있습니다. 표별 원본 구조와 셀 텍스트가 정확히 대응하는 경우에만 이미지를 만들며, 결과는 요약과 manifest·IR 경로로 반환합니다. manifest에는 이미지 경로·SHA-256, 생성 방식 및 표별 실패 사유가 포함되고, 상대 이미지 경로는 `image_dir`를 기준으로 해석합니다. 출력 경로는 원본 SHA-256과 실행별 ID를 포함해 이전 실행의 파일과 섞이지 않습니다. 32MiB보다 큰 HWPX 섹션은 표 캡처용 전용 IR로 읽으며, 전체 문서 IR인 `document.json`과 구분해 응답합니다.

`get_best_available`은 출력 목적에 맞는 경로를 안내·실행합니다. `recover_pdf_table_text`의 OCR 결과와 `diagnose_hwp_pdf_tables`의 표 위험 진단은 검토 후보이며 의미적 정확성이나 원문과의 대응을 자동 승인하지 않습니다.

HWP/HWPX 원본 위치와 PDF 페이지는 서로 다른 좌표계입니다. 변환 계보·해시를 유지하며, 그림·표 제목·단위·각주·이어짐의 대응이 입증되지 않으면 판단을 보류합니다. 정부 예산서에서 얻은 기존 수치를 모든 문서의 정확도로 일반화하지 않습니다. [C3 검토 방법 Skill](skills/c3-hwp-parsing/SKILL.md)을 HWP 기본 검토 절차로 사용합니다.

0.2.0은 기존 C3 실험의 native/pyhwp/rhwp 비교와 control consumer 표현 생성을 제품에 연결합니다. 매 요청마다 독립 실행 디렉터리에 원시 출력, 검증 결과, `combined_document.json`을 보존합니다. C2의 페이지별 대응과 C3의 의미 판단은 자동 완료되지 않습니다. `artifacts_ready: true`와 `status: held`가 함께 반환될 수 있으며 `complete`는 false입니다. `phases`, `mismatches`, `next_action`을 확인하고 원문 화면 검토를 이어가세요. PDF가 없거나 계보가 불명확하면 `convert_hwp`를 안내하며 C3를 성공했다고 표시하지 않습니다.

`parse_hwp_source` 등 개별 엔진 도구와 명시적인 `rag_mode="text_only_structure_aware"`는 기존 진단·텍스트 경로를 유지합니다. 기본 C3 RAG는 문단별 JSONL이며 표 관계와 control 표현은 함께 반환하는 C3 문서를 참조합니다. 가져온 C3 JSON도 해시 확인만으로 의미 검증 완료로 간주하지 않습니다.

## 경로

기본 데이터 루트는 사용자 홈의 `.govparse`, 기본 원본 입력은 그 아래 `input`입니다. 설치 디렉터리에 코퍼스를 쓰지 않습니다. `GOVPARSE_DATA`, `GOVPARSE_SOURCE`로 각각 덮어쓸 수 있습니다. 기존 Windows MCP의 명시적 `GOVPARSE_DATA` 설정은 계속 유효합니다.

생성된 자료는 `00_raw`, `01_extracted`, `02_normalized`, `03_converted`, `04_parsed`, `05_reports` 등으로 관리됩니다. 문서·변환 산출물·비밀 설정·DB는 Git에서 제외합니다. 패키지의 TOC·사업정보 정제 보조 모듈은 과거 적용 사례의 호환 자산이며 사업 유사도를 계산하는 MCP 기능은 제공하지 않습니다.

## 반입과 검증

최신 로컬 패키지의 Python 소스 24개와 기존 테스트 17개를 선별했고, 별도의 이식성 테스트를 추가했습니다. 원문 PDF/HWP/HWPX·업무 DB·변환 문서·대량 실험 결과는 반입하지 않았습니다. Skill은 개발 방법 문서이며 기존 Markdown 출처 조사에서 변환 로그가 발견되지 않은 항목입니다. 이 README와 MCP 설정 문서는 새로 작성한 사용 설명입니다.

[반입 해시](provenance/import-manifest.json), [검증 결과](provenance/verification.json)를 확인하세요. 등록된 MCP 도구는 19개입니다. 원본 Windows 실행 환경을 수정하거나 기존 MCP 등록을 교체하지 않고, 이 checkout과 격리 환경에서 검증합니다.

라이선스 표기는 기존 `Proprietary`를 유지합니다. 공개 오픈소스 라이선스를 새로 부여하지 않았습니다.

### HWP 본문 복원

MCP 도구 `restore_hwp_body(doc_name)`는 등록된 HWP/HWPX의 본문 문단과 중첩 표를 편집 가능한 HWPX로 저장하고 결과 경로를 반환합니다. 원본 XML에서 배치와 자산을 대응할 수 있는 그림은 HWPX에 포함합니다. 같은 그림을 여러 곳에 사용한 경우 각 배치의 위치와 크기를 별도로 처리합니다. 일반 도형과 정확한 쪽 배치는 원본과 렌더를 대조해 확인하세요.
`table_diff`는 본문 표의 순서와 중첩 위치, 셀 주소, 병합 범위, 셀 텍스트를 비교합니다. `json_to_hwpx`는 파싱 JSON과 복원본을, HWPX 입력의 `source_to_json`은 원본과 JSON을 비교합니다. 바이너리 HWP의 `source_to_json`은 null입니다. 차이 건수와 최대 10개 위치를 반환합니다.

### HWPX 표 복원 검증

파서가 원본과 대응시킨 셀에서 도형의 배치와 좌표를 읽고, 복원기가 그 셀에 편집 가능한 HWPX 도형으로 기록합니다. 텍스트 상자, 바이너리 자산에 의존하는 도형, 다른 도형을 참조하는 연결선에는 이 방식이 적용되지 않습니다.

기상청 사업설명자료의 두 중첩 표를 rhwp로 대조했습니다. 복원한 흐름도에서 도형 6개의 트리가 원본과 일치했습니다(재발급한 객체 ID 제외). 하단 설명과 화살표의 겹침은 여전히 보입니다. 회차별 화면 차이와 구조 비교 방법은 [표 복원 평가](docs/table-roundtrip-evaluation.md)에 정리했습니다.

합성 문서 회귀 검사는 `python -m pytest tests/test_vector_anchor_roundtrip_crop.py tests/test_table_vector_kinds.py tests/test_table_images.py`로 실행합니다.
