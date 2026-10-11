"""packaging/check_pins.py, with GitHub's API faked.

Run with:  python -m unittest discover -s tests -v
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "packaging")]
import build_exe  # noqa: E402
import check_pins  # noqa: E402


def release(tag, assets=()):
    return {"tag_name": tag, "html_url": f"https://github.com/x/releases/{tag}",
            "assets": [{"name": name, "digest": f"sha256:{sha}",
                        "browser_download_url": f"https://github.com/x/{name}"}
                       for name, sha in assets]}


class CheckPinsTest(unittest.TestCase):
    def run_updates(self, ffmpeg, deno):
        answers = {"GyanD/codexffmpeg": ffmpeg, "denoland/deno": deno}

        def get_json(url):
            return answers[url.split("/repos/")[1].removesuffix("/releases/latest")]

        with mock.patch.object(check_pins, "_get_json", get_json):
            return check_pins.updates()

    def test_nothing_newer(self):
        found = self.run_updates(release(build_exe.FFMPEG_VERSION),
                                 release(f"v{build_exe.DENO_VERSION}"))
        self.assertEqual(found, [])

    def test_a_newer_release(self):
        found = self.run_updates(
            release("99.0", [("ffmpeg-99.0-essentials_build.zip", "ab" * 32),
                             ("ffmpeg-99.0-full_build.zip", "cd" * 32)]),
            release(f"v{build_exe.DENO_VERSION}"))
        self.assertEqual(len(found), 1)
        update = found[0]
        self.assertEqual((update["name"], update["pinned"], update["latest"]),
                         ("ffmpeg", build_exe.FFMPEG_VERSION, "99.0"))
        self.assertEqual(update["sha256"], "ab" * 32, "the essentials build's checksum")
        title, body = check_pins.issue(update)
        self.assertEqual(title, "Update the bundled ffmpeg to 99.0")
        self.assertIn("ab" * 32, body)
        self.assertIn("FFMPEG_VERSION and FFMPEG_SHA256", body)
        self.assertIn("gyan.dev", body, "says where to check the checksum")

    def test_deno_versions_compare_without_the_v(self):
        found = self.run_updates(release(build_exe.FFMPEG_VERSION),
                                 release("v2.10.0", [(build_exe.DENO_ZIP, "ef" * 32)]))
        self.assertEqual([(u["name"], u["latest"]) for u in found], [("deno", "2.10.0")])
        self.assertTrue(check_pins.newer("2.10.0", "2.9.7"), "not compared as text")
        self.assertFalse(check_pins.newer("2.9.7", "2.9.7"))

    def test_an_issue_is_opened_once(self):
        update = self.run_updates(
            release("99.0", [("ffmpeg-99.0-essentials_build.zip", "ab" * 32)]),
            release(f"v{build_exe.DENO_VERSION}"))
        title = "Update the bundled ffmpeg to 99.0"
        for existing, expect_create in (("", True), (title + "\n", False)):
            with self.subTest(existing=existing), \
                    mock.patch.object(check_pins.subprocess, "run") as run, \
                    mock.patch("builtins.print"):
                run.return_value.stdout = existing
                check_pins.open_issues(update)
            created = [c for c in run.call_args_list if c.args[0][:3] == ["gh", "issue", "create"]]
            self.assertEqual(bool(created), expect_create)


if __name__ == "__main__":
    unittest.main()
