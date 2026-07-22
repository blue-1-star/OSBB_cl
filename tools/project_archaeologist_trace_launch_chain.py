#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
project_archaeologist_trace_launch_chain.py

Trace OSBB launch chain from root .bat files to Python launchers, installers,
patchers, parking_bot.py, and handlers.

Why:
- some "adult" service-order functionality worked through sandbox/live launchers;
- root BAT files may have launched wrapper scripts that patched imports or wired
  service_orders_workspace dynamically;
- this tool reconstructs that chain as text, without executing any project code.

Default root:
  G:\Programming\Py\OSBB

Usage:
  python .\OSBB\tools\project_archaeologist_trace_launch_chain.py

Output:
  OSBB\Recovered\TRACE_LAUNCH_CHAIN_<timestamp>.md
"""

from __future__ import annotations

import argparse
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


DEFAULT_ROOT = Path(r"G:\Programming\Py\OSBB")

PY_REF_RE = re.compile(r"([A-Za-z0-9_./\\ -]+\.py)", re.I)
BAT_CALL_RE = re.compile(r"([A-Za-z0-9_./\\ -]+\.bat)", re.I)
IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([A-Za-z0-9_.]+)\s+import\s+(.+)|import\s+([A-Za-z0-9_., ]+))",
    re.M,
)

KEY_PATTERNS = [
    ("parking_bot", r"parking_bot\.py|import\s+parking_bot|from\s+.*parking_bot"),
    ("service_orders_workspace", r"service_orders_workspace|handlers\.service_orders_workspace"),
    ("client_portal", r"client_portal"),
    ("client_portal_v3", r"client_portal_v3"),
    ("handle_client_portal_text", r"handle_client_portal_text"),
    ("patches_in_memory", r"in memory|setattr\(|exec\(|globals\(\)|monkey|patch"),
    ("installer", r"install_|installer|apply|patch_"),
    ("sandbox_db", r"sandbox|osbb_test|live_services|live_service"),
    ("ukrainian_ui", r"Пульти|україн|uk"),
    ("remote_button", r"Заявки на пульты|Пульты и доступ|Пульти та доступ"),
]


@dataclass
class FileNode:
    path: Path
    rel: str
    kind: str
    sha12: str
    size: int
    modified: str
    refs: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    markers: list[str] = field(default_factory=list)
    interesting_lines: list[str] = field(default_factory=list)


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def read_text(path: Path) -> str:
    for enc in ("utf-8", "utf-8-sig", "cp1251", "cp866"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            pass
    return path.read_text(encoding="utf-8", errors="replace")


def sha12(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]


def rel(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def normalize_ref(base: Path, ref: str) -> str:
    ref = ref.strip().strip('"').strip("'")
    ref = ref.replace("/", "\\")
    return ref


def find_existing_ref(root: Path, base_file: Path, ref: str) -> Path | None:
    clean = normalize_ref(base_file.parent, ref)
    candidates = [
        base_file.parent / clean,
        root / clean,
        root / "Bots" / clean,
        root.parent / clean,
    ]
    for c in candidates:
        if c.exists():
            return c.resolve()

    name = Path(clean).name
    matches = list(root.rglob(name))
    if len(matches) == 1:
        return matches[0].resolve()
    return None


def parse_refs(path: Path, text: str, root: Path) -> list[str]:
    refs: list[str] = []
    patterns = [PY_REF_RE]
    if path.suffix.lower() == ".bat":
        patterns.append(BAT_CALL_RE)
    for pat in patterns:
        for m in pat.finditer(text):
            raw = m.group(1)
            if raw.lower().endswith((".py", ".bat")):
                found = find_existing_ref(root, path, raw)
                refs.append(str(found) if found else raw.strip())
    # de-duplicate preserving order
    out = []
    seen = set()
    for r in refs:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def parse_imports(text: str) -> list[str]:
    imports = []
    for m in IMPORT_RE.finditer(text):
        if m.group(1):
            imports.append(f"from {m.group(1)} import {m.group(2).strip()}")
        elif m.group(3):
            imports.append(f"import {m.group(3).strip()}")
    return imports[:80]


def scan_file(root: Path, path: Path) -> FileNode:
    text = read_text(path)
    markers = []
    interesting = []

    for label, pattern in KEY_PATTERNS:
        if re.search(pattern, text, re.I):
            markers.append(label)

    for i, line in enumerate(text.splitlines(), 1):
        if any(re.search(pattern, line, re.I) for _label, pattern in KEY_PATTERNS):
            interesting.append(f"{i}: {line.rstrip()}")

    kind = path.suffix.lower().lstrip(".") or "file"
    return FileNode(
        path=path,
        rel=rel(root, path),
        kind=kind,
        sha12=sha12(text),
        size=path.stat().st_size,
        modified=datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        refs=parse_refs(path, text, root),
        imports=parse_imports(text) if path.suffix.lower() == ".py" else [],
        markers=markers,
        interesting_lines=interesting[:120],
    )


def collect_roots(root: Path) -> list[Path]:
    roots = []
    # root BAT launchers first
    for p in root.glob("*.bat"):
        roots.append(p)
    # common Python launchers in root and payloads
    for p in root.rglob("*.py"):
        low = str(p).lower()
        if "__pycache__" in p.parts:
            continue
        name = p.name.lower()
        if (
            name.startswith("run_bot")
            or name.startswith("start_")
            or "sandbox" in name
            or "live_services" in name
            or "live_service" in name
            or name in {"parking_bot.py"}
        ):
            roots.append(p)
    # de-duplicate
    result = []
    seen = set()
    for p in roots:
        rp = p.resolve()
        if rp not in seen and p.exists():
            seen.add(rp)
            result.append(p)
    return sorted(result, key=lambda p: str(p).lower())


def trace_from(root: Path, start: Path, max_depth: int = 4) -> list[tuple[int, Path | str]]:
    out: list[tuple[int, Path | str]] = []
    seen: set[str] = set()

    def walk(item: Path | str, depth: int) -> None:
        key = str(item)
        if key in seen or depth > max_depth:
            return
        seen.add(key)
        out.append((depth, item))
        if not isinstance(item, Path) or not item.exists():
            return
        if item.suffix.lower() not in {".bat", ".py"}:
            return
        text = read_text(item)
        for ref in parse_refs(item, text, root):
            p = Path(ref)
            if p.exists():
                walk(p, depth + 1)
            else:
                walk(ref, depth + 1)

        # Python import heuristic for handlers and parking_bot.
        if item.suffix.lower() == ".py":
            for imp in parse_imports(text):
                for module in [
                    "parking_bot",
                    "handlers.client_portal",
                    "handlers.client_portal_v3",
                    "handlers.service_orders_workspace",
                    "client_portal_v3",
                    "service_orders_workspace",
                ]:
                    if module in imp:
                        mod_path = module.replace(".", "\\") + ".py"
                        for candidate in [root / "Bots" / mod_path, root / mod_path, root / "Bots" / "handlers" / (module.split(".")[-1] + ".py")]:
                            if candidate.exists():
                                walk(candidate.resolve(), depth + 1)

    walk(start.resolve(), 0)
    return out


def score_node(node: FileNode) -> int:
    score = 0
    marker_weights = {
        "parking_bot": 20,
        "service_orders_workspace": 30,
        "client_portal_v3": 30,
        "handle_client_portal_text": 20,
        "patches_in_memory": 25,
        "installer": 20,
        "sandbox_db": 10,
        "ukrainian_ui": 10,
        "remote_button": 10,
    }
    for m in node.markers:
        score += marker_weights.get(m, 5)
    if node.kind == "bat":
        score += 15
    if "before" in node.rel.lower():
        score -= 3
    if "live" in node.rel.lower() or "sandbox" in node.rel.lower():
        score += 8
    return score


def write_report(root: Path, nodes: list[FileNode], chains: dict[str, list[tuple[int, Path | str]]]) -> Path:
    out_dir = root / "Recovered"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"TRACE_LAUNCH_CHAIN_{now_stamp()}.md"

    ranked = sorted(nodes, key=score_node, reverse=True)

    lines: list[str] = []
    lines.append("# OSBB launch chain trace")
    lines.append("")
    lines.append(f"Generated: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`")
    lines.append(f"Root: `{root}`")
    lines.append("")
    lines.append("## Main hypothesis to verify")
    lines.append("")
    lines.append("The adult service-orders UI may have worked through sandbox/live launchers that patched or wired `service_orders_workspace` into `parking_bot.py` at launch time.")
    lines.append("")
    lines.append("## Ranked interesting files")
    lines.append("")
    lines.append("| Rank | Score | Kind | Size KB | SHA | Modified | Markers | Path |")
    lines.append("|---:|---:|---|---:|---|---|---|---|")
    for i, n in enumerate(ranked, 1):
        lines.append(f"| {i} | {score_node(n)} | `{n.kind}` | {n.size/1024:.1f} | `{n.sha12}` | `{n.modified}` | `{', '.join(n.markers)}` | `{n.rel}` |")
    lines.append("")

    lines.append("## Chains from launch roots")
    lines.append("")
    for start, chain in chains.items():
        lines.append(f"### `{start}`")
        lines.append("")
        lines.append("```text")
        for depth, item in chain:
            indent = "  " * depth
            if isinstance(item, Path):
                lines.append(f"{indent}- {rel(root, item)}")
            else:
                lines.append(f"{indent}- {item}")
        lines.append("```")
        lines.append("")

    lines.append("## File details")
    lines.append("")
    for n in ranked:
        lines.append(f"### `{n.rel}`")
        lines.append("")
        lines.append(f"- Score: `{score_node(n)}`")
        lines.append(f"- Kind: `{n.kind}`")
        lines.append(f"- SHA: `{n.sha12}`")
        lines.append(f"- Size: `{n.size}`")
        lines.append(f"- Modified: `{n.modified}`")
        lines.append(f"- Markers: `{', '.join(n.markers) or 'none'}`")
        lines.append("")
        if n.refs:
            lines.append("References:")
            for r in n.refs[:80]:
                lines.append(f"- `{r}`")
            lines.append("")
        if n.imports:
            lines.append("Imports:")
            for imp in n.imports[:80]:
                lines.append(f"- `{imp}`")
            lines.append("")
        if n.interesting_lines:
            lines.append("Interesting lines:")
            lines.append("```text")
            for line in n.interesting_lines:
                lines.append(line)
            lines.append("```")
            lines.append("")

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--max-depth", type=int, default=4)
    args = ap.parse_args()

    root = Path(args.root)
    if not root.exists():
        raise SystemExit(f"Root not found: {root}")

    roots = collect_roots(root)

    all_paths: dict[Path, FileNode] = {}
    chains: dict[str, list[tuple[int, Path | str]]] = {}

    for start in roots:
        chain = trace_from(root, start, max_depth=args.max_depth)
        chains[rel(root, start)] = chain
        for _depth, item in chain:
            if isinstance(item, Path) and item.exists() and item.suffix.lower() in {".bat", ".py"}:
                try:
                    all_paths[item.resolve()] = scan_file(root, item)
                except Exception:
                    pass

    # Also scan likely direct files even if not reached by references.
    for p in roots:
        try:
            all_paths[p.resolve()] = scan_file(root, p)
        except Exception:
            pass

    nodes = list(all_paths.values())
    report = write_report(root, nodes, chains)

    print("=" * 100)
    print("OSBB Project Archaeologist: trace-launch-chain")
    print("=" * 100)
    print("Root:", root)
    print("Launch roots:", len(roots))
    print("Interesting files:", len(nodes))
    print("")
    for n in sorted(nodes, key=score_node, reverse=True)[:30]:
        print(f"score={score_node(n):4d} kind={n.kind:3s} sha={n.sha12} markers={','.join(n.markers)}")
        print(" ", n.rel)
    print("")
    print("Report:", report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
