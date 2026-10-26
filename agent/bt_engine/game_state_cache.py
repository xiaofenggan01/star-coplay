"""
bt_engine.game_state_cache — 线程安全游戏状态缓存 + watchdog Observer 封装。

从 game_state.json 加载游戏状态快照，供快环 BT 节点在不访问磁盘的情况下读取。
利用 watchdog 监听文件变化，自动刷新缓存并触发注册的回调（如 ReactiveLoop._tick_event.set()）。

设计要点：
    - 线程安全（RLock），read-copy-update 模式
    - watchdog 监听父目录（watchdog 正确用法）
    - on_created + on_modified 均触发 reload（防止原子 rename 写文件时 on_modified 不触发）
    - reload 失败时保留旧快照，仅首次失败（上次成功后再失败）输出 warning（日志降噪）
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)


class _GameStateFileHandler(FileSystemEventHandler):
    """watchdog 文件变化处理器，内部类，不对外暴露。"""

    def __init__(self, state_path: Path, on_change: Callable[[], None]) -> None:
        """
        Args:
            state_path: 监听的目标文件绝对路径（game_state.json）
            on_change:  文件变化时调用的回调，无参数，通常是 GameStateCache._reload
        """
        super().__init__()
        self._state_path = state_path.resolve()
        self._on_change = on_change

    def on_modified(self, event) -> None:
        """仅响应 state_path 文件变化，忽略目录和其他文件。"""
        if not event.is_directory and Path(event.src_path).resolve() == self._state_path:
            self._on_change()

    def on_created(self, event) -> None:
        """也监听 on_created，防止文件被原子替换（rename）时 on_modified 不触发。"""
        self.on_modified(event)


class GameStateCache:
    """线程安全的游戏状态缓存。

    职责：
    1. 从 game_state.json 直接读文件加载状态
    2. watchdog 监听文件变化，自动刷新缓存
    3. 变化时触发注册的回调（ReactiveLoop 用此触发 tick_event.set()）
    4. get_latest() 提供快照，供 tick 中的叶节点读取
    """

    def __init__(self, state_path: str | Path) -> None:
        """
        Args:
            state_path: game_state.json 的路径（内部转为绝对 Path）

        关键字段：
            _state: dict | None，当前缓存快照，None 表示尚未加载或加载失败
            _lock: threading.RLock，保护 _state 的读写
            _callbacks: list[Callable]，状态变化时依次调用
            _observer: watchdog Observer，start() 后启动
            _last_load_ok: bool，上次加载是否成功（用于日志降噪）
        """
        self._state_path = Path(state_path).resolve()
        self._state: dict | None = None
        self._lock = threading.RLock()
        self._callbacks: list[Callable[[], None]] = []
        self._observer: Observer | None = None
        self._last_load_ok: bool = False

    def start(self) -> None:
        """启动 watchdog Observer，开始监听文件变化。

        调用前先做一次 _reload() 预热缓存（避免首次 tick 前缓存为空）。
        MUST：由 ReactiveLoop.start() 调用，不能单独调用后立即 stop()。
        """
        # 预热缓存
        self._reload()

        # 创建并启动 watchdog Observer
        handler = _GameStateFileHandler(
            state_path=self._state_path,
            on_change=self._reload,
        )
        self._observer = Observer()
        # watchdog 监听父目录（正确用法）
        self._observer.schedule(handler, str(self._state_path.parent), recursive=False)
        self._observer.start()
        logger.info(
            "GameStateCache: 开始监听 %s",
            self._state_path,
        )

    def stop(self) -> None:
        """停止 watchdog Observer，释放资源。由 ReactiveLoop.stop() 调用。"""
        if self._observer is not None:
            self._observer.stop()
            self._observer.join()
            self._observer = None
            logger.info("GameStateCache: 已停止")

    def register_change_callback(self, callback: Callable[[], None]) -> None:
        """注册状态变化回调。每次文件刷新成功后依次调用所有回调。

        Args:
            callback: 无参数的可调用对象，通常是 lambda: self._tick_event.set()
        MUST：在 start() 前注册，避免竞争条件。
        """
        self._callbacks.append(callback)

    def get_latest(self) -> dict | None:
        """获取当前缓存的游戏状态快照（线程安全）。

        Returns:
            游戏状态 dict，从未成功加载时返回 None。
            返回的是当前缓存引用，调用方不应修改（如需修改请 copy.deepcopy）。
        """
        with self._lock:
            return self._state

    @staticmethod
    def _report_bridge_disconnected(reason: str) -> None:
        """上报 bridge.disconnected 事件（静默失败，不影响缓存逻辑）。"""
        try:
            from report import events as _events
            _events.bridge_disconnected(reason=reason)
        except Exception:
            pass

    def _reload(self) -> None:
        """（内部）从文件重新加载状态，更新 _state，触发回调。

        设计：使用 read-copy-update：先读文件，成功后加锁替换 _state。
        失败时：记录 warning（仅在上次成功后才 warn，避免文件不存在时刷屏），
                不清空 _state（保留上次有效快照供 BT 使用）。
        """
        try:
            # On Windows, Path.read_text() may fail with PermissionError when
            # the Mod is mid-write (Windows file locking is stricter than Unix).
            # Retry up to 3 times with a small delay to handle transient locks.
            text = None
            for _attempt in range(3):
                try:
                    text = self._state_path.read_text(encoding="utf-8")
                    break
                except PermissionError:
                    import time
                    time.sleep(0.05)  # 50ms wait for file lock to release
            if text is None:
                text = self._state_path.read_text(encoding="utf-8")  # final attempt, let it raise
            new_state = json.loads(text)
        except FileNotFoundError:
            if self._last_load_ok:
                logger.warning(
                    "GameStateCache: 文件不存在，保留旧快照: %s",
                    self._state_path,
                )
                self._report_bridge_disconnected("file_missing")
            self._last_load_ok = False
            return
        except json.JSONDecodeError as exc:
            if self._last_load_ok:
                logger.warning(
                    "GameStateCache: JSON 解析失败，保留旧快照: %s (%s)",
                    self._state_path,
                    exc,
                )
                self._report_bridge_disconnected("json_decode_error")
            self._last_load_ok = False
            return
        except OSError as exc:
            if self._last_load_ok:
                logger.warning(
                    "GameStateCache: 读取失败，保留旧快照: %s (%s)",
                    self._state_path,
                    exc,
                )
                self._report_bridge_disconnected("io_error")
            self._last_load_ok = False
            return

        # 成功：加锁替换状态，然后触发回调
        was_ok = self._last_load_ok
        with self._lock:
            self._state = new_state

        self._last_load_ok = True

        # Bridge 首次就绪 → 上报 bridge.connected（session 内去重由 onboard 机制保证）
        if not was_ok:
            try:
                from report import events as _events
                _events.bridge_connected()
            except Exception:
                pass  # telemetry 不应影响状态缓存
        logger.debug("GameStateCache: 已刷新状态 (%s)", self._state_path.name)

        # 在锁外触发回调，避免回调中重入死锁
        for cb in self._callbacks:
            try:
                cb()
            except Exception:
                logger.exception("GameStateCache: 回调异常")
