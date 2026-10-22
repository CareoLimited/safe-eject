#!/usr/bin/env python3
"""
SafeEject unit and integration tests
"""

import unittest
import json
import io
import os
import plistlib
import signal
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import safe_eject


class IsolatedTestCase(unittest.TestCase):
    """No test may run a real command, send a signal or read real disk info."""

    def setUp(self):
        for target in ('subprocess.run', 'os.kill', 'os.statvfs', 'os.listdir', 'os.path.exists', 'os.path.ismount'):
            patcher = patch('safe_eject.' + target, side_effect=AssertionError('Real call blocked: ' + target))
            mock = patcher.start()
            self.addCleanup(patcher.stop)
            setattr(self, target.replace('.', '_'), mock)
        patcher = patch('safe_eject.os.path.realpath', side_effect=lambda path: path)
        patcher.start()
        self.addCleanup(patcher.stop)


class TestProcessClassification(IsolatedTestCase):
    def test_ai_processes(self):
        cat_id, label, is_sys = safe_eject.classify_process("Claude", 2048)
        self.assertEqual(cat_id, "ai")
        self.assertFalse(is_sys)

        cat_id, _, _ = safe_eject.classify_process("cursor-agent", 3000)
        self.assertEqual(cat_id, "ai")

        cat_id, _, _ = safe_eject.classify_process("SkyComputerUseService", 4000)
        self.assertEqual(cat_id, "ai")

    def test_dev_processes(self):
        cat_id, _, is_sys = safe_eject.classify_process("node", 1234)
        self.assertEqual(cat_id, "dev")
        self.assertFalse(is_sys)

        cat_id, _, _ = safe_eject.classify_process("python3", 5678)
        self.assertEqual(cat_id, "dev")

        cat_id, _, _ = safe_eject.classify_process("git", 9999)
        self.assertEqual(cat_id, "dev")

    def test_terminal_processes(self):
        cat_id, _, _ = safe_eject.classify_process("zsh", 4321)
        self.assertEqual(cat_id, "terminal")

        cat_id, _, _ = safe_eject.classify_process("bash", 4322)
        self.assertEqual(cat_id, "terminal")

    def test_system_processes(self):
        cat_id, _, is_sys = safe_eject.classify_process("mds", 88)
        self.assertEqual(cat_id, "system")
        self.assertTrue(is_sys)

        cat_id, _, is_sys = safe_eject.classify_process("fseventsd", 99)
        self.assertTrue(is_sys)

        # PID <= 100 always counts as a system process
        cat_id, _, is_sys = safe_eject.classify_process("unknown_daemon", 12)
        self.assertTrue(is_sys)

    def test_short_keywords_do_not_match_inside_words(self):
        cases = {"Google Chrome": "app", "Photoshop": "app", "Safari": "app",
                 "Cursor Helper (Renderer)": "ai", "LM Studio": "ai", "zsh": "terminal",
                 "python3.12": "dev", "com.docker.backend": "dev", "git-remote-https": "dev",
                 "QuickLookUIService": "system"}
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(safe_eject.classify_process(name, 5000)[0], expected)


class TestProcessOrigin(IsolatedTestCase):
    def test_display_name(self):
        self.assertEqual(safe_eject.display_name('/Users/me/.local/share/claude/versions/2.1.3'), 'claude')
        self.assertEqual(safe_eject.display_name('-zsh'), 'zsh')
        self.assertEqual(safe_eject.display_name('/opt/homebrew/bin/node'), 'node')

    def test_app_name_uses_the_outermost_bundle(self):
        path = '/Applications/Cursor.app/Contents/Frameworks/Cursor Helper (Plugin).app/Contents/MacOS/Cursor Helper (Plugin)'
        self.assertEqual(safe_eject.app_name(path), 'Cursor')
        self.assertIsNone(safe_eject.app_name('/opt/homebrew/bin/node'))

    def test_origin_skips_shells_and_finds_the_app(self):
        table = {
            500: (400, '/opt/homebrew/bin/node'),
            400: (300, '/Users/me/.local/share/claude/versions/2.1.3'),
            300: (200, '-zsh'),
            200: (100, '/usr/bin/login'),
            100: (1, '/Applications/iTerm.app/Contents/MacOS/iTerm2'),
        }
        self.assertEqual(safe_eject.describe_origin(500, table), {'app': 'iTerm', 'own_app': False, 'parent': 'claude'})
        self.assertEqual(safe_eject.describe_origin(300, table), {'app': 'iTerm', 'own_app': False, 'parent': None})
        self.assertEqual(safe_eject.describe_origin(100, table), {'app': 'iTerm', 'own_app': True, 'parent': None})

    def test_tool_inside_its_own_app_reports_the_app_it_runs_in(self):
        table = {
            700: (300, '/Volumes/X/claude-code/2.1.304/abc/claude.app/Contents/MacOS/claude'),
            300: (100, '-zsh'),
            100: (1, '/Applications/iTerm.app/Contents/MacOS/iTerm2'),
            800: (900, '/Applications/Claude.app/Contents/Frameworks/Claude Helper.app/Contents/MacOS/Claude Helper'),
            900: (1, '/Applications/Claude.app/Contents/MacOS/Claude'),
        }
        self.assertEqual(safe_eject.describe_origin(700, table), {'app': 'iTerm', 'own_app': False, 'parent': None})
        self.assertEqual(safe_eject.describe_origin(800, table), {'app': 'Claude', 'own_app': True, 'parent': None})
        # A tool shipped inside another app is not "part of" that app
        table[650] = (300, '/Applications/Xcode.app/Contents/Developer/Library/Frameworks/Python3.framework/'
                           'Versions/3.9/Resources/Python.app/Contents/MacOS/Python')
        self.assertEqual(safe_eject.describe_origin(650, table), {'app': 'iTerm', 'own_app': False, 'parent': None})
        # A process started by such a tool names the tool and the app it runs in
        table[600] = (700, '/opt/homebrew/bin/python3.12')
        self.assertEqual(safe_eject.describe_origin(600, table), {'app': 'iTerm', 'own_app': False, 'parent': 'claude'})

    def test_origin_survives_missing_and_looping_entries(self):
        self.assertEqual(safe_eject.describe_origin(42, {}), {'app': None, 'own_app': False, 'parent': None})
        loop = {10: (11, '/bin/a'), 11: (10, '/bin/b')}
        self.assertEqual(safe_eject.describe_origin(10, loop)['parent'], 'b')

    def test_lock_kinds(self):
        cases = [(('cwd', 'DIR', '/Volumes/T/a'), 'cwd'), (('txt', 'REG', '/Volumes/T/bin', '/Volumes/T/bin'), 'program'),
                 (('txt', 'REG', '/Volumes/T/lib/libfoo.dylib'), 'program'),
                 (('txt', 'REG', '/Volumes/T/AppSupport/Cookies'), 'mapped'),
                 (('txt', 'REG', '/Volumes/T/AppSupport/state.db'), 'database'),
                 (('4', 'REG', '/Volumes/T/db.sqlite-wal'), 'database'), (('5', 'DIR', '/Volumes/T/src'), 'directory'),
                 (('6', 'REG', '/Volumes/T/a.txt'), 'file')]
        for args, expected in cases:
            with self.subTest(args=args):
                self.assertEqual(safe_eject.lock_kind(*args), expected)


class TestVolumeListing(IsolatedTestCase):
    def test_list_all_volumes(self):
        info = {'VolumeName': 'Test Disk', 'TotalSize': 1000, 'FreeSpace': 500,
                'Internal': False, 'DeviceIdentifier': 'disk99s1'}

        def command(args, **kwargs):
            if args == ['mount']:
                return subprocess.CompletedProcess(args, 0, '/dev/disk99s1 on /Volumes/Test (apfs, local)\n')
            if args[:3] == ['diskutil', 'info', '-plist']:
                return subprocess.CompletedProcess(args, 0, plistlib.dumps(info))
            raise AssertionError('Unexpected command: ' + repr(args))

        self.subprocess_run.side_effect = command
        self.os_listdir.side_effect = None
        self.os_listdir.return_value = []
        with patch('safe_eject.os.path.exists', side_effect=lambda p: p in ('/', '/Volumes', '/Volumes/Test')):
            vols = safe_eject.list_all_volumes()
        self.assertIsInstance(vols, list)
        self.assertGreater(len(vols), 0)

        # At least one volume is / or under /Volumes/
        mount_points = [v["mount_point"] for v in vols]
        self.assertTrue(any(mp.startswith("/Volumes/") or mp == "/" for mp in mount_points))

        sample = vols[0]
        for key in ["name", "mount_point", "fs_type", "total_size", "used_percent"]:
            self.assertIn(key, sample)


class TestHttpHandlerDirect(IsolatedTestCase):
    def _execute_request(self, method: str, path: str, body: bytes = b"{}", headers=None, backend=None):
        handler = object.__new__(safe_eject.SafeEjectHandler)
        handler.server = SimpleNamespace(server_address=('127.0.0.1', 5005),
                                         allowed_hosts={'localhost', '127.0.0.1'}, csrf_token='test-token',
                                         backend=backend or safe_eject.LiveBackend())
        handler.command = method
        handler.path = path
        handler.requestline = f"{method} {path} HTTP/1.1"
        handler.request_version = "HTTP/1.1"
        handler.headers = {'Host': '127.0.0.1:5005', 'Content-Length': str(len(body)),
                           'Content-Type': 'application/json', 'X-SafeEject-Token': 'test-token'}
        handler.headers.update(headers or {})
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()

        if method == "GET":
            handler.do_GET()
        elif method == "POST":
            handler.do_POST()
        elif method == 'OPTIONS':
            handler.do_OPTIONS()

        handler.wfile.seek(0)
        output = handler.wfile.read().decode("utf-8", errors="replace")
        return output

    def test_api_status(self):
        out = self._execute_request("GET", "/api/status")
        self.assertIn("200 OK", out)
        self.assertIn("Cache-Control: no-cache, no-store, must-revalidate", out)
        self.assertIn('"status": "running"', out)
        self.assertIn('"demo": false', out)

    def test_api_volumes(self):
        with patch('safe_eject.list_all_volumes', return_value=[]):
            out = self._execute_request("GET", "/api/volumes")
        self.assertIn("200 OK", out)
        self.assertIn('"volumes":', out)

    def test_api_locks_missing_param(self):
        out = self._execute_request("GET", "/api/locks")
        self.assertIn("400 Bad Request", out)

    def test_session_token_is_local_only(self):
        out = self._execute_request('GET', '/api/session')
        self.assertIn('"token": "test-token"', out)
        self.assertNotIn('Access-Control-Allow-Origin', out)
        for headers in ({'Host': 'attacker.example:5005'}, {'Origin': 'https://attacker.example'},
                        {'Host': '127.0.0.1:5006'}, {'Origin': 'null'}, {'Sec-Fetch-Site': 'cross-site'}):
            with self.subTest(headers=headers):
                out = self._execute_request('GET', '/api/session', headers=headers)
                self.assertIn('403 Forbidden', out)
                self.assertNotIn('test-token', out)

    def test_cross_origin_post_and_preflight_are_rejected(self):
        with patch('safe_eject.terminate_process') as terminate:
            for method in ('POST', 'OPTIONS'):
                out = self._execute_request(method, '/api/kill', b'{"pid":99999}',
                                            {'Origin': 'https://untrusted.example', 'Content-Type': 'text/plain'})
                self.assertIn('403 Forbidden', out)
            terminate.assert_not_called()

    def test_post_requires_token_and_json(self):
        for headers, status in (({'X-SafeEject-Token': ''}, '403 Forbidden'),
                                ({'X-SafeEject-Token': 'wrong'}, '403 Forbidden'),
                                ({'X-SafeEject-Token': 'ş'}, '403 Forbidden'),
                                ({'Content-Type': 'text/plain'}, '415 Unsupported Media Type')):
            with self.subTest(headers=headers):
                self.assertIn(status, self._execute_request('POST', '/api/kill', b'{"pid":99999}', headers))

    def test_valid_same_origin_post(self):
        with patch('safe_eject.terminate_process', return_value=(True, 'closed')) as terminate:
            out = self._execute_request('POST', '/api/kill', b'{"pid":99999,"force":false}',
                                        {'Origin': 'http://127.0.0.1:5005'})
            self.assertIn('200 OK', out)
            terminate.assert_called_once_with(99999, force=False)

    def test_invalid_json_and_body_lengths(self):
        for body in (b'[]', b'null', b'123', b'"value"', b'{', b'\xff', b'[' * 1100 + b']' * 1100):
            with self.subTest(body=body):
                self.assertIn('400 Bad Request', self._execute_request('POST', '/api/kill', body))
        for length in ('-1', 'bad', '0', '999999'):
            with self.subTest(length=length):
                out = self._execute_request('POST', '/api/kill', headers={'Content-Length': length})
                self.assertIn('413 Request Entity Too Large' if length == '999999' else '400 Bad Request', out)

    def test_payload_types_are_checked(self):
        cases = [('/api/kill', {'pid': True}), ('/api/kill', {'pid': 99999, 'force': 'false'}),
                 ('/api/kill_all', {'volume': 123}), ('/api/eject', {'volume': []}),
                 ('/api/eject', {'volume': '  '}), ('/api/kill_all', {'volume': '/Volumes/Test', 'include_system': 'false'}),
                 ('/api/spotlight', {'volume': '/Volumes/Test'}), ('/api/spotlight', {'volume': '/Volumes/Test', 'enabled': 'no'}),
                 ('/api/reveal', {'path': 7}), ('/api/reveal', {})]
        for path, body in cases:
            with self.subTest(path=path, body=body):
                self.assertIn('400 Bad Request', self._execute_request('POST', path, json.dumps(body).encode()))

    def test_scan_failure_is_not_a_clean_result(self):
        with patch('safe_eject.get_disk_locks', side_effect=safe_eject.LockScanError('mock failure')):
            out = self._execute_request('GET', '/api/locks?volume=/Volumes/Test')
            self.assertIn('503 Service Unavailable', out)
            self.assertIn('scan_failed', out)
            self.assertNotIn('"locks": []', out)

    def test_demo_backend_is_served_without_real_commands(self):
        demo = safe_eject.DemoBackend()
        self.assertIn('"demo": true', self._execute_request('GET', '/api/status', backend=demo))
        out = self._execute_request('GET', '/api/locks?volume=/Volumes/T7%20Shield', backend=demo)
        self.assertIn('200 OK', out)
        self.assertIn('"kind": "cwd"', out)
        self.subprocess_run.assert_not_called()
        self.os_kill.assert_not_called()


class TestLockScanning(IsolatedTestCase):
    def scan(self, result, table=None):
        self.subprocess_run.side_effect = None
        self.subprocess_run.return_value = result
        self.os_kill.side_effect = None
        with patch('safe_eject.os.path.exists', return_value=True), \
                patch('safe_eject.get_process_table', return_value=table or {}):
            return safe_eject.get_disk_locks('/Volumes/Test')

    def test_no_matches_is_a_successful_empty_scan(self):
        self.assertEqual(self.scan(subprocess.CompletedProcess([], 1, '', '')), [])

    def test_command_errors_and_partial_results_raise(self):
        for result in (subprocess.CompletedProcess([], 2, '', 'failure'),
                       subprocess.CompletedProcess([], 1, 'p99999\ncnode\n', 'WARNING: incomplete'),
                       subprocess.CompletedProcess([], 0, '', 'WARNING: permission denied')):
            with self.subTest(result=result), self.assertRaises(safe_eject.LockScanError):
                self.scan(result)

    def test_missing_command_and_timeout_raise(self):
        for error in (FileNotFoundError('mock'), subprocess.TimeoutExpired('lsof', 10)):
            self.subprocess_run.side_effect = error
            with patch('safe_eject.os.path.exists', return_value=True), self.assertRaises(safe_eject.LockScanError):
                safe_eject.get_disk_locks('/Volumes/Test')

    def test_missing_path_raises_before_running_lsof(self):
        with patch('safe_eject.os.path.exists', return_value=False), self.assertRaises(safe_eject.LockScanError):
            safe_eject.get_disk_locks('/Volumes/Missing')
        self.subprocess_run.assert_not_called()

    def test_parent_shell_is_visible_and_self_is_excluded(self):
        parent = os.getppid()
        output = f'p{parent}\ncbash\nu0\nn/Volumes/Test\np{os.getpid()}\ncpython3\nn/Volumes/Test\n'
        locks = self.scan(subprocess.CompletedProcess([], 0, output, ''))
        self.assertEqual([lock['pid'] for lock in locks], [parent])
        self.assertEqual(locks[0]['files'], [{'path': '/Volumes/Test', 'kind': 'file'}])

    def test_lock_kinds_names_and_origin_are_reported(self):
        output = ('p99999\nc2.1.3\nu0\nfcwd\ntDIR\nn/Volumes/Test/app\n'
                  'f7\ntREG\nn/Volumes/Test/app/db.sqlite-wal\nf8\ntREG\nn/Users/me/elsewhere.txt\n')
        table = {99999: (500, '/Users/me/.local/share/claude/versions/2.1.3'),
                 500: (1, '/Applications/iTerm.app/Contents/MacOS/iTerm2')}
        lock = self.scan(subprocess.CompletedProcess([], 0, output, ''), table)[0]
        self.assertEqual(lock['name'], 'claude')
        self.assertEqual(lock['category_id'], 'ai')
        self.assertEqual(lock['origin'], {'app': 'iTerm', 'own_app': False, 'parent': None})
        self.assertEqual(lock['kinds'], ['cwd', 'database'])
        self.assertEqual(lock['files'], [{'path': '/Volumes/Test/app', 'kind': 'cwd'},
                                         {'path': '/Volumes/Test/app/db.sqlite-wal', 'kind': 'database'}])


class TestTermination(IsolatedTestCase):
    def test_success_waits_for_exit(self):
        self.os_kill.side_effect = [None, None, ProcessLookupError()]
        with patch('safe_eject.time.sleep'):
            success, _ = safe_eject.terminate_process(99999)
        self.assertTrue(success)
        self.assertEqual(self.os_kill.call_args_list[0].args, (99999, signal.SIGTERM))
        self.assertEqual(len(self.os_kill.call_args_list), 3)

    def test_timeout_does_not_report_termination(self):
        self.os_kill.side_effect = None
        with patch('safe_eject.time.monotonic', side_effect=[0, safe_eject.PROCESS_EXIT_TIMEOUT]), patch('safe_eject.time.sleep') as sleep:
            success, message = safe_eject.terminate_process(99999, force=True)
        self.assertFalse(success)
        self.assertIn("didn't exit", message)
        sleep.assert_not_called()
        self.assertEqual(self.os_kill.call_args_list[0].args, (99999, signal.SIGKILL))

    def test_root_and_self_are_protected(self):
        for pid in (0, 1, os.getpid()):
            self.assertFalse(safe_eject.terminate_process(pid)[0])
        self.os_kill.assert_not_called()

    def test_scan_failure_prevents_bulk_termination(self):
        with patch('safe_eject.get_disk_locks', side_effect=safe_eject.LockScanError('mock')):
            result = safe_eject.terminate_all_locks('/Volumes/Test')
        self.assertFalse(result['success'])
        self.assertEqual(result['killed_count'], 0)
        self.os_kill.assert_not_called()

    def test_system_root_cannot_be_bulk_terminated(self):
        with patch('safe_eject.get_disk_locks') as scan:
            result = safe_eject.terminate_all_locks('/')
        self.assertFalse(result['success'])
        scan.assert_not_called()
        self.os_kill.assert_not_called()

    def test_bulk_counts_only_confirmed_exits(self):
        locks = [{'pid': 99999, 'name': 'node', 'is_system': False},
                 {'pid': 99998, 'name': 'mds', 'is_system': True}]
        with patch('safe_eject.get_disk_locks', return_value=locks), patch('safe_eject.terminate_process', return_value=(False, 'timeout')) as terminate:
            result = safe_eject.terminate_all_locks('/Volumes/Test')
        self.assertFalse(result['success'])
        self.assertEqual((result['killed_count'], result['failed_count'], result['skipped_count']), (0, 1, 1))
        terminate.assert_called_once_with(99999, force=False)


class TestEjection(IsolatedTestCase):
    def test_invalid_targets_do_not_run_disk_commands(self):
        for path in ('/', '/Volumes/Missing'):
            with patch('safe_eject.os.path.ismount', return_value=False):
                self.assertFalse(safe_eject.safe_eject_volume(path)['success'])
        self.subprocess_run.assert_not_called()

    def test_scan_failure_prevents_sync_and_eject(self):
        with patch('safe_eject.os.path.ismount', return_value=True), patch('safe_eject.get_disk_locks', side_effect=safe_eject.LockScanError('mock')):
            result = safe_eject.safe_eject_volume('/Volumes/Test')
        self.assertEqual(result['stage'], 'scan_failed')
        self.subprocess_run.assert_not_called()

    def test_remaining_lock_prevents_disk_commands(self):
        with patch('safe_eject.os.path.ismount', return_value=True), patch('safe_eject.get_disk_locks', return_value=[{'pid': 99999}]):
            result = safe_eject.safe_eject_volume('/Volumes/Test')
        self.assertEqual(result['stage'], 'locks_detected')
        self.subprocess_run.assert_not_called()

    def test_system_services_do_not_block_eject(self):
        self.subprocess_run.side_effect = [subprocess.CompletedProcess(['sync'], 0),
                                           subprocess.CompletedProcess([], 0, 'ejected', '')]
        with patch('safe_eject.os.path.ismount', return_value=True), \
                patch('safe_eject.get_disk_locks', return_value=[{'pid': 389, 'is_system': True}]):
            self.assertTrue(safe_eject.safe_eject_volume('/Volumes/Test')['success'])

    def test_successful_eject_uses_only_mock_commands(self):
        self.subprocess_run.side_effect = [subprocess.CompletedProcess(['sync'], 0),
                                           subprocess.CompletedProcess([], 0, 'ejected', '')]
        with patch('safe_eject.os.path.ismount', return_value=True), patch('safe_eject.get_disk_locks', return_value=[]):
            result = safe_eject.safe_eject_volume('/Volumes/Test')
        self.assertTrue(result['success'])
        self.assertEqual([call.args[0] for call in self.subprocess_run.call_args_list], [['sync'], ['diskutil', 'eject', '/Volumes/Test']])

    def test_sync_failure_prevents_eject(self):
        self.subprocess_run.side_effect = subprocess.CalledProcessError(1, 'sync')
        with patch('safe_eject.os.path.ismount', return_value=True), patch('safe_eject.get_disk_locks', return_value=[]):
            result = safe_eject.safe_eject_volume('/Volumes/Test')
        self.assertFalse(result['success'])
        self.assertEqual(len(self.subprocess_run.call_args_list), 1)


class TestSpotlightAndFinder(IsolatedTestCase):
    def test_status_is_parsed(self):
        for stdout, expected in (('/Volumes/T:\n\tIndexing enabled. \n', True),
                                 ('/Volumes/T:\n\tIndexing disabled.\n', False),
                                 ('/Volumes/T:\n\tError: unknown indexing state.\n', None)):
            self.subprocess_run.side_effect = None
            self.subprocess_run.return_value = subprocess.CompletedProcess([], 0, stdout, '')
            with self.subTest(stdout=stdout):
                self.assertEqual(safe_eject.get_spotlight_status('/Volumes/T'), expected)

    def test_only_external_mounts_can_be_changed(self):
        with patch('safe_eject.os.path.ismount', return_value=True):
            for path in ('/', '/Users/me'):
                with self.subTest(path=path):
                    self.assertFalse(safe_eject.set_spotlight(path, False)['success'])
        self.subprocess_run.assert_not_called()

    def test_falls_back_to_admin_prompt_and_reports_cancel(self):
        calls = []

        def command(args, **kwargs):
            calls.append(args)
            if args[:2] == ['mdutil', '-i']:
                return subprocess.CompletedProcess(args, 0, '', '')
            if args[:2] == ['mdutil', '-s']:
                return subprocess.CompletedProcess(args, 0, 'Indexing enabled.', '')
            if args[0] == 'osascript':
                return subprocess.CompletedProcess(args, 1, '', 'execution error: User canceled. (-128)')
            raise AssertionError('Unexpected command: ' + repr(args))

        self.subprocess_run.side_effect = command
        with patch('safe_eject.os.path.ismount', return_value=True):
            result = safe_eject.set_spotlight('/Volumes/T', False)
        self.assertFalse(result['success'])
        self.assertIn('Cancelled', result['message'])
        # The path is passed as an argument, never inside the script text
        self.assertEqual(calls[-1][-2:], ['off', '/Volumes/T'])
        self.assertTrue(all('/Volumes/T' not in part for part in calls[-1][:-1]))

    def test_reveal_only_allows_mounted_disks(self):
        for path in ('/etc/passwd', '/Users/me/file'):
            with self.subTest(path=path):
                self.assertFalse(safe_eject.reveal_in_finder(path)['success'])
        self.subprocess_run.assert_not_called()


class TestDemoBackend(IsolatedTestCase):
    def test_quit_force_quit_and_eject_flow(self):
        demo = safe_eject.DemoBackend()
        volume = '/Volumes/T7 Shield'
        first = demo.kill_all(volume, force=False, include_system=False)
        self.assertEqual((first['failed_count'], first['skipped_count']), (1, 1))
        self.assertEqual(demo.eject(volume)['stage'], 'locks_detected')
        self.assertTrue(demo.kill_all(volume, force=True, include_system=False)['success'])
        # Only the Spotlight service is left, and it doesn't block
        self.assertTrue(demo.eject(volume)['success'])
        self.assertNotIn(volume, [v['mount_point'] for v in demo.volumes()])
        self.assertFalse(demo.eject('/')['success'])


class TestCli(IsolatedTestCase):
    def args(self, **values):
        defaults = dict(list=False, scan=None, eject=None, kill_all=None, force=False, yes=False)
        defaults.update(values)
        return SimpleNamespace(**defaults)

    def test_kill_all_without_tty_or_yes_is_cancelled(self):
        locks = [{'pid': 99999, 'name': 'node', 'user': 'me', 'category_label': 'Developer tool',
                  'origin': {}, 'file_count': 0, 'files': []}]
        with patch('safe_eject.get_disk_locks', return_value=locks), \
                patch('safe_eject.terminate_all_locks') as terminate, \
                patch('safe_eject.sys.stdin', io.StringIO()), \
                patch('sys.stdout', io.StringIO()), patch('sys.stderr', io.StringIO()):
            code = safe_eject.run_cli(self.args(kill_all='/Volumes/Test'))
        self.assertEqual(code, 1)
        terminate.assert_not_called()

    def test_failed_eject_returns_nonzero(self):
        with patch('safe_eject.get_disk_locks', return_value=[]), \
                patch('safe_eject.safe_eject_volume', return_value={'success': False, 'message': 'busy'}), \
                patch('sys.stdout', io.StringIO()):
            self.assertEqual(safe_eject.run_cli(self.args(eject='/Volumes/Test')), 1)


if __name__ == "__main__":
    unittest.main()
