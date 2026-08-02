"""
Проверка синтаксиса нескольких файлов разом — без ловушки с
бэкслешами в путях (той, что сломала предыдущую однострочную команду:
\\t, \\u внутри строки Python интерпретируются как escape-последовательности).

Использование:
    python check_syntax.py file1.py file2.py file3.py ...
"""
import ast
import sys
from pathlib import Path

for filepath in sys.argv[1:]:
    p = Path(filepath)
    try:
        ast.parse(p.read_text(encoding="utf-8"))
        print(f"✅ {filepath} OK")
    except SyntaxError as e:
        print(f"❌ {filepath}: {e}")
    except Exception as e:
        print(f"⚠ {filepath}: {type(e).__name__}: {e}")