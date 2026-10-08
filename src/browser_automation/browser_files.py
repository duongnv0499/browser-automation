"""POSIX-only file transfer confinement, anchored to retained directory FDs."""
from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_UPLOAD_BYTES = 128 * 1024 * 1024
MAX_UPLOAD_FILES = 32


class FilePolicyError(RuntimeError):
    code = 'unsafe_action'


def _directory_flags() -> int:
    if os.name != 'posix' or not hasattr(os,'O_NOFOLLOW') or os.open not in os.supports_dir_fd:
        raise FilePolicyError('Secure file transfer requires POSIX directory descriptors and O_NOFOLLOW; this platform is unsupported')
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os,'O_CLOEXEC',0)


class ScopedFiles:
    """Pin the originally approved directory inode, never follow symlink entries."""

    def __init__(self, root: str | Path):
        flags = _directory_flags()
        self.root = Path(os.path.abspath(Path(root).expanduser()))
        descriptor = os.open('/',flags)
        try:
            for part in self.root.parts[1:]:
                next_descriptor = os.open(part,flags,dir_fd=descriptor)
                os.close(descriptor)
                descriptor = next_descriptor
        except BaseException:
            os.close(descriptor)
            raise
        self.descriptor = descriptor

    def __enter__(self):
        return self

    def __exit__(self,*_):
        os.close(self.descriptor)

    def parent(self,path: str | Path) -> tuple[int,str,Path]:
        absolute = Path(os.path.abspath(Path(path).expanduser()))
        try:
            relative = absolute.relative_to(self.root)
        except ValueError:
            raise FilePolicyError('File path is outside the approved directory') from None
        if not relative.parts:
            raise FilePolicyError('File path must name a file, not the approved directory')
        descriptor = os.dup(self.descriptor)
        try:
            for part in relative.parts[:-1]:
                next_descriptor = os.open(part,_directory_flags(),dir_fd=descriptor)
                os.close(descriptor)
                descriptor = next_descriptor
            return descriptor,relative.name,absolute
        except BaseException:
            os.close(descriptor)
            raise

    def read(self,path: str | Path,limit: int = MAX_FILE_BYTES) -> bytes:
        parent,name,_ = self.parent(path)
        descriptor = None
        try:
            descriptor = os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|getattr(os,'O_CLOEXEC',0),dir_fd=parent)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise FilePolicyError('Upload requires a regular file')
            if info.st_size > limit:
                raise FilePolicyError(f'Upload file exceeds the {limit}-byte remaining limit')
            with os.fdopen(descriptor,'rb') as stream:
                descriptor = None
                data = stream.read(limit+1)
            if len(data)>limit:
                raise FilePolicyError('Upload file grew beyond its size limit')
            return data
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(parent)

    def destination(self,path: str | Path) -> PinnedDestination:
        parent,name,absolute = self.parent(path)
        return PinnedDestination(parent,name,absolute)


class PinnedDestination:
    def __init__(self,parent: int,name: str,path: Path):
        self.parent,self.name,self.path = parent,name,path

    def __enter__(self):
        return self

    def __exit__(self,*_):
        os.close(self.parent)

    def save_from(self,source: str | Path) -> int:
        # Keep the descriptor open throughout copying. A pathname replacement
        # cannot redirect the bytes or overwrite an unrelated existing inode.
        descriptor = os.open(self.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|getattr(os,'O_CLOEXEC',0),0o600,dir_fd=self.parent)
        with os.fdopen(descriptor,'wb') as output, open(source,'rb') as input_stream:
            shutil.copyfileobj(input_stream,output,length=1024*1024)
            return output.tell()
