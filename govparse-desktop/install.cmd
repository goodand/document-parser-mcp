@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -Command "$ErrorActionPreference='Stop'; $parent=Join-Path $env:LOCALAPPDATA 'GovParse Desktop'; $dest=Join-Path $parent '0.1.0'; $tmp=Join-Path $parent ([guid]::NewGuid().ToString()); $zip=Join-Path (Get-Location) 'app.zip'; if(!(Test-Path -LiteralPath $zip)){throw 'app.zip missing'}; if(!(Test-Path -LiteralPath $dest)){New-Item -ItemType Directory -Path $tmp -Force | Out-Null; Add-Type -AssemblyName System.IO.Compression.FileSystem; [IO.Compression.ZipFile]::ExtractToDirectory($zip,$tmp); Move-Item -LiteralPath $tmp -Destination $dest}; $exe=Join-Path $dest 'GovParse Desktop.exe'; if(!(Test-Path -LiteralPath $exe)){throw 'Application missing'}; $menu=[Environment]::GetFolderPath('Programs'); $w=New-Object -ComObject WScript.Shell; $l=$w.CreateShortcut((Join-Path $menu 'GovParse Desktop.lnk')); $l.TargetPath=$exe; $l.WorkingDirectory=$dest; $l.Save(); Start-Process -FilePath $exe"
if errorlevel 1 pause
