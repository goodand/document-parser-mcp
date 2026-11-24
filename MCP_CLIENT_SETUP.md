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
