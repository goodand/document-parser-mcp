# PySide6 화면 사양 테스트 검토표

`qt/tests/`의 테스트 174개(함수 139개, 일부는 여러 경우로 나뉨)를 화면 영역별로 정리했습니다. 각 줄은 "무엇을 하면, 무엇이 보여야 하는가"입니다.

**기준 칸**
- **W**: 지금 Windows 앱의 동작을 그대로 옮김
- **변경**: 일부러 바꿈 (`SPEC.md` 7절)
- **새로**: Mac·Linux 이전 때문에 새로 정함

검토하실 때는 세 가지를 봐 주시면 됩니다.
1. 기대값이 틀렸거나 바라는 동작과 다른 줄
2. 4절 "아직 테스트가 없는 동작" 중 꼭 넣어야 할 것
3. 5절 "결정이 필요한 점"

---

## 1. 메인 창 (`test_main_window.py`, 51개)

| 테스트 | 확인 내용 | 기준 |
|---|---|---|
| title_and_minimum_size | 창 제목 `GovParse Desktop`, 최소 크기 560×420 | W |
| initial_state | 시작 시 결과 보기 꺼짐, 원본 보기 선택, 취소 버튼·그림 목록 숨김, 가운데 `HWP 또는 HWPX 파일을 여기에 놓으세요`와 `파일 열기`, 쪽 `– / –`, 이전·다음·확대 꺼짐, 처리 상태 줄 초기 문구 | W |
| start_status_message | 아래 상태 막대 `Ctrl+O 또는 끌어다 놓기로 HWP/HWPX 문서를 여세요.` (흐린 색) | W |
| top_bar_order | 위쪽 막대 순서: 원본 보기, 결과 보기, 파일 열기, 결과 불러오기, 설정, 설명서, 파일 이름 | W |
| initial_result_view_message | 결과가 없을 때 결과 탭 안내 `아직 결과가 없습니다…` | W |
| open_button_uses_dialog | 파일 열기 → 대화상자 → 미리보기·구조 읽기 시작, 파일 이름 표시, 창 제목 `GovParse Desktop · 파일명` | W |
| open_dialog_cancel_changes_nothing | 대화상자에서 취소하면 아무 작업도, 메시지도 없음 | W |
| ctrl_o_opens_dialog | Ctrl+O(Mac ⌘O)로 파일 열기 대화상자 | W, 새로(⌘) |
| center_button_opens_dialog | 가운데 `파일 열기` 버튼도 같은 대화상자 | W |
| extension_check_is_case_insensitive (3) | `.HWPX`, `.hwp`, `.Hwp` 모두 열림 | W |
| other_files_are_refused (3) | `.pdf`, `.docx`, 확장자 없음 → `HWP 또는 HWPX 파일만 열 수 있습니다: 이름` (경고색), 워커 호출 없음 | W |
| open_runs_preview_page0_and_summary | 열면 1쪽 미리보기와 구조 요약을 워커에 요청 | W |
| open_shows_structure_counts | `구조 읽기 완료 … 문단 16 · 표 3 (중첩 1) · 그림 6` (초록) | W |
| structure_from_xml_route_is_named | rhwp가 못 열면 끝에 `원본 XML에서 직접 읽음` | W |
| structure_failure | `구조: 읽기 실패 · 사유` (경고색) | W |
| open_switches_to_original_tab | 결과 탭에서 문서를 열면 원본 탭으로 돌아옴 | W |
| drop_one_file_opens_it | 파일 하나를 끌어다 놓으면 열림 | W |
| drop_two_files_is_refused | 두 개를 놓으면 `파일을 하나만 놓아 주세요. (2개를 놓았습니다)` | W |
| korean_and_space_paths | 한글·공백 경로가 그대로 워커에 전달됨 | W |
| page_label_and_buttons | 3쪽 문서: `1 / 3`, 이전 꺼짐, 다음 켜짐 | W |
| next_and_prev | 다음·이전으로 쪽 이동, 마지막 쪽에서 다음 꺼짐 | W |
| visited_pages_are_cached | 한 번 본 쪽은 다시 그리지 않음 | W |
| preview_temp_png_is_deleted | 미리보기 임시 그림 파일은 읽은 뒤 지움 | W |
| preview_failure_offers_retry | 미리보기 실패 → `미리보기 실패 · 사유` + `내보내기는 계속할 수 있습니다.`, `다시 시도` 버튼, 상태 줄 `미리보기: 실패` | W |
| retry_after_failure | 다시 시도로 회복 | W |
| stale_preview_does_not_overwrite_new_document | 앞 문서의 늦은 미리보기가 새 문서 화면을 덮지 않음 | W |
| zoom_options (5) | 폭 맞춤, 쪽 맞춤, 100%, 150%, 200% → 버튼에 `라벨 ▾` | W |
| zoom_is_per_view | 확대 설정은 원본·결과 탭이 따로 기억 | W |
| figures_button_only_on_original_tab | `그림 목록` 버튼은 원본 탭에서만 보임 | W |
| busy_bar_while_structure_runs | 구조 읽는 동안 `구조 읽는 중 · N초 경과`와 진행 막대 | W |
| preview_done_line | `미리보기: 완료 · 3쪽` | W |
| hancom_pdf_route_is_named | 한컴 PDF로 그렸으면 `(한컴 PDF)`와 `· 한컴 PDF로 표시` | W |
| status_shows_page_after_open | 열고 나면 상태 막대 `원본 미리보기 · 1 / 3쪽` | W |
| elapsed_format (4) | 경과 시간 `5초`, `59초`, `1분 00초`, `2분 05초` | W |
| progress_text (3) | `표 이미지 10/40쪽 (25%)`, 천 단위 쉼표, 단계 설명 `쪽 그리기 (한컴 PDF 재사용)` | W |
| remaining_time_appears_after_five_steps | 5단계 이상 진행 후 `남은 시간 약 N분`, 거의 끝나면 `곧 끝남` | W |
| manual_opens_with_default_app | 설명서는 기본 앱(브라우저)으로 열림 | W |
| close_stops_worker_processes | 창을 닫으면 앱이 띄운 워커를 모두 끝냄 | W |

## 2. 내보내기·변환 (`test_export.py`, 20개)

| 테스트 | 확인 내용 | 기준 |
|---|---|---|
| format_options | 형식 4개: JSON + PNG, HTML, 한글 문서 · HWPX, 한글 문서 · HWP. 처음 선택은 JSON + PNG | W |
| hwp_disabled_without_hancom | 한컴이 없으면 HWP 항목이 꺼지고 설명 `이 PC에서 한컴오피스를 찾지 못해 사용할 수 없음` | W (Mac·Linux는 항상 이 상태) |
| export_without_document | 문서 없이 내보내기 → `먼저 문서를 여세요.` | W |
| save_dialog_defaults | 기본 이름: JSON은 `문서명-json`, HWPX는 `문서명-복원.hwpx` | W |
| save_dialog_cancel | 저장 취소 → `저장을 취소했습니다. 문서와 형식 선택은 그대로입니다.` | W |
| refuse_saving_over_source | 원본과 같은 경로 → 거부 문구. **1.5초 뒤에도 문구가 남아 있음** | W, 변경(C2) |
| json_export_publishes_zips_and_reports | 결과 폴더 생성, 임시 폴더 없음, zip 생성, 상태 `… 압축 파일 이름.zip (2.1MB) … — ‘결과 보기’에서 확인`, 결과 탭 켜지지만 **탭은 바꾸지 않음** | W |
| existing_target_gets_new_name | 같은 이름이 있으면 `이름 (2)`로 저장, 기존 파일은 그대로 | W |
| hwpx_export_moves_single_file | HWPX는 파일 하나로 저장, zip 없음 | W |
| running_export_state_and_progress | 내보내는 중: 내보내기 꺼짐, `취소 (Esc)` 보임, 진행률 표시, 끝나면 원래대로 | W |
| second_export_ignored_while_running | 내보내는 중 두 번째 요청은 무시 | W |
| cancel_export (2) | 취소 버튼과 Esc 모두: `내보내기를 취소했습니다…`, 결과·임시 폴더 없음 | W |
| export_failure_message | `내보내기 실패 · 문서명 · 사유` (경고색) | W |
| zip_failure_keeps_export | zip 실패해도 결과 폴더는 저장, 문구 `압축 실패: 사유 (결과 폴더는 저장됨)` | W |
| export_of_previous_document_does_not_replace_view | 내보내는 중 다른 문서를 열면 끝났을 때 `이전 문서 …의 내보내기 완료 … (현재 문서 화면은 바꾸지 않았습니다)` | W |
| convert_from_json_result (2) | JSON 결과에서 HTML로·HWPX로 변환, 기본 이름 `폴더명-html`, `폴더명-변환.hwpx` | W |
| convert_buttons_only_for_json | HTML 결과에는 변환 버튼 없음 | W |
| late_convert_does_not_replace_newer_result | 변환 중 다른 결과를 열면 늦게 끝난 변환이 화면을 바꾸지 않음 | 변경 |

## 3. 결과 보기 (`test_result_view.py`, 31개)

| 테스트 | 확인 내용 | 기준 |
|---|---|---|
| load_result_dialog_and_tab | 결과 불러오기 → `기존 결과를 불러왔습니다 · 경로`, 결과 탭 켜지고 이동 | W |
| load_result_failure | `결과를 불러오지 못했습니다 · 사유` | W |
| title_has_source_name | 제목 `원본파일명 → document.json` | W (오늘 추가) |
| asset_columns_and_rows | 칸 `자산`, `출처`, `위치 / 설명`, `원본 파일`. 내장 그림·표 이미지 줄, 조각 표 `1/2쪽 조각` | W |
| page_capture_rows_say_table_unknown | 예전 쪽 캡처 결과: `미리보기 12쪽의 표 영역 (어느 표인지는 확인되지 않음)` | W |
| embedded_without_text | 설명 없는 내장 그림: `문서에 들어 있던 그림` | W |
| tables_without_image_are_listed_with_reason | 미생성 표가 `(이미지 없음) / 표 이미지 미생성 / 이름 · 사유`로 목록 끝에, 메모에 `표 3개 중 2개 …` | W (오늘 추가) |
| selecting_missing_table_row_clears_picture | 미생성 줄을 고르면 그림 비우고 OCR 버튼 꺼짐 | W |
| integrity_note (3) | 검증 목록 없음, 변경 없음 `(파일 6개 해시 확인)`, 바뀐·없어진 파일 수 | W |
| state_line_and_first_row_selected | `파일 저장: 성공` / `미리보기 성공 · 자산 3개`, 첫 줄 자동 선택 | W |
| no_assets_state | 자산 없음 → `(추출된 PNG 0개)` | W |
| small_picture_shown_at_natural_size | 작은 그림은 늘리지 않고 원래 크기 | W |
| buttons_by_kind | JSON 결과: `JSON 열기`, `압축 파일 보기`, 변환 버튼 2개 | W |
| zip_button_hidden_without_zip | zip이 없으면 압축 파일 보기 숨김 | W |
| open_folder_and_zip_reveal_files | 폴더 열기·압축 파일 보기 → 파일 관리자에서 해당 파일 선택 | W, 새로(Finder 등) |
| open_external_uses_default_app | JSON 열기 → 기본 앱 | W |
| html_result | HTML 결과: `브라우저에서 열기`, `미리보기 성공 · 저장된 로컬 HTML을 표시`, 자산 목록 없음 | W |
| hwpx_result_pages | HWPX 결과: `한글에서 열기`, 쪽 단위 미리보기, 쪽 도구가 결과를 따라감 | W |
| copy_selected_asset_with_ctrl_c | Ctrl+C로 그림 복사(이미지 + 파일), `그림을 복사했습니다 (120×80)…` | W |
| right_click_selects_row_then_copy | 우클릭하면 그 줄이 선택되고 `그림 복사` 메뉴 | W |
| copy_missing_file | 파일 없는 그림 → `이 그림은 원본 파일이 없어 복사할 수 없습니다.` | W |
| transparent_picture_copied_on_white | 투명 배경은 흰색으로 복사 | W |
| ocr_initial_state | `OCR 미실행`, OCR 버튼 켜짐, 복사 꺼짐 | W |
| ocr_run_and_copy | OCR → `OCR 결과 2줄 (그림 안 글자 · 설명 아님) · 엔진 이름`, 텍스트 복사 | W |
| ocr_outcomes (3) | 빈 결과, 사용 불가, 실패 문구 | W |
| ocr_running_disables_button | `OCR 실행 중…`, 버튼 꺼짐 | W |
| late_ocr_does_not_overwrite_other_selection | 다른 그림으로 옮긴 뒤 늦게 온 OCR은 표시하지 않고, 원래 그림으로 돌아가면 보여 줌 | W |

## 4. 그림 목록·설명 편집 (`test_figures.py`, 44개)

| 테스트 | 확인 내용 | 기준 |
|---|---|---|
| open_panel_sends_open | 그림 목록을 열면 그림 검토 세션에 문서 열기 요청 | W |
| summary_text | `그림 6개` / `설명이 확인된 그림 2개 · 미확인 4개` / `원본에서 위치를 표시할 수 있는 그림 2개` | W |
| columns_and_groups | 칸 `그림`, `설명 여부`, `설명`, `문서 안 위치`, `원본 유형`, 위치별 묶음 | W |
| status_column (5) | 설명 있음, 설명 미확인, 설명 지정함, 결정 불가, 설명 없는 그림 | W |
| location_column (4) | `후보 1쪽 · …`, `후보 1·2·3·4…쪽`, `1쪽 · `, `쪽 미확인 · ` | W |
| unknown_rows_are_warn_colored | 설명 미확인 줄은 경고색 | W |
| filter (3) | 모든 그림 / 설명 미확인만 / 설명 있는 그림만 | W |
| numbering_counts_all_figures | 필터해도 `#번호`는 전체 기준 | W |
| filter_keeps_selection_when_still_listed | 필터를 바꿔도 선택한 그림이 남아 있으면 선택 유지 | 변경 |
| detail_for_auto_caption | 상세: `설명: … (문서에 적힌 캡션)`, `그림 앞 글:`, 위치 미확인 안내 | W |
| detail_for_no_caption | `설명: 없음 (설명 없는 그림으로 표시함)` | W |
| verified_figure_jumps_to_its_page_and_boxes_it | 위치 확인된 그림을 고르면 그 쪽으로 이동, 원본에 `그림` 상자와 `설명` 점선 상자 | W |
| unverified_figure_shows_notice_on_every_page | 위치 미확인 그림은 모든 쪽에 안내 문구 | W |
| verified_figure_other_page_draws_nothing | 다른 쪽에서는 상자를 그리지 않음 | W |
| copy_figure_ctrl_c | 그림 목록 Ctrl+C 복사 | W |
| copy_without_selection | 선택 없이 복사 → `먼저 목록에서 그림을 선택하세요.` | W |
| copy_figure_without_file | 파일 없는 그림 복사 거부 문구 | W |
| edit_mode_groups | `그림 설명 편집` → ①②③ 묶음 제목, 버튼 `설명 저장 (Ctrl+S)`, `설명 삭제`, `설명 되돌리기`, `설명 칸에 넣기` | W |
| ocr_to_caption_hidden_in_view_mode | 보기 모드에서는 `설명 칸에 넣기` 숨김 | W |
| save_without_selection / save_empty_caption | 선택 없음, 빈 설명 안내 문구 | W |
| save_typed_caption_with_ctrl_s | Ctrl+S로 직접 쓴 설명 저장 (출처 typed) | W |
| ctrl_s_only_in_edit_mode | 보기 모드에서는 Ctrl+S 무시 | W |
| double_click_sentence_fills_caption | 주변 문장 더블클릭 → 설명 칸에 들어가고 안내, 저장은 안 함 | W |
| saving_picked_sentence_is_a_link | 문장 그대로 저장하면 "원문 연결"로 저장 | W |
| edited_picked_sentence_is_typed | 문장을 고쳐서 저장하면 직접 쓴 설명 | W |
| pick_kinds_are_marked | 파일 이름 문장은 `(파일 이름)` 표시 | W |
| ocr_text_into_caption | OCR → 설명 칸에 넣기 → 저장 (출처 ocr) | W |
| ocr_to_caption_before_ocr | OCR 전에 누르면 `먼저 ‘이 그림 OCR’을 눌러 글자를 읽으세요.` | W |
| delete_and_undo | 설명 삭제, 설명 되돌리기 | W |
| multi_select_save_applies_to_all | 여러 그림 선택 후 저장하면 모두에 적용 | W |
| selection_kept_after_save | 저장 후에도 같은 그림 선택 유지 | W |
| new_document_clears_editor | 새 문서를 열면 설명 칸 비움 | W |
| serve_failure_message | 그림 검토 실패 → `실패 · 사유` | W |
| hwp_panel_explains_conversion | HWP는 `HWP는 한컴으로 HWPX 변환본을 만든 뒤…` 안내 | W |

## 5. 설정·MCP 통로·OS 차이 (`test_settings_bridge_platform.py`, 24개)

| 테스트 | 확인 내용 | 기준 |
|---|---|---|
| settings_lists_engines | 엔진 목록, 쓸 수 없는 엔진은 꺼지고 `사용할 수 없음 · 사유`, `설정 파일: 경로` | W |
| settings_save / save_failure / read_failure | 저장 요청, 저장 실패 문구, 읽기 실패 시 저장 꺼짐 | W |
| settings_escape_cancels | Esc로 닫으면 저장 안 함 | W |
| settings_saved_message | `OCR 엔진을 바꿨습니다 · 엔진 이름` | W |
| state_without_document / state_reports_user_selection | MCP 상태: 문서 없음, 선택한 그림의 해시·ID·쪽 | W (실측) |
| show_reselects_and_jumps | MCP로 그림 지정 → 선택·쪽 이동·원본 상자 | W (실측) |
| rejections_keep_user_selection (4) | 틀린 해시, 없는 ID, 위치 미확인, 잘못된 요청 → 사용자 선택 유지 | W (실측) |
| show_with_panel_closed / show_filtered_out_item | 그림 목록 닫힘, 필터로 숨긴 그림 | W |
| switch_views | 결과 없으면 `view_unavailable`, 있으면 탭 전환 | W (실측) |
| show_from_result_tab | 결과 탭에서 그림 지정 → 선택하고 원본 탭으로 | W (C1) |
| modal_dialog_blocks_bridge | 대화상자가 열려 있으면 `dialog_open` | W (오늘 고친 결함) |
| bridge_transport_is_private | Windows는 사용자 전용 named pipe, Mac·Linux는 권한 0600 소켓 | 새로 |
| bridge_round_trip_over_transport | 실제 통로로 왕복 | 새로 |
| settings_folder_per_os | 설정 위치: Windows `%LOCALAPPDATA%`, Mac `~/Library/Application Support`, Linux `~/.config` | 새로 |
| hancom_option_follows_platform | 한컴이 있는 PC에서는 HWP 항목 켜짐 | W |
| same_path_guard_respects_case_sensitivity | Linux는 대소문자가 다른 경로를 다른 파일로 봄 | 새로 |
| real_platform_reveal_command | 폴더 열기: 탐색기 `/select`, Finder `open -R`, Linux `xdg-open` | 새로 |

## 6. 실제 워커로 끝까지 (`test_integration.py`, 4개)

| 테스트 | 확인 내용 |
|---|---|
| open_synthetic_figures | 합성 문서를 열어 `표 3 (중첩 1)`, `그림 6`, `1 / 1` |
| json_export_then_result_view | JSON+PNG 내보내기 → 표 이미지 3개, zip, 결과 목록 7줄 |
| moved_result_reopens | 35표 결과를 한글·공백 폴더로 옮겨도 다시 열림, 해시 변경 없음, 35줄 |
| hwp_option_disabled_without_hancom | 한컴 없는 환경에서 HWP 항목 꺼짐 |

---

## 7. 아직 테스트가 없는 동작

Windows 화면 조사(동작 144개)와 대조해서 빠진 것입니다. 넣을지 정해 주세요.

| 동작 | 비고 |
|---|---|
| 형식 메뉴 키보드 조작 (↑↓로 열기, Enter 선택, Esc 닫기, 포커스 복귀) | 키보드만 쓰는 사용자 |
| 폭 맞춤·쪽 맞춤의 실제 크기 계산 | 화면 크기 의존이라 값보다 "가로 스크롤 없음" 정도로 검사 가능 |
| 결과 쪽 미리보기 실패 시 `다시 시도` | 원본 쪽만 테스트함 |
| 그림 목록 위·아래 비율 (보기 50%, 편집 30%) | 배치 |
| 편집 모드 상세의 `— 고급 — 판정 근거` 줄 | 개발자용 정보 |
| 그림 목록을 닫은 채 새 문서를 열 때의 초기화 | |
| 작업별 제한 시간 (미리보기 15분, 내보내기 3시간 등)과 `처리 제한 시간을 초과했습니다.` | |
| 실패·취소 시 앱이 띄운 한컴 자동화 창만 닫기 | Windows 전용 |
| `jobs.log` 기록 | 사용자에게 안 보임 |
| 글꼴 선택 (KoPubWorld돋움체 → Pretendard → 맑은 고딕) | Mac은 Apple SD Gothic Neo 필요 |
| 고해상도 화면 배율 | Qt가 처리 |

## 8. 결정이 필요한 점

| # | 질문 | 지금 테스트 |
|---|---|---|
| 1 | 내보내기가 끝나면 결과 탭으로 **자동 이동**할까요? 지금은 이동하지 않고 "‘결과 보기’에서 확인"이라고만 씁니다. 처음 흐름 설명 때 헷갈리셨던 부분입니다. | 이동 안 함 (W) |
| 2 | 진행률 단위가 표 이미지·압축 단계에서도 `쪽`으로 나옵니다 (`표 이미지 10/40쪽`). 단계에 맞게 `표`, `파일`로 바꿀까요? | `쪽` (W) |
| 3 | 결과 탭은 한 번 켜지면 다른 문서를 열어도 계속 켜져 있고, 이전 결과가 남습니다. 새 문서를 열면 결과 탭을 비울까요? | 유지 (W) |
| 4 | Mac 단축키: Windows의 Ctrl+O/C/S를 Mac에서는 ⌘O/C/S로 두었습니다. 맞나요? | ⌘ |
| 5 | HWP 형식은 Mac·Linux에서 목록에 **꺼진 채 보이게** 했습니다. 아예 숨길까요? | 꺼진 채 보임 |
| 6 | 7절에서 꼭 추가할 항목 | – |
