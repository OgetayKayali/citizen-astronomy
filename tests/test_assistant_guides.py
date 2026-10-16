from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from photometry_app.app_metadata import application_root_path
from photometry_app.assistant.file_guard import GuardedFileWriter
from photometry_app.assistant.guide_tools import guide_tools
from photometry_app.assistant.guides import GuideLibrary, parse_guide, search_terms
from photometry_app.assistant.messages import ToolCall
from photometry_app.assistant.program_map import MODES, mode_info, program_map_text
from photometry_app.assistant.prompts import build_system_prompt
from photometry_app.assistant.tools import ToolContext, ToolRegistry, execute_tool_call
from photometry_app.core.models import AppMode


SAMPLE_GUIDE = """\
# Star Tool

<img src="logo.png"> Intro text with a [link](https://example.org).

## How It Works

### Step 1: Open a Folder

Use File > Open Folder to load frames.
![screenshot](images/x.jpg)

### Step 2: Export

Save the light curve as CSV.

```
# not a heading
```

## Settings

### Presets

Presets for apertures.

## Export Presets

### Presets

Presets for export sizes.
"""


class ParseGuideTest(unittest.TestCase):
    def setUp(self) -> None:
        self.guide = parse_guide("star_tool", SAMPLE_GUIDE)

    def test_sections_follow_headings_with_breadcrumbs(self) -> None:
        self.assertEqual(self.guide.title, "Star Tool")
        paths = [section.path_label for section in self.guide.sections]
        self.assertIn("Star Tool > How It Works > Step 1: Open a Folder", paths)
        self.assertIn("Star Tool > Settings > Presets", paths)
        self.assertNotIn("not a heading", " ".join(paths))

    def test_duplicate_headings_get_distinct_ids(self) -> None:
        ids = [section.section_id for section in self.guide.sections]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("star_tool#presets", ids)
        self.assertIn("star_tool#presets-2", ids)

    def test_images_and_html_are_removed_and_link_text_kept(self) -> None:
        intro = self.guide.sections[0]
        self.assertEqual(intro.text, "Intro text with a link.")
        step = next(section for section in self.guide.sections if section.heading.startswith("Step 1"))
        self.assertNotIn("screenshot", step.text)
        code = next(section for section in self.guide.sections if section.heading == "Step 2: Export")
        self.assertIn("# not a heading", code.text)

    def test_search_terms_normalize_plurals_and_verbs(self) -> None:
        self.assertEqual(search_terms("Images"), search_terms("image"))
        self.assertEqual(search_terms("stacking"), search_terms("stack"))
        self.assertEqual(search_terms("How do I use the"), [])


class GuideLibraryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        folder = Path(self._temp.name)
        (folder / "star_tool.md").write_text(SAMPLE_GUIDE, encoding="utf-8")
        (folder / "other.md").write_text("# Other\n\n## Folders\n\nNothing about light curves here.\n", encoding="utf-8")
        (folder / "notes.txt").write_text("# Ignored", encoding="utf-8")
        self.library = GuideLibrary(folder)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_only_markdown_files_are_guides(self) -> None:
        self.assertEqual(sorted(guide.guide_id for guide in self.library.guides()), ["other", "star_tool"])

    def test_search_prefers_the_section_about_the_question(self) -> None:
        hits = self.library.search("save light curve csv")
        self.assertEqual(hits[0].section.heading, "Step 2: Export")
        self.assertIn("CSV", hits[0].snippet)

    def test_search_can_be_limited_to_one_guide(self) -> None:
        hits = self.library.search("folder", guide_id="other")
        self.assertEqual({hit.section.guide_id for hit in hits}, {"other"})

    def test_search_without_useful_words_returns_nothing(self) -> None:
        self.assertEqual(self.library.search("how do I"), [])
        self.assertEqual(self.library.search("zzzz"), [])

    def test_section_reads_with_its_subsections_only(self) -> None:
        text, omitted = self.library.section_with_subsections("star_tool#how-it-works")
        self.assertIn("### Step 1: Open a Folder", text)
        self.assertIn("### Step 2: Export", text)
        self.assertNotIn("Presets", text)
        self.assertEqual(omitted, [])

    def test_long_sections_list_what_did_not_fit(self) -> None:
        text, omitted = self.library.section_with_subsections("star_tool#how-it-works", max_chars=100)
        self.assertIn("Step 1", text)
        self.assertEqual(omitted, ["star_tool#step-2-export"])

    def test_missing_folder_means_no_guides(self) -> None:
        self.assertEqual(GuideLibrary(Path(self._temp.name) / "missing").guides(), [])


class BundledGuidesTest(unittest.TestCase):
    def test_every_mode_guide_ships(self) -> None:
        folder = application_root_path() / "guides"
        for info in MODES:
            if info.guide_id is not None:
                self.assertTrue((folder / f"{info.guide_id}.md").is_file(), info.guide_id)

    def test_real_guides_answer_common_questions(self) -> None:
        library = GuideLibrary()
        cases = {
            "create a custom survey from my own images": "sky_atlas",
            "export an AAVSO report": "differential_photometry",
            "find co-moving stars in a cluster": "hr_diagram",
            "make a stacking animation": "astrostack",
            "blink frames to find moving objects": "asteroid_comet_detection",
        }
        for query, guide_id in cases.items():
            with self.subTest(query=query):
                self.assertEqual(library.search(query, limit=1)[0].section.guide_id, guide_id)


class ProgramMapTest(unittest.TestCase):
    def test_every_mode_is_described_once(self) -> None:
        self.assertEqual(sorted(info.mode for info in MODES), sorted(AppMode))

    def test_lookup_by_mode_or_name(self) -> None:
        self.assertEqual(mode_info(AppMode.ASTROSTACK).name, "Deep Stack")
        self.assertEqual(mode_info("sky atlas").mode, AppMode.SKY_VIEW)
        self.assertIsNone(mode_info("Unknown"))

    def test_system_prompt_carries_the_map_and_guide_instructions(self) -> None:
        prompt = build_system_prompt("- Open mode: HR Diagram")
        self.assertIn(program_map_text(), prompt)
        self.assertIn("search_guides", prompt)
        self.assertIn("Observation Deck: opens", prompt)
        self.assertTrue(prompt.rstrip().endswith("- Open mode: HR Diagram"))


class GuideToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        folder = Path(self._temp.name)
        (folder / "star_tool.md").write_text(SAMPLE_GUIDE, encoding="utf-8")
        self.registry = ToolRegistry(guide_tools(GuideLibrary(folder)))
        self.context = ToolContext(writer=GuardedFileWriter())

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _run(self, name: str, arguments: dict[str, object]) -> tuple[str, dict[str, object]]:
        result = execute_tool_call(self.registry, self.context, ToolCall("c1", name, arguments), approver=None)
        return result.status, json.loads(result.content)

    def test_guide_tools_are_read_only_and_need_no_approval(self) -> None:
        status, payload = self._run("search_guides", {"query": "export light curve"})
        self.assertEqual(status, "ok")
        first = payload["results"][0]
        self.assertEqual(first["section"], "Star Tool > How It Works > Step 2: Export")
        status, payload = self._run("read_guide_section", {"section_id": first["section_id"]})
        self.assertEqual(status, "ok")
        self.assertIn("Save the light curve as CSV.", payload["text"])

    def test_no_match_says_so(self) -> None:
        status, payload = self._run("search_guides", {"query": "spectroscopy"})
        self.assertEqual(status, "ok")
        self.assertEqual(payload["results"], [])
        self.assertIn("No guide section matched", payload["note"])

    def test_unknown_guide_or_section_is_an_error(self) -> None:
        status, payload = self._run("search_guides", {"query": "x", "guide": "nope"})
        self.assertEqual(status, "error")
        self.assertIn("star_tool", payload["message"])
        status, payload = self._run("read_guide_section", {"section_id": "star_tool#nope"})
        self.assertEqual(status, "error")

    def test_list_guides_and_sections(self) -> None:
        status, payload = self._run("list_guide_sections", {})
        self.assertEqual(payload["guides"], [{"guide": "star_tool", "title": "Star Tool", "modes": []}])
        status, payload = self._run("list_guide_sections", {"guide": "star_tool"})
        self.assertIn({"section_id": "star_tool#how-it-works", "level": 2, "heading": "How It Works"}, payload["sections"])

    def test_arguments_are_checked(self) -> None:
        status, payload = self._run("search_guides", {"query": "x", "max_results": 50})
        self.assertEqual(status, "error")
        status, payload = self._run("search_guides", {})
        self.assertEqual(status, "error")


if __name__ == "__main__":
    unittest.main()
