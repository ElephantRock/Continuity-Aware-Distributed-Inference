from pathlib import Path

p = Path("experiments/c73_retention_engine.py")
text = p.read_text()
old = 'f"{event.kind.value} cannot occur before first ADMIT for the same State"'
new = 'f"{event.kind.value} cannot occur before ADMIT (first admission) for the same State"'
if text.count(old) != 1:
    raise SystemExit("causal validation message anchor drift")
p.write_text(text.replace(old, new))
