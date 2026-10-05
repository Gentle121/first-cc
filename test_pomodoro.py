# -*- coding: utf-8 -*-
"""番茄钟逻辑冒烟测试：不弹通知、不播放声音，只验证状态机。"""
import time
import tkinter as tk

import pomodoro as P


def finish_current(app):
    """把当前阶段瞬间推到结束，并触发一次 tick。"""
    app.remaining = 0.0
    app.running = True
    app.deadline = time.monotonic() - 1
    app._tick()


def main():
    root = tk.Tk()
    app = P.PomodoroApp(root)
    app._notify = lambda *a, **k: None          # 静音，别打扰用户

    assert app.phase == "focus" and app.remaining == 25 * 60
    app._render()
    assert app.canvas.itemcget(app.time_text, "text") == "25:00"
    print("初始状态            :", app.phase, app.remaining, "秒，显示 25:00")

    # 开始 / 暂停
    app.toggle()
    assert app.running and app.deadline > time.monotonic()
    app.toggle()
    assert not app.running
    print("开始/暂停           : OK，暂停后剩余", round(app.remaining), "秒")

    # 走完 4 个专注，检查 短休息 x3 -> 长休息 -> 计数归零
    seq = []
    for i in range(4):
        finish_current(app)                                     # 完成一次专注
        seq.append((app.phase, app.completed_focus, app.running))
        if i < 3:
            finish_current(app)                                 # 完成随后的短休息
    for phase, count, running in seq:
        print(f"  番茄 {count} 完成 -> {phase:12s} 自动开始={running}")

    assert [s[0] for s in seq] == ["short_break"] * 3 + ["long_break"], seq
    assert [s[1] for s in seq] == [1, 2, 3, 4]
    assert all(s[2] for s in seq), "休息应当自动开始"

    # 长休息结束 -> 回到专注，计数归零，且不自动开始
    assert app.phase == "long_break" and app.completed_focus == 4
    finish_current(app)
    assert app.phase == "focus" and app.completed_focus == 0 and not app.running
    print("长休息结束          : 回到 focus，番茄计数归零，等待手动开始 =", not app.running)

    # 短休息结束也要回到专注（第 1 个番茄之后）
    finish_current(app)
    assert app.phase == "short_break"
    finish_current(app)
    assert app.phase == "focus" and not app.running and app.completed_focus == 1
    print("短休息结束          : 回到 focus =", app.phase)

    # 重置
    app.remaining = 100.0
    app.reset()
    assert app.remaining == 25 * 60 and not app.running
    print("重置                : OK")

    # 跳过
    app.skip()
    assert app.phase == "short_break"
    app.skip()
    assert app.phase == "focus"
    print("跳过                : OK")

    # 时间格式边界
    for remain, expect in [(1500.0, "25:00"), (0.4, "00:01"), (0.0, "00:00"), (61.0, "01:01")]:
        app.remaining = remain
        app._render()
        got = app.canvas.itemcget(app.time_text, "text")
        assert got == expect, (remain, got, expect)
    print("时间格式            : OK")

    root.destroy()
    print("\n全部通过 ✅")


if __name__ == "__main__":
    main()
