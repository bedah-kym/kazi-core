from __future__ import annotations

import unittest

from orchestration.shell.classifier import (
    TIER_BOUNDED,
    TIER_DENIED,
    TIER_DESTRUCTIVE,
    TIER_SAFE,
    classify_command,
)


class DetectionTests(unittest.TestCase):
    def test_read_only_commands_are_safe(self):
        for command in ("ls -la", "cat /etc/hosts", "ps aux", "df -h", "echo hi"):
            self.assertEqual(classify_command(command)["tier"], TIER_SAFE, command)

    def test_destructive_tripwire(self):
        for command in (
            "rm -rf /",
            "rm -fr /tmp/x",
            "dd if=/dev/zero of=/dev/sda",
            "mkfs.ext4 /dev/sdb1",
            ":(){ :|:& };:",
            "shred /dev/sda",
        ):
            self.assertTrue(classify_command(command)["destructive"], command)
            self.assertEqual(classify_command(command)["tier"], TIER_DESTRUCTIVE, command)

    def test_windows_destructive_tripwire(self):
        for command in (
            "rmdir /s /q qa_auto_mode",
            "RD /S /Q C:\\data",
            "rd/s/q C:\\data",
            "del /s /q C:\\Users\\User\\Documents",
            "del/s/q build",
            "del /q *.*",
            "erase /s build",
            "format D: /q",
            "powershell -Command Remove-Item -Recurse -Force C:\\data",
            "powershell -NoProfile -Command \"rm -Recurse build\"",
            "Remove-Item -Rec -Fo C:\\data",
            "Get-ChildItem C:\\x -Recurse | Remove-Item -Force",
            "rm -Recurse -Force build",
            "cd build && rmdir /s /q out",
            "diskpart /s wipe.txt",
            "cipher /w:C",
            "rm -Rf /",
        ):
            for profile in ("open", "standard", "locked"):
                self.assertEqual(classify_command(command, profile)["tier"], TIER_DESTRUCTIVE, command)

    def test_windows_everyday_commands_are_not_destructive(self):
        for command in (
            "dir /s /b",
            "del notes.txt",
            "del /q notes.txt",
            "rmdir empty_folder",
            "mkdir build",
            "type README.md",
            "python -m unittest -v",
            "echo format the report",
            "git log --format c:",
            "rm -r build",
            "rm -R build",
            "git rm -r --cached .",
            "docker run --rm img pip install -r requirements.txt",
            "docker compose run --rm web pytest -r a",
            "grep 'rm' -r .",
            "cp src/rd dest -r",
            "python del.py -r",
            "zip rm.zip -r dir",
            "pip install -r requirements.txt",
            "Remove-Item notes.txt",
        ):
            self.assertFalse(classify_command(command)["destructive"], command)

    def test_network_detection(self):
        for command in ("ping -c 1 1.1.1.1", "curl https://example.com",
                        "git clone https://x/y.git", "pip install requests",
                        "ssh host", "wget https://x"):
            self.assertTrue(classify_command(command)["needs_network"], command)

    def test_git_local_command_is_not_network(self):
        self.assertFalse(classify_command("git status")["needs_network"])

    def test_root_detection(self):
        for command in ("sudo ls", "su -", "doas reboot", "chmod u+s /bin/sh"):
            self.assertTrue(classify_command(command)["needs_root"], command)

    def test_env_assignment_prefix_does_not_hide_root(self):
        self.assertTrue(classify_command("FOO=bar sudo ls")["needs_root"])


class TierProfileMatrixTests(unittest.TestCase):
    """Every tier x profile combination from roadmap §3/§4.3."""

    def test_safe_command_is_safe_everywhere(self):
        for profile in ("open", "standard", "locked"):
            self.assertEqual(classify_command("ls -la", profile)["tier"], TIER_SAFE, profile)

    def test_destructive_is_durable_everywhere(self):
        for profile in ("open", "standard", "locked"):
            self.assertEqual(classify_command("rm -rf /", profile)["tier"], TIER_DESTRUCTIVE, profile)

    def test_network_tier_by_profile(self):
        self.assertEqual(classify_command("curl https://x", "open")["tier"], TIER_SAFE)
        self.assertEqual(classify_command("curl https://x", "standard")["tier"], TIER_BOUNDED)
        self.assertEqual(classify_command("curl https://x", "locked")["tier"], TIER_DENIED)

    def test_root_tier_by_profile(self):
        self.assertEqual(classify_command("sudo ls", "open")["tier"], TIER_BOUNDED)
        self.assertEqual(classify_command("sudo ls", "standard")["tier"], TIER_DENIED)
        self.assertEqual(classify_command("sudo ls", "locked")["tier"], TIER_DENIED)

    def test_unknown_profile_falls_back_to_standard(self):
        result = classify_command("curl https://x", "bogus")
        self.assertEqual(result["profile"], "standard")
        self.assertEqual(result["tier"], TIER_BOUNDED)


class ObfuscationTests(unittest.TestCase):
    def test_obfuscated_destructive_is_a_safe_tier_but_not_unguarded(self):
        """A classifier miss is acceptable: the sandbox still bounds it.

        This documents the design (roadmap §4.3) — the Docker backend runs
        every command non-root, read-only, network-none, so a false negative
        costs nothing. The sandbox guarantees are asserted in
        ``test_shell_backends.py``.
        """
        for command in (r"r''m -rf /", r"$'\x72\x6d' -rf /",
                        "python -c \"import shutil;shutil.rmtree('/')\""):
            self.assertFalse(classify_command(command, "standard")["destructive"], command)
            self.assertEqual(classify_command(command, "standard")["tier"], TIER_SAFE, command)
