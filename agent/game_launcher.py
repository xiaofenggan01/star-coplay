"""
game_launcher — 自动启动第二个 Stardew Valley 游戏实例。

Steam 不允许同时运行同一游戏的两个实例，但直接运行游戏可执行文件可以绕过此限制。
本模块负责：
    1. 按平台自动探测游戏安装路径（含 Steam libraryfolders.vdf 额外库）
    2. 优先通过 SMAPI 启动器启动（支持 Mod），fallback 到原版启动器
    3. 非阻塞进程管理（启动 / 存活检测 / 关闭）
    4. 支持 GAME_PATH 手动指定路径（GAME_DIR 兼容旧配置）
"""

from __future__ import annotations

import logging
import os
import platform as py_platform
import subprocess
from pathlib import Path

log = logging.getLogger("game_launcher")

_STEAM_STARDEW_APP_ID = "413150"


def _vdf_tokenize(text: str) -> list[str]:
    i = 0
    n = len(text)
    out: list[str] = []
    while i < n:
        while i < n and text[i] in " \t\r\n":
            i += 1
        if i >= n:
            break
        c = text[i]
        if c == "{":
            out.append("{"); i += 1; continue
        if c == "}":
            out.append("}"); i += 1; continue
        if c == '"':
            i += 1
            buf: list[str] = []
            while i < n:
                if text[i] == "\\" and i + 1 < n:
                    buf.append(text[i + 1]); i += 2; continue
                if text[i] == '"':
                    i += 1
                    break
                buf.append(text[i]); i += 1
            out.append("".join(buf))
            continue
        i += 1
    return out


def _vdf_parse_body(tokens: list[str], i: int) -> tuple[dict[str, object], int]:
    d: dict[str, object] = {}
    while i < len(tokens) and tokens[i] != "}":
        key = tokens[i]
        i += 1
        if i >= len(tokens):
            break
        if tokens[i] == "{":
            i += 1
            nested, i = _vdf_parse_body(tokens, i)
            d[key] = nested
        else:
            d[key] = tokens[i]
            i += 1
    if i < len(tokens) and tokens[i] == "}":
        i += 1
    return d, i


def _vdf_parse_root(tokens: list[str]) -> dict[str, object]:
    if not tokens:
        return {}
    if tokens[0] in ("{", "}"):
        d, _ = _vdf_parse_body(tokens, 0)
        return d
    key = tokens[0]
    if len(tokens) < 3 or tokens[1] != "{":
        return {}
    d, _ = _vdf_parse_body(tokens, 2)
    return {key: d}


def _normalize_vdf_path(s: str) -> Path:
    return Path(s.replace("\\\\", "\\").strip())


def _library_roots_from_vdf_tree(section: dict[str, object]) -> list[Path]:
    roots: list[Path] = []
    for _key, entry in section.items():
        if isinstance(entry, str):
            p = _normalize_vdf_path(entry)
            if p.is_dir():
                roots.append(p)
            continue
        if not isinstance(entry, dict):
            continue
        ed = {str(k): v for k, v in entry.items()}
        raw_path = ed.get("path")
        if not isinstance(raw_path, str):
            continue
        lib_path = _normalize_vdf_path(raw_path)
        if not lib_path.is_dir():
            continue
        apps = ed.get("apps")
        if isinstance(apps, dict) and _STEAM_STARDEW_APP_ID in {str(k) for k in apps.keys()}:
            roots.append(lib_path)
        elif not isinstance(apps, dict):
            roots.append(lib_path)
    return roots


def _parse_libraryfolders_vdf(vdf_path: Path) -> list[Path]:
    try:
        text = vdf_path.read_text(encoding="utf-8-sig")
    except OSError:
        return []
    try:
        tokens = _vdf_tokenize(text)
        root = _vdf_parse_root(tokens)
    except Exception:
        log.debug("VDF tokenize/parse failed: %s", vdf_path, exc_info=True)
        return []
    for name in ("libraryfolders", "LibraryFolders"):
        if name in root and isinstance(root[name], dict):
            return _library_roots_from_vdf_tree(root[name])  # type: ignore[arg-type]
    if len(root) == 1:
        inner = next(iter(root.values()))
        if isinstance(inner, dict):
            return _library_roots_from_vdf_tree(inner)  # type: ignore[arg-type]
    return []


def _windows_registry_game_paths() -> list[Path]:
    import winreg

    flags = winreg.KEY_READ | winreg.KEY_WOW64_64KEY
    out: list[Path] = []

    def read_install_loc(subkey: str) -> None:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey, 0, flags) as k:
                v, _ = winreg.QueryValueEx(k, "InstallLocation")
                if isinstance(v, str) and v.strip():
                    out.append(Path(v.strip()))
        except OSError:
            pass

    for sk in (
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Steam App 413150",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Steam App 413150",
    ):
        read_install_loc(sk)

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\WOW6432Node\GOG.com\Games\1453375253",
            0,
            flags,
        ) as k:
            v, _ = winreg.QueryValueEx(k, "PATH")
            if isinstance(v, str) and v.strip():
                out.append(Path(v.strip()))
    except OSError:
        pass

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", 0, flags) as k:
            v, _ = winreg.QueryValueEx(k, "SteamPath")
            if isinstance(v, str) and v.strip():
                root = Path(v.replace("/", "\\").rstrip("\\"))
                out.append(root / "steamapps" / "common" / "Stardew Valley")
    except OSError:
        pass

    return out


def _windows_steam_libraryfolders_vdf() -> list[Path]:
    import winreg

    flags = winreg.KEY_READ | winreg.KEY_WOW64_64KEY
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", 0, flags) as k:
            v, _ = winreg.QueryValueEx(k, "SteamPath")
            if isinstance(v, str) and v.strip():
                root = Path(v.replace("/", "\\").rstrip("\\"))
                return [root / "steamapps" / "libraryfolders.vdf"]
    except OSError:
        pass
    return []


def _search_dirs_for_game_root(game_dir: Path, system: str) -> list[Path]:
    dirs: list[Path] = [game_dir]
    if system == "Darwin":
        for extra in (
            game_dir / "Contents" / "MacOS",
            game_dir / "Stardew Valley.app" / "Contents" / "MacOS",
        ):
            if extra.is_dir():
                dirs.insert(0, extra)
    return dirs


def _dir_has_stardew_executable(game_dir: Path, system: str, smapi_names: dict[str, str], vanilla_names: dict[str, list[str]]) -> bool:
    if not game_dir.is_dir():
        return False
    if (game_dir / "Stardew Valley.dll").is_file():
        return True
    smapi = smapi_names.get(system)
    vans = vanilla_names.get(system, [])
    for d in _search_dirs_for_game_root(game_dir, system):
        if smapi and (d / smapi).is_file():
            return True
        for vn in vans:
            if (d / vn).is_file():
                return True
    return False


class GameLauncher:
    """管理 Stardew Valley 第二实例的生命周期。"""

    _SMAPI_NAMES: dict[str, str] = {
        "Darwin": "StardewModdingAPI",
        "Linux": "StardewModdingAPI",
        "Windows": "StardewModdingAPI.exe",
    }

    _VANILLA_NAMES: dict[str, list[str]] = {
        "Darwin": ["StardewValley"],
        "Linux": ["StardewValley"],
        "Windows": ["Stardew Valley.exe", "StardewValley.exe"],
    }

    def __init__(self, game_path: str | None = None) -> None:
        """初始化 GameLauncher。

        Args:
            game_path: 游戏安装目录的绝对路径。
                       为 None 时从环境变量 GAME_PATH（或 GAME_DIR）读取，
                       仍为空则自动探测。
        """
        self._process: subprocess.Popen | None = None  # type: ignore[type-arg]
        self._system = py_platform.system()

        # 路径解析优先级：参数 > 环境变量 > 自动探测
        raw = (game_path or os.environ.get("GAME_PATH", "").strip() or os.environ.get("GAME_DIR", "").strip())
        if raw:
            self._game_dir = Path(raw)
            log.debug("使用手动指定的游戏路径: %s", self._game_dir)
        else:
            detected = self._detect_game_dir()
            if detected:
                self._game_dir = detected
                log.debug("自动探测到游戏安装目录: %s", self._game_dir)
            else:
                self._game_dir = Path("")
                log.warning("未能探测到游戏安装目录，请通过 GAME_PATH 环境变量手动指定")

    # ── 公开接口 ──────────────────────────────────────────────────────────

    def launch(self) -> bool:
        """启动游戏进程（非阻塞）。

        Returns:
            启动成功返回 True，失败返回 False。
        """
        if self.is_running():
            log.info("游戏进程已在运行 (PID=%s)，跳过启动", self._process.pid)  # type: ignore[union-attr]
            return True

        executable = self._resolve_executable()
        if executable is None:
            log.error("未找到可执行文件，无法启动游戏")
            return False

        try:
            log.info("正在启动游戏: %s", executable)

            if self._system == "Darwin":
                # macOS: launch SMAPI in a new Terminal window so the user can see
                # the SMAPI console (for agent_water, agent_status, etc.)
                # osascript opens Terminal.app with the executable as a command
                script = (
                    f'tell application "Terminal"\n'
                    f'  do script "cd {str(executable.parent)!r} && {str(executable)!r}"\n'
                    f'  activate\n'
                    f'end tell'
                )
                self._process = subprocess.Popen(
                    ["osascript", "-e", script],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                log.info("游戏启动成功 (通过 Terminal.app, PID=%s)", self._process.pid)
            else:
                # Windows/Linux: run directly (SMAPI opens its own console on Windows)
                self._process = subprocess.Popen(
                    [str(executable)],
                    cwd=str(executable.parent),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                log.info("游戏启动成功 (PID=%s)", self._process.pid)

            return True
        except OSError as exc:
            log.error("启动游戏失败: %s", exc)
            self._process = None
            return False

    def is_running(self) -> bool:
        """检测游戏进程是否仍在运行。"""
        if self._process is None:
            return False
        return self._process.poll() is None

    def stop(self) -> None:
        """终止游戏进程（如果仍在运行）。"""
        if not self.is_running():
            return
        assert self._process is not None
        pid = self._process.pid
        log.info("正在终止游戏进程 (PID=%s)...", pid)
        self._process.terminate()
        try:
            self._process.wait(timeout=10)
            log.info("游戏进程已正常退出")
        except subprocess.TimeoutExpired:
            log.warning("游戏进程未响应 terminate，执行 kill")
            self._process.kill()
            self._process.wait(timeout=5)
        self._process = None

    @property
    def pid(self) -> int | None:
        """当前游戏进程 PID，未启动时返回 None。"""
        if self._process is not None and self.is_running():
            return self._process.pid
        return None

    @property
    def game_dir(self) -> Path:
        """游戏安装目录。"""
        return self._game_dir

    # ── 内部方法 ──────────────────────────────────────────────────────────

    def _launch_search_dirs(self) -> list[Path]:
        return _search_dirs_for_game_root(self._game_dir, self._system)

    @property
    def smapi_executable(self) -> Path | None:
        if not self._game_dir or not self._game_dir.exists():
            return None
        smapi_name = self._SMAPI_NAMES.get(self._system)
        if not smapi_name:
            return None
        for d in self._launch_search_dirs():
            candidate = d / smapi_name
            if candidate.is_file():
                return candidate
        return None

    def _resolve_executable(self) -> Path | None:
        """按优先级查找可执行文件：SMAPI > 原版。"""
        if not self._game_dir or not self._game_dir.exists():
            log.error("游戏目录不存在: %s", self._game_dir)
            return None

        smapi = self.smapi_executable
        if smapi is not None:
            log.info("找到 SMAPI 启动器: %s", smapi)
            return smapi
        log.info("未找到 SMAPI，尝试原版启动器")

        vanilla_names = self._VANILLA_NAMES.get(self._system, [])
        for d in self._launch_search_dirs():
            for name in vanilla_names:
                candidate = d / name
                if candidate.is_file():
                    log.info("找到原版启动器: %s", candidate)
                    return candidate

        return None

    def _detect_game_dir(self) -> Path | None:
        """按平台自动探测 Stardew Valley 安装目录。"""
        candidates: list[Path] = list(self._get_default_candidates())
        for lib_root in self._steam_library_roots():
            candidates.append(lib_root / "steamapps" / "common" / "Stardew Valley")
        seen: set[str] = set()
        for path in candidates:
            try:
                key = str(path.resolve())
            except OSError:
                key = str(path)
            if key in seen:
                continue
            seen.add(key)
            if _dir_has_stardew_executable(path, self._system, self._SMAPI_NAMES, self._VANILLA_NAMES):
                return path
        return None

    def _get_default_candidates(self) -> list[Path]:
        """返回各平台的默认游戏安装候选路径。"""
        home = Path.home()
        system = self._system

        if system == "Darwin":
            steam_base = home / "Library" / "Application Support" / "Steam"
            return [
                Path("/Applications/Stardew Valley.app/Contents/MacOS"),
                steam_base / "steamapps" / "common" / "Stardew Valley",
                home / "GOG Games" / "Stardew Valley",
            ]
        if system == "Linux":
            steam_a = home / ".steam" / "steam" / "steamapps" / "common" / "Stardew Valley"
            steam_b = home / ".local" / "share" / "Steam" / "steamapps" / "common" / "Stardew Valley"
            steam_game = steam_a if steam_a.is_dir() else steam_b
            return [
                home / "GOG Games" / "Stardew Valley" / "game",
                steam_game,
                home / ".var" / "app" / "com.valvesoftware.Steam" / "data" / "Steam" / "steamapps" / "common" / "Stardew Valley",
                home / "GOG Games" / "Stardew Valley",
            ]
        if system == "Windows":
            reg_paths = _windows_registry_game_paths()
            pf_extras: list[Path] = []
            for pf in ("C:/Program Files", "C:/Program Files (x86)"):
                base = Path(pf)
                pf_extras.extend(
                    [
                        base / "Steam" / "steamapps" / "common" / "Stardew Valley",
                        base / "GOG Games" / "Stardew Valley",
                        base / "GOG Galaxy" / "Games" / "Stardew Valley",
                        base / "GalaxyClient" / "Games" / "Stardew Valley",
                    ]
                )
            for letter in "CDEFGH":
                pf_extras.append(
                    Path(f"{letter}:/Program Files/ModifiableWindowsApps/Stardew Valley")
                )
            return reg_paths + pf_extras
        log.warning("不支持的平台: %s", system)
        return []

    def _steam_library_roots(self) -> list[Path]:
        roots: list[Path] = []
        seen: set[str] = set()
        for vdf in self._libraryfolders_vdf_paths():
            for lib in _parse_libraryfolders_vdf(vdf):
                try:
                    k = str(lib.resolve())
                except OSError:
                    k = str(lib)
                if k not in seen:
                    seen.add(k)
                    roots.append(lib)
        return roots

    def _libraryfolders_vdf_paths(self) -> list[Path]:
        home = Path.home()
        system = self._system
        if system == "Darwin":
            return [home / "Library" / "Application Support" / "Steam" / "steamapps" / "libraryfolders.vdf"]
        if system == "Linux":
            return [
                home / ".local" / "share" / "Steam" / "steamapps" / "libraryfolders.vdf",
                home / ".steam" / "steam" / "steamapps" / "libraryfolders.vdf",
            ]
        if system == "Windows":
            seen: set[str] = set()
            ordered: list[Path] = []
            for p in _windows_steam_libraryfolders_vdf():
                k = str(p)
                if k not in seen:
                    seen.add(k)
                    ordered.append(p)
            for p in (
                Path("C:/Program Files (x86)/Steam/steamapps/libraryfolders.vdf"),
                Path("C:/Program Files/Steam/steamapps/libraryfolders.vdf"),
            ):
                k = str(p)
                if k not in seen:
                    seen.add(k)
                    ordered.append(p)
            return ordered
        return []


def iter_starco_mod_dir_candidates() -> list[Path]:
    gl = GameLauncher()
    system = gl._system
    mod_rel = Path("Mods") / "StarCoPlay"
    roots: list[Path] = []
    gd = gl.game_dir
    if str(gd).strip() and gd.exists():
        roots.append(gd)
    roots.extend(gl._get_default_candidates())
    for lib in gl._steam_library_roots():
        roots.append(lib / "steamapps" / "common" / "Stardew Valley")
    seen: set[str] = set()
    uniq_roots: list[Path] = []
    for r in roots:
        try:
            k = str(r.resolve())
        except OSError:
            k = str(r)
        if k not in seen:
            seen.add(k)
            uniq_roots.append(r)
    mod_dirs: list[Path] = []
    for root in uniq_roots:
        if system == "Darwin":
            mod_dirs.append(root / mod_rel)
            mod_dirs.append(root / "Stardew Valley.app" / "Contents" / "MacOS" / mod_rel)
        else:
            mod_dirs.append(root / mod_rel)
    seen_m: set[str] = set()
    out: list[Path] = []
    for m in mod_dirs:
        try:
            k = str(m.resolve())
        except OSError:
            k = str(m)
        if k not in seen_m:
            seen_m.add(k)
            out.append(m)
    return out
