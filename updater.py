"""检查 GitHub Releases 上的新版本，下载、校验 SHA-256 并替换程序文件。

打包版：把新文件解压到临时文件夹，启动一个隐藏的 PowerShell 脚本后面板退出；
脚本等进程结束，换掉 LocalModelPanel.exe 和 _internal 文件夹，再重新打开面板。
只换这两样：面板可能和 llama-swap 放在同一个文件夹，别的文件（README、LICENSE 等）不碰。
源码运行不自动替换，只打开下载页面。llama-swap 服务是单独的进程，更新面板不影响它。
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

REPO = "tiger-git2022/LocalModelPanel"
RELEASES_URL = f"https://github.com/{REPO}/releases/latest"
TIMEOUT = 30

FROZEN = getattr(sys, "frozen", False)
EXE = Path(sys.executable).resolve()
APP_DIR = EXE.parent
ONEDIR = FROZEN and (APP_DIR / "_internal").is_dir()   # PyInstaller 文件夹版；单文件版没有 _internal


def mode():
    """'zip'：打包版；None：不能自动替换（源码运行）。"""
    return "zip" if ONEDIR and sys.platform == "win32" else None


def asset_name(version):
    return f"LocalModelPanel-{version}.zip"


def parse(v):
    m = re.match(r"v?(\d+)\.(\d+)(?:\.(\d+))?", (v or "").strip())
    return tuple(int(x or 0) for x in m.groups()) if m else None


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "LocalModelPanel-updater", "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=TIMEOUT)


# 发版时附带的 update.json，格式和 GitHub 接口返回的发布信息一样。走的是普通下载地址，
# 不占接口每个 IP 每小时 60 次的额度（同一网络、同一代理出口的设备共用，容易用完，报 403 rate limit exceeded）。
MANIFEST_URL = f"{RELEASES_URL}/download/update.json"


def _latest_release():
    """先读 update.json；读不到（比如旧版本发布时还没有这个文件）再问 GitHub 接口。"""
    try:
        with _get(MANIFEST_URL) as r:
            return json.load(r)
    except (OSError, ValueError) as manifest_error:
        try:
            with _get(f"https://api.github.com/repos/{REPO}/releases/latest") as r:
                return json.load(r)
        except (OSError, ValueError) as api_error:
            raise RuntimeError(f"update.json：{manifest_error}；GitHub 接口：{api_error}") from api_error


def check(current):
    """有比 current 新的版本就返回 dict(version, notes, page, url, sha256)，否则返回 None。网络出错时抛异常。"""
    data = _latest_release()
    tag = data.get("tag_name") or ""
    latest, cur = parse(tag), parse(current) or (0, 0, 0)
    if not latest or latest <= cur:
        return None
    version = tag.lstrip("vV")
    info = {"version": version, "notes": data.get("body") or "", "page": data.get("html_url") or RELEASES_URL,
            "url": None, "sha256": None}
    if mode():
        want = asset_name(version)
        for a in data.get("assets") or []:
            if a.get("name") == want:
                digest = a.get("digest") or ""
                info["url"] = a.get("browser_download_url")
                info["sha256"] = digest[7:].lower() if digest.startswith("sha256:") else None
                break
    return info


def can_install(info):
    return bool(mode() and info.get("url") and info.get("sha256"))


def can_write_app_dir():
    try:
        probe = APP_DIR / f".update-probe-{os.getpid()}"
        probe.write_text("")
        probe.unlink()
        return True
    except OSError:
        return False


def download(info, progress=None):
    """下载并校验，返回本地文件路径。progress(百分比) 在下载线程里调用。"""
    url = info["url"]
    if not url or not url.startswith(f"https://github.com/{REPO}/releases/download/"):
        raise RuntimeError("下载地址不属于项目仓库")
    if not info.get("sha256"):
        raise RuntimeError("缺少 SHA-256 校验值")
    work = Path(tempfile.gettempdir()) / f"LocalModelPanel-update-{info['version']}"
    work.mkdir(parents=True, exist_ok=True)
    target = work / "update.zip"
    sha = hashlib.sha256()
    with _get(url) as r, open(target, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while chunk := r.read(1 << 16):
            f.write(chunk)
            sha.update(chunk)
            done += len(chunk)
            if progress and total:
                progress(done * 100 // total)
    if sha.hexdigest() != info["sha256"]:
        target.unlink(missing_ok=True)
        raise RuntimeError("SHA-256 校验失败，下载的文件可能不完整")
    return target


def start_install(path):
    """准备好新文件并启动更新脚本。调用后程序要马上退出。"""
    path = Path(path)
    work = path.parent
    files = work / "files"
    if files.exists():
        shutil.rmtree(files)
    files.mkdir()
    with zipfile.ZipFile(path) as z:
        z.extractall(files)
    source = files
    entries = list(files.iterdir())
    if len(entries) == 1 and entries[0].is_dir():   # zip 里只有一个顶层文件夹（LocalModelPanel\...）
        source = entries[0]
    if not (source / EXE.name).is_file() or not (source / "_internal").is_dir():
        raise RuntimeError(f"安装包里没有 {EXE.name} 和 _internal")
    for item in source.iterdir():   # 只换程序本身
        if item.name not in (EXE.name, "_internal"):
            shutil.rmtree(item) if item.is_dir() else item.unlink()
    script = work / "update.ps1"
    script.write_text(SCRIPT, encoding="utf-8-sig")
    args = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", str(script),
            "-ProcId", str(os.getpid()), "-Source", str(source), "-Target", str(APP_DIR), "-Exe", str(EXE),
            "-Log", str(work / "update.log")]
    # 重新打开的新程序要当成全新启动，不能沿用旧进程的 PyInstaller 环境（单文件版会去找已删除的解压目录）
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    subprocess.Popen(args, cwd=str(work), env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# 等旧进程退出 → 文件夹整个替换、文件逐个覆盖（被占用时重试）→ 重新打开程序。出错写进 update.log，仍然尝试打开程序。
SCRIPT = r"""param([int]$ProcId, [string]$Source, [string]$Target, [string]$Exe, [string]$Log)
$ErrorActionPreference = 'Stop'
function Retry([scriptblock]$Action) {
  for ($i = 0; ; $i++) {
    try { & $Action; return } catch { if ($i -ge 40) { throw }; Start-Sleep -Milliseconds 500 }
  }
}
try {
  try { Wait-Process -Id $ProcId -Timeout 120 } catch { }
  Start-Sleep -Milliseconds 500
  foreach ($item in Get-ChildItem -LiteralPath $Source -Force) {
    $dest = Join-Path $Target $item.Name
    if ($item.PSIsContainer) {
      if (Test-Path -LiteralPath $dest) { Retry { Remove-Item -LiteralPath $dest -Recurse -Force } }
      Retry { Copy-Item -LiteralPath $item.FullName -Destination $dest -Recurse -Force }
    } else {
      Retry { Copy-Item -LiteralPath $item.FullName -Destination $dest -Force }
    }
  }
  'ok ' + (Get-Date) | Out-File -LiteralPath $Log -Encoding utf8
} catch {
  ('failed ' + (Get-Date) + ': ' + $_) | Out-File -LiteralPath $Log -Encoding utf8
}
Start-Process -FilePath $Exe -WorkingDirectory $Target
"""
