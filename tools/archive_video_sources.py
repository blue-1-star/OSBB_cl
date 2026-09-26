"""Move video source files into a dated archive, preserving byte-level lineage."""
from pathlib import Path
import hashlib
import json
import argparse

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    base = ROOT / 'data/raw/video_recognition'
    target = base / 'archive/2026-09-26'
    files = sorted(p for p in base.iterdir() if p.is_file() and p.suffix.lower() in {'.xlsx', '.zip'})
    rows = [{'old_path': str(p.relative_to(ROOT)),
             'new_path': str((target / p.name).relative_to(ROOT)),
             'source_file': p.name, 'bytes': p.stat().st_size,
             'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]
    if any((target / p.name).exists() for p in files):
        raise FileExistsError('Archive destination already contains a source; refusing overwrite')
    print(f'{len(files)} source files; apply={args.apply}')
    if not args.apply or not files:
        return
    target.mkdir(parents=True, exist_ok=True)
    manifest = target / 'source_manifest.json'
    if manifest.exists():
        raise FileExistsError(manifest)
    # Persist the map first: even an interrupted move can be traced/recovered.
    manifest.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    for p, row in zip(files, rows):
        destination = target / p.name
        p.rename(destination)
        assert hashlib.sha256(destination.read_bytes()).hexdigest() == row['sha256']
    print(f'Verified {len(rows)} unchanged sources; manifest: {manifest.relative_to(ROOT)}')

if __name__ == '__main__':
    main()
