# 桌面番茄钟

一个纯 Python 标准库写的番茄钟：圆环倒计时 + 系统提示音 + Windows 原生通知。
不依赖任何第三方包（打包时需要 PyInstaller，运行时不需要）。

## 直接使用（不用装 Python）

到 **[Releases 页面](https://github.com/Gentle121/first-cc/releases/latest)** 下载，双击即可：

| 文件 | 体积 | 启动速度 | 适合 |
|---|---|---|---|
| `pomodoro-timer.exe` | 11 MB 单文件 | 约 2.4 秒 | 发给别人 / 丢 U 盘，就一个文件 |
| `pomodoro-timer-faststart.zip` | 11 MB 压缩包 | 约 0.25 秒 | 自己常用，放桌面/任务栏 |

两者功能完全一样，差别只在于 `--onefile` 每次启动都要把内容解压到临时目录，所以慢。
压缩包解压出来是 `pomodoro-timer-faststart/` 文件夹，**要整个目录一起留着**，单独把里面的
exe 拿出来是不能用的。

首次运行若被 Windows SmartScreen 拦下，点「更多信息」→「仍要运行」——exe 没有代码签名
证书，不是文件有问题。

> 附件名是英文的，因为 GitHub 会剥掉附件名里的非 ASCII 字符（中文名会被吞成
> `default.exe`）。所以 Release 里的东西由 CI 用英文名重新打包（见下面「发版」），
> 仓库里自己双击 `打包.bat` 出的产物仍然叫 `番茄钟.exe`。
>
> `dist/` 里是可随时重新生成的构建产物，**没有入库**，所以克隆下来是没有 exe 的——
> 要 exe 去 Releases 下，要从源码跑看下一节。

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

产物在 `dist/`（已被 `.gitignore` 排除，不会进仓库）。

## 发版

想让别人拿到新 exe，不用手动打包上传——打个 tag 推上去就行：

```bash
git tag v1.1.0
git push origin v1.1.0
```

`.github/workflows/release.yml` 会在 windows runner 上跑逻辑测试、重新打包两份、
建好 Release 并把 `pomodoro-timer.exe` 和 `pomodoro-timer-faststart.zip` 传上去，
说明文字由 GitHub 按提交记录自动生成。命名规则见上面那段：CI 里一律用英文名，
省掉手动改名这一步。

跑挂了在 Actions 页面点 **Re-run jobs** 重跑即可，ref 还是原来那个标签，不会发错版本；
若 Release 已经存在，重跑是覆盖同名附件而不是报错。

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
