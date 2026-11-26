# GovParse Desktop · 작업 인계 (2026-11-26)

다른 PC에서 이 브랜치(`desktop/windows-table-edit`)를 받아 이어서 작업하기 위한 문서입니다. 지금 우선순위는 **Windows 데스크톱 앱 완성과 납품**이고, `main` 병합은 그 뒤입니다.

## 1. 이 브랜치에 들어 있는 것
- Windows C# 앱(먼저 반영)과 PySide6 화면의 **표 고치기** 모드. 사양은 `qt/SPEC.md` 8절.
  - 문서에서 읽은 표를 칸 단위로 고치기, 행·열 추가와 삭제, 안쪽 표 열기, 되돌리기, 저장.
  - 그림으로만 남은 표는 **이미지에서 표 읽기**(RapidTable SLANetPlus, 선택 시 RapidOCR).
- 엔진: `src/govparse/table_edit.py`(원본 칸 → 편집 후보), `src/govparse/image_table.py`와 `image_table_ocr.py`(그림 → 표 격자).
- 워커 모드: `json`(표마다 후보 생성), `index`, `image-table`, `table-save`. 저장 규칙은 `gpdesk/table_edit.py` 한 곳에 있고 두 화면이 함께 씁니다.
- 설정 창: 미리보기 방식(기본 rhwp 또는 한컴 PDF), 프로그램 제거.
- 설치기 0.3.0: 제거 시 앱이 닫히기를 기다리고 `uninstall.exe`도 스스로 지웁니다.
- 설명서 `manual.html`: 설치(설치 파일 방식), 표 고치기, 이미지에서 표 읽기, 미리보기 방식, 프로그램 제거.

## 2. 다른 PC에서 준비할 것
1. 저장소를 받고 이 브랜치를 체크아웃합니다.
2. `govparse-desktop`에서 `uv sync`로 가상환경을 만듭니다. `pyproject.toml`에 PySide6와 pytest-qt가 있습니다. 표 그림 읽기까지 빌드하려면 `rapid-table==3.0.2`도 그 환경에 있어야 합니다.
3. **모델 가중치는 저장소에 없습니다.** 빌드가 SHA-256으로 확인합니다.
   - OCR 3개: RapidOCR 3.10.0 기본 모델. 위치는 `config.json`의 `ocr_models` 또는 LM Studio 설정에서 찾습니다. 파일 이름과 해시는 `build.py`의 `OCR_MODELS`에 있습니다.
   - 표 구조 1개: `models/table-structure/slanet-plus.onnx`(저장소 루트 기준), SHA-256 `d57a942af6a2f57d6a4a0372573c696a2379bf5857c45e2ac69993f3b334514b`. 받는 곳: `https://www.modelscope.cn/models/RapidAI/RapidTable/resolve/v2.0.0/slanet-plus.onnx`.
   - 표 구조 모델이나 `rapid-table`이 없으면 빌드는 그대로 되고, 앱에서 "이미지에서 표 읽기" 단추만 숨습니다.

## 3. 확인 명령
```
cd govparse-desktop
python -m pytest tests -m "not live" -q        # 2026-11-26: 124 통과, 2 건너뜀
python -m pytest qt/tests -q                    # 242 통과, 5 건너뜀 (Mac·Linux 전용)
cd .. && python -m pytest tests/test_table_edit.py tests/test_table_identity.py tests/test_image_table_ocr.py -q   # 11 + 4 통과
cd govparse-desktop && python build.py          # build/app, dist/GovParse-Desktop-Setup-x64.exe
```

## 4. 확인된 것과 남은 것
확인됨(이 브랜치, Windows):
- 위 테스트 수치, C# 앱과 설치기 컴파일.
- `cd7f66e` 시점에 빌드한 앱만으로 합성 문서를 내보내고, 표 고치기의 편집·저장·되돌리기·안쪽 표 열기를 화면 밖 시험으로 확인.
- 이미지 표 읽기는 실제 모델로 실행해 봄: 구조만 약 0.9초, OCR까지 약 5초. 합성 2×2 표를 5×2로 읽은 사례가 있어, 결과는 사람이 확인해야 합니다.

남음:
- [ ] 이 커밋으로 다시 빌드해 설치본에서 이미지 표 읽기가 실제로 도는지 확인 (모델·패키지 동봉 경로는 아직 빌드로 검증하지 않음).
- [ ] 큰 실문서 전체를 내보낸 결과에서 표 고치기와 재열기 확인.
- [ ] 설치 → 실행 → 제거를 최종 설치 파일로 다시 시험, `install-test-checklist.html`을 PDF로 다시 만들기(설치 파일 크기와 SHA-256 갱신, 표 고치기 단계 추가).
- [ ] 표를 고친 뒤 압축 파일(.zip)을 다시 만드는 기능. 지금은 내보낼 때 한 번만 만듭니다.
- [ ] 본문 그림(표 이미지가 아닌 그림)과 여러 쪽에 걸친 표의 이미지 읽기.
- [ ] 미리보기 겹침: rhwp가 표 칸 안의 그림 다음 표를 겹쳐 그립니다. 한컴 미리보기 설정으로 우회 중이고, rhwp 수정본이 필요합니다.
- [ ] SLANetPlus 가중치의 재배포 조건: RapidTable(코드)과 원 출처 PaddleX는 Apache-2.0이고 별도 제한은 찾지 못했지만, 모델 배포 페이지(ModelScope)는 확인하지 못했습니다. 외부 배포 전에 확인하세요.
- [ ] Mac 빌드와 실행, Lean 증명(`formal/gui-spec`)에 표 고치기 반영.

## 5. 넣지 않는 것
실제 업무 문서와 그 결과물, 한컴 출력 PDF·PNG, 모델 가중치, `config.json`, `build/`, `dist/`, `evidence/`.
