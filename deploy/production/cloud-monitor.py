from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path


def module(name):
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), Path(__file__).with_name(name + ".py")
    )
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


if __name__ == "__main__":
    try:
        cloud = module("cloud-backup")
        ssh_config = cloud.configure_ssh("monitor")
        monitor = module("monitor")

        def remote_health(_config):
            result = cloud.ssh(ssh_config, "health")
            if result.returncode:
                raise OSError("Cloud probe unavailable")
            return json.loads(result.stdout)

        monitor.remote_health = remote_health
        config = json.loads(os.environ["JOPES_MONITOR_CONFIG"])
        config.update(
            {
                "single_owner": True,
                "daily_summary": False,
                "critical_labels": ["Bot", "Panel", "Support", "SSH"],
            }
        )
        report = monitor.monitor(config, Path("monitor-state.json"))
        print(json.dumps(report))
    except Exception as error:  # noqa: BLE001 -- do not expose credentials
        print(f"Cloud monitor failed ({type(error).__name__})")
        raise SystemExit(1) from None
