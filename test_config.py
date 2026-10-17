#!/usr/bin/env python3
"""Offline tests for config — .env parsing and source resolution."""

import os
import tempfile
import unittest

from config import ConfigError, load_config, load_env, parse_env


class ParseEnv(unittest.TestCase):
    def test_comments_quotes_and_export(self):
        env = parse_env(
            "# comment\n"
            "\n"
            "SOURCES=shazam,youtube   # what to collect\n"
            "export YOUTUBE_API_KEY=\"AIza#notacomment\"\n"
            "SINGLE='a b'\n"
            "EMPTY=\n"
            "not a setting\n"
        )
        self.assertEqual(env, {"SOURCES": "shazam,youtube",
                               "YOUTUBE_API_KEY": "AIza#notacomment",
                               "SINGLE": "a b", "EMPTY": ""})

    def test_missing_file_is_empty(self):
        self.assertEqual(load_env(os.path.join(tempfile.gettempdir(), "no-such.env")), {})

    def test_bom_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, ".env")
            with open(path, "w", encoding="utf-8-sig") as f:
                f.write("SOURCES=shazam\n")
            self.assertEqual(load_env(path), {"SOURCES": "shazam"})


class Sources(unittest.TestCase):
    def test_default_is_shazam_without_key(self):
        self.assertEqual(load_config(dotenv={}, environ={}).sources, ["shazam"])

    def test_default_adds_youtube_with_key(self):
        cfg = load_config(dotenv={"YOUTUBE_API_KEY": "k"}, environ={})
        self.assertEqual(cfg.sources, ["shazam", "youtube"])
        self.assertEqual(cfg.youtube_api_key, "k")

    def test_dotenv_can_disable_youtube(self):
        cfg = load_config(dotenv={"SOURCES": "shazam", "YOUTUBE_API_KEY": "k"}, environ={})
        self.assertEqual(cfg.sources, ["shazam"])

    def test_precedence_flag_env_dotenv(self):
        dotenv = {"SOURCES": "shazam", "YOUTUBE_API_KEY": "file"}
        environ = {"SOURCES": "youtube", "YOUTUBE_API_KEY": "real"}
        self.assertEqual(load_config(dotenv=dotenv, environ=environ).sources, ["youtube"])
        self.assertEqual(load_config(dotenv=dotenv, environ=environ).youtube_api_key, "real")
        self.assertEqual(load_config("youtube,shazam", dotenv=dotenv,
                                     environ=environ).sources, ["youtube", "shazam"])

    def test_empty_real_env_does_not_hide_dotenv(self):
        cfg = load_config(dotenv={"YOUTUBE_API_KEY": "file"}, environ={"YOUTUBE_API_KEY": ""})
        self.assertEqual(cfg.youtube_api_key, "file")

    def test_no_youtube_flag(self):
        cfg = load_config(no_youtube=True, dotenv={"SOURCES": "youtube"}, environ={})
        self.assertEqual(cfg.sources, ["shazam"])

    def test_youtube_without_key_fails(self):
        with self.assertRaises(ConfigError) as ctx:
            load_config("shazam,youtube", dotenv={}, environ={})
        self.assertIn("YOUTUBE_API_KEY", str(ctx.exception))

    def test_unknown_source_fails(self):
        with self.assertRaises(ConfigError):
            load_config("shazam,spotify", dotenv={}, environ={})

    def test_spaces_case_and_duplicates(self):
        cfg = load_config(" YouTube , shazam,youtube ", dotenv={"YOUTUBE_API_KEY": "k"},
                          environ={})
        self.assertEqual(cfg.sources, ["youtube", "shazam"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
