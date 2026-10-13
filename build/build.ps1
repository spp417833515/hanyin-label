# 一键构建: 图标 → exe (PyInstaller) → 安装包 (Inno Setup)
# 用法: 在 PowerShell 里运行  .\build\build.ps1
# 产物: build\output\汉印标签打印-安装-<版本>.exe
# 注意: PyInstaller 把进度信息写到 stderr, PowerShell 5.1 在 Stop 模式下会当成错误中断, 所以这里逐步检查退出码
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $here
try {
    if (-not (Test-Path '.venv\Scripts\python.exe')) {
        Write-Host '首次构建: 创建构建环境 .venv ...'
        python -m venv .venv
        .\.venv\Scripts\python.exe -m pip install -q pyinstaller pillow pywin32 qrcode
    }
    .\.venv\Scripts\python.exe make_icon.py
    if ($LASTEXITCODE -ne 0) { throw '生成图标失败' }

    Write-Host '编译 exe ...'
    cmd /c ".venv\Scripts\pyinstaller.exe --noconfirm --windowed --name HanyinLabel --icon ..\web\favicon.ico --add-data ..\web;web --add-data ..\presets.json;. --hidden-import win32timezone --version-file version_info.txt --distpath dist --workpath work --specpath . ..\server.py > pyinstaller.log 2>&1"
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 失败, 见 build\pyinstaller.log' }

    Write-Host '编译安装包 ...'
    $iscc = Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'
    if (-not (Test-Path $iscc)) { throw '没有 Inno Setup: winget install --id JRSoftware.InnoSetup -e --scope user' }
    # 火绒等杀毒软件会锁住刚生成的安装包导致写资源失败, 所以先输出到临时目录并重试
    $tmp = Join-Path $env:TEMP 'hanyin_setup_out'
    $ok = $false
    foreach ($i in 1..3) {
        cmd /c "`"$iscc`" /Qp `"/O$tmp`" installer.iss > iscc.log 2>&1"
        if ($LASTEXITCODE -eq 0) { $ok = $true; break }
        Write-Host "  第 $i 次失败 (多半是杀毒软件锁文件), 重试 ..."
        Start-Sleep -Seconds 3
    }
    if (-not $ok) { throw 'Inno Setup 编译失败' }
    New-Item -ItemType Directory -Force output | Out-Null
    Copy-Item (Join-Path $tmp '*.exe') output -Force
    Remove-Item -Recurse -Force $tmp, work -ErrorAction SilentlyContinue
    Get-ChildItem output | ForEach-Object { Write-Host "完成: $($_.FullName)  ($([math]::Round($_.Length / 1MB, 1)) MB)" }
} finally {
    Pop-Location
}
