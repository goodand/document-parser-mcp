using System;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Windows.Forms;
using System.Diagnostics;
using Microsoft.Win32;
using System.Collections.Generic;

// Per-user installer: no administrator rights. Installs to %LOCALAPPDATA%\GovParse Desktop\app,
// replaces an earlier install (the user's settings and figure decisions live elsewhere and are kept),
// adds Start-menu and desktop shortcuts, and registers an uninstaller under Settings > Apps.
//   Setup.exe                 install (asks first)
//   Setup.exe --extract DIR   unpack only (build test)
//   Setup.exe --uninstall     remove (also what Settings > Apps runs)
class Setup {
    const string Name = "GovParse Desktop", Version = "0.2.0";
    const string UninstallKey = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\GovParseDesktop";
    static string Root { get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), Name); } }
    static string AppDir { get { return Path.Combine(Root, "app"); } }
    static string MenuLink { get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), Name + ".lnk"); } }
    static string DesktopLink { get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), Name + ".lnk"); } }

    sealed class SavedRegistryValue {
        public object Value;
        public RegistryValueKind Kind;
    }

    static Dictionary<string, SavedRegistryValue> SaveUninstallEntry() {
        using (var existing = Registry.CurrentUser.OpenSubKey(UninstallKey)) {
            if (existing == null) return null;
            var saved = new Dictionary<string, SavedRegistryValue>();
            foreach (var name in existing.GetValueNames()) {
                saved[name] = new SavedRegistryValue {
                    Value = existing.GetValue(name, null, RegistryValueOptions.DoNotExpandEnvironmentNames),
                    Kind = existing.GetValueKind(name)
                };
            }
            return saved;
        }
    }

    static void RestoreUninstallEntry(Dictionary<string, SavedRegistryValue> saved) {
        Registry.CurrentUser.DeleteSubKeyTree(UninstallKey, false);
        if (saved == null) return;
        using (var key = Registry.CurrentUser.CreateSubKey(UninstallKey)) {
            foreach (var pair in saved) key.SetValue(pair.Key, pair.Value.Value, pair.Value.Kind);
        }
    }

    static void RestoreFile(string path, string backup, bool existed) {
        if (existed) {
            if (!File.Exists(backup)) throw new IOException("이전 파일 백업을 찾을 수 없습니다: " + backup);
            File.Copy(backup, path, true);
        } else if (File.Exists(path)) {
            File.Delete(path);
        }
    }

    [STAThread] static int Main(string[] args) {
        try {
            if (args.Length == 2 && args[0] == "--extract") { Extract(Path.GetFullPath(args[1])); return 0; }
            if (args.Length >= 1 && args[0] == "--uninstall") return Uninstall(args.Length > 1 && args[1] == "--quiet");
            return Install();
        } catch (Exception e) { MessageBox.Show(e.Message, "설치 실패"); return 1; }
    }

    static void Extract(string dir) {
        using (var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("payload"))
        using (var archive = new ZipArchive(stream, ZipArchiveMode.Read)) archive.ExtractToDirectory(dir);
    }

    static bool Running() {
        foreach (var p in Process.GetProcessesByName(Name)) {
            try { if (p.MainModule.FileName.StartsWith(AppDir, StringComparison.OrdinalIgnoreCase)) return true; } catch { }
        }
        return false;
    }

    static int Install() {
        bool upgrade = File.Exists(Path.Combine(AppDir, Name + ".exe"));
        string msg = (upgrade ? "기존 GovParse Desktop을 새 버전(" + Version + ")으로 바꿉니다." : "GovParse Desktop " + Version + "을 설치합니다.")
            + "\n\n설치 위치: " + AppDir + "\n관리자 권한은 필요하지 않습니다. 설정과 그림 설명 기록은 그대로 유지됩니다.";
        if (MessageBox.Show(msg, "GovParse Desktop 설치", MessageBoxButtons.OKCancel, MessageBoxIcon.Information) != DialogResult.OK) return 0;
        while (Running()) {
            if (MessageBox.Show("GovParse Desktop이 실행 중입니다. 프로그램을 닫은 뒤 다시 시도를 누르세요.", "설치", MessageBoxButtons.RetryCancel, MessageBoxIcon.Warning) != DialogResult.Retry) return 0;
        }
        Directory.CreateDirectory(Root);
        string fresh = Path.Combine(Root, "app-new-" + Guid.NewGuid().ToString("N").Substring(0, 8));
        string old = Path.Combine(Root, "app-old-" + Guid.NewGuid().ToString("N").Substring(0, 8));
        string self = Path.Combine(Root, "uninstall.exe");
        string selfStage = Path.Combine(Root, "uninstall-new-" + Guid.NewGuid().ToString("N") + ".exe");
        string selfBackup = Path.Combine(Root, "uninstall-old-" + Guid.NewGuid().ToString("N") + ".exe");
        string menuBackup = Path.Combine(Root, "menu-link-old-" + Guid.NewGuid().ToString("N") + ".lnk");
        string desktopBackup = Path.Combine(Root, "desktop-link-old-" + Guid.NewGuid().ToString("N") + ".lnk");
        var registryBackup = SaveUninstallEntry();
        bool hadSelf = File.Exists(self), hadMenuLink = File.Exists(MenuLink), hadDesktopLink = File.Exists(DesktopLink);
        bool hadOld = Directory.Exists(AppDir), oldMoved = false, newInstalled = false;
        if (hadSelf) File.Copy(self, selfBackup, false);
        if (hadMenuLink) File.Copy(MenuLink, menuBackup, false);
        if (hadDesktopLink) File.Copy(DesktopLink, desktopBackup, false);
        try {
            Extract(fresh);                                   // unpack fully before touching the current install
            if (hadOld) { Directory.Move(AppDir, old); oldMoved = true; }
            Directory.Move(fresh, AppDir);
            newInstalled = true;
            string exe0 = Path.Combine(AppDir, Name + ".exe");
            Shortcut(MenuLink, exe0); Shortcut(DesktopLink, exe0);
            File.Copy(Assembly.GetExecutingAssembly().Location, selfStage, true);
            File.Copy(selfStage, self, true);
            using (var k = Registry.CurrentUser.CreateSubKey(UninstallKey)) {
                k.SetValue("DisplayName", Name); k.SetValue("DisplayVersion", Version); k.SetValue("Publisher", Name);
                k.SetValue("InstallLocation", AppDir); k.SetValue("DisplayIcon", exe0);
                k.SetValue("UninstallString", "\"" + self + "\" --uninstall");
                k.SetValue("NoModify", 1, RegistryValueKind.DWord); k.SetValue("NoRepair", 1, RegistryValueKind.DWord);
            }
        } catch (Exception e) {
            var rollbackErrors = new List<string>();
            try { RestoreUninstallEntry(registryBackup); } catch (Exception restore) { rollbackErrors.Add("레지스트리 복원 실패: " + restore.Message); }
            try { RestoreFile(self, selfBackup, hadSelf); } catch (Exception restore) { rollbackErrors.Add("제거 프로그램 복원 실패: " + restore.Message); }
            try { RestoreFile(MenuLink, menuBackup, hadMenuLink); } catch (Exception restore) { rollbackErrors.Add("시작 메뉴 바로 가기 복원 실패: " + restore.Message); }
            try { RestoreFile(DesktopLink, desktopBackup, hadDesktopLink); } catch (Exception restore) { rollbackErrors.Add("바탕 화면 바로 가기 복원 실패: " + restore.Message); }
            try {
                if (newInstalled && Directory.Exists(AppDir)) Directory.Delete(AppDir, true);
                if (oldMoved) Directory.Move(old, AppDir);
            } catch (Exception restore) { rollbackErrors.Add("이전 앱 복원 실패: " + restore.Message); }
            try { if (Directory.Exists(fresh)) Directory.Delete(fresh, true); } catch (Exception restore) { rollbackErrors.Add("임시 설치 폴더 정리 실패: " + restore.Message); }
            try { if (File.Exists(selfStage)) File.Delete(selfStage); } catch (Exception restore) { rollbackErrors.Add("임시 제거 프로그램 정리 실패: " + restore.Message); }
            string recovery = oldMoved && Directory.Exists(old) ? old : AppDir;
            if (rollbackErrors.Count > 0) {
                throw new IOException("설치에 실패했고 일부 복구 작업도 완료되지 않았습니다.\n이전 앱 백업 또는 복구 파일을 확인하세요:\n"
                    + recovery + "\n\n" + string.Join("\n", rollbackErrors.ToArray()) + "\n원인: " + e.Message, e);
            }
            try { if (File.Exists(selfBackup)) File.Delete(selfBackup); } catch { }
            try { if (File.Exists(menuBackup)) File.Delete(menuBackup); } catch { }
            try { if (File.Exists(desktopBackup)) File.Delete(desktopBackup); } catch { }
            throw new IOException("설치에 실패해 이전 상태로 되돌렸습니다.\n원인: " + e.Message, e);
        }
        // everything succeeded: only now is the previous version removed
        try { if (Directory.Exists(old)) Directory.Delete(old, true); } catch { }
        try { if (File.Exists(selfBackup)) File.Delete(selfBackup); } catch { }
        try { if (File.Exists(menuBackup)) File.Delete(menuBackup); } catch { }
        try { if (File.Exists(desktopBackup)) File.Delete(desktopBackup); } catch { }
        try { if (File.Exists(selfStage)) File.Delete(selfStage); } catch { }
        string exe = Path.Combine(AppDir, Name + ".exe");
        if (MessageBox.Show("설치가 완료되었습니다. 바탕 화면과 시작 메뉴에 바로 가기를 만들었습니다.\n지금 실행할까요?", Name, MessageBoxButtons.YesNo, MessageBoxIcon.Information) == DialogResult.Yes)
            Process.Start(exe);
        return 0;
    }

    static void Shortcut(string link, string exe) {
        dynamic shell = Activator.CreateInstance(Type.GetTypeFromProgID("WScript.Shell"));
        dynamic l = shell.CreateShortcut(link);
        l.TargetPath = exe; l.WorkingDirectory = Path.GetDirectoryName(exe); l.Save();
    }

    static int Uninstall(bool quiet) {
        if (!quiet && MessageBox.Show("GovParse Desktop을 제거합니다.\n\n" + AppDir + "\n\n내보낸 결과 파일, 설정, 그림 설명 기록은 지우지 않습니다.", "제거", MessageBoxButtons.OKCancel) != DialogResult.OK) return 0;
        while (Running()) {
            if (MessageBox.Show("GovParse Desktop이 실행 중입니다. 닫은 뒤 다시 시도를 누르세요.", "제거", MessageBoxButtons.RetryCancel) != DialogResult.Retry) return 0;
        }
        foreach (var l in new[] { MenuLink, DesktopLink }) try { if (File.Exists(l)) File.Delete(l); } catch { }
        if (Directory.Exists(AppDir)) Directory.Delete(AppDir, true);
        try { Registry.CurrentUser.DeleteSubKeyTree(UninstallKey, false); } catch { }
        // uninstall.exe cannot delete itself while running: remove it once this process has exited
        string self = Path.Combine(Root, "uninstall.exe");
        if (File.Exists(self))
            Process.Start(new ProcessStartInfo("cmd.exe", "/c ping -n 3 127.0.0.1 >nul & del /f /q \"" + self + "\"") { CreateNoWindow = true, UseShellExecute = false });
        if (!quiet) MessageBox.Show("제거했습니다.", "제거");
        return 0;
    }
}
