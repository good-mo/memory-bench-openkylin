"""沙箱抽象：评测期间智能体工作区可在独立环境快照/回滚。"""

import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import List


@dataclass
class Snapshot:
    """快照句柄（抽象基类）。"""

    def restore(self) -> None:  # pragma: no cover - 由子类实现
        raise NotImplementedError


@dataclass
class FileSnapshot(Snapshot):
    """文件系统快照：记录快照目录路径。"""

    path: str

    def restore(self) -> None:
        """由 FSSandbox 使用，等价于回到快照目录（本句柄只携带路径信息）。"""
        if not os.path.isdir(self.path):
            raise FileNotFoundError("快照目录不存在：{}".format(self.path))


class Sandbox:
    """沙箱抽象基类：提供工作区快照与回滚能力。"""

    def snapshot(self) -> Snapshot:
        raise NotImplementedError

    def restore(self, snapshot: Snapshot) -> None:
        raise NotImplementedError


class FSSandbox(Sandbox):
    """基于文件系统的沙箱：把 root 目录整体复制到临时目录作为快照。

    用法：
        box = FSSandbox("/path/to/workspace")
        snap = box.snapshot()   # 复制 root 到临时目录
        ...智能体执行产生副作用...
        box.restore(snap)       # 删除 root 现有内容，从快照复制回来
    """

    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)

    def snapshot(self) -> FileSnapshot:
        """把 root 目录完整复制到临时目录，返回 FileSnapshot。"""
        snap_dir = tempfile.mkdtemp(prefix="mb_sandbox_snap_")
        snap_root = os.path.join(snap_dir, "workspace")
        shutil.copytree(self.root, snap_root)
        return FileSnapshot(path=snap_root)

    def restore(self, snapshot: Snapshot) -> None:
        """删除 root 现有内容后，从快照目录复制回来。"""
        if not isinstance(snapshot, FileSnapshot):
            raise TypeError("FSSandbox.restore 需要 FileSnapshot")
        # 1. 清空 root 现有内容
        for name in os.listdir(self.root):
            full = os.path.join(self.root, name)
            if os.path.isdir(full) and not os.path.islink(full):
                shutil.rmtree(full)
            else:
                os.remove(full)
        # 2. 从快照复制回来
        shutil.copytree(snapshot.path, self.root, dirs_exist_ok=True)

    def snapshot_path(self, snapshot: Snapshot) -> str:
        return snapshot.path


def list_files(root: str) -> List[str]:
    """返回 root 下全部文件的相对路径列表（相对 root），按名称排序。"""
    root = os.path.abspath(root)
    found: List[str] = []
    if not os.path.isdir(root):
        return found
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fname in sorted(filenames):
            full = os.path.join(dirpath, fname)
            found.append(os.path.relpath(full, root))
    return sorted(found)