# Hancom COM from the deployed app (no pywin32 in the bundled runtime).
# usage: powershell -NoProfile -ExecutionPolicy Bypass -File hancom.ps1 -Mode save|verify -Src <path> [-Dst <path>] [-Format PDF|HWP|HWPX]
# Prints "pages=N" on success. Closes only the automation instance it created.
param([string]$Mode, [string]$Src, [string]$Dst = "", [string]$Format = "PDF")
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
function Get-Automation { @(Get-CimInstance Win32_Process -Filter "Name='Hwp.exe'" | Where-Object { $_.CommandLine -match '-Automation' } | ForEach-Object { $_.ProcessId }) }
$before = Get-Automation
$hwp = $null
try {
    $hwp = New-Object -ComObject "HWPFrame.HwpObject"
    try { [void]$hwp.RegisterModule("FilePathCheckDLL", "FilePathCheckerModule") } catch {}
    try { [void]$hwp.SetMessageBoxMode(0x00020000) } catch {}
    try { $hwp.XHwpWindows.Item(0).Visible = $false } catch {}
    $fmt = ""
    if ($Src -match '\.html?$') { $fmt = "HTML" }
    if (-not $hwp.Open($Src, $fmt, "forceopen:true;versionwarning:false;suspendpassword:true")) { throw "Open 실패" }
    $pages = 0; try { $pages = [int]$hwp.PageCount } catch {}
    if ($Mode -eq "save") {
        if (-not $hwp.SaveAs($Dst, $Format, "")) { throw "SaveAs $Format 실패" }
        if (-not (Test-Path -LiteralPath $Dst) -or (Get-Item -LiteralPath $Dst).Length -eq 0) { throw "결과 파일 미생성" }
    }
    Write-Output "pages=$pages"
    exit 0
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
} finally {
    if ($hwp -ne $null) {
        try { [void]$hwp.Clear(1) } catch {}
        try { [void]$hwp.Quit() } catch {}
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($hwp)
    }
    Start-Sleep -Milliseconds 500
    foreach ($id in (Get-Automation)) { if ($before -notcontains $id) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue } }
}
