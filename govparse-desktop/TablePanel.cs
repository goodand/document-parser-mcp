using System;
using System.Collections;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

// 표 고치기 (qt/SPEC.md §8): one table candidate (engine table JSON v1.0) and a person's corrections, in memory.
// Opening reads the files directly (fast). Saving goes through the worker's table-save mode, so the C# app and the
// PySide6 GUI write corrections by the same rules (gpdesk/table_edit.py): the engine candidate is never written.
class TableDoc {
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };
    public string File, Status, ImageSha;
    public int Rows, Cols;
    public List<Dictionary<string, object>> Cells;
    public bool Stale, Confirmed, Structure;
    public ArrayList History = new ArrayList();
    int engRows, engCols; List<Dictionary<string, object>> engCells; string onDisk;
    readonly Stack<string> undo = new Stack<string>();

    static object G(Dictionary<string, object> d, string k) { return d != null && d.ContainsKey(k) ? d[k] : null; }
    static int I(object o) { return o == null ? 0 : System.Convert.ToInt32(o); }
    public static string EditedPath(string f) { return Path.Combine(Path.GetDirectoryName(f), Path.GetFileNameWithoutExtension(f) + ".edited.json"); }
    public static string Sha(string f) { using (var h = SHA256.Create()) return BitConverter.ToString(h.ComputeHash(System.IO.File.ReadAllBytes(f))).Replace("-", "").ToLowerInvariant(); }
    static List<Dictionary<string, object>> CellsOf(Dictionary<string, object> d) {
        var a = G(d, "cells") as ArrayList;
        if (a == null || a.Count == 0) throw new InvalidDataException("표에 칸이 없습니다");
        return a.Cast<Dictionary<string, object>>().ToList();
    }
    static List<Dictionary<string, object>> Copy(List<Dictionary<string, object>> cells) {
        return ((ArrayList)Json.Deserialize<Dictionary<string, object>>(Json.Serialize(new Dictionary<string, object> { { "c", cells } }))["c"])
            .Cast<Dictionary<string, object>>().ToList();
    }

    public static TableDoc Load(string file) {
        var d = Json.Deserialize<Dictionary<string, object>>(System.IO.File.ReadAllText(file, Encoding.UTF8));
        if (d == null || System.Convert.ToString(G(d, "schema_version")) != "1.0") throw new InvalidDataException("이 버전이 읽을 수 없는 표 형식입니다");
        string status = G(d, "status") as string ?? "ready";
        if (status != "ready" && status != "review") throw new InvalidDataException("편집할 수 있는 상태의 표가 아닙니다");
        var t = new TableDoc { File = file, Status = status, Rows = I(G(d, "rows")), Cols = I(G(d, "cols")) };
        if (t.Rows < 1) throw new InvalidDataException("표에 행이 없습니다");
        t.engCells = CellsOf(d);
        foreach (var c in t.engCells) c["edited"] = false;
        t.engRows = t.Rows; t.engCols = t.Cols; t.Cells = Copy(t.engCells);
        var img = G(d, "image") as Dictionary<string, object>;
        t.ImageSha = img != null ? G(img, "sha256") as string : G(d, "image_sha256") as string;
        string ep = EditedPath(file);
        if (System.IO.File.Exists(ep)) {
            try {
                var e = Json.Deserialize<Dictionary<string, object>>(System.IO.File.ReadAllText(ep, Encoding.UTF8));
                if (G(e, "base_sha256") as string != Sha(file)) throw new InvalidDataException("stale");
                t.Cells = CellsOf(e); t.Rows = I(G(e, "rows")); t.Cols = I(G(e, "cols"));
                var edits = G(e, "edits") as Dictionary<string, object>;
                t.Structure = edits != null && true.Equals(G(edits, "structure_changed"));
                t.History = (edits != null ? G(edits, "history") as ArrayList : null) ?? new ArrayList();
                var review = G(e, "review") as Dictionary<string, object>;
                t.Confirmed = review != null && true.Equals(G(review, "confirmed_by_user"));
            } catch (Exception) { t.Stale = true; t.Cells = Copy(t.engCells); t.Rows = t.engRows; t.Cols = t.engCols; t.Structure = false; t.History = new ArrayList(); }
        }
        t.onDisk = t.Snapshot();
        return t;
    }

    string Snap(List<Dictionary<string, object>> cells, int rows, int cols) {
        var sb = new StringBuilder(rows + "x" + cols);
        foreach (var c in cells.OrderBy(c => I(G(c, "row"))).ThenBy(c => I(G(c, "col"))))
            sb.Append('|').Append(I(G(c, "row"))).Append(',').Append(I(G(c, "col"))).Append(',').Append(Span(c, "row_span")).Append(',').Append(Span(c, "col_span")).Append(':').Append(G(c, "text") as string ?? "");
        return sb.ToString();
    }
    string Snapshot() { return Snap(Cells, Rows, Cols); }
    static int Span(Dictionary<string, object> c, string k) { int v = I(G(c, k)); return v < 1 ? 1 : v; }
    public int EditedCells { get { return Cells.Count(c => true.Equals(G(c, "edited"))); } }
    public bool Differs { get { return Snapshot() != Snap(engCells, engRows, engCols); } }
    public bool Dirty { get { return Snapshot() != onDisk; } }
    public bool NeedsConfirm { get { return Status == "review" && !Confirmed; } }
    public bool CanSave { get { return Dirty || NeedsConfirm; } }
    public bool CanUndo { get { return undo.Count > 0; } }
    public bool HasMerge { get { return Cells.Any(c => Span(c, "row_span") > 1 || Span(c, "col_span") > 1); } }

    public Dictionary<string, object> At(int r, int c) { return Cells.FirstOrDefault(x => I(G(x, "row")) == r && I(G(x, "col")) == c); }
    // grid positions hidden under a merged cell -> its top-left cell
    public Dictionary<Point, Dictionary<string, object>> Covered() {
        var m = new Dictionary<Point, Dictionary<string, object>>();
        foreach (var c in Cells) {
            int r0 = I(G(c, "row")), c0 = I(G(c, "col")), rs = Span(c, "row_span"), cs = Span(c, "col_span");
            for (int r = r0; r < r0 + rs; r++) for (int k = c0; k < c0 + cs; k++) if (r != r0 || k != c0) m[new Point(k, r)] = c;
        }
        return m;
    }
    public string Text(Dictionary<string, object> c) { return G(c, "text") as string ?? ""; }
    public string TextCheck(Dictionary<string, object> c) { return true.Equals(G(c, "edited")) ? null : G(c, "text_check") as string; }
    public List<string> Nested(Dictionary<string, object> c) { var a = G(c, "nested_table_ids") as ArrayList; return a == null ? new List<string>() : a.Cast<object>().Select(o => o.ToString()).ToList(); }
    public RectangleF? Box(Dictionary<string, object> c) {
        var a = G(c, "bbox_px") as ArrayList;
        if (a == null || a.Count != 4) return null;
        float x0 = System.Convert.ToSingle(a[0]), y0 = System.Convert.ToSingle(a[1]), x1 = System.Convert.ToSingle(a[2]), y1 = System.Convert.ToSingle(a[3]);
        return x1 > x0 && y1 > y0 ? (RectangleF?)RectangleF.FromLTRB(x0, y0, x1, y1) : null;
    }

    void Push() { undo.Push(Json.Serialize(new Dictionary<string, object> { { "rows", Rows }, { "cols", Cols }, { "cells", Cells }, { "s", Structure }, { "h", History } })); }
    public void Undo() {
        if (undo.Count == 0) return;
        var d = Json.Deserialize<Dictionary<string, object>>(undo.Pop());
        Rows = I(d["rows"]); Cols = I(d["cols"]); Cells = ((ArrayList)d["cells"]).Cast<Dictionary<string, object>>().ToList();
        Structure = true.Equals(d["s"]); History = (ArrayList)d["h"] ?? new ArrayList();
    }
    public bool SetText(int r, int c, string v) {
        var cell = At(r, c);
        if (cell == null || Text(cell) == v) return false;
        Push(); cell["text"] = v; cell["edited"] = true; return true;
    }
    void NeedPlain() { if (HasMerge) throw new InvalidOperationException("병합된 칸이 있는 표는 행·열을 바꿀 수 없습니다"); }
    static Dictionary<string, object> NewCell(int r, int c) {
        return new Dictionary<string, object> { { "row", r }, { "col", c }, { "row_span", 1 }, { "col_span", 1 }, { "text", "" }, { "bbox_px", null }, { "nested_table_ids", new ArrayList() }, { "edited", true } };
    }
    void Log(string op, int at) { Structure = true; History.Add(new Dictionary<string, object> { { "op", op }, { "at", at } }); }
    public void InsertRow(int at) {
        NeedPlain(); Push(); at = Math.Max(0, Math.Min(at, Rows));
        foreach (var c in Cells) if (I(c["row"]) >= at) c["row"] = I(c["row"]) + 1;
        for (int k = 0; k < Cols; k++) Cells.Add(NewCell(at, k));
        Rows++; Log("insert_row", at);
    }
    public void DeleteRow(int r) {
        NeedPlain(); if (Rows <= 1) throw new InvalidOperationException("마지막 행은 지울 수 없습니다");
        Push(); Cells.RemoveAll(c => I(c["row"]) == r);
        foreach (var c in Cells) if (I(c["row"]) > r) c["row"] = I(c["row"]) - 1;
        Rows--; Log("delete_row", r);
    }
    public void InsertCol(int at) {
        NeedPlain(); Push(); at = Math.Max(0, Math.Min(at, Cols));
        foreach (var c in Cells) if (I(c["col"]) >= at) c["col"] = I(c["col"]) + 1;
        for (int r = 0; r < Rows; r++) Cells.Add(NewCell(r, at));
        Cols++; Log("insert_col", at);
    }
    public void DeleteCol(int k) {
        NeedPlain(); if (Cols <= 1) throw new InvalidOperationException("마지막 열은 지울 수 없습니다");
        Push(); Cells.RemoveAll(c => I(c["col"]) == k);
        foreach (var c in Cells) if (I(c["col"]) > k) c["col"] = I(c["col"]) - 1;
        Cols--; Log("delete_col", k);
    }
    public void Reset() {
        if (!Differs && EditedCells == 0) return;
        Push(); Cells = Copy(engCells); Rows = engRows; Cols = engCols; Structure = false; History = new ArrayList();
    }
    public string StateJson() {
        return Json.Serialize(new Dictionary<string, object> { { "rows", Rows }, { "cols", Cols }, { "cells", Cells }, { "structure_changed", Structure }, { "history", History } });
    }
}

class TablePanel : Panel {
    class Entry { public Dictionary<string, object> Asset; public string Label, Status, Reason; public TableDoc Doc; }
    static readonly Dictionary<string, string> NotEditable = new Dictionary<string, string> { { "not_checked", "검사 전" }, { "no_grid", "표 구조 확인 못함" }, { "failed", "검사 실패" } };
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };
    readonly Worker worker; readonly Action<string, Color> say; readonly Action<string, RectangleF?> showImage;
    readonly List<Entry> entries = new List<Entry>();
    readonly Dictionary<string, string> sha = new Dictionary<string, string>();
    readonly ListView list = new ListView { Dock = DockStyle.Top, View = View.Details, FullRowSelect = true, HideSelection = false, MultiSelect = false, BorderStyle = BorderStyle.None };
    readonly Label state = new Label { Dock = DockStyle.Top, AutoEllipsis = true, Padding = new Padding(8, 6, 8, 0) };
    readonly Label hint = new Label { Dock = DockStyle.Top, AutoEllipsis = true, ForeColor = Theme.Muted, Padding = new Padding(8, 2, 8, 0), Text = "칸을 더블클릭해 고치고(줄바꿈은 Shift+Enter), 행·열은 오른쪽 클릭으로 추가·삭제합니다." };
    readonly FlowLayoutPanel readBar = new FlowLayoutPanel { Dock = DockStyle.Top, AutoSize = true, BackColor = Theme.Surface, Padding = new Padding(4, 2, 4, 0) };
    readonly AxButton read = new AxButton("이미지에서 표 읽기", AxKind.Secondary);
    readonly CheckBox readOcr = new CheckBox { Text = "글자도 읽기 (OCR)", Checked = true, AutoSize = true, Margin = new Padding(8, 9, 0, 0) };
    public readonly DataGridView Grid = new DataGridView {
        Dock = DockStyle.Fill, AllowUserToAddRows = false, AllowUserToDeleteRows = false, AllowUserToResizeRows = false,
        SelectionMode = DataGridViewSelectionMode.CellSelect, MultiSelect = false, BackgroundColor = Color.White, BorderStyle = BorderStyle.None,
        AutoSizeRowsMode = DataGridViewAutoSizeRowsMode.AllCells, EditMode = DataGridViewEditMode.EditOnKeystrokeOrF2,
        ColumnHeadersHeightSizeMode = DataGridViewColumnHeadersHeightSizeMode.DisableResizing, ShowCellToolTips = true };
    readonly Label message = new Label { Dock = DockStyle.Fill, TextAlign = ContentAlignment.MiddleCenter, ForeColor = Theme.Warn, Visible = false };
    readonly AxButton undoBtn = new AxButton("되돌리기", AxKind.Secondary), resetBtn = new AxButton("처음 읽은 표로", AxKind.Secondary), saveBtn = new AxButton("저장", AxKind.Primary);
    readonly ContextMenuStrip menu = new ContextMenuStrip();
    readonly ToolTip tips = new ToolTip();
    Entry reading; bool filling, canReadImages;

    public TablePanel(Worker worker, Action<string, Color> say, Action<string, RectangleF?> showImage) {
        this.worker = worker; this.say = say; this.showImage = showImage;
        BackColor = Theme.Surface; Font = Theme.Body; Visible = false;
        list.Columns.Add("표", Theme.Px(190)); list.Columns.Add("상태", Theme.Px(220)); list.Columns.Add("크기", Theme.Px(70));
        list.Height = Theme.Px(112); state.Height = Theme.Px(28); hint.Height = Theme.Px(24); hint.Font = Theme.Small;
        readBar.Controls.Add(read); readBar.Controls.Add(readOcr);
        Grid.DefaultCellStyle.WrapMode = DataGridViewTriState.True;
        Grid.DefaultCellStyle.SelectionBackColor = Theme.Selected; Grid.DefaultCellStyle.SelectionForeColor = Theme.Text;
        Grid.RowHeadersWidth = Theme.Px(44); Grid.EnableHeadersVisualStyles = false;
        Grid.ColumnHeadersDefaultCellStyle.BackColor = Theme.Canvas; Grid.RowHeadersDefaultCellStyle.BackColor = Theme.Canvas;
        Grid.ContextMenuStrip = menu;
        var buttons = new FlowLayoutPanel { Dock = DockStyle.Bottom, AutoSize = true, BackColor = Theme.Surface, Padding = new Padding(4) };
        buttons.Controls.Add(undoBtn); buttons.Controls.Add(resetBtn); buttons.Controls.Add(saveBtn);
        Controls.Add(Grid); Controls.Add(message); Controls.Add(hint); Controls.Add(readBar); Controls.Add(state); Controls.Add(list); Controls.Add(buttons);
        list.SelectedIndexChanged += (s, e) => { if (list.SelectedIndices.Count > 0) ShowCurrent(); };
        Grid.CellEndEdit += (s, e) => CellEdited(e.RowIndex, e.ColumnIndex);
        Grid.CurrentCellChanged += (s, e) => Highlight();
        Grid.CellMouseDown += (s, e) => { if (e.Button == MouseButtons.Right && e.RowIndex >= 0 && e.ColumnIndex >= 0) Grid.CurrentCell = Grid[e.ColumnIndex, e.RowIndex]; };
        menu.Opening += (s, e) => BuildMenu();
        undoBtn.Click += (s, e) => { var d = Doc; if (d != null) { var at = At(); d.Undo(); Draw(at); } };
        resetBtn.Click += (s, e) => { var d = Doc; if (d != null) { var at = At(); d.Reset(); Draw(at); } };
        saveBtn.Click += async (s, e) => await Save();
        read.Click += async (s, e) => await ReadImage();
    }

    Entry Cur { get { return list.SelectedIndices.Count > 0 && list.SelectedIndices[0] < entries.Count ? entries[list.SelectedIndices[0]] : null; } }
    TableDoc Doc { get { var e = Cur; return e == null ? null : e.Doc; } }
    public int Count { get { return entries.Count; } }
    public int Unsaved { get { return entries.Count(e => e.Doc != null && e.Doc.Dirty); } }
    static string Str(Dictionary<string, object> d, string k) { return d != null && d.ContainsKey(k) ? d[k] as string : null; }
    static int Int(Dictionary<string, object> d, string k) { return d != null && d.ContainsKey(k) && d[k] != null ? System.Convert.ToInt32(d[k]) : 0; }
    // tables/<png name>.image.json in the same result folder (assets/tables/<png> -> tables/)
    static string ImagePath(string png) { return Path.Combine(Path.GetDirectoryName(Path.GetDirectoryName(Path.GetDirectoryName(png))), "tables", Path.GetFileNameWithoutExtension(png) + ".image.json"); }

    public void Load(ArrayList assets, bool canReadImages = false) {
        entries.Clear(); reading = null; this.canReadImages = canReadImages;
        var seen = new HashSet<string>();
        foreach (Dictionary<string, object> a in assets ?? new ArrayList()) {
            string tid = Str(a, "table_id");
            string origin = Str(a, "origin");
            if ((origin != "table_image" && origin != "table_source_page") || tid == null || !seen.Add(tid)) continue;   // one entry per table
            var tj = a.ContainsKey("table_json") ? a["table_json"] as Dictionary<string, object> : null;
            var e = new Entry { Asset = a, Label = Str(a, "label") ?? tid, Status = Str(tj, "status") ?? "not_checked", Reason = Str(tj, "reason") ?? "" };
            if ((e.Status == "ready" || e.Status == "review") && Str(tj, "file") != null) Open(e, Str(tj, "file"));
            else if (!NotEditable.ContainsKey(e.Status)) e.Status = "failed";
            string png = Str(a, "file");
            if (e.Doc == null && png != null && File.Exists(ImagePath(png))) Open(e, ImagePath(png));   // read from the picture earlier
            entries.Add(e);
        }
        list.BeginUpdate(); list.Items.Clear();
        foreach (var e in entries) list.Items.Add(new ListViewItem(new[] { e.Label, "", "" }));
        list.EndUpdate();
        RefreshStates();
        if (entries.Count > 0) list.Items[0].Selected = true; else ShowCurrent();
    }

    static void Open(Entry e, string file) {
        try { e.Doc = TableDoc.Load(file); e.Status = e.Doc.Status; e.Reason = ""; }
        catch (Exception ex) {
            e.Doc = null; string st = null;
            try { st = Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(file, Encoding.UTF8))["status"] as string; } catch { }
            if (st == "no_grid") { e.Status = "no_grid"; e.Reason = "이미지에서 표 격자를 찾지 못했습니다"; }
            else { e.Status = "failed"; e.Reason = ex.Message; }
        }
    }

    static string StateOf(Entry e) {
        var d = e.Doc;
        if (d == null) return (NotEditable.ContainsKey(e.Status) ? NotEditable[e.Status] : "검사 실패") + (e.Reason.Length > 0 ? " · " + e.Reason : "");
        string tail = d.Structure ? " · 행·열 바뀜" : "";
        if (d.Dirty) return "저장 안 됨 · " + (d.EditedCells > 0 || d.Structure ? d.EditedCells + "칸" + tail : "처음 읽은 표로 되돌림");
        if (d.Differs) return "사람이 고침 · " + d.EditedCells + "칸" + tail;
        if (d.Status == "review") return d.Confirmed ? "사람이 확인함" : "이미지에서 읽음 · 확인 필요";
        return "원본 표에서 읽음" + (d.Stale ? " · 이전 결과에서 수정함" : "");
    }

    void RefreshStates() {
        for (int i = 0; i < entries.Count; i++) {
            var e = entries[i]; var it = list.Items[i];
            it.SubItems[1].Text = StateOf(e);
            it.SubItems[2].Text = e.Doc != null ? e.Doc.Rows + "행 " + e.Doc.Cols + "열" : "";
            it.ForeColor = e.Doc == null ? Theme.Muted : e.Doc.Dirty || e.Doc.NeedsConfirm ? Theme.Warn : Theme.Text;
        }
        var cur = Cur; var d = Doc;
        state.Text = cur != null ? StateOf(cur) : "";
        state.ForeColor = d == null || d.Dirty || d.NeedsConfirm ? Theme.Warn : d.Differs || d.Confirmed ? Theme.Ok : Theme.Muted;
        undoBtn.Enabled = d != null && d.CanUndo;
        resetBtn.Enabled = d != null && (d.Differs || d.EditedCells > 0);
        saveBtn.Enabled = d != null && d.CanSave;
        saveBtn.SetText(d != null && d.Status == "review" ? "확인 후 저장" : "저장");
        string why; bool can = CanRead(cur, out why);
        readBar.Visible = canReadImages && cur != null && !(d != null && d.Status == "ready");   // source cells first: never read over them
        read.Enabled = can; read.SetText(reading != null ? "표 읽는 중…" : d != null && d.Status == "review" ? "이미지에서 다시 읽기" : "이미지에서 표 읽기");
        tips.SetToolTip(read, why);
    }

    bool CanRead(Entry e, out string why) {
        why = "";
        if (e == null) return false;
        if (e.Doc != null && e.Doc.Status == "ready") { why = "원본 문서에서 칸을 읽은 표는 그 결과를 씁니다"; return false; }
        if (Str(e.Asset, "origin") == "table_source_page") { why = "이 그림은 원본 쪽 전체라서 표 하나만 읽을 수 없습니다"; return false; }
        if (reading != null) { why = "다른 표를 읽는 중입니다"; return false; }
        if (Int(e.Asset, "parts") > 1) { why = "여러 쪽에 걸친 표는 조각마다 따로 읽어야 해서 아직 지원하지 않습니다"; return false; }
        if (e.Doc != null && e.Doc.Dirty) { why = "저장하지 않은 수정이 있습니다 · 저장하거나 되돌린 뒤 다시 읽으세요"; return false; }
        why = "표 그림에서 행·열과 병합을 추정합니다. 결과는 원본과 대조해 확인해야 합니다"; return true;
    }

    public void ShowCurrent() { Draw(null); }

    Point At() { var c = Grid.CurrentCell; return c == null ? new Point(0, 0) : new Point(c.ColumnIndex, c.RowIndex); }

    void Draw(Point? keep) {
        var e = Cur; var d = Doc;
        Grid.Visible = d != null; message.Visible = d == null; hint.Visible = d != null;
        if (e != null) showImage(Str(e.Asset, "file"), null);
        filling = true;
        Grid.Rows.Clear(); Grid.Columns.Clear();
        if (d == null) {
            message.Text = e != null ? StateOf(e) + "\n표 이미지는 그대로 볼 수 있습니다." : "고칠 표가 없습니다";
            filling = false; RefreshStates(); return;
        }
        for (int k = 0; k < d.Cols; k++) Grid.Columns.Add("c" + k, (k + 1).ToString());
        foreach (DataGridViewColumn col in Grid.Columns) { col.SortMode = DataGridViewColumnSortMode.NotSortable; col.MinimumWidth = Theme.Px(60); }
        if (d.Rows > 0) Grid.Rows.Add(d.Rows);
        for (int r = 0; r < d.Rows; r++) Grid.Rows[r].HeaderCell.Value = (r + 1).ToString();
        var covered = d.Covered();
        for (int r = 0; r < d.Rows; r++) for (int k = 0; k < d.Cols; k++) {
            var cell = Grid[k, r];
            Dictionary<string, object> under;
            if (covered.TryGetValue(new Point(k, r), out under)) {
                cell.ReadOnly = true; cell.Style.BackColor = Theme.Canvas; cell.Value = "";
                cell.ToolTipText = "병합된 칸 · 글자는 왼쪽 위 칸에 있습니다"; continue;
            }
            var c = d.At(r, k);
            if (c == null) { cell.ReadOnly = true; cell.Style.BackColor = Theme.Canvas; continue; }
            cell.Value = d.Text(c);
            var tips = new List<string>();
            int rs = System.Convert.ToInt32(c["row_span"]), cs = System.Convert.ToInt32(c["col_span"]);
            if (rs > 1 || cs > 1) { tips.Add(rs + "행 × " + cs + "열 병합"); cell.Style.BackColor = Theme.Selected; }
            if (d.Nested(c).Count > 0) tips.Add("이 칸 안에 표가 있습니다 · 오른쪽 클릭 › 안쪽 표 열기");
            string check = d.TextCheck(c);
            if (!d.Confirmed && (check == "review" || check == "not_detected")) {
                cell.Style.BackColor = Theme.WarnBg;
                tips.Add(check == "review" ? "그림에서 읽은 글자 · 원본과 확인 필요" : "글자를 찾지 못함 · 빈 칸으로 확인된 것은 아님");
            }
            cell.ToolTipText = String.Join("\n", tips);
        }
        Grid.AutoResizeColumns(DataGridViewAutoSizeColumnsMode.AllCells);
        foreach (DataGridViewColumn col in Grid.Columns) col.Width = Math.Min(col.Width, Theme.Px(260));
        filling = false;
        if (keep.HasValue && d.Rows > 0 && d.Cols > 0) Grid.CurrentCell = Grid[Math.Min(keep.Value.X, d.Cols - 1), Math.Min(keep.Value.Y, d.Rows - 1)];
        RefreshStates(); Highlight();
    }

    void CellEdited(int r, int k) {
        var d = Doc; if (filling || d == null || r < 0 || k < 0) return;
        string v = (Grid[k, r].Value as string ?? "").Replace("\r\n", "\n");
        if (d.SetText(r, k, v)) { var cell = Grid[k, r]; cell.Style.BackColor = Color.White; cell.ToolTipText = ""; }
        RefreshStates();
    }

    void BuildMenu() {
        menu.Items.Clear();
        var d = Doc; bool plain = d != null && !d.HasMerge; var at = At();
        Action<string, bool, Action> add = (label, on, act) => { var it = new ToolStripMenuItem(label) { Enabled = on }; it.Click += (s, e) => Do(act); menu.Items.Add(it); };
        add("위에 행 추가", plain, () => d.InsertRow(at.Y));
        add("아래에 행 추가", plain, () => d.InsertRow(at.Y + 1));
        add("이 행 삭제", plain && d.Rows > 1, () => d.DeleteRow(at.Y));
        menu.Items.Add(new ToolStripSeparator());
        add("왼쪽에 열 추가", plain, () => d.InsertCol(at.X));
        add("오른쪽에 열 추가", plain, () => d.InsertCol(at.X + 1));
        add("이 열 삭제", plain && d.Cols > 1, () => d.DeleteCol(at.X));
        menu.Items.Add(new ToolStripSeparator());
        int nested = NestedIndex();
        var open = new ToolStripMenuItem("안쪽 표 열기") { Enabled = nested >= 0 };
        open.Click += (s, e) => { if (nested >= 0) list.Items[nested].Selected = true; };
        menu.Items.Add(open);
        if (d != null && d.HasMerge) menu.Items.Add(new ToolStripMenuItem("(병합된 칸이 있는 표는 행·열을 바꿀 수 없습니다)") { Enabled = false });
    }

    void Do(Action act) {
        var at = At();
        try { act(); } catch (InvalidOperationException ex) { say(ex.Message, Theme.Warn); return; }
        Draw(at);
    }

    int NestedIndex() {
        var d = Doc; if (d == null || Grid.CurrentCell == null) return -1;
        var c = d.At(Grid.CurrentCell.RowIndex, Grid.CurrentCell.ColumnIndex); if (c == null) return -1;
        var ids = d.Nested(c);
        for (int i = 0; i < entries.Count; i++) if (ids.Contains(Str(entries[i].Asset, "table_id"))) return i;
        return -1;
    }

    // outline the selected cell on the picture, only when its boxes were measured on that very image
    void Highlight() {
        var e = Cur; var d = Doc; if (e == null || filling) return;
        string png = Str(e.Asset, "file"); RectangleF? box = null;
        if (d != null && d.ImageSha != null && png != null && File.Exists(png) && Grid.CurrentCell != null) {
            string h; if (!sha.TryGetValue(png, out h)) { try { h = TableDoc.Sha(png); sha[png] = h; } catch { h = null; } }
            var c = d.At(Grid.CurrentCell.RowIndex, Grid.CurrentCell.ColumnIndex);
            if (h == d.ImageSha && c != null) box = d.Box(c);
        }
        showImage(png, box);
    }

    async Task Save() {
        var e = Cur; var d = Doc; if (d == null) return;
        if (Grid.IsCurrentCellInEditMode) Grid.EndEdit();
        string tmp = Path.Combine(Path.GetTempPath(), "govparse-table-" + Guid.NewGuid().ToString("N") + ".json");
        saveBtn.Enabled = false;
        try {
            File.WriteAllText(tmp, d.StateJson(), new UTF8Encoding(false));
            string o = await Task.Run(() => worker.Run("table-save " + Worker.Quote(d.File) + " " + Worker.Quote(tmp), 120000));
            var r = Json.Deserialize<Dictionary<string, object>>(o);
            var at = At();
            e.Doc = TableDoc.Load(d.File);
            Draw(at);
            if (r["saved"] == null) say("원본에서 읽은 표로 되돌렸습니다 · " + e.Label, Theme.Ok);
            else say("표를 저장했습니다 · " + e.Label + " · 고친 칸 " + r["edited_cells"] + "개 · " + Path.GetFileName((string)r["saved"]), Theme.Ok);
        } catch (Exception ex) { say("표를 저장하지 못했습니다 · " + ex.Message, Theme.Warn); RefreshStates(); }
        finally { try { File.Delete(tmp); } catch { } }
    }

    async Task ReadImage() {
        var e = Cur; string why;
        if (!CanRead(e, out why)) { if (why.Length > 0) say(why, Theme.Warn); return; }
        string png = Str(e.Asset, "file"), outFile = ImagePath(png);
        bool ocr = readOcr.Checked;
        reading = e; RefreshStates();
        say("표 그림을 읽는 중 · " + e.Label + (ocr ? " (글자 포함, 처음에는 모델을 불러오느라 몇 초 걸립니다)" : ""), Theme.Muted);
        try {
            string o = await Task.Run(() => worker.Run("image-table " + Worker.Quote(png) + " " + Worker.Quote(outFile) + " " + (ocr ? "ocr" : "no-ocr") + " " + Worker.Quote(Str(e.Asset, "table_id") ?? ""), 900000));
            var r = Json.Deserialize<Dictionary<string, object>>(o);
            reading = null;
            if (!entries.Contains(e)) return;                       // another result was opened meanwhile
            Open(e, outFile);
            if (e.Doc != null) say(String.Format("표 그림을 읽었습니다 · {0} · {1}행 {2}열 · {3} · 원본과 대조해 확인하세요", e.Label, e.Doc.Rows, e.Doc.Cols,
                                   Int(r, "ocr_cells") > 0 ? "글자 읽은 칸 " + Int(r, "ocr_cells") + "개" : "글자는 읽지 않음"), Theme.Ok);
            else say("표 그림에서 격자를 찾지 못했습니다 · " + e.Label + " · 표 이미지는 그대로 볼 수 있습니다", Theme.Warn);
            if (Cur == e) Draw(null); else RefreshStates();
        } catch (Exception ex) {
            reading = null;
            if (entries.Contains(e)) { say("표 그림을 읽지 못했습니다 · " + e.Label + " · " + ex.Message, Theme.Warn); RefreshStates(); }
        }
    }
}
