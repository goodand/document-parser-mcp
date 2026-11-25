# GovParse Desktop (PySide6) · 화면 사양과 테스트 계약

Mac·Linux·Windows 공통 화면을 PySide6로 다시 만들기 위한 계약입니다. `qt/tests/`의 테스트가 이 문서를 기준으로 화면을 찾고 조작합니다.
동작 기준은 현재 Windows 앱(`Desktop.cs`, `FigurePanel.cs`, `Theme.cs`, `SettingsForm.cs`, 2026-11-25)입니다. 화면 문구는 Windows 앱과 같은 한국어를 씁니다.

## 1. 구성과 주입

```python
from gpqt.app import MainWindow
w = MainWindow(worker=..., dialogs=..., platform=...)
```

| 인자 | 역할 | 테스트에서 |
|---|---|---|
| `worker` | 문서 처리 워커 클라이언트 (아래 2절) | `FakeWorker` |
| `dialogs` | 파일 열기·저장 대화상자, 확인 창 | `FakeDialogs` (미리 정한 경로를 돌려줌) |
| `platform` | OS별 기능: 한컴 유무, 파일 관리자에서 보이기, 기본 앱으로 열기, 설정 폴더 | `FakePlatform` (호출 기록) |

기본값(인자 생략)은 실제 워커, Qt 기본 대화상자, 현재 OS의 구현입니다.

## 2. 워커 클라이언트

```python
worker.run(mode: str, *args, on_progress=None, cancel: threading.Event | None = None) -> dict
worker.serve(cmd: dict) -> dict          # 그림 검토 세션(worker.py serve)의 한 줄 요청/응답
```

- 화면은 `run`을 UI 스레드 밖에서 부르고, 결과를 UI 스레드로 돌려 반영합니다.
- `on_progress`는 `@@progress` 줄 하나마다 `{stage, done?, total?, detail?}`로 불립니다.
- `cancel`이 켜지면 워커 프로세스 트리를 끝내고 `CancelledError`를 올립니다.
- 실패는 예외(메시지 = 워커 stderr 마지막 줄)로 올립니다.

| mode | args | 응답(dict) |
|---|---|---|
| `preview` | doc, out_png, page | `{pages, renderer, png}`. 워커의 원래 출력은 한 줄 `"<pages>|<renderer>"`이고 그림은 입력한 `out_png`에 써집니다. 클라이언트가 이 줄을 dict로 바꿉니다 |
| `summary` | doc | `{seconds, builder, paragraphs, tables, nested_tables, pictures, source_sha256, preview_note}` |
| `json` / `html` / `hwpx` / `hwp` | doc, out_dir | 워커 report (`saved`, `notes`, `bundle`, `restore`, `ir_builder` …) |
| `convert` | document.json, out_dir, `html`\|`hwpx` | report |
| `index` | document.json | `{assets, missing, source_name, table_images, integrity, capture_state, convertible}`. 2026-11-25 이전 결과는 `table_images`와 `integrity`가 `None` |
| `ocr` | png | `{status, texts, error, engine_label}` |
| `zip` | folder, target | `{zip, files, bytes}` |
| `settings` / `settings-set` | – / key, value | `{settings, path, engines:[{id,label,description,available,reason}]}` |

## 3. 위젯 이름 (objectName)

테스트는 위젯을 이 이름으로만 찾습니다. 위치나 크기에 의존하지 않습니다.

| 영역 | 이름 |
|---|---|
| 위쪽 막대 | `tabOriginal`, `tabResult`, `btnOpen`, `btnLoadResult`, `btnSettings`, `btnManual`, `lblFileName` |
| 쪽 도구 | `btnPrev`, `lblPage`, `btnNext`, `btnZoom`, `btnFigures` |
| 원본 보기 | `origView`, `origMessage`, `origAction` |
| 처리 상태 줄 | `stStructure`, `stPreview`, `stExport`, `busyBar` |
| 아래 상태 막대 | `statusText` |
| 내보내기 | `btnFormat`, `btnExport`, `btnCancel` |
| 결과 보기 | `resultView`, `resTitle`, `resState`, `resNotes`, `assetSearch`, `assetList`, `assetPreview`, `resultPages`, `resultWeb`, `btnOpenExternal`, `btnOpenFolder`, `btnOpenZip`, `btnToHtml`, `btnToHwpx` |
| OCR 상자 (두 곳: `resultOcr`, `figureOcr`) | `ocrRun`, `ocrCopy`, `ocrState`, `ocrText`, `ocrToCaption`(그림 쪽 편집 모드만) |
| 그림 목록 | `figurePanel`, `figSummary`, `figFilter`, `figSearch`, `figEditToggle`, `figList`, `figDetail`, `figStatus` |
| 그림 설명 편집 | `groupCaption`, `groupOcr`, `groupPicks`, `captionBox`, `btnCaptionSave`, `btnCaptionDelete`, `btnCaptionUndo`, `pickList` |
| 설정 창 | `settingsDialog`, `engine_<id>`(라디오), `settingsSave`, `settingsCancel`, `settingsPath` |

문구의 색 구분은 위젯의 동적 속성 `tone`(`ok` \| `warn` \| `muted` \| `text`)으로 확인합니다. 색 값은 OS 테마에 맞출 수 있습니다.

## 4. 목록 위젯

- `assetList`, `figList`는 `QTreeWidget`입니다. 칸 순서와 머리글은 Windows 앱과 같습니다.
  - `assetList`: `자산`, `출처`, `위치 / 설명`, `원본 파일`
  - `figList`: `그림`, `설명 여부`, `설명`, `문서 안 위치`, `원본 유형`. 묶음(group)은 최상위 항목, 그림은 그 자식입니다.
- 각 항목의 원본 데이터는 `item.data(0, Qt.UserRole)`에 dict로 둡니다.

## 4-1. 설명 검색

- 그림 목록의 `figSearch`, 결과 보기의 `assetSearch`. 안내 문구는 `설명으로 검색`입니다.
- 그림 목록은 그림의 설명(캡션)에서, 결과 보기는 `위치 / 설명` 칸에서 찾습니다. 공백과 대소문자는 무시합니다.
- 필터(`모든 그림` 등)와 함께 적용됩니다(둘 다 만족하는 그림만). `#번호`는 바뀌지 않습니다.
- 설명을 저장하면 바로 그 설명으로 찾을 수 있습니다. 목록을 다시 채워도 검색어는 유지되고, 새 문서를 열면 지워집니다. Esc로 지웁니다.
- 선택한 그림이 검색 결과에 남아 있으면 선택을 유지합니다. 검색으로 숨긴 그림은 MCP로도 선택되지 않습니다(`item_missing_or_filtered`).
- 상태 문구: `‘검색어’ 검색 결과 N개`, 결과가 없으면 `‘검색어’와 일치하는 설명이 없습니다.`
- 검색은 화면 안에서만 하고 워커를 부르지 않습니다. 여러 문서에 걸친 검색과 OCR 글자 검색은 엔진 쪽 색인이 생긴 뒤 다룹니다.

## 5. MCP 통로

```python
w.bridge_request(line: str) -> dict      # 'state' | 'original' | 'result' | 'show|<sha256>|<occ_id>'
```

- 응답과 상태 코드는 Windows 앱과 같습니다: `open`, `shown`, `selected`, `dialog_open`, `view_unavailable`, `figure_panel_unavailable`, `document_mismatch`, `item_missing_or_filtered`, `location_unverified`, `invalid_request`.
- 응답 필드: `status`, `title`, `view`, `source_sha256`, `item_id`, `page`.
- 전송 방식은 OS별입니다. 둘 다 같은 사용자만 접속할 수 있습니다.
  - **Windows**: named pipe `GovParseDesktop-<pid>`, 현재 사용자 SID만 허용.
  - **Mac·Linux**: Unix 소켓 `<dir>/govparse-desktop/app-<pid>.sock`.
    - `<dir>`은 Linux에서 `$XDG_RUNTIME_DIR`입니다. 설정돼 있지 않으면 Mac과 같은 사용자별 대체 경로를 씁니다.
    - Mac에서 `<dir>`은 사용자 전용 임시 폴더입니다.
    - 폴더는 0700이고 사용자 소유, 소켓은 0600입니다. 주소 길이는 104바이트 미만이어야 합니다.
- 형식: UTF-8 한 줄 요청, JSON 한 줄 응답.
- **찾기**: `gpqt.bridge.discover()`가 실제로 연결되는 앱 수를 셉니다. 0개면 `app_closed`, 1개면 `one`, 여러 개면 `multiple_windows`입니다. 죽은 앱이 남긴 소켓 파일은 연결이 안 되므로 세지 않고, 응답이 늦는 앱은 `timeout`으로 구분합니다.
- `app_closed`, `multiple_windows`는 앱이 아니라 연결하는 쪽(MCP 도구)이 판정합니다.
- 앱은 요청을 UI 스레드에서 처리하되, 소켓 대기는 별도 스레드에서 해서 화면이 멈추지 않게 합니다.

## 6. OS별 차이 (사양)

| 기능 | Windows | macOS · Linux |
|---|---|---|
| HWP로 내보내기 | 한컴이 있으면 사용 | 형식 목록에 보이되 꺼짐, 설명 `이 PC에서 한컴오피스를 찾지 못해 사용할 수 없음` |
| Windows 내장 OCR | 설정에서 선택 가능 | 설정에서 꺼짐, 사유 표시 |
| 폴더 열기 | 탐색기에서 파일 선택 | Finder(`open -R`) / 파일 관리자(`xdg-open` 폴더) |
| 단축키 | Ctrl+O, Ctrl+C, Ctrl+S | macOS ⌘O, ⌘C, ⌘S · Linux Ctrl+O, Ctrl+C, Ctrl+S (Qt 표준 키 `QKeySequence.Open` 등) |
| 설정·기록 위치 | `%LOCALAPPDATA%\GovParse Desktop` | macOS `~/Library/Application Support/GovParse Desktop`, Linux `$XDG_CONFIG_HOME/govparse-desktop` |
| 경로 비교 | 대소문자 무시 | macOS 기본은 대소문자 무시, Linux는 구분 |

## 7. 의도적으로 바꾸는 점

`formal/gui-spec/GuiSpec.lean`에서 테스트들이 한 화면 안에서 함께 성립하는지 Lean 4로 증명했습니다. 그 과정에서 나온 충돌은 아래 표의 C1·C2입니다.

Windows 앱 조사에서 나온 동작 중 아래는 그대로 옮기지 않고 고칩니다. 테스트는 고친 동작을 기준으로 합니다.

| Windows 앱 | PySide6 |
|---|---|
| 그림 목록 필터를 바꾸면 선택이 사라짐 | 선택한 그림이 새 필터에 남아 있으면 선택 유지 |
| 변환(HTML·HWPX로 변환)에 작업 번호 검사가 없음 | 다른 결과를 연 뒤 늦게 끝난 변환은 화면을 바꾸지 않음 |
| HTML 결과를 열 때마다 완료 처리기가 쌓임 | 한 번만 처리 |
| 1초마다 상태 막대를 "원본 미리보기 · p / n쪽"으로 덮어씀. 저장·내보내기·이전 문서 문구만 예외라, "원본과 같은 경로에는 저장할 수 없습니다" 같은 안내가 1초 안에 사라짐 | 자동 문구("여는 중", 쪽 표시)만 갱신하고, 사용자 동작에 대한 답은 다음 동작 전까지 유지 (formal/gui-spec C2) |
| 모든 쪽에 위치 미확인 안내를 그림 | 같음 (사용자 확인 전까지 유지) |
