import os
from pathlib import Path

dirs = [Path('staticfiles'), Path('core/static'), Path('media')]
files = []
for d in dirs:
    if d.exists():
        for root, _, filenames in os.walk(d):
            for fn in filenames:
                fp = Path(root) / fn
                try:
                    sz = fp.stat().st_size
                except Exception:
                    continue
                files.append((sz, str(fp)))

files.sort(reverse=True)
print('SizeKB\tPath')
for sz, path in files[:30]:
    print(f"{sz/1024:.2f}\t{path}")
