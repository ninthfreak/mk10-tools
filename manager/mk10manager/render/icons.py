"""Built-in icons (Tabler, MIT licence) and image files, drawn at any size and colour."""

import gzip
import json
import os
from pathlib import Path
from xml.sax.saxutils import quoteattr

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QImage, QImageReader, QPainter
from PySide6.QtSvg import QSvgRenderer

RES = Path(__file__).resolve().parent.parent / "resources"


class IconLibrary:
    def __init__(self):
        self._icons: dict | None = None
        self._cache: dict = {}
        self._file_cache: dict = {}

    @property
    def icons(self) -> dict:
        if self._icons is None:
            with gzip.open(RES / "tabler-icons.json.gz", "rb") as f:
                self._icons = json.loads(f.read())
        return self._icons

    def names(self) -> list[str]:
        return sorted(self.icons)

    def search(self, text: str, limit: int = 400) -> list[str]:
        text = text.strip().lower()
        if not text:
            return self.names()[:limit]
        words = text.split()
        if not hasattr(self, "_hay"):
            self._hay = {name: " ".join([name, str(meta.get("c", ""))] + [str(t) for t in meta.get("t", [])]).lower()
                         for name, meta in self.icons.items()}
        exact, starts, rest = [], [], []
        for name, hay in self._hay.items():
            if not all(w in hay for w in words):
                continue
            if name == text:
                exact.append(name)
            elif name.startswith(words[0]):
                starts.append(name)
            else:
                rest.append(name)
        return (exact + sorted(starts) + sorted(rest))[:limit]

    def exists(self, name: str) -> bool:
        return name in self.icons

    def svg(self, name: str, color: str = "#FFFFFF", stroke: float = 2.0) -> str | None:
        meta = self.icons.get(name)
        if not meta:
            return None
        parts = []
        for tag, attrs in meta["n"]:
            a = " ".join("%s=%s" % (k, quoteattr(str(v))) for k, v in attrs.items() if k != "key")
            parts.append("<%s %s/>" % (tag, a))
        return ('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" '
                'stroke=%s stroke-width="%s" stroke-linecap="round" stroke-linejoin="round">%s</svg>'
                % (quoteattr(color), stroke, "".join(parts)))

    def builtin_image(self, name: str, size: int, color: str = "#FFFFFF") -> QImage | None:
        key = ("b", name, size, color)
        if key in self._cache:
            return self._cache[key]
        svg = self.svg(name, color, 2.0 if size >= 48 else 1.75)
        if not svg:
            return None
        img = render_svg(svg.encode(), size, size)
        if len(self._cache) > 600:
            self._cache.clear()
        self._cache[key] = img
        return img

    def file_image(self, path: str) -> QImage | None:
        """Full-resolution image from disk (first frame for GIFs). Cached until the file changes."""
        if not path:
            return None
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return None
        key = (path, mtime)
        if key in self._file_cache:
            return self._file_cache[key]
        img = None
        if path.lower().endswith(".svg"):
            try:
                data = Path(path).read_bytes()
                r = QSvgRenderer(QByteArray(data))
                if r.isValid():
                    s = r.defaultSize()
                    w, h = max(1, s.width()), max(1, s.height())
                    scale = max(1.0, 512 / max(w, h))
                    img = render_svg(data, int(w * scale), int(h * scale))
            except OSError:
                img = None
        else:
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            got = reader.read()
            img = got if not got.isNull() else None
        if len(self._file_cache) > 100:
            self._file_cache.clear()
        self._file_cache[key] = img
        return img

    def icon_image(self, icon: dict | None, size: int) -> QImage | None:
        if not icon:
            return None
        if icon.get("kind") == "builtin":
            return self.builtin_image(icon.get("name", ""), size, icon.get("color") or "#FFFFFF")
        if icon.get("kind") == "file":
            return self.file_image(icon.get("path", ""))
        return None


def render_svg(data: bytes, w: int, h: int) -> QImage:
    img = QImage(QSize(w, h), QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    r = QSvgRenderer(QByteArray(data))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    r.render(p, QRectF(0, 0, w, h))
    p.end()
    return img
