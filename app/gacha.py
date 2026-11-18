"""常驻招募抽卡：识别台上英雄 → 有目标点英雄再金币抽；无目标直接正常抽。不用最大/绿砖。"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .actions import click_in_client, sleep_ms
from .window_lock import enforce_locked_size
from .capture import Region, find_window_rect, grab_screen
from .gacha_store import (
    UI_TEMPLATES,
    append_gacha_result,
    find_ui_template,
    list_available_card_templates,
    list_ui_templates,
    new_gacha_session,
    normalize_gacha,
    ui_dir,
)
from .matcher import load_template, match_template


class GachaRunner:
    def __init__(
        self,
        log: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[Dict], None]] = None,
        on_finished: Optional[Callable[[], None]] = None,
    ):
        self._log = log or (lambda _msg: None)
        self._on_progress = on_progress or (lambda _info: None)
        self._on_finished = on_finished or (lambda: None)
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.running = False

    def start(self, *, config: Dict, **_kwargs) -> None:
        if self.running:
            self._log("抽卡已在运行")
            return
        self._stop.clear()
        self.running = True
        self._thread = threading.Thread(
            target=self._loop,
            args=(config,),
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if not self.running and not self._stop.is_set():
            return
        self._stop.set()
        self.running = False
        self._log("抽卡已停止")

    def _wait(self, ms: int) -> bool:
        left = max(0, int(ms))
        while left > 0 and not self._stop.is_set():
            chunk = min(100, left)
            sleep_ms(chunk)
            left -= chunk
        return not self._stop.is_set()

    def _region(self, title: str) -> Optional[Region]:
        return find_window_rect(title) if title else None

    def _bottom_region(self, region: Region, top_ratio: float = 0.55) -> Region:
        """只在窗口下半区找按钮，减少误点台上英雄/其它绿块。"""
        top = region.top + int(region.height * top_ratio)
        height = max(40, region.top + region.height - top)
        return Region(region.left, top, region.width, height, hwnd=region.hwnd)

    def _btn_threshold(self, label: str, threshold: float) -> float:
        # 「最大」保持偏严；金币按钮略松，适配缩放
        name = label.split(":")[0]
        if name in ("最大", "刷新"):
            return max(0.75, float(threshold) - 0.08)
        if name in ("放弃",):
            # 白字下划线小图，略放宽（缩放/抗锯齿后分数容易偏低）
            return max(0.55, min(float(threshold), 0.68))
        if name in ("减号",):
            return max(0.68, min(float(threshold), 0.78))
        # 正常抽卡/金币招募（缩放后分数常在 0.65~0.75）
        return max(0.58, min(float(threshold), 0.68))

    def _grab_ui(self, region: Region):
        """优先 CDP CSS 帧（点击坐标准）；PrintWindow 在 125% DPI 下底部易偏。"""
        try:
            from .cdp_click import capture_css_frame

            frame = capture_css_frame(width=390, height=844, force_dpr1=False)
            if frame is not None and getattr(frame, "size", 0) > 0:
                return frame, "cdp"
        except Exception:  # noqa: BLE001
            pass
        screen = grab_screen(region)
        return screen, "printwindow"

    def _click_template(
        self,
        region: Region,
        path: Path,
        threshold: float,
        label: str,
        quiet: bool = False,
        bottom_only: bool = True,
    ) -> bool:
        screen, source = self._grab_ui(region)
        if screen is None or screen.size == 0:
            if not quiet:
                self._log(f"截图失败 [{label}]")
            return False
        tpl = load_template(str(path))
        if tpl is None:
            if not quiet:
                self._log(f"无法读取模板 [{label}] {path}")
            return False

        h = screen.shape[0]
        y0 = int(h * 0.55) if bottom_only else 0
        search_img = screen[y0:] if bottom_only else screen
        btn_threshold = self._btn_threshold(label, threshold)
        matched = match_template(search_img, tpl, btn_threshold)
        if not matched.found:
            if not quiet:
                self._log(
                    f"未找到 [{label}] score={matched.score:.3f} "
                    f"(阈值{btn_threshold:.2f}，可重截模板)"
                )
            return False
        client_x = int(matched.center[0])
        client_y = int(matched.center[1] + y0)
        how = click_in_client(
            int(region.hwnd or 0),
            client_x,
            client_y,
            screen_left=region.left,
            screen_top=region.top,
        )
        self._log(
            f"点击 [{label}] client=({client_x},{client_y}) "
            f"score={matched.score:.3f} via={how}/{source}"
        )
        return True

    def _click_any(
        self,
        region: Region,
        paths: List[Path],
        threshold: float,
        label: str,
        bottom_only: bool = True,
    ) -> bool:
        if not paths:
            self._log(f"没有可用模板 [{label}]")
            return False
        for path in paths:
            if self._click_template(
                region,
                path,
                threshold,
                f"{label}:{path.stem}",
                quiet=True,
                bottom_only=bottom_only,
            ):
                return True
        # 模板都失败时：用绿色大按钮兜底（正常抽卡/金币招募）
        if "抽卡" in label or "金币" in label or "招募" in label:
            if self._click_green_recruit_fallback(region, label):
                return True
        self._log(f"未找到 [{label}]（已试 {len(paths)} 个模板）")
        return False

    def _click_green_recruit_fallback(self, region: Region, label: str) -> bool:
        """下半区找「带黄色金币」的绿色大按钮；避免点到绿砖73。"""
        import cv2

        screen, source = self._grab_ui(region)
        if screen is None or screen.size == 0:
            return False
        h = screen.shape[0]
        y0 = int(h * 0.58)
        roi = screen[y0:]
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        green = cv2.inRange(hsv, (35, 80, 80), (95, 255, 255))
        yellow = cv2.inRange(hsv, (15, 100, 120), (40, 255, 255))
        cnts, _ = cv2.findContours(green, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best = None
        for c in cnts:
            x, y, bw, bh = cv2.boundingRect(c)
            area = bw * bh
            if area < 2500 or bw < 70 or bh < 28 or bw < bh * 1.6:
                continue
            # 按钮内必须有金币黄，排除绿砖按钮
            patch = yellow[y : y + bh, x : x + bw]
            if int(patch.sum() / 255) < 40:
                continue
            if best is None or area > best[0]:
                best = (area, x, y, bw, bh)
        if best is None:
            return False
        _, x, y, bw, bh = best
        client_x = x + bw // 2
        client_y = y0 + y + bh // 2
        how = click_in_client(
            int(region.hwnd or 0),
            client_x,
            client_y,
            screen_left=region.left,
            screen_top=region.top,
        )
        self._log(
            f"点击 [{label}:金币绿键] client=({client_x},{client_y}) "
            f"size={bw}x{bh} via={how}/{source}"
        )
        return True

    def _ensure_coin_mode(self, region: Region, ui_coin_list: List[Path], threshold: float) -> Region:
        """若点了英雄变成绿砖价，先点空白取消选中，恢复金币100按钮。"""
        region = self._region_from(region) or region
        if self._coin_visible(region, ui_coin_list, threshold):
            return region
        self._log("未看到金币抽按钮（可能选中了英雄），点击空白取消选中")
        # 点舞台上方偏左空白（避开「刷新」按钮）
        cx = int(region.width * 0.35)
        cy = int(region.height * 0.20)
        click_in_client(
            int(region.hwnd or 0),
            cx,
            cy,
            screen_left=region.left,
            screen_top=region.top,
        )
        self._wait(600)
        region = self._region_from(region) or region
        return region

    def _region_from(self, region: Region) -> Optional[Region]:
        if region.hwnd:
            from .capture import _client_region_from_hwnd

            return _client_region_from_hwnd(int(region.hwnd)) or region
        return region

    def _coin_visible(self, region: Region, ui_coin_list: List[Path], threshold: float) -> bool:
        screen, _src = self._grab_ui(region)
        if screen is None or screen.size == 0:
            return False
        h = screen.shape[0]
        bottom = screen[int(h * 0.55) :]
        btn_th = self._btn_threshold("金币招募", threshold)
        for path in ui_coin_list:
            tpl = load_template(str(path))
            if tpl is None:
                continue
            matched = match_template(bottom, tpl, btn_th)
            if matched.found:
                return True
        return False

    def _grab_for_abandon(self, region: Region):
        """与按钮点击同一套截图源，保证放弃坐标可点中。"""
        return self._grab_ui(region)

    def _find_abandon_match(
        self,
        region: Region,
        ui_abandon: Optional[Path],
        threshold: float,
    ):
        """只在绿键下方条带里找「放弃」，避免误匹配顶部白字。"""
        import cv2

        if ui_abandon is None:
            return None
        tpl = load_template(str(ui_abandon))
        if tpl is None:
            return None

        screen, source = self._grab_for_abandon(region)
        if screen is None or screen.size == 0:
            return None
        h, w = screen.shape[:2]
        hsv = cv2.cvtColor(screen, cv2.COLOR_BGR2HSV)
        green = cv2.inRange(hsv, (35, 70, 70), (95, 255, 255))
        cnts, _ = cv2.findContours(green, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best = None
        for c in cnts:
            x, y, bw, bh = cv2.boundingRect(c)
            area = bw * bh
            if y < h * 0.55 or area < 2200 or bw < 80 or bh < 26 or bw < bh * 1.8:
                continue
            if best is None or area > best[0]:
                best = (area, x, y, bw, bh)

        btn_th = self._btn_threshold("放弃", threshold)
        if best is not None:
            _, x, y, bw, bh = best
            y0 = y + bh
            y1 = min(h, y0 + 48)
            x0 = max(0, x - 8)
            x1 = min(w, x + bw + 8)
            if y1 - y0 >= tpl.shape[0] and x1 - x0 >= tpl.shape[1]:
                strip = screen[y0:y1, x0:x1]
                matched = match_template(strip, tpl, btn_th)
                if matched.found:
                    return (
                        x0 + matched.center[0],
                        y0 + matched.center[1],
                        matched.score,
                        f"绿键下/{source}",
                    )

        # 回退：只搜窗口最底部 22%
        y0 = int(h * 0.78)
        bottom = screen[y0:]
        matched = match_template(bottom, tpl, btn_th)
        if matched.found:
            return (
                matched.center[0],
                y0 + matched.center[1],
                matched.score,
                f"底部/{source}",
            )
        return None

    def _click_abandon_under_green(self, region: Region) -> bool:
        """找下半区最大绿色招募键，点其正下方「放弃」文字位置。"""
        import cv2

        screen, _src = self._grab_for_abandon(region)
        if screen is None or screen.size == 0:
            return False
        h, _w = screen.shape[:2]
        hsv = cv2.cvtColor(screen, cv2.COLOR_BGR2HSV)
        green = cv2.inRange(hsv, (35, 70, 70), (95, 255, 255))
        cnts, _ = cv2.findContours(green, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best = None
        for c in cnts:
            x, y, bw, bh = cv2.boundingRect(c)
            area = bw * bh
            if y < h * 0.55 or area < 2200 or bw < 80 or bh < 26 or bw < bh * 1.8:
                continue
            if best is None or area > best[0]:
                best = (area, x, y, bw, bh)
        if best is None:
            return False
        _, x, y, bw, bh = best
        space = h - (y + bh)
        if space < 10:
            # 底部被裁切，放弃不可见，盲点无效
            return False
        client_x = x + bw // 2
        client_y = y + bh + min(18, max(12, space // 2))
        how = click_in_client(
            int(region.hwnd or 0),
            client_x,
            client_y,
            screen_left=region.left,
            screen_top=region.top,
        )
        self._log(
            f"点击 [放弃:绿键下方] client=({client_x},{client_y}) "
            f"btn={bw}x{bh} space={space} via={how}"
        )
        return True

    def _after_draw_abandon(
        self,
        title: str,
        region: Region,
        ui_abandon: Optional[Path],
        threshold: float,
        settle_ms: int,
        ui_coin_list: Optional[List[Path]] = None,
    ) -> bool:
        """抽卡后：有「放弃」则点；没有且已可再抽则跳过。返回 False=手动停止。"""
        # 先等动画；有的结果没有放弃、直接可再抽
        wait_ms = max(1600, min(int(settle_ms), 3200))
        self._log(f"抽卡后等待 {wait_ms}ms，检查是否需要点「放弃」")
        if not self._wait(wait_ms):
            return False

        coin_paths = list(ui_coin_list or [])
        for attempt in range(1, 8):
            if self._stop.is_set():
                return False
            enforce_locked_size(title)
            region = self._region(title) or region

            found = self._find_abandon_match(region, ui_abandon, threshold)
            if found is not None:
                cx, cy, score, where = found
                how = click_in_client(
                    int(region.hwnd or 0),
                    int(cx),
                    int(cy),
                    screen_left=region.left,
                    screen_top=region.top,
                )
                self._log(
                    f"点击 [放弃] client=({int(cx)},{int(cy)}) "
                    f"score={score:.3f} via={how} ({where})"
                )
                self._log("已点「放弃」，准备下一轮")
                return self._wait(max(800, int(settle_ms) // 2))

            # 没有放弃、但金币抽按钮已在 → 可直接下一轮
            if coin_paths and self._coin_visible(region, coin_paths, threshold):
                self._log("未出现「放弃」，已可再次抽卡，跳过放弃直接下一轮")
                return self._wait(400)

            if attempt in (1, 3, 5):
                self._log(f"暂无「放弃」、也未确认可再抽（第{attempt}次），继续等")
            if not self._wait(550):
                return False

        # 超时仍无放弃：不硬点，避免误点绿键；交给主循环再试抽
        self._log("未见到「放弃」，按可继续抽处理（若卡住请检查界面）")
        return self._wait(300)

    def _reset_multiplier_if_needed(
        self,
        region: Region,
        ui_minus: Optional[Path],
        ui_coin300: Optional[Path],
        ui_coin100: Optional[Path],
        threshold: float,
    ) -> None:
        """无目标抽卡前：仅当明确是 300 且不像 100 时，点减号降回 1 倍。"""
        if ui_coin300 is None or ui_minus is None:
            return
        search = self._bottom_region(region)
        screen = grab_screen(search)
        tpl300 = load_template(str(ui_coin300))
        if tpl300 is None:
            return
        # 300 要很像才认，避免和 100 按钮误判
        m300 = match_template(screen, tpl300, 0.90)
        if not m300.found:
            return
        if ui_coin100 is not None:
            tpl100 = load_template(str(ui_coin100))
            if tpl100 is not None:
                m100 = match_template(screen, tpl100, 0.55)
                if m100.score >= m300.score:
                    return
        self._log(f"检测到已是 300/3倍(score={m300.score:.3f})，先点减号恢复正常抽")
        for _ in range(3):
            if not self._click_template(region, ui_minus, threshold, "减号", quiet=True):
                break
            if not self._wait(250):
                break

    def _find_best_hero(
        self,
        region: Region,
        loaded: Dict[str, object],
        threshold: float,
    ) -> Optional[Tuple[str, float, Tuple[int, int]]]:
        """在台上找勾选目标英雄，返回最佳匹配。"""
        screen = grab_screen(region)
        best: Optional[Tuple[str, float, Tuple[int, int]]] = None
        for name, tpl in loaded.items():
            matched = match_template(screen, tpl, threshold)
            if not matched.found:
                continue
            score = float(matched.score)
            center = (
                region.left + matched.center[0],
                region.top + matched.center[1],
            )
            if best is None or score > best[1]:
                best = (name, score, center)
        return best

    def _loop(self, config: Dict) -> None:
        gacha = normalize_gacha(config.get("gacha"))
        enabled = list(gacha.get("enabled_cards") or [])
        max_draws = int(gacha.get("max_draws", 0) or 0)
        settle_ms = int(gacha.get("settle_ms", 2800) or 0)
        threshold = float(gacha.get("match_threshold", config.get("match_threshold", 0.82)))
        no_hit = str(gacha.get("no_hit_action") or "正常抽卡")
        use_green_gem = bool(gacha.get("use_green_gem", False))
        use_max = bool(gacha.get("use_max_button", True))
        stop_on_hit = bool(gacha.get("stop_on_hit", False))
        title = str(config.get("target_window_title") or "").strip()

        session = new_gacha_session(enabled)
        hit_targets: List[str] = []
        stop_reason = "手动停止"

        try:
            if not enabled:
                self._log("请先勾选至少一张目标英雄")
                stop_reason = "未勾选目标"
                return

            card_paths = list_available_card_templates()
            watch = [n for n in enabled if n in card_paths]
            missing = [n for n in enabled if n not in card_paths]
            if missing:
                self._log(
                    "勾选英雄缺模板（跳过）: "
                    + "、".join(missing)
                    + " → templates/cards/<名>.png"
                )
            if not watch:
                self._log("没有可用的目标英雄模板，无法识别台上英雄")
                stop_reason = "无英雄模板"
                return

            # 默认只用金币招募，禁止绿砖/宝石按钮
            ui_coin_list = list_ui_templates(UI_TEMPLATES["recruit_coin"])
            ui_gem_list = list_ui_templates(UI_TEMPLATES["recruit_gem"])
            ui_max = find_ui_template(UI_TEMPLATES["max"])
            ui_refresh = find_ui_template(UI_TEMPLATES["refresh"])
            ui_abandon = find_ui_template(UI_TEMPLATES["abandon"])
            ui_minus = find_ui_template(UI_TEMPLATES["minus"])
            ui_coin300 = find_ui_template(["金币招募300"])

            img_root = ui_dir()
            need = []
            if not ui_coin_list:
                need.append(f"金币招募 → {img_root / '金币招募.png'}（绿键金币抽）")
            if use_max and ui_max is None:
                need.append(f"最大 → {img_root / '最大.png'}")
            if ui_abandon is None:
                need.append(f"放弃 → {img_root / '放弃.png'}（每次抽完必点）")
            if need:
                self._log("缺少必要按钮模板，无法开始：")
                for item in need:
                    self._log("  - " + item)
                stop_reason = "缺少UI模板"
                return

            loaded = {
                name: load_template(str(card_paths[name]))
                for name in watch
            }
            loaded = {k: v for k, v in loaded.items() if v is not None}
            if not loaded:
                self._log("英雄模板加载失败")
                stop_reason = "模板加载失败"
                return

            self._log(
                "开始常驻招募：目标 "
                + "、".join(watch)
                + "；无目标→金币抽→(有则)放弃"
                + "；有目标→最大→金币抽→(有则)放弃"
                + "；无放弃且可再抽则跳过"
                + (f"；最多 {max_draws} 次" if max_draws > 0 else "；不限次数")
                + ("；绿砖关" if not use_green_gem else "；绿砖开")
            )
            self._emit(session, hit_targets)

            draws = 0
            idle_rounds = 0
            while not self._stop.is_set():
                if max_draws > 0 and draws >= max_draws:
                    stop_reason = f"达到最大抽数 {max_draws}"
                    self._log(stop_reason)
                    break

                region = self._region(title)
                if region is None:
                    self._log("未找到游戏窗口，等待…")
                    if not self._wait(800):
                        stop_reason = "手动停止"
                        break
                    continue

                # 不抢前台：窗口可被拖走/被浏览器挡住，仍靠标题找窗+识图+后台点击
                enforce_locked_size(title)

                # 1) 有目标：最大 → 金币抽 → 放弃
                best = self._find_best_hero(region, loaded, threshold)
                if best is not None:
                    name, score, (_hx, _hy) = best
                    self._log(
                        f"识别到目标 [{name}] score={score:.3f}，最大→抽卡→放弃"
                    )
                    region = self._ensure_coin_mode(region, ui_coin_list, threshold)

                    if use_max:
                        region = self._region(title) or region
                        if ui_max is None:
                            self._log("有目标但缺少「最大.png」，无法把100变成300")
                        elif not self._click_template(region, ui_max, threshold, "最大"):
                            self._log("未点到「最大」，稍后重试")
                            if not self._wait(800):
                                stop_reason = "手动停止"
                                break
                            continue
                        if not self._wait(350):
                            stop_reason = "手动停止"
                            break

                    region = self._region(title) or region
                    if not self._click_any(region, ui_coin_list, threshold, "金币招募"):
                        self._log("未点到金币招募，稍后重试")
                        if not self._wait(800):
                            stop_reason = "手动停止"
                            break
                        continue

                    draws += 1
                    session["结果"]["总次数"] = draws
                    session["结果"][name] = int(session["结果"].get(name, 0)) + 1
                    session["结果"]["目标命中次数"] = int(
                        session["结果"].get("目标命中次数", 0) or 0
                    ) + 1
                    if name not in hit_targets:
                        hit_targets.append(name)
                    self._log(
                        f"目标统计 [{name}]={session['结果'][name]}，"
                        f"合计={session['结果']['目标命中次数']}"
                    )
                    self._emit(session, hit_targets)
                    idle_rounds = 0

                    if not self._after_draw_abandon(
                        title,
                        region,
                        ui_abandon,
                        threshold,
                        settle_ms,
                        ui_coin_list=ui_coin_list,
                    ):
                        stop_reason = "手动停止"
                        break

                    if stop_on_hit:
                        stop_reason = f"抽到目标并停止: {name}"
                        self._log(stop_reason)
                        break
                    continue

                # 2) 无目标：金币抽 →（有则）放弃；无放弃可直接再抽
                idle_rounds += 1
                region = self._region(title) or region
                region = self._ensure_coin_mode(region, ui_coin_list, threshold)
                coin100 = ui_coin_list[0] if ui_coin_list else None
                self._reset_multiplier_if_needed(
                    region, ui_minus, ui_coin300, coin100, threshold
                )
                region = self._region(title) or region
                acted = self._click_any(region, ui_coin_list, threshold, "正常抽卡")
                if acted:
                    draws += 1
                    session["结果"]["总次数"] = draws
                    self._emit(session, hit_targets)
                    self._log("无目标，已正常金币抽，检查是否需要放弃")
                    idle_rounds = 0
                    if not self._after_draw_abandon(
                        title,
                        region,
                        ui_abandon,
                        threshold,
                        settle_ms,
                        ui_coin_list=ui_coin_list,
                    ):
                        stop_reason = "手动停止"
                        break
                    continue

                if idle_rounds >= 8:
                    self._log("多次未识别到目标/金币按钮，检查模板与阈值")
                    idle_rounds = 0
                if not self._wait(700):
                    stop_reason = "手动停止"
                    break

        except Exception as exc:  # noqa: BLE001
            stop_reason = f"异常: {exc}"
            self._log(f"抽卡异常: {exc}")
        finally:
            session["结束时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            session["结果"]["命中目标"] = hit_targets
            session["停止原因"] = stop_reason
            try:
                append_gacha_result(session)
                self._log(f"抽卡结果已写入 saves/gacha_result.json（{stop_reason}）")
            except Exception as exc:  # noqa: BLE001
                self._log(f"写入抽卡结果失败: {exc}")
            self._emit(session, hit_targets)
            self.running = False
            try:
                self._on_finished()
            except Exception:  # noqa: BLE001
                pass

    def _emit(self, session: Dict, hit_targets: List[str]) -> None:
        result = session.get("结果", {}) or {}
        targets = list(session.get("目标卡") or [])
        counts = {
            name: int(result.get(name, 0) or 0)
            for name in targets
        }
        info = {
            "draws": int(result.get("总次数", 0) or 0),
            "target_hits": int(result.get("目标命中次数", 0) or 0),
            "hits": list(hit_targets),
            "counts": counts,
        }
        try:
            self._on_progress(info)
        except Exception:  # noqa: BLE001
            pass
