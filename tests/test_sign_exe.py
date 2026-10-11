import base64
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "packaging"))
import sign_exe

CERT = base64.b64encode(b"pfx bytes").decode()
SIGNING = {"WINDOWS_SIGNING_CERT": CERT, "WINDOWS_SIGNING_PASSWORD": "pw-secret"}


class SignExeTest(unittest.TestCase):
    def test_unsigned_without_a_certificate(self):
        with mock.patch.object(sign_exe.subprocess, "run") as run:
            self.assertFalse(sign_exe.sign("app.exe", env={}))
        run.assert_not_called()

    def test_signs_and_verifies(self):
        pfx_contents = []

        def run(cmd, **kwargs):
            if cmd[1] == "sign":
                with open(cmd[cmd.index("/f") + 1], "rb") as f:
                    pfx_contents.append(f.read())
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with (mock.patch.object(sign_exe, "find_signtool", return_value="signtool.exe"),
              mock.patch.object(sign_exe.subprocess, "run", side_effect=run) as fake):
            self.assertTrue(sign_exe.sign("app.exe", env=SIGNING))
        sign_cmd, verify_cmd = (c.args[0] for c in fake.call_args_list)
        self.assertEqual(pfx_contents, [b"pfx bytes"])
        self.assertEqual(sign_cmd[sign_cmd.index("/tr") + 1], sign_exe.DEFAULT_TIMESTAMP_URL)
        self.assertEqual(sign_cmd[sign_cmd.index("/fd") + 1], "SHA256")
        self.assertEqual(sign_cmd[-1], "app.exe")
        self.assertFalse(os.path.exists(sign_cmd[sign_cmd.index("/f") + 1]),
                         "the .pfx is deleted")
        self.assertEqual(verify_cmd, ["signtool.exe", "verify", "/pa", "app.exe"])

    def test_failure_hides_the_password(self):
        failed = subprocess.CompletedProcess([], 1, "SignTool Error: pw-secret is wrong", "")
        with (mock.patch.object(sign_exe, "find_signtool", return_value="signtool.exe"),
              mock.patch.object(sign_exe.subprocess, "run", return_value=failed),
              self.assertRaises(SystemExit) as cm):
            sign_exe.sign("app.exe", env=SIGNING)
        self.assertNotIn("pw-secret", str(cm.exception.code))
        self.assertIn("***", str(cm.exception.code))

    def test_misconfigured_signing_fails(self):
        cases = (({"WINDOWS_SIGNING_CERT": CERT}, "WINDOWS_SIGNING_PASSWORD"),
                 ({**SIGNING, "WINDOWS_SIGNING_CERT": "not base64!"}, "base64"))
        for env, why in cases:
            with self.subTest(why), self.assertRaises(SystemExit) as cm:
                sign_exe.sign("app.exe", env=env)
            self.assertIn(why, str(cm.exception.code))
        with (mock.patch.object(sign_exe, "find_signtool", return_value=None),
              self.assertRaises(SystemExit) as cm):
            sign_exe.sign("app.exe", env=SIGNING)
        self.assertIn("signtool", str(cm.exception.code))

    def test_finds_the_newest_sdk(self):
        with tempfile.TemporaryDirectory() as root:
            for version in ("10.0.19041.0", "10.0.22621.0", "10.0.9.0"):
                folder = os.path.join(root, "Windows Kits", "10", "bin", version, "x64")
                os.makedirs(folder)
                open(os.path.join(folder, "signtool.exe"), "wb").close()
            with (mock.patch.object(sign_exe.shutil, "which", return_value=None),
                  mock.patch.dict(sign_exe.os.environ, {"ProgramFiles(x86)": root})):
                found = sign_exe.find_signtool()
        self.assertIn("10.0.22621.0", found)


if __name__ == "__main__":
    unittest.main()
