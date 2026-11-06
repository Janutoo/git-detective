"""Read small text files stored inside an analyzed repository.

Files such as ``.mailmap`` come from the repository, so they must be treated as
untrusted: a symlink could point at a private file whose contents would end up
in a report, and a FIFO or device could block or never stop producing data.

All checks run on the opened file descriptor, so the file cannot be swapped
between checking and reading it.
"""

import errno
import os
import stat
from pathlib import Path

MAX_REPO_FILE_BYTES = 1024 * 1024

# O_NOFOLLOW refuses symlinks; O_NONBLOCK keeps open() from waiting on a FIFO.
# Neither exists on Windows, where symlinks need elevated rights to create.
_OPEN_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)


def read_repo_text(path: Path, *, max_bytes: int = MAX_REPO_FILE_BYTES) -> str:
    """Return the UTF-8 text of a regular file that is at most ``max_bytes`` long.

    Raises:
        FileNotFoundError: If the file does not exist.
        OSError: If it is a symlink, is not a regular file, is too large or
            cannot be read.
        UnicodeDecodeError: If it is not valid UTF-8.
    """
    descriptor = os.open(path, _OPEN_FLAGS)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise OSError(errno.EINVAL, "Not a regular file", str(path))
        if info.st_size > max_bytes:
            raise OSError(errno.EFBIG, "File is too large", str(path))
        # One read is enough for a regular file; asking for one extra byte
        # detects a file that grew after fstat().
        data = os.read(descriptor, max_bytes + 1)
    finally:
        os.close(descriptor)
    if len(data) > max_bytes:
        raise OSError(errno.EFBIG, "File is too large", str(path))
    return data.decode("utf-8")
