# Log in to __NAME__ over SSH:   EGO __NAME__
# Extra arguments run as a remote command:   EGO __NAME__ uptime
#
# Template for Egorov "python" functions. Replace __NAME__, __HOST__ and
# __KEYVAR__ (the variable holding the private key, named key-...).
# Works on Windows, macOS and Linux. The key is written to a private
# temporary file and removed once ssh has logged in, and in any case when
# ssh exits, including after Ctrl+C.
import os
import subprocess
import sys
import tempfile
import threading

HOST = "__HOST__"
KEY = """${__KEYVAR__}"""


def write_key():
    """Write the key to a temporary file only this user can read."""
    fd, path = tempfile.mkstemp(prefix="egorov-", suffix=".pem")  # mode 0600 on Linux and macOS
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(line.strip() for line in KEY.splitlines() if line.strip()) + "\n")
    if os.name == "nt":  # OpenSSH on Windows rejects keys that other accounts can read
        subprocess.run(["icacls", path, "/inheritance:r", "/grant:r", os.environ.get("USERNAME", "") + ":R"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return path


def remove(path):
    try:
        os.remove(path)
    except OSError:
        pass  # already removed, or ssh still has it open; the final cleanup tries again


def main():
    path = write_key()
    timer = threading.Timer(10, remove, [path])  # ssh only needs the key while logging in
    timer.daemon = True
    timer.start()
    try:
        return subprocess.call(["ssh", "-i", path, "-o", "IdentitiesOnly=yes",
                                "-o", "StrictHostKeyChecking=accept-new", HOST] + sys.argv[1:])
    except FileNotFoundError:
        print("ssh was not found. Install the OpenSSH client.", file=sys.stderr)
        return 127
    except KeyboardInterrupt:
        return 130
    finally:
        timer.cancel()
        remove(path)


if __name__ == "__main__":
    sys.exit(main())
