"""
小蛋报告窗口 — 基于 WKWebView 的周报/月报/书单界面
"""

import json
import os
import sys
import objc
import urllib.parse
from datetime import date as _date, timedelta as _timedelta

from Foundation import NSObject, NSURL
from AppKit import (
    NSWindow, NSColor, NSApplication,
    NSTitledWindowMask, NSClosableWindowMask,
    NSMiniaturizableWindowMask, NSResizableWindowMask,
    NSBackingStoreBuffered,
)

try:
    from WebKit import WKWebView, WKWebViewConfiguration
except ImportError:
    raise SystemExit("缺少依赖，请运行：pip install pyobjc-framework-WebKit")

from analyzer import (
    get_all_weeks, get_all_months,
    get_week_stats, get_month_stats, get_month_daily_stats,
    get_month_daily_period_stats,
    get_reflection, save_reflection,
    get_book_notes, save_book_note,
    update_book_note, delete_book_note,
    get_monthly_reflection, save_monthly_reflection,
    get_monthly_summary,
)

_base = os.environ["RESOURCEPATH"] if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
_CHART_JS_PATH = os.path.join(_base, "chart.umd.min.js")
with open(_CHART_JS_PATH, "r", encoding="utf-8") as _f:
    _CHART_JS_CONTENT = _f.read()


# ── 颜色 & 分类常量 ──────────────────────────────────────────────────────────

COLORS = {
    "自主学习":       "#9B72CF",
    "学校学习":       "#5B8DEF",
    "娱乐":          "#4ECDC4",
    "其他":          "#B8BCC8",
}

CATEGORIES = ["自主学习", "学校学习", "娱乐", "其他"]


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def fmt_duration(seconds: int | float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "不足1分钟"
    total_m = seconds // 60
    h, m = divmod(total_m, 60)
    if h > 0:
        return f"{h}h {m:02d}m"
    return f"{m}m"


def fmt_change(current: int | float, prev) -> tuple[str, str]:
    if prev is None:
        return ("", "flat")
    diff = int(current) - int(prev)
    if diff == 0:
        return ("持平", "flat")
    abs_m = abs(diff) // 60
    h, m = divmod(abs_m, 60)
    dur = f"{h}h {m:02d}m" if h > 0 else f"{m}m"
    return (f"+{dur}", "up") if diff > 0 else (f"-{dur}", "down")


def _fmt_week_range(year: int, week: int) -> str:
    """同月→「6月15日-21日」，跨月→「5月26日-6月1日」。"""
    ds = _date.fromisocalendar(year, week, 1)
    de = _date.fromisocalendar(year, week, 7)
    if ds.month == de.month:
        return f"{ds.month}月{ds.day}日-{de.day}日"
    return f"{ds.month}月{ds.day}日-{de.month}月{de.day}日"


def _week_prev(year: int, week: int) -> tuple[int, int]:
    d = _date.fromisocalendar(year, week, 1) - _timedelta(weeks=1)
    y, w, _ = d.isocalendar()
    return y, w


def _week_next(year: int, week: int) -> tuple[int, int]:
    d = _date.fromisocalendar(year, week, 1) + _timedelta(weeks=1)
    y, w, _ = d.isocalendar()
    return y, w


def _month_prev(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _month_next(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


# ── ReportWindow ─────────────────────────────────────────────────────────────

class ReportWindow(NSObject):

    def init(self):
        self = objc.super(ReportWindow, self).init()
        if self is not None:
            self._window        = None
            self._webview       = None
            self._current_type  = None
            self._current_key   = None
            self._current_sub   = None
            self._editing_note  = None  # prefill dict for booknotes_edit sub
        return self

    @objc.python_method
    def show(self):
        if self._window is None:
            self._build_window()
        self._window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self._navigate_default()

    @objc.python_method
    def _build_window(self):
        mask = (NSTitledWindowMask | NSClosableWindowMask
                | NSMiniaturizableWindowMask | NSResizableWindowMask)
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (780, 560)), mask, NSBackingStoreBuffered, False
        )
        win.setTitle_("小蛋报告")
        win.setMinSize_((780, 560))
        win.setBackgroundColor_(NSColor.whiteColor())
        win.setReleasedWhenClosed_(False)
        win.center()
        self._window = win

        config = WKWebViewConfiguration.alloc().init()
        wv = WKWebView.alloc().initWithFrame_configuration_(
            win.contentView().bounds(), config
        )
        wv.setAutoresizingMask_(18)
        wv.setNavigationDelegate_(self)
        wv.setUIDelegate_(self)
        win.contentView().addSubview_(wv)
        self._webview = wv

    @objc.python_method
    def _navigate_default(self):
        today = _date.today()
        year, week, _ = today.isocalendar()
        self._render_week(year, week)

    @objc.python_method
    def _load_html(self, html_str: str):
        self._webview.loadHTMLString_baseURL_(html_str, None)

    # ── 渲染方法 ──────────────────────────────────────────────────────────────

    @objc.python_method
    def _render_week(self, year: int, week: int, sub=None, toast=None):
        self._current_type = "week"
        self._current_key  = (year, week)
        self._current_sub  = sub
        d_monday     = _date.fromisocalendar(year, week, 1)
        active_year  = d_monday.year
        active_month = d_monday.month
        sidebar = self._build_sidebar("week", active_year, active_month)
        if sub == "detail":
            content = self._build_detail_content(year, week)
        elif sub == "booknotes":
            content = self._build_booknotes_content(year, week, toast=toast)
        elif sub == "booknotes_new":
            content = self._build_booknotes_new_content(year, week, prefill=None)
        elif sub == "booknotes_edit":
            content = self._build_booknotes_new_content(year, week, prefill=self._editing_note)
        else:
            content = self._build_week_content(year, week)
        self._load_html(self._build_page(sidebar, content))

    @objc.python_method
    def _render_month(self, year: int, month: int, sub=None):
        self._current_type = "month"
        self._current_key  = (year, month)
        self._current_sub  = sub
        sidebar = self._build_sidebar("month", year, month)
        if sub == "ranking":
            content = self._build_ranking_content(year, month)
        else:
            content = self._build_month_content(year, month)
        self._load_html(self._build_page(sidebar, content))

    @objc.python_method
    def _render_booknotes_history(self, toast=None):
        if self._current_type == "week" and self._current_key:
            y, w = self._current_key
        else:
            today = _date.today()
            y, w, _ = today.isocalendar()
        self._render_week(y, w, sub="booknotes", toast=toast)

    @objc.python_method
    def _render_settings(self, toast=None):
        self._current_type = "settings"
        self._current_key  = None
        self._current_sub  = None
        today = _date.today()
        sidebar = self._build_sidebar("settings", today.year, today.month)
        content = self._build_settings_content(toast=toast)
        self._load_html(self._build_page(sidebar, content))

    @objc.python_method
    def _build_settings_content(self, toast=None) -> str:
        from settings import load_settings
        s = load_settings()
        checked = "checked" if s.get("api_enabled", True) else ""

        key_invalid = False
        fmt_error = False
        if s.get("api_enabled", True):
            try:
                from classifier import is_api_key_invalid, is_api_format_error
                key_invalid = is_api_key_invalid()
                fmt_error = is_api_format_error()
            except Exception:
                pass

        html = '<div class="page-title">设置</div>'
        html += '<hr class="sec-div">'

        if key_invalid:
            warn_badge = (
                ' <span style="display:inline-block;color:#D85A30;font-size:14px;'
                'vertical-align:middle;cursor:default;" '
                'title="API Key 可能已失效，请检查或重新配置">⚠</span>'
            )
            warn_note = (
                '<div style="font-size:11px;color:#D85A30;margin-top:5px;">'
                'API Key 可能已失效，请重新配置</div>'
            )
        elif fmt_error:
            warn_badge = (
                ' <span style="display:inline-block;color:#D85A30;font-size:14px;'
                'vertical-align:middle;cursor:default;" '
                'title="服务返回格式可能不兼容 Anthropic SDK，请确认 Base URL 服务商支持">⚠</span>'
            )
            warn_note = (
                '<div style="font-size:11px;color:#D85A30;margin-top:5px;">'
                '服务格式可能不兼容，请确认 Base URL 服务商支持 Anthropic 格式</div>'
            )
        else:
            warn_badge = ''
            warn_note = ''

        html += (
            f'<div style="display:flex;justify-content:space-between;align-items:center;padding:10px 0;">'
            f'<div>'
            f'<div style="font-size:13px;color:#1C1C1E;">启用 AI 简报{warn_badge}</div>'
            f'<div style="font-size:11px;color:#8E8E93;margin-top:3px;">关闭后不调用 AI 接口，修改后重启生效</div>'
            f'{warn_note}'
            f'</div>'
            f'<label class="xd-toggle">'
            f'<input type="checkbox" {checked} '
            f'onchange="xdNav(\'xd://save_api_enabled?value=\'+(this.checked?\'1\':\'0\'))">'
            f'<span class="xd-toggle-slider"></span>'
            f'</label>'
            f'</div>'
        )

        # Base URL 编辑区
        import json as _json
        base_url_val = s.get("api_base_url", "").strip()
        base_url_escaped = _json.dumps(base_url_val, ensure_ascii=False)
        html += (
            f'<div style="padding:10px 0;border-top:.5px solid #E0E0E0;">'
            f'<div style="font-size:13px;color:#1C1C1E;margin-bottom:4px;">API Base URL</div>'
            f'<div style="font-size:11px;color:#8E8E93;margin-bottom:8px;">'
            f'留空使用 Anthropic 官方端点；填写可接入 OpenRouter 等代理服务</div>'
            f'<input type="text" id="base-url-input" value={base_url_escaped} '
            f'placeholder="https://openrouter.ai/api/v1" '
            f'style="width:100%;box-sizing:border-box;padding:6px 8px;font-size:12px;'
            f'border:.5px solid #C7C7CC;border-radius:6px;outline:none;font-family:inherit;" '
            f'onblur="xdNav(\'xd://save_api_base_url?value=\'+encodeURIComponent(this.value.trim()))">'
            f'</div>'
        )

        if toast:
            html += f'<script>setTimeout(function(){{showToast({_json.dumps(toast)});}},50);</script>'
        return html

    # ── HTML 骨架 ─────────────────────────────────────────────────────────────

    @objc.python_method
    def _build_page(self, sidebar_html: str, content_html: str) -> str:
        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<script>{_CHART_JS_CONTENT}</script>
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;color:#1C1C1E;display:flex;height:100vh;overflow:hidden;}}
#sidebar{{width:160px;flex-shrink:0;background:#F7F7F7;border-right:.5px solid #E0E0E0;height:100vh;overflow-y:auto;padding:12px 0;}}
#content{{flex:1;padding:20px;overflow-y:auto;height:100vh;}}
::-webkit-scrollbar{{width:4px;}}
::-webkit-scrollbar-thumb{{background:#D0D0D0;border-radius:2px;}}
.sec-title{{font-size:10px;color:#8E8E93;padding:8px 12px 4px;letter-spacing:.5px;text-transform:uppercase;}}
.year-header{{display:flex;align-items:center;gap:5px;padding:5px 12px;cursor:pointer;font-size:12px;color:#8E8E93;user-select:none;}}
.year-header:hover{{color:#1C1C1E;}}
.chevron{{font-size:8px;width:10px;text-align:center;flex-shrink:0;}}
.nav-month{{display:block;padding:5px 12px 5px 24px;font-size:13px;color:#8E8E93;cursor:pointer;text-decoration:none;border-right:2px solid transparent;}}
.nav-month:hover{{color:#1C1C1E;}}
.nav-month.active{{color:#1C1C1E;background:#FFF;border-right-color:#1C1C1E;font-weight:500;}}
hr.divider{{border:none;border-top:.5px solid #E0E0E0;margin:8px 0;}}
.week-nav{{display:flex;align-items:center;justify-content:center;gap:14px;padding-bottom:12px;}}
.nav-arrow{{background:none;border:none;cursor:pointer;font-size:20px;color:#1C1C1E;padding:0 6px;line-height:1;border-radius:4px;}}
.nav-arrow:hover:not(:disabled){{background:#F0F0F0;}}
.nav-arrow:disabled{{color:#D0D0D0;cursor:default;}}
.nav-date{{font-size:14px;font-weight:500;min-width:140px;text-align:center;}}
.week-tabs{{display:flex;border-bottom:.5px solid #E0E0E0;margin-bottom:16px;}}
.tab{{padding:9px 16px;font-size:13px;color:#8E8E93;cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px;}}
.tab:hover{{color:#1C1C1E;}}
.tab.active{{color:#1C1C1E;border-bottom-color:#1C1C1E;}}
.total-row{{font-size:20px;font-weight:500;margin:12px 0 4px;}}
.card-grid{{display:flex;gap:8px;margin:14px 0;}}
.card{{flex:1;background:#F5F5F5;border-radius:8px;padding:10px 12px;}}
.card-label{{font-size:11px;color:#8E8E93;display:flex;align-items:center;gap:5px;}}
.dot{{width:7px;height:7px;border-radius:2px;flex-shrink:0;}}
.card-value{{font-size:18px;font-weight:500;margin-top:4px;}}
.card-change{{font-size:11px;margin-top:2px;}}
.up{{color:#1D9E75;}}.down{{color:#D85A30;}}.flat{{color:#8E8E93;}}
hr.sec-div{{border:none;border-top:.5px solid #E0E0E0;margin:16px 0;}}
.page-title{{font-size:18px;font-weight:500;margin-bottom:10px;}}
.chart-wrap{{margin:14px 0;}}
textarea{{width:100%;border:.5px solid #E0E0E0;border-radius:6px;padding:8px;font-size:13px;font-family:inherit;resize:vertical;outline:none;color:#1C1C1E;}}
textarea:focus{{border-color:#9B72CF;}}
.btn{{padding:5px 14px;border:none;border-radius:6px;font-size:13px;cursor:pointer;font-family:inherit;}}
.btn-primary{{background:#1C1C1E;color:#FFF;}}
.btn-secondary{{background:#F0F0F0;color:#1C1C1E;}}
.btn-danger{{background:#FFE5E5;color:#D85A30;}}
input[type=text]{{width:100%;border:.5px solid #E0E0E0;border-radius:6px;padding:7px 10px;font-size:13px;font-family:inherit;outline:none;color:#1C1C1E;margin-bottom:8px;}}
input[type=text]:focus{{border-color:#9B72CF;}}
.note-card{{border:.5px solid #E0E0E0;border-radius:8px;padding:12px 14px;margin-bottom:10px;}}
.tag-pill{{display:inline-block;background:#F0F0F0;border-radius:10px;padding:1px 7px;font-size:11px;color:#8E8E93;margin-right:4px;}}
.pie-grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:14px;}}
.pie-cell{{border:.5px solid #E0E0E0;border-radius:8px;padding:12px;}}
.pie-cell-title{{font-size:13px;font-weight:500;margin-bottom:8px;display:flex;justify-content:space-between;}}
.sub-row{{display:flex;justify-content:space-between;font-size:12px;color:#8E8E93;margin-top:4px;}}
.page-row{{display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:.5px solid #F0F0F0;}}
.page-idx{{font-size:14px;font-weight:500;color:#8E8E93;width:20px;flex-shrink:0;}}
.page-info{{flex:1;min-width:0;}}
.page-title-t{{font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
.page-url-t{{font-size:12px;color:#8E8E93;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
.page-dur{{font-size:13px;font-weight:500;flex-shrink:0;}}
.prog-bg{{height:3px;background:#F0F0F0;border-radius:2px;margin-top:4px;}}
.prog-fill{{height:3px;border-radius:2px;}}
.form-card{{border:.5px solid #E0E0E0;border-radius:8px;padding:16px;margin-bottom:16px;}}
.new-note-btn{{display:flex;align-items:center;justify-content:center;gap:6px;width:100%;padding:9px;border:.5px dashed #D0D0D0;border-radius:8px;background:none;font-size:13px;color:#8E8E93;cursor:pointer;margin-bottom:14px;}}
.new-note-btn:hover{{border-color:#9B72CF;color:#9B72CF;}}
.toast{{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:rgba(28,28,30,.9);color:#FFF;padding:8px 18px;border-radius:20px;font-size:13px;z-index:9999;opacity:0;transition:opacity .25s;pointer-events:none;}}
.xd-confirm-overlay{{position:fixed;inset:0;background:rgba(0,0,0,0.3);display:flex;align-items:center;justify-content:center;z-index:9999;}}
.xd-confirm-box{{background:#fff;border-radius:12px;padding:20px 24px;width:260px;box-shadow:0 4px 20px rgba(0,0,0,0.15);font-family:-apple-system,sans-serif;}}
.xd-confirm-box p{{font-size:14px;color:#1c1c1e;margin:0 0 16px;line-height:1.5;}}
.xd-confirm-btns{{display:flex;gap:8px;justify-content:flex-end;}}
.xd-confirm-btns button{{padding:6px 14px;border-radius:7px;border:none;font-size:13px;cursor:pointer;}}
.xd-btn-cancel{{background:#f2f2f7;color:#1c1c1e;}}
.xd-btn-confirm{{background:#ff3b30;color:#fff;}}
.xd-btn-confirm-gray{{background:#8e8e93;color:#fff;}}
.xd-toggle{{position:relative;display:inline-block;width:40px;height:22px;flex-shrink:0;}}
.xd-toggle input{{opacity:0;width:0;height:0;position:absolute;}}
.xd-toggle-slider{{position:absolute;cursor:pointer;top:0;left:0;right:0;bottom:0;background:#D0D0D0;border-radius:22px;transition:.2s;}}
.xd-toggle-slider:before{{position:absolute;content:"";height:18px;width:18px;left:2px;bottom:2px;background:#fff;border-radius:50%;transition:.2s;}}
.xd-toggle input:checked+.xd-toggle-slider{{background:#1C1C1E;}}
.xd-toggle input:checked+.xd-toggle-slider:before{{transform:translateX(18px);}}
</style>
<script>
function xdNav(p){{window.location.href=p;}}
function showToast(msg){{
  var t=document.createElement('div');
  t.className='toast';t.textContent=msg;
  document.body.appendChild(t);
  setTimeout(function(){{t.style.opacity='1';}},20);
  setTimeout(function(){{t.style.opacity='0';setTimeout(function(){{t.remove();}},260);}},2000);
}}
function xdConfirm(msg,onConfirm,cancelLabel,okLabel,okClass){{
  var cl=cancelLabel||'取消';
  var ol=okLabel||'删除';
  var oc=okClass||'xd-btn-confirm';
  var overlay=document.createElement('div');
  overlay.className='xd-confirm-overlay';
  overlay.innerHTML='<div class="xd-confirm-box"><p>'+msg+'</p><div class="xd-confirm-btns"><button class="xd-btn-cancel" id="xd-cancel">'+cl+'</button><button class="'+oc+'" id="xd-ok">'+ol+'</button></div></div>';
  document.body.appendChild(overlay);
  overlay.querySelector('#xd-cancel').onclick=function(){{overlay.remove();}};
  overlay.querySelector('#xd-ok').onclick=function(){{overlay.remove();onConfirm();}};
}}
function toggleYear(id){{
  var el=document.getElementById(id);
  var ch=document.getElementById('chev_'+id);
  if(el.style.display==='none'){{el.style.display='block';ch.textContent='▼';}}
  else{{el.style.display='none';ch.textContent='▶';}}
}}
function navigate(type,year,key){{
  if(type==='week')xdNav('xd://navigate_week?year='+year+'&week='+key);
  else if(type==='week_month')xdNav('xd://navigate_week_month?year='+year+'&month='+key);
  else if(type==='month')xdNav('xd://navigate_month?year='+year+'&month='+key);
  else if(type==='booknotes_new')xdNav('xd://navigate_booknotes_new');
  else if(type==='booknotes_history')xdNav('xd://navigate_booknotes_history');
}}
function navWeek(year,week){{xdNav('xd://navigate_week?year='+year+'&week='+week);}}
function navTab(sub,year,week){{
  if(sub==='')xdNav('xd://navigate_week?year='+year+'&week='+week);
  else xdNav('xd://navigate_week_sub?year='+year+'&week='+week+'&sub='+sub);
}}
function saveReflection(year,week){{
  var t=document.getElementById('reflection').value;
  xdNav('xd://save_reflection?year='+year+'&week='+week+'&content='+encodeURIComponent(t));
}}
function deleteBookNote(id){{
  xdNav('xd://delete_book_note?id='+id);
}}
function toggleNote(id){{
  var el=document.getElementById('note-content-'+id);
  var btn=document.getElementById('note-toggle-'+id);
  if(el.style.display==='none'){{el.style.display='block';btn.textContent='收起 ▴';}}
  else{{el.style.display='none';btn.textContent='展开 ▾';}}
}}
function navigateSub(sub,year,month){{
  xdNav('xd://navigate_month_sub?sub='+sub+'&year='+year+'&month='+month);
}}
function saveMonthlyReflection(year,month,content){{
  xdNav('xd://save_monthly_reflection?year='+year+'&month='+month+'&content='+encodeURIComponent(content));
}}
</script>
</head>
<body>
<div id="sidebar">{sidebar_html}</div>
<div id="content">{content_html}</div>
</body>
</html>"""

    # ── Sidebar ───────────────────────────────────────────────────────────────

    @objc.python_method
    def _build_sidebar(self, active_type: str, active_year: int, active_month: int) -> str:
        today = _date.today()
        cur_y = today.year
        cur_m = today.month

        # ── 周报 section: {year: set of months} ─────────────────────────────
        week_ym: dict[int, set] = {}
        for y, _w, ds, _de in get_all_weeks():
            d = _date.fromisoformat(ds)
            week_ym.setdefault(d.year, set()).add(d.month)
        week_ym.setdefault(cur_y, set()).add(cur_m)

        def _week_expanded(y: int) -> bool:
            if active_type == "week":
                return y == active_year
            return y == max(week_ym.keys())

        html = '<div class="sec-title">周报</div>'
        for y in sorted(week_ym, reverse=True):
            exp = _week_expanded(y)
            gid = f"wy{y}"
            html += (f'<div class="year-header" onclick="toggleYear(\'{gid}\')">'
                     f'<span class="chevron" id="chev_{gid}">{"▼" if exp else "▶"}</span>'
                     f'{y}年</div>'
                     f'<div id="{gid}" style="display:{"block" if exp else "none"}">')
            for m in sorted(week_ym[y], reverse=True):
                is_act = active_type == "week" and y == active_year and m == active_month
                cls = " active" if is_act else ""
                html += (f'<a class="nav-month{cls}" '
                         f'onclick="navigate(\'week_month\',{y},{m})">{m}月</a>')
            html += '</div>'

        html += '<hr class="divider">'

        # ── 月报 section: {year: set of months} ─────────────────────────────
        month_ym: dict[int, set] = {}
        for y, m in get_all_months():
            month_ym.setdefault(y, set()).add(m)
        month_ym.setdefault(cur_y, set()).add(cur_m)

        def _month_expanded(y: int) -> bool:
            if active_type == "month":
                return y == active_year
            return y == max(month_ym.keys())

        html += '<div class="sec-title">月报</div>'
        for y in sorted(month_ym, reverse=True):
            exp = _month_expanded(y)
            gid = f"my{y}"
            html += (f'<div class="year-header" onclick="toggleYear(\'{gid}\')">'
                     f'<span class="chevron" id="chev_{gid}">{"▼" if exp else "▶"}</span>'
                     f'{y}年</div>'
                     f'<div id="{gid}" style="display:{"block" if exp else "none"}">')
            for m in sorted(month_ym[y], reverse=True):
                is_act = active_type == "month" and y == active_year and m == active_month
                cls = " active" if is_act else ""
                html += (f'<a class="nav-month{cls}" '
                         f'onclick="navigate(\'month\',{y},{m})">{m}月</a>')
            html += '</div>'

        html += '<hr class="divider">'
        settings_cls = " active" if active_type == "settings" else ""
        html += (f'<a class="nav-month{settings_cls}" style="padding-left:12px;" '
                 f'onclick="xdNav(\'xd://navigate_settings\')">⚙ 设置</a>')

        return html

    # ── 周切换导航 + Tabs ─────────────────────────────────────────────────────

    @objc.python_method
    def _build_week_header(self, year: int, week: int, sub) -> str:
        today = _date.today()
        cur_y, cur_w, _ = today.isocalendar()
        is_current = (year == cur_y and week == cur_w)

        py, pw = _week_prev(year, week)
        ny, nw = _week_next(year, week)

        all_weeks = get_all_weeks()
        if all_weeks:
            ey, ew = all_weeks[-1][0], all_weeks[-1][1]
            has_prev = (_date.fromisocalendar(py, pw, 1)
                        >= _date.fromisocalendar(ey, ew, 1))
        else:
            has_prev = False

        left  = (f'<button class="nav-arrow" onclick="navWeek({py},{pw})">‹</button>'
                 if has_prev else '<button class="nav-arrow" disabled>‹</button>')
        right = (f'<button class="nav-arrow" onclick="navWeek({ny},{nw})">›</button>'
                 if not is_current else '<button class="nav-arrow" disabled>›</button>')

        nav = (f'<div class="week-nav">{left}'
               f'<span class="nav-date">{_fmt_week_range(year, week)}</span>'
               f'{right}</div>')

        tab_active = "booknotes" if sub in ("booknotes_new", "booknotes_edit") else sub
        tabs = '<div class="week-tabs">'
        for tab_sub, label in [("", "总览"), ("detail", "时间明细"), ("booknotes", "读书笔记")]:
            is_act = (tab_active is None and tab_sub == "") or (tab_sub != "" and tab_active == tab_sub)
            cls = " active" if is_act else ""
            tabs += (f'<span class="tab{cls}" '
                     f'onclick="navTab(\'{tab_sub}\',{year},{week})">{label}</span>')
        tabs += '</div>'

        return nav + tabs

    @objc.python_method
    def _build_month_header(self, year: int, month: int, sub) -> str:
        today = _date.today()
        is_current = (year == today.year and month == today.month)

        py, pm = _month_prev(year, month)
        ny, nm = _month_next(year, month)

        all_months_set = {(y, m) for y, m in get_all_months()}
        has_prev = (py, pm) in all_months_set
        has_next = not is_current

        left  = (f'<button class="nav-arrow" onclick="navigate(\'month\',{py},{pm})">‹</button>'
                 if has_prev else '<button class="nav-arrow" disabled>‹</button>')
        right = (f'<button class="nav-arrow" onclick="navigate(\'month\',{ny},{nm})">›</button>'
                 if has_next else '<button class="nav-arrow" disabled>›</button>')

        nav = (f'<div class="week-nav">{left}'
               f'<span class="nav-date">{year}年{month}月</span>'
               f'{right}</div>')

        tabs = '<div class="week-tabs">'
        for tab_sub, label in [("", "总览"), ("ranking", "时间排行")]:
            is_act = (sub is None and tab_sub == "") or (tab_sub != "" and sub == tab_sub)
            cls = " active" if is_act else ""
            tabs += (f'<span class="tab{cls}" '
                     f'onclick="navigateSub(\'{tab_sub}\',{year},{month})">{label}</span>')
        tabs += '</div>'

        return nav + tabs

    # ── 周报内容 ──────────────────────────────────────────────────────────────

    @objc.python_method
    def _build_week_content(self, year: int, week: int) -> str:
        stats = get_week_stats(year, week)
        weeks = get_all_weeks()
        prev_stats = None
        for i, (y, w, _ds, _de) in enumerate(weeks):
            if y == year and w == week and i + 1 < len(weeks):
                prev_stats = get_week_stats(*weeks[i + 1][:2])
                break

        total      = stats["total_seconds"]
        prev_total = prev_stats["total_seconds"] if prev_stats else None
        chg_text, chg_dir = fmt_change(total, prev_total)
        chg_span = (f'<span class="{chg_dir}" style="margin-left:8px;font-size:12px;">'
                    f'{chg_text}</span>') if chg_text else ""

        html  = self._build_week_header(year, week, None)
        html += f'<div class="total-row">{fmt_duration(total)}{chg_span}</div>'

        html += '<div class="card-grid">'
        for cat in CATEGORIES:
            cat_data  = stats["by_category"].get(cat, {"seconds": 0})
            prev_cat  = (prev_stats["by_category"].get(cat, {"seconds": 0})
                         if prev_stats else None)
            secs      = cat_data["seconds"]
            prev_secs = prev_cat["seconds"] if prev_cat else None
            ct, cd    = fmt_change(secs, prev_secs)
            color     = COLORS[cat]
            chg_div   = (f'<div class="card-change {cd}">{ct}</div>'
                         if ct else '<div class="card-change flat">&nbsp;</div>')
            html += (f'<div class="card">'
                     f'<div class="card-label">'
                     f'<span class="dot" style="background:{color}"></span>{cat}</div>'
                     f'<div class="card-value">{fmt_duration(secs)}</div>'
                     f'{chg_div}</div>')
        html += '</div>'

        DAY_NAMES   = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        sorted_days = sorted(stats["by_day"].keys())
        labels = []
        for d in sorted_days:
            try:
                labels.append(DAY_NAMES[_date.fromisoformat(d).isoweekday() - 1])
            except Exception:
                labels.append(d)

        cat_rgb = {
            "自主学习": "155,114,207", "学校学习": "91,141,239",
            "娱乐": "78,205,196",     "其他": "184,188,200",
        }
        datasets = []
        for cat in CATEGORIES:
            data = [stats["by_day"][d].get(cat, 0) / 60 for d in sorted_days]
            rgb  = cat_rgb.get(cat, "184,188,200")
            datasets.append({
                "label": cat, "data": data,
                "backgroundColor": f"rgba({rgb},0.85)",
                "borderRadius": 3, "barThickness": 10, "stack": "main",
            })
        chart_json = json.dumps({"labels": labels, "datasets": datasets}, ensure_ascii=False)

        html += f"""<div class="chart-wrap"><canvas id="weekChart" height="120"></canvas></div>
<script>(function(){{
  if(window._weekChart)window._weekChart.destroy();
  window._weekChart=new Chart(document.getElementById('weekChart').getContext('2d'),{{
    type:'bar',data:{chart_json},
    options:{{responsive:true,plugins:{{legend:{{display:false}},
      tooltip:{{callbacks:{{label:function(c){{
        var m=c.parsed.y,h=Math.floor(m/60),mn=Math.round(m%60);
        return c.dataset.label+': '+(h>0?h+'h '+(mn<10?'0':'')+mn+'m':mn+'m');
      }}}}}}}},
      scales:{{x:{{stacked:true,grid:{{display:false}}}},y:{{stacked:true,display:false}}}}
    }}
  }});
}})();</script>"""

        reflection = get_reflection(year, week) or ""

        def he(s):
            return (s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

        html += (f'<hr class="sec-div">'
                 f'<div style="font-size:12px;color:#8E8E93;margin-bottom:6px;">本周 reflection</div>'
                 f'<textarea id="reflection" rows="4" style="min-height:80px;">{he(reflection)}</textarea>'
                 f'<div style="margin-top:8px;text-align:right;">'
                 f'<button class="btn btn-primary" onclick="saveReflection({year},{week})">保存</button></div>')
        return html

    @objc.python_method
    def _build_detail_content(self, year: int, week: int) -> str:
        stats = get_week_stats(year, week)
        html  = self._build_week_header(year, week, "detail")

        # ── 饼图 ──────────────────────────────────────────────────────────────
        pie_labels, pie_data, pie_colors, legend_html = [], [], [], ""
        for cat in CATEGORIES:
            secs = stats["by_category"].get(cat, {}).get("seconds", 0)
            if secs <= 0:
                continue
            color = COLORS[cat]
            pie_labels.append(cat)
            pie_data.append(round(secs / 60))
            pie_colors.append(color)
            legend_html += (
                f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">'
                f'<div style="width:8px;height:8px;border-radius:2px;background:{color};flex-shrink:0;"></div>'
                f'<span style="font-size:13px;color:#1C1C1E;min-width:70px;">{cat}</span>'
                f'<span style="font-size:13px;color:#8E8E93;">{fmt_duration(secs)}</span>'
                f'</div>'
            )

        if pie_data:
            pie_json = json.dumps({
                "labels": pie_labels,
                "datasets": [{"data": pie_data, "backgroundColor": pie_colors, "borderWidth": 0}],
            }, ensure_ascii=False)
            html += f"""<div style="width:580px;margin:0 auto 24px;padding:16px 0;display:flex;justify-content:center;">
<canvas id="detailPieChart" width="420" height="420"
  style="display:block;width:420px;height:420px;"></canvas>
</div>
<script>(function(){{
  function fmtMin(m){{var h=Math.floor(m/60),mn=Math.round(m%60);if(h>0&&mn>0)return h+'h '+mn+'m';if(h>0)return h+'h';return mn+'m';}}
  var outsideLabels={{
    id:'outsideLabels',
    afterDraw:function(chart){{
      var ctx=chart.ctx;
      var cx=chart.chartArea.left+chart.chartArea.width/2;
      var cy=chart.chartArea.top+chart.chartArea.height/2;
      var r=Math.min(chart.chartArea.width,chart.chartArea.height)/2;
      chart.data.datasets[0].data.forEach(function(val,i){{
        if(!val)return;
        var meta=chart.getDatasetMeta(0);
        var arc=meta.data[i];
        var angle=(arc.startAngle+arc.endAngle)/2;
        var isRight=Math.cos(angle)>=0;
        var x1=cx+r*1.0*Math.cos(angle);
        var y1=cy+r*1.0*Math.sin(angle);
        var x2=cx+r*1.15*Math.cos(angle);
        var y2=cy+r*1.15*Math.sin(angle);
        var x3=x2+(isRight?30:-30);
        ctx.beginPath();
        ctx.moveTo(x1,y1);
        ctx.lineTo(x2,y2);
        ctx.lineTo(x3,y2);
        ctx.strokeStyle='#B0B0B0';
        ctx.lineWidth=1;
        ctx.stroke();
        var label=chart.data.labels[i];
        var duration=fmtMin(val);
        ctx.textAlign=isRight?'left':'right';
        ctx.textBaseline='middle';
        ctx.fillStyle='#1C1C1E';
        ctx.font='600 12px -apple-system,BlinkMacSystemFont,sans-serif';
        ctx.fillText(label,x3+(isRight?4:-4),y2-6);
        ctx.font='10px -apple-system,BlinkMacSystemFont,sans-serif';
        ctx.fillStyle='#8e8e93';
        ctx.fillText(duration,x3+(isRight?4:-4),y2+8);
      }});
    }}
  }};
  if(window._detailPie)window._detailPie.destroy();
  window._detailPie=new Chart(document.getElementById('detailPieChart').getContext('2d'),{{
    type:'doughnut',data:{pie_json},
    options:{{responsive:false,cutout:'60%',layout:{{padding:{{top:50,bottom:50,left:110,right:110}}}},plugins:{{legend:{{display:false}},
      tooltip:{{callbacks:{{label:function(ctx){{return fmtMin(ctx.parsed);}}}}}}}}
    }},
    plugins:[outsideLabels]
  }});
}})();</script>
<hr style="border:none;border-top:0.5px solid #E0E0E0;margin:0 0 20px;">"""

        any_data = False

        for cat in CATEGORIES:
            cat_data  = stats["by_category"].get(cat, {"seconds": 0, "sub": {}})
            sub_dict  = cat_data.get("sub", {})
            total_s   = cat_data.get("seconds", 0)
            if not sub_dict:
                continue
            any_data = True
            color     = COLORS[cat]
            sub_items = sorted(sub_dict.items(), key=lambda x: -x[1])
            max_s     = sub_items[0][1] if sub_items else 1

            html += (f'<div style="margin-bottom:20px;">'
                     f'<div style="display:flex;align-items:center;gap:6px;margin-bottom:10px;">'
                     f'<span style="width:7px;height:7px;border-radius:2px;background:{color};'
                     f'flex-shrink:0;display:inline-block;"></span>'
                     f'<span style="font-size:13px;font-weight:500;color:{color};">{cat}</span>'
                     f'<span style="font-size:12px;color:#8E8E93;margin-left:4px;">'
                     f'{fmt_duration(total_s)}</span></div>')

            for name, secs in sub_items:
                pct = int(secs / max_s * 100) if max_s else 0
                op  = round(0.4 + 0.6 * (secs / max_s), 2) if max_s else 1.0
                html += (
                    f'<div style="display:flex;align-items:center;margin-bottom:7px;">'
                    f'<span style="width:80px;font-size:12px;color:#8E8E93;text-align:right;'
                    f'flex-shrink:0;padding-right:10px;overflow:hidden;white-space:nowrap;'
                    f'text-overflow:ellipsis;">{name}</span>'
                    f'<div style="flex:1;height:8px;border-radius:4px;background:#F0F0F0;">'
                    f'<div style="width:{pct}%;height:8px;border-radius:4px;background:{color};'
                    f'opacity:{op};"></div></div>'
                    f'<span style="width:50px;font-size:12px;color:#1C1C1E;margin-left:8px;'
                    f'flex-shrink:0;">{fmt_duration(secs)}</span>'
                    f'</div>'
                )
            html += '</div>'

        if not any_data:
            html += ('<div style="font-size:13px;color:#8E8E93;text-align:center;'
                     'padding:30px 0;">本周暂无数据</div>')
        return html

    @objc.python_method
    def _build_booknotes_content(self, year: int, week: int, toast=None) -> str:
        import json as _json
        notes = get_book_notes()

        def he(s):
            return (s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")

        html  = self._build_week_header(year, week, "booknotes")
        html += (f'<button class="new-note-btn" '
                 f'onclick="xdNav(\'xd://navigate_booknotes_new?year={year}&week={week}\')">＋ 新增笔记</button>')

        if not notes:
            html += ('<div style="font-size:13px;color:#8E8E93;'
                     'text-align:center;padding:20px 0;">还没有笔记，写第一篇吧</div>')
        else:
            for note in notes:
                nid       = note["id"]
                title_raw = note.get("title") or ""
                title     = he(title_raw)
                author    = he(note.get("author") or "")
                tags_raw  = note.get("tags") or ""
                content   = note.get("content") or ""
                date_read = he(note.get("date_read") or "")
                tag_pills = "".join(f'<span class="tag-pill">{he(t)}</span>'
                                    for t in tags_raw.split() if t)
                author_s  = (f'<span style="font-size:12px;color:#8E8E93;margin-left:6px;">'
                             f'{author}</span>') if author else ""
                date_s    = (f'<span style="font-size:11px;color:#8E8E93;margin-right:8px;">'
                             f'{date_read}</span>') if date_read else ""
                # Title safe for use inside single-quoted JS string
                title_js  = title_raw.replace("\\", "\\\\").replace("'", "\\'").replace('"', "&quot;").replace("&", "&amp;")
                del_onclick = f"xdConfirm('确定要删除《{title_js}》这条笔记吗？',function(){{deleteBookNote({nid})}})"

                html += f"""<div class="note-card">
<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:4px;">
  <div><span style="font-size:14px;font-weight:500;">{title}</span>{author_s}</div>
  <div style="display:flex;gap:6px;align-items:center;">
    <button id="note-toggle-{nid}"
      style="font-size:11px;color:#8E8E93;border:none;background:none;cursor:pointer;padding:4px 0;"
      onclick="toggleNote({nid})">展开 ▾</button>
    <button class="btn btn-secondary" style="font-size:11px;padding:2px 8px;"
      onclick="xdNav('xd://navigate_booknotes_edit?year={year}&week={week}&id={nid}')">编辑</button>
    <button class="btn btn-secondary" style="font-size:11px;padding:2px 8px;"
      onclick="{del_onclick}">删除</button>
  </div>
</div>
<div style="margin-bottom:4px;">{date_s}{tag_pills}</div>
<div id="note-content-{nid}"
  style="display:none;margin-top:8px;font-size:13px;color:#4D4D4D;line-height:1.6;white-space:pre-wrap;">{he(content)}</div>
</div>"""

        if toast:
            html += f'<script>setTimeout(function(){{showToast({_json.dumps(toast)});}},50);</script>'
        return html

    # ── 读书笔记 新增/编辑 页面 ────────────────────────────────────────────────

    @objc.python_method
    def _build_booknotes_new_content(self, year: int, week: int, prefill=None) -> str:
        import json as _json

        def he(s):
            return (s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")

        editing  = prefill is not None
        nid      = prefill.get("id", "") if editing else ""
        f_title  = he(prefill.get("title",   "") if editing else "")
        f_author = he(prefill.get("author",  "") if editing else "")
        f_tags   = he(prefill.get("tags",    "") if editing else "")
        f_cont   = he(prefill.get("content", "") if editing else "")

        orig = _json.dumps({
            "title":   f_title,
            "author":  f_author,
            "tags":    f_tags,
            "content": f_cont,
        })
        confirm_msg = ("有未保存的更改，确定要离开吗？" if editing
                       else "有未保存的笔记，确定要离开吗？")

        html  = self._build_week_header(year, week, "booknotes")
        html += f"""
<script>
var _original = {orig};
function _fieldVal(id){{return document.getElementById(id).value;}}
function checkUnsaved(){{
  var changed = _fieldVal('inp-title')   !== _original.title   ||
                _fieldVal('inp-author')  !== _original.author  ||
                _fieldVal('inp-tags')    !== _original.tags    ||
                _fieldVal('inp-content') !== _original.content;
  if(!changed){{xdNav('xd://navigate_booknotes_history');return;}}
  xdConfirm('{confirm_msg}',function(){{xdNav('xd://navigate_booknotes_history');}},'继续编辑','不保存，离开','xd-btn-confirm-gray');
}}
function doSave(){{
  var t=_fieldVal('inp-title');
  if(!t.trim()){{alert('书名不能为空');return;}}
  var a=encodeURIComponent(_fieldVal('inp-author'));
  var g=encodeURIComponent(_fieldVal('inp-tags'));
  var c=encodeURIComponent(_fieldVal('inp-content'));
  t=encodeURIComponent(t);
"""
        if editing:
            html += f"  xdNav('xd://update_book_note?id={nid}&title='+t+'&author='+a+'&tags='+g+'&content='+c);\n"
        else:
            html += f"  xdNav('xd://save_book_note?year={year}&week={week}&title='+t+'&author='+a+'&tags='+g+'&content='+c);\n"
        html += f"""}}
</script>
<div class="form-card">
<input type="text" id="inp-title" placeholder="书名（必填）" value="{f_title}"
  style="width:100%;box-sizing:border-box;margin-bottom:8px;">
<input type="text" id="inp-author" placeholder="作者（选填）" value="{f_author}"
  style="width:100%;box-sizing:border-box;margin-bottom:8px;">
<input type="text" id="inp-tags" placeholder="标签，用空格分隔（选填）" value="{f_tags}"
  style="width:100%;box-sizing:border-box;margin-bottom:8px;">
<textarea id="inp-content" placeholder="写下你的感想..." rows="6"
  style="width:100%;box-sizing:border-box;min-height:120px;">{f_cont}</textarea>
</div>
<div style="display:flex;gap:8px;padding:0 2px;">
  <button class="btn btn-secondary" onclick="checkUnsaved()">← 返回</button>
  <div style="flex:1;"></div>
"""
        if editing:
            html += (f'  <button class="btn btn-danger" '
                     f'onclick="xdConfirm(\'确定要删除这条笔记吗？\',function(){{xdNav(\'xd://delete_book_note?id={nid}\')}})">删除记录</button>\n'
                     f'  <button class="btn btn-primary" onclick="doSave()">保存更新</button>\n')
        else:
            html += '  <button class="btn btn-primary" onclick="doSave()">保存</button>\n'
        html += '</div>'
        return html

    # ── 月报内容 ──────────────────────────────────────────────────────────────

    @objc.python_method
    def _build_month_content(self, year: int, month: int) -> str:
        stats  = get_month_stats(year, month)
        months = get_all_months()
        prev_stats = None
        for i, (y, m) in enumerate(months):
            if y == year and m == month and i + 1 < len(months):
                prev_stats = get_month_stats(*months[i + 1])
                break

        total      = stats["total_seconds"]
        prev_total = prev_stats["total_seconds"] if prev_stats else None
        chg_text, chg_dir = fmt_change(total, prev_total)
        chg_span = (f'<span class="{chg_dir}" style="margin-left:8px;font-size:12px;">'
                    f'{chg_text}</span>') if chg_text else ""

        html  = self._build_month_header(year, month, None)
        html += f'<div class="total-row" style="margin-top:0;">{fmt_duration(total)}{chg_span}</div>'

        html += '<div class="card-grid">'
        for cat in CATEGORIES:
            cat_data  = stats["by_category"].get(cat, {"seconds": 0})
            prev_cat  = (prev_stats["by_category"].get(cat, {"seconds": 0})
                         if prev_stats else None)
            secs      = cat_data["seconds"]
            prev_secs = prev_cat["seconds"] if prev_cat else None
            ct, cd    = fmt_change(secs, prev_secs)
            color     = COLORS[cat]
            chg_div   = (f'<div class="card-change {cd}">{ct}</div>'
                         if ct else '<div class="card-change flat">&nbsp;</div>')
            html += (f'<div class="card">'
                     f'<div class="card-label">'
                     f'<span class="dot" style="background:{color}"></span>{cat}</div>'
                     f'<div class="card-value">{fmt_duration(secs)}</div>{chg_div}</div>')
        html += '</div>'

        by_week = stats.get("by_week", [])
        cat_rgb = {
            "自主学习": "155,114,207", "学校学习": "91,141,239",
            "娱乐": "78,205,196",     "其他": "184,188,200",
        }
        labels = []
        for w in by_week:
            ds_str = w.get("date_start", "")
            if ds_str:
                ds = _date.fromisoformat(ds_str)
                de = ds + _timedelta(days=6)
                if ds.month == de.month:
                    lbl = f"{ds.month:02d}/{ds.day:02d}-{de.day:02d}"
                else:
                    lbl = f"{ds.month:02d}/{ds.day:02d}-{de.month:02d}/{de.day:02d}"
            else:
                lbl = f"W{w['week']}"
            labels.append(lbl)

        datasets = []
        for cat in CATEGORIES:
            data = [w.get(cat, 0) / 60 for w in by_week]
            rgb  = cat_rgb.get(cat, "184,188,200")
            datasets.append({
                "label": cat, "data": data,
                "backgroundColor": f"rgba({rgb},0.85)",
                "borderRadius": 3, "barThickness": 10, "stack": "main",
            })
        chart_json = json.dumps({"labels": labels, "datasets": datasets}, ensure_ascii=False)

        html += f"""<div class="chart-wrap"><canvas id="monthChart" height="120"></canvas></div>
<script>(function(){{
  if(window._monthChart)window._monthChart.destroy();
  window._monthChart=new Chart(document.getElementById('monthChart').getContext('2d'),{{
    type:'bar',data:{chart_json},
    options:{{responsive:true,plugins:{{legend:{{display:false}},
      tooltip:{{callbacks:{{label:function(c){{
        var m=c.parsed.y,h=Math.floor(m/60),mn=Math.round(m%60);
        return c.dataset.label+': '+(h>0?h+'h '+(mn<10?'0':'')+mn+'m':mn+'m');
      }}}}}}}},
      scales:{{x:{{stacked:true,grid:{{display:false}},
        ticks:{{maxRotation:0,font:{{size:10}}}}}},y:{{stacked:true,display:false}}}}
    }}
  }});
}})();</script>"""

        # ── 时段热力图（3行×每天列，横向） ────────────────────────────────────
        import calendar as _cal
        period_stats = get_month_daily_period_stats(year, month)
        _, last_day  = _cal.monthrange(year, month)
        _PERIODS     = ["早", "午", "晚"]
        _P_COLORS    = ["#F1EFE8", "#EDF1FC", "#D6E2F8", "#B0C8F2", "#84A8EB", "#5B8DEF"]
        _WD_CN       = ["一", "二", "三", "四", "五", "六", "日"]

        _all_secs = [period_stats[f"{year}-{month:02d}-{d:02d}"][p]
                     for d in range(1, last_day + 1) for p in _PERIODS]
        _max_secs = max(_all_secs) if any(s > 0 for s in _all_secs) else 1
        _step     = _max_secs / 5

        def _period_color(secs):
            if secs <= 0:          return _P_COLORS[0]
            if secs <= _step:      return _P_COLORS[1]
            if secs <= 2 * _step:  return _P_COLORS[2]
            if secs <= 3 * _step:  return _P_COLORS[3]
            if secs <= 4 * _step:  return _P_COLORS[4]
            return _P_COLORS[5]

        # 日期标题行
        html += '<div style="margin:36px 0 0;">'
        html += '<div style="font-size:11px;color:#8E8E93;margin-bottom:8px;">学习热力图（学校 + 自主）</div>'

        # 顶部日期数字行（与格子列对齐）
        html += '<div style="display:flex;align-items:center;margin-bottom:3px;padding-left:26px;gap:2px;">'
        for d in range(1, last_day + 1):
            wd = _date(year, month, d).weekday()
            clr = "#9B72CF" if wd >= 5 else "#C0C0C5"
            html += (f'<div style="width:15px;text-align:center;font-size:8px;'
                     f'color:{clr};line-height:12px;">{d}</div>')
        html += '</div>'

        # 3个时段行
        for p in _PERIODS:
            html += '<div style="display:flex;align-items:center;gap:2px;margin-bottom:2px;">'
            html += (f'<div style="width:22px;text-align:right;font-size:10px;font-weight:500;'
                     f'color:#555;margin-right:4px;line-height:16px;">{p}</div>')
            for d in range(1, last_day + 1):
                d_str = f"{year}-{month:02d}-{d:02d}"
                secs  = period_stats[d_str][p]
                color = _period_color(secs)
                wd    = _date(year, month, d).weekday()
                dur   = fmt_duration(secs) if secs > 0 else "无记录"
                tip   = f"{month}月{d}日（周{_WD_CN[wd]}）{p} · {dur}"
                html += (f'<div style="width:15px;height:16px;border-radius:3px;'
                         f'background:{color};" data-htip="{tip}"></div>')
            html += '</div>'

        html += """<script>(function(){
  var tip=document.createElement('div');
  tip.style.cssText='position:fixed;background:rgba(28,28,30,.85);color:#fff;padding:4px 9px;border-radius:6px;font-size:11px;pointer-events:none;display:none;z-index:9999;white-space:nowrap;';
  document.body.appendChild(tip);
  document.querySelectorAll('[data-htip]').forEach(function(el){
    el.addEventListener('mouseenter',function(e){tip.textContent=el.dataset.htip;tip.style.display='block';tip.style.left=(e.clientX+12)+'px';tip.style.top=(e.clientY-32)+'px';});
    el.addEventListener('mousemove',function(e){tip.style.left=(e.clientX+12)+'px';tip.style.top=(e.clientY-32)+'px';});
    el.addEventListener('mouseleave',function(){tip.style.display='none';});
  });
})();</script>"""
        html += '</div>'

        reflection = (get_monthly_reflection(year, month) or "").replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")
        html += (f'<div style="border-top:.5px solid #E0E0E0;padding-top:14px;margin-top:16px;">'
                 f'<div style="font-size:12px;color:#8E8E93;margin-bottom:6px;">本月 reflection</div>'
                 f'<textarea id="monthly_reflection" rows="4">{reflection}</textarea>'
                 f'<div style="text-align:right;margin-top:8px;">'
                 f'<button class="btn btn-primary" '
                 f'onclick="saveMonthlyReflection({year},{month},'
                 f'document.getElementById(\'monthly_reflection\').value)">保存</button>'
                 f'</div></div>')
        return html

    @objc.python_method
    def _build_ranking_content(self, year: int, month: int) -> str:
        stats       = get_month_stats(year, month)
        by_category = stats.get("by_category", {})

        html = self._build_month_header(year, month, "ranking")

        # ── 每日折线图 ────────────────────────────────────────────────────────
        daily_stats = get_month_daily_stats(year, month)
        _nm_first = _date(year + (1 if month == 12 else 0), month % 12 + 1, 1)
        last_day  = (_nm_first - _timedelta(days=1)).day
        day_labels = [str(d + 1) for d in range(last_day)]

        cat_line_colors = {
            "学校学习": "#5B8DEF",
            "自主学习": "#9B72CF",
            "娱乐":     "#4ECDC4",
            "其他":     "#B8BCC8",
        }
        line_datasets = []
        for cat in CATEGORIES:
            data = []
            for d in range(last_day):
                d_str = f"{year}-{month:02d}-{d + 1:02d}"
                data.append(round(daily_stats.get(d_str, {}).get(cat, 0) / 60, 1))
            line_datasets.append({
                "label": cat,
                "data": data,
                "borderColor": cat_line_colors[cat],
                "backgroundColor": cat_line_colors[cat] + "33",
                "borderWidth": 2,
                "pointRadius": 0,
                "pointHoverRadius": 5,
                "pointHitRadius": 10,
                "tension": 0.4,
                "fill": False,
                "hidden": cat != "自主学习",
            })
        # 动态 Y 轴上限：当月最大值向上取整到下一小时，再加 1h
        all_pts = [v for ds in line_datasets for v in ds["data"]]
        max_min = max(all_pts) if all_pts else 0
        suggested_max = (int(max_min // 60) + 2) * 60  # 单位：分钟

        line_json   = json.dumps({"labels": day_labels, "datasets": line_datasets}, ensure_ascii=False)
        colors_json = json.dumps([cat_line_colors[cat] for cat in CATEGORIES])

        # 图表上方右对齐横向按钮行
        btn_row = '<div style="display:flex;justify-content:flex-end;gap:6px;margin-bottom:8px;">'
        for i, cat in enumerate(CATEGORIES):
            color = cat_line_colors[cat]
            if cat == "自主学习":
                btn_style = f"background:#fff;border:2px solid {color};color:#1C1C1E;"
            else:
                btn_style = "background:#fff;border:2px solid #E0DEDA;color:#8E8E93;"
            btn_row += (f'<button id="linebtn{i}" onclick="toggleRankingLine({i})" '
                        f'style="padding:5px 10px;border-radius:99px;'
                        f'font-size:11px;cursor:pointer;{btn_style}">{cat}</button>')
        btn_row += '</div>'

        html += (f'{btn_row}'
                 f'<div class="chart-wrap">'
                 f'<canvas id="rankingLineChart" height="140"></canvas></div>')

        html += f"""<script>(function(){{
  if(window._rankingLine)window._rankingLine.destroy();
  window._rankingLine=new Chart(document.getElementById('rankingLineChart').getContext('2d'),{{
    type:'line',data:{line_json},
    options:{{responsive:true,interaction:{{mode:'index',intersect:false}},
      plugins:{{legend:{{display:false}},
        tooltip:{{callbacks:{{label:function(c){{
          var m=c.parsed.y,h=Math.floor(m/60),mn=Math.round(m%60);
          return c.dataset.label+': '+(h>0?h+'h '+mn+'m':mn+'m');
        }}}}}}}},
      scales:{{
        x:{{grid:{{display:false}},ticks:{{maxRotation:0,font:{{size:10}},maxTicksLimit:10}}}},
        y:{{suggestedMax:{suggested_max},ticks:{{callback:function(v){{return v>=60?Math.floor(v/60)+'h':v+'m';}},font:{{size:10}}}},grid:{{color:'#F0F0F0'}}}}
      }}
    }}
  }});
  var _lc={colors_json};
  window.toggleRankingLine=function(idx){{
    var ds=window._rankingLine.data.datasets[idx];
    ds.hidden=!ds.hidden;
    var btn=document.getElementById('linebtn'+idx);
    if(ds.hidden){{btn.style.border='2px solid #E0DEDA';btn.style.color='#8E8E93';}}
    else{{btn.style.border='2px solid '+_lc[idx];btn.style.color='#1C1C1E';}}
    window._rankingLine.update();
  }};
}})();</script>
<hr style="border:none;border-top:0.5px solid #E0E0E0;margin:0 0 20px;">"""

        # ── 二级分类进度条列表 ─────────────────────────────────────────────────
        items = []
        for l1, cat_data in by_category.items():
            for l2, secs in cat_data.get("sub", {}).items():
                if secs >= 300:
                    items.append((l1, l2, secs))
        items.sort(key=lambda x: -x[2])
        items = items[:15]

        if not items:
            return (html + '<div style="font-size:13px;color:#8E8E93;text-align:center;'
                    'padding:30px 0;">暂无数据</div>')

        max_s = items[0][2]
        html += '<div style="margin-top:14px;">'
        for i, (l1, l2, secs) in enumerate(items):
            color = COLORS.get(l1, "#B8BCC8")
            pct   = int(secs / max_s * 100) if max_s else 0
            html += (
                f'<div style="display:flex;align-items:center;margin-bottom:9px;">'
                f'<span style="width:20px;font-size:11px;color:#8E8E93;flex-shrink:0;">{i+1}</span>'
                f'<span style="width:100px;font-size:12px;color:#1C1C1E;overflow:hidden;'
                f'white-space:nowrap;text-overflow:ellipsis;flex-shrink:0;">{l2}</span>'
                f'<span style="width:5px;height:5px;border-radius:50%;background:{color};'
                f'flex-shrink:0;margin-right:8px;display:inline-block;"></span>'
                f'<div style="flex:1;height:8px;border-radius:4px;background:#F0F0F0;">'
                f'<div style="width:{pct}%;height:8px;border-radius:4px;background:{color};"></div>'
                f'</div>'
                f'<span style="width:50px;font-size:12px;color:#1C1C1E;margin-left:8px;'
                f'flex-shrink:0;text-align:right;">{fmt_duration(secs)}</span>'
                f'</div>'
            )
        html += '</div>'

        # ── 月报 AI 叙事总结（进度条列表下方）────────────────────────────────
        monthly_summary = get_monthly_summary(year, month)
        if monthly_summary:
            escaped = (monthly_summary
                       .replace("&", "&amp;")
                       .replace("<", "&lt;")
                       .replace('"', "&quot;"))
            paras = [p.strip() for p in escaped.split("\n\n") if p.strip()]
            paras_html = "".join(
                f'<div style="font-size:13px;color:#3C3C43;line-height:1.8;'
                f'margin-bottom:{"10px" if i < len(paras) - 1 else "0"};">{p}</div>'
                for i, p in enumerate(paras)
            )
            html += (
                f'<div style="margin-top:32px;background:#FAFAF8;border-radius:12px;'
                f'padding:18px 20px;box-shadow:0 1px 4px rgba(0,0,0,.08);">'
                f'<div style="font-size:11px;color:#8E8E93;margin-bottom:12px;'
                f'letter-spacing:.3px;">本月总结</div>'
                f'{paras_html}'
                f'</div>'
            )
        else:
            html += (
                '<div style="margin-top:32px;background:#FAFAF8;border-radius:12px;'
                'padding:18px 20px;box-shadow:0 1px 4px rgba(0,0,0,.08);'
                'font-size:12px;color:#C0C0C5;text-align:center;">'
                '月底会自动生成本月总结</div>'
            )

        return html

    # ── WKUIDelegate ─────────────────────────────────────────────────────────

    def webView_runJavaScriptConfirmPanelWithMessage_initiatedByFrame_completionHandler_(
            self, webview, message, frame, handler):
        from AppKit import NSAlert
        alert = NSAlert.alloc().init()
        alert.setMessageText_(message)
        alert.addButtonWithTitle_("确定")
        alert.addButtonWithTitle_("取消")
        result = alert.runModal()
        handler(result == 1000)

    # ── WKNavigationDelegate ─────────────────────────────────────────────────

    def webView_decidePolicyForNavigationAction_decisionHandler_(
            self, webview, action, handler):
        url = action.request().URL()
        if url is not None and url.scheme() == "xd":
            handler(0)
            self._handle_action(url)
        else:
            handler(1)

    @objc.python_method
    def _handle_action(self, url):
        action = url.host() or ""
        qs = urllib.parse.parse_qs(url.query() or "", keep_blank_values=True)

        def _int(key):
            return int(qs[key][0]) if key in qs else None

        def _str(key):
            return urllib.parse.unquote(qs[key][0]) if key in qs else ""

        def _valid_int(key, lo, hi):
            v = _int(key)
            if v is None:
                return None
            if not (lo <= v <= hi):
                print(f"[report_window] 非法参数 {key}={v!r}，合法范围 [{lo}, {hi}]，已忽略")
                return None
            return v

        if action == "navigate_week":
            y, w = _valid_int("year", 2020, 2100), _valid_int("week", 1, 53)
            if y and w:
                self._render_week(y, w)

        elif action == "navigate_week_sub":
            y, w, sub = _valid_int("year", 2020, 2100), _valid_int("week", 1, 53), _str("sub")
            if y and w:
                self._render_week(y, w, sub or None)

        elif action == "navigate_week_month":
            # Click on a month entry in the week sidebar section
            y, m = _valid_int("year", 2020, 2100), _valid_int("month", 1, 12)
            if y and m:
                target = None
                for wy, ww, ds, _de in get_all_weeks():
                    d_start = _date.fromisoformat(ds)
                    if d_start.year == y and d_start.month == m:
                        target = (wy, ww)
                        break  # newest-first → first match is latest week in that month
                if target is None:
                    # No data: first ISO week containing the 1st of that month
                    d = _date(y, m, 1)
                    wy, ww, _ = d.isocalendar()
                    target = (wy, ww)
                self._render_week(*target)

        elif action == "navigate_month":
            y, m = _valid_int("year", 2020, 2100), _valid_int("month", 1, 12)
            if y and m:
                self._render_month(y, m)

        elif action == "navigate_month_sub":
            y, m, sub = _valid_int("year", 2020, 2100), _valid_int("month", 1, 12), _str("sub")
            if y and m:
                self._render_month(y, m, sub or None)

        elif action == "save_reflection":
            y, w, content = _valid_int("year", 2020, 2100), _valid_int("week", 1, 53), _str("content")
            if y and w:
                save_reflection(y, w, content)
                self._render_week(y, w, self._current_sub)

        elif action == "navigate_booknotes_history":
            self._render_booknotes_history()

        elif action == "navigate_booknotes_new":
            y, w = _valid_int("year", 2020, 2100), _valid_int("week", 1, 53)
            if y and w:
                self._editing_note = None
                self._render_week(y, w, sub="booknotes_new")

        elif action == "navigate_booknotes_edit":
            y, w, nid = _valid_int("year", 2020, 2100), _valid_int("week", 1, 53), _int("id")
            if y and w and nid:
                all_notes = get_book_notes()
                note = next((n for n in all_notes if n["id"] == nid), None)
                self._editing_note = note
                self._render_week(y, w, sub="booknotes_edit")

        elif action == "save_book_note":
            y, w = _valid_int("year", 2020, 2100), _valid_int("week", 1, 53)
            save_book_note(
                _str("title"), _str("author"),
                _str("date_read"), _str("tags"), _str("content"),
            )
            if y and w:
                self._render_week(y, w, sub="booknotes", toast="已保存")
            elif self._current_type == "week" and self._current_key:
                self._render_week(*self._current_key, sub="booknotes", toast="已保存")

        elif action == "update_book_note":
            note_id = _int("id")
            if note_id:
                update_book_note(
                    note_id,
                    _str("title"), _str("author"),
                    _str("date_read"), _str("tags"), _str("content"),
                )
                if self._current_type == "week" and self._current_key:
                    self._render_week(*self._current_key, sub="booknotes", toast="已更新")

        elif action == "delete_book_note":
            note_id = _int("id")
            if note_id:
                delete_book_note(note_id)
                self._render_booknotes_history(toast="已删除")

        elif action == "save_monthly_reflection":
            y, m, content = _valid_int("year", 2020, 2100), _valid_int("month", 1, 12), _str("content")
            if y and m:
                save_monthly_reflection(y, m, content)
                self._webview.evaluateJavaScript_completionHandler_(
                    'showToast("已保存")', None
                )

        elif action == "navigate_settings":
            self._render_settings()

        elif action == "save_api_enabled":
            from settings import load_settings, save_settings
            val = qs.get("value", ["1"])[0] == "1"
            s = load_settings()
            s["api_enabled"] = val
            save_settings(s)
            if not val:
                try:
                    from classifier import clear_api_key_invalid, clear_api_format_error
                    clear_api_key_invalid()
                    clear_api_format_error()
                except Exception:
                    pass
            self._render_settings(toast="已保存，重启后生效")

        elif action == "save_api_base_url":
            from settings import load_settings, save_settings, get_api_credentials
            val = urllib.parse.unquote(qs.get("value", [""])[0]).strip()
            s = load_settings()
            s["api_base_url"] = val
            save_settings(s)
            api_key, _ = get_api_credentials()
            if val and api_key:
                self._render_settings(toast="已保存，正在验证连通性...")
                import threading
                threading.Thread(
                    target=self._bg_validate_base_url, args=(api_key, val), daemon=True
                ).start()
            elif val and not api_key:
                self._render_settings(toast="Base URL 已保存（请先配置 API Key 才能验证连通性）")
            else:
                self._render_settings(toast="Base URL 已保存")

    @objc.python_method
    def _bg_validate_base_url(self, api_key: str, base_url: str):
        """子线程：验证 base_url 连通性，结果通过 performSelector 切回主线程。"""
        import json as _json
        try:
            import anthropic as _anthropic
        except ImportError:
            result = _json.dumps({"ok": False, "msg": "缺少 anthropic 依赖"})
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "onBaseUrlValidated:", result, False
            )
            return
        try:
            client = _anthropic.Anthropic(api_key=api_key, base_url=base_url)
            client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
            )
            result = _json.dumps({"ok": True, "msg": "连通性验证通过"})
        except _anthropic.AuthenticationError:
            result = _json.dumps({"ok": False, "msg": "该地址下 API Key 认证失败，请检查 Key 与 URL 是否匹配"})
        except _anthropic.RateLimitError:
            result = _json.dumps({"ok": True, "msg": "连通性验证通过（触发限流，Key 有效）"})
        except (_anthropic.APIConnectionError, _anthropic.APITimeoutError):
            result = _json.dumps({"ok": False, "msg": "无法连接到该地址，请检查 URL 是否正确"})
        except _anthropic.APIResponseValidationError as e:
            result = _json.dumps({"ok": False, "msg": f"服务返回格式与 Anthropic SDK 不兼容，请确认服务商支持"})
        except Exception as e:
            result = _json.dumps({"ok": False, "msg": f"验证时遇到异常：{e}"})
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "onBaseUrlValidated:", result, False
        )

    def onBaseUrlValidated_(self, result_json):
        """主线程 ObjC 方法：接收验证结果并刷新设置页。"""
        import json as _json
        try:
            data = _json.loads(str(result_json))
            ok = data.get("ok", False)
            msg = data.get("msg", "未知结果")
        except Exception:
            ok = False
            msg = "验证结果解析失败"
        toast = f"✓ {msg}" if ok else f"✗ {msg}"
        self._render_settings(toast=toast)


# ── 对外接口 ──────────────────────────────────────────────────────────────────

_instance = None


def show_report_window():
    global _instance
    if _instance is None:
        _instance = ReportWindow.alloc().init()
    _instance.show()
