# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目性质

零依赖的 Windows 桌面番茄钟：**运行时只用 Python 标准库**（tkinter / winsound / subprocess 调
PowerShell），PyInstaller 只在打包时用到。"不依赖任何第三方包"是 README 里写明的卖点，所以不要为
了省事引入运行时依赖——连弹原生通知这种事都是靠 PowerShell 绕过去的，而不是装 win10toast。

## 常用命令

| 目的 | 命令 |
|---|---|
| 运行 | `python pomodoro.py` |
| 测试 | `python test_pomodoro.py` |
| 打包（出两份产物） | 双击 `打包.bat`，或见下面两条 |
| 打包后自检通知 | `"dist/番茄钟.exe" --selftest`，结果看 `%TEMP%\pomodoro_tk\selftest.txt` |
| 重生成圆环打底图 | `python tools/make_ring_bg.py`（开发期工具，自己要 Pillow） |
| 预览发布说明 | `python tools/make_release_notes.py --to v1.1.0`（不加 `--to` 就是「上一个标签→HEAD」） |
| 发版 | `git tag v1.1.0 && git push origin v1.1.0` |

```bash
# 打包：本地出中文名（CI 里出英文名，原因见「硬约束」）
# --add-data 不能省：圆环打底图 assets/ring_bg.png 要随 exe 一起走，漏了程序会自动退化成纯色背景
python -m PyInstaller --add-data "assets;assets" --noconfirm --onefile --windowed --name 番茄钟 pomodoro.py
python -m PyInstaller --add-data "assets;assets" --noconfirm --onedir  --windowed --name 番茄钟-快速启动 pomodoro.py
```

`test_pomodoro.py` 是**纯 assert 脚本，不是 pytest**，没有筛选单条测试的机制。
它最后会建真的 Tk 窗口，所以要有能起 GUI 的会话（GitHub 的 windows runner 上可以）。
临时验证一小段逻辑时用 `python - <<'PY'` 写 heredoc 片段，别用 `python -c`：
这台机器的 MSYS 会把中文实参传成乱码，找中文关键字会全部 miss。

`启动番茄钟.bat` 是从源码启动的入口（优先用 `pythonw`，不起黑色控制台窗口）。

## 架构：单文件四层

`pomodoro.py` 本身就是全部产品代码，自上而下分四层，改动时按层定位：

1. **配置层** — 时长常量、`PHASES` 表、所有颜色常量，外加 `_resource_path()`（定位随程序分发的
   资源：打包后根目录是 `sys._MEIPASS`，源码运行是文件所在目录）。改时长/轮数/配色只动这一块。
   `PHASES` 是"阶段 → 标签/颜色/秒数"的查表字典，逻辑层靠查表避免 `if/else` 分支；
   加新阶段（比如"超长休息"）就是加一行 + 改跳转逻辑。
2. **控件层** — `PillButton`（Canvas 自绘胶囊：`tk.Button` 做不出圆角，Windows 上还会强套系统主题
   边框。描边是**两层胶囊叠出来**的：外层三块画描边色、内层三块四周内缩 2px 画底板色；
   给三块各自加 `outline=` 会在半圆与矩形的接缝处画出内部竖线）、`TextButton`（用 Label 当按钮，
   为了背景色能真正隐形；悬停是**加下划线**——色卡的浅色底板对背景只有 1.09~1.34:1，
   且板上 11pt 小字最高才 4.69:1，没有一个色能同时满足"底板看得见"和"字达标"）。
   字体族由 `ui_font()` 运行时解析（`tkfont.families()` 需要已存在的 Tk root，所以**不能**写进
   函数默认参数——那会在导入期求值）。
3. **通知层** — `_TOAST_PS` / `show_toast` / `play_sound`，与 Windows 系统打交道的全部代码。
4. **主程序** — `PomodoroApp`：状态机（计时）+ `_render`（渲染）。

### 必须守住的两条不变式

**① 状态与显示分离。** 状态只有 `phase / completed_focus / remaining / running`
（外加仅 `running=True` 时有意义的 `deadline`）。`_render()` 是纯函数：只读状态画界面，
不写任何状态。任何状态变更之后都要调一次 `_render()`。**切阶段的唯一入口是 `_set_phase()`**
（重置剩余时间、运行状态、必要时设 deadline、刷新按钮和界面）——绕过它直接改 `self.phase`
一定会漏掉某一步。

**② 计时用绝对截止时刻。** `deadline = time.monotonic() + remaining`，每 tick 用
`deadline - time.monotonic()` 重算剩余。**不要**改回 `after(1000, 秒数-1)` 的累加写法：
`after` 的延迟会累积，跑一小时能慢好几秒。配套地，暂停（`toggle`）时必须先按 deadline
把 `remaining` 结算一次，否则会丢掉最后不到一帧的进度。

### 阶段流转

专注结束 → 番茄数 +1 → 攒够 `ROUNDS_BEFORE_LONG_BREAK`(4) 个进长休息，否则短休息，**两者都自动开始**；
休息结束 → 回专注，**不自动开始**（人可能不在座位上，专注空转等于白扣一个番茄）。
`completed_focus` 是在**长休息结束时**才归零，而不是进入长休息时——这样长休息期间底部 4 段进度
保持全亮，用户能看到"这轮我做了 4 个"。`skip()` 不算成绩：跳过专注不计番茄数，跳过长休息要清零计数。

### 渲染层的"看着奇怪但都是故意的"

- 时间用 `ceil(remaining - 1e-9)`：ceil 保证刚启动显示 `25:00` 而不是 `24:59`；那个 1e-9 是压掉
  deadline 一加一减引入的浮点误差，否则会显示 `25:01`。
- 圆环 = 底槽 oval + 进度 arc + 一个跟着弧末端跑的"圆头"小圆点（Tk 的 arc 只能画平头线）。
  端点坐标 `y = cy - r*sin(θ)` 是**减**，因为屏幕 y 轴朝下。
- arc 的 `extent` 夹在 0.01~359.99：0 在 Tk 里无意义，360 会画满一圈反而别扭。
- 动画一律用 `itemconfigure` / `coords` 改已有图形，**不要** delete 后重画（会闪）。
- 底部 4 段番茄进度是 `create_line(..., width=8, capstyle="round")`——圆头线帽本身就是胶囊形，
  比 `create_oval` 圆点面积大得多，而弱化色对背景只有 1.34:1，只能靠面积补可见性。
  圆帽两端各外扩 `width/2`，所以"线长 16"画出来是 24；`line` 和 `oval` 共享 `-fill`，
  `_render` 里的 `itemconfigure(id, fill=...)` 对两者通用。
- 主色 `#C65A4A` **只给进度弧**（外加已点亮的番茄段和主按钮悬停描边）。阶段名用「文本」色，
  因为 13pt 下主色对背景只有 3.80:1、不达 AA。详见记忆 `pomodoro-color-card`。
- 圆环底下垫了一张 `assets/ring_bg.png`（浅色柔光，给界面一点纵深）。它在 `_build_ui` 里必须
  **第一个** `create_image`——Canvas 里先创建的在下层，晚一步就被圆环盖住。`PhotoImage` 要由
  `self._ring_bg` **自持有引用**，否则被 GC 回收后图会当着用户的面消失；读图失败静默退回纯色
  `COL_BG`，一张背景图绝不能拖垮计时。

### 通知层为什么绕这么一大圈

Python 标准库没有任何弹 WinRT 原生通知的办法（第三方包违背零依赖，ctypes 写 COM 太繁琐，
自画窗口又不进通知中心）。最终让 PowerShell 5.1 代劳，几个不能动的点：

- `.ps1` 写到 `%TEMP%\pomodoro_tk\`，不写程序目录——打包后程序可能在只读位置。
- 标题/正文走 **base64**：PowerShell 5.1 按系统 ANSI 代码页解析命令行参数，中文直传会乱码。
- **故意不加** `-ExecutionPolicy Bypass`：这是自己刚写的本地文件、没有"来自网络"标记，
  默认 `RemoteSigned` 本来就放行，没必要覆盖系统安全策略。
- `stdout/stderr` 显式指向 `DEVNULL`：`--windowed` 的 exe 没有有效 stdio 句柄，
  子进程继承时会抛异常，导致通知静默失效。
- 通知横幅顶部显示 "Windows PowerShell" 是借用了它的 AppUserModelID，换成"番茄钟"需要
  在开始菜单注册带 AppUserModelID 的快捷方式（约 60 行 COM 代码），目前不做。
- 所有失败都静默降级：声音、通知、窗口置顶三路互相兜底，通知弹不出来不影响计时。

## 本仓库的硬约束

- **配色只许用色卡上的 8 个色**（"色卡"= 仓库根目录的 `配色方案.png`，色值抄在配置层的注释里），
  不做提亮 / 混色 / 透明度派生。色卡是**单强调色板**（只有一个彩色 `#C65A4A`），
  所以休息阶段用中性灰而不是绿/蓝——"红 = 正在专注 / 灰 = 休息中"是有意设计，
  短休和长休同色、靠阶段名和时长区分。
  **目前唯一一处偏离**是圆环打底图 `assets/ring_bg.png`：它天生是一张柔光渐变（四边淡出到
  `COL_BG`），不存在"用 8 个色里的哪一个"的答案。它是**离线预生成**的——改色卡或调光晕浓淡要
  重跑 `python tools/make_ring_bg.py`（这个脚本自己用 Pillow，**只在开发时**；`pomodoro.py`
  运行时仍然只用标准库）。代码里除了这张图，不要新增任何渐变 / 半透明 / 调亮调暗的派生色。
- **`.bat` 必须是 GBK + CRLF**（`.gitattributes` 已锁 `*.bat text eol=crlf`）。写成 UTF-8/LF
  或加 `chcp 65001` 会让 cmd 解析错位、静默失效——`打包.bat` 曾经因此从没真正执行过 PyInstaller。
- **Release 附件名必须是 ASCII**：GitHub 会把非 ASCII 附件名静默剥成 `default.exe`。
  所以 CI 里一律用英文名打包，本地 `打包.bat` 仍然出 `番茄钟.exe`。
- **打包产物是 `--windowed`、没有控制台**：`print` 什么都看不见，出错就是静默消失。
  要给打包后的程序加诊断信息，就加进 `selftest()`（写文件），别指望 stdout。
- `*.spec` 是 PyInstaller 跑出来的副产物：`打包.bat` 走命令行参数、每次都会重新生成它们，
  手改 `.spec` 不影响 `打包.bat` 的产物。

## CI / 发版

`.github/workflows/release.yml` 在推 `v*` 标签时触发：windows runner 上跑测试 → 用英文名打包两份
→ 用 `--selftest` 冒烟确认 exe 真能启动 → 压 zip → `gh release create`（Release 已存在则
`gh release upload --clobber`，所以 Re-run jobs 是覆盖附件而不是报错）。已经踩过的点：

- **job 级 `PYTHONUTF8: "1"` 不能删**：runner 控制台是 cp1252，测试脚本 `print` 中文会
  `UnicodeEncodeError` 把整步直接跑挂（run 37271669623 就是这么挂的）。
- **Release 正文由 `tools/make_release_notes.py` 生成**：逐条列出两个标签之间的提交，每条附
  「查看改动」链接指向那次提交的 diff。**不要**换回 `gh release create --generate-notes`——
  它只会在正文里留一行 `**Full Changelog**: v1.0.0...v1.1.0`，点进去是一大坨混合 diff，
  读者看不出这个版本到底改了什么。
- 配套地 **`actions/checkout` 必须 `fetch-depth: 0`**：默认只抓 1 个提交、且不抓标签，
  而生成正文要靠 `git describe` 找上一个 `v*` 标签、靠 `git log` 取两个标签之间的提交，
  浅检出会让它直接落空。
- 需要 `permissions: contents: write`；改 `.github/workflows/` 下的文件要求推送用的 token
  带 `workflow` scope，否则 push 会被拒。
- 改了 workflow 只能推个 tag 看真跑，本地没有等价的复现环境。
