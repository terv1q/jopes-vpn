from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def remote_health(config: dict) -> dict:
    source = """import json,pathlib,subprocess
wanted=__CONTAINERS__
containers={}
for name in wanted:
 r=subprocess.run(['docker','inspect','--format','{{json .State}}',name],capture_output=True,text=True)
 if r.returncode: containers[name]=False; continue
 s=json.loads(r.stdout); containers[name]=s['Running'] and s.get('Health',{}).get('Status','healthy')=='healthy'
mem={line.split(':')[0]:int(line.split()[1]) for line in pathlib.Path('/proc/meminfo').read_text().splitlines()}
ports=subprocess.run(['ss','-H','-lnt'],capture_output=True,text=True).stdout
print(json.dumps({'containers':containers,'available_mb':mem['MemAvailable']//1024,'swap_percent':100*(mem['SwapTotal']-mem['SwapFree'])/max(1,mem['SwapTotal']),'vpn_tcp_ports':all(':'+str(port)+' ' in ports for port in (2053,8443))}))
""".replace("__CONTAINERS__", repr(config["containers"]))
    result = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            config["ssh_host"],
            "python3 -",
        ],
        input=source,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    if result.returncode:
        raise OSError("Remote health probe failed")
    return json.loads(result.stdout)


def http_health(url: str, allowed: list[int]) -> bool:
    try:
        request = Request(url, headers={"User-Agent": "JopesVPN-Monitor/1"})
        with urlopen(request, timeout=10) as response:
            return response.status in allowed
    except HTTPError as error:
        return error.code in allowed
    except (URLError, OSError, TimeoutError):
        return False


def transitions(
    state: dict, failed: set[str], labels: set[str], threshold: int
) -> dict:
    counts = state.setdefault("counts", {})
    active = set(state.get("active", []))
    for label in labels:
        count = counts.get(label, 0)
        count = max(0, count) + 1 if label in failed else min(0, count) - 1
        counts[label] = max(-threshold, min(threshold, count))
        if count >= threshold:
            active.add(label)
        elif count <= -threshold:
            active.discard(label)
    state["active"] = sorted(active)
    return state


def notify(config: dict, state: dict) -> int:
    notification = config.get("telegram")
    if not notification:
        return 0
    active = state["active"]
    signature = ",".join(active) if active else "ok"
    delivered = state.setdefault("notifications", {})
    failures = 0
    for admin_id in notification["admin_ids"]:
        previous = delivered.get(str(admin_id))
        if previous == signature or (previous is None and not active):
            continue
        text = (
            "Jopes VPN: мониторинг обнаружил проблему.\n" + "\n".join(active)
            if active
            else "Jopes VPN: все проверяемые сервисы восстановились."
        )
        request = Request(
            f"https://api.telegram.org/bot{notification['token']}/sendMessage",
            data=json.dumps({"chat_id": admin_id, "text": text}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                ok = json.load(response).get("ok", False)
        except (HTTPError, URLError, OSError, TimeoutError, ValueError):
            ok = False
        if ok:
            delivered[str(admin_id)] = signature
        else:
            failures += 1
    return failures


def monitor(config: dict, state_path: Path) -> dict:
    failed = set()
    labels = set(config["endpoints"]) | {"SSH"}
    for label, endpoint in config["endpoints"].items():
        if not http_health(endpoint["url"], endpoint["statuses"]):
            failed.add(label)
    resources = {}
    try:
        resources = remote_health(config)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        failed.add("SSH")
    else:
        labels |= set(config["containers"]) | {"RAM", "Swap", "VPN TCP"}
        failed.update(
            name for name, running in resources["containers"].items() if not running
        )
        if resources["available_mb"] < config["min_available_mb"]:
            failed.add("RAM")
        if resources["swap_percent"] > config["max_swap_percent"]:
            failed.add("Swap")
        if not resources["vpn_tcp_ports"]:
            failed.add("VPN TCP")
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        state = {}
    state = transitions(state, failed, labels, config["failure_threshold"])
    notification_failures = notify(config, state)
    temporary = state_path.with_suffix(".partial")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    temporary.replace(state_path)
    return {
        "checked_at": datetime.now(UTC).isoformat(),
        "status": "degraded" if state["active"] else "ok",
        "issues": state["active"],
        "pending": sorted(failed - set(state["active"])),
        "available_mb": resources.get("available_mb"),
        "notification_failures": notification_failures,
    }


if __name__ == "__main__":
    try:
        config = json.load(sys.stdin)
        report = monitor(config, Path(sys.argv[1]))
        print(json.dumps(report))
        raise SystemExit(
            1 if report["status"] != "ok" or report["notification_failures"] else 0
        )
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        print(f"Monitor failed ({type(error).__name__})", file=sys.stderr)
        raise SystemExit(2) from None
