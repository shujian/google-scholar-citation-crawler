"""`papers/` 论文目录树的**使用锁** —— 谁在用这棵树，谁把它写下来。

阶段 1 会按最新排名**改论文目录名**，而阶段 2 拼路径时只认输入快照里的
`pub.no` + 标题（`paper_dir_for()`），写完还会 `os.makedirs(..., exist_ok=True)`。两者同时跑
就会出现"不受控的目录改变"：阶段 1 把 `083-旧标题` 改成 `083-新标题`（或换号），阶段 2 又把
**旧名字重新建出来**，PDF / `download_progress.json` / `citations_meta.json` 被拆到两套目录里。

所以这里用一个 lock 文件把这件事摆到明处（**提醒，不是强制**）：

```
<papers 根>/.stage2.lock
{"stage": "stage2", "pid": 12345, "host": "mac.local", "started_at": "<ISO timestamp>"}
```

* 阶段 2 在**通过启动校验之后** `acquire()`，正常结束 / `sys.exit` / 中断都 `release()`
  （调用方再加一道 `atexit` 兜底）；
* 阶段 1 在目录维护 `apply` 之前 `read()` + `is_live()`：还活着就**跳过本轮的改名/归档**，
  只打印计划并提示"阶段 2 结束后重跑阶段 1"，想强行改就显式 `--renumber-papers force`；
* 陈旧锁（进程已不在、或跨主机且文件太旧）不挡路，会被直接覆盖。

**只用标准库**：本文件是跨仓库共享件（阶段 1 宿主、阶段 2 经 `code/_shared.py` 消费），
不能 import 任何 `code.*` —— 阶段 2 里 `code` 指的是它自己的包，会拿到错的模块。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import socket
import sys

#: 锁文件名（固定放在 papers 根下，两阶段都经 `papers` 符号链接看到同一个文件）
LOCK_FILENAME = ".stage2.lock"

#: 跨主机（SSHFS）看不到 pid 时的陈旧判据：文件比这还旧就当作死锁
STALE_AFTER_HOURS = 24.0


def lock_path(papers_root):
    """`<papers 根>/.stage2.lock`。"""
    return os.path.join(papers_root, LOCK_FILENAME)


def read(papers_root):
    """读锁；没有或读不懂都返回 ``None``（读不懂的锁不挡路）。"""
    try:
        with open(lock_path(papers_root), encoding="utf-8") as handle:
            info = json.load(handle)
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) else None


def _pid_alive(pid):
    """同主机上 pid 是否还在（`PermissionError` 说明进程活着，只是不归我们管）。"""
    try:
        os.kill(int(pid), 0)
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return False
    return True


def is_live(info, papers_root=None, now=None):
    """这条锁是否还算"有人正在用"。纯判断，不改文件。

    * 同主机（`info['host']` == 本机名）：看 pid 还在不在；
    * 跨主机：看不到 pid，退回**文件年龄**（`STALE_AFTER_HOURS`）；
    * 没有文件可用于看年龄时，按"活着"处理（宁可少改名，也不拆数据）。
    """
    if not isinstance(info, dict) or not info:
        return False
    host = info.get("host")
    if host == socket.gethostname():
        pid = info.get("pid")
        return _pid_alive(pid) if pid is not None else False
    if papers_root:
        try:
            age = (now or _dt.datetime.now()).timestamp() - os.path.getmtime(
                lock_path(papers_root))
            return age < STALE_AFTER_HOURS * 3600.0
        except OSError:
            return False
    return True


def describe(info):
    """给人看的一行描述（放进日志/提示里）。"""
    if not isinstance(info, dict) or not info:
        return "unknown"
    parts = [str(info.get("stage") or "?")]
    if info.get("host"):
        parts.append("host=%s" % info["host"])
    if info.get("pid") is not None:
        parts.append("pid=%s" % info["pid"])
    if info.get("started_at"):
        parts.append("since %s" % info["started_at"])
    return ", ".join(parts)


def acquire(papers_root, stage="stage2", extra=None):
    """写锁（覆盖旧的，含陈旧的）。返回写入的字典；失败抛 OSError（调用方决定是否忽略）。"""
    info = {
        "stage": stage,
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "started_at": _dt.datetime.now().isoformat(timespec="seconds"),
    }
    if extra:
        info.update(extra)
    os.makedirs(papers_root, exist_ok=True)
    target = lock_path(papers_root)
    tmp = target + ".tmp-%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(info, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, target)
    return info


def release(papers_root):
    """删锁（幂等；不是自己写的也删 —— 这个文件只是提示，不承载数据）。"""
    try:
        os.remove(lock_path(papers_root))
        return True
    except OSError:
        return False


def is_this_process(info):
    """这条锁是不是本进程之前写的（用于"退出时只删自己的"这种保守用法）。"""
    return (isinstance(info, dict)
            and info.get("host") == socket.gethostname()
            and info.get("pid") == os.getpid())


if __name__ == "__main__":   # pragma: no cover - 手工排查用
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    current = read(root)
    print(json.dumps(current, ensure_ascii=False, indent=2)
          if current else "(no lock)")
    print("live:", is_live(current, root))
