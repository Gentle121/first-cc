# -*- coding: utf-8 -*-
"""生成 Release 正文：把两个标签之间的提交整理成「功能点 + 可点链接」的清单。

开发/CI 工具，运行时用不到本脚本，产品仍然零依赖（这里也只用标准库，靠 subprocess 调 git）。

为什么不用 `gh release create --generate-notes`：它只会在正文里留一行
`**Full Changelog**: v1.0.0...v1.1.0`，点进去是一大坨混合 diff，读者得自己在里面找
「这个版本到底改了什么」。这里改成逐条列出，每条都能点进去看那一次提交的具体改动。

用法：
    python tools/make_release_notes.py                      # 上一个标签 -> HEAD
    python tools/make_release_notes.py --to v1.2.0          # 上一个标签 -> v1.2.0
    python tools/make_release_notes.py --from v1.0.0 --to v1.1.0
    python tools/make_release_notes.py --to v1.1.0 -o notes.md
"""
import argparse
import re
import subprocess
import sys

# 提交里人不用看的尾注（Claude Code 等工具加的署名行）
TRAILER = re.compile(r"^(Co-Authored-By|Signed-off-by|Reviewed-by|Acked-by):", re.I)

# 中日韩文字与全角标点。拼回折行时，前一字符是这些就不该插空格（"，" 后面加空格很别扭）
CJK = re.compile(r"[　-鿿＀-￯]")


def git(*args: str) -> str:
    out = subprocess.run(["git", *args], capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    if out.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败：{out.stderr.strip()}")
    return out.stdout


def repo_url() -> str:
    """把 origin 的 remote 地址归一成 https://github.com/OWNER/REPO（去掉 .git）。

    CI 里的 origin 有可能是 `https://x-access-token:<token>@github.com/...` 这种带凭据的
    形式，必须把 userinfo 剥掉——否则 token 会被原样写进公开的 Release 正文里。
    """
    url = git("remote", "get-url", "origin").strip()
    if url.startswith("git@github.com:"):
        url = "https://github.com/" + url[len("git@github.com:"):]
    url = re.sub(r"^([a-z]+://)[^@/]*@", r"\1", url)     # 去掉 user:pass@
    return re.sub(r"\.git$", "", url).rstrip("/")


def previous_tag(ref: str):
    """ref 之前最近的一个 v* 标签；没有就返回 None（首个版本）。

    故意不写 `-> str | None`：那个写法在 3.10 以下会当场报错，而本项目的 Python 版本
    下限由打包环境决定，没必要为了一行注解把门槛抬上去。
    """
    out = subprocess.run(["git", "describe", "--tags", "--abbrev=0", "--match", "v*",
                          f"{ref}^"], capture_output=True, text=True)
    return out.stdout.strip() or None if out.returncode == 0 else None


def commit_bullets(body: str) -> list[str]:
    """把提交正文整理成二级要点。

    手动 -m 写的中文正文是把长句折了行的，所以「不以列表符号开头的行」要并回上一条，
    否则会碎成一堆断句的段落。
    """
    bullets: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or TRAILER.match(line):
            continue
        if line.startswith(("- ", "* ")):
            bullets.append("- " + line[2:].strip())
        elif bullets:
            sep = "" if CJK.search(bullets[-1][-1:]) else " "
            bullets[-1] += sep + line
    return bullets


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 Release 正文")
    ap.add_argument("--from", dest="frm", help="起始标签（默认自动取上一个 v* 标签）")
    ap.add_argument("--to", dest="to", default="HEAD", help="结束版本（默认 HEAD）")
    ap.add_argument("-o", "--output",
                    help="写到这个文件（UTF-8、无 BOM）；不给就打到标准输出。"
                         "CI 里用它——让 Python 自己写文件，绕开 shell 重定向的编码坑")
    args = ap.parse_args()

    to = args.to
    frm = args.frm if args.frm else previous_tag(to)
    rng = f"{frm}..{to}" if frm else to
    base = repo_url()

    # %x1f 分隔字段、%x1e 分隔记录，避免正文里的换行把解析搞乱
    raw = git("log", "--no-merges", "--reverse",
              f"--pretty=format:%H%x1f%s%x1f%b%x1e", rng)
    records = [r for r in raw.split("\x1e") if r.strip()]

    lines = ["## 本次更新", ""]
    if not records:
        lines.append("（这个范围内没有提交）")
    for rec in records:
        parts = rec.split("\x1f")
        sha, subject = parts[0].strip(), parts[1].strip()
        body = parts[2] if len(parts) > 2 else ""
        lines.append(f"- **{subject}** · [查看改动 `{sha[:7]}`]({base}/commit/{sha})")
        for b in commit_bullets(body):
            lines.append("  " + b)      # 缩进一级，挂在该条提交下面
    lines.append("")

    if frm:
        lines.append(f"**完整对比**: [{frm}...{to}]({base}/compare/{frm}...{to})")
    else:
        lines.append(f"**完整对比**: [{to} 的全部提交]({base}/commits/{to})")

    text = "\n".join(lines)
    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text + "\n")
    else:
        sys.stdout.reconfigure(encoding="utf-8")   # 控制台默认 GBK，中文直接 print 会炸
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
