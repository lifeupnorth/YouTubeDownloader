"""Signs the Windows .exe with a code-signing certificate, if one is set up.

The "Windows app" workflow runs it after the build. It reads:
  WINDOWS_SIGNING_CERT      the certificate with its private key: the .pfx
                            file, base64-encoded (e.g. base64 -w0 cert.pfx)
  WINDOWS_SIGNING_PASSWORD  the .pfx password
  SIGNING_TIMESTAMP_URL     optional; an RFC 3161 timestamp server
Without WINDOWS_SIGNING_CERT it does nothing, so the .exe stays unsigned.

    python packaging/sign_exe.py dist/YT-Downloader.exe
"""
import base64
import binascii
import glob
import os
import shutil
import subprocess
import sys
import tempfile

DEFAULT_TIMESTAMP_URL = "http://timestamp.digicert.com"


def _sdk_version(path):
    # .../Windows Kits/10/bin/10.0.22621.0/x64/signtool.exe
    folder = os.path.basename(os.path.dirname(os.path.dirname(path)))
    return tuple(int(p) if p.isdigit() else 0 for p in folder.split("."))


def find_signtool():
    """signtool.exe from PATH or the newest Windows SDK, or None."""
    found = shutil.which("signtool")
    if found:
        return found
    kits = os.path.join(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"),
                        "Windows Kits", "10", "bin")
    candidates = glob.glob(os.path.join(kits, "*", "x64", "signtool.exe"))
    return max(candidates, key=_sdk_version, default=None)


def sign(exe, env=os.environ):
    """Signs and verifies exe; False if no certificate is set up. Exits with
    a message (never containing the password) if signing was asked for but
    can't be done."""
    cert = env.get("WINDOWS_SIGNING_CERT", "").strip()
    if not cert:
        print("No WINDOWS_SIGNING_CERT: leaving the .exe unsigned.")
        return False
    password = env.get("WINDOWS_SIGNING_PASSWORD", "")
    if not password:
        sys.exit("WINDOWS_SIGNING_CERT is set but WINDOWS_SIGNING_PASSWORD isn't.")
    try:
        pfx = base64.b64decode(cert, validate=True)
    except (binascii.Error, ValueError):
        sys.exit("WINDOWS_SIGNING_CERT isn't base64 (encode the .pfx file).")
    signtool = find_signtool()
    if signtool is None:
        sys.exit("signtool.exe wasn't found (it comes with the Windows SDK).")
    timestamp = env.get("SIGNING_TIMESTAMP_URL") or DEFAULT_TIMESTAMP_URL

    with tempfile.TemporaryDirectory() as tmp:
        pfx_path = os.path.join(tmp, "cert.pfx")
        with open(pfx_path, "wb") as f:
            f.write(pfx)
        # Not check=True: its error would show the command, password included.
        run = subprocess.run([signtool, "sign", "/f", pfx_path, "/p", password,
                              "/fd", "SHA256", "/tr", timestamp, "/td", "SHA256", exe],
                             capture_output=True, text=True)
    if run.returncode:
        output = (run.stdout or "") + (run.stderr or "")
        sys.exit("Signing failed:\n" + output.replace(password, "***"))
    verify = subprocess.run([signtool, "verify", "/pa", exe], capture_output=True, text=True)
    if verify.returncode:
        sys.exit("The signature didn't verify:\n" + (verify.stdout or "") + (verify.stderr or ""))
    print(f"Signed {exe}")
    return True


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sign(sys.argv[1])
