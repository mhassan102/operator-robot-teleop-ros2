"""Desktop apps and the login registry that pairs them.

This tree is named ``packaging``. ROS 2 also imports the PyPI library
of that name (``packaging.version``). The container puts this tree
first on ``PYTHONPATH``, so a submodule that is not in this tree is
loaded from the PyPI copy.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _share_path_with_pypi_packaging() -> None:
    own = Path(__file__).resolve().parent
    for entry in list(sys.path):
        if not entry:
            continue
        root = Path(entry) / "packaging"
        try:
            if root.resolve() == own:
                continue
        except OSError:
            continue
        if not (root / "version.py").is_file():
            continue
        text = str(root)
        if text not in __path__:
            __path__.append(text)
        return


_share_path_with_pypi_packaging()
