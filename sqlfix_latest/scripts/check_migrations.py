from pathlib import Path
import re
root=Path(__file__).resolve().parents[1]/"supabase"/"migrations"
files=sorted(root.glob("*.sql"))
assert files, "No canonical Supabase migrations found"
versions=[]
for p in files:
    m=re.match(r"(\d{14})_",p.name)
    assert m, f"invalid Supabase migration filename: {p.name}"
    versions.append(m.group(1))
assert len(versions)==len(set(versions)), f"duplicate migration versions: {versions}"
assert versions == sorted(versions), "Supabase migrations are not ordered"
print(f"canonical migrations: {len(files)}")
