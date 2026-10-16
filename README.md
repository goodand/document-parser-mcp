# Document Parser MCP

HWP·HWPX·PDF를 텍스트·문단·표·셀·시각 근거로 구조화하는 범용 MCP 서버입니다. HWP 계열의 원본 문단·셀 경계 보존과 Windows 한컴 변환을 지원합니다. 정부 예산서는 개발·평가에 사용한 적용 사례이며 입력 도메인 제한이 아닙니다.

Python 패키지와 실행 명령은 호환성을 위해 `govparse`, `govparse-mcp`를 유지합니다. 반입 버전은 `0.1.1`입니다. 작성자: `chatgpt_gpt6`(repo 반입·경로 이식성·검증 기록). 기존 코드의 저자 표기는 각 파일을 따릅니다.

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
| PDF 구조 파싱 | opendataloader-pdf, Java 11 이상 |
| PDF 표 복구·페이지 이미지 | PyMuPDF |
| HWP/HWPX → PDF 변환 | Windows + 한컴오피스 + COM |
| 로컬 표 OCR 보조 | `uv sync --frozen --extra table-ocr`, 별도 한국어 OCR 모델 |

OCR 모델·한컴오피스·JDK 바이너리·문서 코퍼스는 이 저장소에 포함하지 않습니다. Java는 PATH 또는 `JAVA_HOME`으로 지정합니다. `doctor`의 환경 점검 성공과 실제 문서의 파싱 품질은 별도로 확인해야 합니다.

## 사용 흐름

1. `doctor`로 사용할 수 있는 엔진을 확인합니다.
2. `prepare_corpus(source_dir="C:/documents")`로 문서 목록을 준비합니다.
3. `list_documents()`에서 정규화된 이름을 확인합니다.
4. HWP/HWPX 원본 구조는 `parse_hwp_source`, PDF 구조는 `parse_document`를 사용합니다. HWP를 PDF로 처리하려면 먼저 `convert_hwp`가 필요합니다.
5. `get_tables`, `get_visual_evidence`, `get_hwp_element`, `search_hwp_source`로 근거를 조회하고, 필요한 경우 RAG JSONL을 내보냅니다.

`get_best_available`은 출력 목적에 맞는 경로를 안내·실행합니다. `recover_pdf_table_text`의 OCR 결과와 `diagnose_hwp_pdf_tables`의 표 위험 진단은 검토 후보이며 의미적 정확성이나 원문과의 대응을 자동 승인하지 않습니다.

HWP/HWPX 원본 위치와 PDF 페이지는 서로 다른 좌표계입니다. 변환 계보·해시를 유지하며, 그림·표 제목·단위·각주·이어짐의 대응이 입증되지 않으면 판단을 보류합니다. 정부 예산서에서 얻은 기존 수치를 모든 문서의 정확도로 일반화하지 않습니다. [C3 검토 방법 Skill](skills/c3-hwp-parsing/SKILL.md)은 선택적으로 참고할 수 있습니다.

## 경로

기본 데이터 루트는 사용자 홈의 `.govparse`, 기본 원본 입력은 그 아래 `input`입니다. 설치 디렉터리에 코퍼스를 쓰지 않습니다. `GOVPARSE_DATA`, `GOVPARSE_SOURCE`로 각각 덮어쓸 수 있습니다. 기존 Windows MCP의 명시적 `GOVPARSE_DATA` 설정은 계속 유효합니다.

생성된 자료는 `00_raw`, `01_extracted`, `02_normalized`, `03_converted`, `04_parsed`, `05_reports` 등으로 관리됩니다. 문서·변환 산출물·비밀 설정·DB는 Git에서 제외합니다. 패키지의 TOC·사업정보 정제 보조 모듈은 과거 적용 사례의 호환 자산이며 사업 유사도를 계산하는 MCP 기능은 제공하지 않습니다.

## 반입과 검증

최신 로컬 패키지의 Python 소스 24개와 기존 테스트 17개를 선별했고, 별도의 이식성 테스트를 추가했습니다. 원문 PDF/HWP/HWPX·업무 DB·변환 문서·대량 실험 결과는 반입하지 않았습니다. Skill은 개발 방법 문서이며 기존 Markdown 출처 조사에서 변환 로그가 발견되지 않은 항목입니다. 이 README와 MCP 설정 문서는 새로 작성한 사용 설명입니다.

[반입 해시](provenance/import-manifest.json), [검증 결과](provenance/verification.json)를 확인하세요. 등록된 MCP 도구는 18개입니다. 원본 Windows 실행 환경을 수정하거나 기존 MCP 등록을 교체하지 않고, 이 checkout과 격리 환경에서 검증합니다.

라이선스 표기는 기존 `Proprietary`를 유지합니다. 공개 오픈소스 라이선스를 새로 부여하지 않았습니다.
