import contextlib
import errno
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import install
from wind_bridge.backend import Backend
from wind_bridge.common import Problem, ROOT
from wind_bridge.native_lock import native_lock
from wind_bridge.platform_support import discover_module_dir, inspect_platform, venv_python
from wind_bridge.storage import Store


class DesktopPolicyTests(unittest.TestCase):
    def linux(self, release, supplemental=""):
        return inspect_platform(system="Linux", machine="aarch64", release=release, supplemental=supplemental)

    def test_requested_desktops_are_recognized_without_claiming_sdk_validation(self):
        cases = [
            ({"ID": "nfsdesktop", "NAME": "方德桌面操作系统", "VERSION_ID": "5.0"}, "nfs5"),
            ({"ID": "uos", "PRETTY_NAME": "UnionTech OS Desktop 20 Pro", "VERSION_ID": "20"}, "uos20"),
            ({"ID": "kylin", "PRETTY_NAME": "银河麒麟桌面操作系统 V10 (SP1)", "VERSION_ID": "v10"}, "kylin_v10_sp1"),
        ]
        for release, profile in cases:
            with self.subTest(profile=profile):
                result = self.linux(release)
                self.assertTrue(result["supported"])
                self.assertEqual(result["profile"], profile)
                self.assertTrue(result["policy_match_is_not_native_validation"])
        for system in ("Windows", "Darwin"):
            self.assertTrue(inspect_platform(system=system, machine="AMD64")["supported"])

    def test_server_editions_are_rejected_even_with_desktop_tokens(self):
        for release in [
            {"ID": "uos", "NAME": "UOS Server", "VERSION_ID": "20", "VARIANT": "Desktop installed"},
            {"ID": "kylin", "NAME": "银河麒麟服务器 V10 SP1", "VARIANT": "Desktop"},
            {"ID": "nfsdesktop", "NAME": "NFS Desktop Server", "VERSION_ID": "5.0"},
        ]:
            self.assertEqual(self.linux(release)["reason"], "linux_server_editions_not_supported")

    def test_other_distributions_versions_and_ambiguous_editions_fail_closed(self):
        cases = [
            {"ID": "ubuntu", "NAME": "Ubuntu Desktop", "VERSION_ID": "20.04"},
            {"ID": "deepin", "NAME": "Deepin Desktop", "VERSION_ID": "20"},
            {"ID": "openkylin", "NAME": "openKylin Desktop V10 SP1"},
            {"ID": "uos", "NAME": "UOS Desktop", "VERSION_ID": "21"},
            {"ID": "nfs", "NAME": "NFS Desktop", "VERSION_ID": "5.1"},
            {"ID": "kylin", "NAME": "Kylin Desktop V10 SP2"},
            {"ID": "kylin", "NAME": "Kylin Desktop V10 SP10"},
            {"ID": "kylin", "NAME": "Kylin Desktop V10"},
            {"ID": "uos", "NAME": "UOS", "VERSION_ID": "20"},
            {},
        ]
        for release in cases:
            with self.subTest(release=release):
                self.assertFalse(self.linux(release)["supported"])
        self.assertFalse(inspect_platform(system="FreeBSD", machine="x86_64")["supported"])

    def test_vendor_release_files_supply_desktop_and_sp1_evidence(self):
        kylin = self.linux({"ID": "kylin", "VERSION_ID": "v10"}, "[dist]\nmilestone=Desktop-V10-SP1-Release-2107")
        self.assertTrue(kylin["supported"])
        uos = self.linux({"ID": "uos", "VERSION_ID": "20"}, "[Version]\nProductType=Desktop\nMajorVersion=20")
        self.assertTrue(uos["supported"])
        self.assertFalse(self.linux({"ID": "kylin", "VERSION": "Desktop V10 SP2"}, "old=Desktop-V10-SP1")["supported"])

    def test_unsupported_installer_exits_before_creating_files_or_installing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(install, "ROOT", root), patch.object(install, "inspect_platform", return_value=self.linux({"ID": "ubuntu"})), \
                 patch.object(sys, "argv", ["install.py"]), patch.object(install.subprocess, "run") as run, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    install.main()
                self.assertEqual(raised.exception.code, 2)
                run.assert_not_called()
                self.assertEqual(list(root.iterdir()), [])

    def test_native_backend_rejects_unsupported_linux_before_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = Backend(Store(Path(directory)))
            with patch("wind_bridge.platform_support.inspect_platform", return_value=self.linux({"ID": "ubuntu"})), \
                 patch("wind_bridge.backend.subprocess.run") as run:
                with self.assertRaises(Problem) as raised:
                    backend._native("__status__", {})
                self.assertEqual(raised.exception.code, "UNSUPPORTED_PLATFORM")
                run.assert_not_called()


class SdkAndInstallerTests(unittest.TestCase):
    def test_sdk_discovery_does_not_import_and_preserves_explicit_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "WindPy.py").write_text('raise RuntimeError("must not import")', encoding="utf-8")
            self.assertEqual(discover_module_dir(environ={}, system="Windows", search_paths=[directory]), root)
            missing = root / "explicit missing"
            self.assertEqual(discover_module_dir(environ={"WIND_TERMINAL_MODULE_DIR": str(missing)}, system="Windows", search_paths=[directory]), missing)
            self.assertIsNone(discover_module_dir(environ={}, system="Linux", search_paths=[]))
            self.assertEqual(venv_python(root, "Windows"), root / "Scripts/python.exe")
            self.assertEqual(venv_python(root, "Linux"), root / "bin/python")

    def test_windows_install_uses_scripts_python_no_symlinks_and_sdk_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sdk = root / "官方 SDK"
            sdk.mkdir()
            (sdk / "WindPy.py").write_text("# fixture", encoding="utf-8")
            def create_venv(environment):
                (environment / "Scripts").mkdir(parents=True)
                (environment / "Scripts/python.exe").touch()
            fake_run = types.SimpleNamespace(stdout='{"fixture":true}\n')
            with patch.object(install, "ROOT", root), patch.object(install, "inspect_platform", return_value={"supported": True, "profile": "windows"}), \
                 patch.object(install.platform, "system", return_value="Windows"), \
                 patch.object(sys, "argv", ["install.py", "--wind-module-dir", str(sdk)]), \
                 patch.object(install.venv, "EnvBuilder") as builder, patch.object(install.subprocess, "run", return_value=fake_run) as run, \
                 contextlib.redirect_stdout(io.StringIO()):
                builder.return_value.create.side_effect = create_venv
                install.main()
                builder.assert_called_once_with(with_pip=True, symlinks=False)
                self.assertTrue(all(call.args[0][0].endswith("Scripts/python.exe") for call in run.call_args_list))
                configs = [call for call in run.call_args_list if "--format" in call.args[0]]
                self.assertEqual(len(configs), 3)
                self.assertTrue(all(str(sdk) in call.args[0] and call.kwargs["env"]["PYTHONUTF8"] == "1" for call in configs))
                self.assertTrue((root / "client-configs/workbuddy.json").is_file())

    def test_generated_json_and_toml_preserve_unicode_paths_and_utf8(self):
        import tomllib
        with tempfile.TemporaryDirectory(prefix="wind 中文 ") as directory:
            sdk = Path(directory)
            (sdk / "WindPy.py").touch()
            for fmt in ("json", "workbuddy", "codex"):
                result = subprocess.run([sys.executable, str(ROOT / "client_config.py"), "--python", sys.executable,
                                         "--format", fmt, "--wind-module-dir", str(sdk)],
                                        text=True, encoding="utf-8", capture_output=True, check=True)
                if fmt == "codex":
                    entry = tomllib.loads(result.stdout)["mcp_servers"]["wind_terminal_api"]
                else:
                    entry = json.loads(result.stdout)["mcpServers"]["wind_terminal_api"]
                self.assertEqual(entry["env"]["WIND_TERMINAL_MODULE_DIR"], str(sdk))
                self.assertEqual(entry["env"]["PYTHONUTF8"], "1")
                self.assertNotIn("WIND_API_KEY", entry["env"])

    def test_worker_handles_missing_tzset_unicode_logs_and_original_nulls(self):
        fixture = '''import datetime
from types import SimpleNamespace
class FixtureWind:
    def start(self, **kwargs):
        print("模拟SDK日志：中文")
        return SimpleNamespace(ErrorCode=0)
    def isconnected(self): return True
    def stop(self): pass
    def wsd(self, **kwargs):
        return SimpleNamespace(ErrorCode=0, Codes=["TEST00.SHF"], Fields=["CLOSE"], Times=[datetime.datetime(2026,9,29)], Data=[[float("nan")]])
w=FixtureWind()
'''
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "WindPy.py").write_text(fixture, encoding="utf-8")
            script = "import time; delattr(time, 'tzset') if hasattr(time, 'tzset') else None; from wind_bridge.worker import main; main()"
            args = {"method": "wsd", "arguments": {"codes": "TEST00.SHF", "fields": "close", "beginTime": "2026-09-29", "endTime": "2026-09-29"}}
            env = {**os.environ, "WIND_TERMINAL_MODULE_DIR": directory, "PYTHONUTF8": "1"}
            result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, input=json.dumps(args), env=env,
                                    capture_output=True, text=True, encoding="utf-8", check=True)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["raw"]["Data"], [[None]])
            self.assertIn("模拟SDK日志：中文", result.stderr)
            self.assertNotIn("模拟SDK日志", result.stdout)


class NativeLockTests(unittest.TestCase):
    def test_lock_is_exclusive_and_released_after_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "native.lock"
            with self.assertRaisesRegex(RuntimeError, "fixture"):
                with native_lock(path):
                    with self.assertRaises(Problem) as raised:
                        with native_lock(path, timeout=0.02):
                            self.fail("concurrent lock acquired")
                    self.assertEqual(raised.exception.code, "WIND_BUSY")
                    raise RuntimeError("fixture")
            with native_lock(path, timeout=0.02):
                self.assertGreaterEqual(path.stat().st_size, 1)

    def test_windows_locks_and_unlocks_same_first_byte(self):
        from wind_bridge import native_lock as module
        calls = []
        fake = types.SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0,
                                     locking=lambda fd, mode, count: calls.append((os.lseek(fd, 0, os.SEEK_CUR), mode, count)))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "native.lock"
            with patch.object(module.os, "name", "nt"), patch.dict(sys.modules, {"msvcrt": fake}):
                with native_lock(path):
                    pass
        self.assertEqual(calls, [(0, 2, 1), (0, 0, 1)])


if __name__ == "__main__":
    unittest.main()
