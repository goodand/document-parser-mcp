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
    string chosen;
    public string SavedLabel;

    public SettingsForm(Worker worker) {
        this.worker = worker;
        Text = "설정"; Font = Theme.Body; BackColor = Theme.Surface; ForeColor = Theme.Text;
        StartPosition = FormStartPosition.CenterParent; FormBorderStyle = FormBorderStyle.FixedDialog; MaximizeBox = MinimizeBox = false; ShowInTaskbar = false;
        ClientSize = new Size(Theme.Px(560), Theme.Px(380)); KeyPreview = true;
        var title = new Label { Dock = DockStyle.Top, Height = Theme.Px(56), Padding = new Padding(14, 12, 14, 0), Font = Theme.Title,
            Text = "OCR 엔진", BackColor = Theme.Surface };
        var sub = new Label { Dock = DockStyle.Top, Height = Theme.Px(46), Padding = new Padding(14, 0, 14, 0), ForeColor = Theme.Muted,
            Text = "‘이 그림 OCR’을 누를 때 그림 속 글자를 읽는 방법입니다. 쓸 수 없는 엔진은 이유와 함께 흐리게 표시됩니다." };
        var buttons = new FlowLayoutPanel { Dock = DockStyle.Bottom, Height = Theme.Px(52), FlowDirection = FlowDirection.RightToLeft, Padding = new Padding(10, 8, 10, 0), BackColor = Theme.Surface };
        buttons.Controls.Add(cancel); buttons.Controls.Add(save);
        cancel.Click += (s, e) => Close(); save.Click += async (s, e) => await Save();
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
            save.Enabled = true; save.Focus();
        } catch (Exception ex) { engines.Controls.Clear(); engines.Controls.Add(new Label { AutoSize = true, ForeColor = Theme.Warn, Text = "설정을 읽지 못했습니다 · " + ex.Message }); }
    }

    async Task Save() {
        if (chosen == null) return;
        save.Enabled = false;
        try {
            var d = json.Deserialize<Dictionary<string, object>>(await Task.Run(() => worker.Run("settings-set ocr_engine " + chosen, 60000)));
            var eng = ((ArrayList)d["engines"]).Cast<Dictionary<string, object>>().First(x => (string)x["id"] == chosen);
            SavedLabel = (string)eng["label"]; DialogResult = DialogResult.OK; Close();
        } catch (Exception ex) { save.Enabled = true; where.ForeColor = Theme.Warn; where.Text = "저장하지 못했습니다 · " + ex.Message; }
    }
}
