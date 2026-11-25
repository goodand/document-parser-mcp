using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

// Review panel for "그림과 설명". Talks to `worker.py serve` (one JSON line per command).
// The panel never edits the source; decisions are stored by the worker per source SHA-256.
class FigurePanel : Panel {
    static readonly Color Teal = Theme.Primary, Line = Theme.Border, Muted = Theme.Muted, Warn = Theme.Warn;
    readonly Worker worker; readonly OcrBox ocr;
    public string Source { get { return source; } }
    public Action<float> Reveal;     // moves the original preview to the selected figure
    readonly string root;
    readonly Func<int, Task> showPage;      // renderer page index (0-based)
    readonly Action invalidateViewer;
    Process proc;
    readonly object gate = new object();
    readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };
    readonly Label summary = new Label { Dock = DockStyle.Top, Height = 76, Padding = new Padding(10, 8, 10, 0) };
    readonly ComboBox filter = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Width = 170 };
    readonly ListView list = new ListView { Dock = DockStyle.Fill, View = View.Details, FullRowSelect = true, HideSelection = false, MultiSelect = true };
    readonly ImageList thumbs = new ImageList { ImageSize = new Size(64, 48), ColorDepth = ColorDepth.Depth24Bit };
    readonly TextBox detail = new TextBox { Dock = DockStyle.Fill, Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical, BackColor = Color.White };
    readonly ListBox picks = new ListBox { Dock = DockStyle.Fill, SelectionMode = SelectionMode.MultiExtended, HorizontalScrollbar = true };
        readonly TextBox captionBox = new TextBox { Dock = DockStyle.Fill, BorderStyle = BorderStyle.FixedSingle };
    bool advanced;
    readonly Label status = new Label { Dock = DockStyle.Bottom, Height = 24, ForeColor = Muted };
    List<Dictionary<string, object>> links = new List<Dictionary<string, object>>();
    public Dictionary<string, object> Current;   // selected link (for viewer overlay)
    public int CurrentPage = -1;
    string source;
    int generation;   // bumps on every Open; older replies are ignored (JobRouting.tla ViewIsCurrent)

    public FigurePanel(string root, Worker worker, Func<int, Task> showPage, Action invalidateViewer) {
        this.root = root; this.worker = worker; Font = Theme.Body; list.Font = Theme.Body; detail.Font = Theme.Small; picks.Font = Theme.Body; summary.Font = Theme.Body; status.Font = Theme.Small; list.BorderStyle = BorderStyle.None; detail.BorderStyle = BorderStyle.None; picks.BorderStyle = BorderStyle.None;
        ocr = new OcrBox(worker, () => Current != null ? (string)Current["asset_file"] : null) { Dock = DockStyle.Bottom }; this.showPage = showPage; this.invalidateViewer = invalidateViewer;
        Dock = DockStyle.Fill; BackColor = Color.White; Padding = new Padding(0);
        filter.Items.AddRange(new object[] { "모든 그림", "설명 미확인만", "설명 있는 그림만" });
        filter.SelectedIndex = 0; filter.SelectedIndexChanged += (s, e) => Fill();
        list.Columns.Add("그림", Theme.Px(96)); list.Columns.Add("설명 여부", Theme.Px(92)); list.Columns.Add("설명", Theme.Px(170)); list.Columns.Add("문서 안 위치", Theme.Px(230)); list.Columns.Add("원본 유형", Theme.Px(200));
        list.SmallImageList = thumbs; list.SelectedIndexChanged += (s, e) => OnSelect();
        var bar = new FlowLayoutPanel { Dock = DockStyle.Top, Height = Theme.Px(44), Padding = new Padding(6, 4, 6, 0), BackColor = Theme.Surface };
        bar.Controls.Add(filter);
        var adv = new AxButton("그림 설명 편집", AxKind.Ghost); bar.Controls.Add(adv);
        var menu = new ContextMenuStrip(); menu.Items.Add(new ToolStripMenuItem("그림 복사", null, (s2, e2) => CopyImage()) { ShortcutKeyDisplayString = "Ctrl+C" });
        list.ContextMenuStrip = menu;
        list.MouseDown += (s2, e2) => {   // right-click selects the row under the mouse first
            if (e2.Button != MouseButtons.Right) return;
            var hit = list.HitTest(e2.Location).Item;
            if (hit != null && !hit.Selected) { list.SelectedItems.Clear(); hit.Selected = true; }
        };
        // Edit mode = three groups, top to bottom. Every input ends in the ONE caption box; only "설명 저장" saves.
        //   (1) 이 그림의 설명: caption box + 설명 저장 / 설명 삭제 / 설명 되돌리기
        //   (2) 그림 속 글자(OCR): 이 그림 OCR / 텍스트 복사 / 설명 칸에 넣기 + OCR text
        //   (3) 주변 원문 문장: click a sentence -> it goes into the caption box
        var editPanel = new Panel { Dock = DockStyle.Fill, BackColor = Theme.Surface, Visible = false };
        var g1 = new Panel { Dock = DockStyle.Top, Height = Theme.Px(126), Padding = new Padding(8, 4, 8, 4), BackColor = Theme.Surface };
        var g1Title = new Label { Dock = DockStyle.Top, Height = Theme.Px(24), Text = "① 이 그림의 설명", Font = Theme.Strong };
        captionBox.Multiline = true; captionBox.Height = Theme.Px(48); captionBox.Dock = DockStyle.Top; captionBox.ScrollBars = ScrollBars.Vertical;
        var g1Buttons = new FlowLayoutPanel { Dock = DockStyle.Top, Height = Theme.Px(44), BackColor = Theme.Surface, Padding = new Padding(0, 4, 0, 0) };
        var save = new AxButton("설명 저장 (Ctrl+S)", AxKind.Primary); save.Click += async (s2, e2) => await SaveCaption();
        g1Buttons.Controls.Add(save);
        g1Buttons.Controls.Add(Btn("설명 삭제", async (s2, e2) => await Decide("unlink")));
        g1Buttons.Controls.Add(Btn("설명 되돌리기", async (s2, e2) => await Send(new { cmd = "decide", action = "undo" })));
        g1.Controls.Add(g1Buttons); g1.Controls.Add(captionBox); g1.Controls.Add(g1Title);
                var g2Title = new Label { Dock = DockStyle.Top, Height = Theme.Px(26), Padding = new Padding(8, 6, 0, 0), Text = "② 그림 속 글자 (OCR)", Font = Theme.Strong, BackColor = Theme.Surface };
        var toCaption = Btn("설명 칸에 넣기", (s2, e2) => {
            if (ocr.Result.Length == 0) { status.Text = "먼저 ‘이 그림 OCR’을 눌러 글자를 읽으세요."; status.ForeColor = Warn; return; }
            captionBox.Text = ocr.Result.Replace("\r\n", " "); pending = "ocr"; captionBox.Focus();
            status.Text = "OCR 글을 설명 칸에 넣었습니다. 고친 뒤 ‘설명 저장’을 누르세요."; status.ForeColor = Muted; });
        ocr.AddAction(toCaption);
        var ocrHost = new Panel { Dock = DockStyle.Top, Height = Theme.Px(150), BackColor = Theme.Surface };
        var g3Title = new Label { Dock = DockStyle.Top, Height = Theme.Px(44), Padding = new Padding(8, 6, 8, 0), BackColor = Theme.Surface,
            Text = "③ 주변 원문 문장 (그림에서 가까운 순) — 더블클릭하면 ① 설명 칸에 들어갑니다" };
        g3Title.Font = Theme.Strong;
        picks.DoubleClick += (s2, e2) => {   // double-click fills the caption box; saving stays explicit (Ctrl+S)
            if (!advanced || picks.SelectedItems.Count == 0) return;
            captionBox.Text = String.Join(" / ", picks.SelectedItems.Cast<object>().Select(o => (string)((Dictionary<string, object>)o)["text"]));
            pending = "source"; captionBox.Focus(); captionBox.SelectionStart = captionBox.TextLength;
            status.Text = "문장을 설명 칸에 넣었습니다. Ctrl+S 또는 ‘설명 저장’으로 저장합니다."; status.ForeColor = Muted; };
        editPanel.Controls.Add(picks); editPanel.Controls.Add(g3Title); editPanel.Controls.Add(ocrHost); editPanel.Controls.Add(g2Title); editPanel.Controls.Add(g1);
        picks.DrawMode = DrawMode.OwnerDrawFixed; picks.ItemHeight = Theme.Px(26); picks.DrawItem += DrawPick;
        var split = new SplitContainer { Dock = DockStyle.Fill, Orientation = Orientation.Horizontal, SplitterDistance = 260 };
        split.Panel1.Controls.Add(list);
        split.Panel2.Controls.Add(detail); split.Panel2.Controls.Add(editPanel); split.Panel2.Controls.Add(ocr);
        Controls.Add(split); Controls.Add(bar); Controls.Add(summary); Controls.Add(status);
        EventHandler fit = (s2, e2) => { try { if (split.Height > 200) split.SplitterDistance = (int)(split.Height * (advanced ? 0.30 : 0.50)); } catch { } };
        // view mode: list / detail / OCR.  edit mode: list / (1)(2)(3); the OCR box moves into group (2).
        Action mode = () => {
            detail.Visible = !advanced; editPanel.Visible = advanced; toCaption.Visible = advanced;   // only meaningful while editing
            if (advanced) { ocr.Parent = ocrHost; ocr.Dock = DockStyle.Fill; } else { ocr.Parent = split.Panel2; ocr.Dock = DockStyle.Bottom; ocr.Height = Theme.Px(150); ocr.BringToFront(); detail.BringToFront(); }
            adv.Kind = advanced ? AxKind.Tab : AxKind.Ghost; adv.Active = advanced; adv.Invalidate(); fit(null, null); if (Current != null) OnSelect(); };
        adv.Click += (s2, e2) => { advanced = !advanced; mode(); }; mode();
        split.Resize += fit;
    }

    // Ctrl+S saves the caption box while the editor is open, wherever the focus is inside this panel
    // Copies the selected picture (original file, original size) to the clipboard.
    void CopyImage() {
        if (Current == null) { status.Text = "먼저 목록에서 그림을 선택하세요."; status.ForeColor = Warn; return; }
        string f = (string)Current["asset_file"];
        if (String.IsNullOrEmpty(f) || !File.Exists(f)) { status.Text = "이 그림은 원본 파일을 꺼내지 못해 복사할 수 없습니다."; status.ForeColor = Warn; return; }
        try {
            using (var img = Image.FromFile(f)) using (var bmp = new Bitmap(img.Width, img.Height)) {
                using (var g = Graphics.FromImage(bmp)) { g.Clear(Color.White); g.DrawImage(img, 0, 0, img.Width, img.Height); }   // transparency -> white
                var data = new DataObject();
                data.SetImage(bmp);
                data.SetFileDropList(new System.Collections.Specialized.StringCollection { f });   // pastes as a file in Explorer / mail too
                Clipboard.SetDataObject(data, true);
                status.Text = String.Format("그림을 복사했습니다 ({0}×{1}). 문서나 메일에 붙여 넣을 수 있습니다.", img.Width, img.Height); status.ForeColor = Theme.Ok;
            }
        } catch (Exception ex) { status.Text = "이 그림 형식은 복사하지 못했습니다 · " + ex.Message; status.ForeColor = Warn; }
    }

    protected override bool ProcessCmdKey(ref Message msg, Keys keyData) {
        if (keyData == (Keys.Control | Keys.C) && list.Focused) { CopyImage(); return true; }   // Ctrl+C on the list copies the picture
        if (advanced && keyData == (Keys.Control | Keys.S)) { var _ = SaveCaption(); return true; }
        return base.ProcessCmdKey(ref msg, keyData);
    }

    // a caption typed for one document must never be saved onto another
    void ClearEditor() { captionBox.Text = ""; pending = "typed"; ocr.Show(); }

    static AxButton Btn(string text, EventHandler h) { var b = new AxButton(text, AxKind.Secondary); b.Click += h; return b; }
    public void Reset() { source = null; generation++; Current = null; CurrentPage = -1; links.Clear(); list.Items.Clear(); detail.Text = ""; picks.Items.Clear(); summary.Text = ""; ClearEditor(); }

    // ---------------------------------------------------------------- worker session
    string Call(string line) {
        lock (gate) {
            if (proc == null || proc.HasExited) {
                var info = new ProcessStartInfo(Path.Combine(root, "runtime", "python.exe"),
                    "-I -X utf8 \"" + Path.Combine(root, "worker.py") + "\" serve") {
                    UseShellExecute = false, CreateNoWindow = true, RedirectStandardInput = true,
                    RedirectStandardOutput = true, RedirectStandardError = true,
                    StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8 };
                proc = Process.Start(info);
                proc.ErrorDataReceived += (s, e) => { };
                proc.BeginErrorReadLine();
            }
            var bytes = Encoding.UTF8.GetBytes(line + "\n");
            proc.StandardInput.BaseStream.Write(bytes, 0, bytes.Length);
            proc.StandardInput.BaseStream.Flush();
            var reply = proc.StandardOutput.ReadLine();
            if (reply == null) throw new Exception("그림 검토 작업이 종료되었습니다.");
            return reply;
        }
    }

    async Task Send(object cmd) {
        status.Text = "처리 중…"; status.ForeColor = Muted;
        string selected = Current != null ? OccId(Current) : null;
        int gen = generation;
        try {
            string line = json.Serialize(cmd);
            var reply = json.Deserialize<Dictionary<string, object>>(await Task.Run(() => Call(line)));
            if (!(bool)reply["ok"]) { status.Text = "실패 · " + reply["error"]; status.ForeColor = Warn; return; }
            var data = json.Deserialize<Dictionary<string, object>>(File.ReadAllText((string)reply["file"], Encoding.UTF8));
            if (gen != generation) return; // a newer document was opened meanwhile
            links = ((ArrayList)data["links"]).Cast<Dictionary<string, object>>().ToList();
            var s = (Dictionary<string, object>)data["summary"];
            int total = (int)s["total"], known = (int)s["auto_explicit"] + (int)s["user_confirmed"], located = total - (int)s["location_unverified"];
            summary.Text = String.Format("그림 {0}개\n설명이 확인된 그림 {1}개 · 미확인 {2}개\n원본에서 위치를 표시할 수 있는 그림 {3}개", total, known, total - known, located);
            if (data["renderer_error"] != null) summary.Text += " (미리보기가 이 문서를 열지 못함)";
            Fill(selected);
            status.Text = data.ContainsKey("note") && data["note"] != null ? (string)data["note"] : (advanced ? "저장한 설명은 같은 내용의 문서를 다시 열면 복원되고, 문서 본문에는 쓰지 않습니다." : "그림을 고르면 원본에서 위치와 설명을 보여 줍니다.");
        } catch (Exception ex) { status.Text = "실패 · " + ex.Message; status.ForeColor = Warn; }
    }

    public async Task Open(string path) {
        source = path; generation++; Current = null; CurrentPage = -1; links.Clear(); list.Items.Clear(); detail.Text = ""; picks.Items.Clear(); ClearEditor();
        summary.Text = path.ToLowerInvariant().EndsWith(".hwpx") ? "그림 위치와 설명 후보를 읽는 중…" : "HWP는 한컴으로 HWPX 변환본을 만든 뒤 구조를 읽습니다. 잠시 걸립니다…";
        string outDir = Path.Combine(Path.GetTempPath(), "GovParseFigures", Guid.NewGuid().ToString("N").Substring(0, 8));
        await Send(new { cmd = "open", src = path, @out = outDir });
    }

    string pending = "typed";   // where the caption box text came from: typed | ocr | source
    async Task SaveCaption() {
        if (Current == null) { status.Text = "먼저 그림을 선택하세요."; return; }
        string text = (captionBox.Text ?? "").Trim();
        if (text.Length == 0) { status.Text = "설명 칸이 비어 있습니다. 설명을 없애려면 ‘설명 삭제’를 누르세요."; status.ForeColor = Warn; return; }
        var picked = picks.SelectedItems.Cast<object>().Select(o => (string)((Dictionary<string, object>)o)["text"]).ToList();
        if (pending == "source" && picked.Count > 0 && text == String.Join(" / ", picked)) { await Decide(list.SelectedItems.Count > 1 ? "share" : "link"); return; }
        string origin = pending == "ocr" && text == ocr.Result.Replace("\r\n", " ").Trim() ? "ocr" : "typed";   // edited text counts as typed
        var ids = list.SelectedItems.Cast<ListViewItem>().Select(i => OccId((Dictionary<string, object>)i.Tag)).ToList();
        await Send(new { cmd = "decide", action = "text", occ_ids = ids, text = text, origin = origin });
    }
    async Task Decide(string action) {
        if (Current == null) { status.Text = "먼저 그림을 선택하세요."; return; }
        var ids = list.SelectedItems.Cast<ListViewItem>().Select(i => OccId((Dictionary<string, object>)i.Tag)).ToList();
        var refs = picks.SelectedItems.Cast<object>().Select(o => (string)((Dictionary<string, object>)o)["ref"]).ToList();
        if ((action == "link" || action == "share") && refs.Count == 0) { status.Text = "먼저 ③ 주변 원문 문장 목록에서 문장을 클릭하세요."; status.ForeColor = Warn; return; }
        await Send(new { cmd = "decide", action = action, occ_ids = ids, para_refs = refs });
    }

    public void Shutdown() { try { if (proc != null && !proc.HasExited) proc.Kill(); } catch { } }

    // ---------------------------------------------------------------- list
    static string OccId(Dictionary<string, object> l) { return (string)((Dictionary<string, object>)l["occurrence"])["occ_id"]; }
    static string Status(string s) {
        switch (s) {
            case "auto_explicit": return "설명 있음";
            case "user_linked": return "설명 지정함";
            case "user_shared": return "설명 지정함(공통)";
            case "user_no_caption": return "설명 없는 그림";
            case "user_undecided": return "결정 불가";
            default: return "설명 미확인";
        }
    }

    void Fill(string keepSelected = null) {
        list.BeginUpdate(); list.Items.Clear(); list.Groups.Clear(); thumbs.Images.Clear();
        var groups = new Dictionary<string, ListViewGroup>();
        int n = 0;
        foreach (var l in links) {
            n++;
            string st = (string)l["status"];
            var loc = (Dictionary<string, object>)l["location"];
            bool verified = (bool)loc["verified"];
            int f = filter.SelectedIndex;
            bool unknown = st == "needs_review" || st == "user_undecided";
            if (f == 1 && !unknown) continue;
            if (f == 2 && unknown) continue;
            var occ = (Dictionary<string, object>)l["occurrence"];
            string gk = (string)l["group_key"];
            if (!groups.ContainsKey(gk)) { groups[gk] = new ListViewGroup(gk, (string)l["path_text"]); list.Groups.Add(groups[gk]); }
            string cap = (string)l["caption_text"];
            string why = cap;
            // page = preview (renderer) page where the picture was verified; not the printed page number
            var cands = loc.ContainsKey("candidate_pages") && loc["candidate_pages"] != null ? ((ArrayList)loc["candidate_pages"]).Cast<object>().Select(o => System.Convert.ToInt32(o) + 1).ToList() : new List<int>();
            string page = verified ? String.Format("{0}쪽 · ", (int)loc["page_index"] + 1)
                : cands.Count > 0 ? "후보 " + String.Join("·", cands.Take(4)) + (cands.Count > 4 ? "…" : "") + "쪽 · " : "쪽 미확인 · ";
            string where = page + (string)l["path_text"] + String.Format(" · 그 칸의 {0}번째 그림", (int)occ["order_in_cell"] + 1);
            var item = new ListViewItem(new[] { "#" + n, Status(st), why.Replace("\n", " "), where, l.ContainsKey("source_kind") ? (string)l["source_kind"] : "" }, groups[gk]) { Tag = l };
            string thumb = (string)l["thumb"];
            if (thumb.Length > 0 && File.Exists(thumb)) {
                try { using (var img = Image.FromFile(thumb)) { thumbs.Images.Add(new Bitmap(img, 64, 48)); } item.ImageIndex = thumbs.Images.Count - 1; } catch { }
            }
            if (st == "needs_review" || st == "user_undecided") item.ForeColor = Warn;
            list.Items.Add(item);
            if (keepSelected != null && OccId(l) == keepSelected) item.Selected = true;
        }
        list.EndUpdate();
        if (list.SelectedItems.Count > 0) list.SelectedItems[0].EnsureVisible();
    }

    async void OnSelect() {
        if (list.SelectedItems.Count == 0) return;
        var l = (Dictionary<string, object>)list.SelectedItems[0].Tag; Current = l;
        var occ = (Dictionary<string, object>)l["occurrence"];
        var loc = (Dictionary<string, object>)l["location"];
        var sb = new StringBuilder();
        string st0 = (string)l["status"], cap0 = (string)l["caption_text"];
        if (((string)occ["parent_item"]).Length > 0) sb.AppendLine("속한 항목: " + occ["parent_item"]);
        if (cap0.Length > 0) sb.AppendLine("설명: " + cap0 + (st0 == "auto_explicit" ? "  (문서에 적힌 캡션)" : "  (직접 지정함)"));
        else if (st0 == "user_no_caption") sb.AppendLine("설명: 없음 (설명 없는 그림으로 표시함)");
        else sb.AppendLine("설명: 문서에 이 그림의 캡션이 따로 적혀 있지 않습니다.");
        var before = ((ArrayList)l["before"]).Cast<string>().ToList(); var after = ((ArrayList)l["after"]).Cast<string>().ToList();
        if (before.Count > 0) sb.AppendLine("그림 앞 글: " + String.Join(" / ", before));
        if (after.Count > 0) sb.AppendLine("그림 뒤 글: " + String.Join(" / ", after));
        if (!(bool)loc["verified"]) sb.AppendLine("원본 화면에서 위치를 표시하지 못했습니다. 위 ‘문서 안 위치’를 참고하세요.");
        if (advanced) {
            sb.AppendLine("— 고급 —  판정 근거: " + l["evidence"] + " · 그림 파일: " + occ["asset_ref"]);
            foreach (Dictionary<string, object> c in (ArrayList)l["candidates"])
                sb.AppendLine(String.Format("  주변 문장: {0} — {1}", c["text"], c["reason"]));
        }
        detail.Text = sb.ToString().Replace("\n", "\r\n");
        picks.Items.Clear();
        foreach (Dictionary<string, object> p in (ArrayList)l["pick_list"]) picks.Items.Add(p);
        if ((bool)loc["verified"]) {
            CurrentPage = (int)loc["page_index"];
            await showPage(CurrentPage);
            if (Reveal != null) { var box = (ArrayList)loc["figure_box"]; var size = (ArrayList)loc["page_size"]; Reveal(Convert.ToSingle(box[1]) / Convert.ToSingle(size[1])); }
        } else { CurrentPage = -1; }
        ocr.Show();
        captionBox.Text = (string)l["caption_text"]; pending = "typed";
        invalidateViewer();
    }

    void DrawPick(object sender, DrawItemEventArgs e) {
        e.DrawBackground();
        if (e.Index < 0) return;
        var p = (Dictionary<string, object>)picks.Items[e.Index];
        string kind = (string)p["kind"];
        bool excluded = kind == "figure_marker" || kind == "file_name" || kind == "table_title";
        var color = (e.State & DrawItemState.Selected) != 0 ? SystemColors.HighlightText : excluded ? Muted : Theme.Text;
        TextRenderer.DrawText(e.Graphics, "  " + p["text"] + (excluded ? "  (" + (kind == "figure_marker" ? "그림 표식 문구" : kind == "file_name" ? "파일 이름" : "표 제목") + ")" : ""), picks.Font, e.Bounds, color, TextFormatFlags.Left | TextFormatFlags.VerticalCenter | TextFormatFlags.EndEllipsis);
        e.DrawFocusRectangle();
    }

    // Draws figure (solid teal) and caption candidate (dashed orange) boxes over the preview.
    public void PaintOverlay(PictureBox viewer, Graphics g, int shownPage) {
        if (Current == null || viewer.Image == null) return;
        var loc = (Dictionary<string, object>)Current["location"];
        if (!(bool)loc["verified"]) {
            TextRenderer.DrawText(g, "이 그림은 원본 화면에서 위치를 표시하지 못했습니다 — 오른쪽 ‘문서 안 위치’를 보세요", Font, new Point(12, 12), Warn, Theme.WarnBg);
            return;
        }
        if ((int)loc["page_index"] != shownPage) return;
        var size = (ArrayList)loc["page_size"];
        float svgW = Convert.ToSingle(size[0]);
        var img = viewer.Image; float scale = Math.Min((float)viewer.ClientSize.Width / img.Width, (float)viewer.ClientSize.Height / img.Height);
        float ox = (viewer.ClientSize.Width - img.Width * scale) / 2, oy = (viewer.ClientSize.Height - img.Height * scale) / 2;
        float k = img.Width / svgW * scale;
        Func<ArrayList, RectangleF> R = b => RectangleF.FromLTRB(ox + Convert.ToSingle(b[0]) * k, oy + Convert.ToSingle(b[1]) * k, ox + Convert.ToSingle(b[2]) * k, oy + Convert.ToSingle(b[3]) * k);
        using (var pen = new Pen(Teal, 3)) {
            var r = R((ArrayList)loc["figure_box"]); g.DrawRectangle(pen, r.X, r.Y, r.Width, r.Height);
            TextRenderer.DrawText(g, "그림", Font, new Point((int)r.X, (int)r.Y - 20), Color.White, Teal);
        }
        if (loc["caption_box"] != null) using (var pen = new Pen(Warn, 2) { DashStyle = DashStyle.Dash }) {
            var r = R((ArrayList)loc["caption_box"]); g.DrawRectangle(pen, r.X - 2, r.Y - 2, r.Width + 4, r.Height + 4);
            TextRenderer.DrawText(g, "설명", Font, new Point((int)r.Right + 4, (int)r.Y), Color.White, Warn);
        }
    }
}
