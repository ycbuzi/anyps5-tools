"""The 2D kingdom's side pictures (work_skills.render_kingdom_side): waits for no game and a free GPU flag, renders, and
prints what it saved. Argument: minutes to wait."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import work_skills  # noqa: E402

print(work_skills.render_kingdom_side(float(sys.argv[1]) if len(sys.argv) > 1 else 240), flush=True)
