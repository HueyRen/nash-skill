#!/usr/bin/env python3
"""Render a validated Nash analysis JSON file as an inline visualization fragment."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def safe_root_id(value: str) -> str:
    value = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    return f"nash-{value or 'report'}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("analysis", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--template", type=Path)
    parser.add_argument("--root-id")
    args = parser.parse_args()

    template_path = args.template or Path(__file__).resolve().parents[1] / "assets" / "report-template.html"
    analysis = json.loads(args.analysis.read_text(encoding="utf-8"))
    template = template_path.read_text(encoding="utf-8")

    root_id = args.root_id or safe_root_id(analysis.get("meta", {}).get("slug", args.output.stem))
    payload = json.dumps(analysis, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/")

    fragment = template.replace("__NASH_ROOT_ID__", root_id).replace("__NASH_DATA__", payload)
    if "__NASH_" in fragment:
        raise ValueError("Unresolved template placeholder")
    if re.search(r"<!doctype|<\s*(?:html|head|body)(?:\s|>)", fragment, re.IGNORECASE):
        raise ValueError("Report template must remain an HTML fragment")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(fragment, encoding="utf-8")
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
