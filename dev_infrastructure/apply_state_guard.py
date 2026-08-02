"""
Автоматически добавляет защиту isinstance(state, dict) сразу после
строк вида "state = user_states.get(user_id...)" — там, где её ещё
нет. Идемпотентно: если защита уже стоит следующей строкой, файл не
трогается. Делает резервную копию каждого изменённого файла (.bak)
перед записью.
"""
import re
import sys
from pathlib import Path

ASSIGN_PATTERN = re.compile(r'^(\s*)state\s*=\s*user_states\.get\(user_id.*\)\s*$')

def patch_file(path: Path) -> int:
    lines = path.read_text(encoding='utf-8').splitlines(keepends=True)
    out = []
    changed = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = ASSIGN_PATTERN.match(line.rstrip('\n'))
        if m:
            indent = m.group(1)
            next_line = lines[i + 1] if i + 1 < len(lines) else ""
            if "isinstance" in next_line and "state" in next_line:
                pass  # уже защищено
            else:
                out.append(f"{indent}if not isinstance(state, dict):\n")
                out.append(f"{indent}    state = {{}}\n")
                changed += 1
        i += 1
    if changed:
        path.with_suffix(path.suffix + '.bak').write_text(''.join(lines), encoding='utf-8')
        path.write_text(''.join(out), encoding='utf-8')
    return changed

if __name__ == "__main__":
    total = 0
    for filepath in sys.argv[1:]:
        p = Path(filepath)
        if not p.exists():
            print(f"⚠ не найден: {filepath}")
            continue
        n = patch_file(p)
        print(f"{filepath}: добавлено защит — {n}")
        total += n
    print(f"\nИтого добавлено: {total}")