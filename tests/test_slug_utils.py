from __future__ import annotations

import unittest

from ultimate_stem_lab.slug_utils import choose_project_slug, safe_slug


class SafeSlugTests(unittest.TestCase):
    def test_ascii_title_becomes_lowercase_underscore_slug(self) -> None:
        self.assertEqual(safe_slug("My Song Title"), "my_song_title")

    def test_unicode_and_punctuation_still_produce_clean_slug(self) -> None:
        self.assertEqual(
            safe_slug("Beyonc\u00e9 & Jay-Z\u2019s D\u00e9j\u00e0 Vu!!!"),
            "beyonce_and_jay_zs_deja_vu",
        )
        self.assertTrue(safe_slug("   ???   ", fallback="track"))


class ProjectSlugSelectionTests(unittest.TestCase):
    def test_prefers_track_over_other_metadata_fields(self) -> None:
        selection = choose_project_slug(
            metadata={
                "track": "Album Cut",
                "title": "Video Title",
                "fulltitle": "Artist - Album Cut (Official Video)",
                "id": "abc123",
            },
            source_stem="Video Title [abc123]",
            existing_slugs=set(),
        )
        self.assertEqual(selection.base_slug, "album_cut")
        self.assertEqual(selection.final_slug, "album_cut")
        self.assertEqual(selection.source_label, "track")

    def test_falls_back_to_cleaned_media_filename(self) -> None:
        selection = choose_project_slug(
            metadata=None,
            source_stem="Song Title [abc123]",
            existing_slugs=set(),
        )
        self.assertEqual(selection.base_slug, "song_title")
        self.assertEqual(selection.final_slug, "song_title")

    def test_uses_video_id_suffix_when_base_slug_exists(self) -> None:
        selection = choose_project_slug(
            metadata={"title": "Song Title", "id": "abc123"},
            source_stem="Song Title [abc123]",
            existing_slugs={"song_title"},
        )
        self.assertEqual(selection.base_slug, "song_title")
        self.assertEqual(selection.final_slug, "song_title_abc123")

    def test_uses_numeric_suffix_without_video_id(self) -> None:
        selection = choose_project_slug(
            metadata={"title": "Song Title"},
            source_stem="Song Title",
            existing_slugs={"song_title", "song_title_2"},
        )
        self.assertEqual(selection.final_slug, "song_title_3")

    def test_uses_numbered_video_id_suffix_when_needed(self) -> None:
        selection = choose_project_slug(
            metadata={"title": "Song Title", "id": "abc123"},
            source_stem="Song Title [abc123]",
            existing_slugs={"song_title", "song_title_abc123"},
        )
        self.assertEqual(selection.final_slug, "song_title_abc123_2")


if __name__ == "__main__":
    unittest.main()
