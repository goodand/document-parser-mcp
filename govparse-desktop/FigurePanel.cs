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
    readonly Label pickHint = new Label { Dock = DockStyle.Top, Height = 26, Padding = new Padding(8, 5, 0, 0), Text = "설명으로 쓸 문단을 고르세요 (★ = 설명일 가능성이 높은 문단). 그림 여러 개를 함께 고르면 같은 설명이 지정됩니다.", ForeColor = Muted };
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
        list.Columns.Add("그림", Theme.Px(104)); list.Columns.Add("상위 항목", Theme.Px(190)); list.Columns.Add("설명 여부", Theme.Px(120)); list.Columns.Add("설명", Theme.Px(260));
        list.SmallImageList = thumbs; list.SelectedIndexChanged += (s, e) => OnSelect();
        var bar = new FlowLayoutPanel { Dock = DockStyle.Top, Height = Theme.Px(44), Padding = new Padding(6, 4, 6, 0), BackColor = Theme.Surface };
        bar.Controls.Add(filter);
        var adv = new AxButton("고급: 설명 직접 지정", AxKind.Ghost); bar.Controls.Add(adv);
        var actions = new FlowLayoutPanel { Dock = DockStyle.Bottom, AutoSize = true, Padding = new Padding(6, 4, 6, 4), BackColor = Theme.Surface };
        actions.Controls.Add(Btn("고른 문단을 설명으로 지정", async (s, e) => await Decide(list.SelectedItems.Count > 1 ? "share" : "link")));

        actions.Controls.Add(Btn("지정 취소", async (s, e) => await Decide("unlink")));
        actions.Controls.Add(Btn("설명 없는 그림으로 표시", async (s, e) => await Decide("none")));

        actions.Controls.Add(Btn("되돌리기", async (s, e) => await Send(new { cmd = "decide", action = "undo" })));
        var lower = new SplitContainer { Dock = DockStyle.Fill, Orientation = Orientation.Horizontal, SplitterDistance = 150 };
        lower.Panel1.Controls.Add(detail);
        lower.Panel2.Controls.Add(picks); lower.Panel2.Controls.Add(pickHint);
        picks.DrawMode = DrawMode.OwnerDrawFixed; picks.ItemHeight = Theme.Px(26); picks.DrawItem += DrawPick;
        var split = new SplitContainer { Dock = DockStyle.Fill, Orientation = Orientation.Horizontal, SplitterDistance = 260 };
        split.Panel1.Controls.Add(list);
        split.Panel2.Controls.Add(lower); split.Panel2.Controls.Add(ocr); split.Panel2.Controls.Add(actions);
        Controls.Add(split); Controls.Add(bar); Controls.Add(summary); Controls.Add(status);
        // keep the list, the evidence text and the candidate list all visible at any panel height
        EventHandler fit = (s2, e2) => { try { if (split.Height > 200) split.SplitterDistance = (int)(split.Height * (advanced ? 0.34 : 0.50)); if (advanced && lower.Height > 120) lower.SplitterDistance = (int)(lower.Height * 0.5); } catch { } };
        Action mode = () => { lower.Panel2Collapsed = !advanced; actions.Visible = advanced; adv.Kind = advanced ? AxKind.Tab : AxKind.Ghost; adv.Active = advanced; adv.Invalidate(); fit(null, null); if (Current != null) OnSelect(); };
        adv.Click += (s2, e2) => { advanced = !advanced; mode(); }; mode();
        split.Resize += fit; lower.Resize += fit;
    }

    static AxButton Btn(string text, EventHandler h) { var b = new AxButton(text, AxKind.Secondary); b.Click += h; return b; }
    public void Reset() { source = null; generation++; Current = null; CurrentPage = -1; links.Clear(); list.Items.Clear(); detail.Text = ""; picks.Items.Clear(); summary.Text = ""; }

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
            status.Text = data.ContainsKey("note") && data["note"] != null ? (string)data["note"] : (advanced ? "지정한 설명은 저장되며, 같은 내용의 문서를 다시 열면 복원됩니다." : "그림을 고르면 원본에서 위치와 설명을 보여 줍니다.");
        } catch (Exception ex) { status.Text = "실패 · " + ex.Message; status.ForeColor = Warn; }
    }

    public async Task Open(string path) {
        source = path; generation++; Current = null; CurrentPage = -1; links.Clear(); list.Items.Clear(); detail.Text = ""; picks.Items.Clear();
        summary.Text = path.ToLowerInvariant().EndsWith(".hwpx") ? "그림 위치와 설명 후보를 읽는 중…" : "HWP는 한컴으로 HWPX 변환본을 만든 뒤 구조를 읽습니다. 잠시 걸립니다…";
        string outDir = Path.Combine(Path.GetTempPath(), "GovParseFigures", Guid.NewGuid().ToString("N").Substring(0, 8));
        await Send(new { cmd = "open", src = path, @out = outDir });
    }

    async Task Decide(string action) {
        if (Current == null) { status.Text = "먼저 그림을 선택하세요."; return; }
        var ids = list.SelectedItems.Cast<ListViewItem>().Select(i => OccId((Dictionary<string, object>)i.Tag)).ToList();
        var refs = picks.SelectedItems.Cast<object>().Select(o => (string)((Dictionary<string, object>)o)["ref"]).ToList();
        if ((action == "link" || action == "share") && refs.Count == 0) { status.Text = "먼저 가운데 목록에서 설명으로 쓸 문단을 고르세요."; status.ForeColor = Warn; return; }
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
            var item = new ListViewItem(new[] { "#" + n, (string)occ["parent_item"], Status(st), why.Replace("\n", " ") }, groups[gk]) { Tag = l };
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
        sb.AppendLine("문서 안 위치: " + l["path_text"] + String.Format(" · 그 칸의 {0}번째 그림", (int)occ["order_in_cell"] + 1));
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
                sb.AppendLine(String.Format("  후보{0} {1} — {2}", (bool)c["proposed"] ? "★" : " ", c["text"], c["reason"]));
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
        invalidateViewer();
    }

    void DrawPick(object sender, DrawItemEventArgs e) {
        e.DrawBackground();
        if (e.Index < 0) return;
        var p = (Dictionary<string, object>)picks.Items[e.Index];
        string kind = (string)p["kind"];
        bool proposed = kind == "numbered_label" || kind == "bracket_title";
        bool excluded = kind == "figure_marker" || kind == "file_name" || kind == "table_title";
        var color = (e.State & DrawItemState.Selected) != 0 ? SystemColors.HighlightText : excluded ? Muted : Theme.Text;
        TextRenderer.DrawText(e.Graphics, (proposed ? "★ " : "   ") + p["text"] + (excluded ? "  (" + kind + ")" : ""), picks.Font, e.Bounds, color, TextFormatFlags.Left | TextFormatFlags.VerticalCenter | TextFormatFlags.EndEllipsis);
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
