# Line Doctor — line diagnostics for Transport Fever 3

*English · [Français](README.fr.md)*

Line Doctor comes in **two parts**:

1. **A Transport Fever 3 mod** (`olrick_line_doctor_1`) that works on its own: a compass and a company
   **health** indicator in the bottom bar, a "Line Doctor" card in every line window, and a monthly export of
   the detailed state of your game.
2. **An interface with Claude** (this repository, opened in Claude Code): Python analyzers and instructions
   that let Claude read the export and explain why a line, a production chain or the whole company is losing
   money, and what to do about it.

```
 Transport Fever 3 (the mod)                       Claude Code (this repository)
┌───────────────────────────────────┐          ┌──────────────────────────────────────┐
│ bottom bar: Cap · Santé ● ● ● ●   │          │ analyzer/*.py   → metrics, reports    │
│ line window: card                 │  export  │ skill analyze-lines → explanations    │
│ game script (read-only) ──────────┼────────▶ │                       and fixes       │
│   export on load + once a month   │stdout.txt│ exports/<game>/ → history            │
└───────────────────────────────────┘          └──────────────────────────────────────┘
```

The mod is **read-only**: it sends no command to the game, changes no price and writes nothing to the
accounts. It is flagged `cosmetic` so it does not block achievements.

> **Language:** the in-game labels (`Cap`, `Santé`, tooltips) and Claude's answers are currently in French.

---

## Part 1 — What runs inside TF3

### The compass

In the bottom bar, left of the earnings: **`Cap NE 45°`**.

- It shows where the camera is looking: N, NE, E, SE, S, SW, W, NW and the heading in degrees.
- TF3 has no official north: here **north is the map's +y axis**, headings go clockwise. Claude's reports use
  the same convention ("the station is 2 km away, heading 330°"), so you can find in game a building it points
  out.
- Calibrated in game against several landmarks.

### Company health

Next to the compass: **`Santé ● ● ● ●`** (health), one dot per indicator, **green**, **orange** or **red**.
Hover over it for the details (values and thresholds).

**What health measures.** Four ratios that tell whether the company is structurally profitable, beyond this
month's result:

| Dot | Indicator | Green | Orange | Red | What it watches |
|---|---|---|---|---|---|
| 1 | Income ÷ vehicle running costs | ≥ 2.0 | 1.7 – 2.0 | < 1.7 | do vehicles earn enough compared to what they cost to run? |
| 2 | Building upkeep ÷ income | ≤ 20 % | 20 – 28 % | > 28 % | too many stations, depots, ports for what they bring in |
| 3 | Vehicle maintenance ÷ income | ≤ 9 % | 9 – 11 % | > 11 % | fleet too old or badly maintained |
| 4 | Operating result ÷ income | ≥ 10 % | 0 – 10 % | < 0 % | does the company make money, investments aside? |

The thresholds come from a real game that was healthy until 1959 and then went into deficit: at that point
ratios 1 to 3 were 1.6, 34 % and 12 %.

**Projected at the maximum rank.** With TF3's inflation, the price paid per passenger or per ton drops with
every company promotion (down to ×0.75 at rank 15 with Normal inflation), while vehicle and building costs
never drop. Health is therefore computed **as if the company had already reached the maximum rank**:
income × (multiplier at rank 15 ÷ current multiplier), costs unchanged. As a result:

- a dot that is green today stays green through every promotion;
- a promotion does not change the dots: only income, costs and buildings move them;
- with inflation turned off (game option), the projection changes nothing.

The calculation covers the **last 12 complete months** of the game's finance table, **excluding subsidies**
(one-off). It is refreshed at every monthly export; before the first one, the indicator shows "Santé …".

### The Line Doctor card in the line window

Each line window gets a "Line Doctor" card showing the **pending profit**: income already earned by the line
but not paid yet. In TF3 cargo is paid only when it reaches its final customer, for every leg of the trip: a
feeder line can look unprofitable while its cargo is still on its way.

### The export

When a game is loaded and then every in-game month (the game must not be paused), the mod writes a snapshot of
the game to the game log (`stdout.txt`), between `LINE_DOCTOR_BEGIN` / `LINE_DOCTOR_END` markers: TF3 game
scripts are not allowed to write files.

Per line: stops, 12-month finances and monthly series, capacity and load, passengers or cargo waiting at each
stop, actual vs theoretical section times, vehicles (model, age, condition, yearly cost, income), problems
reported by the game. Company-wide: cash, loan, rank and inflation, finance table, upkeep of every building,
**warehouse and industry stocks** (stored, produced, delivered, shipped per year, cargo thrown away), observed
ticket prices and pending profit.

---

## Part 2 — The interface with Claude

Open this repository in **Claude Code** and ask questions, for example:

- "analyse my TF3 lines", "health?", "why is my cash going down?"
- "analyse the chain that delivers clothes to Vandœuvre", "show the infrastructure"
- "look at the Vitry warehouse", "can a fast train be profitable?"

Claude follows [.claude/skills/analyze-lines/SKILL.md](.claude/skills/analyze-lines/SKILL.md): it reads the
latest export, runs the analyzers, cross-checks the clues and answers with causes, figures and concrete
actions to take in the game. It does not play for you: the mod cannot change anything.

The analyzers can also be run directly:

| Command | Output |
|---|---|
| `python analyzer/analyze.py --deficit-only` | metrics and diagnostics per line (`OVERCAPACITY`, `QUEUE`, `TRANSFER_DEPENDENT`, `POOR_MAINTENANCE`…), finance table |
| `python analyzer/health.py --html out/sante.html` | detailed health check: ratios, rank and inflation, stress test at rank 15, fragile lines |
| `python analyzer/chains.py --html out/chaines.html` | end-to-end production chains, from the final customer back to raw materials, profit per leg |
| `python analyzer/infra.py --html out/infrastructure.html` | upkeep building by building, stations without a line |
| `python analyzer/history.py --html out/historique.html` | company history year by year |

Every analysis archives the exports found in the game log into `exports/<game id>/`, because the game log is
wiped at each launch: the history of your games is kept.

---

## Installation

### Requirements

- **Windows** and **Transport Fever 3** on Steam.
- For the Claude part: **[Claude Code](https://claude.com/claude-code)**, **Python 3.10+** and **git**.
- For the offline tests (optional): `pip install lupa`.

### 1. Get the repository

```bash
git clone https://github.com/Olrick/tf3-line-doctor.git
```

```bash
cd tf3-line-doctor
```

### 2. Install the mod into TF3

```bash
powershell -ExecutionPolicy Bypass -File scripts/install.ps1
```

The script finds the TF3 user data folder on its own
(`C:\Program Files (x86)\Steam\userdata\<Steam id>\3493540\local\`) and copies the mod into its `mods\`
subfolder. Options:

- `-UserData "<path>\3493540\local"` if the folder is not found;
- `-Link` for development: creates a link to the repository instead of a copy.

### 3. Enable the mod

1. Start TF3.
2. Create a game or load a save: in the mod list, tick **Line Doctor**.
3. Let the game run a few seconds, **not paused**: the compass shows up right away, health after the first
   export.

### 4. Use the Claude interface

Open the repository folder in Claude Code and ask "analyse my TF3 lines". Claude reads the game log
(`…\3493540\local\crash_dump\stdout.txt`); if no export is found, check that the mod is ticked in the game and
that the game is not paused.

### Updating

```bash
git pull
```

```bash
powershell -ExecutionPolicy Bypass -File scripts/install.ps1
```

- If only existing files changed: **reloading the save** is enough.
- If a file was **added** to the mod: **quit TF3 completely and restart it** (the game only indexes new files
  at startup).

---

## Repository layout

| Path | Role |
|---|---|
| `mod/olrick_line_doctor_1/` | the TF3 mod |
| `…/line_doctor/collector.lua` | data collection and health calculation (every API call wrapped in `pcall`) |
| `…/line_doctor/line_doctor.script.lua` | game script: triggers and writes the export |
| `…/line_doctor/line_doctor_gui.script.lua` | in-game interface: compass, health, line card |
| `…/line_doctor/pending.lua`, `ticket_probe.lua` | pending profit, ticket price observation |
| `analyzer/` | Python analyzers (see part 2) |
| `.claude/skills/analyze-lines/` | instructions for Claude |
| `tests/` | offline tests: the mod's real Lua code against a simulated game API |
| `docs/TESTING.md` | in-game test procedure (French) |

## Tests

```bash
pip install lupa
```

```bash
python -m unittest discover -s tests -v
```

See [docs/TESTING.md](docs/TESTING.md) for the in-game procedure.
