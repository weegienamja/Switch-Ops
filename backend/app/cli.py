"""Machine-readable local CLI for Lab Assurance.

The CLI talks only to the loopback sidecar and emits JSON. It does not load
credentials or open device sessions itself.
"""
from __future__ import annotations

import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASE_URL = "http://127.0.0.1:8765"


def _privacy_safe(value: object) -> object:
    """Replace local labels while retaining opaque IDs and all conclusions."""
    if isinstance(value, list):
        return [_privacy_safe(item) for item in value]
    if not isinstance(value, dict):
        return value
    reference = str(
        value.get("id")
        or value.get("nodeId")
        or value.get("targetToken")
        or "local"
    )
    protected: dict[str, object] = {}
    for key, item in value.items():
        if key in {"label", "targetLabel"}:
            protected[key] = f"protected:{reference}"
        else:
            protected[key] = _privacy_safe(item)
    return protected


def _request(path: str, *, method: str = "GET") -> object:
    request = Request(f"{BASE_URL}{path}", method=method, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=420) as response:  # noqa: S310 - fixed loopback URL
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"SwitchOps sidecar returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError("SwitchOps sidecar is not available on 127.0.0.1:8765.") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="switchops", description="SwitchOps local machine-readable interface")
    subparsers = parser.add_subparsers(dest="command", required=True)
    analyze = subparsers.add_parser("analyze", help="Return the complete Lab Assurance state as JSON")
    analyze.add_argument("--refresh", action="store_true", help="Collect current read-only evidence first")
    subparsers.add_parser("capabilities", help="Return capability evidence as JSON")
    paths = subparsers.add_parser("paths", help="Return evidence-backed paths as JSON")
    paths.add_argument("--from-node")
    paths.add_argument("--to-node")
    subparsers.add_parser("failures", help="Return bounded failure scenarios as JSON")
    subparsers.add_parser("performance", help="Return recent bounded probe observations as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "analyze":
            payload = _request("/api/lab-assurance/refresh", method="POST") if args.refresh else _request("/api/lab-assurance/state")
        elif args.command == "capabilities":
            payload = _request("/api/lab-assurance/capabilities")
        elif args.command == "paths":
            query = {key: value for key, value in {"fromNodeId": args.from_node, "toNodeId": args.to_node}.items() if value}
            payload = _request(f"/api/lab-assurance/paths{'?' + urlencode(query) if query else ''}")
        elif args.command == "failures":
            payload = _request("/api/lab-assurance/failures")
        else:
            payload = _request("/api/lab-assurance/performance")
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(_privacy_safe(payload), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
