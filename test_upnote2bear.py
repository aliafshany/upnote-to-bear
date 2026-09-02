#!/usr/bin/env python3
"""Conversion tests. Run with: python3 test_upnote2bear.py

These use synthetic HTML in the shapes UpNote actually emits, so no notes of
your own are needed and nothing is imported into Bear.
"""

import unittest

from upnote2bear import (bear_tag, rename_key, safe_name, tidy_title,
                         to_markdown, wiki_link)


def md(html, assets=(), titles=None):
    return to_markdown(html, set(assets), titles or {})[0]


class Inline(unittest.TestCase):
    def test_emphasis_and_code(self):
        self.assertEqual(md("<div><b>bold</b> and <i>it</i></div>"),
                         "**bold** and *it*")
        self.assertEqual(md("<div>run <code>ls -l</code></div>"),
                         "run `ls -l`")

    def test_highlight_becomes_bear_syntax(self):
        self.assertEqual(
            md('<div><span class="shine-highlight-yellow">hi</span></div>'),
            "::hi::")

    def test_trailing_space_stays_outside_the_marker(self):
        # "**bold **" is not emphasis in Markdown; the space must move out.
        self.assertEqual(md("<div><b>bold </b>tail</div>"), "**bold** tail")

    def test_html_whitespace_is_collapsed(self):
        self.assertEqual(md("<div>a\n\t   b</div>"), "a b")

    def test_link_forms(self):
        self.assertEqual(md('<div><a href="https://x.dev">X</a></div>'),
                         "[X](https://x.dev)")
        self.assertEqual(md('<div><a href="https://x.dev">https://x.dev</a>'
                            "</div>"), "https://x.dev")

    def test_upnote_loopback_links_are_dropped(self):
        self.assertEqual(
            md('<div><a href="http://localhost:9425/files/a.png">shot</a>'
               "</div>"), "shot")

    def test_note_link_becomes_a_bear_cross_note_link(self):
        html = ('<div><a href="upnote://x-callback-url/openNote?noteId=abc" '
                'data-note-id="abc">old title</a></div>')
        self.assertEqual(md(html, titles={"abc": "Real Title"}),
                         "[[Real Title]]")

    def test_inline_tag_keeps_its_name(self):
        html = '<div><a data-upnote-tag="#work" href="#">#work</a></div>'
        self.assertEqual(md(html), "#work")


class Images(unittest.TestCase):
    def test_local_image_is_carried_into_assets(self):
        html = '<div><img src="http://localhost:9425/images/a.png"></div>'
        body, used, absent = to_markdown(html, {"a.png"})
        self.assertEqual(body, "![](assets/a.png)")
        self.assertEqual(used, {"a.png"})
        self.assertFalse(absent)

    def test_image_upnote_never_downloaded_is_reported(self):
        html = '<div><img src="http://localhost:9425/images/gone.png"></div>'
        body, used, absent = to_markdown(html, set())
        self.assertIn("not stored locally", body)
        self.assertEqual(absent, {"gone.png"})
        self.assertFalse(used)

    def test_emoji_served_as_an_image_becomes_the_emoji(self):
        html = ('<div><img alt="✅" src="https://cdn.jsdelivr.net/joypixels/'
                'assets/8.0/png/unicode/64/2705.png" width="64"></div>')
        self.assertEqual(md(html), "✅")

    def test_emoji_without_alt_falls_back_to_the_codepoint(self):
        html = ('<div><img src="https://abs-0.twimg.com/emoji/v2/svg/'
                '1f602.svg"></div>')
        self.assertEqual(md(html), "😂")

    def test_remote_image_keeps_its_url(self):
        html = '<div><img alt="a" src="https://example.com/p.png"></div>'
        self.assertEqual(md(html), "![a](https://example.com/p.png)")


class Lists(unittest.TestCase):
    def test_sublist_written_as_a_sibling_is_not_lost(self):
        # UpNote emits <ul><li>a</li><ul><li>b</li></ul></ul>: the nested list
        # is a sibling of the item, not a child of it.
        html = "<ul><li>a</li><ul><li>b</li></ul></ul>"
        self.assertEqual(md(html), "- a\n\t- b")

    def test_sublist_nested_inside_the_item_also_works(self):
        html = "<ul><li>a<ul><li>b</li></ul></li></ul>"
        self.assertEqual(md(html), "- a\n\t- b")

    def test_checkboxes_keep_their_state(self):
        html = ('<ul><li data-checked="true">done</li>'
                '<li data-checked="false">todo</li></ul>')
        self.assertEqual(md(html), "- [x] done\n- [ ] todo")

    def test_ordered_list_numbers_itself(self):
        self.assertEqual(md("<ol><li>a</li><li>b</li></ol>"), "1. a\n2. b")


class Blocks(unittest.TestCase):
    def test_headings_and_rule(self):
        self.assertEqual(md("<h2>Title</h2><div><hr></div>"), "## Title\n\n---")

    def test_code_block_keeps_its_language(self):
        html = '<pre data-code-language="python">x = 1</pre>'
        self.assertEqual(md(html), "```python\nx = 1\n```")

    def test_code_containing_a_fence_is_wrapped_in_a_longer_one(self):
        html = "<pre>```<br>inner<br>```</pre>"
        out = md(html)
        self.assertTrue(out.startswith("````"))
        self.assertTrue(out.endswith("````"))
        self.assertIn("```\ninner\n```", out)

    def test_blockquote(self):
        self.assertEqual(md("<blockquote><div>q</div></blockquote>"), "> q")

    def test_table_without_a_header_row_still_renders(self):
        html = "<table><tbody><tr><td>a</td><td>b</td></tr></tbody></table>"
        self.assertEqual(md(html),
                         "|  |  |\n| --- | --- |\n| a | b |")

    def test_pipe_inside_a_cell_is_escaped(self):
        html = "<table><tbody><tr><td>a|b</td></tr></tbody></table>"
        self.assertIn(r"a\|b", md(html))

    def test_ragged_table_rows_are_padded(self):
        html = ("<table><tbody><tr><th>a</th><th>b</th></tr>"
                "<tr><td>1</td></tr></tbody></table>")
        self.assertEqual(md(html).splitlines()[-1], "| 1 |  |")


class Naming(unittest.TestCase):
    def test_tag_with_spaces_gets_a_closing_hash(self):
        self.assertEqual(bear_tag("2- Area/Apple ID"), "#2- Area/Apple ID#")
        self.assertEqual(bear_tag("work"), "#work")

    def test_filename_is_stripped_of_path_characters(self):
        self.assertEqual(safe_name("a/b:c", "fallback"), "a b c")

    def test_empty_title_falls_back(self):
        self.assertEqual(safe_name("   ", "Untitled 001"), "Untitled 001")

    def test_long_title_is_truncated(self):
        self.assertEqual(len(safe_name("x" * 200, "f")), 80)


class TidyTitles(unittest.TestCase):
    def test_medium_byline_tail_is_cut(self):
        self.assertEqual(
            tidy_title("4 Habits of Highly Confident People | by Nick Wignall"
                       " | Personal Growth | Medium"),
            "4 Habits of Highly Confident People")

    def test_site_name_tail_is_cut(self):
        self.assertEqual(
            tidy_title("The Psychological Effects of Quarantine | Elemental"),
            "The Psychological Effects of Quarantine")

    def test_short_first_segment_is_kept_whole(self):
        # Not a site tail: both halves carry meaning.
        self.assertEqual(tidy_title("Part 1 | Intro"), "Part 1 - Intro")

    def test_leading_hash_would_become_a_tag(self):
        self.assertEqual(tidy_title("#XMPlus node config"), "XMPlus node config")

    def test_slash_and_pipe_are_replaced(self):
        self.assertEqual(tidy_title("5G/4G Wireless Router"),
                         "5G-4G Wireless Router")

    def test_long_title_is_cut_at_a_word_boundary(self):
        out = tidy_title("word " * 40)
        self.assertLessEqual(len(out), 70)
        self.assertFalse(out.endswith("wor"))

    def test_rename_key_ignores_non_breaking_spaces(self):
        self.assertEqual(rename_key("a\u00a0b"), rename_key("a b"))


class Links(unittest.TestCase):
    def test_title_bear_can_link_to(self):
        self.assertEqual(wiki_link("Plain Title"), "[[Plain Title]]")

    def test_title_with_slash_or_pipe_is_not_linked(self):
        # Bear reads / as a heading and | as an alias, with no escape.
        self.assertEqual(wiki_link("5G/4G Router"), "\u201c5G/4G Router\u201d")
        self.assertEqual(wiki_link("Post | by Someone"),
                         "\u201cPost | by Someone\u201d")

    def test_url_with_a_bracket_is_wrapped(self):
        html = ('<div><a href="https://w.org/Symmetry_(physics)">S</a></div>')
        self.assertEqual(md(html),
                         "[S](<https://w.org/Symmetry_(physics)>)")


class Malformed(unittest.TestCase):
    def test_unclosed_tags_do_not_lose_text(self):
        self.assertIn("second", md("<div><b>first<div>second</div>"))

    def test_body_wrapper_does_not_flatten_the_note(self):
        html = "<body><h1>T</h1><ul><li>item</li></ul></body>"
        self.assertEqual(md(html), "# T\n\n- item")

    def test_blank_lines_inside_code_are_kept(self):
        html = "<pre>a<br><br><br><br>b</pre>"
        self.assertEqual(md(html), "```\na\n\n\n\nb\n```")

    def test_empty_html_is_empty(self):
        self.assertEqual(md(""), "")
        self.assertEqual(md(None), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
