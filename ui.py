"""
小蛋菜单栏 UI — 自定义视图、NSApplicationDelegate、菜单构建
"""

import os
import sqlite3
import threading
import webbrowser
from datetime import date, datetime, timedelta

import objc
from Foundation import NSObject, NSTimer, NSDistributedNotificationCenter

try:
    from AppKit import (
        NSView, NSBezierPath, NSColor, NSFont, NSAttributedString,
        NSFontAttributeName, NSForegroundColorAttributeName,
        NSParagraphStyleAttributeName, NSMutableParagraphStyle,
        NSApplication, NSStatusBar, NSMenu, NSMenuItem,
        NSVariableStatusItemLength,
        NSWindow, NSButton, NSTextField, NSBox, NSSegmentedControl,
    )
except ImportError:
    raise SystemExit("缺少依赖，请运行：pip install pyobjc-framework-Cocoa")

try:
    from wellness import get_random_activity
except ImportError:
    def get_random_activity(category=None):
        return None

try:
    from analyzer import get_report, generate_report
except ImportError:
    def get_report(date_str): return None
    def generate_report(date_str): return None

try:
    from report_window import show_report_window
except ImportError:
    def show_report_window(): pass

from tracker import get_category_stats, POLL_INTERVAL, APP_SUPPORT_DIR


# ── 图表常量 & UI 辅助 ────────────────────────────────────────────────────────

CATEGORY_ORDER = ["学校学习", "自主学习", "娱乐", "其他"]

_CAT_RGB = {
    "学校学习": (0x5B / 255, 0x8D / 255, 0xEF / 255),
    "自主学习": (0x9B / 255, 0x72 / 255, 0xCF / 255),
    "娱乐":     (0x4E / 255, 0xCD / 255, 0xC4 / 255),
    "其他":     (0xB8 / 255, 0xBC / 255, 0xC8 / 255),
}

# 目标硬编码（后续可持久化）
_GOALS = [
    ("自主学习", 3 * 3600, "目标"),
    ("学校学习", 2 * 3600, "目标"),
    ("娱乐",     2 * 3600, "上限"),
]


def _fmt_dur(secs: float) -> str:
    """将秒数格式化为「Xh Ym」（≥1h）或「Ym」（<1h）。"""
    total_m = max(0, int(secs)) // 60
    h, m = divmod(total_m, 60)
    return f"{h}h {m:02d}m" if h > 0 else f"{m}m"


def _nscolor(cat: str) -> "NSColor":
    r, g, b = _CAT_RGB.get(cat, _CAT_RGB["其他"])
    return NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1.0)


# ── 图表视图 ──────────────────────────────────────────────────────────────────

class DonutChartView(NSView):
    """
    环形图（240×120pt）。
    用法：view._stats = get_today_category_stats(); view.setNeedsDisplay_(True)
    """

    def initWithFrame_(self, frame):
        self = objc.super(DonutChartView, self).initWithFrame_(frame)
        if self is not None:
            self._stats = {}
        return self

    def isOpaque(self):
        return False

    def drawRect_(self, dirty):
        stats = getattr(self, "_stats", {})
        total = sum(v["total"] for v in stats.values()) if stats else 0.0

        # ── 圆环参数（坐标原点左下角，y 向上）────────────────────────────────
        cx, cy = 62.0, 60.0
        outer_r = 36.0
        ring_w = round(outer_r * 0.35)        # ≈ 13pt，圆环宽度约为外径的35%
        mid_r = outer_r - ring_w / 2.0        # stroke 弧线半径

        # 灰色背景环（完整圆）
        bg_arc = NSBezierPath.bezierPath()
        bg_arc.setLineWidth_(ring_w)
        bg_arc.setLineCapStyle_(0)             # NSLineCapStyleButt = 0，端点不延伸
        bg_arc.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
            (cx, cy), mid_r, 90.0, -270.0, True
        )
        NSColor.colorWithSRGBRed_green_blue_alpha_(0.921, 0.921, 0.921, 1.0).setStroke()
        bg_arc.stroke()

        # 各分类扇形（顺时针，从12点方向开始）
        if total > 0:
            angle = 90.0
            for cat in CATEGORY_ORDER:
                secs = stats.get(cat, {}).get("total", 0.0)
                if secs <= 0:
                    continue
                sweep = secs / total * 360.0
                end_angle = angle - sweep
                seg = NSBezierPath.bezierPath()
                seg.setLineWidth_(ring_w)
                seg.setLineCapStyle_(0)
                seg.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
                    (cx, cy), mid_r, angle, end_angle, True
                )
                _nscolor(cat).setStroke()
                seg.stroke()
                angle = end_angle

        # ── 圆心两行文字 ─────────────────────────────────────────────────────
        study = (stats.get("学校学习", {}).get("total", 0.0)
                 + stats.get("自主学习", {}).get("total", 0.0))
        pct = int(round(study / total * 100)) if total > 0 else 0
        gray = NSColor.colorWithSRGBRed_green_blue_alpha_(0.557, 0.557, 0.576, 1.0)
        dark = NSColor.colorWithSRGBRed_green_blue_alpha_(0.110, 0.110, 0.118, 1.0)

        lbl = NSAttributedString.alloc().initWithString_attributes_(
            "学习",
            {NSFontAttributeName: NSFont.systemFontOfSize_(9.0),
             NSForegroundColorAttributeName: gray},
        )
        pct_t = NSAttributedString.alloc().initWithString_attributes_(
            f"{pct}%",
            {NSFontAttributeName: NSFont.boldSystemFontOfSize_(14.0),
             NSForegroundColorAttributeName: dark},
        )
        # 上行「学习」baseline 在圆心上方 3pt，下行「XX%」baseline 在圆心下方 13pt
        lbl.drawAtPoint_((cx - lbl.size().width / 2.0, cy + 3.0))
        pct_t.drawAtPoint_((cx - pct_t.size().width / 2.0, cy - 13.0))

        # ── 右侧图例 ─────────────────────────────────────────────────────────
        lx = 118.0            # 图例区左边缘 x
        box_top0 = 104.0      # 第一个色块顶部 y（从底部算）
        row_gap = 22.0        # 行间距（色块顶到下一行色块顶）

        txt_attrs = {NSFontAttributeName: NSFont.systemFontOfSize_(10.0),
                     NSForegroundColorAttributeName: gray}

        for i, cat in enumerate(CATEGORY_ORDER):
            box_top = box_top0 - i * row_gap
            box_bot = box_top - 7.0            # 色块底部（7pt 高）

            # 彩色方块（7×7，圆角 1.5pt）
            _nscolor(cat).set()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                ((lx, box_bot), (7.0, 7.0)), 1.5, 1.5
            ).fill()

            # 短名（2字）+ 时长
            secs = stats.get(cat, {}).get("total", 0.0)
            line_t = NSAttributedString.alloc().initWithString_attributes_(
                f"{cat[:2]}  {_fmt_dur(secs)}", txt_attrs
            )
            line_t.drawAtPoint_((lx + 10.0, box_bot))


class BarChartView(NSView):
    """
    横向条形图（240×120pt），按时长降序排列。
    用法：view._stats = get_today_category_stats(); view.setNeedsDisplay_(True)
    """

    def initWithFrame_(self, frame):
        self = objc.super(BarChartView, self).initWithFrame_(frame)
        if self is not None:
            self._stats = {}
        return self

    def isOpaque(self):
        return False

    def drawRect_(self, dirty):
        stats = getattr(self, "_stats", {})

        # 按时长降序排序
        cats = sorted(CATEGORY_ORDER,
                      key=lambda c: stats.get(c, {}).get("total", 0.0),
                      reverse=True)
        max_secs = max((stats.get(c, {}).get("total", 0.0) for c in cats), default=0.0)
        if max_secs <= 0:
            max_secs = 1.0

        n = len(cats)
        bar_h = 6.0
        inner_gap = 3.0           # 标签到条形的垂直间距
        label_h = 11.0            # 11pt 字体的视觉行高（baseline 到 cap-top）
        row_h = label_h + inner_gap + bar_h   # 22pt
        inter_gap = 9.0           # 行与行之间的间距

        block_h = n * row_h + (n - 1) * inter_gap   # ≈ 115pt
        bot_margin = (120.0 - block_h) / 2.0         # 上下各 ≈ 2.5pt

        pad_l, pad_r = 8.0, 8.0
        avail_w = 240.0 - pad_l - pad_r              # 224pt

        gray = NSColor.colorWithSRGBRed_green_blue_alpha_(0.557, 0.557, 0.576, 1.0)
        bg_col = NSColor.colorWithSRGBRed_green_blue_alpha_(0.921, 0.921, 0.921, 1.0)
        txt_attrs = {NSFontAttributeName: NSFont.systemFontOfSize_(11.0),
                     NSForegroundColorAttributeName: gray}

        for i, cat in enumerate(cats):
            # 行 0（最长）在顶部 → 最大 y 值；NSView y 从底部向上
            bar_bot = bot_margin + (n - 1 - i) * (row_h + inter_gap)
            lbl_baseline = bar_bot + bar_h + inner_gap

            secs = stats.get(cat, {}).get("total", 0.0)
            fill_w = (secs / max_secs) * avail_w

            # 背景灰条
            bg_col.set()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                ((pad_l, bar_bot), (avail_w, bar_h)), 3.0, 3.0
            ).fill()

            # 彩色填充条
            if fill_w >= 1.0:
                _nscolor(cat).set()
                NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    ((pad_l, bar_bot), (fill_w, bar_h)), 3.0, 3.0
                ).fill()

            # 分类名（左对齐）
            cat_t = NSAttributedString.alloc().initWithString_attributes_(cat, txt_attrs)
            cat_t.drawAtPoint_((pad_l, lbl_baseline))

            # 时长（右对齐）
            dur_t = NSAttributedString.alloc().initWithString_attributes_(
                _fmt_dur(secs), txt_attrs
            )
            dur_t.drawAtPoint_((240.0 - pad_r - dur_t.size().width, lbl_baseline))


# ── 菜单栏自定义视图 ──────────────────────────────────────────────────────────

class BriefRowView(NSView):
    """
    简报分类行（260 × 46pt 含二级分类 / 32pt 无二级分类）。
    使用前设置 _cat / _total / _subs，再调用 setNeedsDisplay_(True)。
    """

    def initWithFrame_(self, frame):
        self = objc.super(BriefRowView, self).initWithFrame_(frame)
        if self is not None:
            self._cat = ""
            self._total = 0.0
            self._subs = {}
        return self

    def isOpaque(self):
        return False

    def drawRect_(self, dirty):
        cat   = getattr(self, "_cat",   "")
        total = getattr(self, "_total", 0.0)
        subs  = getattr(self, "_subs",  {})

        H        = self.frame().size.height   # 46 或 32
        has_subs = bool(subs)
        cat_y    = H - 20.0                   # 分类名 baseline（非翻转，从底部算）
        dot_cy   = cat_y + 4.0               # 圆点中心 y（与分类名视觉对齐）

        gray = NSColor.colorWithSRGBRed_green_blue_alpha_(0.557, 0.557, 0.576, 1.0)
        dark = NSColor.colorWithSRGBRed_green_blue_alpha_(0.110, 0.110, 0.118, 1.0)

        # ── 彩色圆点（8pt 直径） ─────────────────────────────────────────────
        _nscolor(cat).set()
        NSBezierPath.bezierPathWithOvalInRect_(
            ((14.0, dot_cy - 4.0), (8.0, 8.0))
        ).fill()

        # ── 分类名 ───────────────────────────────────────────────────────────
        name_t = NSAttributedString.alloc().initWithString_attributes_(
            cat,
            {NSFontAttributeName: NSFont.systemFontOfSize_(13.0),
             NSForegroundColorAttributeName: gray if cat == "其他" else dark},
        )
        name_t.drawAtPoint_((34.0, cat_y))

        # ── 时长（右对齐） ───────────────────────────────────────────────────
        dur_t = NSAttributedString.alloc().initWithString_attributes_(
            _fmt_dur(total),
            {NSFontAttributeName: NSFont.systemFontOfSize_(13.0),
             NSForegroundColorAttributeName: gray},
        )
        dur_t.drawAtPoint_((260.0 - 14.0 - dur_t.size().width, cat_y))

        # ── 二级分类小字（11pt 灰色） ─────────────────────────────────────────
        if has_subs:
            sub_str = " · ".join(
                f"{k} {_fmt_dur(v)}"
                for k, v in sorted(subs.items(), key=lambda x: -x[1])
            )
            sub_t = NSAttributedString.alloc().initWithString_attributes_(
                sub_str,
                {NSFontAttributeName: NSFont.systemFontOfSize_(11.0),
                 NSForegroundColorAttributeName: gray},
            )
            sub_t.drawAtPoint_((34.0, 9.0))


class GoalRowView(NSView):
    """
    目标进度行（260 × 50pt）。
    使用前设置 _cat / _actual / _goal_secs / _goal_label / _pct。
    """

    def initWithFrame_(self, frame):
        self = objc.super(GoalRowView, self).initWithFrame_(frame)
        if self is not None:
            self._cat       = ""
            self._actual    = 0.0
            self._goal_secs = 3600.0
            self._goal_label = "目标"
            self._pct       = 0.0
        return self

    def isOpaque(self):
        return False

    def drawRect_(self, dirty):
        cat        = getattr(self, "_cat",        "")
        actual     = getattr(self, "_actual",     0.0)
        goal_secs  = getattr(self, "_goal_secs",  3600.0)
        goal_label = getattr(self, "_goal_label", "目标")
        pct        = getattr(self, "_pct",        0.0)

        gray  = NSColor.colorWithSRGBRed_green_blue_alpha_(0.557, 0.557, 0.576, 1.0)
        dark  = NSColor.colorWithSRGBRed_green_blue_alpha_(0.110, 0.110, 0.118, 1.0)
        color = _nscolor(cat)
        pad   = 14.0

        # ── 分类名（baseline ≈ 34pt from bottom） ────────────────────────────
        name_t = NSAttributedString.alloc().initWithString_attributes_(
            cat,
            {NSFontAttributeName: NSFont.systemFontOfSize_(13.0),
             NSForegroundColorAttributeName: dark},
        )
        name_t.drawAtPoint_((pad, 34.0))

        # ── 百分比（右对齐，同行，彩色） ─────────────────────────────────────
        pct_t = NSAttributedString.alloc().initWithString_attributes_(
            f"{int(round(pct * 100))}%",
            {NSFontAttributeName: NSFont.boldSystemFontOfSize_(11.0),
             NSForegroundColorAttributeName: color},
        )
        pct_t.drawAtPoint_((260.0 - pad - pct_t.size().width, 34.0))

        # ── 进度说明（baseline ≈ 20pt from bottom） ──────────────────────────
        prog_t = NSAttributedString.alloc().initWithString_attributes_(
            f"今日 {_fmt_dur(actual)} / {goal_label} {_fmt_dur(goal_secs)}",
            {NSFontAttributeName: NSFont.systemFontOfSize_(11.0),
             NSForegroundColorAttributeName: gray},
        )
        prog_t.drawAtPoint_((pad, 20.0))

        # ── 进度条（bottom=8pt，height=3pt） ─────────────────────────────────
        bar_w = 260.0 - pad * 2

        NSColor.colorWithSRGBRed_green_blue_alpha_(0.921, 0.921, 0.921, 1.0).set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            ((pad, 8.0), (bar_w, 3.0)), 1.5, 1.5
        ).fill()

        fill_w = max(0.0, min(bar_w, pct * bar_w))
        if fill_w >= 1.0:
            color.set()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                ((pad, 8.0), (fill_w, 3.0)), 1.5, 1.5
            ).fill()


class DateNavView(NSView):
    """横向日期导航条：‹  日期  ›，嵌入单个 NSMenuItem。"""

    def initWithFrame_(self, frame):
        self = objc.super(DateNavView, self).initWithFrame_(frame)
        if self is not None:
            self._delegate   = None
            self._date_label = ""
            self._is_today   = True
        return self

    @objc.python_method
    def setup(self, delegate, date_label, is_today):
        self._delegate   = delegate
        self._date_label = date_label
        self._is_today   = is_today
        return self

    def drawRect_(self, dirty_rect):
        NSColor.clearColor().set()
        NSBezierPath.fillRect_(self.bounds())

        dark  = NSColor.colorWithSRGBRed_green_blue_alpha_(0.110, 0.110, 0.118, 1.0)
        gray  = NSColor.colorWithSRGBRed_green_blue_alpha_(0.557, 0.557, 0.576, 1.0)
        faded = NSColor.colorWithSRGBRed_green_blue_alpha_(0.75,  0.75,  0.77,  1.0)

        arrow_font = NSFont.systemFontOfSize_(15.0)
        label_font = NSFont.systemFontOfSize_(11.0)

        def _draw_centered(text, color, font, rx, ry, rw, rh):
            ns_str = NSAttributedString.alloc().initWithString_attributes_(
                text, {NSFontAttributeName: font,
                       NSForegroundColorAttributeName: color})
            sz = ns_str.size()
            ns_str.drawAtPoint_((rx + (rw - sz.width)  / 2.0,
                                 ry + (rh - sz.height) / 2.0))

        _draw_centered("‹", dark, arrow_font, 0, 0, 36, 36)
        _draw_centered(self._date_label, gray, label_font, 36, 0, 188, 36)
        _draw_centered("›",
                       faded if self._is_today else dark,
                       arrow_font, 224, 0, 36, 36)

    def mouseUp_(self, event):
        if self._delegate is None:
            return
        x = self.convertPoint_fromView_(event.locationInWindow(), None).x
        if x < 36:
            self._delegate.prevDay_(None)
        elif x > 224 and not self._is_today:
            self._delegate.nextDay_(None)

    def acceptsFirstMouse_(self, event):
        return True

    def isOpaque(self):
        return False


class WellnessCardView(NSView):
    """今日小憩卡片（260×80pt）。使用前设置 _activity / _delegate。"""

    def initWithFrame_(self, frame):
        self = objc.super(WellnessCardView, self).initWithFrame_(frame)
        if self is not None:
            self._activity  = None
            self._delegate  = None
            self._btn_min_x = 9999.0
        return self

    def isOpaque(self):
        return False

    def drawRect_(self, dirty):
        activity = getattr(self, "_activity", None)
        if not activity:
            return

        gray = NSColor.colorWithSRGBRed_green_blue_alpha_(0.557, 0.557, 0.576, 1.0)
        dark = NSColor.colorWithSRGBRed_green_blue_alpha_(0.110, 0.110, 0.118, 1.0)
        pad  = 14.0

        small_attrs = {
            NSFontAttributeName: NSFont.systemFontOfSize_(10.0),
            NSForegroundColorAttributeName: gray,
        }

        # 第一行：标题（10pt 灰色）
        NSAttributedString.alloc().initWithString_attributes_(
            "🌱 今日小憩", small_attrs
        ).drawAtPoint_((pad, 64.0))

        # 第二行：活动文字（13pt 主色，自动换行）
        para = NSMutableParagraphStyle.alloc().init()
        para.setLineBreakMode_(1)  # NSLineBreakByWordWrapping
        NSAttributedString.alloc().initWithString_attributes_(
            activity.get("text", ""),
            {
                NSFontAttributeName: NSFont.systemFontOfSize_(13.0),
                NSForegroundColorAttributeName: dark,
                NSParagraphStyleAttributeName: para,
            },
        ).drawInRect_(((pad, 32.0), (260.0 - pad * 2, 30.0)))

        # 第三行：类别·时长（左），换一个（右）
        cat = activity.get("category", "")
        dur = activity.get("duration", 5)
        NSAttributedString.alloc().initWithString_attributes_(
            f"{cat} · 约{dur}分钟", small_attrs
        ).drawAtPoint_((pad, 12.0))

        btn_t = NSAttributedString.alloc().initWithString_attributes_("换一个", small_attrs)
        self._btn_min_x = 260.0 - pad - btn_t.size().width
        btn_t.drawAtPoint_((self._btn_min_x, 12.0))

    def mouseUp_(self, event):
        pt = self.convertPoint_fromView_(event.locationInWindow(), None)
        if pt.y < 28.0 and pt.x >= getattr(self, "_btn_min_x", 9999.0):
            delegate = getattr(self, "_delegate", None)
            if delegate is not None:
                delegate.refreshWellness_(None)

    def acceptsFirstMouse_(self, event):
        return True


# ── 可点击图表/简报容器 ────────────────────────────────────────────────────────

class _ChartClickView(NSView):
    """图表区域的容器 View，点击时通知 delegate 切换简报/图表。"""
    def mouseUp_(self, event):
        if getattr(self, "_delegate", None) is not None:
            self._delegate.toggleReport_(self)

    def acceptsFirstMouse_(self, event):
        return True


# ── NSApplicationDelegate ─────────────────────────────────────────────────────

class XiaoDanDelegate(NSObject):
    """
    NSApplicationDelegate — 管理状态栏图标、菜单构建与页面切换。
    追踪逻辑在独立后台线程运行，本类只负责 UI。
    """

    # ── 初始化 ───────────────────────────────────────────────────────────────
    def init(self):
        self = objc.super(XiaoDanDelegate, self).init()
        if self is not None:
            self._conn              = None    # sqlite3.Connection，由 start_ui() 注入
            self._status_item       = None
            self._chart_mode        = "donut"
            self._view_date         = date.today()
            self._wellness_activity = None
            self._wellness_date     = None
            self._wellness_enabled  = False
            self._menu              = None    # 当前首页 NSMenu（用于 menuWillOpen_ 对比）
            self._report_time       = (19, 0)
            self._show_report       = False   # False=图表，True=简报
            self._generating_report = False   # 防止并发生成
            self._last_recheck_time = None    # 每小时触发一次 --recheck-other
        return self

    # ── NSApplicationDelegate ────────────────────────────────────────────────
    def applicationDidFinishLaunching_(self, notification):
        bar = NSStatusBar.systemStatusBar()
        self._status_item = bar.statusItemWithLength_(NSVariableStatusItemLength)
        self._status_item.button().setTitle_("…")
        self._show_home()
        self._menu = self._status_item.menu()
        self._menu.setDelegate_(self)
        self.refreshTitle_(None)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            float(POLL_INTERVAL), self, "refreshTitle:", None, True
        )
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            60.0, self, "checkReport:", None, True
        )
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            3600.0, self, "checkOtherCategory:", None, True
        )
        NSDistributedNotificationCenter.defaultCenter() \
            .addObserver_selector_name_object_(
                self, "onClassifierDone:", "XiaoDanClassifierDone", None
            )

    # ── ObjC 可见方法（NSTimer 回调 & NSMenuItem actions） ──────────────────
    def refreshTitle_(self, timer):
        try:
            if not self._conn:
                return
            today_str = date.today().strftime("%Y-%m-%d")
            stats = get_category_stats(self._conn, today_str)
            total = sum(v["total"] for v in stats.values())
            self._status_item.button().setTitle_(_fmt_dur(total) if total > 0 else "小蛋")
        except Exception:
            self._status_item.button().setTitle_("🥚")

    def showHome_(self, sender):
        self._show_home()

    @objc.python_method
    def _show_settings(self):
        menu = self._build_settings_menu()
        old = self._status_item.menu()
        if old:
            old.cancelTracking()
        self._status_item.setMenu_(menu)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.0, self, "reopenMenu:", None, False
        )

    @objc.python_method
    def _build_settings_menu(self):
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        self._settings_views = []  # 防 GC

        back = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "← 返回", "showHome:", "")
        back.setTarget_(self)
        menu.addItem_(back)
        menu.addItem_(NSMenuItem.separatorItem())

        # ── 分组标题「显示」
        display_header = NSView.alloc().initWithFrame_(((0, 0), (260, 24)))
        tf = NSTextField.alloc().initWithFrame_(((16, 4), (228, 16)))
        tf.setStringValue_("显示")
        tf.setEditable_(False)
        tf.setSelectable_(False)
        tf.setBezeled_(False)
        tf.setDrawsBackground_(False)
        tf.setFont_(NSFont.systemFontOfSize_(11))
        tf.setTextColor_(NSColor.labelColor())
        display_header.addSubview_(tf)
        self._settings_views.append(display_header)
        display_header_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", None, "")
        display_header_item.setView_(display_header)
        display_header_item.setEnabled_(False)
        menu.addItem_(display_header_item)

        # ── 图表类型行
        chart_row = NSView.alloc().initWithFrame_(((0, 0), (260, 30)))
        lbl = NSTextField.alloc().initWithFrame_(((16, 6), (100, 18)))
        lbl.setStringValue_("图表类型")
        lbl.setEditable_(False)
        lbl.setSelectable_(False)
        lbl.setBezeled_(False)
        lbl.setDrawsBackground_(False)
        lbl.setFont_(NSFont.systemFontOfSize_(13))
        chart_row.addSubview_(lbl)
        seg = NSSegmentedControl.alloc().initWithFrame_(((108, 4), (140, 22)))
        seg.setSegmentCount_(2)
        seg.setLabel_forSegment_("甜甜圈", 0)
        seg.setLabel_forSegment_("柱状图", 1)
        seg.setSelectedSegment_(0 if self._chart_mode == "donut" else 1)
        seg.setTarget_(self)
        seg.setAction_("toggleChartType:")
        chart_row.addSubview_(seg)
        self._settings_views.append(chart_row)
        chart_row_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", None, "")
        chart_row_item.setView_(chart_row)
        chart_row_item.setEnabled_(False)
        menu.addItem_(chart_row_item)

        # ── 今日小憩行
        wellness_row = NSView.alloc().initWithFrame_(((0, 0), (260, 30)))
        lbl2 = NSTextField.alloc().initWithFrame_(((16, 6), (100, 18)))
        lbl2.setStringValue_("今日小憩")
        lbl2.setEditable_(False)
        lbl2.setSelectable_(False)
        lbl2.setBezeled_(False)
        lbl2.setDrawsBackground_(False)
        lbl2.setFont_(NSFont.systemFontOfSize_(13))
        wellness_row.addSubview_(lbl2)
        chk = NSButton.alloc().initWithFrame_(((220, 4), (22, 22)))
        chk.setButtonType_(3)  # NSButtonTypeSwitch
        chk.setTitle_("")
        chk.setState_(1 if self._wellness_enabled else 0)
        chk.setTarget_(self)
        chk.setAction_("toggleWellness:")
        wellness_row.addSubview_(chk)
        self._settings_views.append(wellness_row)
        wellness_row_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", None, "")
        wellness_row_item.setView_(wellness_row)
        wellness_row_item.setEnabled_(False)
        menu.addItem_(wellness_row_item)

        menu.addItem_(NSMenuItem.separatorItem())

        # ── 分组标题「简报」
        brief_header = NSView.alloc().initWithFrame_(((0, 0), (260, 24)))
        tf2 = NSTextField.alloc().initWithFrame_(((16, 4), (228, 16)))
        tf2.setStringValue_("简报")
        tf2.setEditable_(False)
        tf2.setSelectable_(False)
        tf2.setBezeled_(False)
        tf2.setDrawsBackground_(False)
        tf2.setFont_(NSFont.systemFontOfSize_(11))
        tf2.setTextColor_(NSColor.labelColor())
        brief_header.addSubview_(tf2)
        self._settings_views.append(brief_header)
        brief_header_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", None, "")
        brief_header_item.setView_(brief_header)
        brief_header_item.setEnabled_(False)
        menu.addItem_(brief_header_item)

        # ── 简报时间行
        time_row = NSView.alloc().initWithFrame_(((0, 0), (260, 30)))
        lbl3 = NSTextField.alloc().initWithFrame_(((16, 6), (100, 18)))
        lbl3.setStringValue_("简报时间")
        lbl3.setEditable_(False)
        lbl3.setSelectable_(False)
        lbl3.setBezeled_(False)
        lbl3.setDrawsBackground_(False)
        lbl3.setFont_(NSFont.systemFontOfSize_(13))
        time_row.addSubview_(lbl3)
        time_field = NSTextField.alloc().initWithFrame_(((184, 4), (60, 22)))
        time_field.setStringValue_(
            f"{self._report_time[0]:02d}:{self._report_time[1]:02d}")
        time_field.setBezeled_(True)
        time_field.setEditable_(True)
        time_field.setDelegate_(self)
        time_row.addSubview_(time_field)
        self._settings_views.append(time_row)
        time_row_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", None, "")
        time_row_item.setView_(time_row)
        time_row_item.setEnabled_(False)
        menu.addItem_(time_row_item)
        return menu

    def showBrief_(self, sender):
        self._show_brief()

    def showGoals_(self, sender):
        self._show_goals()

    def toggleChart_(self, sender):
        self._chart_mode = "bar" if self._chart_mode == "donut" else "donut"
        self._show_home()

    def prevDay_(self, sender):
        self._view_date -= timedelta(days=1)
        self._show_home()

    def nextDay_(self, sender):
        if self._view_date < date.today():
            self._view_date += timedelta(days=1)
            self._show_home()

    def refreshWellness_(self, sender):
        self._wellness_activity = get_random_activity()
        self._wellness_date = date.today()
        self._show_home()

    def setChartDonut_(self, sender):
        self._chart_mode = "donut"
        self._show_home()

    def setChartBar_(self, sender):
        self._chart_mode = "bar"
        self._show_home()

    def toggleWellness_(self, sender):
        self._wellness_enabled = not self._wellness_enabled
        self._show_settings()

    def toggleChartType_(self, sender):
        self._chart_mode = "donut" if sender.selectedSegment() == 0 else "bar"
        self._show_settings()

    def controlTextDidEndEditing_(self, notification):
        import re
        field = notification.object()
        text = field.stringValue().strip()
        m = re.match(r'^(\d{1,2}):(\d{2})$', text)
        if m:
            h, mn = int(m.group(1)), int(m.group(2))
            if 17 <= h <= 23 and 0 <= mn <= 59:
                self._report_time = (h, mn)
                return
        field.setStringValue_(
            f"{self._report_time[0]:02d}:{self._report_time[1]:02d}")

    def toggleReport_(self, sender):
        self._show_report = not self._show_report
        self._show_home()

    def checkReport_(self, timer):
        today_str = str(date.today())
        if datetime.now().hour >= self._report_time[0] and get_report(today_str) is None:
            if self._generating_report:
                return
            self._generating_report = True
            def _gen():
                try:
                    generate_report(today_str)
                except Exception:
                    pass
                finally:
                    self._generating_report = False
                if self._show_report:
                    self.performSelectorOnMainThread_withObject_waitUntilDone_(
                        "refreshAfterReport:", None, False
                    )
            threading.Thread(target=_gen, daemon=True).start()

    def onClassifierDone_(self, notification):
        from datetime import date
        if str(self._view_date) == str(date.today()):
            menu = self._status_item.menu()
            if menu and menu is self._menu:
                self._build_home_menu(menu)

    def checkOtherCategory_(self, timer):
        from datetime import date as _date, datetime as _datetime
        import sqlite3 as _sqlite3, os as _os, subprocess as _subprocess, sys as _sys
        now = _datetime.now()
        if (self._last_recheck_time and
                self._last_recheck_time.date() == now.date() and
                self._last_recheck_time.hour == now.hour):
            return
        db = _os.path.expanduser("~/Library/Application Support/XiaoDan/activity.db")
        try:
            conn = _sqlite3.connect(db)
            row = conn.execute("""
                SELECT SUM(gap) FROM (
                    SELECT CAST(
                        (JULIANDAY(LEAD(timestamp) OVER (ORDER BY timestamp))
                         - JULIANDAY(timestamp)) * 86400 AS INTEGER
                    ) as gap, category
                    FROM activity_log
                    WHERE date = ? AND activity_type NOT IN ('idle','dock')
                ) WHERE category LIKE '其他%' AND gap > 0 AND gap <= 600
            """, (str(_date.today()),)).fetchone()
            conn.close()
            total_seconds = row[0] or 0
        except Exception:
            return
        if total_seconds <= 1800:
            return
        self._last_recheck_time = now
        script = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "classifier.py")
        _subprocess.Popen(
            [_sys.executable, script, "--recheck-other"],
            stdout=_subprocess.DEVNULL,
            stderr=_subprocess.DEVNULL,
        )

    def retryReport_(self, sender):
        target_str = str(self._view_date)
        if self._generating_report:
            return
        self._generating_report = True
        def _gen():
            try:
                generate_report(target_str)
            except Exception:
                pass
            finally:
                self._generating_report = False
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "refreshAfterReport:", None, False
            )
        threading.Thread(target=_gen, daemon=True).start()

    def refreshAfterReport_(self, _):
        if self._show_report:
            self._show_home()

    def openReport_(self, sender):
        show_report_window()

    def showSettings_(self, sender):
        self._show_settings()

    def menuWillOpen_(self, menu):
        if getattr(self, "_menu", None) is menu:
            self._build_home_menu(menu)

    # ── 页面切换（Python only） ──────────────────────────────────────────────
    @objc.python_method
    def _show_home(self):
        menu = self._build_home_menu()
        menu.setDelegate_(self)
        self._menu = menu
        old = self._status_item.menu()
        if old:
            old.cancelTracking()
        self._status_item.setMenu_(menu)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.0, self, "reopenMenu:", None, False
        )

    @objc.python_method
    def _show_brief(self):
        menu = self._build_brief_menu()
        old = self._status_item.menu()
        if old:
            old.cancelTracking()
        self._status_item.setMenu_(menu)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.0, self, "reopenMenu:", None, False
        )

    @objc.python_method
    def _show_goals(self):
        menu = self._build_goals_menu()
        old = self._status_item.menu()
        if old:
            old.cancelTracking()
        self._status_item.setMenu_(menu)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.0, self, "reopenMenu:", None, False
        )

    def reopenMenu_(self, _timer):
        self._status_item.button().performClick_(None)

    @objc.python_method
    def _get_stats(self):
        return get_category_stats(self._conn, str(self._view_date)) if self._conn else {}

    # ── 简报视图构建 ──────────────────────────────────────────────────────────
    @objc.python_method
    def _build_report_view(self):
        today = date.today()
        is_today = (self._view_date == today)
        date_str = str(self._view_date)
        gray = NSColor.secondaryLabelColor()

        container = _ChartClickView.alloc().initWithFrame_(((0, 0), (260, 120)))
        container._delegate = self

        def _label(text, y, h, centered=True):
            tf = NSTextField.alloc().initWithFrame_(((0, y), (260, h)))
            tf.setStringValue_(text)
            tf.setEditable_(False)
            tf.setSelectable_(False)
            tf.setBezeled_(False)
            tf.setDrawsBackground_(False)
            tf.setFont_(NSFont.systemFontOfSize_(13))
            tf.setTextColor_(gray)
            if centered:
                tf.setAlignment_(1)  # NSTextAlignmentCenter
            return tf

        if is_today and datetime.now().hour < self._report_time[0]:
            # 情况A：还没到简报时间
            container.addSubview_(_label(
                f"{self._report_time[0]:02d}:00 之后再来查看吧", 45, 30))
            return container

        report_text = get_report(date_str)

        if report_text:
            # 情况B：有简报
            tf = NSTextField.alloc().initWithFrame_(((16, 8), (228, 104)))
            tf.setStringValue_(report_text)
            tf.setEditable_(False)
            tf.setSelectable_(False)
            tf.setBezeled_(False)
            tf.setDrawsBackground_(False)
            tf.setFont_(NSFont.systemFontOfSize_(13))
            tf.setTextColor_(NSColor.colorWithWhite_alpha_(0.3, 1.0))
            tf.cell().setWraps_(True)
            container.addSubview_(tf)
        else:
            # 无简报：今天（已过时间）或历史日期，均显示生成按钮
            label_text = "暂时无法获取简报" if is_today else "暂无简报"
            container.addSubview_(_label(label_text, 50, 30))
            btn_title = "↻ 重试" if is_today else "↻ 生成"
            retry = NSButton.alloc().initWithFrame_(((204, 4), (50, 18)))
            retry.setTitle_(btn_title)
            retry.setFont_(NSFont.systemFontOfSize_(10))
            retry.setBordered_(False)
            retry.setTarget_(self)
            retry.setAction_("retryReport:")
            container.addSubview_(retry)

        return container

    # ── 菜单构建 ─────────────────────────────────────────────────────────────
    @objc.python_method
    def _build_home_menu(self, menu=None):
        stats = self._get_stats()
        total = sum(v["total"] for v in stats.values())
        if menu is None:
            menu = NSMenu.alloc().init()
            menu.setAutoenablesItems_(False)
        else:
            menu.removeAllItems()

        gray = NSColor.colorWithSRGBRed_green_blue_alpha_(0.557, 0.557, 0.576, 1.0)
        dark = NSColor.colorWithSRGBRed_green_blue_alpha_(0.110, 0.110, 0.118, 1.0)

        # 日期翻页：‹   日期标签   ›（单行自定义 NSView）
        is_today = self._view_date == date.today()
        vd = self._view_date
        date_label = (
            f"今天 · {vd.year}年{vd.month}月{vd.day}日"
            if is_today else
            f"{vd.year}年{vd.month}月{vd.day}日"
        )

        nav_view = DateNavView.alloc().initWithFrame_(((0, 0), (260, 36)))
        nav_view.setup(self, date_label, is_today)
        self._nav_view = nav_view  # 防 GC

        nav_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", None, "")
        nav_item.setView_(nav_view)
        menu.addItem_(nav_item)

        # 标题行：图表模式显示活跃时长，简报模式显示日期
        title_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", None, "")
        if self._show_report:
            para = NSMutableParagraphStyle.alloc().init()
            para.setAlignment_(1)  # NSTextAlignmentCenter
            title_item.setAttributedTitle_(
                NSAttributedString.alloc().initWithString_attributes_(
                    "",
                    {NSFontAttributeName: NSFont.boldSystemFontOfSize_(15.0),
                     NSForegroundColorAttributeName: dark,
                     NSParagraphStyleAttributeName: para},
                )
            )
        else:
            title_item.setAttributedTitle_(
                NSAttributedString.alloc().initWithString_attributes_(
                    f"活跃 {_fmt_dur(total)}",
                    {NSFontAttributeName: NSFont.boldSystemFontOfSize_(22.0),
                     NSForegroundColorAttributeName: dark},
                )
            )
        title_item.setEnabled_(False)
        menu.addItem_(title_item)

        # 图表 / 简报区域（260×120pt，点击切换）
        chart_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", None, "")
        chart_item.setEnabled_(False)
        if self._show_report:
            container = self._build_report_view()
        else:
            if self._chart_mode == "donut":
                cv = DonutChartView.alloc().initWithFrame_(((10, 0), (240, 120)))
            else:
                cv = BarChartView.alloc().initWithFrame_(((10, 0), (240, 120)))
            cv._stats        = stats
            self._chart_view = cv
            container        = _ChartClickView.alloc().initWithFrame_(((0, 0), (260, 120)))
            container._delegate = self
            container.addSubview_(cv)
        self._chart_container = container  # 防 GC
        chart_item.setView_(container)
        menu.addItem_(chart_item)

        menu.addItem_(NSMenuItem.separatorItem())

        # 今日小憩卡片（仅今天显示）
        today = date.today()
        if self._view_date == today:
            if self._wellness_date != today or self._wellness_activity is None:
                self._wellness_activity = get_random_activity()
                self._wellness_date = today
            if self._wellness_enabled and self._wellness_activity is not None:
                wv = WellnessCardView.alloc().initWithFrame_(((0, 0), (260, 80)))
                wv._activity        = self._wellness_activity
                wv._delegate        = self
                self._wellness_view = wv  # 防 GC
                w_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", None, "")
                w_item.setView_(wv)
                menu.addItem_(w_item)

        menu.addItem_(NSMenuItem.separatorItem())

        # 导航入口
        report = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "查看周报 ›", "openReport:", ""
        )
        report.setTarget_(self)
        menu.addItem_(report)

        menu.addItem_(NSMenuItem.separatorItem())

        settings_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "设置", "showSettings:", ""
        )
        settings_item.setTarget_(self)
        menu.addItem_(settings_item)

        menu.addItem_(NSMenuItem.separatorItem())

        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "退出小蛋", "terminate:", "q"
        )
        quit_item.setTarget_(NSApplication.sharedApplication())
        menu.addItem_(quit_item)

        return menu

    @objc.python_method
    def _build_brief_menu(self):
        stats = self._get_stats()
        menu  = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)

        back = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "‹ 返回", "showHome:", ""
        )
        back.setTarget_(self)
        menu.addItem_(back)
        menu.addItem_(NSMenuItem.separatorItem())

        for cat in CATEGORY_ORDER:
            cat_stats = stats.get(cat, {})
            secs = cat_stats.get("total", 0.0)
            subs = cat_stats.get("subs", {})
            h    = 46.0 if subs else 32.0
            row  = BriefRowView.alloc().initWithFrame_(((0, 0), (260, h)))
            row._cat   = cat
            row._total = secs
            row._subs  = subs
            row_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", None, "")
            row_item.setEnabled_(False)
            row_item.setView_(row)
            menu.addItem_(row_item)

        menu.addItem_(NSMenuItem.separatorItem())

        report = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "查看完整日报 ›", "openReport:", ""
        )
        report.setTarget_(self)
        menu.addItem_(report)

        return menu

    @objc.python_method
    def _build_goals_menu(self):
        stats = self._get_stats()
        menu  = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)

        back = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "‹ 返回", "showHome:", ""
        )
        back.setTarget_(self)
        menu.addItem_(back)
        menu.addItem_(NSMenuItem.separatorItem())

        for cat, goal_secs, label in _GOALS:
            actual = stats.get(cat, {}).get("total", 0.0)
            pct    = min(1.0, actual / goal_secs) if goal_secs > 0 else 0.0
            row    = GoalRowView.alloc().initWithFrame_(((0, 0), (260, 50)))
            row._cat        = cat
            row._actual     = actual
            row._goal_secs  = goal_secs
            row._goal_label = label
            row._pct        = pct
            row_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", None, "")
            row_item.setEnabled_(False)
            row_item.setView_(row)
            menu.addItem_(row_item)

        menu.addItem_(NSMenuItem.separatorItem())

        add_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "＋ 添加目标", None, ""
        )
        add_item.setEnabled_(False)
        menu.addItem_(add_item)

        return menu


# ── 入口 ─────────────────────────────────────────────────────────────────────

def start_ui(db_path: str) -> None:
    """启动菜单栏 UI（在主线程中阻塞运行，直到退出小蛋）。"""
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(1)   # NSApplicationActivationPolicyAccessory，无 Dock 图标

    delegate = XiaoDanDelegate.alloc().init()
    delegate._conn = sqlite3.connect(db_path, check_same_thread=False)
    delegate._conn.execute("PRAGMA journal_mode=WAL")
    app.setDelegate_(delegate)
    app.run()
