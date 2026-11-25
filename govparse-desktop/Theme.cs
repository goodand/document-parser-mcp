using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Text;
using System.IO;
using System.Linq;
using System.Management;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Forms;

// Design tokens.
// Colours and type: design.md (중소기업지원사업 검색시스템 teal + Korean typeface).
// Shape and component behaviour: Astryx (meta/astryx 29aa0f4) neutral theme —
//   --radius-inner 0.375rem (6px), --radius-element 0.625rem (10px), --radius-container 0.75rem (12px),
//   thin top bar over a grey canvas with a white content surface (AppShell),
//   menu rows with a leading radio mark and an optional one-line description (DropdownMenuRadioItem).
static class Theme {
    public static readonly Color Canvas = ColorTranslator.FromHtml("#F4F7F9");
    public static readonly Color Surface = Color.White;
    public static readonly Color Border = ColorTranslator.FromHtml("#E3E8EE");
    public static readonly Color Text = ColorTranslator.FromHtml("#1F2A37");
    public static readonly Color Muted = ColorTranslator.FromHtml("#63707E");
    public static readonly Color Primary = ColorTranslator.FromHtml("#00646E");
    public static readonly Color PrimaryPressed = ColorTranslator.FromHtml("#004A52");
    public static readonly Color Selected = ColorTranslator.FromHtml("#EEF5F5");
    public static readonly Color Warn = ColorTranslator.FromHtml("#B25400");
    public static readonly Color WarnBg = ColorTranslator.FromHtml("#FFF7EB");
    public static readonly Color Ok = ColorTranslator.FromHtml("#198100");
    public const int RadiusInner = 6, RadiusElement = 10, RadiusContainer = 12;
    public static float Scale = 1f;
    static string family;
    public static int Px(int v) { return (int)Math.Round(v * Scale); }
    public static string Family {
        get {
            if (family != null) return family;
            var installed = new HashSet<string>(new InstalledFontCollection().Families.Select(f => f.Name));
            foreach (var name in new[] { "KoPubWorld돋움체 Medium", "KoPubWorld돋움체", "KoPubWorldDotum Medium", "Pretendard", "맑은 고딕", "Malgun Gothic" })
                if (installed.Contains(name)) { family = name; return family; }
            family = SystemFonts.MessageBoxFont.Name; return family;
        }
    }
    public static Font Body { get { return new Font(Family, 10.5f); } }      // 14px
    public static Font Small { get { return new Font(Family, 9.5f); } }
    public static Font Strong { get { return new Font(Family, 10.5f, FontStyle.Bold); } }
    public static Font Title { get { return new Font(Family, 12f, FontStyle.Bold); } }
    public static GraphicsPath Round(RectangleF r, float radius) {
        float d = radius * 2; var p = new GraphicsPath();
        p.AddArc(r.X, r.Y, d, d, 180, 90); p.AddArc(r.Right - d, r.Y, d, d, 270, 90);
        p.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90); p.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
        p.CloseFigure(); return p;
    }
}

enum AxKind { Primary, Secondary, Ghost, Tab }

// Astryx-style button: rounded element radius, visible 2px focus ring, text states.
class AxButton : Button {
    public AxKind Kind = AxKind.Secondary;
    public bool Active;         // for Tab kind: current tab
    bool hover, down;
    public AxButton(string text, AxKind kind) {
        Text = text; Kind = kind; Font = kind == AxKind.Primary ? Theme.Strong : Theme.Body;
        FlatStyle = FlatStyle.Flat; FlatAppearance.BorderSize = 0; AutoSize = false; TabStop = true;
        SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer, true);
        Margin = new Padding(Theme.Px(3)); Fit();
        MouseEnter += (s, e) => { hover = true; Invalidate(); }; MouseLeave += (s, e) => { hover = false; down = false; Invalidate(); };
        MouseDown += (s, e) => { down = true; Invalidate(); }; MouseUp += (s, e) => { down = false; Invalidate(); };
        GotFocus += (s, e) => Invalidate(); LostFocus += (s, e) => Invalidate(); EnabledChanged += (s, e) => Invalidate();
    }
    public void Fit() {
        var sz = TextRenderer.MeasureText(Text, Font);
        Size = new Size(sz.Width + Theme.Px(26), Theme.Px(34));
    }
    public void SetText(string t) { Text = t; Fit(); Invalidate(); }
    protected override void OnPaint(PaintEventArgs e) {
        var g = e.Graphics; g.SmoothingMode = SmoothingMode.AntiAlias;
        g.Clear(Parent != null ? Parent.BackColor : Theme.Surface);
        var r = new RectangleF(2, 2, Width - 5, Height - 5);
        Color fill, fore, line = Color.Empty;
        if (Kind == AxKind.Primary) { fill = down ? Theme.PrimaryPressed : Theme.Primary; fore = Color.White; }
        else if (Kind == AxKind.Tab) { fill = Active ? Theme.Selected : (hover ? Theme.Canvas : Color.Transparent); fore = Active ? Theme.Primary : Theme.Text; }
        else if (Kind == AxKind.Ghost) { fill = down ? Theme.Border : (hover ? Theme.Canvas : Color.Transparent); fore = Theme.Text; }
        else { fill = down ? Theme.Selected : (hover ? Theme.Canvas : Theme.Surface); fore = Theme.Text; line = Theme.Border; }
        if (!Enabled) { fore = Color.FromArgb(160, 168, 178); if (Kind == AxKind.Primary) fill = Color.FromArgb(170, 198, 201); }
        using (var path = Theme.Round(r, Theme.RadiusElement * Theme.Scale)) {
            if (fill != Color.Transparent) using (var b = new SolidBrush(fill)) g.FillPath(b, path);
            if (line != Color.Empty) using (var p = new Pen(line)) g.DrawPath(p, path);
        }
        if (Focused && ShowFocusCues) using (var ring = Theme.Round(new RectangleF(0.5f, 0.5f, Width - 2, Height - 2), (Theme.RadiusElement + 2) * Theme.Scale))
            using (var p = new Pen(Kind == AxKind.Primary ? Theme.Text : Theme.Primary, 2)) g.DrawPath(p, ring);
        var font = Kind == AxKind.Tab && Active ? Theme.Strong : Font;
        TextRenderer.DrawText(g, Text, font, ClientRectangle, fore, TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.SingleLine);
    }
    protected override bool ShowFocusCues { get { return true; } }
}

// Radio menu modelled on Astryx DropdownMenuRadioItem: leading radio mark, label, optional description.
// Arrow keys move, Enter commits, Esc closes without changing the choice; focus returns to the opener.
class RadioMenu : ContextMenuStrip {
    public class Option { public string Key, Label, Description; public bool Enabled = true; }
    public string Value;
    public event Action<string> Committed;
    readonly List<Option> options = new List<Option>();
    public RadioMenu() { Renderer = new Painter(this); ShowImageMargin = false; ShowCheckMargin = false; Padding = new Padding(Theme.Px(6)); BackColor = Theme.Surface; DropShadowEnabled = true; }
    public void Add(string key, string label, string description, bool enabled = true) {
        var o = new Option { Key = key, Label = label, Description = description, Enabled = enabled }; options.Add(o);
        var item = new FixedItem(label) { Tag = o, AutoSize = false, Enabled = enabled, Size = new Size(Theme.Px(400), Theme.Px(description == null ? 38 : 54)), AccessibleName = label + (description == null ? "" : ", " + description) };
        item.Click += (s, e) => { Value = key; if (Committed != null) Committed(key); };
        Items.Add(item);
    }
    class FixedItem : ToolStripMenuItem { public FixedItem(string t) : base(t) { } public override Size GetPreferredSize(Size c) { return Size; } }
    public string LabelOf(string key) { return options.First(o => o.Key == key).Label; }
    public void Open(Control opener, bool above) {
        var screen = Screen.FromControl(opener).WorkingArea; int top = opener.PointToScreen(Point.Empty).Y;
        int room = above ? top - screen.Top - 8 : screen.Bottom - top - opener.Height - 8;
        AutoSize = false; MaximumSize = new Size(Theme.Px(400) + Padding.Horizontal, Math.Max(Theme.Px(120), room));
        int total = Padding.Vertical; foreach (ToolStripItem it in Items) { it.Width = Theme.Px(400); total += it.Height + it.Margin.Vertical; }
        Size = new Size(Theme.Px(400) + Padding.Horizontal, Math.Min(total, MaximumSize.Height));   // full item width; scroll inside the available height
        if (above) Show(opener, new Point(0, 0), ToolStripDropDownDirection.AboveRight); else Show(opener, new Point(0, opener.Height));
        foreach (ToolStripMenuItem it in Items) if (((Option)it.Tag).Key == Value) { it.Select(); break; }
    }
    class Painter : ToolStripProfessionalRenderer {
        readonly RadioMenu menu;
        public Painter(RadioMenu m) { menu = m; }
        protected override void OnRenderToolStripBorder(ToolStripRenderEventArgs e) { using (var p = new Pen(Theme.Border)) e.Graphics.DrawRectangle(p, 0, 0, e.AffectedBounds.Width - 1, e.AffectedBounds.Height - 1); }
        protected override void OnRenderMenuItemBackground(ToolStripItemRenderEventArgs e) {
            var g = e.Graphics; g.SmoothingMode = SmoothingMode.AntiAlias; var o = (Option)e.Item.Tag;
            var r = new RectangleF(1, 1, e.Item.Width - 3, e.Item.Height - 3);
            using (var b = new SolidBrush(e.Item.Selected && o.Enabled ? Theme.Selected : Theme.Surface)) using (var path = Theme.Round(r, Theme.RadiusInner * Theme.Scale)) g.FillPath(b, path);
            float d = Theme.Px(18), cx = Theme.Px(12), cy = (e.Item.Height - d) / 2f; bool on = o.Key == menu.Value;
            var ink = o.Enabled ? Theme.Primary : Theme.Border;
            if (on) { using (var b = new SolidBrush(ink)) g.FillEllipse(b, cx, cy, d, d); using (var b = new SolidBrush(Color.White)) g.FillEllipse(b, cx + d * 0.3f, cy + d * 0.3f, d * 0.4f, d * 0.4f); }
            else using (var p = new Pen(o.Enabled ? Theme.Muted : Theme.Border, 1.5f)) g.DrawEllipse(p, cx, cy, d, d);
        }
        protected override void OnRenderItemText(ToolStripItemTextRenderEventArgs e) {
            var o = (Option)e.Item.Tag; int x = Theme.Px(42);
            var fore = o.Enabled ? Theme.Text : Color.FromArgb(160, 168, 178);
            if (o.Description == null) { TextRenderer.DrawText(e.Graphics, o.Label, Theme.Body, new Rectangle(x, 0, e.Item.Width - x, e.Item.Height), fore, TextFormatFlags.VerticalCenter | TextFormatFlags.Left); return; }
            TextRenderer.DrawText(e.Graphics, o.Label, Theme.Body, new Point(x, Theme.Px(7)), fore);
            TextRenderer.DrawText(e.Graphics, o.Description, Theme.Small, new Point(x, Theme.Px(29)), o.Enabled ? Theme.Muted : Color.FromArgb(175, 182, 190));
        }
    }
}

// Runs worker.py modes as child processes; cancellable; kills only what it started.
class Worker {
    readonly string root;
    readonly HashSet<Process> live = new HashSet<Process>();
    public Worker(string root) { this.root = root; }
    public static string Quote(string s) { return "\"" + s.Replace("\"", "\\\"") + "\""; }
    public Process Start(string args) {
        var info = new ProcessStartInfo(Path.Combine(root, "runtime", "python.exe"), "-I -X utf8 " + Quote(Path.Combine(root, "worker.py")) + " " + args) {
            UseShellExecute = false, CreateNoWindow = true, RedirectStandardError = true, RedirectStandardOutput = true, RedirectStandardInput = true,
            StandardErrorEncoding = Encoding.UTF8, StandardOutputEncoding = Encoding.UTF8 };
        var p = Process.Start(info); lock (live) live.Add(p); return p;
    }
    // Blocking: call from Task.Run. Throws OperationCanceledException if the process was killed by Cancel.
    // progress: called (on a worker thread) for every "@@progress {json}" line the worker writes to stderr.
    public string Run(string args, int timeoutMs, Action<Process> started = null, Action<Dictionary<string, object>> progress = null) {
        using (var proc = Start(args)) {
            if (started != null) started(proc);
            try {
                var errLines = new List<string>(); var ser = new System.Web.Script.Serialization.JavaScriptSerializer();
                var err = Task.Run(() => {
                    string line;
                    while ((line = proc.StandardError.ReadLine()) != null) {
                        if (line.StartsWith("@@progress ")) {
                            if (progress != null) try { progress(ser.Deserialize<Dictionary<string, object>>(line.Substring(11))); } catch { }
                        } else if (line.Trim().Length > 0) lock (errLines) errLines.Add(line);
                    }
                });
                var res = proc.StandardOutput.ReadToEndAsync();
                if (!proc.WaitForExit(timeoutMs)) { Kill(proc); throw new TimeoutException("처리 제한 시간을 초과했습니다."); }
                Task.WaitAll(err, res);
                if (proc.ExitCode != 0) {
                    if (cancelled.Remove(proc.Id)) throw new OperationCanceledException();
                    throw new Exception(errLines.Count > 0 ? errLines[errLines.Count - 1].Trim() : "작업이 실패했습니다 (종료 코드 " + proc.ExitCode + ")");
                }
                // the result is the last non-empty line; anything a library printed before it is ignored
                var outLines = res.Result.Split('\n').Select(l => l.Trim()).Where(l => l.Length > 0).ToArray();
                return outLines.Length > 0 ? outLines[outLines.Length - 1] : "";
            } finally { lock (live) live.Remove(proc); }
        }
    }
    readonly HashSet<int> cancelled = new HashSet<int>();
    public void Cancel(Process p) { try { if (p != null && !p.HasExited) { cancelled.Add(p.Id); Kill(p); } } catch { } }
    static void Kill(Process p) {
        try { using (var k = Process.Start(new ProcessStartInfo("taskkill", "/F /T /PID " + p.Id) { CreateNoWindow = true, UseShellExecute = false })) k.WaitForExit(5000); } catch { }
    }
    public void Shutdown() { Process[] all; lock (live) all = live.ToArray(); foreach (var p in all) try { if (!p.HasExited) Kill(p); } catch { } }
}

// Hancom automation servers (hwp.exe -Automation) are spawned by COM, outside our process tree.
// Snapshot before a Hancom step; afterwards close only automation instances that appeared since.
// Windows the user opened (no -Automation) and instances that existed before are never touched.
static class HancomGuard {
    public static bool Installed { get { try { return Type.GetTypeFromProgID("HWPFrame.HwpObject") != null; } catch { return false; } } }
    public static HashSet<int> Snapshot() {
        var set = new HashSet<int>();
        try {
            using (var q = new ManagementObjectSearcher("SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name='Hwp.exe'"))
                foreach (ManagementObject o in q.Get()) { var cmd = o["CommandLine"] as string; if (cmd != null && cmd.IndexOf("-Automation", StringComparison.OrdinalIgnoreCase) >= 0) set.Add(Convert.ToInt32(o["ProcessId"])); }
        } catch { return null; }      // snapshot unavailable: ownership cannot be established
        return set;
    }
    public static void CloseNewSince(HashSet<int> before) {
        var now = Snapshot();
        if (before == null || now == null) return;   // unresolved ownership: terminate nothing
        foreach (int id in now) if (!before.Contains(id)) try { Process.GetProcessById(id).Kill(); } catch { }
    }
}

// Copies a picture file to the clipboard (as an image, and as a file for Explorer / mail).
static class PictureClipboard {
    // Returns a status message; ok=false when the file is missing or Windows cannot read the format.
    public static string Copy(string file, out bool ok) {
        ok = false;
        if (String.IsNullOrEmpty(file) || !File.Exists(file)) return "이 그림은 원본 파일이 없어 복사할 수 없습니다.";
        try {
            using (var img = Image.FromFile(file)) using (var bmp = new Bitmap(img.Width, img.Height)) {
                using (var g = Graphics.FromImage(bmp)) { g.Clear(Color.White); g.DrawImage(img, 0, 0, img.Width, img.Height); }   // transparency -> white
                var data = new DataObject();
                data.SetImage(bmp);
                data.SetFileDropList(new System.Collections.Specialized.StringCollection { file });
                Clipboard.SetDataObject(data, true);
                ok = true;
                return String.Format("그림을 복사했습니다 ({0}×{1}). 문서나 메일에 붙여 넣을 수 있습니다.", img.Width, img.Height);
            }
        } catch (Exception ex) { return "이 그림 형식은 복사하지 못했습니다 · " + ex.Message; }
    }
    // Ctrl+C and right-click "그림 복사" on a list; fileOf gives the selected item's picture file.
    public static void Attach(ListView list, Func<string> fileOf, Action<string, bool> report) {
        var menu = new ContextMenuStrip();
        menu.Items.Add(new ToolStripMenuItem("그림 복사", null, (s, e) => { bool ok; var m = Copy(fileOf(), out ok); report(m, ok); }) { ShortcutKeyDisplayString = "Ctrl+C" });
        list.ContextMenuStrip = menu;
        list.MouseDown += (s, e) => {
            if (e.Button != MouseButtons.Right) return;
            var hit = list.HitTest(e.Location).Item;
            if (hit != null && !hit.Selected) { list.SelectedItems.Clear(); hit.Selected = true; }
        };
        list.KeyDown += (s, e) => { if (e.Control && e.KeyCode == Keys.C) { bool ok; var m = Copy(fileOf(), out ok); report(m, ok); e.Handled = true; e.SuppressKeyPress = true; } };
    }
}

// OCR for one selected picture, with copy. Shared by the figure panel and the result panel.
class OcrBox : Panel {
    readonly Worker worker; readonly Func<string> imagePath;
    readonly TextBox text = new TextBox { Dock = DockStyle.Fill, Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical, BackColor = Color.White, BorderStyle = BorderStyle.FixedSingle };
    readonly Label state = new Label { Dock = DockStyle.Top, Height = Theme.Px(22), AutoEllipsis = true, ForeColor = Theme.Muted, Padding = new Padding(6, 3, 6, 0) };   // own line: result count + engine
    readonly AxButton run = new AxButton("이 그림 OCR", AxKind.Secondary), copy = new AxButton("텍스트 복사", AxKind.Secondary);
    readonly Dictionary<string, string[]> cache = new Dictionary<string, string[]>();
    readonly FlowLayoutPanel bar = new FlowLayoutPanel { Dock = DockStyle.Top, Height = Theme.Px(42), BackColor = Theme.Surface };
    int ticket;
    public OcrBox(Worker worker, Func<string> imagePath) {
        this.worker = worker; this.imagePath = imagePath; Height = Theme.Px(150); BackColor = Theme.Surface; Font = Theme.Body; text.Font = Theme.Body;
        bar.Controls.Add(run); bar.Controls.Add(copy);
        Controls.Add(text); Controls.Add(state); Controls.Add(bar); copy.Enabled = false;
        text.AccessibleName = "OCR 결과 텍스트";
        run.Click += async (s, e) => await RunOcr();
        copy.Click += (s, e) => { if (text.Text.Length > 0) { Clipboard.SetText(text.Text); state.Text = "클립보드에 복사했습니다 (" + text.Text.Length + "자)"; state.ForeColor = Theme.Ok; } };
    }
    // Call when the selection changes: shows a cached result or the "not run" state.
    public string Result { get { return text.Text.Trim(); } }   // current OCR text (empty if not run)
    readonly List<AxButton> extra = new List<AxButton>();
    public void AddAction(AxButton b) { extra.Add(b); bar.Controls.Add(b); }   // e.g. 설명 칸에 넣기, kept with the OCR buttons
    public event Action ResultChanged;
    public void Show(string[] known = null) {
        ticket++; string path = imagePath(); string[] r = known;
        if (r == null && path != null) cache.TryGetValue(path, out r);
        run.Enabled = !String.IsNullOrEmpty(path) && File.Exists(path);
        if (r == null) { text.Text = ""; copy.Enabled = false; state.ForeColor = Theme.Muted; state.Text = run.Enabled ? "OCR 미실행" : "선택한 그림의 파일이 없습니다"; return; }
        text.Text = String.Join("\r\n", r); copy.Enabled = r.Length > 0; if (ResultChanged != null) ResultChanged(); state.ForeColor = Theme.Muted; state.Text = r.Length > 0 ? "OCR 결과 " + r.Length + "줄 (그림 안 글자 · 설명 아님)" : "OCR 결과 없음 (빈 결과)";
    }
    async Task RunOcr() {
        string path = imagePath(); if (String.IsNullOrEmpty(path)) return; int mine = ++ticket;
        run.Enabled = false; state.ForeColor = Theme.Muted; state.Text = "OCR 실행 중…";
        try {
            string outp = await Task.Run(() => worker.Run("ocr " + Worker.Quote(path), 240000));
            var d = new System.Web.Script.Serialization.JavaScriptSerializer().Deserialize<Dictionary<string, object>>(outp);
            string st = (string)d["status"]; var lines = ((System.Collections.ArrayList)d["texts"]).Cast<string>().ToArray();
            if (st == "ok" || st == "empty") cache[path] = lines;
            if (mine != ticket) return;      // selection moved on: keep result cached, do not overwrite the box
            if (st == "ok" || st == "empty") { Show(lines); if (d.ContainsKey("engine_label")) state.Text += " · " + d["engine_label"]; }
            else { text.Text = ""; copy.Enabled = false; state.ForeColor = Theme.Warn; state.Text = (st == "not_available" ? "OCR 사용 불가 · " : "OCR 실패 · ") + d["error"]; }
        } catch (Exception ex) { if (mine == ticket) { state.ForeColor = Theme.Warn; state.Text = "OCR 실패 · " + ex.Message; } }
        finally { if (mine == ticket) run.Enabled = true; }
    }
}
