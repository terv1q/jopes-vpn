from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import io
import json
import sqlite3
import tarfile
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.error import HTTPError

from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import GetMe

from jopesvpn.health import BotHealth

DEPLOY = Path(__file__).resolve().parents[1] / "deploy" / "production"


def module(name):
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), DEPLOY / f"{name}.py"
    )
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


backup_verify = module("backup-verify")
backup_auth = module("backup-auth")
renderer = module("render-config")
monitor = module("monitor")


class HealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_readiness_expiry_and_recovery_do_not_expose_credentials(self):
        with patch("jopesvpn.health.time.monotonic", return_value=100):
            health = BotHealth(max_age=180)
            self.assertFalse(health.healthy())
            health.record_success()
            self.assertFalse(health.healthy())
            health.ready = True
            self.assertTrue(health.healthy())
        with patch("jopesvpn.health.time.monotonic", return_value=281):
            response = await health.handler(None)
            self.assertEqual(response.status, 503)
            self.assertEqual(
                set(json.loads(response.text)), {"status", "uptime_seconds"}
            )
            health.record_success()
            self.assertTrue(health.healthy())

    async def test_repeated_probe_failure_logs_once_and_recovers(self):
        health = BotHealth()
        bot = AsyncMock()
        bot.get_me.side_effect = [
            TelegramNetworkError(method=GetMe(), message="private failure detail"),
            TelegramNetworkError(method=GetMe(), message="private failure detail"),
            object(),
        ]
        with (
            patch(
                "jopesvpn.health.asyncio.sleep",
                new=AsyncMock(side_effect=[None, None, asyncio.CancelledError]),
            ),
            self.assertLogs("jopesvpn.health", level="INFO") as logs,
            self.assertRaises(asyncio.CancelledError),
        ):
            await health.probe(bot)
        self.assertEqual(sum("failed" in line for line in logs.output), 1)
        self.assertEqual(sum("recovered" in line for line in logs.output), 1)
        self.assertNotIn("private failure detail", "\n".join(logs.output))


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        database = self.root / "database.sqlite3"
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("CREATE TABLE old_records (value TEXT)")
            db.execute("INSERT INTO old_records VALUES ('retained')")
        caddy = io.BytesIO()
        with tarfile.open(fileobj=caddy, mode="w") as archive:
            info = tarfile.TarInfo("caddy/certificate.crt")
            info.size = 4
            archive.addfile(info, io.BytesIO(b"test"))
        self.files = {
            "bot-state.sqlite3": database.read_bytes(),
            "panel.dump": b"PGDMPtest",
            "caddy-data.tar": caddy.getvalue(),
            "config/.env": b"BOT_TOKEN=test",
            "config/.env.node": b"NODE=test",
            "config/Caddyfile": b"test",
            "config/docker-compose.yml": b"test",
        }

    def bundle(self, *, changed=None, unsafe=None):
        manifest = {
            "format": "jopesvpn-recovery-v1",
            "postgres_restore_verified": True,
            "sha256": {
                name: hashlib.sha256(value).hexdigest()
                for name, value in self.files.items()
            },
        }
        data = {**self.files, "manifest.json": json.dumps(manifest).encode()}
        if changed:
            data[changed] += b"tampered"
        if unsafe:
            data[unsafe] = b"unsafe"
        path = self.root / "recovery.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            for name, value in data.items():
                info = tarfile.TarInfo(name)
                info.size = len(value)
                archive.addfile(info, io.BytesIO(value))
        return path

    def test_full_bundle_verifies_and_removes_plaintext_verification_files(self):
        result = backup_verify.verify(self.bundle())
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["postgres_restore_verified"])
        self.assertEqual(list(self.root.glob("verify-*")), [])

    def test_tampered_and_traversal_archives_are_rejected(self):
        with self.assertRaises(ValueError):
            backup_verify.verify(self.bundle(changed="panel.dump"))
        for name in ("../escape", "/absolute"):
            with self.assertRaises(ValueError):
                backup_verify.verify(self.bundle(unsafe=name))

    def test_ciphertext_authentication_changes_when_data_changes(self):
        key = self.root / "key"
        cipher = self.root / "cipher"
        key.write_bytes(b"test key")
        cipher.write_bytes(b"encrypted fixture")
        digest = backup_auth.mac_for(key, cipher)
        cipher.write_bytes(b"tampered fixture")
        self.assertNotEqual(digest, backup_auth.mac_for(key, cipher))


class RenderTests(unittest.TestCase):
    def test_renderer_uses_configured_domain_and_preserves_template(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_file = root / "env"
            env_file.write_text(
                "UNRELATED_SECRET=do-not-print\nCONNECT_DOMAIN=connect.jopes.test\n"
            )
            source = DEPLOY / "hysteria2-inbound.json"
            original = source.read_bytes()
            output = root / "rendered.json"
            with patch.dict("os.environ", {}, clear=True):
                renderer.render(source, env_file, output)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(
                json.loads(output.read_text())["streamSettings"]["hysteriaSettings"][
                    "masquerade"
                ]["url"],
                "https://connect.jopes.test",
            )
            with self.assertRaises(ValueError):
                renderer.render(source, env_file, env_file)

    def test_renderer_rejects_placeholder_or_injected_host(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "env"
            for domain in (
                "connect.example.com",
                "https://host.test",
                "host.test/path",
                "host.test;cmd",
                "host.test:443",
            ):
                path.write_text(f"CONNECT_DOMAIN={domain}")
                with (
                    patch.dict("os.environ", {}, clear=True),
                    self.assertRaises(ValueError),
                ):
                    renderer.connect_domain(path)


class MonitorTests(unittest.TestCase):
    def test_failure_and_recovery_require_consecutive_checks(self):
        state = monitor.transitions({}, {"Bot"}, {"Bot"}, 2)
        self.assertEqual(state["active"], [])
        state = monitor.transitions(state, {"Bot"}, {"Bot"}, 2)
        self.assertEqual(state["active"], ["Bot"])
        state = monitor.transitions(state, set(), {"Bot"}, 2)
        self.assertEqual(state["active"], ["Bot"])
        state = monitor.transitions(state, set(), {"Bot"}, 2)
        self.assertEqual(state["active"], [])

    def test_authentication_required_support_endpoint_counts_as_alive(self):
        with patch.object(
            monitor,
            "urlopen",
            side_effect=HTTPError(
                "https://host.test/support", 401, "Unauthorized", {}, None
            ),
        ):
            self.assertTrue(monitor.http_health("https://host.test/support", [401]))
            self.assertFalse(monitor.http_health("https://host.test/healthz", [200]))

    def test_unknown_ssh_state_does_not_clear_existing_container_alarm(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(json.dumps({"active": ["Node"], "counts": {"Node": 2}}))
            config = {"endpoints": {}, "containers": ["Node"], "failure_threshold": 2}
            with patch.object(monitor, "remote_health", side_effect=OSError):
                result = monitor.monitor(config, path)
            self.assertIn("Node", result["issues"])

    def test_disabled_notifications_never_call_telegram(self):
        with patch.object(monitor, "urlopen") as request:
            self.assertEqual(monitor.notify({}, {"active": ["Bot"]}), 0)
        request.assert_not_called()

    def test_notification_is_once_per_recipient_and_recovery_is_once(self):
        config = {"telegram": {"token": "test", "admin_ids": [1, 2]}}
        state = {"active": ["Bot"]}
        context = MagicMock()
        context.__enter__.side_effect = lambda: io.BytesIO(b'{"ok":true}')
        with patch.object(monitor, "urlopen", return_value=context) as request:
            monitor.notify(config, state)
            monitor.notify(config, state)
            self.assertEqual(request.call_count, 2)
            state["active"] = []
            monitor.notify(config, state)
            monitor.notify(config, state)
            self.assertEqual(request.call_count, 4)
