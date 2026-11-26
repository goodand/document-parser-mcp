using System;
using System.Collections;
using System.Collections.Generic;
using System.Drawing;
using System.Linq;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

// Settings window. Today: which OCR engine reads a picture. Settings live in the per-user
// settings.json the worker manages (Windows %LOCALAPPDATA%, macOS ~/Library/Application Support).
class SettingsForm : Form {
    readonly Worker worker;
    readonly JavaScriptSerializer json = new JavaScriptSerializer();
    readonly FlowLayoutPanel engines = new FlowLayoutPanel { Dock = DockStyle.Fill, FlowDirection = FlowDirection.TopDown, WrapContents = false, AutoScroll = true, Padding = new Padding(14, 4, 14, 4), BackColor = Theme.Surface };
    readonly Label where = new Label { Dock = DockStyle.Bottom, Height = 40, Padding = new Padding(14, 4, 14, 0), ForeColor = Theme.Muted };
    readonly AxButton save = new AxButton("저장", AxKind.Primary), cancel = new AxButton("취소", AxKind.Secondary);
    string chosen, chosenPreview, startPreview;
    public string SavedLabel, SavedPreviewLabel;
    public bool PreviewChanged { get { return chosenPreview != startPreview; } }

    public SettingsForm(Worker worker) {
        this.worker = worker;
        Text = "설정"; Font = Theme.Body; BackColor = Theme.Surface; ForeColor = Theme.Text;
        StartPosition = FormStartPosition.CenterParent; FormBorderStyle = FormBorderStyle.FixedDialog; MaximizeBox = MinimizeBox = false; ShowInTaskbar = false;
        ClientSize = new Size(Theme.Px(560), Theme.Px(560)); KeyPreview = true;
        var title = new Label { Dock = DockStyle.Top, Height = Theme.Px(56), Padding = new Padding(14, 12, 14, 0), Font = Theme.Title,
            Text = "OCR 엔진", BackColor = Theme.Surface };
        var sub = new Label { Dock = DockStyle.Top, Height = Theme.Px(46), Padding = new Padding(14, 0, 14, 0), ForeColor = Theme.Muted,
            Text = "‘이 그림 OCR’을 누를 때 그림 속 글자를 읽는 방법입니다. 쓸 수 없는 엔진은 이유와 함께 흐리게 표시됩니다." };
        var buttons = new FlowLayoutPanel { Dock = DockStyle.Bottom, Height = Theme.Px(52), FlowDirection = FlowDirection.RightToLeft, Padding = new Padding(10, 8, 10, 0), BackColor = Theme.Surface };
        buttons.Controls.Add(cancel); buttons.Controls.Add(save);
        cancel.Click += (s, e) => Close(); save.Click += async (s, e) => await Save();
        // installed copy only: <root>\app\GovParse Desktop.exe with <root>\uninstall.exe beside the app folder
        string uninstaller = System.IO.Path.Combine(System.IO.Path.GetDirectoryName(Application.StartupPath) ?? "", "uninstall.exe");
        if (System.IO.File.Exists(uninstaller)) {
            var remove = new AxButton("프로그램 제거…", AxKind.Ghost) { ForeColor = Theme.Warn, Margin = new Padding(0, 0, Theme.Px(150), 0) };
            remove.Click += (s, e) => Remove(uninstaller);
            buttons.Controls.Add(remove);
        }
        KeyDown += (s, e) => { if (e.KeyCode == Keys.Escape) Close(); };
        Controls.Add(engines); Controls.Add(where); Controls.Add(buttons); Controls.Add(sub); Controls.Add(title);
        engines.Controls.Add(new Label { AutoSize = true, Text = "엔진 목록을 확인하는 중…", ForeColor = Theme.Muted });
        save.Enabled = false;
        Shown += async (s, e) => await Load2();
    }

    async Task Load2() {
        try {
            var d = json.Deserialize<Dictionary<string, object>>(await Task.Run(() => worker.Run("settings", 60000)));
            chosen = (string)((Dictionary<string, object>)d["settings"])["ocr_engine"];
            where.Text = "설정 파일: " + d["path"];
            engines.Controls.Clear();
            foreach (Dictionary<string, object> e in (ArrayList)d["engines"]) {
                bool ok = (bool)e["available"]; string id = (string)e["id"];
                var rb = new RadioButton { AutoSize = true, Text = (string)e["label"], Font = Theme.Strong, Enabled = ok, Checked = id == chosen, Tag = id, Margin = new Padding(0, 10, 0, 0) };
                rb.CheckedChanged += (s, ev) => { if (rb.Checked) chosen = (string)rb.Tag; };
                var desc = new Label { AutoSize = true, MaximumSize = new Size(Theme.Px(500), 0), Margin = new Padding(Theme.Px(20), 0, 0, 0), ForeColor = ok ? Theme.Muted : Color.FromArgb(160, 168, 178),
                    Text = (string)e["description"] + (ok ? "" : "\n사용할 수 없음 · " + e["reason"]) };
                engines.Controls.Add(rb); engines.Controls.Add(desc);
            }
            // 미리보기 방식: separate radio group (own container so it does not share checked state with OCR)
            chosenPreview = startPreview = (string)((Dictionary<string, object>)d["settings"])["preview"];
            engines.Controls.Add(new Label { AutoSize = true, Font = Theme.Title, Text = "미리보기 방식", Margin = new Padding(0, Theme.Px(18), 0, 0) });
            engines.Controls.Add(new Label { AutoSize = true, MaximumSize = new Size(Theme.Px(500), 0), ForeColor = Theme.Muted, Text = "원본 보기 화면에서 쪽을 그리는 방법입니다." });
            var group = new FlowLayoutPanel { AutoSize = true, FlowDirection = FlowDirection.TopDown, WrapContents = false, Margin = Padding.Empty, BackColor = Theme.Surface };
            foreach (Dictionary<string, object> p in (ArrayList)d["previews"]) {
                bool ok = (bool)p["available"]; string id = (string)p["id"];
                var rb = new RadioButton { AutoSize = true, Text = (string)p["label"], Font = Theme.Strong, Enabled = ok, Checked = id == chosenPreview, Tag = p["label"], Name = "preview_" + id, Margin = new Padding(0, 10, 0, 0) };
                rb.CheckedChanged += (s, ev) => { if (rb.Checked) chosenPreview = rb.Name.Substring(8); };
                if (id == chosenPreview) SavedPreviewLabel = (string)p["label"];
                var desc = new Label { AutoSize = true, MaximumSize = new Size(Theme.Px(500), 0), Margin = new Padding(Theme.Px(20), 0, 0, 0), ForeColor = ok ? Theme.Muted : Color.FromArgb(160, 168, 178),
                    Text = (string)p["description"] + (ok ? "" : "\n사용할 수 없음 · " + p["reason"]) };
                group.Controls.Add(rb); group.Controls.Add(desc);
            }
            engines.Controls.Add(group);
            save.Enabled = true; save.Focus();
        } catch (Exception ex) { engines.Controls.Clear(); engines.Controls.Add(new Label { AutoSize = true, ForeColor = Theme.Warn, Text = "설정을 읽지 못했습니다 · " + ex.Message }); }
    }

    void Remove(string uninstaller) {
        var msg = "GovParse Desktop을 이 PC에서 제거합니다.\n\n" + Application.StartupPath +
                  "\n\n프로그램이 닫힌 뒤 제거됩니다. 내보낸 결과 파일, 설정, 그림 설명 기록은 지우지 않습니다.\n계속할까요?";
        if (MessageBox.Show(this, msg, "프로그램 제거", MessageBoxButtons.YesNo, MessageBoxIcon.Warning, MessageBoxDefaultButton.Button2) != DialogResult.Yes) return;
        try {
            System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(uninstaller, "--uninstall --from-app") { UseShellExecute = false });
        } catch (Exception ex) {
            where.ForeColor = Theme.Warn; where.Text = "제거 프로그램을 시작하지 못했습니다 · " + ex.Message; return;
        }
        Application.Exit();
    }

    async Task Save() {
        if (chosen == null) return;
        save.Enabled = false;
        try {
            var d = json.Deserialize<Dictionary<string, object>>(await Task.Run(() => worker.Run("settings-set ocr_engine " + chosen, 60000)));
            if (chosenPreview != null && chosenPreview != startPreview)
                d = json.Deserialize<Dictionary<string, object>>(await Task.Run(() => worker.Run("settings-set preview " + chosenPreview, 60000)));
            var eng = ((ArrayList)d["engines"]).Cast<Dictionary<string, object>>().First(x => (string)x["id"] == chosen);
            SavedLabel = (string)eng["label"];
            var pv = ((ArrayList)d["previews"]).Cast<Dictionary<string, object>>().FirstOrDefault(x => (string)x["id"] == chosenPreview);
            if (pv != null) SavedPreviewLabel = (string)pv["label"];
            DialogResult = DialogResult.OK; Close();
        } catch (Exception ex) { save.Enabled = true; where.ForeColor = Theme.Warn; where.Text = "저장하지 못했습니다 · " + ex.Message; }
    }
}
