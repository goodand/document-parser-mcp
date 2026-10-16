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
