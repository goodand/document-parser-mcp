/-
  GovParse Desktop GUI · coexistence of the PySide6 specification tests (qt/tests)

  Question: can ONE GUI pass all specification tests at the same time, or do two tests demand
  contradictory behavior?  Method: the decision rules the tests exercise are written once as
  functions (the model).  Each test expectation is a theorem about that single model.
  If every theorem is proved, the model is a witness that the tests can coexist.
  Where a test cannot hold together with others, the conflict is stated and proved as a theorem.

  The model follows the Windows app (Desktop.cs / FigurePanel.cs, 2026-11-25) except where
  qt/SPEC.md §7 deliberately changes it.  Lean 4.33 core only (proof-skills lean.sh, Docker).
-/

namespace GuiSpec

/-! ## Figures, filter, selection -/

inductive St | autoExplicit | userLinked | userShared | userNoCaption | userUndecided | needsReview
  deriving DecidableEq, Repr

/-- "설명 미확인" in the UI. -/
def St.unknown : St → Bool
  | .needsReview | .userUndecided => true
  | _ => false

structure Fig where
  id : Nat
  st : St
  verified : Bool
  page : Nat            -- 0-based page index of the verified location
  caption : List Nat    -- caption words after normalization (spaces and case removed); [] = no caption
  deriving DecidableEq, Repr

inductive Filter | all | unknownOnly | knownOnly
  deriving DecidableEq, Repr

def Filter.shows : Filter → Fig → Bool
  | .all, _ => true
  | .unknownOnly, f => f.st.unknown
  | .knownOnly, f => !f.st.unknown

inductive View | original | result
  deriving DecidableEq, Repr

structure S where
  dialog : Bool              -- a modal dialog is open (native disabled state, not Control.Enabled)
  panelOpen : Bool           -- figure panel expanded
  hash : Option Nat          -- figure panel document hash (none until loaded)
  view : View
  figs : List Fig
  filter : Filter
  query : Option Nat         -- caption search word (none = empty search box)
  sel : Option Nat           -- selected figure id
  page : Nat                 -- original view page index
  pages : Nat
  resultEnabled : Bool
  deriving Repr

/-- Caption search: an empty box hits everything; a word hits only figures whose caption has it. -/
def hits : Option Nat → Fig → Bool
  | none, _ => true
  | some w, f => f.caption.contains w

def visible (s : S) (f : Fig) : Bool := s.filter.shows f && hits s.query f

def listed (s : S) (id : Nat) : Option Fig :=
  (s.figs.filter (visible s)).find? (·.id == id)

/-- Typing in the search box: same selection rule as the filter. -/
def setQuery (s : S) (q : Option Nat) : S :=
  let s' := { s with query := q }
  match s.sel with
  | some id => if (listed s' id).isSome then s' else { s' with sel := none }
  | none => s'

/-- Saving a caption (worker reply refills the list): the figure's caption becomes `words`; the search stays. -/
def saveCaption (s : S) (id : Nat) (words : List Nat) : S :=
  { s with figs := s.figs.map fun f => if f.id == id then { f with caption := words, st := .userLinked } else f }

/-- SPEC.md §7: changing the filter keeps the selection when the figure is still listed. -/
def setFilter (s : S) (f : Filter) : S :=
  let s' := { s with filter := f }
  match s.sel with
  | some id => if (listed s' id).isSome then s' else { s' with sel := none }
  | none => s'

/-! ## MCP bridge (same codes as the Windows app, measured 2026-11-25) -/

inductive Req | state | original | result | pick (h id : Nat) | bogus

inductive Code
  | open_ | shown | selected | dialogOpen | viewUnavailable
  | panelUnavailable | mismatch | missing | unverified | invalid
  deriving DecidableEq, Repr

def handle (s : S) : Req → Code × S
  | .state => (.open_, s)
  | .original => if s.dialog then (.dialogOpen, s) else (.shown, { s with view := .original })
  | .result =>
      if s.dialog then (.dialogOpen, s)
      else if !s.resultEnabled then (.viewUnavailable, s)
      else (.shown, { s with view := .result })
  | .pick h id =>
      if s.dialog then (.dialogOpen, s)
      else if s.hash.isNone || !s.panelOpen then (.panelUnavailable, s)
      else if s.hash != some h then (.mismatch, s)
      else match listed s id with
        | none => (.missing, s)
        | some f =>
            if !f.verified then (.unverified, s)
            else (.selected, { s with sel := some id, view := .original, page := f.page })
  | .bogus => (.invalid, s)

/-- Reply field item_id: only on the original tab with the panel open. -/
def itemId (s : S) : Option Nat := if s.view == .original && s.panelOpen then s.sel else none

/-- Reply field page (1-based): only on the original tab with pages. -/
def pageNo (s : S) : Option Nat := if s.view == .original && s.pages > 0 then some (s.page + 1) else none

/-! ## Status bar refresh (1-second timer) -/

/-- What produced the current status-bar text. -/
inductive Msg
  | auto        -- "원본 미리보기 · p / n쪽", "여는 중 · …"
  | saved       -- "저장 완료 …", "저장을 취소했습니다 …"
  | export      -- "내보내기 실패 …", "내보내는 중 …", "내보내기를 취소했습니다 …"
  | prevDoc     -- "이전 문서 … 내보내기 완료"
  | event       -- any other answer to a user action, e.g. "원본과 같은 경로에는 저장할 수 없습니다 …"
  deriving DecidableEq, Repr

structure Bar where
  msg : Msg
  docOpen : Bool
  onOriginal : Bool
  pages : Nat
  previewError : Bool
  exporting : Bool
  deriving Repr

def refreshAllowed (b : Bar) : Bool :=
  b.onOriginal && b.docOpen && b.pages > 0 && !b.previewError && !b.exporting

/-- Windows rule (Desktop.Refresh2): overwrite unless the text starts with 저장 / 내보내기 / 이전 문서. -/
def tickWindows (b : Bar) : Bar :=
  if refreshAllowed b && !(b.msg == .saved || b.msg == .export || b.msg == .prevDoc)
  then { b with msg := .auto } else b

/-- Specification rule (SPEC.md §7): only automatic text is refreshed; an answer to a user action stays. -/
def tickSpec (b : Bar) : Bar :=
  if refreshAllowed b && b.msg == .auto then { b with msg := .auto } else b

/-! ## Export buttons and the job guard -/

inductive Kind | none | json | html | pages
  deriving DecidableEq, Repr

structure Ex where
  exporting : Bool
  kind : Kind          -- kind of the result shown
  job : Nat            -- current document job
  deriving Repr

def exportEnabled (e : Ex) : Bool := !e.exporting
def cancelVisible (e : Ex) : Bool := e.exporting
def convertEnabled (e : Ex) : Bool := !e.exporting && e.kind == .json
/-- A finished export (or, per SPEC.md §7, a finished convert) updates the result view only for its own job. -/
def showsResult (e : Ex) (startedJob : Nat) : Bool := startedJob == e.job

/-! ## Fixture of qt/tests/test_figures.py -/

/-- Words: 10 = "구성도", 11 = "사업", 20 = "직접", 30 = "흐름도", 99 = a word in no caption. -/
def figs : List Fig :=
  [ ⟨1, .autoExplicit, false, 0, [11, 10]⟩, ⟨2, .needsReview, false, 0, []⟩, ⟨3, .needsReview, true, 0, []⟩,
    ⟨4, .userLinked, true, 1, [20]⟩, ⟨5, .userUndecided, false, 0, []⟩, ⟨6, .userNoCaption, false, 0, []⟩ ]

/-- Panel open on the original tab, document loaded, user selected #1. -/
def s0 : S :=
  { dialog := false, panelOpen := true, hash := some 7, view := .original, figs := figs,
    filter := .all, query := none, sel := some 1, page := 0, pages := 3, resultEnabled := false }

def ids (f : Filter) : List Nat := (figs.filter f.shows).map (·.id)

/-! ## Part 1 · every test expectation holds in the one model (coexistence witness) -/

-- test_figures.py::test_filter
theorem test_filter_all : ids .all = [1, 2, 3, 4, 5, 6] := by decide
theorem test_filter_unknown : ids .unknownOnly = [2, 3, 5] := by decide
theorem test_filter_known : ids .knownOnly = [1, 4, 6] := by decide

-- test_figures.py::test_filter_keeps_selection_when_still_listed  (select #4, then known-only)
theorem test_filter_keeps_selection :
    (setFilter { s0 with sel := some 4 } .knownOnly).sel = some 4 := by decide
-- a figure that leaves the list is no longer selected
theorem filter_drops_hidden_selection :
    (setFilter { s0 with sel := some 3 } .knownOnly).sel = none := by decide

-- test_settings_bridge_platform.py::test_state_reports_user_selection
theorem test_state_reports_user_selection :
    let s := { s0 with sel := some 3 }
    (handle s .state).1 = .open_ ∧ itemId s = some 3 ∧ pageNo s = some 1 := by decide

-- ::test_show_reselects_and_jumps  (#1 selected, AI asks for #4 on page index 1)
theorem test_show_reselects_and_jumps :
    let r := handle s0 (.pick 7 4)
    r.1 = .selected ∧ itemId r.2 = some 4 ∧ pageNo r.2 = some 2 := by decide

-- ::test_rejections_keep_user_selection  (4 cases)
theorem test_reject_wrong_hash : let r := handle s0 (.pick 0 3); r.1 = .mismatch ∧ itemId r.2 = some 1 := by decide
theorem test_reject_unknown_id : let r := handle s0 (.pick 7 99); r.1 = .missing ∧ itemId r.2 = some 1 := by decide
theorem test_reject_unverified : let r := handle s0 (.pick 7 2); r.1 = .unverified ∧ itemId r.2 = some 1 := by decide
theorem test_reject_bogus : let r := handle s0 .bogus; r.1 = .invalid ∧ itemId r.2 = some 1 := by decide

-- ::test_show_with_panel_closed
theorem test_show_with_panel_closed :
    (handle { s0 with panelOpen := false } (.pick 7 3)).1 = .panelUnavailable := by decide

-- ::test_show_filtered_out_item
theorem test_show_filtered_out_item :
    (handle (setFilter s0 .knownOnly) (.pick 7 3)).1 = .missing := by decide

-- ::test_switch_views
theorem test_switch_views_no_result : (handle s0 .result).1 = .viewUnavailable := by decide
theorem test_switch_views_with_result :
    let r := handle { s0 with resultEnabled := true } .result
    r.1 = .shown ∧ r.2.view = .result := by decide

-- ::test_show_from_result_tab  (corrected, see Part 2 · conflict C1)
theorem test_show_from_result_tab :
    let r := handle { s0 with view := .result, resultEnabled := true } (.pick 7 3)
    r.1 = .selected ∧ r.2.view = .original ∧ itemId r.2 = some 3 := by decide

-- ::test_modal_dialog_blocks_bridge
theorem test_modal_dialog_blocks_bridge :
    (handle { s0 with dialog := true } .original).1 = .dialogOpen ∧
    (handle { s0 with dialog := true } (.pick 7 3)).1 = .dialogOpen := by decide

-- ::test_state_without_document
theorem test_state_without_document :
    let s := { s0 with hash := none, panelOpen := false, sel := none, pages := 0 }
    (handle s .state).1 = .open_ ∧ itemId s = none ∧ pageNo s = none := by decide

-- test_export.py: running export state, second export ignored, convert buttons only for json
theorem test_running_export_state :
    let e : Ex := ⟨true, .json, 1⟩
    exportEnabled e = false ∧ cancelVisible e = true ∧ convertEnabled e = false := by decide
theorem test_convert_buttons_only_for_json :
    convertEnabled ⟨false, .json, 1⟩ = true ∧ convertEnabled ⟨false, .html, 1⟩ = false := by decide
-- ::test_export_of_previous_document_does_not_replace_view  and  ::test_late_convert_does_not_replace_newer_result
theorem test_previous_job_does_not_show : showsResult ⟨false, .json, 2⟩ 1 = false := by decide
theorem same_job_shows : showsResult ⟨false, .json, 2⟩ 2 = true := by decide

-- Status bar, with the specification rule:
-- test_main_window.py::test_status_shows_page_after_open  ("여는 중" is replaced by the page line)
theorem test_status_shows_page_after_open :
    (tickSpec ⟨.auto, true, true, 3, false, false⟩).msg = .auto := by decide
-- test_export.py::test_refuse_saving_over_source  (the refusal stays on screen)
theorem test_refuse_message_stays :
    (tickSpec ⟨.event, true, true, 3, false, false⟩).msg = .event := by decide
-- test_export.py::test_json_export_publishes_zips_and_reports / test_cancel_export / test_export_failure_message
theorem test_export_messages_stay :
    (tickSpec ⟨.saved, true, true, 3, false, false⟩).msg = .saved ∧
    (tickSpec ⟨.export, true, true, 3, false, false⟩).msg = .export ∧
    (tickSpec ⟨.prevDoc, true, true, 3, false, false⟩).msg = .prevDoc := by decide

-- test_caption_search.py
def shownIds (s : S) : List Nat := (s.figs.filter (visible s)).map (·.id)
theorem test_search_by_caption : shownIds (setQuery s0 (some 10)) = [1] := by decide
theorem test_no_match : shownIds (setQuery s0 (some 99)) = [] := by decide
theorem test_clearing_search_shows_all : shownIds (setQuery (setQuery s0 (some 10)) none) = [1, 2, 3, 4, 5, 6] := by decide
theorem test_search_and_filter_combine :
    shownIds (setQuery (setFilter s0 .unknownOnly) (some 10)) = [] ∧
    shownIds (setFilter (setQuery (setFilter s0 .unknownOnly) (some 10)) .all) = [1] := by decide
theorem test_search_keeps_selection : (setQuery { s0 with sel := some 4 } (some 20)).sel = some 4 := by decide
theorem test_search_drops_hidden_selection : (setQuery { s0 with sel := some 4 } (some 10)).sel = none := by decide
theorem test_bridge_respects_search : (handle (setQuery s0 (some 10)) (.pick 7 3)).1 = .missing := by decide
theorem test_newly_saved_caption_is_searchable :
    shownIds (setQuery s0 (some 30)) = [] ∧
    shownIds (saveCaption (setQuery s0 (some 30)) 2 [30]) = [2] := by decide
-- the search survives the refill that follows a save (the query field is untouched)
theorem test_search_survives_refresh (s : S) (id : Nat) (w : List Nat) : (saveCaption s id w).query = s.query := rfl

/-! ## Part 2 · conflicts found -/

/-- C1. qt test `test_show_from_result_tab` first expected `figure_panel_unavailable` with the panel
    still open.  The Windows handler (and this model) selects the figure and returns to the original
    tab, which `test_show_reselects_and_jumps` also relies on.  The original expectation is false in the
    model; the test was corrected to the statement proved in Part 1. -/
theorem C1_old_expectation_false :
    (handle { s0 with view := .result, resultEnabled := true } (.pick 7 3)).1 ≠ .panelUnavailable := by decide

/-- C2. With the Windows refresh rule, an answer to a user action that is not a 저장/내보내기/이전 문서
    message is replaced by the page line on the next 1-second tick, while a document is open.
    So `test_refuse_saving_over_source` (message must be shown) and `test_status_shows_page_after_open`
    (page line must appear) cannot both hold under the Windows rule: the refusal disappears after ≤1 s. -/
theorem C2_windows_rule_erases_refusal :
    (tickWindows ⟨.event, true, true, 3, false, false⟩).msg = .auto := by decide

/-! ## Part 3 · general properties (all states, not only the fixture) -/

/-- A request that is not answered `selected` or `shown` changes nothing: user selection, tab and page are kept. -/
theorem rejection_changes_nothing (s : S) (r : Req)
    (h1 : (handle s r).1 ≠ .selected) (h2 : (handle s r).1 ≠ .shown) : (handle s r).2 = s := by
  cases r with
  | state => rfl
  | original => simp only [handle] at *; split <;> simp_all
  | result => simp only [handle] at *; split <;> (try split) <;> simp_all
  | bogus => rfl
  | pick h id =>
    simp only [handle] at *
    split
    · rfl
    · split
      · rfl
      · split
        · rfl
        · split
          · rfl
          · split <;> simp_all

/-- While a modal dialog is open, no request other than `state` / invalid input acts. -/
theorem dialog_blocks (s : S) (hd : s.dialog = true) :
    (handle s .original).1 = .dialogOpen ∧ (handle s .result).1 = .dialogOpen ∧
    ∀ h id, (handle s (.pick h id)).1 = .dialogOpen := by
  simp [handle, hd]

/-- `selected` only ever selects a listed, location-verified figure of the same document, on the original tab. -/
theorem selected_is_sound (s : S) (h id : Nat) (hs : (handle s (.pick h id)).1 = .selected) :
    ∃ f, listed s id = some f ∧ f.verified = true ∧ s.hash = some h ∧
      (handle s (.pick h id)).2.sel = some id ∧ (handle s (.pick h id)).2.view = .original := by
  simp only [handle] at *
  split at hs
  · contradiction
  · split at hs
    · contradiction
    · split at hs
      · contradiction
      · rename_i hpanel hhash
        split at hs
        · contradiction
        · rename_i f hf
          split at hs
          · contradiction
          · rename_i hv
            refine ⟨f, hf, ?_, ?_, ?_, ?_⟩
            · simpa using hv
            · simp at hpanel hhash
              cases hh : s.hash with
              | none => simp [hh] at hpanel
              | some v => simp [hh] at hhash; simp [hhash]
            · simp_all
            · simp_all

/-- A figure without a caption is never found by a non-empty search. -/
theorem no_caption_never_matches (f : Fig) (w : Nat) (h : f.caption = []) : hits (some w) f = false := by
  simp [hits, h]

/-- Search and filter only hide: every figure listed under both is listed under each alone. -/
theorem search_only_narrows (s : S) (q : Nat) (f : Fig) (h : visible { s with query := some q } f = true) :
    visible { s with query := none } f = true := by
  simp [visible, hits] at *
  exact h.1

/-- The specification refresh rule never erases a message the user has not yet seen replaced by an action. -/
theorem spec_keeps_non_auto (b : Bar) (h : b.msg ≠ .auto) : (tickSpec b).msg = b.msg := by
  simp [tickSpec, h]

/-- … and still shows the page line whenever the Windows rule would have, for automatic text. -/
theorem spec_agrees_on_auto (b : Bar) (h : b.msg = .auto) : (tickSpec b).msg = (tickWindows b).msg := by
  simp [tickSpec, tickWindows, h]

/-- Export and cancel are never offered at the same time, and convert is never offered during an export. -/
theorem export_buttons_exclusive (e : Ex) :
    exportEnabled e = !cancelVisible e ∧ (e.exporting = true → convertEnabled e = false) := by
  cases e with
  | mk ex k j => cases ex <;> simp [exportEnabled, cancelVisible, convertEnabled]

end GuiSpec
