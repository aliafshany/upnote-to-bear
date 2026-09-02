#!/usr/bin/env python3
"""Copy UpNote notes into Bear, entirely offline.

UpNote for Mac keeps everything in a local SQLite database inside its app
container, with attachment bytes in a sibling ``images`` folder. This script
reads that database directly, converts each note's HTML body to Markdown, and
writes one TextBundle per note. Bear registers itself as the handler for
``.textbundle``, so opening the bundles imports them silently.

Nothing here touches the network, and nothing is written back to UpNote.

Usage:
    python3 upnote2bear.py                 # convert, then import into Bear
    python3 upnote2bear.py --no-import     # convert only
    python3 upnote2bear.py --include-trash # also bring UpNote's trash

Requires only the Python 3 that ships with macOS.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import unicodedata
from html.parser import HTMLParser
from urllib.parse import unquote

UPNOTE_DIR = os.path.expanduser(
    "~/Library/Containers/com.getupnote.desktop/Data/Library/"
    "Application Support/UpNote"
)
BEAR_DB = os.path.expanduser(
    "~/Library/Group Containers/9K33E3U3T4.net.shinyfrog.bear/"
    "Application Data/database.sqlite"
)
DEFAULT_OUT = os.path.expanduser("~/Desktop/UpNote to Bear")

# Tags Bear cannot represent, and characters that break note filenames.
TAG_UNSAFE = re.compile(r"[#/\\]+")
NAME_UNSAFE = re.compile(r"[\n\r\t/\\:]+")

VOID = {"br", "hr", "img", "col", "input"}
BLOCK = {"div", "p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li",
         "blockquote", "pre", "table", "tr", "hr", "tbody", "thead",
         "colgroup", "html", "body", "main", "center", "header", "footer",
         "aside", "nav", "dl", "dd", "dt", "form", "fieldset", "details",
         "summary", "figure", "section", "article"}
# HTML collapses runs of whitespace; Markdown does not, so clipped web pages
# would otherwise arrive carrying their source indentation.
WS_RUN = re.compile(r"[ \t\r\n ]+")
# Emoji served as images by web pages the notes were clipped from.
EMOJI_CDN = re.compile(
    r"(joypixels|twemoji|/emoji/|emoji\.(?:png|svg)|s\.w\.org/images/core/emoji)",
    re.I)


# --------------------------------------------------------------------------
# HTML -> tree
# --------------------------------------------------------------------------

class Node:
    __slots__ = ("tag", "attrs", "kids", "text")

    def __init__(self, tag, attrs=None, text=None):
        self.tag = tag
        self.attrs = dict(attrs or [])
        self.kids = []
        self.text = text


class Tree(HTMLParser):
    """Builds a forgiving DOM. UpNote's HTML is well formed, but notes
    clipped from the web are not, so unclosed tags must not lose content."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].kids.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].kids.append(Node(tag, attrs))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].kids.append(Node("#text", None, data))


def has_block(node):
    return any(k.tag in BLOCK for k in node.kids)


# --------------------------------------------------------------------------
# tree -> Markdown
# --------------------------------------------------------------------------

class Render:
    def __init__(self, assets, note_titles=None):
        self.assets = assets      # image filename -> present in images/ dir
        self.note_titles = note_titles or {}   # UpNote note id -> title
        self.used = set()         # attachments carried into the bundle
        self.absent = set()       # referenced but never downloaded by UpNote

    # ---- inline ----

    def inline(self, node):
        return "".join(self.inline_one(k) for k in node.kids)

    def inline_one(self, n):
        tag = n.tag
        if tag == "#text":
            return WS_RUN.sub(" ", n.text)
        if tag == "br":
            return "\n"
        if tag in ("b", "strong"):
            return self.wrap(self.inline(n), "**")
        if tag in ("i", "em"):
            return self.wrap(self.inline(n), "*")
        if tag == "code":
            s = self.inline(n)
            return "`" + s + "`" if s.strip() else s
        if tag == "a":
            return self.link(n)
        if tag == "img":
            return self.image(n)
        if tag == "span":
            s = self.inline(n)
            if "shine-highlight" in n.attrs.get("class", ""):
                return self.wrap(s, "::")   # Bear's highlight syntax
            return s
        return self.inline(n)

    def link(self, n):
        text = self.inline(n).strip()
        href = n.attrs.get("href", "")
        inline_tag = n.attrs.get("data-upnote-tag")
        if inline_tag:
            return bear_tag(inline_tag.lstrip("#"))
        # A link from one note to another becomes a Bear cross-note link.
        note_id = n.attrs.get("data-note-id") or ""
        if not note_id:
            linked = re.search(r"openNote\?noteId=([0-9a-fA-F-]+)", href)
            note_id = linked.group(1) if linked else ""
        if note_id:
            target = self.note_titles.get(note_id)
            if target:
                return wiki_link(target)
            return text or "(linked UpNote note)"
        if href.startswith("upnote://x-callback-url/tag/view"):
            name = re.search(r"[?&]tag=([^&]+)", href)
            if name:
                return bear_tag(unquote(name.group(1)))
        # UpNote's loopback links (open-original, attachment viewer) point at
        # a local server that only runs inside UpNote.
        if not href or href.startswith("http://localhost:"):
            return text
        if not text:
            return href
        if text == href:
            return href
        if ")" in href or " " in href:
            return "[%s](<%s>)" % (text, href)
        return "[%s](%s)" % (text, href)

    def image(self, n):
        src = n.attrs.get("src", "")
        alt = n.attrs.get("alt", "")
        # Emoji pasted from the web arrive as 64px images, which Bear renders
        # at full size. The alt text is the emoji itself, so use that.
        if EMOJI_CDN.search(src):
            if alt.strip():
                return alt.strip()
            points = re.findall(r"([0-9a-f]{4,6})", src.rsplit("/", 1)[-1])
            try:
                return "".join(chr(int(p, 16)) for p in points)
            except ValueError:
                return ""
        local = re.match(r"https?://localhost:\d+/images/(.+)$", src)
        if not local:
            return "![%s](%s)" % (alt, src)      # remote image, keep the URL
        fname = local.group(1)
        if fname in self.assets:
            self.used.add(fname)
            return "![%s](assets/%s)" % (alt, fname)
        # UpNote never downloaded this attachment: the bytes exist only in its
        # cloud storage, so there is nothing local to carry over.
        self.absent.add(fname)
        return "*[image not stored locally by UpNote: %s]*" % fname

    @staticmethod
    def wrap(s, mark):
        if not s.strip():
            return s
        lead = s[:len(s) - len(s.lstrip())]
        trail = s[len(s.rstrip()):]
        return lead + mark + s.strip() + mark + trail

    # ---- block ----

    def blocks(self, node, depth=0):
        out, buf = [], []

        def flush():
            if buf:
                s = "".join(buf).strip("\n")
                if s.strip():
                    out.append(s)
                buf.clear()

        for k in node.kids:
            tag = k.tag
            if tag in ("script", "style"):
                continue
            if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                flush()
                s = self.inline(k).strip()
                if s:
                    out.append("#" * int(tag[1]) + " " + s)
            elif tag == "hr":
                flush()
                out.append("---")
            elif tag == "pre":
                flush()
                lang = k.attrs.get("data-code-language", "")
                code = self.raw(k).strip("\n")
                # A snippet containing its own ``` would close the block early
                # and leave the rest of the note rendered as code.
                longest = max((len(m) for m in re.findall(r"`+", code)),
                              default=0)
                fence = "`" * max(3, longest + 1)
                out.append("%s%s\n%s\n%s" % (fence, lang, code, fence))
            elif tag == "blockquote":
                flush()
                inner = "\n\n".join(self.blocks(k, depth))
                out.append("\n".join("> " + l if l else ">"
                                     for l in inner.split("\n")))
            elif tag in ("ul", "ol"):
                flush()
                out.append(self.listing(k, depth))
            elif tag == "table":
                flush()
                out.append(self.table(k))
            elif tag in ("div", "p", "section", "article", "tbody", "thead",
                         "figure", "html", "body", "main", "center", "header",
                         "footer", "aside", "nav", "dl", "dd", "dt", "form",
                         "label", "fieldset", "details", "summary"):
                flush()
                if has_block(k):
                    out.extend(self.blocks(k, depth))
                else:
                    s = self.inline(k).strip("\n")
                    if s.strip():
                        out.append(s)
            elif tag == "#text":
                buf.append(WS_RUN.sub(" ", k.text))
            else:
                buf.append(self.inline_one(k))
        flush()
        return out

    def raw(self, node):
        """Verbatim text, for code blocks."""
        if node.tag == "#text":
            return node.text
        if node.tag == "br":
            return "\n"
        return "".join(self.raw(k) for k in node.kids)

    def listing(self, node, depth):
        ordered = node.tag == "ol"
        pad = "\t" * depth
        lines, number = [], 1
        for li in node.kids:
            # UpNote writes sub-lists as siblings of <li>, not inside it, so a
            # walker that only looks inside list items silently drops them.
            if li.tag in ("ul", "ol"):
                sub = self.listing(li, depth + 1)
                if sub.strip():
                    lines.append(sub)
                continue
            if li.tag != "li":
                continue
            nested = [k for k in li.kids if k.tag in ("ul", "ol")]
            body = Node("li")
            body.kids = [k for k in li.kids if k.tag not in ("ul", "ol")]
            if has_block(body):
                text = "\n".join(self.blocks(body, depth + 1)).strip()
            else:
                text = self.inline(body).strip()

            checked = li.attrs.get("data-checked")
            if checked is not None:
                marker = "- [x] " if checked == "true" else "- [ ] "
            elif ordered:
                marker = "%d. " % number
                number += 1
            else:
                marker = "- "

            lines.append(pad + marker + text.replace("\n", "\n" + pad + "\t"))
            for sub in nested:
                lines.append(self.listing(sub, depth + 1))
        return "\n".join(l for l in lines if l.strip())

    def table(self, node):
        rows = []

        def walk(n):
            for k in n.kids:
                if k.tag == "tr":
                    rows.append(k)
                else:
                    walk(k)

        walk(node)
        if not rows:
            return ""
        grid = []
        for r in rows:
            cells = [c for c in r.kids if c.tag in ("td", "th")]
            grid.append([self.inline(c).replace("\n", " ")
                         .replace("|", "\\|").strip() for c in cells])
        width = max(len(r) for r in grid)
        grid = [r + [""] * (width - len(r)) for r in grid]
        # GitHub-flavoured Markdown always needs a header row.
        headed = any(c.tag == "th" for c in rows[0].kids)
        head = grid[0] if headed else [""] * width
        body = grid[1:] if headed else grid
        out = ["| " + " | ".join(head) + " |",
               "| " + " | ".join(["---"] * width) + " |"]
        out += ["| " + " | ".join(r) + " |" for r in body]
        return "\n".join(out)


def wiki_link(title):
    """Bear reads `/` in a cross-note link as a heading and `|` as an alias,
    and offers no way to escape either, so titles containing them cannot be
    linked. Name the note in plain text instead of emitting a dead link."""
    if any(c in title for c in "/|[]"):
        return "\u201c%s\u201d" % title
    return "[[%s]]" % title


def bear_tag(name):
    """Bear needs the closing hash when a tag contains spaces."""
    name = name.strip().strip("#")
    if not name:
        return ""
    return "#%s#" % name if re.search(r"\s", name) else "#%s" % name


def to_markdown(raw_html, assets, note_titles=None):
    parser = Tree()
    parser.feed(raw_html or "")
    parser.close()
    render = Render(assets, note_titles)
    blocks = [b for b in render.blocks(parser.root) if b.strip()]
    # Squeeze runs of blank lines, but leave fenced code exactly as it was.
    blocks = [b if b.startswith("`") else re.sub(r"\n{3,}", "\n\n", b)
              for b in blocks]
    return "\n\n".join(blocks).strip(), render.used, render.absent


def safe_name(title, fallback):
    name = unicodedata.normalize("NFC", (title or "").strip())
    name = NAME_UNSAFE.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    return (name or fallback)[:80]


# --------------------------------------------------------------------------
# tidier titles
# --------------------------------------------------------------------------

# "Real Title | by Author | Publication | Feb, 2026 | Medium" and friends.
BYLINE = re.compile(r"\s+\|\s+by\s", re.I)
TITLE_CAP = 70


def tidy_title(title, cap=TITLE_CAP):
    """Turn a title clipped from the web into something readable in a note
    list, and remove the characters Bear cannot carry in a cross-note link."""
    out = unicodedata.normalize("NFC", (title or "").strip())

    byline = BYLINE.search(out)
    if byline:
        out = out[:byline.start()]
    elif " | " in out:
        # "Some Article | Elemental" - a site name tacked onto the end.
        head = out.split(" | ")[0].strip()
        if len(head) >= 25:
            out = head

    out = out.replace("|", "-").replace("/", "-")
    # A leading # would be read as a tag, and any # invents one mid-title.
    out = re.sub(r"#(?=\S)", "", out)
    out = re.sub(r"\s+", " ", out).strip(" -–—,;:.")

    if len(out) > cap:
        cut = out[:cap].rsplit(" ", 1)[0]
        out = (cut if len(cut) >= cap * 0.6 else out[:cap]).rstrip(" -–—,;:.")
    return out or title


def load_renames(path):
    """Optional JSON of {"original title": "title to use instead"}. A key may
    also be an UpNote note id, for telling apart notes that share a title."""
    if not path:
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {rename_key(k): unicodedata.normalize("NFC", v.strip())
            for k, v in data.items()}


def rename_key(title):
    """Match on visible text: titles clipped from the web are full of
    non-breaking spaces that nobody types when writing a rename map."""
    return unicodedata.normalize(
        "NFC", WS_RUN.sub(" ", title or "").strip())


# --------------------------------------------------------------------------
# UpNote database
# --------------------------------------------------------------------------

def open_upnote(work_dir, upnote_dir):
    """Copy the database aside before reading it, so a running UpNote is
    never blocked and its write-ahead log is picked up consistently."""
    os.makedirs(work_dir, exist_ok=True)
    # A -wal left over from an earlier run would be replayed onto the fresh
    # copy of the database, mixing old pages into new ones.
    for suffix in ("", "-wal", "-shm"):
        stale = os.path.join(work_dir, "upnote.sqlite3" + suffix)
        if os.path.exists(stale):
            os.remove(stale)
    found = False
    for suffix in ("", "-wal", "-shm"):
        src = os.path.join(upnote_dir, "upnote.sqlite3" + suffix)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(work_dir, "upnote.sqlite3" + suffix))
            found = found or suffix == ""
    if not found:
        raise SystemExit(
            "Could not find UpNote's database at:\n  %s\n"
            "Open UpNote once on this Mac, then run this again." % upnote_dir)
    db = sqlite3.connect(os.path.join(work_dir, "upnote.sqlite3"))
    db.row_factory = sqlite3.Row
    return db


def apply_tag_map(path, tag_map):
    """Rename a notebook path, and everything under it, on the way out.
    {"1- Projects": "1-Projects"} turns 1- Projects/Trade into
    1-Projects/Trade."""
    if not tag_map:
        return path
    parts = path.split("/")
    for depth in range(len(parts), 0, -1):
        head = "/".join(parts[:depth])
        if head in tag_map:
            tail = parts[depth:]
            return "/".join([tag_map[head]] + tail) if tail else tag_map[head]
    return path


def notebook_paths(db, tag_map=None):
    """UpNote stores notebook membership in `lists` rows keyed
    notebooks_<notebookId>, each holding a JSON array of note ids."""
    books = {r["id"]: (r["title"] or "", r["parent"] or "")
             for r in db.execute("select id, title, parent from notebooks "
                                 "where coalesce(deleted, 0) = 0")}

    def path_of(book_id):
        parts, seen = [], set()
        while book_id in books and book_id not in seen:
            seen.add(book_id)
            title, parent = books[book_id]
            title = TAG_UNSAFE.sub("-", title).strip()
            if title:
                parts.append(title)
            book_id = parent
        return "/".join(reversed(parts))

    per_note, ordered = {}, {}
    for row in db.execute("select id, content from lists where id like "
                          "'notebooks_%' and coalesce(deleted, 0) = 0"):
        path = apply_tag_map(path_of(row["id"][len("notebooks_"):]), tag_map)
        if not path:
            continue
        try:
            members = json.loads(row["content"] or "[]")
        except ValueError:
            continue
        members = members if isinstance(members, list) else []
        # The array order is the order you arranged the notebook in by hand.
        ordered.setdefault(path, []).extend(members)
        for note_id in members:
            per_note.setdefault(note_id, set()).add(path)
    return per_note, ordered


# --------------------------------------------------------------------------
# conversion
# --------------------------------------------------------------------------

def write_bundle(out_dir, name, text, source_id="", modified=0.0):
    bundle = os.path.join(out_dir, name + ".textbundle")
    os.makedirs(os.path.join(bundle, "assets"), exist_ok=True)
    with open(os.path.join(bundle, "text.md"), "w", encoding="utf-8") as f:
        f.write(text)
    with open(os.path.join(bundle, "info.json"), "w", encoding="utf-8") as f:
        json.dump({"version": 2,
                   "type": "net.daringfireball.markdown",
                   "transient": False,
                   "creatorIdentifier": "com.getupnote.desktop",
                   "sourceURL": "upnote://note/" + source_id},
                  f, ensure_ascii=False, indent=2)
    if modified:
        for path in (os.path.join(bundle, "text.md"),
                     os.path.join(bundle, "info.json"), bundle):
            try:
                os.utime(path, (modified, modified))
            except OSError:
                pass
    return bundle


def index_notes(out_dir, ordered, note_titles, taken):
    """Bear sorts notes by date and has no manual ordering, so the sequence
    you arranged each UpNote notebook in cannot survive as such. Write it
    down instead: one contents note per notebook, listing its notes in the
    original order as Bear cross-note links."""
    made = []
    for path in sorted(ordered):
        members = [n for n in ordered[path] if n in note_titles]
        if len(members) < 2:
            continue
        leaf = path.rsplit("/", 1)[-1]
        lines = ["# %s (notebook contents)" % leaf, "",
                 "The notes of *%s*, in the order they were arranged in "
                 "UpNote." % path, ""]
        lines += ["%d. %s" % (i, wiki_link(note_titles[n]))
                  for i, n in enumerate(members, 1)]
        lines += ["", bear_tag(path)]
        name = safe_name("%s (notebook contents)" % leaf, "Notebook contents")
        # Two notebooks can share a leaf name under different parents, and a
        # real note could already own this filename; never overwrite either.
        key = name.lower()
        taken[key] = taken.get(key, 0) + 1
        if taken[key] > 1:
            name = "%s (%d)" % (name, taken[key])
        made.append(write_bundle(out_dir, name, "\n".join(lines) + "\n"))
    return made


def convert(db, out_dir, upnote_dir, include_trash=False, with_index=True,
            tidy=False, renames=None, tag_map=None, log=print):
    image_dir = os.path.join(upnote_dir, "images")
    assets = set(os.listdir(image_dir)) if os.path.isdir(image_dir) else set()
    tags_by_note, ordered = notebook_paths(db, tag_map)

    where = "coalesce(deleted, 0) = 0"
    if not include_trash:
        where += " and coalesce(trashed, 0) = 0"
    rows = list(db.execute(
        "select id, title, html, createdAt, updatedAt, trashed from notes "
        "where %s order by createdAt" % where))

    if os.path.isdir(out_dir):
        strays = [e for e in os.listdir(out_dir)
                  if not e.endswith(".textbundle")
                  and e not in ("MISSING-IMAGES.txt", ".DS_Store")]
        if strays:
            raise SystemExit(
                "%s already exists and holds files this tool did not write "
                "(for example %r).\nPoint --out somewhere else, or empty that "
                "folder first." % (out_dir, strays[0]))
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)

    used_names, absent_all, bundles = {}, {}, []
    carried = 0

    # First pass: settle every note's final title, so that links from one note
    # to another can be written as Bear cross-note links in the second pass.
    titles, note_titles = {}, {}
    for index, row in enumerate(rows, 1):
        title = row["title"] or ""
        if not title:
            plain = to_markdown(row["html"], assets)[0]
            heading = re.search(r"^#{1,6}[ \t]+(.+)$", plain, re.M)
            title = heading.group(1).strip() if heading else ""
        renames = renames or {}
        # A rename may be keyed by the note's UpNote id, which is the only way
        # to tell apart two notes that share a title.
        renamed = renames.get(row["id"]) or renames.get(rename_key(title))
        if renamed:
            title = renamed
        elif tidy:
            title = tidy_title(title)
        name = safe_name(title, "Untitled %03d" % index)
        key = name.lower()
        used_names[key] = used_names.get(key, 0) + 1
        if used_names[key] > 1:
            name = "%s (%d)" % (name, used_names[key])
        titles[row["id"]] = (title or name, name)
        note_titles[row["id"]] = title or name

    for index, row in enumerate(rows, 1):
        body, used, absent = to_markdown(row["html"], assets, note_titles)
        title, name = titles[row["id"]]

        # UpNote usually repeats the title as the body's first heading; the
        # bundle already carries it as the H1, so drop the echo.
        heading = re.match(r"^#{1,6}[ \t]+(.+)$", body, re.M)
        if heading:
            echoed = re.sub(r"[*_`]", "", heading.group(1)).strip()
            if echoed and echoed == re.sub(r"[*_`]", "", title).strip():
                body = body[heading.end():].lstrip("\n")

        tags = sorted(tags_by_note.get(row["id"], ()))
        if include_trash and row["trashed"]:
            tags.append("UpNote Trash")
        tag_line = " ".join(bear_tag(t) for t in tags)

        text = "# %s\n\n%s" % (title, body.strip())
        if tag_line:
            text = text.rstrip() + "\n\n" + tag_line
        text = text.rstrip() + "\n"

        modified = (row["updatedAt"] or row["createdAt"] or 0) / 1000.0
        bundle = write_bundle(out_dir, name, text, row["id"], modified)

        for fname in used:
            shutil.copy2(os.path.join(image_dir, fname),
                         os.path.join(bundle, "assets", fname))
        carried += len(used)
        for fname in absent:
            absent_all.setdefault(fname, []).append(name)
        bundles.append(bundle)

    made = (index_notes(out_dir, ordered, note_titles, used_names)
            if with_index else [])
    bundles.extend(made)

    if absent_all:
        with open(os.path.join(out_dir, "MISSING-IMAGES.txt"), "w",
                  encoding="utf-8") as f:
            f.write("These images are referenced by your notes but UpNote "
                    "never saved a copy on this Mac,\nso there was nothing "
                    "to copy into Bear. To recover them: open the notes "
                    "below in UpNote\nwhile you are online, let the images "
                    "load, then run this tool again.\n\n")
            for fname in sorted(absent_all):
                f.write("%s\n" % fname)
                for note in sorted(set(absent_all[fname])):
                    f.write("    %s\n" % note)

    log("Converted %d notes." % (len(bundles) - len(made)))
    if made:
        log("Added %d notebook contents notes, preserving the order you "
            "arranged each notebook in." % len(made))
    log("Copied %d images into the notes." % carried)
    if absent_all:
        log("%d images were not saved on this Mac by UpNote - see "
            "MISSING-IMAGES.txt." % len(absent_all))
    return bundles


# --------------------------------------------------------------------------
# Bear import
# --------------------------------------------------------------------------

def bear_note_count():
    """Read Bear's own database to confirm what actually landed."""
    if not os.path.exists(BEAR_DB):
        return None
    tmp = os.path.join("/tmp", "bear-count-%d.sqlite" % os.getpid())
    try:
        for suffix in ("", "-wal", "-shm"):
            src = BEAR_DB + suffix
            if os.path.exists(src):
                shutil.copy2(src, tmp + suffix)
        db = sqlite3.connect(tmp)
        count = db.execute(
            "select count(*) from ZSFNOTE where ZTRASHED = 0").fetchone()[0]
        db.close()
        return count
    except sqlite3.Error:
        return None
    finally:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(tmp + suffix)
            except OSError:
                pass


def import_into_bear(bundles, log=print, batch=8, pause=3.0):
    if not os.path.isdir("/Applications/Bear.app"):
        log("Bear is not installed in /Applications, so nothing was "
            "imported. The converted notes are still on your Desktop.")
        return False
    before = bear_note_count()
    log("Importing into Bear. This takes about a minute; Bear may flash as "
        "notes arrive.")
    for index, bundle in enumerate(bundles, 1):
        subprocess.run(["open", "-a", "Bear", bundle], check=False)
        if index % batch == 0:
            time.sleep(pause)
    time.sleep(8)
    after = bear_note_count()
    if before is not None and after is not None:
        log("Bear went from %d notes to %d." % (before, after))
        missing = len(bundles) - (after - before)
        if missing > 0:
            log("%d notes may still be arriving - give Bear a moment, then "
                "check the count in its sidebar." % missing)
    return True


# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Copy UpNote notes into Bear, offline.")
    parser.add_argument("--out", default=DEFAULT_OUT,
                        help="where to write the converted notes "
                             "(default: %s)" % DEFAULT_OUT)
    parser.add_argument("--upnote-dir", default=UPNOTE_DIR,
                        help="UpNote's data folder, if it is not in the "
                             "usual place")
    parser.add_argument("--work-dir", default="/tmp/upnote2bear",
                        help="scratch folder for the database copy")
    parser.add_argument("--include-trash", action="store_true",
                        help="also copy notes sitting in UpNote's trash, "
                             "tagged #UpNote Trash#")
    parser.add_argument("--tidy-titles", action="store_true",
                        help="shorten titles clipped from the web and drop "
                             "the characters Bear cannot link to")
    parser.add_argument("--rename-map", metavar="FILE",
                        help="JSON file of {\"old title\": \"new title\"} "
                             "applied before everything else")
    parser.add_argument("--tag-map", metavar="FILE",
                        help="JSON file of {\"UpNote notebook\": \"Bear tag\"} "
                             "renames; child notebooks follow their parent")
    parser.add_argument("--no-index", action="store_true",
                        help="skip the per-notebook contents notes that "
                             "record UpNote's manual note order")
    parser.add_argument("--no-import", action="store_true",
                        help="convert only, do not hand the notes to Bear")
    args = parser.parse_args(argv)

    db = open_upnote(args.work_dir, args.upnote_dir)
    bundles = convert(db, args.out, args.upnote_dir,
                      include_trash=args.include_trash,
                      with_index=not args.no_index,
                      tidy=args.tidy_titles,
                      renames=load_renames(args.rename_map),
                      tag_map=load_renames(args.tag_map))
    if not bundles:
        print("No notes found.")
        return 1
    print("Saved to: %s" % args.out)
    if not args.no_import:
        import_into_bear(bundles)
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
