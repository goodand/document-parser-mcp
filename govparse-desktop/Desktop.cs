using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

// One document preview: current page, zoom, and a cache of the current and adjacent pages.
// Each tab (원본 / 결과) owns its own PageView, so page and zoom are remembered per tab.
class PageView : Panel {
    public string DocPath, Renderer = "", Error;
    public int Page, Pages;
    public string Zoom = "width";
    public event Action Changed;
    public Action<PictureBox, Graphics> Overlay;
    public readonly PictureBox Pic = new PictureBox { SizeMode = PictureBoxSizeMode.StretchImage, BackColor = Color.White, Visible = false };
    readonly Panel scroll = new Panel { Dock = DockStyle.Fill, AutoScroll = true, BackColor = Theme.Canvas };
    readonly Label message = new Label { AutoSize = false, TextAlign = ContentAlignment.MiddleCenter, ForeColor = Theme.Muted };
    public readonly AxButton Action;
    readonly Dictionary<int, Image> cache = new Dictionary<int, Image>();
    readonly Worker worker; int gen; public bool Busy;
    const float RenderScale = 1.1f;

    public PageView(Worker worker, string emptyText, string actionText) {
        this.worker = worker; Dock = DockStyle.Fill; BackColor = Theme.Canvas;
        message.Font = Theme.Title; message.Text = emptyText;
        Action = new AxButton(actionText ?? "다시 시도", AxKind.Primary) { Visible = actionText != null };
        scroll.Controls.Add(Pic); Controls.Add(Action); Controls.Add(message); Controls.Add(scroll);
        message.BringToFront(); Action.BringToFront();
        Pic.Paint += (s, e) => { if (Overlay != null) Overlay(Pic, e.Graphics); };
        Resize += (s, e) => Arrange(); scroll.Resize += (s, e) => Arrange();
    }
    void Arrange() {
        message.Bounds = new Rectangle(0, Height / 2 - Theme.Px(70), Width, Theme.Px(60));
        Action.Location = new Point((Width - Action.Width) / 2, Height / 2);
        if (Pic.Image == null) return;
        var img = Pic.Image; int pad = Theme.Px(16); float w, h;
        float baseW = img.Width / RenderScale, baseH = img.Height / RenderScale;
        if (Zoom == "width") { w = Math.Min(Theme.Px(1100), Math.Max(100, scroll.ClientSize.Width - pad * 2 - SystemInformation.VerticalScrollBarWidth)); h = w * img.Height / img.Width; }   // fit width, capped so a page stays readable on wide monitors
        else if (Zoom == "page") { float k = Math.Min((scroll.ClientSize.Width - pad * 2f) / img.Width, (scroll.ClientSize.Height - pad * 2f) / img.Height); w = img.Width * k; h = img.Height * k; }
        else { float k = int.Parse(Zoom) / 100f * Theme.Scale; w = baseW * k; h = baseH * k; }
        Pic.Size = new Size((int)w, (int)h);
        Pic.Location = new Point(Math.Max(pad, (scroll.ClientSize.Width - (int)w) / 2) + scroll.AutoScrollPosition.X, pad + scroll.AutoScrollPosition.Y);
        scroll.AutoScrollMinSize = Size.Empty;   // extents come from the page control itself
        if (Zoom == "width" || Zoom == "page") ShowScrollBar(scroll.Handle, 0, false);   // never a horizontal bar when the page is fitted
    }
    [System.Runtime.InteropServices.DllImport("user32.dll")] static extern bool ShowScrollBar(IntPtr hWnd, int bar, bool show);
    // Scroll so that a point at this fraction of the page height is near the top of the view.
    public void Reveal(float fraction) { if (Pic.Image == null) return; scroll.AutoScrollPosition = new Point(0, Math.Max(0, (int)(Pic.Height * fraction) - Theme.Px(90))); Pic.Invalidate(); }
    public void SetZoom(string z) { Zoom = z; Arrange(); Pic.Invalidate(); }
    void Notice(string text, bool retry, Color color) {
        message.Text = text; message.ForeColor = color; message.Visible = true; Action.Visible = retry; Pic.Visible = false;
        if (retry) Action.SetText("다시 시도"); Arrange();
    }
    public void Clear(string text) { gen++; DocPath = null; Pages = 0; Page = 0; Error = null; Drop(); Pic.Image = null; Notice(text, false, Theme.Muted); if (Changed != null) Changed(); }
    void Drop() { foreach (var i in cache.Values) i.Dispose(); cache.Clear(); }
    public async Task Load(string path) {
        gen++; Drop(); Pic.Image = null; DocPath = path; Pages = 0; Page = 0; Error = null;
        Notice("미리보기 준비 중 · " + Path.GetFileName(path), false, Theme.Muted);
        await Show(0);
    }
    public async Task Retry() { if (DocPath != null) await Show(Page); }
    public async Task Show(int index) {
        if (DocPath == null) return; int g = gen; string doc = DocPath; Image img;
        if (!cache.TryGetValue(index, out img)) {
            string tmp = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString("N") + ".png");
            Busy = true; if (Changed != null) Changed();
            HashSet<int> snap = null;
            try {
                string o = await Task.Run(() => { snap = HancomGuard.Snapshot(); return worker.Run("preview " + Worker.Quote(doc) + " " + Worker.Quote(tmp) + " " + index, 900000); });
                if (g != gen) return;                                     // a newer document owns this view now
                var parts = o.Split('|'); Pages = int.Parse(parts[0]); Renderer = parts.Length > 1 ? parts[1] : "";
                using (var f = Image.FromFile(tmp)) img = new Bitmap(f);
                cache[index] = img;
            } catch (Exception ex) {
                if (snap != null) { var s2 = snap; var _ = Task.Run(() => HancomGuard.CloseNewSince(s2)); }
                if (g != gen) return;
                Busy = false; Error = ex.Message; Notice("미리보기 실패 · " + ex.Message + "\n내보내기는 계속할 수 있습니다.", true, Theme.Warn);
                if (Changed != null) Changed(); return;
            } finally { try { File.Delete(tmp); } catch { } }
        }
        Busy = false; Error = null; Page = index; Pic.Image = img; Pic.Visible = true; message.Visible = false; Action.Visible = false;
        foreach (int k in cache.Keys.Where(k => Math.Abs(k - index) > 1).ToList()) { cache[k].Dispose(); cache.Remove(k); }   // current + adjacent only
        Arrange(); Pic.Invalidate(); if (Changed != null) Changed();
    }
}

class Desktop : Form {
    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] static extern bool AllowSetForegroundWindow(int pid);
    static void Launch(string file, string args = null) {
        AllowSetForegroundWindow(-1);   // ASFW_ANY: the app we start may come to the front
        if (args == null) Process.Start(file); else Process.Start(file, args);
    }
    readonly string root = AppDomain.CurrentDomain.BaseDirectory;
    readonly Worker worker;
    readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };
    readonly PageView orig, resultPages;
    readonly FigurePanel figures;
    readonly SplitContainer origSplit = new SplitContainer { Dock = DockStyle.Fill, Panel2Collapsed = true, FixedPanel = FixedPanel.Panel2, BackColor = Theme.Border, SplitterWidth = 2 };
    readonly SplitContainer resultSplit = new SplitContainer { Dock = DockStyle.Fill, FixedPanel = FixedPanel.Panel2, BackColor = Theme.Border, SplitterWidth = 2, Visible = false };
    readonly WebBrowser web = new WebBrowser { Dock = DockStyle.Fill, Visible = false, AllowWebBrowserDrop = false, ScriptErrorsSuppressed = true, IsWebBrowserContextMenuEnabled = false };
    readonly PictureBox assetPic = new PictureBox { Dock = DockStyle.Fill, SizeMode = PictureBoxSizeMode.Zoom, BackColor = Theme.Canvas, Visible = false };
    readonly Label fileName = new Label { AutoSize = true, ForeColor = Theme.Muted, Padding = new Padding(8, 10, 12, 0) };
    readonly Label pageLabel = new Label { AutoSize = true, Padding = new Padding(6, 9, 6, 0), Text = "– / –" };
    readonly Label status = new Label { Dock = DockStyle.Fill, TextAlign = ContentAlignment.MiddleLeft, AutoEllipsis = true, Padding = new Padding(10, 0, 10, 0) };
    readonly AxButton tabOrig = new AxButton("원본 보기", AxKind.Tab) { Active = true }, tabResult = new AxButton("결과 보기", AxKind.Tab) { Enabled = false };
    readonly AxButton prev = new AxButton("이전", AxKind.Secondary), next = new AxButton("다음", AxKind.Secondary), zoomBtn = new AxButton("폭 맞춤 ▾", AxKind.Secondary);
    readonly AxButton figBtn = new AxButton("그림 목록", AxKind.Secondary), formatBtn = new AxButton("JSON + PNG ▾", AxKind.Secondary);
    readonly AxButton exportBtn = new AxButton("내보내기", AxKind.Primary), cancelBtn = new AxButton("취소 (Esc)", AxKind.Secondary) { Visible = false };
    readonly RadioMenu formatMenu = new RadioMenu(), zoomMenu = new RadioMenu();
    // result side panel
    readonly Label resTitle = new Label { Dock = DockStyle.Top, AutoEllipsis = true, Padding = new Padding(10, 10, 10, 0) };
    readonly Label resState = new Label { Dock = DockStyle.Top, Padding = new Padding(10, 4, 10, 4) };
    readonly TextBox resNotes = new TextBox { Dock = DockStyle.Top, Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical, BackColor = Color.White, BorderStyle = BorderStyle.None };
    readonly ListView assets = new ListView { Dock = DockStyle.Fill, View = View.Details, FullRowSelect = true, HideSelection = false, MultiSelect = false, BorderStyle = BorderStyle.None };
    readonly AxButton openExternal = new AxButton("브라우저에서 열기", AxKind.Secondary), openFolder = new AxButton("폴더 열기", AxKind.Secondary);
    readonly AxButton toHtml = new AxButton("HTML로 변환", AxKind.Secondary), toHwpx = new AxButton("HWPX로 변환", AxKind.Secondary);
    readonly AxButton openZip = new AxButton("압축 파일 보기", AxKind.Secondary);
    readonly OcrBox ocr;
    // shown above the original view when the preview program may draw a picture and a table over each other
    readonly Panel previewNotice = new Panel { Dock = DockStyle.Top, BackColor = Theme.WarnBg, Visible = false };
    readonly Label previewNoticeText = new Label { Dock = DockStyle.Fill, ForeColor = Theme.Warn, Padding = new Padding(10, 6, 6, 4) };
    readonly AxButton previewNoticeClose = new AxButton("닫기", AxKind.Ghost) { Dock = DockStyle.Right };
    // 표 고치기 (qt/SPEC.md §8): a separate mode of the result side panel
    readonly TablePanel tables;
    readonly AxButton tableBtn = new AxButton("표 고치기", AxKind.Secondary);
    readonly FlowLayoutPanel tableBar = new FlowLayoutPanel { Dock = DockStyle.Top, AutoSize = true, BackColor = Theme.Surface, Padding = new Padding(6, 2, 6, 2) };
    readonly ToolTip tableTip = new ToolTip();
    FlowLayoutPanel actions;
    bool tableMode; RectangleF? picBox;   // selected table cell, in the shown PNG's pixels
    string source, resultPath, resultKind, resultJson, lastDir;
    // processing status strip: what is being read/rendered/exported right now, with elapsed time
    readonly Label stStructure = new Label { AutoSize = true, Padding = new Padding(10, 7, 14, 0) };
    readonly Label stPreview = new Label { AutoSize = true, Padding = new Padding(0, 7, 14, 0) };
    readonly Label stExport = new Label { AutoSize = true, Padding = new Padding(0, 7, 10, 0) };
    readonly ProgressBar busyBar = new ProgressBar { Style = ProgressBarStyle.Marquee, MarqueeAnimationSpeed = 30, Width = 90, Height = 8, Visible = false, Margin = new Padding(6, 12, 6, 0) };
    readonly Timer tick = new Timer { Interval = 1000 };
    DateTime? structStart, previewStart, exportStart, stageStart; string structDone, exportStep, previewRoute = "", exportStage = "";
    int stageDone = -1, stageTotal = 0;
    int jobId; bool showingResult; Process exportProc;

    public Desktop() {
        worker = new Worker(root);
        Text = "GovParse Desktop"; Font = Theme.Body; ForeColor = Theme.Text; BackColor = Theme.Canvas;
        Width = Theme.Px(1180); Height = Theme.Px(820); MinimumSize = new Size(Theme.Px(560), Theme.Px(420)); AllowDrop = true; KeyPreview = true;
        orig = new PageView(worker, "HWP 또는 HWPX 파일을 여기에 놓으세요", "파일 열기");
        resultPages = new PageView(worker, "아직 결과가 없습니다. 내보내기를 하거나 ‘결과 불러오기’로 기존 결과를 여세요.", null);
        figures = new FigurePanel(root, worker, i => orig.Show(i), () => orig.Pic.Invalidate());
        ocr = new OcrBox(worker, () => SelectedAssetFile()) { Dock = DockStyle.Bottom };
        tables = new TablePanel(worker, Say, ShowPic) { Dock = DockStyle.Fill };
        orig.Overlay = (pic, g) => figures.PaintOverlay(pic, g, orig.Page, orig.Renderer);
        figures.Reveal = f => orig.Reveal(f);
        orig.Action.Click += async (s, e) => { if (source == null) Pick(); else await orig.Retry(); };
        resultPages.Action.Click += async (s, e) => await resultPages.Retry();
        orig.Changed += Refresh2; resultPages.Changed += Refresh2;

        // ── top bar (AppShell: thin white bar, file name, tabs) ──────────────────────────
        var top = Bar(DockStyle.Top, 50);
        top.Controls.Add(new Label { Text = "GovParse Desktop", Font = Theme.Title, AutoSize = true, Padding = new Padding(10, 9, 4, 0) });
        top.Controls.Add(tabOrig); top.Controls.Add(tabResult);
        var open = new AxButton("파일 열기", AxKind.Secondary); var load = new AxButton("결과 불러오기", AxKind.Secondary); var help = new AxButton("설명서", AxKind.Ghost);
        var settingsBtn = new AxButton("설정", AxKind.Ghost);
        top.Controls.Add(open); top.Controls.Add(load); top.Controls.Add(settingsBtn); top.Controls.Add(help); top.Controls.Add(fileName);   // file name last, so buttons never shift
        open.Click += (s, e) => Pick(); load.Click += async (s, e) => await LoadResult();
        settingsBtn.Click += async (s, e) => {
            bool previewChanged = false;
            using (var f = new SettingsForm(worker)) if (f.ShowDialog(this) == DialogResult.OK) {
                Say("설정을 저장했습니다 · OCR " + f.SavedLabel + " · 미리보기 " + f.SavedPreviewLabel, Theme.Ok);
                previewChanged = f.PreviewChanged;
            }
            // pages already drawn came from the other preview program: draw the document again, keeping the page
            if (previewChanged && orig.DocPath != null) { int page = orig.Page; await orig.Load(orig.DocPath); if (page > 0 && page < orig.Pages) await orig.Show(page); }
        };
        help.Click += (s, e) => { try { Process.Start(Path.Combine(root, "manual.html")); } catch { } };
        tabOrig.Click += (s, e) => SwitchTab(false); tabResult.Click += (s, e) => SwitchTab(true);

        // ── page tools ──────────────────────────────────────────────────────────────────
        var tools = Bar(DockStyle.Top, 46);
        tools.Controls.Add(prev); tools.Controls.Add(pageLabel); tools.Controls.Add(next); tools.Controls.Add(zoomBtn); tools.Controls.Add(figBtn);
        prev.Click += async (s, e) => { var v = Cur; if (v.Page > 0) await v.Show(v.Page - 1); };
        next.Click += async (s, e) => { var v = Cur; if (v.Page + 1 < v.Pages) await v.Show(v.Page + 1); };
        foreach (var z in new[] { "width|폭 맞춤", "page|쪽 맞춤", "100|100%", "150|150%", "200|200%" }) { var kv = z.Split('|'); zoomMenu.Add(kv[0], kv[1], null); }
        zoomMenu.Value = "width";
        zoomMenu.Committed += k => { Cur.SetZoom(k); Refresh2(); }; zoomMenu.Closed += (s, e) => zoomBtn.Focus();
        zoomBtn.Click += (s, e) => { zoomMenu.Value = Cur.Zoom; zoomMenu.Open(zoomBtn, false); };
        figBtn.Click += (s, e) => ToggleFigures();

        // ── export bar (wraps to two lines on narrow windows) ───────────────────────────
        var export = Bar(DockStyle.Bottom, 50); export.AutoSize = true; export.MinimumSize = new Size(0, Theme.Px(50));
        export.Controls.Add(new Label { Text = "내보낼 형식", AutoSize = true, Padding = new Padding(10, 10, 4, 0) });
        export.Controls.Add(formatBtn); export.Controls.Add(exportBtn); export.Controls.Add(cancelBtn);
        formatMenu.Add("json", "JSON + PNG", "데이터와 표 이미지");
        formatMenu.Add("html", "HTML", "브라우저에서 열기");
        formatMenu.Add("hwpx", "한글 문서 · HWPX (.hwpx)", "편집 가능한 HWPX — 본문·표·그림 복원");
        bool hancom = HancomGuard.Installed;
        formatMenu.Add("hwp", "한글 문서 · HWP (.hwp)", hancom ? "한컴에서 HWP로 저장하고 다시 열어 확인" : "이 PC에서 한컴오피스를 찾지 못해 사용할 수 없음", hancom);
        formatMenu.Value = "json";
        formatMenu.Committed += k => { formatBtn.SetText(formatMenu.LabelOf(k) + " ▾"); };   // selecting never parses or writes anything
        formatMenu.Closed += (s, e) => formatBtn.Focus();
        formatBtn.Click += (s, e) => formatMenu.Open(formatBtn, true);
        formatBtn.KeyDown += (s, e) => { if (e.KeyCode == Keys.Down || e.KeyCode == Keys.Up) { formatMenu.Open(formatBtn, true); e.Handled = true; } };
        exportBtn.Click += (s, e) => Export(); cancelBtn.Click += (s, e) => CancelExport();
        var statusBar = new Panel { Dock = DockStyle.Bottom, Height = Theme.Px(30), BackColor = Theme.Surface }; statusBar.Controls.Add(status);

        // ── result side panel ───────────────────────────────────────────────────────────
        var side = new Panel { Dock = DockStyle.Fill, BackColor = Theme.Surface };
        resTitle.Font = Theme.Strong; resTitle.Height = Theme.Px(34); resState.Height = Theme.Px(48); resNotes.Height = Theme.Px(120); resNotes.Font = Theme.Small;
        assets.Columns.Add("자산", Theme.Px(110)); assets.Columns.Add("출처", Theme.Px(80)); assets.Columns.Add("위치 / 설명", Theme.Px(280)); assets.Columns.Add("원본 파일", Theme.Px(200));
        assets.SelectedIndexChanged += (s, e) => SelectAsset();
        PictureClipboard.Attach(assets, () => SelectedAssetFile(),
            (m, ok) => Say(m, ok ? Theme.Ok : Theme.Warn));
        actions = new FlowLayoutPanel { Dock = DockStyle.Bottom, AutoSize = true, BackColor = Theme.Surface, Padding = new Padding(6) };
        actions.Controls.Add(openExternal); actions.Controls.Add(openFolder); actions.Controls.Add(openZip); actions.Controls.Add(toHtml); actions.Controls.Add(toHwpx);
        openZip.Visible = false;
        openZip.Click += async (s, e) => { var z = ZipOf(resultPath); if (z != null) await ShowInExplorer(z); };
        openExternal.Click += (s, e) => OpenExternal(); openFolder.Click += async (s, e) => { if (resultPath != null) await ShowInExplorer((Directory.Exists(resultPath) ? Path.Combine(resultPath, resultKind == "html" ? "document.html" : "document.json") : resultPath)); };
        toHtml.Click += async (s, e) => await Convert("html"); toHwpx.Click += async (s, e) => await Convert("hwpx");
        tableBar.Controls.Add(tableBtn); tableBar.Visible = false;
        tableBtn.Click += (s, e) => ToggleTables();
        side.Controls.Add(assets); side.Controls.Add(tables); side.Controls.Add(Rule(DockStyle.Top)); side.Controls.Add(tableBar); side.Controls.Add(resNotes); side.Controls.Add(resState); side.Controls.Add(resTitle); side.Controls.Add(ocr); side.Controls.Add(actions);
        previewNotice.Height = Theme.Px(64); previewNoticeText.Font = Theme.Small;
        previewNotice.Controls.Add(previewNoticeText); previewNotice.Controls.Add(previewNoticeClose);
        previewNoticeClose.Click += (s, e) => previewNotice.Visible = false;
        origSplit.Panel1.Controls.Add(orig); origSplit.Panel1.Controls.Add(previewNotice); origSplit.Panel2.Controls.Add(figures);
        resultSplit.Panel1.Controls.Add(resultPages); resultSplit.Panel1.Controls.Add(web); resultSplit.Panel1.Controls.Add(assetPic); resultSplit.Panel2.Controls.Add(side);

        var strip = Bar(DockStyle.Top, 34); strip.Padding = new Padding(0); strip.BackColor = Theme.Canvas; strip.WrapContents = false;
        strip.Controls.Add(busyBar); strip.Controls.Add(stStructure); strip.Controls.Add(stPreview); strip.Controls.Add(stExport);
        foreach (var l in new[] { stStructure, stPreview, stExport }) { l.Font = Theme.Small; l.ForeColor = Theme.Muted; }
        stStructure.Text = "구조: 문서를 열면 자동으로 읽습니다";
        tick.Tick += (s2, e2) => UpdateStrip(); tick.Start();
        var content = new Panel { Dock = DockStyle.Fill }; content.Controls.Add(origSplit); content.Controls.Add(resultSplit);
        Controls.Add(content); Controls.Add(Rule(DockStyle.Top)); Controls.Add(strip); Controls.Add(Rule(DockStyle.Top)); Controls.Add(tools); Controls.Add(Rule(DockStyle.Top)); Controls.Add(top);
        Controls.Add(Rule(DockStyle.Bottom)); Controls.Add(export); Controls.Add(Rule(DockStyle.Bottom)); Controls.Add(statusBar);

        DragEnter += (s, e) => { if (e.Data.GetDataPresent(DataFormats.FileDrop)) e.Effect = DragDropEffects.Copy; };
        DragDrop += (s, e) => {
            var paths = (string[])e.Data.GetData(DataFormats.FileDrop);
            if (paths == null) return;
            if (paths.Length != 1) { Say("파일을 하나만 놓아 주세요. (" + paths.Length + "개를 놓았습니다)", Theme.Warn); return; }
            Open(paths[0]);                                                // drop and Ctrl+O share Open()
        };
        KeyDown += (s, e) => {
            if (e.Control && e.KeyCode == Keys.O) { Pick(); e.Handled = true; }
            else if (e.KeyCode == Keys.Escape && exportProc != null) { CancelExport(); e.Handled = true; }
        };
        FormClosing += (s, e) => { figures.Shutdown(); worker.Shutdown(); };   // only processes this app started
        resultSplit.Resize += (s, e) => FitResultPanel();
        assetPic.Resize += (s, e) => FitAssetPic();
        assetPic.Paint += (s, e) => PaintPicBox(e.Graphics);
        Shown += (s, e) => { Task.Run((Action)ServeDesktop); Refresh2(); Say("Ctrl+O 또는 끌어다 놓기로 HWP/HWPX 문서를 여세요.", Theme.Muted); };
    }

    PageView Cur { get { return showingResult ? resultPages : orig; } }
    static FlowLayoutPanel Bar(DockStyle dock, int h) { return new FlowLayoutPanel { Dock = dock, Height = Theme.Px(h), BackColor = Theme.Surface, Padding = new Padding(Theme.Px(6), Theme.Px(5), Theme.Px(6), Theme.Px(3)), WrapContents = true }; }
    static Panel Rule(DockStyle dock) { return new Panel { Dock = dock, Height = 1, BackColor = Theme.Border }; }
    void Say(string text, Color color) { status.ForeColor = color; status.Text = text; }

    void Refresh2() {
        var v = Cur; bool pages = !showingResult || resultKind == "pages";
        pageLabel.Text = pages && v.Pages > 0 ? String.Format("{0} / {1}", v.Page + 1, v.Pages) : "– / –";
        prev.Enabled = pages && v.Page > 0 && !v.Busy; next.Enabled = pages && v.Page + 1 < v.Pages && !v.Busy; zoomBtn.Enabled = pages && v.DocPath != null;
        zoomBtn.SetText(zoomMenu.LabelOf(v.Zoom) + " ▾"); figBtn.Visible = !showingResult;
        tabOrig.Active = !showingResult; tabResult.Active = showingResult; tabOrig.Invalidate(); tabResult.Invalidate();
        if (showingResult && resultKind == "pages" && resultPath != null) {
            if (resultPages.Error != null) SetResState(true, "미리보기 실패 · " + resultPages.Error, false);
            else if (resultPages.Pages > 0) SetResState(true, String.Format("미리보기 성공 · 저장된 결과 파일을 다시 그려 표시 ({0}쪽)", resultPages.Pages), true);
        }
        if (!showingResult && source != null && orig.Pages > 0 && orig.Error == null && exportProc == null && !status.Text.StartsWith("저장") && !status.Text.StartsWith("내보내기") && !status.Text.StartsWith("이전 문서"))
            Say(String.Format("원본 미리보기 · {0} / {1}쪽{2}", orig.Page + 1, orig.Pages, orig.Renderer == "hancom-pdf" ? " · 한컴 PDF로 표시" : ""), Theme.Muted);
    }

    void SwitchTab(bool result) {
        if (result && !tabResult.Enabled) return;
        showingResult = result; origSplit.Visible = !result; resultSplit.Visible = result; FitResultPanel(); Refresh2();     // each PageView keeps its own page and zoom
    }

    static string Ago(DateTime? t) { if (t == null) return ""; var d = DateTime.Now - t.Value; return d.TotalMinutes >= 1 ? String.Format("{0}분 {1:00}초", (int)d.TotalMinutes, d.Seconds) : String.Format("{0}초", d.Seconds); }
    void UpdateStrip() {
        if (structStart != null) { stStructure.Text = "구조 읽는 중 · " + Ago(structStart) + " 경과"; stStructure.ForeColor = Theme.Text; }
        else if (structDone != null) { stStructure.Text = structDone; }
        if (orig.Busy && previewStart != null) { stPreview.Text = "미리보기: " + (previewRoute.Length > 0 ? previewRoute : "그리는 중") + " · " + Ago(previewStart) + " 경과"; stPreview.ForeColor = Theme.Text; }
        else if (source != null && orig.Pages > 0) { stPreview.Text = String.Format("미리보기: 완료 · {0}쪽{1}", orig.Pages, orig.Renderer == "hancom-pdf" ? " (한컴 PDF)" : ""); stPreview.ForeColor = Theme.Muted; previewStart = null; }
        else if (orig.Error != null) { stPreview.Text = "미리보기: 실패"; stPreview.ForeColor = Theme.Warn; previewStart = null; }
        stExport.Text = exportStart == null ? "" : "내보내는 중: " + exportStep + (exportStage.Length > 0 ? " · " + exportStage + Progress() : "") + " · " + Ago(exportStart) + " 경과";
        stExport.ForeColor = Theme.Text;
        busyBar.Visible = structStart != null || (orig.Busy && previewStart != null) || exportStart != null;
    }
    // "표 찾기 340/1182쪽 (29%) · 남은 시간 약 9분": the estimate appears once a few pages are done
    string Progress() {
        if (stageTotal <= 0 || stageDone < 0) return "";
        string p = String.Format(" {0:N0}/{1:N0}쪽 ({2}%)", stageDone, stageTotal, stageDone * 100 / stageTotal);
        if (stageStart != null && stageDone >= 5 && stageDone < stageTotal) {
            double perPage = (DateTime.Now - stageStart.Value).TotalSeconds / stageDone; var left = TimeSpan.FromSeconds(perPage * (stageTotal - stageDone));
            p += left.TotalMinutes >= 1 ? String.Format(" · 남은 시간 약 {0}분", (int)Math.Ceiling(left.TotalMinutes)) : " · 곧 끝남";
        }
        return p;
    }
    void OnProgress(Dictionary<string, object> e) {
        BeginInvoke((Action)(() => {
            string stage = (string)e["stage"];
            if (stage != exportStage) { exportStage = stage; stageStart = DateTime.Now; stageDone = -1; stageTotal = 0; }
            if (e.ContainsKey("total")) { stageTotal = System.Convert.ToInt32(e["total"]); stageDone = System.Convert.ToInt32(e["done"]); }
            if (e.ContainsKey("detail") && stageTotal == 0) exportStage = stage + " (" + e["detail"] + ")";
            UpdateStrip();
        }));
    }
    async Task ReadStructure(string path, int job) {
        structStart = DateTime.Now; structDone = null; UpdateStrip();
        try {
            string o = await Task.Run(() => worker.Run("summary " + Worker.Quote(path), 1800000));
            if (job != jobId) return;                                     // an older document finished late
            var d = json.Deserialize<Dictionary<string, object>>(o);
            structDone = String.Format("구조 읽기 완료 ({4}초 걸림) · 문단 {0:N0} · 표 {1:N0} (중첩 {2:N0}) · 그림 {3:N0}", d["paragraphs"], d["tables"], d["nested_tables"], d["pictures"], d["seconds"]);
            if ((string)d["builder"] != "rhwp") { structDone += " · 원본 XML에서 직접 읽음"; previewRoute = "한컴으로 전체 문서를 PDF로 변환 중 (큰 문서는 수 분)"; }
            stStructure.ForeColor = Theme.Ok;
            ShowPreviewNotice(d.ContainsKey("preview_overlap") ? d["preview_overlap"] as Dictionary<string, object> : null);
        } catch (Exception ex) { if (job != jobId) return; structDone = "구조: 읽기 실패 · " + ex.Message; stStructure.ForeColor = Theme.Warn; }
        finally { if (job == jobId) { structStart = null; UpdateStrip(); } }
    }

    // The document has places the preview program is known to draw over each other: say so, and point to the original.
    void ShowPreviewNotice(Dictionary<string, object> risk) {
        int n = risk != null && risk.ContainsKey("count") ? System.Convert.ToInt32(risk["count"]) : 0;
        if (n == 0 || orig.Renderer == "hancom-pdf") { previewNotice.Visible = false; return; }
        var labels = ((ArrayList)risk["labels"]).Cast<object>().Select(o => o.ToString()).ToList();
        string where = labels.Count > 0 ? " (" + String.Join(", ", labels) + (n > labels.Count ? " 등" : "") + ")" : "";
        previewNoticeText.Text = "미리보기 주의 · 이 문서에는 화면 구현기(미리보기 프로그램)가 그림과 표를 겹쳐 그릴 수 있는 곳이 " + n + "곳 있습니다" + where + ". "
            + "화면 표시만의 문제이고 문서와 내보낸 결과는 정상입니다. 정확한 모양은 원본 문서(한글)에서 확인하세요."
            + (HancomGuard.Installed ? " 설정 › 미리보기 방식 › ‘한컴오피스로 그리기’로 바꾸면 원본과 같게 보입니다." : "");
        previewNotice.Visible = true;
    }

    void FitResultPanel() { try { if (resultSplit.Visible && resultSplit.Width > Theme.Px(700)) resultSplit.SplitterDistance = resultSplit.Width - Theme.Px(460); } catch { } }

    // ── open ───────────────────────────────────────────────────────────────────────────
    void Pick() {
        using (var d = new OpenFileDialog { Title = "한글 문서 열기", Filter = "한글 문서 (*.hwp;*.hwpx)|*.hwp;*.hwpx" })
            if (d.ShowDialog(this) == DialogResult.OK) Open(d.FileName);
    }
    void Open(string path) {
        string ext = Path.GetExtension(path).ToLowerInvariant();
        if (ext != ".hwp" && ext != ".hwpx") { Say("HWP 또는 HWPX 파일만 열 수 있습니다: " + Path.GetFileName(path), Theme.Warn); return; }
        source = path; int job = ++jobId; previewNotice.Visible = false;
        Text = "GovParse Desktop · " + Path.GetFileName(path); fileName.Text = Path.GetFileName(path);
        Say("여는 중 · " + Path.GetFileName(path), Theme.Muted);
        SwitchTab(false);
        previewStart = DateTime.Now; previewRoute = ""; stPreview.Text = "";
        var _ = orig.Load(path);                                           // UI stays responsive; format can be chosen meanwhile
        var __s = ReadStructure(path, job);                                // parsing starts by itself; the strip shows it
        if (!origSplit.Panel2Collapsed) { var __ = figures.Open(path); } else figures.Reset();
        Task.Run(() => {                                                    // job id + source SHA-256 on record
            try {
                string sha; using (var s = File.OpenRead(path)) using (var h = SHA256.Create()) sha = BitConverter.ToString(h.ComputeHash(s)).Replace("-", "").ToLowerInvariant();
                string dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "GovParse Desktop"); Directory.CreateDirectory(dir);
                File.AppendAllText(Path.Combine(dir, "jobs.log"), String.Format("{0:s}\tjob{1}\t{2}\t{3}\r\n", DateTime.Now, job, sha, path), Encoding.UTF8);
            } catch { }
        });
    }
    [DllImport("user32.dll")] static extern bool IsWindowEnabled(IntPtr hWnd);
    void ServeDesktop() {
        string pipeName = "GovParseDesktop-" + Process.GetCurrentProcess().Id;
        var sid = System.Security.Principal.WindowsIdentity.GetCurrent().User;
        var acl = new System.IO.Pipes.PipeSecurity();
        acl.AddAccessRule(new System.IO.Pipes.PipeAccessRule(sid, System.IO.Pipes.PipeAccessRights.FullControl,
            System.Security.AccessControl.AccessControlType.Allow));
        while (!IsDisposed) {
            try {
                using (var pipe = new System.IO.Pipes.NamedPipeServerStream(pipeName, System.IO.Pipes.PipeDirection.InOut,
                    1, System.IO.Pipes.PipeTransmissionMode.Byte, System.IO.Pipes.PipeOptions.None, 4096, 4096, acl)) {
                    pipe.WaitForConnection();
                    using (var input = new StreamReader(pipe, Encoding.UTF8, false, 1024, true))
                    using (var output = new StreamWriter(pipe, new UTF8Encoding(false))) {
                        string request = input.ReadLine();
                        if (request == null) continue;
                        output.WriteLine((string)Invoke(new Func<string>(() => DesktopRequest(request))));
                    }
                }
            } catch (IOException) { } catch (InvalidOperationException) { return; }
        }
    }
    string DesktopRequest(string request) {
        var fields = request.Split('|');
        string statusCode = "open";
        if (fields.Length == 3 && fields[0] == "show") {
            if (!IsWindowEnabled(Handle)) statusCode = "dialog_open";   // a modal dialog disables the window natively; Control.Enabled stays true
            else if (figures.DocumentHash == null || origSplit.Panel2Collapsed) statusCode = "figure_panel_unavailable";
            else if (figures.DocumentHash != fields[1]) statusCode = "document_mismatch";
            else {
                statusCode = figures.SelectVerified(fields[2]);
                if (statusCode == "selected") SwitchTab(false);
            }
        } else if (request == "original" || request == "result") {
            if (!IsWindowEnabled(Handle)) statusCode = "dialog_open";   // a modal dialog disables the window natively; Control.Enabled stays true
            else if (request == "result" && !tabResult.Enabled) statusCode = "view_unavailable";
            else { SwitchTab(request == "result"); statusCode = "shown"; }
        } else if (request != "state") statusCode = "invalid_request";
        return json.Serialize(new { status = statusCode, title = Text, view = showingResult ? "result" : "original",
            source_sha256 = figures.DocumentHash, item_id = !showingResult && !origSplit.Panel2Collapsed ? figures.SelectedId : null,
            page = !showingResult && orig.Pages > 0 ? (int?)(orig.Page + 1) : null });
    }
    void ToggleFigures() {
        origSplit.Panel2Collapsed = !origSplit.Panel2Collapsed;
        if (!origSplit.Panel2Collapsed) { origSplit.SplitterDistance = Math.Max(Theme.Px(220), origSplit.Width - Theme.Px(600)); if (source != null && figures.Source != source) { var _ = figures.Open(source); } }
    }

    // ── export ─────────────────────────────────────────────────────────────────────────
    static bool SamePath(string a, string b) { return String.Equals(Path.GetFullPath(a).TrimEnd('\\'), Path.GetFullPath(b).TrimEnd('\\'), StringComparison.OrdinalIgnoreCase); }
    static string Unique(string target) {
        if (!File.Exists(target) && !Directory.Exists(target)) return target;
        string dir = Path.GetDirectoryName(target), stem = Path.GetFileNameWithoutExtension(target), ext = Path.GetExtension(target);
        if (Directory.Exists(target)) { stem = Path.GetFileName(target); ext = ""; }
        for (int n = 2; ; n++) { string c = Path.Combine(dir, stem + " (" + n + ")" + ext); if (!File.Exists(c) && !Directory.Exists(c)) return c; }
    }
    void SetExporting(bool on) { exportBtn.Enabled = !on; cancelBtn.Visible = on; toHtml.Enabled = toHwpx.Enabled = !on && resultKind == "json"; }
    void CancelExport() { var p = exportProc; if (p != null) { Say("취소하는 중…", Theme.Muted); worker.Cancel(p); } }

    string AskTarget(string mode, string stem, string suffix) {
        if (mode == "hwpx" || mode == "hwp") {
            using (var d = new SaveFileDialog { Title = "한글 문서로 저장", Filter = mode == "hwp" ? "한글 문서 (*.hwp)|*.hwp" : "한글 문서 (*.hwpx)|*.hwpx", FileName = stem + suffix + "." + mode, OverwritePrompt = false, InitialDirectory = lastDir })
                return d.ShowDialog(this) == DialogResult.OK ? Path.ChangeExtension(d.FileName, "." + mode) : null;
        }
        // A result folder is named like a file: one ordinary save dialog where a path can be typed or pasted.
        using (var d = new SaveFileDialog { Title = "결과 폴더 이름과 위치 (이 이름의 새 폴더가 만들어집니다)", Filter = "결과 폴더|*.", FileName = stem + "-" + mode, OverwritePrompt = false, AddExtension = false, InitialDirectory = lastDir })
            return d.ShowDialog(this) == DialogResult.OK ? d.FileName.TrimEnd('.') : null;
    }

    // Stage next to the target (same volume), then publish with a move that never overwrites.
    async Task<Tuple<string, Dictionary<string, object>>> RunStaged(string mode, string workerArgsPrefix, string target, string label) {
        string final = Unique(target), parent = Path.GetDirectoryName(final);
        string temp = Path.Combine(parent, ".govparse-" + Guid.NewGuid().ToString("N").Substring(0, 10));
        HashSet<int> snap = null; bool ok = false;
        exportStart = DateTime.Now; exportStep = label; exportStage = ""; stageDone = -1; stageTotal = 0; UpdateStrip();
        Directory.CreateDirectory(temp); SetExporting(true); Say("내보내는 중 · " + label + "  (Esc로 취소)", Theme.Muted);
        try {
            string outJson = await Task.Run(() => { snap = HancomGuard.Snapshot(); return worker.Run(workerArgsPrefix + " " + Worker.Quote(temp) + (mode.StartsWith("convert") ? " " + mode.Substring(8) : ""), 3600000 * 3, p => exportProc = p, OnProgress); });
            var rep = json.Deserialize<Dictionary<string, object>>(outJson);
            string kind = mode.StartsWith("convert") ? mode.Substring(8) : mode;
            if (kind == "hwpx" || kind == "hwp") {
                final = Unique(final); File.Move(Path.Combine(temp, "document." + kind), final);
                string links = Path.Combine(temp, "document.links.json"); if (File.Exists(links)) File.Move(links, Unique(Path.ChangeExtension(final, ".links.json")));
                Directory.Delete(temp, true);
            } else { final = Unique(final); Directory.Move(temp, final); }
            ok = true; lastDir = parent;
            return Tuple.Create(final, rep);
        } finally {
            exportProc = null; exportStart = null; UpdateStrip();
            if (!ok && snap != null) { var s2 = snap; var _ = Task.Run(() => HancomGuard.CloseNewSince(s2)); }   // only automation instances started during this step
            try { if (Directory.Exists(temp)) Directory.Delete(temp, true); } catch { }
            SetExporting(false); exportBtn.Focus();
        }
    }

    async void Export() {
        if (source == null) { Say("먼저 문서를 여세요.", Theme.Warn); return; }
        if (exportProc != null) return;
        string mode = formatMenu.Value, input = source, name = Path.GetFileName(input); int job = jobId;
        string target = AskTarget(mode, Path.GetFileNameWithoutExtension(input), "-복원");
        if (target == null) { Say("저장을 취소했습니다. 문서와 형식 선택은 그대로입니다.", Theme.Muted); exportBtn.Focus(); return; }
        if (SamePath(target, input)) { Say("원본과 같은 경로에는 저장할 수 없습니다. 다른 이름을 선택하세요.", Theme.Warn); return; }
        try {
            var r = await RunStaged(mode, mode + " " + Worker.Quote(input), target, name + " → " + formatMenu.LabelOf(mode));
            int count = Directory.Exists(r.Item1) ? Directory.GetFiles(r.Item1, "*", SearchOption.AllDirectories).Length : 1;
            string zipNote = mode == "json" ? await MakeZip(r.Item1, name) : "";
            string renamed = SamePath(r.Item1, target) ? "" : " (같은 이름이 있어 새 이름으로 저장)";
            if (job == jobId) { await ShowResult(r.Item1, r.Item2, name); tabResult.Enabled = true; Say(String.Format("저장 완료 · {0} · 파일 {1}개 · {2}{3}{4} — ‘결과 보기’에서 확인", name, count, r.Item1, renamed, zipNote), zipNote.StartsWith(" · 압축 실패") ? Theme.Warn : Theme.Ok); }
            else Say(String.Format("이전 문서 {0}의 내보내기 완료 · 파일 {1}개 · {2}{3} (현재 문서 화면은 바꾸지 않았습니다)", name, count, r.Item1, renamed), Theme.Ok);
        } catch (OperationCanceledException) { Say("내보내기를 취소했습니다. 원본과 기존 결과는 그대로입니다.", Theme.Muted); }
        catch (Exception ex) { Say("내보내기 실패 · " + name + " · " + ex.Message, Theme.Warn); }
    }

    async Task Convert(string fmt) {
        if (resultJson == null || exportProc != null) return;
        string stem = Path.GetFileName(Path.GetDirectoryName(resultJson));
        string target = AskTarget(fmt, stem, "-변환");
        if (target == null) { Say("변환을 취소했습니다.", Theme.Muted); return; }
        try {
            var r = await RunStaged("convert " + fmt, "convert " + Worker.Quote(resultJson), target, "기존 결과 → " + fmt.ToUpperInvariant());
            await ShowResult(r.Item1, r.Item2, stem); Say("변환 완료 · " + r.Item1, Theme.Ok);
        } catch (OperationCanceledException) { Say("변환을 취소했습니다.", Theme.Muted); }
        catch (Exception ex) { Say("변환 실패 · " + ex.Message, Theme.Warn); }
    }

    // ── result view ────────────────────────────────────────────────────────────────────
    async Task LoadResult() {
        using (var d = new OpenFileDialog { Title = "기존 결과 불러오기", Filter = "GovParse 결과 (document.json, *.html, *.hwpx, *.hwp)|*.json;*.html;*.htm;*.hwpx;*.hwp" }) {
            if (d.ShowDialog(this) != DialogResult.OK) return;
            try { await ShowResult(d.FileName, null, null); tabResult.Enabled = true; SwitchTab(true); Say("기존 결과를 불러왔습니다 · " + d.FileName, Theme.Ok); }
            catch (Exception ex) { Say("결과를 불러오지 못했습니다 · " + ex.Message, Theme.Warn); }
        }
    }
    void SetResState(bool saved, string preview, bool previewOk) {
        resState.Text = (saved ? "파일 저장: 성공" : "파일 저장: 확인 필요") + "\n" + preview;
        resState.ForeColor = saved && previewOk ? Theme.Ok : Theme.Warn;
    }
    async Task ShowResult(string path, Dictionary<string, object> rep, string sourceName) {
        string file = path, kind;
        if (Directory.Exists(path)) { file = File.Exists(Path.Combine(path, "document.html")) ? Path.Combine(path, "document.html") : Path.Combine(path, "document.json"); }
        string ext = Path.GetExtension(file).ToLowerInvariant();
        kind = ext == ".json" ? "json" : (ext == ".html" || ext == ".htm") ? "html" : "pages";
        var notes = new List<string>(); bool saved = File.Exists(file);
        if (rep != null) {
            if (rep.ContainsKey("notes")) notes.AddRange(((ArrayList)rep["notes"]).Cast<object>().Select(o => "· " + o));
            if (rep.ContainsKey("restore")) { var r = (Dictionary<string, object>)rep["restore"]; notes.Add(String.Format("· 그림 {0}개 복원 · 원문 캡션 {1}개 · 사용자 연결 {2}건은 .links.json", r["pictures"], r["source_captions"], r["user_links_sidecar"])); }
            if (rep.ContainsKey("bundle")) { var bundle = (Dictionary<string, object>)rep["bundle"]; var b = (Dictionary<string, object>)bundle["counts"]; bool perTable = bundle.ContainsKey("table_images") && bundle["table_images"] != null;
                notes.Add(perTable ? String.Format("· 내장 그림 {0}개 · 표 이미지 {1}장", b["embedded_picture"], b["table_image"])
                                   : String.Format("· 내장 그림 {0}개 · 표 캡처 {1}", b["embedded_picture"], b["rendered_table_capture"] == null ? "미확인" : b["rendered_table_capture"] + "장")); }
            if (rep.ContainsKey("ir_builder")) notes.Add("· 구조 읽기: " + rep["ir_builder"]);
        } else notes.Add("· 기존 결과를 불러왔습니다 (이번 실행에서 만든 결과가 아님)");
        resultPath = path; resultKind = kind; resultJson = kind == "json" ? file : null;
        if (sourceName == null) sourceName = SourceNameOf(path);   // a loaded result: the source name is in its report.json
        resTitle.Text = (sourceName != null ? sourceName + " → " : "") + Path.GetFileName(path);
        resNotes.Text = String.Join("\r\n", notes);
        web.Visible = kind == "html"; assetPic.Visible = kind == "json"; resultPages.Visible = kind == "pages";
        if (tableMode) { tableMode = false; tables.Visible = false; resNotes.Visible = actions.Visible = true; }   // a new result leaves the mode
        tableBtn.SetText("표 고치기"); tableBtn.Enabled = false; tableBar.Visible = kind == "json"; tables.Load(null); picBox = null;
        assets.Visible = ocr.Visible = kind == "json"; assets.Items.Clear(); assetPic.Image = null;
        openExternal.SetText(kind == "html" ? "브라우저에서 열기" : kind == "pages" ? "한글에서 열기" : "JSON 열기");
        toHtml.Visible = toHwpx.Visible = kind == "json"; toHtml.Enabled = toHwpx.Enabled = kind == "json";
        openZip.Visible = ZipOf(path) != null;
        if (kind == "html") { SetResState(saved, "미리보기 여는 중…", false); web.Navigate(new Uri(file)); web.DocumentCompleted += (s, e) => { if (resultKind == "html") SetResState(true, "미리보기 성공 · 저장된 로컬 HTML을 표시", true); }; }
        else if (kind == "pages") { SetResState(saved, "미리보기 준비 중 · 저장된 결과 파일을 다시 그리는 중", false); var _ = resultPages.Load(file); }
        else {
            SetResState(saved, "자산 목록 읽는 중…", false);
            string o = await Task.Run(() => worker.Run("index " + Worker.Quote(file), 600000));
            var idx = json.Deserialize<Dictionary<string, object>>(o);
            string src = idx.ContainsKey("source_name") && idx["source_name"] != null ? (string)idx["source_name"] : "";
            foreach (Dictionary<string, object> a in (ArrayList)idx["assets"]) {
                string origin = (string)a["origin"];
                string txt = a.ContainsKey("text") && a["text"] != null ? (string)a["text"] : "";
                string kindText, info;
                if (origin == "embedded_picture") { kindText = "내장 그림"; info = txt.Length > 0 ? txt : "문서에 들어 있던 그림"; }
                else if (origin == "table_image") { kindText = "표 이미지"; info = txt; }
                else if (origin == "table_source_page") { kindText = "원본 쪽"; info = txt; }
                else { kindText = "표 캡처"; info = "미리보기 " + a["page"] + "쪽의 표 영역 (어느 표인지는 확인되지 않음)"; }
                if ((origin == "table_image" || origin == "table_source_page") && a.ContainsKey("table_type_label") && a["table_type_label"] != null)
                    info = String.IsNullOrEmpty(info) ? (string)a["table_type_label"] : info + " · " + a["table_type_label"];
                assets.Items.Add(new ListViewItem(new[] { Path.GetFileName((string)a["path"]), kindText, info, src }) { Tag = a });
            }
            // tables without an image stay visible with the reason, instead of being filled with a similar-looking capture
            var ti = idx.ContainsKey("table_images") ? idx["table_images"] as Dictionary<string, object> : null;
            if (ti != null) {
                resNotes.Text += String.Format("\r\n· 표 {0}개 중 {1}개 이미지 생성 · PNG {2}장", ti["tables"], ti["rendered"], ti["png"]);
                if (ti.ContainsKey("source_page_tables") && System.Convert.ToInt32(ti["source_page_tables"]) > 0)
                    resNotes.Text += " · 표 " + ti["source_page_tables"] + "개는 원본 쪽으로 표시";
                if (((ArrayList)ti["missing"]).Count > 0) resNotes.Text += " · 미생성 " + ((ArrayList)ti["missing"]).Count + "개는 목록 끝에 사유 표시";
                foreach (Dictionary<string, object> m in (ArrayList)ti["missing"])
                    assets.Items.Add(new ListViewItem(new[] { "(이미지 없음)", "표 이미지 미생성", m["label"] + " · " + m["reason_text"], src }) { Tag = m, ForeColor = Theme.Warn });
            }
            int missing = ((ArrayList)idx["missing"]).Count;
            var integ = idx["integrity"] as Dictionary<string, object>;
            if (integ == null) resNotes.Text += "\r\n· 저장 후 변경 여부: 확인할 수 없음 (검증 목록이 없는 결과)";
            else {
                int changed = ((ArrayList)integ["changed"]).Count, gone = ((ArrayList)integ["missing"]).Count;
                resNotes.Text += changed + gone == 0 ? "\r\n· 저장 후 변경 없음 (파일 " + integ["checked"] + "개 해시 확인)" : "\r\n· 주의: 저장 후 바뀐 파일 " + changed + "개, 없어진 파일 " + gone + "개";
                missing += changed;
            }
            SetResState(saved, String.Format("미리보기 성공 · 자산 {0}개{1}", assets.Items.Count, missing > 0 ? " · 파일 없는 자산 " + missing + "개" : assets.Items.Count == 0 ? " (추출된 PNG 0개)" : ""), missing == 0);
            tables.Load((ArrayList)idx["assets"], idx.ContainsKey("can_read_table_images") && true.Equals(idx["can_read_table_images"]));
            tableBtn.Enabled = tables.Count > 0;
            tableTip.SetToolTip(tableBtn, tables.Count > 0 ? "표를 확인하고 고칩니다 (원본 칸 또는 이미지에서 읽은 표)" : "이 결과에는 표 이미지가 없습니다");
            if (assets.Items.Count > 0) assets.Items[0].Selected = true;
        }
        Refresh2();
    }
    // picture file of the selected result row; null for a table that has no image
    string SelectedAssetFile() {
        if (assets.SelectedItems.Count == 0) return null;
        var t = (Dictionary<string, object>)assets.SelectedItems[0].Tag;
        return t.ContainsKey("file") ? (string)t["file"] : null;
    }
    void SelectAsset() {
        if (assets.SelectedItems.Count == 0) return;
        var a = (Dictionary<string, object>)assets.SelectedItems[0].Tag;
        if (!a.ContainsKey("file")) { var old0 = assetPic.Image; assetPic.Image = null; if (old0 != null) old0.Dispose(); ocr.Show(null); return; }   // table without an image
        try { using (var f = Image.FromFile((string)a["file"])) { var old = assetPic.Image; var bmp = new Bitmap(f); assetPic.Image = bmp; assetPic.Tag = a["file"]; FitAssetPic(); if (old != null) old.Dispose(); } } catch { assetPic.Image = null; assetPic.Tag = null; }
        string st = (string)a["ocr_status"];
        ocr.Show(st == "ok" || st == "empty" ? ((ArrayList)a["ocr_texts"]).Cast<string>().ToArray() : null);
    }
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr hWnd, int cmd);
    // Explorer hands "/select" to the running shell process, which may open behind this window.
    // We are the foreground app, so we may bring that new Explorer window to the front ourselves.
    // <folder>.zip made next to a JSON result folder for sending it on; null when there is none.
    static string ZipOf(string resultFolder) {
        if (String.IsNullOrEmpty(resultFolder) || !Directory.Exists(resultFolder)) return null;
        string z = resultFolder.TrimEnd('\\', '/') + ".zip";
        return File.Exists(z) ? z : null;
    }
    // Packs a finished JSON result folder into one .zip beside it. The folder stays (the app reads it);
    // a failed or cancelled zip never fails the export. Returns a note for the status line.
    async Task<string> MakeZip(string folder, string name) {
        string target = folder.TrimEnd('\\', '/') + ".zip";
        exportStart = DateTime.Now; exportStep = name + " → 압축 파일"; exportStage = ""; stageDone = -1; stageTotal = 0; UpdateStrip();
        SetExporting(true); Say("압축 파일 만드는 중 · " + Path.GetFileName(target) + "  (Esc로 취소해도 결과 폴더는 그대로)", Theme.Muted);
        try {
            string o = await Task.Run(() => worker.Run("zip " + Worker.Quote(folder) + " " + Worker.Quote(target), 3600000, p => exportProc = p, OnProgress));
            var z = json.Deserialize<Dictionary<string, object>>(o);
            return String.Format(" · 압축 파일 {0} ({1:0.0}MB)", Path.GetFileName((string)z["zip"]), System.Convert.ToDouble(z["bytes"]) / 1048576.0);
        } catch (OperationCanceledException) { return " · 압축은 취소함 (결과 폴더는 저장됨)"; }
        catch (Exception ex) { return " · 압축 실패: " + ex.Message + " (결과 폴더는 저장됨)"; }
        finally { exportProc = null; exportStart = null; UpdateStrip(); SetExporting(false); }
    }
    async Task ShowInExplorer(string file) {
        Launch("explorer.exe", "/select,\"" + file + "\"");
        string folder = new Uri(Path.GetDirectoryName(file) + Path.DirectorySeparatorChar).AbsoluteUri.TrimEnd('/');
        for (int i = 0; i < 20; i++) {
            await Task.Delay(150);
            try {
                dynamic shell = Activator.CreateInstance(Type.GetTypeFromProgID("Shell.Application"));
                foreach (dynamic w in shell.Windows()) {
                    string url = ((string)w.LocationURL ?? "").TrimEnd('/');
                    if (String.Equals(url, folder, StringComparison.OrdinalIgnoreCase)) { var h = new IntPtr((long)w.HWND); ShowWindow(h, 9); SetForegroundWindow(h); return; }
                }
            } catch { }
        }
    }
    // Source file name recorded in the result's report.json (next to the file, or inside the folder); null when absent.
    string SourceNameOf(string path) {
        try {
            string dir = Directory.Exists(path) ? path : Path.GetDirectoryName(path);
            string rp = Path.Combine(dir, "report.json");
            if (!File.Exists(rp)) return null;
            var rep = json.Deserialize<Dictionary<string, object>>(File.ReadAllText(rp, Encoding.UTF8));
            string src = rep.ContainsKey("source") ? rep["source"] as string : null;
            return String.IsNullOrEmpty(src) ? null : Path.GetFileName(src);
        } catch { return null; }
    }
    // 표 고치기 on/off: the asset list, notes, OCR box and actions give way to the table list and grid
    void ToggleTables() {
        tableMode = !tableMode;
        assets.Visible = ocr.Visible = resNotes.Visible = actions.Visible = !tableMode;
        tables.Visible = tableMode;
        tableBtn.SetText(tableMode ? "표 고치기 닫기" : "표 고치기");
        if (tableMode) tables.ShowCurrent();
        else {
            picBox = null; SelectAsset();
            int n = tables.Unsaved;
            if (n > 0) Say("저장하지 않은 표 " + n + "개가 있습니다 · ‘표 고치기’에서 저장하세요", Theme.Warn);
        }
    }
    // picture for the table panel; box = selected cell in that picture's pixels (null: no outline)
    void ShowPic(string file, RectangleF? box) {
        picBox = box;
        if (file == null || !File.Exists(file)) { var o = assetPic.Image; assetPic.Image = null; if (o != null) o.Dispose(); return; }
        if (assetPic.Tag as string != file || assetPic.Image == null) {
            try { using (var f = Image.FromFile(file)) { var old = assetPic.Image; assetPic.Image = new Bitmap(f); assetPic.Tag = file; FitAssetPic(); if (old != null) old.Dispose(); } }
            catch { assetPic.Image = null; assetPic.Tag = null; }
        }
        assetPic.Invalidate();
    }
    void PaintPicBox(Graphics g) {
        var img = assetPic.Image; if (!tableMode || picBox == null || img == null) return;
        float k, ox, oy, cw = assetPic.ClientSize.Width, ch = assetPic.ClientSize.Height;
        if (assetPic.SizeMode == PictureBoxSizeMode.Zoom) k = Math.Min(cw / img.Width, ch / img.Height); else k = 1f;
        ox = (cw - img.Width * k) / 2; oy = (ch - img.Height * k) / 2;
        var b = picBox.Value;
        using (var pen = new Pen(Theme.Primary, Math.Max(2f, Theme.Px(2))))
            g.DrawRectangle(pen, ox + b.X * k, oy + b.Y * k, b.Width * k, b.Height * k);
    }
    void FitAssetPic() {
        var b = assetPic.Image; if (b == null) return;
        assetPic.SizeMode = b.Width <= assetPic.ClientSize.Width && b.Height <= assetPic.ClientSize.Height ? PictureBoxSizeMode.CenterImage : PictureBoxSizeMode.Zoom;
    }
    void OpenExternal() {
        if (resultPath == null) return;
        string f = Directory.Exists(resultPath) ? Path.Combine(resultPath, resultKind == "html" ? "document.html" : "document.json") : resultPath;
        try { Launch(f); } catch (Exception ex) { Say("열 수 없습니다 · " + ex.Message, Theme.Warn); }   // opens in the user's own app; never closed by this app
    }

    [STAThread] static void Main() {
        try { SetProcessDPIAware(); } catch { }
        Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
        using (var g = Graphics.FromHwnd(IntPtr.Zero)) Theme.Scale = g.DpiX / 96f;
        Application.Run(new Desktop());
    }
}
