"""Archives Line Doctor exports out of the game log, one file per export, per game.

The mod cannot write files (no `io` in game scripts), so exports land in the game log (stdout.txt), which the
game resets at every launch. This keeps them:

    exports/<gameId>/<gameTime>.json   one file per export (never rewritten)
    exports/<gameId>/latest.json       copy of the most recent export of that game

Exports made before the mod had a game id go to exports/sans-id/. Run automatically by the analyzers
(analyze.load_history); can also be run by hand:

    python analyzer/archive.py [path/to/stdout.txt]
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import analyze  # noqa: E402

ROOT = Path(__file__).resolve().parents[1] / "exports"


def sync(log_path: str, root: Path = ROOT) -> Path | None:
    """Copies every complete export of the log to the archive; returns the folder of the latest export's game."""
    text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    snaps = analyze.extract_all_from_log(text)
    latest_dir = None
    for snap in snaps:
        game = str(snap.get("gameId") or "sans-id")
        folder = root / game
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{int(snap.get('gameTime') or 0):012d}.json"
        if not target.exists():
            target.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        latest_dir = folder
    if latest_dir is not None and snaps:
        (latest_dir / "latest.json").write_text(json.dumps(snaps[-1], ensure_ascii=False), encoding="utf-8")
    return latest_dir


def load_game(folder: Path) -> list[dict]:
    """Every archived export of one game, ordered by simulation time."""
    files = sorted(p for p in Path(folder).glob("*.json") if p.name != "latest.json")
    return [analyze.upgrade_snapshot(json.loads(p.read_text(encoding="utf-8"))) for p in files]


def main(argv=None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    log = args[0] if args else next((c for c in analyze.default_candidates() if c.lower().endswith("stdout.txt")), None)
    if not log:
        print("Journal du jeu introuvable.", file=sys.stderr)
        return 2
    folder = sync(log)
    count = len(load_game(folder)) if folder else 0
    print(f"{count} exports archivés dans {folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
