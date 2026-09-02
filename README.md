# UpNote → Bear

Copy every note out of [UpNote](https://getupnote.com) and into
[Bear](https://bear.app) on the same Mac. No account, no export dialog, no
internet connection, nothing uploaded anywhere.

You end up with two independent copies of your notes: the ones still in
UpNote, and a matching set in Bear.

---

## Just make it work

1. Download this project (green **Code** button → **Download ZIP**) and
   unzip it.
2. Open the unzipped folder and double-click **`UpNote to Bear.command`**.
3. macOS will ask whether you're sure you want to open it — click **Open**.
   (The first time, you may need to right-click the file and choose **Open**
   instead of double-clicking.)
4. Answer the two questions it asks, then wait about a minute.

That's it. Your notes appear in Bear, and a folder called **UpNote to Bear**
is left on your Desktop holding a plain-text copy of everything.

You do not need to close UpNote, and nothing in UpNote is changed or deleted.

---

## What comes across

| In UpNote | In Bear |
|---|---|
| Note title and body | Same, converted to Markdown |
| Notebooks, including nested ones | Nested tags — `2- Area` and `2- Area/Apple ID` |
| Inline tags (`#work`) | The same tags |
| Bold, italic, highlight, headings, quotes | Same |
| Checklists | Bear checkboxes, ticked or unticked as they were |
| Code blocks | Code blocks, language kept |
| Tables | Markdown tables |
| Images saved on your Mac | Attached inside the note |
| Links from one note to another | Bear cross-note links, `[[Like this]]` |
| The order you arranged a notebook in | A "notebook contents" note listing it |

### What cannot come across

Two things are lost, and it is worth knowing why before you start.

**Creation dates.** Every note arrives in Bear dated today. Bear's importer
sets the date itself, and no file format or URL scheme lets an outside tool
override it. The notes keep their order relative to each other only through
the notebook contents notes.

**Images UpNote never downloaded.** UpNote keeps attachments in the cloud and
only saves a local copy of the ones you have actually looked at recently. Any
image without a local copy cannot be copied, because the bytes are not on your
Mac. Those notes get a line reading *[image not stored locally by UpNote: …]*,
and the full list is written to `MISSING-IMAGES.txt` in the output folder.

To rescue them: open those notes in UpNote while you're online, scroll so the
images load, then run this tool again.

**Manual note order.** Bear sorts notes by date or title and has no manual
ordering, so a notebook you arranged by hand cannot keep that arrangement.
Instead, each notebook gets a *"(notebook contents)"* note listing its notes
in the original order, as clickable links. Delete those notes if you don't
want them, or pass `--no-index` to skip making them.

### Smaller things to be aware of

- **Note titles containing `/` or `|` cannot be linked.** Bear reads `/` in a
  cross-note link as a heading and `|` as an alias, and gives no way to escape
  either. Links to such notes are written as the note's name in quotation
  marks rather than as a dead link. `--tidy-titles` removes the problem at
  source.
- **Two notes with the same title stay two notes with the same title.** A
  cross-note link to that title will land on one of them, and Bear picks
  which.
- **Bear sometimes shows a tag twice** after a large import — two rows with
  the same name, holding the same notes. It is cosmetic, on Bear's side, and
  you can merge them by renaming one to the name it already has.

---

## How it works

UpNote for Mac stores everything in a plain SQLite database inside its
application container:

```
~/Library/Containers/com.getupnote.desktop/Data/Library/Application Support/UpNote/
    upnote.sqlite3      all notes, notebooks and tags
    images/             attachment bytes, for the ones downloaded
```

The tool copies that database aside (so a running UpNote is never disturbed),
converts each note's HTML body to Markdown, and writes one
[TextBundle](http://textbundle.org) per note — a small folder holding the
Markdown plus the note's images.

Bear registers itself as the handler for `.textbundle`, so the tool then just
opens each bundle. Bear imports it silently, with no dialog.

Nothing is written back to UpNote, and no network request is ever made.

### Two details worth recording

If you are writing something similar, these two cost the most time to find:

- **Notebook membership is not a foreign key.** There is an `organizers`
  table that looks exactly like the join table you want, and on a synced
  install it is empty. The real mapping lives in the `lists` table, in rows
  keyed `notebooks_<notebookId>` whose `content` column is a JSON array of
  note ids. That array's order is the manual order you arranged the notebook
  in.

- **UpNote nests sub-lists as siblings.** It emits
  `<ul><li>…</li><ul><li>…</li></ul></ul>` — the nested `<ul>` is a sibling of
  the `<li>`, not a child of it. An HTML-to-Markdown converter that only
  recurses into list items will silently drop entire sub-trees. One note here
  lost 83% of its content that way before it was caught.

---

## For the command line

```bash
python3 upnote2bear.py                  # convert, then import into Bear
python3 upnote2bear.py --no-import      # convert only, import yourself later
python3 upnote2bear.py --include-trash  # bring UpNote's trash too, tagged
python3 upnote2bear.py --no-index       # skip the notebook contents notes
python3 upnote2bear.py --tidy-titles    # shorten titles clipped from the web
python3 upnote2bear.py --rename-map my-titles.json
python3 upnote2bear.py --out ~/somewhere/else
```

### Tidier titles

Notes clipped from the web arrive with the whole page title attached —
`4 Habits of Highly Confident People | by Nick Wignall | Personal Growth |
Medium`. Worse, a title containing `/` or `|` cannot be the target of a Bear
cross-note link, and a title starting with `#` is read as a tag.

`--tidy-titles` fixes all of that: it cuts the byline and publication tail,
replaces `/` and `|` with `-`, removes stray `#`, and caps the title at 70
characters on a word boundary.

For the titles no rule can guess — a note called `Notes:`, or three notes
sharing one name — write a `--rename-map`, a JSON file of
`{"old title": "new title"}`:

```json
{
  "Notes:": "Apple ID accounts",
  "8d13c8f7-07b0-4046-89af-f2a01683846f": "Router — WAN info (4G+)"
}
```

A key can be the original title (matched ignoring non-breaking spaces, which
clipped titles are full of) or an UpNote note id, which is the only way to
tell apart two notes that share a title. Renames are applied before
everything else, so the contents notes and cross-note links all use the new
names.

Run `python3 upnote2bear.py --help` for the full list.

The only requirement is the Python 3 that already ships with macOS. There is
nothing to install.

If you skipped the import, you can do it yourself at any time: select all the
`.textbundle` folders in the output folder and open them with Bear, or use
Bear's **File → Import From → Markdown Folder**.

---

## Tests

```bash
python3 test_upnote2bear.py
```

42 tests covering the conversion, written against the HTML shapes UpNote
actually produces. They use synthetic input, so they need no notes of your own
and never touch Bear.

## Checking the result

After importing, compare the two apps:

- Bear's sidebar should show your notebook tree as nested tags.
- The note count in Bear's **All Notes** should equal your UpNote note count,
  plus one note per notebook if you kept the contents notes.
- Read `MISSING-IMAGES.txt` to see exactly which images could not be copied.

If something looks wrong, everything is recoverable: the notes in UpNote are
untouched, and Bear's importer only ever adds notes.

---

## Running it twice

Running the tool again imports a second copy of every note — Bear has no way
to know it has seen them before. If you want a clean re-run, move the
previously imported notes to Bear's trash first (select them in Bear, press
⌘⌫), then run the tool again.

---

## Requirements

- macOS, with UpNote and Bear both installed
- UpNote opened at least once on this Mac, so its database exists
- Python 3 (already present on macOS)

## Licence

MIT — see [LICENSE](LICENSE).

Not affiliated with UpNote or Bear.
