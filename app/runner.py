"""任务循环执行。"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .actions import click_in_client, offset_point, sleep_ms
from .capture import Region, find_window_rect, grab_screen
from .config_store import project_root, templates_dir
from .matcher import load_template, match_template


def _bg_click_region(
    region: Region,
    client_x: int,
    client_y: int,
    *,
    clicks: int = 1,
    button: str = "left",
) -> str:
    return click_in_client(
        int(region.hwnd or 0),
        int(client_x),
        int(client_y),
        screen_left=region.left,
        screen_top=region.top,
        clicks=clicks,
        button=button,
        allow_steal_mouse=False,
    )


def resolve_template_path(image: str) -> Optional[Path]:
    raw = (image or "").strip().strip("\"'")
    if not raw:
        return None
    path = Path(raw)
    candidates: list[Path] = []
    if path.is_absolute():
        candidates.append(path)
    else:
        name = path.name
        stem = path.stem
        root = project_root()
        tdir = templates_dir()
        candidates.extend(
            [
                path,
                root / path,
                tdir / path,
                tdir / name,
                tdir / f"{stem}.png",
                tdir / "ui" / name,
                tdir / "ui" / f"{stem}.png",
                tdir / "cards" / name,
                tdir / "cards" / f"{stem}.png",
            ]
        )
    for c in candidates:
        if c.exists() and c.is_file():
            return c
    return None


def play_record_once(
    steps: List[Dict],
    *,
    title: str = "",
    region: Optional[Region] = None,
    stop_event: Optional[threading.Event] = None,
    log: Optional[Callable[[str], None]] = None,
    click_delay_ms: int = 300,
    match_threshold: float = 0.85,
    name: str = "脚本",
) -> bool:
    """回放一组录制步骤一次。成功返回 True；窗口缺失或被中断返回 False。"""
    _log = log or (lambda _msg: None)
    if not steps:
        _log(f"录制脚本为空 [{name}]")
        return False

    if region is None and title:
        region = find_window_rect(title)

    use_relative = any(
        bool(s.get("relative")) and str(s.get("mode") or "click") == "click"
        for s in steps
    )
    if use_relative and region is None:
        _log(
            f"回放失败 [{name}]：录制为窗口相对坐标，"
            "请填写正确的「目标窗口标题」并保持游戏窗口打开"
        )
        return False

    origin_x = region.left if region else 0
    origin_y = region.top if region else 0
    _log(f"回放 [{name}] 共 {len(steps)} 步")
    for i, step in enumerate(steps, start=1):
        if stop_event is not None and stop_event.is_set():
            return False
        sleep_ms(int(step.get("delay_ms", 0)))
        if stop_event is not None and stop_event.is_set():
            return False

        mode = str(step.get("mode") or "click")
        if mode in ("find", "template", "找图"):
            image = str(step.get("image") or "")
            path = resolve_template_path(image)
            if path is None:
                _log(f"  步骤{i}/{len(steps)} 找图失败：模板不存在 [{image}]")
                return False
            # 刷新窗口位置，找图更稳
            if title:
                region = find_window_rect(title) or region
            if region is None:
                _log(f"  步骤{i}/{len(steps)} 找图失败：未找到窗口")
                return False
            screen = grab_screen(region)
            template = load_template(str(path))
            if template is None:
                _log(f"  步骤{i}/{len(steps)} 找图失败：无法读取 {path.name}")
                return False
            matched = match_template(screen, template, match_threshold)
            if not matched.found:
                _log(
                    f"  步骤{i}/{len(steps)} 未找到 [{path.stem}] "
                    f"score={matched.score:.3f}（可降阈值或重截模板）"
                )
                return False
            cx, cy = matched.center
            button = str(step.get("button") or "left")
            clicks = int(step.get("clicks", 1))
            how = _bg_click_region(
                region, cx, cy, clicks=clicks, button=button
            )
            _log(
                f"  步骤{i}/{len(steps)} 找图点击 [{path.stem}] "
                f"client=({cx},{cy}) score={matched.score:.3f} via={how}"
            )
            sleep_ms(click_delay_ms)
            continue

        # 每步刷新窗口位置，拖走游戏窗也不偏
        if title:
            region = find_window_rect(title) or region
        if region is not None:
            origin_x, origin_y = region.left, region.top

        sx = int(step.get("x", 0))
        sy = int(step.get("y", 0))
        if step.get("relative"):
            client_x, client_y = sx, sy
        else:
            # 绝对屏幕坐标 → 当前窗口客户区
            if region is None:
                _log(f"  步骤{i}/{len(steps)} 点击失败：无窗口")
                return False
            client_x = sx - region.left
            client_y = sy - region.top
        if region is None:
            _log(f"  步骤{i}/{len(steps)} 点击失败：无窗口")
            return False
        button = str(step.get("button") or "left")
        clicks = int(step.get("clicks", 1))
        how = _bg_click_region(
            region, client_x, client_y, clicks=clicks, button=button
        )
        _log(
            f"  步骤{i}/{len(steps)} {button} client=({client_x},{client_y}) via={how}"
        )
    sleep_ms(click_delay_ms)
    return True


class TaskRunner:
    def __init__(
        self,
        log: Optional[Callable[[str], None]] = None,
        on_finished: Optional[Callable[[], None]] = None,
    ):
        self._log = log or (lambda _msg: None)
        self._on_finished = on_finished or (lambda: None)
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.running = False

    def start(
        self,
        config: Dict,
        slot_task: Dict | None = None,
        loop: bool = False,
    ) -> None:
        if self.running:
            self._log("已在运行")
            return
        self._stop.clear()
        self.running = True
        payload = dict(config)
        if slot_task is not None:
            payload["_slot_task"] = slot_task
        payload["_loop"] = bool(loop)
        self._thread = threading.Thread(target=self._loop, args=(payload,), daemon=True)
        self._thread.start()
        self._log("开始运行" + ("（循环）" if loop else "（单次）"))

    def stop(self) -> None:
        self._stop.set()
        self.running = False
        self._log("已停止")

    def _loop(self, config: Dict) -> None:
        threshold = float(config.get("match_threshold", 0.85))
        click_delay = int(config.get("click_delay_ms", 300))
        interval = int(config.get("loop_interval_ms", 800))
        title = str(config.get("target_window_title") or "").strip()
        loop = bool(config.get("_loop", False))
        slot_task = config.get("_slot_task")
        if slot_task:
            tasks = [slot_task]
        else:
            tasks = [t for t in (config.get("tasks") or []) if t.get("enabled", True)]

        if not tasks:
            self._log("没有可运行的存档/任务")
            self.running = False
            try:
                self._on_finished()
            except Exception:  # noqa: BLE001
                pass
            return

        while not self._stop.is_set():
            # 每轮重新找窗：拖走/被挡住也能继续
            region = find_window_rect(title) if title else None
            if region is None:
                self._log("未找到游戏窗口，等待…")
                sleep_ms(interval)
                continue
            screen = grab_screen(region)

            for task in tasks:
                if self._stop.is_set():
                    break
                mode = task.get("mode", "template")
                name = task.get("name", "未命名")
                try:
                    region = find_window_rect(title) or region
                    if region is None:
                        self._log(f"任务跳过 [{name}]：无窗口")
                        continue

                    if mode == "point":
                        cx = int(task.get("x", 0))
                        cy = int(task.get("y", 0))
                        how = _bg_click_region(
                            region, cx, cy, clicks=int(task.get("clicks", 1))
                        )
                        self._log(f"定点点击 [{name}] client=({cx},{cy}) via={how}")
                        sleep_ms(click_delay)
                        continue

                    if mode == "record":
                        play_record_once(
                            task.get("steps") or [],
                            title=title,
                            region=region,
                            stop_event=self._stop,
                            log=self._log,
                            click_delay_ms=click_delay,
                            match_threshold=threshold,
                            name=name,
                        )
                        continue

                    image = str(task.get("image") or "")
                    path = resolve_template_path(image)
                    template = load_template(str(path)) if path else None
                    if template is None:
                        self._log(f"模板不存在 [{name}] {image}")
                        continue
                    screen = grab_screen(region)
                    matched = match_template(screen, template, threshold)
                    if not matched.found:
                        self._log(f"未找到 [{name}] score={matched.score:.3f}")
                        continue
                    cx, cy = offset_point(
                        matched.center,
                        int(task.get("offset_x", 0)),
                        int(task.get("offset_y", 0)),
                    )
                    how = _bg_click_region(
                        region, cx, cy, clicks=int(task.get("clicks", 1))
                    )
                    self._log(
                        f"找图点击 [{name}] client=({cx},{cy}) "
                        f"score={matched.score:.3f} via={how}"
                    )
                    sleep_ms(click_delay)
                except Exception as ex:  # noqa: BLE001
                    self._log(f"任务异常 [{name}] {ex}")

            if not loop:
                break
            sleep_ms(interval)

        self.running = False
        if not self._stop.is_set():
            self._log("单次运行结束")
        try:
            self._on_finished()
        except Exception:  # noqa: BLE001
            pass
