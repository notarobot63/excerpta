"""Format Netscape Bookmark (import / export des favoris).

Le format n'a pas de grammaire stricte : `<DT>` et `<DD>` ne sont jamais
fermés, `<p>` traîne après chaque `<DL>`. Un arbre DOM construit par
BeautifulSoup/html.parser rangeait donc le `<DD>` (la note) à l'intérieur du
`<DT>`, là où l'ancien code ne le cherchait pas : toutes les notes étaient
perdues. Et les dossiers (`<H3>` suivi de son `<DL>`) étaient ignorés, alors
que la documentation les annonçait.

On lit donc le flux de balises, comme le font les navigateurs à l'import :
un `<H3>` nomme le `<DL>` qui le suit, chaque `<DL>` ouvre un niveau, et un
`<DD>` complète le dernier `<A>` rencontré.
"""
from __future__ import annotations

from collections import defaultdict
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse

from .utils import build_folder_tree

MAX_FOLDER_NAME_LEN = 200


class _BookmarkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict] = []
        self._path: list[str] = []          # dossiers ouverts, de la racine au courant
        self._dl_opened_folder: list[bool] = []  # par <DL> ouvert : a-t-il empilé un dossier ?
        self._pending_folder: str | None = None  # nom lu dans un <H3>, en attente de son <DL>
        self._capture: str | None = None    # "h3" | "a" | "dd"
        self._text: list[str] = []
        self._anchor: dict | None = None
        self._last_item: dict | None = None  # destinataire d'un éventuel <DD>

    # ── Capture de texte ─────────────────────────────────────────────────────
    def _flush(self) -> None:
        """Termine la capture en cours, quelle qu'elle soit."""
        kind, text = self._capture, " ".join("".join(self._text).split())
        self._capture, self._text = None, []
        if kind == "h3":
            self._pending_folder = text
            self._last_item = None  # un <DD> après un <H3> décrit le dossier
        elif kind == "a" and self._anchor is not None:
            self._add_item(self._anchor, text)
            self._anchor = None
        elif kind == "dd" and self._last_item is not None and text:
            self._last_item["note"] = text[:50_000]
            self._last_item = None

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._text.append(data)

    # ── Structure ────────────────────────────────────────────────────────────
    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("dt", "dd", "dl", "h3", "a"):
            self._flush()
        if tag == "dl":
            opened = bool(self._pending_folder)
            if opened:
                self._path.append(self._pending_folder)
            self._dl_opened_folder.append(opened)
            self._pending_folder = None
            self._last_item = None
        elif tag == "h3":
            self._capture = "h3"
        elif tag == "a":
            self._anchor = {k: (v or "") for k, v in attrs}
            self._capture = "a"
        elif tag == "dd":
            self._capture = "dd"

    def handle_endtag(self, tag: str) -> None:
        if tag in ("h3", "a"):
            if self._capture == tag:
                self._flush()
        elif tag == "dl":
            self._flush()
            if self._dl_opened_folder and self._dl_opened_folder.pop():
                self._path.pop()
            self._last_item = None

    def close(self) -> None:
        super().close()
        self._flush()

    # ── Élément ──────────────────────────────────────────────────────────────
    def _add_item(self, attrs: dict, text: str) -> None:
        href = attrs.get("href", "").strip()
        if not href.startswith("http"):
            self._last_item = None
            return
        tags: list[str] = []
        # Sépare par virgule puis par espace (tags Linkding parfois multi-mots)
        for part in (attrs.get("tags") or attrs.get("tag") or "").split(","):
            tags.extend(w.lower() for w in part.split() if w.strip())
        folder_path = tuple(
            name[:MAX_FOLDER_NAME_LEN] for name in self._path if name
        )
        if not folder_path and attrs.get("folder", "").strip():
            # Exports Excerpta antérieurs : liste plate, dossier en attribut.
            folder_path = (attrs["folder"].strip()[:MAX_FOLDER_NAME_LEN],)
        parsed = urlparse(href)
        item = {
            "url": href,
            "title": (text or href)[:500],
            "tags": tags,
            "note": "",
            "favicon_url": f"{parsed.scheme}://{parsed.netloc}/favicon.ico",
            "folder_path": folder_path,
        }
        self.items.append(item)
        self._last_item = item


def parse_bookmarks(html: str) -> list[dict]:
    """Favoris d'un fichier Netscape : url, title, tags, note, favicon_url,
    folder_path (tuple des noms de dossiers, de la racine au plus profond)."""
    parser = _BookmarkParser()
    parser.feed(html)
    parser.close()
    return parser.items


def build_bookmarks(links: list, folders: list) -> str:
    """Export Netscape avec l'arborescence des dossiers (`<H3>` imbriqués).

    L'ancien export était plat, le dossier réduit à un attribut `FOLDER` que ni
    les navigateurs ni notre propre import ne relisaient.
    """
    by_folder: dict = defaultdict(list)
    for lk in links:
        by_folder[lk.folder_id].append(lk)

    lines = [
        "<!DOCTYPE NETSCAPE-Bookmark-file-1>",
        '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
        "<TITLE>Excerpta Export</TITLE>",
        "<H1>Bookmarks</H1>",
        "<DL><p>",
    ]

    def pad(level: int) -> str:
        return "    " * level

    def emit_link(lk, level: int) -> None:
        tags = escape(",".join(t.name for t in lk.tags), quote=True)
        ts = int(lk.created_at.timestamp())
        lines.append(
            f'{pad(level)}<DT><A HREF="{escape(lk.url, quote=True)}" ADD_DATE="{ts}" '
            f'TAGS="{tags}">{escape(lk.title)}</A>'
        )
        body = lk.note or lk.description
        if body:
            lines.append(f"{pad(level)}<DD>{escape(body)}")

    # build_folder_tree parcourt l'arbre en profondeur, et rattache à la racine
    # les dossiers orphelins ou pris dans un cycle : aucun n'est perdu.
    open_levels = 0
    for folder, depth in build_folder_tree(folders):
        while open_levels > depth:
            lines.append(f"{pad(open_levels)}</DL><p>")
            open_levels -= 1
        lines.append(f"{pad(depth + 1)}<DT><H3>{escape(folder.name)}</H3>")
        lines.append(f"{pad(depth + 1)}<DL><p>")
        open_levels = depth + 1
        for lk in by_folder.pop(folder.id, []):
            emit_link(lk, depth + 2)
    while open_levels > 0:
        lines.append(f"{pad(open_levels)}</DL><p>")
        open_levels -= 1

    # Sans dossier, puis (par sûreté) tout lien dont le dossier n'a pas été émis.
    for lk in by_folder.pop(None, []):
        emit_link(lk, 1)
    for remaining in by_folder.values():
        for lk in remaining:
            emit_link(lk, 1)
    lines.append("</DL><p>")
    return "\n".join(lines)
