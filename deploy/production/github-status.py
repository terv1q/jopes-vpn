"""Safe operational status: no credentials, artifact URLs or workflow log bodies."""

import argparse
import json
import subprocess
from urllib.request import Request, urlopen


def api(path, method="GET", data=None):
    result = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True,
        text=True,
        check=True,
    )
    credential = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    request = Request(
        "https://api.github.com" + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={
            "Authorization": "Bearer " + credential["password"],
            "Accept": "application/vnd.github+json",
            "User-Agent": "JopesVPN-Operations",
        },
        method=method,
    )
    with urlopen(request, timeout=30) as response:
        payload = response.read()
        return json.loads(payload) if payload else {}


def status(repo):
    info = api("/repos/" + repo)
    runs = api("/repos/" + repo + "/actions/runs?per_page=5")["workflow_runs"]
    artifacts = api("/repos/" + repo + "/actions/artifacts?per_page=8")["artifacts"]
    result = {
        "repo": repo,
        "private": info["private"],
        "runs": [],
        "artifacts": [
            {
                k: item[k]
                for k in (
                    "id",
                    "name",
                    "size_in_bytes",
                    "expired",
                    "created_at",
                    "expires_at",
                )
            }
            for item in artifacts
        ],
    }
    for run in runs:
        jobs = api(f"/repos/{repo}/actions/runs/{run['id']}/jobs")["jobs"]
        result["runs"].append(
            {
                "id": run["id"],
                "name": run["name"],
                "status": run["status"],
                "conclusion": run["conclusion"],
                "created_at": run["created_at"],
                "steps": [
                    {"name": step["name"], "conclusion": step["conclusion"]}
                    for job in jobs
                    for step in job["steps"]
                ],
            }
        )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("repo")
    parser.add_argument("--dispatch")
    parser.add_argument("--artifact", type=int)
    parser.add_argument("--ref", default="main")
    args = parser.parse_args()
    try:
        if args.dispatch:
            inputs = {"artifact_id": str(args.artifact)} if args.artifact else {}
            api(
                f"/repos/{args.repo}/actions/workflows/{args.dispatch}/dispatches",
                "POST",
                {"ref": args.ref, "inputs": inputs},
            )
            print(json.dumps({"dispatched": args.dispatch}))
        else:
            print(json.dumps(status(args.repo)))
    except Exception as error:  # noqa: BLE001 -- suppress URLs and credentials
        print(
            json.dumps(
                {
                    "failed": type(error).__name__,
                    "http_status": getattr(error, "code", None),
                }
            )
        )
        raise SystemExit(1) from None
