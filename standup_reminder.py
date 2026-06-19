"""
起身提醒计时器。
- 由 tracker.py 在每次有效活动帧后调用 timer.add_active_seconds()
- 累计达到间隔时，在主线程弹出 NSAlert
"""
import threading


class StandupTimer:
    def __init__(self):
        self._lock = threading.Lock()
        self._active_seconds = 0.0
        self._interval_seconds = 45 * 60
        self._enabled = False
        self._last_elapsed_min = 45  # showStandupAlert_ 读取用

    def configure(self, enabled: bool, interval_minutes: int):
        with self._lock:
            self._enabled = enabled
            self._interval_seconds = max(1, interval_minutes) * 60
            self._last_elapsed_min = max(1, interval_minutes)
            if not enabled:
                self._active_seconds = 0.0

    def add_active_seconds(self, seconds: float, activity_type: str):
        if activity_type in ("idle", "dock"):
            return
        should_fire = False
        with self._lock:
            if not self._enabled:
                return
            self._active_seconds += seconds
            if self._active_seconds >= self._interval_seconds:
                self._active_seconds = 0.0
                self._last_elapsed_min = int(self._interval_seconds // 60)
                should_fire = True
        if should_fire:
            self._fire()

    def _fire(self):
        try:
            from AppKit import NSApplication
            app = NSApplication.sharedApplication()
            delegate = app.delegate()
            if delegate is not None:
                delegate.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "showStandupAlert:", None, False
                )
        except Exception as e:
            print(f"[standup] 提醒失败: {e}")


timer = StandupTimer()
