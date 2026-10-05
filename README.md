# 桌面番茄钟

一个纯 Python 标准库写的番茄钟：圆环倒计时 + 系统提示音 + Windows 原生通知。
不依赖任何第三方包（打包时需要 PyInstaller，运行时不需要）。

## 直接使用

已经打包好的 exe 在 `dist/` 里，**不需要装 Python**，双击即可：

| 版本 | 体积 | 启动速度 | 适合 |
|---|---|---|---|
| `dist/番茄钟.exe` | 11 MB 单文件 | 约 2.4 秒 | 发给别人 / 丢 U 盘，就一个文件 |
| `dist/番茄钟-快速启动/` | 28 MB 文件夹 | 约 0.25 秒 | 自己常用，放桌面/任务栏 |

两者功能完全一样，差别只在于 `--onefile` 每次启动都要把内容解压到临时目录，所以慢。
**文件夹版要整个目录一起拷贝**，单独把里面的 exe 拿出来是不能用的。

## 从源码运行

```bash
python pomodoro.py
```

## 操作

- `空格` 开始 / 暂停，`R` 重置，`N` 跳过
- 专注结束 → 休息**自动开始**
- 休息结束 → 专注**等你手动点**（否则你离开座位时会被白白扣掉一个番茄）

默认 25 分钟专注 / 5 分钟短休息 / 15 分钟长休息，每 4 个番茄进一次长休息。
改时长就编辑 `pomodoro.py` 顶部的常量，然后重新打包。

## 自检

打包后如果通知没弹出来，运行这个看原因（结果写在 `%TEMP%\pomodoro_tk\selftest.txt`）：

```bash
"dist/番茄钟.exe" --selftest
```

因为 exe 是 `--windowed` 打包的、没有控制台，报错时程序会静默失败，只能靠写文件排查。

## 重新打包

双击 `打包.bat`，或：

```bash
pip install pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name 番茄钟 pomodoro.py
python -m PyInstaller --noconfirm --onedir  --windowed --name 番茄钟-快速启动 pomodoro.py
```

## 测试

```bash
python test_pomodoro.py
```

覆盖状态机流转（4 个番茄后进长休息、计数归零、休息结束回到专注）、重置/跳过、
时间格式边界，约 20 条断言。

## 实现上的两个注意点

**计时用绝对时间戳。** `self.deadline = time.monotonic() + remaining`，每个 tick 重算
剩余时间，而不是 `after(1000, ...)` 反复累加。后者在长时间运行下会明显偏慢。

**通知用 PowerShell 调 WinRT Toast，且不加 `-ExecutionPolicy Bypass`。**
脚本是我们自己写到 `%TEMP%` 的本地文件、没有"来自网络"标记，默认的 `RemoteSigned`
策略本来就放行，没必要去覆盖安全策略。所有失败都静默降级——提示音和窗口自动置顶
始终有效，通知弹不出来不影响使用。
