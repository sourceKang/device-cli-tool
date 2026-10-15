from __future__ import annotations

import argparse
import html
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from cli_tool.reporting.redaction import redact
from cli_tool.workflows.xpon_remediation import (
    assert_dry_run_target_confirmation,
    build_register_method_a_plan,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a locked, family-labelled xPON register-method A dry-run plan.")
    parser.add_argument("--device-family", required=True)
    parser.add_argument("--node-key", required=True)
    parser.add_argument("--host", required=True, help="Expected device IP literal.")
    parser.add_argument("--confirm-node", required=True, help="Must exactly match --node-key.")
    parser.add_argument("--confirm-host", required=True, help="Must exactly match --host.")
    parser.add_argument("--slot", required=True, type=int)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--report-dir", default="reports/xpon-remediation")
    parser.add_argument(
        "--run-mutating",
        action="store_true",
        help="Reserved safety gate. Live execution remains locked until original-state rollback is verified.",
    )
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> tuple[int, dict[str, object]]:
    args = parse_args(argv)
    plan = build_register_method_a_plan(
        device_family=args.device_family,
        node_key=args.node_key,
        host=args.host,
        slot=args.slot,
        port=args.port,
    )
    assert_dry_run_target_confirmation(
        plan,
        confirmed_node=args.confirm_node,
        confirmed_host=args.confirm_host,
    )
    payload = plan.as_dict()
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    if args.run_mutating:
        payload["requested_mode"] = "LIVE"
        payload["result"] = "REFUSED_LIVE_EXECUTION_LOCKED"
        paths = write_report(args.report_dir, payload)
        payload["report_paths"] = {key: str(value) for key, value in paths.items()}
        return 2, payload

    payload["requested_mode"] = "DRY_RUN"
    payload["result"] = "DRY_RUN_READY"
    paths = write_report(args.report_dir, payload)
    payload["report_paths"] = {key: str(value) for key, value in paths.items()}
    return 0, payload


def write_report(report_dir: str | Path, payload: dict[str, object]) -> dict[str, Path]:
    root = Path(report_dir)
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    run_root = root / "runs" / run_id
    paths = {
        "run_json": run_root / "report.json",
        "run_html": run_root / "report.html",
        "latest_json": root / "latest.json",
        "latest_html": root / "latest.html",
    }
    safe_payload = redact(payload)
    json_content = json.dumps(safe_payload, ensure_ascii=False, indent=2)
    html_content = render_html(safe_payload)
    for key, content in (
        ("run_json", json_content),
        ("run_html", html_content),
        ("latest_json", json_content),
        ("latest_html", html_content),
    ):
        _atomic_write(paths[key], content)
    return paths


def render_html(payload: dict[str, object]) -> str:
    target = dict(payload["target"])
    live = dict(payload["live_execution"])
    command_rows = []
    for phase in ("precheck_commands", "mutation_commands", "postcheck_commands"):
        for command in payload[phase]:
            command_rows.append(
                f"<tr><td>{html.escape(phase)}</td><td><code>{html.escape(str(command))}</code></td></tr>"
            )
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>xPON Remediation Dry-run</title><style>body{{margin:0;background:#f4f7f8;color:#17212b;font:14px/1.55 "Segoe UI",sans-serif}}main{{width:min(1000px,calc(100% - 32px));margin:32px auto}}section{{background:#fff;border:1px solid #dce3e8;border-radius:14px;padding:20px;margin-top:18px}}h1{{color:#183b56}}.locked{{color:#8a5800;background:#fff4ce;padding:10px;border-radius:8px}}table{{width:100%;border-collapse:collapse}}th,td{{text-align:left;padding:9px;border-bottom:1px solid #dce3e8}}code{{background:#edf2f4;padding:2px 5px;border-radius:5px}}</style></head><body><main><h1>xPON Remediation Dry-run</h1><p>此報告只顯示計畫，未連線、未修改設備。</p><section><strong>Target</strong><p>Node <code>{html.escape(str(target['node_key']))}</code> · Host <code>{html.escape(str(target['host']))}</code> · Interface <code>{html.escape(str(target['interface']))}</code></p><div class="locked">Live execution: {html.escape(str(live['status']))}<br>{html.escape(str(live['blocker']))}</div></section><section><table><thead><tr><th>Phase</th><th>Command</th></tr></thead><tbody>{''.join(command_rows)}</tbody></table></section></main></body></html>"""


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    try:
        exit_code, payload = run()
    except (PermissionError, ValueError) as error:
        print(json.dumps({"result": "REFUSED", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(payload, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
