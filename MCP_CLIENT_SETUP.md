# MCP client setup

작성자: `chatgpt_gpt6`. 작성한 설치 안내이며 원문 문서를 변환한 MD가 아닙니다.

`uv sync --frozen`으로 설치한 뒤 실제 checkout의 실행 파일을 MCP 클라이언트에 등록합니다. 서버 이름 `govparse`와 도구 이름은 기존 호환성을 유지합니다.

```json
{
  "mcpServers": {
    "govparse": {
      "command": "C:/path/to/document-parser-mcp/.venv/Scripts/govparse-mcp.exe",
      "env": {
        "GOVPARSE_DATA": "C:/document-parser-data",
        "GOVPARSE_HEAP": "3g",
        "JAVA_HOME": "C:/path/to/jdk",
        "PYTHONUTF8": "1"
      }
    }
  }
}
```

경로를 실제 설치 위치로 바꾸세요. Java가 PATH에 있으면 `JAVA_HOME`은 생략할 수 있습니다. 데이터 경로는 원문과 생성 결과가 저장될 위치입니다. 클라이언트는 이미지 근거의 반환 경로를 읽을 수 있어야 합니다. 이 서버는 문서를 외부 API에 자동 전송하는 서버가 아닙니다. `parse_document`의 hybrid 옵션은 별도의 선택 기능입니다.

이미 `govparse` 서버가 등록돼 있다면 신규 checkout을 별도 서버 이름으로 검증한 후 기존 등록을 변경하세요. 설정 예시만으로 현재 실행 서버가 교체되지는 않습니다. 클라이언트에서 재연결한 후 `doctor`와 도구 목록을 확인합니다.

다른 OS의 실행 파일 경로는 `.venv/bin/govparse-mcp`입니다. HWP/HWPX→PDF COM 변환은 Windows 한컴오피스가 필요합니다. 이번 반입은 Windows에서 검증했으며 다른 OS의 전체 동작을 검증하지 않았습니다.
## macOS에서 설치하고 복원하기

Apple Silicon과 Intel Mac에서 Python 3.12 또는 3.13과 uv를 사용합니다. 터미널에서 실행하세요.

```sh
git clone https://github.com/goodand/document-parser-mcp.git
cd document-parser-mcp
uv sync --frozen
pwd
```

LM Studio의 `mcp.json`에서 기존 `mcpServers`에 아래 항목을 추가합니다. 두 경로를 실제 절대 경로로 바꾸세요. `command`에는 `pwd`로 확인한 저장소 경로를 사용합니다.

```json
{
  "mcpServers": {
    "govparse": {
      "command": "/Users/yourname/document-parser-mcp/.venv/bin/govparse-mcp",
      "env": {
        "GOVPARSE_DATA": "/Users/yourname/document-parser-data",
        "PYTHONUTF8": "1"
      }
    }
  }
}
```

LM Studio에서 MCP를 다시 연결하고 `prepare_corpus`에 원본 문서 폴더의 절대 경로를 전달하세요. `list_documents`에서 확인한 정규화 이름으로 `restore_hwp_body`를 호출하고, 응답의 `hwpx_path`를 열면 편집 가능한 본문과 표를 확인할 수 있습니다.

HWP/HWPX 원본 구조 추출과 HWPX 본문 복원은 Java나 한컴 COM을 호출하지 않습니다. PDF 파싱에는 Java가 필요합니다. LM Studio에서 Java를 찾지 못할 때는 `/usr/libexec/java_home`으로 확인한 경로를 `JAVA_HOME`으로 지정하세요. Windows 한컴 COM에 의존하는 HWP/HWPX → PDF 변환이 필요한 작업은 Windows에서 변환한 PDF를 준비하세요.

## 다른 PC로 이전하기

새 PC에서 GitHub 저장소를 클론하고 의존성을 새로 설치합니다. 기존 PC에서 재사용할 원본 문서, 코퍼스, 복원본은 별도로 백업하세요. LM Studio 설정도 보관해 두고 새 PC의 설치 및 데이터 경로에 맞게 바꾸면 됩니다. 백업은 새 PC에서 문서 목록과 HWPX 복원을 확인할 때까지 유지하세요.

## Claude Code에서 연결하기

Claude Code CLI는 로컬 stdio MCP 서버를 지원합니다. 저장소 루트에서 의존성을 설치한 뒤 아래 명령을 실행하세요. 경로를 실제 checkout과 데이터 보관 위치로 바꾸세요.

```powershell
uv sync --frozen
claude mcp add-json govparse '{"type":"stdio","command":"C:/path/to/document-parser-mcp/.venv/Scripts/govparse-mcp.exe","args":[],"env":{"GOVPARSE_DATA":"C:/document-parser-data","PYTHONUTF8":"1"}}' --scope local
claude mcp get govparse
```

`local` 범위는 이 사용자 계정의 해당 프로젝트에만 등록되고, 저장소에 설정 파일을 추가하지 않습니다. 팀과 공유하려면 프로젝트 루트의 `.mcp.json`에 같은 서버 항목을 작성하고, Claude Code가 해당 폴더를 신뢰하도록 승인한 뒤 MCP 서버 승인 요청을 확인하세요. 로컬 서버는 직접 만든 가상환경의 실행 파일을 가리켜야 합니다. macOS/Linux에서는 `command`를 `.../.venv/bin/govparse-mcp`로 바꿉니다. PDF 변환·파싱은 OS별 한컴 변환 가능 여부와 Java 설정을 따릅니다.

실행 후 Claude Code에서 `/mcp`를 열거나 터미널에서 `claude mcp list`로 상태를 확인하고, `doctor`를 호출하세요. 현재 서버가 제공하는 도구는 클라이언트에서 발견한 스키마를 기준으로 사용합니다. 주요 서명은 다음과 같습니다.

| 도구 | 입력 |
| --- | --- |
| `doctor` | 입력 없음 |
| `prepare_corpus` | `source_dir: string` |
| `get_best_available` | `doc_name: string`, `output_kind: "structure" \| "tables" \| "visual" \| "rag"`; 선택 입력 `page`, `c3_document_path`, `odl_run_id`, `rag_mode` |
| `parse_hwp_source` | `doc_name: string` |
| `extract_hwp_table_images` | `doc_name: string`; 선택 입력 `table_ids: string[]`, `list_only: boolean` |
| `restore_hwp_body` | `doc_name: string` |
| `ocr_image` | `image_path: string`; 선택 입력 `raw: boolean`, `padding_px: integer`, `max_side_px: integer \| null` |

`restore_hwp_body`의 현재 범위는 본문 텍스트와 표이며 그림·원본 서식·쪽 배치는 복원하지 않습니다. `ocr_image`는 서버에 `govparse[table-ocr]` 선택 의존성이 설치되어야 사용할 수 있습니다. 다른 도구와 정확한 입력 형식은 서버 버전에 따라 달라질 수 있으므로 `/mcp`에서 현재 연결의 도구 목록을 확인하세요.

Claude Code가 사용할 모델은 MCP 설정에서 정하지 않습니다. Claude Code의 `/model` 메뉴나 사용자가 지정한 `--model`로 선택합니다. 파서는 문서를 로컬에서 처리하지만, MCP 도구가 텍스트나 이미지를 반환하면 Claude Code 호스트가 그 결과를 선택된 모델에 전달할 수 있습니다. 민감 문서는 도구 호출로 반환되는 범위를 확인하고, 조직의 모델·데이터 정책에 맞춰 사용하세요.

구성을 저장하지 않고 시험하려면 JSON 설정을 임시 파일에 작성해 `claude --mcp-config <임시파일> --strict-mcp-config`로 실행할 수 있습니다. `--strict-mcp-config`는 이 실행에서 명시한 MCP 서버만 사용하게 합니다. 이 저장소에서는 Claude Code 2.1.284와 `claude mcp add-json`, `--mcp-config`, `--strict-mcp-config` 옵션을 확인했습니다. 임시 설정으로 연결을 시도했지만 CLI 요청이 60초 동안 응답하지 않아 중단했습니다. 따라서 이 환경에서 설정의 실제 stdio 연결 및 도구 검색까지 성공했다고 확인한 것은 아닙니다.

공식 참고: [Claude Code MCP 연결·범위·임시 설정](https://code.claude.com/docs/en/mcp), [Claude Code 설정 및 모델 선택](https://code.claude.com/docs/en/settings).
