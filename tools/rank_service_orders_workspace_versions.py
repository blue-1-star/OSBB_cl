#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
rank_service_orders_workspace_versions.py

Find and rank all service_orders_workspace.py versions in OSBB project/recovered trees.

Goal:
- not just largest/newest;
- prefer versions that contain the complete remote/pult order workflow:
  resident entry, new remote quantity flow, supplier batches, issue_new_remotes_from_batch,
  admin "Заявки на пульты", phone access integration, and service_preorders_core support.

Default root:
  G:\Programming\Py\OSBB

Usage:

  python .\OSBB\tools\rank_service_orders_workspace_versions.py

  python .\OSBB\tools\rank_service_orders_workspace_versions.py `
    --root "G:\Programming\Py\OSBB" `
    --apply-report

Output:
  OSBB\Recovered\SERVICE_ORDERS_WORKSPACE_RANKING_<timestamp>.md
"""

from __future__ import annotations

import argparse
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


DEFAULT_ROOT = Path(r"G:\Programming\Py\OSBB")

SIGNALS = [
    # name, weight, pattern
    ("client entry: Пульты и доступ", 5, r"Пульты и доступ|Пульти та доступ|Remotes and access"),
    ("resident new remote button", 8, r"Получить новые пульты|Отримати нові пульти|Get new remotes|Получить новый пульт"),
    ("choose quantity", 10, r"choose_quantity|Сколько новых пультов|Скільки нових пультів|How many new remotes"),
    ("NEW_REMOTE_PROFILE", 12, r"NEW_REMOTE_PROFILE|REMOTE_NEW_PREORDER"),
    ("old stock profile", 4, r"REMOTE_NEW_FROM_STOCK"),
    ("supplier batches UI", 12, r"supplier_batches|Поставки пультов|Поставки пультів|Remote deliveries"),
    ("issue new action", 12, r"issue_new|Выдать новые пульты|Видати нові пульти|Issue new remotes"),
    ("issue_new_remotes_from_batch", 20, r"issue_new_remotes_from_batch"),
    ("remote batch issued step", 12, r"REMOTE_BATCH_ISSUED"),
    ("remote supplier batches tables", 10, r"remote_supplier_batches|remote_supplier_batch_links|remote_order_issued_assets"),
    ("admin remote requests route", 10, r"Заявки на пульты"),
    ("phone access route", 7, r"Телефонный доступ|PHONE_ACCESS_CONNECT|phone_access"),
    ("service preorders import", 10, r"service_preorders_core"),
    ("remote stock/assets", 8, r"remote_assets|remote_asset_movements|remote_order_details"),
    ("cashier/payment awareness", 6, r"payment|оплат|кассир|cashier"),
    ("workflow text: money separate", 5, r"Заявка, деньги и физический пульт учитываются отдельно|Orders, money and the physical remote"),
    ("operator actions", 5, r"receive_remote|return_remote|program_done|reserve_remote"),
]

PENALTIES = [
    ("stub wording", -30, r"Здесь будет обработка заказов пультов|Здесь будет обработка|заглушк"),
    ("no offers stub", -5, r"пока нет активных услуг|No active remote"),
]


@dataclass
class Candidate:
    path: Path
    size: int
    sha12: str
    score: int
    signals: list[str]
    penalties: list[str]
    mtime: str
    first_line: str


def read_text(path: Path) -> str:
    for enc in ("utf-8", "utf-8-sig", "cp1251"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def classify_path(path: Path) -> int:
    s = str(path).lower()
    bonus = 0
    # Prefer recovered release versions over hashed files_by_module copies,
    # because they preserve release context.
    if "recovered_releases" in s:
        bonus += 8
    if "phone_barrier_access_v2_working_sandbox" in s:
        bonus += 12
    if "phone_access_ui_fix_v3" in s:
        bonus += 10
    if "phone_access_ui_fix_v2" in s:
        bonus += 8
    if "live_services_sandbox_bundle" in s:
        bonus += 4
    if "files_by_module" in s or "project_archaeology" in s:
        bonus -= 2
    if r"\bots\handlers" in s or "/bots/handlers" in s:
        bonus += 3
    return bonus


def score_file(path: Path) -> Candidate:
    text = read_text(path)
    size = path.stat().st_size
    sha12 = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]
    score = classify_path(path)
    found: list[str] = []
    bad: list[str] = []

    for name, weight, pattern in SIGNALS:
        if re.search(pattern, text, re.IGNORECASE):
            score += weight
            found.append(f"+{weight} {name}")

    for name, weight, pattern in PENALTIES:
        if re.search(pattern, text, re.IGNORECASE):
            score += weight
            bad.append(f"{weight} {name}")

    first_line = ""
    for line in text.splitlines():
        if line.strip():
            first_line = line.strip()[:160]
            break

    return Candidate(
        path=path,
        size=size,
        sha12=sha12,
        score=score,
        signals=found,
        penalties=bad,
        mtime=datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        first_line=first_line,
    )


def find_candidates(root: Path) -> list[Path]:
    names = [
        "service_orders_workspace.py",
    ]

    paths: list[Path] = []
    for p in root.rglob("*.py"):
        name = p.name
        if name in names or name.startswith("service_orders_workspace_"):
            if "__pycache__" not in p.parts:
                paths.append(p)
    return sorted(paths)


def write_report(root: Path, candidates: list[Candidate]) -> Path:
    out_dir = root / "Recovered"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out = out_dir / f"SERVICE_ORDERS_WORKSPACE_RANKING_{stamp}.md"

    lines = []
    lines.append("# service_orders_workspace.py ranking")
    lines.append("")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Root: `{root}`")
    lines.append("")
    lines.append("## Recommendation")
    lines.append("")
    if candidates:
        best = candidates[0]
        lines.append(f"Best candidate by score: `{best.path}`")
        lines.append(f"Score: `{best.score}`")
        lines.append(f"Size: `{best.size}` bytes")
        lines.append(f"SHA: `{best.sha12}`")
    else:
        lines.append("No candidates found.")
    lines.append("")
    lines.append("## Ranked candidates")
    lines.append("")
    lines.append("| Rank | Score | Size KB | SHA | Path |")
    lines.append("|---:|---:|---:|---|---|")
    for i, c in enumerate(candidates, 1):
        lines.append(f"| {i} | {c.score} | {c.size/1024:.1f} | `{c.sha12}` | `{c.path}` |")
    lines.append("")
    lines.append("## Details")
    lines.append("")
    for i, c in enumerate(candidates, 1):
        lines.append(f"### {i}. `{c.path}`")
        lines.append("")
        lines.append(f"- Score: `{c.score}`")
        lines.append(f"- Size: `{c.size}` bytes")
        lines.append(f"- Modified: `{c.mtime}`")
        lines.append(f"- SHA: `{c.sha12}`")
        lines.append(f"- First line: `{c.first_line}`")
        lines.append("")
        lines.append("Signals:")
        if c.signals:
            for s in c.signals:
                lines.append(f"- {s}")
        else:
            lines.append("- none")
        if c.penalties:
            lines.append("")
            lines.append("Penalties:")
            for p in c.penalties:
                lines.append(f"- {p}")
        lines.append("")

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--apply-report", action="store_true")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.exists():
        raise SystemExit(f"Root not found: {root}")

    paths = find_candidates(root)
    candidates = [score_file(p) for p in paths]
    candidates.sort(key=lambda c: (c.score, c.size), reverse=True)

    print("=" * 100)
    print("service_orders_workspace.py version ranking")
    print("=" * 100)
    print("Root:", root)
    print("Candidates:", len(candidates))
    print("")

    for i, c in enumerate(candidates[: args.top], 1):
        print(f"{i:02d}. score={c.score:4d} size={c.size/1024:7.1f}KB sha={c.sha12} path={c.path}")
        for s in c.signals[:6]:
            print("    ", s)
        if c.penalties:
            for p in c.penalties:
                print("    ", p)
        print("")

    report = write_report(root, candidates)
    print("Report:", report)

    if candidates:
        print("")
        print("Best candidate:")
        print(candidates[0].path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
