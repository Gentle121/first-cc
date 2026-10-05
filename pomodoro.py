# -*- coding: utf-8 -*-
"""
桌面番茄钟 (Pomodoro Timer)
===========================

纯 Python 标准库实现，不需要安装任何第三方包：
  * 界面      tkinter / Canvas 手绘圆环进度
  * 提示音    winsound（Windows 自带系统音效，无需音频文件）
  * 系统通知  PowerShell 调 Windows 原生 Toast（尽力而为，失败不影响使用）

运行：
    python pomodoro.py

快捷键：
    空格  开始 / 暂停
    R     重置当前阶段
    N     跳过当前阶段

--------------------------------------------------------------------------------
代码结构（从上到下分四块）
--------------------------------------------------------------------------------
  1. 配置层    常量 + PHASES 表 —— 所有"魔法数字"集中在这里，改时长只动这块
  2. 控件层    PillButton / TextButton —— 两个自绘的小控件
  3. 通知层    _TOAST_PS / show_toast / play_sound —— 与 Windows 系统打交道
  4. 主程序    PomodoroApp —— 状态机（计时逻辑）+ _render（把状态画到屏幕上）

核心设计：**状态和显示彻底分离**。
  * 状态 = phase / remaining / running / completed_focus 这 4 个字段
  * _render() 是纯函数：读状态 → 画界面，不修改任何状态
  * 任何时候状态变了，就调一次 _render()，界面自然跟着变

这样加新功能时只需要改状态，不用担心界面不同步；反过来改界面样式也不会碰坏计时逻辑。
"""
from __future__ import annotations   # 让类型注解延迟求值，可写 tk.Tk 这类前向引用

import base64        # 通知文本转 base64，绕开 PowerShell 命令行编码问题（见通知层注释）
import math          # 圆环端点的三角函数、时间显示的向上取整
import os            # 路径拼接、判断操作系统
import subprocess    # 调 PowerShell 弹系统通知
import sys           # 读命令行参数（--selftest）、获取解释器路径
import tempfile      # 拿临时目录，存放通知用的 .ps1 脚本
import threading     # 声音和通知丢到后台线程，避免卡住界面
import time          # monotonic 单调时钟，计时精度的关键
import tkinter as tk # GUI 框架


# ============================================================================
# 1. 配置层
# ============================================================================
# 想改时长/轮数，只动下面这 5 行即可，其他地方不用碰。
FOCUS_MINUTES = 25            # 专注时长（分钟）
SHORT_BREAK_MINUTES = 5       # 短休息时长
LONG_BREAK_MINUTES = 15       # 长休息时长
ROUNDS_BEFORE_LONG_BREAK = 4  # 每完成几个番茄后进入一次长休息
TICK_MS = 80                  # 界面刷新间隔（毫秒）。80ms ≈ 每秒 12.5 帧，
                              # 既够圆环动画顺滑，又不至于白白烧 CPU

# ---------------------------------------------------------------- 配色
# 浅色主题「温暖 · 柔和 · 简洁」。全部集中在这里，换肤色只改这一块。
#
# 规则：**只用色卡上的 8 个色**，不做任何提亮 / 混色 / 透明度派生。
# 色卡：主色 #C65A4A / 背景 #F7F2EC / 辅助 #EAE3DB / 浅辅助 #F1E8E0
#       高亮 #F8DCCF / 弱化 #D9D2CC / 文本 #6F655E / 分割线 #E5E0DA
COL_BG = "#F7F2EC"        # 窗口背景、各容器底色（背景）
COL_TRACK = "#D9D2CC"     # 圆环底槽 / 未点亮的番茄圆点（弱化）
COL_TEXT = "#6F655E"      # 主文字：时间数字（文本）
COL_MUTED = COL_TEXT      # 次要文字：标题、重置/跳过。色卡里没有比"文本"更浅的
                          # 可读色，所以直接复用"文本"，靠悬停变主色来区分
COL_LINE = "#E5E0DA"      # 两个文字按钮中间的分隔点（分割线 / 轮廓）
COL_HIGHLIGHT = "#F8DCCF" # 主按钮鼠标悬停时的底色（高亮）
COL_FOCUS = "#C65A4A"     # 专注（主色）
COL_BREAK = COL_TEXT      # 短休息：复用"文本"
COL_LONG = COL_TEXT       # 长休息：复用"文本"
# 关于休息阶段为什么是"文本灰"而不是绿/蓝：色卡是**单强调色**板，只有一个彩色
# （主色 #C65A4A），没有绿也没有蓝。严格照卡就只能拿中性色来标休息，于是形成
# "红 = 正在专注 / 灰 = 休息中"的语义；短休和长休颜色因此相同，靠阶段名和时长区分。

# ---------------------------------------------------------------- 阶段定义表
# 把"一个阶段需要的全部信息"打包成一张表，好处是逻辑代码里不用写 if/else 分支：
# 想知道当前阶段的颜色？PHASES[self.phase]["color"]，一行搞定。
# 以后要加"超长休息"之类的阶段，这里加一行 + 改一处跳转逻辑即可。
PHASES = {
    "focus":       {"label": "专注中", "color": COL_FOCUS, "seconds": FOCUS_MINUTES * 60},
    "short_break": {"label": "短休息", "color": COL_BREAK, "seconds": SHORT_BREAK_MINUTES * 60},
    "long_break":  {"label": "长休息", "color": COL_LONG,  "seconds": LONG_BREAK_MINUTES * 60},
}


# ============================================================================
# 2. 控件层
# ============================================================================
class PillButton(tk.Canvas):
    """圆角胶囊按钮（主按钮"开始/暂停"用）。

    为什么继承 Canvas 自绘？因为 tk.Button 做不出圆角，Windows 上还会强制
    套一层系统主题边框，跟这套浅色界面完全不搭。用 Canvas 画就完全可控。

    形状 = 左半圆 + 右半圆 + 中间矩形，三段同色拼起来正好是一个胶囊。
    下面用 height 当直径，所以圆角的半径恒等于 height/2，圆角永远完美贴合。
    """

    def __init__(self, parent, text, command, width=210, height=54,
                 fill=COL_FOCUS, fg=COL_BG, font=("Segoe UI", 14, "bold")):
        # highlightthickness=0 去掉 Canvas 默认的焦点边框，bd=0 去掉边框
        super().__init__(parent, width=width, height=height, bg=COL_BG,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._command = command          # 点击时调用的函数
        self._fill = fill                # 常态底色（随阶段变：专注主色 / 休息文本色）
        self._fg = fg                    # 常态文字色（胶囊底色偏深，所以用浅色字）

        r = height / 2                   # 圆角半径 = 高度的一半
        self._shape = [                  # 三块拼图，统一放在这个列表里方便整体改色
            #            左上角      右下角
            self.create_oval(0, 0, height, height, fill=fill, outline=""),          # 左半圆
            self.create_oval(width - height, 0, width, height, fill=fill, outline=""),  # 右半圆
            self.create_rectangle(r, 0, width - r, height, fill=fill, outline=""),  # 中间补齐
        ]
        # 文字单独作为一层盖在三块图形之上
        self._label = self.create_text(width / 2, height / 2, text=text, fill=fg, font=font)

        # 事件绑定：tk.Canvas 不像 Button 那样自带 click 事件，要自己绑鼠标事件
        self.bind("<Button-1>", lambda _e: self._command())            # 左键点击
        self.bind("<Enter>", lambda _e: self._hover_on())              # 鼠标移入 → 高亮
        self.bind("<Leave>", lambda _e: self._hover_off())             # 鼠标移出 → 复原

    def _paint(self, color):
        """把三块拼图统一改成指定颜色。"""
        for item in self._shape:
            self.itemconfigure(item, fill=color)

    def _hover_on(self):
        """悬停态：底色换成色卡的"高亮"，文字同时换成"文本"色。

        连文字一起换，是因为高亮色 #F8DCCF 本身很浅，
        常态那层浅色字盖上去会看不清，必须配深色字才够对比。
        """
        self._paint(COL_HIGHLIGHT)
        self.itemconfigure(self._label, fill=COL_TEXT)

    def _hover_off(self):
        """移开鼠标：底色和文字都还原成常态。"""
        self._paint(self._fill)
        self.itemconfigure(self._label, fill=self._fg)

    def set(self, text: str = None, fill: str = None):
        """外部更新按钮：改文字、改颜色（阶段切换时调用）。

        两个参数都可选、都可单独传，所以调用方能只改文字不改色，反之亦然。
        """
        # 悬停底色恒为色卡的"高亮"，与阶段无关；换色时只需更新常态底色。
        if fill and fill != self._fill:      # 颜色真的变了才重绘，避免无谓刷新
            self._fill = fill
            self._paint(fill)
        if text is not None:
            self.itemconfigure(self._label, text=text)


class TextButton(tk.Label):
    """无边框文字按钮（"重置"/"跳过"用）。

    用 Label 而不是 Button，是因为 Label 在 Windows 上不会套系统主题边框，
    背景色能真正透明（设成和父容器一致即可），视觉上就是纯文字。
    """

    def __init__(self, parent, text, command):
        # bg 必须和父容器颜色一致，才能"隐形"融入背景
        self._fg = COL_MUTED             # 常态文字色，悬停结束后要还原回它
        super().__init__(parent, text=text, fg=self._fg, bg=COL_BG,
                         cursor="hand2", font=("Segoe UI", 11))
        self.bind("<Button-1>", lambda _e: command())
        # 常态与悬停都在色卡内：常态"文本"，悬停切到"主色"给一点反馈。
        # （色卡里没有比"文本"更浅的可读色，所以用换色而不是深浅变化来提示。）
        self.bind("<Enter>", lambda _e: self.config(fg=COL_FOCUS))     # 悬停 → 主色
        self.bind("<Leave>", lambda _e: self.config(fg=self._fg))      # 移开 → 常态


# ============================================================================
# 3. 通知层（与 Windows 系统交互）
# ============================================================================
# 为什么要绕这么一大圈？因为 Python 标准库**没有**任何弹 Windows 原生通知的办法。
# 三条路都被堵死了：
#   * win10toast / plyer  → 第三方包，违背"零依赖"原则
#   * ctypes 直接调 WinRT  → 需要写一大堆 COM 互操作代码，极其繁琐
#   * tkinter 自己画个窗口 → 不是系统通知，不进入通知中心，全屏程序会挡住
# 剩下最务实的方案：让 PowerShell 替我们调 WinRT（PowerShell 5.1 内置了 WinRT 桥接）。
#
# ┌──────────────────────────────────────────────────────────────────────────┐
# │ 注意：这个 .ps1 脚本不直接调"发通知的命令行"，而是把通知内容用 base64    │
# │ 传进去，脚本内部再解码。原因见 show_toast() 里的注释。                   │
# └──────────────────────────────────────────────────────────────────────────┘
_TOAST_PS = r"""
param([string]$TitleB64, [string]$BodyB64)
$ErrorActionPreference = 'Stop'
# 先把 base64 还原成 UTF-8 原文。这样中文、emoji 都不会乱码。
$Title = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($TitleB64))
$Body  = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($BodyB64))
# 加载 WinRT 的两个类型（这行是 PowerShell 调 WinRT 的固定写法，[void] 是为了吞掉返回值）
[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]
[void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime]
# 用一个系统预置模板：ToastText02 = 一行粗体标题 + 一行正文（最常用的两行样式）
$tpl   = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
             [Windows.UI.Notifications.ToastTemplateType]::ToastText02)
# 模板里有两个空的 <text> 节点，把标题和正文填进去
$texts = $tpl.GetElementsByTagName('text')
$texts.Item(0).AppendChild($tpl.CreateTextNode($Title)) | Out-Null
$texts.Item(1).AppendChild($tpl.CreateTextNode($Body))  | Out-Null
$toast = New-Object Windows.UI.Notifications.ToastNotification $tpl
# 用 PowerShell 自己的 AppUserModelID 发送。
# 微软规定：未打包的桌面应用想弹通知，必须有个"已注册的应用身份"，否则
# CreateToastNotifier 会直接报 Element not found。借用 PowerShell 的身份是最省事的做法，
# 代价是通知横幅顶部会显示 "Windows PowerShell" 而不是 "番茄钟"。
# 想换成自己的名字，得在开始菜单注册带 AppUserModelID 的快捷方式（约 60 行 COM 代码）。
$appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
"""

_toast_script = None   # 缓存已写好的 .ps1 路径，避免每次通知都重复写文件


def _ensure_toast_script() -> str:
    """把上面的 PowerShell 脚本写到临时目录，返回它的路径（全局只写一次）。

    为什么不放在程序目录？因为打包成 exe 后常被放到只读位置
    （Program Files、U 盘、光盘），往那里写文件会直接失败。
    %TEMP% 是任何情况下都可写的地方。
    """
    global _toast_script
    if _toast_script is None:
        folder = os.path.join(tempfile.gettempdir(), "pomodoro_tk")
        os.makedirs(folder, exist_ok=True)          # 存在也不报错，所以要 exist_ok
        path = os.path.join(folder, "toast.ps1")
        with open(path, "w", encoding="utf-8") as f:
            f.write(_TOAST_PS)
        _toast_script = path
    return _toast_script


def show_toast(title: str, body: str):
    """弹出 Windows 原生通知。

    返回值：成功返回 None，失败返回一段错误描述字符串。
    （调用方 _notify 通常忽略返回值，── 这个返回值主要是给 --selftest 排查用的）
    """
    if os.name != "nt":
        return "非 Windows 平台，跳过"     # 这个程序只针对 Windows，其他平台直接放弃

    try:
        script = _ensure_toast_script()

        # ── 为什么要 base64 编码 ──────────────────────────────────────────
        # PowerShell 5.1 是通过**系统 ANSI 代码页**来解析命令行参数的，
        # 不是 UTF-8。中文标题直接当参数传过去会变成乱码。
        # 转成纯 ASCII 的 base64 就没有任何编码歧义了，脚本内部再解码还原。
        b64 = lambda s: base64.b64encode(s.encode("utf-8")).decode("ascii")

        result = subprocess.run(
            ["powershell", "-NoProfile",       # 不加载用户配置文件，启动更快也更干净
             "-NonInteractive",                # 不弹任何交互提示，避免卡死
             # -File 执行脚本文件
             # 故意不加 -ExecutionPolicy Bypass：这个脚本是我们自己刚写到临时目录的
             # 本地文件，没有"从网上下载"的标记，默认的 RemoteSigned 策略本来就放行。
             # 没必要为了省事去覆盖系统的安全策略。
             "-File", script, "-TitleB64", b64(title), "-BodyB64", b64(body)],
            timeout=15,                        # 兜底超时，万一 PowerShell 卡住也不会拖死程序
            creationflags=0x08000000,          # CREATE_NO_WINDOW：别闪一个黑色控制台窗口
            # stdout/stderr 显式指向 DEVNULL，而不是让子进程继承父进程的句柄。
            # 这是打包成 --windowed exe 后的一个经典坑：那种模式下的程序没有有效的
            # stdio 句柄，子进程继承时 subprocess 会直接抛异常，导致通知静默失效。
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return None if result.returncode == 0 else f"powershell 退出码 {result.returncode}"
    except Exception as exc:
        # 通知失败绝不能影响主功能（计时），所以吞掉所有异常，只把原因返回给调用方
        return f"{type(exc).__name__}: {exc}"


def play_sound(alias: str) -> None:
    """播放 Windows 自带音效。

    alias 是系统音效的注册名，例如 "SystemAsterisk"、"SystemExclamation"。
    好处是不用附带任何音频文件，也不占体积。

    用 SND_ALIAS 表示"按名字播放系统音效"，SND_ASYNC 表示异步播放
    （立刻返回、不阻塞调用方），否则声音播完前界面会卡住。
    """
    try:
        import winsound                    # Windows 独有模块，非 Windows 上 import 会失败
        winsound.PlaySound(alias, winsound.SND_ALIAS | winsound.SND_ASYNC)
    except Exception:
        pass                               # 放不出声就算了，不影响计时


# ============================================================================
# 4. 主程序
# ============================================================================
class PomodoroApp:
    """番茄钟主程序。

    ------------------------------------------------------------------------
    状态机字段（只有这 4 个，整个程序的"真相"就在这里）
    ------------------------------------------------------------------------
      phase            当前处于哪个阶段，取值是 PHASES 的三个 key 之一
      completed_focus  本轮循环里已经完成几个番茄（0..4），用来决定下次是短休还是长休
      remaining        当前阶段还剩多少秒（浮点数，暂停时也靠它记住进度）
      running          是否正在倒计时
      deadline         monotonic 时间戳，表示"这个阶段应该在哪一刻结束"
                       —— 仅在 running=True 时有意义

    ------------------------------------------------------------------------
    阶段流转图
    ------------------------------------------------------------------------
                      ┌──────────── 专注结束（自动开始）────────────┐
                      ↓                                            │
        专注 ──(完成 1~3 个)──→ 短休息 ──(结束)──→ 专注 ─────────────┘
         │
         └──(完成第 4 个)──→ 长休息 ──(结束，计数归零)──→ 专注（等手动开始）

      关键点：**休息自动开始，专注不自动开始**。
      因为你可能离开座位，让专注阶段空转等于白扣一个番茄；而休息白跑无所谓。
    """
    # 圆环的几何参数。放在类属性里方便统一调整。
    RING_SIZE = 340      # 画布边长（正方形）
    RING_WIDTH = 16      # 圆环线条粗细
    RING_RADIUS = 138    # 圆环半径（到线条中心线的距离）

    def __init__(self, root: tk.Tk):
        self.root = root

        # ---- 初始化状态机 ----
        self.phase = "focus"
        self.completed_focus = 0
        self.remaining = float(PHASES["focus"]["seconds"])  # 一开始就是完整的 25 分钟
        self.running = False                                 # 启动时不自动开始，等用户点
        self.deadline = 0.0

        # ---- 窗口基本设置 ----
        root.title("番茄钟")
        root.configure(bg=COL_BG)
        root.resizable(False, False)   # 固定尺寸，因为所有坐标都是写死的

        # ---- 搭界面 → 绑快捷键 → 同步一次显示 → 居中 → 启动心跳 ----
        self._build_ui()
        self._bind_keys()
        self._sync_button()
        self._render()
        self._center_on_screen()
        # after 是"多少毫秒后调用一次"，不会阻塞主循环。
        # 注意这里是启动**心跳循环**——_tick 每次执行完会再排一次自己。
        self.root.after(TICK_MS, self._tick)

    # ------------------------------------------------------------------ 界面搭建
    def _build_ui(self):
        """一次性把所有控件建好。建好之后大部分控件是复用的，只改属性不重建。"""
        # wrap 是主容器，用 padx/pady 留出四周留白
        wrap = tk.Frame(self.root, bg=COL_BG)
        wrap.pack(fill="both", expand=True, padx=26, pady=24)

        # 顶部小标题
        tk.Label(wrap, text="番茄钟", fg=COL_MUTED, bg=COL_BG,
                 font=("Segoe UI", 12)).pack(pady=(0, 14))

        # ---------------- 圆环（核心视觉） ----------------
        s, w, r = self.RING_SIZE, self.RING_WIDTH, self.RING_RADIUS
        c = self.canvas = tk.Canvas(wrap, width=s, height=s, bg=COL_BG,
                                    highlightthickness=0)
        c.pack()
        self.cx = self.cy = s / 2      # 圆心坐标（正方形画布，所以 x=y=边长/2）
        self.ring_w = w

        # 圆环的外接正方形，Tk 画圆/弧都是用"外接矩形"来定义的
        box = (self.cx - r, self.cy - r, self.cx + r, self.cy + r)

        # ① 底槽：用 oval（椭圆）画一个整圆。
        #    为什么不用 arc 画整圆？因为 arc 的 extent=360 会留下一条头发丝细的接缝。
        #    fill="" 表示内部不填充，只描边，所以看起来就是个圆环。
        c.create_oval(*box, outline=COL_TRACK, width=w, fill="")

        # ② 进度弧：arc 才是真正显示"进度"的部分。
        #    start=90 表示从 12 点钟方向开始（Tk 的角度：0°=3点钟方向，逆时针为正）
        #    extent 为负数表示**顺时针**画。
        #    初始 extent=-0.01 是极小的一段弧，配 state="hidden" 先藏起来，
        #    等进度大于 0 时再显示并改成真实角度。
        self.arc = c.create_arc(*box, start=90, extent=-0.01, style="arc",
                                width=w, outline=COL_FOCUS, state="hidden")

        # ③ 圆头端点：Tk 的 arc 只能画"平头"线条，做不出圆润的收尾。
        #    解决办法：在弧的末端盖一个小圆点，直径等于线宽，看起来就是圆头了。
        #    位置每帧动态计算，所以初始坐标先给 (0,0,0,0)。
        self.cap = c.create_oval(0, 0, 0, 0, fill=COL_FOCUS, outline="", state="hidden")

        # ④ 环内文字：大号时间 + 阶段名。整体略微偏上（cy-12），视觉重心更稳。
        self.time_text = c.create_text(self.cx, self.cy - 12, text="25:00",
                                       fill=COL_TEXT, font=("Segoe UI", 52, "bold"))
        self.phase_text = c.create_text(self.cx, self.cy + 44, text="专注中",
                                        fill=COL_FOCUS, font=("Segoe UI", 13, "bold"))

        # ---------------- 番茄进度圆点 ----------------
        dots = self.dots_canvas = tk.Canvas(wrap, width=120, height=20, bg=COL_BG,
                                            highlightthickness=0)
        dots.pack(pady=(16, 18))
        self.dots = []
        for i in range(ROUNDS_BEFORE_LONG_BREAK):
            # 把 4 个点整体居中：第 i 个点相对中心的偏移是 (i - 1.5) * 26
            # i=0 → -39, i=1 → -13, i=2 → +13, i=3 → +39，左右对称，整体圆心在 x=60
            x = 60 + (i - (ROUNDS_BEFORE_LONG_BREAK - 1) / 2) * 26
            self.dots.append(dots.create_oval(x - 5, 5, x + 5, 15,   # 半径 5 的圆
                                              fill=COL_TRACK, outline=""))

        # ---------------- 按钮 ----------------
        self.btn_primary = PillButton(wrap, "开 始", self.toggle)
        self.btn_primary.pack()

        row = tk.Frame(wrap, bg=COL_BG)
        row.pack(pady=(14, 0))
        TextButton(row, "重置", self.reset).pack(side="left", padx=14)
        tk.Label(row, text="·", fg=COL_LINE, bg=COL_BG).pack(side="left")   # 中间的分隔点
        TextButton(row, "跳过", self.skip).pack(side="left", padx=14)

    def _bind_keys(self):
        """绑定全局快捷键。

        绑在 root 上而不是按钮上，所以窗口处于激活状态时随时可按，不用先点按钮。
        大小写各绑一次（<r> 和 <R>），这样开着大写锁定也照样管用。
        """
        self.root.bind("<space>", lambda _e: self.toggle())
        self.root.bind("<r>", lambda _e: self.reset())
        self.root.bind("<R>", lambda _e: self.reset())
        self.root.bind("<n>", lambda _e: self.skip())
        self.root.bind("<N>", lambda _e: self.skip())

    def _center_on_screen(self):
        """把窗口摆到屏幕上。

        水平居中，垂直方向放在 1/3 处而不是正中间——正中间会显得偏低，
        这是视觉上的常见做法。用的是 //（整除）保证坐标是整数。
        """
        self.root.update_idletasks()          # 强制先算好布局，否则拿到的宽高是 1x1
        w, h = self.root.winfo_width(), self.root.winfo_height()
        x = (self.root.winfo_screenwidth() - w) // 2
        y = (self.root.winfo_screenheight() - h) // 3
        self.root.geometry(f"+{x}+{y}")       # 只给位置不给尺寸，保持 Tk 算好的大小

    # ------------------------------------------------------------------ 计时核心
    def _tick(self):
        """心跳循环：每 TICK_MS 毫秒执行一次。

        这个函数做三件事：① 按真实时间更新剩余秒数 ② 到点了就切换阶段 ③ 刷新界面，
        最后再把自己排进下一次 after，形成循环。

        ┌──────────────────────────────────────────────────────────────────┐
        │ 计时精度的关键：为什么不用 after(1000, 秒数-1) 这种累加写法？    │
        │                                                                  │
        │ tkinter 的 after 只是"尽快在某时刻之后调用"，并不准时。每次都会  │
        │ 有几毫秒到几十毫秒的延迟，而延迟是会**累积**的——跑一小时下来能慢  │
        │ 好几秒。                                                         │
        │                                                                  │
        │ 所以这里改用**绝对截止时刻**：一开始算出"应该在哪个时刻结束"      │
        │ (deadline)，之后每次都拿"现在"去减，得到剩余时间。               │
        │ 单次延迟只会让这一帧晚一点刷新，不会污染后续计算，误差不累积。    │
        └──────────────────────────────────────────────────────────────────┘
        """
        if self.running:
            # max(0.0, ...) 防止出现负数（比如系统休眠后醒来，时间差可能很大）
            self.remaining = max(0.0, self.deadline - time.monotonic())
            if self.remaining <= 0:
                self._complete_phase()      # 时间到，切换阶段
        self._render()                       # 无论是否在跑都刷一次，保证界面总是最新的
        self.root.after(TICK_MS, self._tick) # 排下一次心跳

    def toggle(self):
        """开始 / 暂停。主按钮和空格键都调这个。"""
        if self.running:
            # ---- 暂停 ----
            # 先把最后这段时间的进度算准再停，否则会丢掉最后不到一帧的时间
            self.remaining = max(0.0, self.deadline - time.monotonic())
            self.running = False
        else:
            # ---- 开始 ----
            total = float(PHASES[self.phase]["seconds"])
            if self.remaining <= 0:
                # 如果上一阶段刚好走完停在 0，重新开始就当是重新计这一轮
                self.remaining = total
            # 把"剩余时间"换算成"绝对截止时刻"，之后 _tick 就靠它算剩余
            self.deadline = time.monotonic() + self.remaining
            self.running = True
        self._sync_button()
        self._render()

    def reset(self):
        """重置当前阶段：退回起点并暂停。

        注意是"重置当前阶段"而不是"重置整个番茄钟"——已经完成的番茄数
        (completed_focus) 保持不变，不会把进度清零。
        """
        self.running = False
        self.remaining = float(PHASES[self.phase]["seconds"])
        self._sync_button()
        self._render()

    def skip(self):
        """跳过当前阶段，直接进入下一阶段。

        跳过的专注**不计入番茄数**（没真干活不该算成绩）。
        跳过长休息则要把计数清零，否则下一轮会立刻又进长休息。
        """
        if self.phase == "focus":
            self._set_phase("short_break", autostart=False)
        else:
            if self.phase == "long_break":
                self.completed_focus = 0
            self._set_phase("focus", autostart=False)

    def _set_phase(self, phase: str, autostart: bool):
        """切换阶段的统一入口。所有跳转都必须走这里，保证状态被完整重置。

        集中成一个函数的好处：切阶段时要做的事（重置剩余时间、重置运行状态、
        必要时设置 deadline、刷新按钮、刷新界面）只需要写一份，不会漏。
        """
        self.phase = phase
        self.remaining = float(PHASES[phase]["seconds"])
        self.running = autostart
        if autostart:
            # 自动开始的阶段，一进入就要把 deadline 设好
            self.deadline = time.monotonic() + self.remaining
        self._sync_button()
        self._render()

    def _complete_phase(self):
        """当前阶段倒计时归零时调用 —— 整个程序的核心状态机。

        分两大类情况：
          A. 刚完成的是「专注」→ 番茄数 +1，然后按数量决定去短休还是长休（都自动开始）
          B. 刚完成的是「休息」→ 回到专注（不自动开始），长休结束时把番茄数清零
        """
        finished = self.phase
        if finished == "focus":
            self.completed_focus += 1

            if self.completed_focus >= ROUNDS_BEFORE_LONG_BREAK:
                # 攒够 4 个，奖励一次长休息
                # 休息都设为自动开始：人离开座位时休息空转没损失，体验反而更连贯
                self._set_phase("long_break", autostart=True)
                self._notify("番茄钟 · 专注结束", "连做 4 个了，好好休息 15 分钟 🌿", "SystemExclamation")
            else:
                self._set_phase("short_break", autostart=True)
                self._notify("番茄钟 · 专注结束", "休息 5 分钟，起来走走 ☕", "SystemExclamation")
        else:
            if finished == "long_break":
                # 长休息结束 = 一轮循环结束，番茄计数归零，开始新的一轮
                # （放在这里而不是进入长休息时清零，是为了让长休息期间
                #   底部 4 个圆点保持全亮，用户能看到"这轮我完成了 4 个"）
                self.completed_focus = 0

            # 专注**不**自动开始：用户可能不在座位上，
            # 空转等于白白消耗掉一个番茄，不如等他回来亲手点。
            self._set_phase("focus", autostart=False)
            self._notify("番茄钟 · 休息结束", "开始新的 25 分钟专注 🍅", "SystemAsterisk")

    # ------------------------------------------------------------------ 渲染
    def _sync_button(self):
        """让主按钮的文字跟上当前状态：开始 / 暂停 / 继续。"""
        meta = PHASES[self.phase]
        if self.running:
            text = "暂 停"
        elif self.remaining < meta["seconds"] - 0.05:
            # 剩余时间比满额少 → 说明是中途暂停的，应该显示"继续"而不是"开始"
            # 那个 0.05 的容差是防浮点误差：刚重置完 remaining 可能差几个 1e-13
            text = "继 续"
        else:
            text = "开 始"
        # 按钮颜色也跟着阶段变（专注=主色红 / 休息=文本灰）
        self.btn_primary.set(text=text, fill=meta["color"])

    def _render(self):
        """把当前状态画到界面上。纯读状态、不写状态，可以随便调多少次。

        每次调用都用 itemconfigure / coords 去**改已有图形**，而不是 delete 之后重画。
        这是 tkinter 做动画的标准做法——重建图形会闪，改属性不会。
        """
        meta = PHASES[self.phase]
        total = float(meta["seconds"])
        color = meta["color"]

        # 进度 0.0 ~ 1.0。clamp 一下防止浮点误差导致越界（比如 -1e-16 或 1.0000001）
        progress = min(1.0, max(0.0, 1.0 - self.remaining / total))

        # ---- 剩余时间数字 ----
        # 用 ceil 向上取整：这样一开始显示 25:00，而且只在真正归零时才跳到 00:00。
        # 如果改用 floor，启动瞬间就会显示 24:59，看着像被偷了一秒。
        # 那个 1e-9 是浮点容差：remaining 本来该是 1500.0，但经过 deadline 一减一加
        # 可能变成 1500.0000000001，ceil 一下就成 1501 了（显示 25:01）。
        # 减去一个极小数就能把这种误差压回正常值。
        secs = max(0, math.ceil(self.remaining - 1e-9))
        self.canvas.itemconfigure(self.time_text, text=f"{secs // 60:02d}:{secs % 60:02d}")
        self.canvas.itemconfigure(self.phase_text, text=meta["label"], fill=color)

        # ---- 圆环进度 ----
        if progress <= 0:
            # 还没开始（或刚重置），弧和圆头端点都藏起来
            self.canvas.itemconfigure(self.arc, state="hidden")
            self.canvas.itemconfigure(self.cap, state="hidden")
        else:
            # extent 限制在 0.01~359.99 之间：
            #   * 下界 0.01 是因为 extent=0 在 Tk 里无意义
            #   * 上界 359.99 是因为 360 会画出一整圈，反而和"起点/终点"重合显得别扭
            #     （差那 0.01° 肉眼根本看不出来）
            self.canvas.itemconfigure(self.arc, state="normal", outline=color,
                                      extent=-max(0.01, min(359.99, 360.0 * progress)))

            # 计算弧末端的坐标，用来摆那个"圆头"小圆点。
            #
            # 角度关系：从 12 点钟方向（90°）顺时针走了 360*progress 度
            #          所以末端角度 θ = 90 - 360 * progress（度）
            #
            # 坐标换算：
            #     x = 圆心x + 半径 * cos(θ)
            #     y = 圆心y - 半径 * sin(θ)
            # 注意 y 是**减**：屏幕坐标的 y 轴朝下，而数学上的 sin 基于 y 轴朝上，
            # 所以要把符号反过来。这是画圆时最容易搞错的一处。
            rad = math.radians(90.0 - 360.0 * progress)
            x = self.cx + self.RING_RADIUS * math.cos(rad)
            y = self.cy - self.RING_RADIUS * math.sin(rad)
            rc = self.ring_w / 2                       # 小圆点半径 = 线宽的一半，刚好补齐线头
            self.canvas.coords(self.cap, x - rc, y - rc, x + rc, y + rc)
            self.canvas.itemconfigure(self.cap, state="normal", fill=color)

        # ---- 底部番茄圆点：前 completed_focus 个点亮 ----
        for i, dot in enumerate(self.dots):
            self.dots_canvas.itemconfigure(
                dot, fill=color if i < self.completed_focus else COL_TRACK)

    # ------------------------------------------------------------------ 提醒
    def _notify(self, title: str, body: str, sound: str):
        """阶段结束时的三重提醒：声音 + 系统通知 + 窗口置顶。

        三重是为了容错——任何一路单独失效，剩下两路仍然能提醒到你：
          声音   → 最直接，但静音或戴耳机时可能听不到
          通知   → 最正式，但可能被"专注助手"屏蔽，或系统策略挡掉
          置顶   → 一定能看到（只要你在电脑前），但不打扰别人

        声音和通知都丢到**后台线程**执行：
          * 声音用 SND_ASYNC 其实已经异步了，放线程里是双保险
          * 通知要等 PowerShell 启动，实测约 2.5 秒，塞在主线程会卡住界面
          daemon=True 表示这些线程不阻止程序退出——否则关窗口时可能被卡住。
        """
        threading.Thread(target=play_sound, args=(sound,), daemon=True).start()
        threading.Thread(target=show_toast, args=(title, body), daemon=True).start()

        # 兜底：无论通知成不成功，都把窗口提到最前
        r = self.root
        r.deiconify()                          # 万一窗口是最小化状态，先还原
        r.lift()                               # 提到同层级窗口的最上面
        r.attributes("-topmost", True)         # 强制置顶
        # 1.5 秒后取消置顶。必须取消，否则窗口会一直压在所有程序上面，非常烦人。
        r.after(1500, lambda: r.attributes("-topmost", False))


# ============================================================================
# 自检入口
# ============================================================================
def selftest() -> int:
    """自检：运行 `番茄钟.exe --selftest` 跑一次通知 + 提示音，把结果写进临时文件。

    为什么需要它？因为 exe 是 --windowed 打包的，**没有控制台**。
    这种情况下 print 什么都看不到，一旦出错程序就静默消失，根本无从排查。
    所以把自检结果写成文件，再去看文件内容，这是打包后的标准调试手段。

    结果文件位置：%TEMP%\\pomodoro_tk\\selftest.txt
    """
    folder = os.path.join(tempfile.gettempdir(), "pomodoro_tk")
    os.makedirs(folder, exist_ok=True)
    lines = [
        # frozen=True 说明是在打包后的 exe 里跑，False 说明是源码直接跑
        f"frozen={getattr(sys, 'frozen', False)}",
        f"executable={sys.executable}",
        f"python={sys.version.split()[0]}",
    ]

    # 通知是打包后最容易失效的一环（子进程调用 + 编码 + 策略，任何一处都可能出问题），
    # 所以单独记录成功还是失败，失败时带上具体原因。
    error = show_toast("番茄钟 · 自检", "打包后的通知功能正常 🍅")
    lines.append("toast=" + ("OK" if error is None else f"FAILED ({error})"))

    try:
        import winsound
        winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC)
        lines.append("sound=OK")
    except Exception as exc:
        lines.append(f"sound=FAILED ({exc})")

    with open(os.path.join(folder, "selftest.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return 0


def main():
    # 命令行带 --selftest 就跑自检然后退出，不打开界面
    if "--selftest" in sys.argv:
        sys.exit(selftest())

    # 标准 tkinter 启动流程：建根窗口 → 建应用 → 进入事件循环
    # mainloop 会一直阻塞在这里，处理鼠标/键盘/定时器事件，直到窗口被关闭
    root = tk.Tk()
    PomodoroApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
