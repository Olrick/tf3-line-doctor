"""Line Doctor analyzer: turns a Line Doctor export into per-line metrics and diagnostics.

Usage:
    python analyzer/analyze.py                      # auto-locate the latest export
    python analyzer/analyze.py path/to/export.json  # explicit export file
    python analyzer/analyze.py path/to/stdout.txt   # extract the export from the game log
    python analyzer/analyze.py --json out.json      # also write machine-readable diagnostics

The report is meant to be read by Claude (see .claude/skills/analyze-lines/SKILL.md), who turns the
diagnostics into an explanation and concrete suggestions. Thresholds are heuristics, not game rules.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

LOG_BEGIN = "LINE_DOCTOR_BEGIN"
LOG_LINE = "LINE_DOCTOR|"
LOG_END = "LINE_DOCTOR_END"

# --- heuristics -------------------------------------------------------------------------------
LOW_UTILISATION = 0.40       # transported / theoretical rate
HIGH_LOAD = 0.85             # used / capacity
CROWD_FACTOR = 2.0           # waiting at a stop vs. capacity of one vehicle
DWELL_RATIO = 0.35           # (real - drive) / real time spent at stops
CONGESTION_RATIO = 1.30      # real drive time vs. expected drive time
OLD_VEHICLE_YEARS = 25
LOW_MAINTENANCE_STATE = 0.5
LONG_INTERVAL_SEC = 600      # passenger lines: one vehicle every 10 min or worse
SHORT_SECTION_SEC = 45       # average drive time between stops
DECLINE_RATIO = 0.25         # net result dropped by more than 25 % of revenue
YOUNG_LINE_MONTHS = 6        # below this, structural verdicts are premature
YOUNG_LINE_CYCLES = 2        # ... or fewer round trips than this
SLOW_CYCLE_MONTHS = 3        # round trip longer than this: monthly results are lumpy
STUCK_DAYS = 30              # a game day is ~4 s: 30 days ≈ 2 min at a terminal
QUEUE_MIN_VEHICLES = 3       # loaded vehicles standing still on the way to the same stop...
QUEUE_MIN_SHARE = 0.4        # ...and at least this share of the fleet
QUEUE_SPEED = 0.5            # m/s, below = standing still
QUEUE_HISTORY = 24           # recent exports (~2 game years) scanned for recurring queues
QUEUE_RECURRING_SHARE = 0.25 # queue seen in at least this share of them = recurring
ASYMMETRY_RATIO = 2.0        # one direction takes this many times longer to drive than the other
TICKS_PER_SEC = 1000         # game time ticks are milliseconds (year = 1 461 000 ticks = 1 461 s)


CONDITION_LEVELS = ["très mauvais", "mauvais", "moyen", "bon", "très bon"]


def condition_label(state: float) -> str:
    """Same banding as the game's vehicle window (5 levels of 20 %)."""
    n = len(CONDITION_LEVELS)
    return CONDITION_LEVELS[min(n, max(1, math.ceil(state * n))) - 1]


@dataclass
class Finding:
    code: str
    severity: str  # "critical" | "major" | "minor" | "info"
    message: str
    suggestion: str
    evidence: dict = field(default_factory=dict)


SEVERITY_ORDER = {"critical": 0, "major": 1, "minor": 2, "info": 3}


# --- loading ----------------------------------------------------------------------------------

def default_candidates() -> list[str]:
    """Places where the mod writes its export, newest first."""
    patterns = []
    steam_dirs = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Steam\userdata"),
        os.path.expanduser("~/.steam/steam/userdata"),
    ]
    for sd in steam_dirs:
        patterns += [
            os.path.join(sd, "*", "3493540", "local", "line_doctor", "export.json"),
            os.path.join(sd, "*", "3493540", "local", "export.json"),
            os.path.join(sd, "*", "3493540", "local", "crash_dump", "stdout.txt"),
        ]
    patterns += [
        r"D:\SteamLibrary\steamapps\common\Transport Fever 3\line_doctor_export.json",
        r"C:\Program Files (x86)\Steam\steamapps\common\Transport Fever 3\line_doctor_export.json",
    ]
    found = [p for pat in patterns for p in glob.glob(pat)]
    return sorted(found, key=os.path.getmtime, reverse=True)


def extract_all_from_log(text: str) -> list[dict]:
    """Returns every complete snapshot dumped in a game log, oldest first."""
    snapshots = []
    chunks: list[str] | None = None
    for raw in text.splitlines():
        if LOG_BEGIN in raw:
            chunks = []
        elif LOG_LINE in raw and chunks is not None:
            chunks.append(raw.split(LOG_LINE, 1)[1])
        elif LOG_END in raw and chunks is not None:
            try:
                snapshots.append(json.loads("".join(chunks)))
            except json.JSONDecodeError:
                pass
            chunks = None
    return snapshots


def extract_from_log(text: str) -> dict | None:
    """Returns the last snapshot dumped in a game log, or None."""
    snapshots = extract_all_from_log(text)
    return snapshots[-1] if snapshots else None


def upgrade_snapshot(snap: dict) -> dict:
    """Schema 1 stored line cargo under the engine's 1-based list index (index = cargo type + 1)."""
    if snap.get("schemaVersion", 1) < 2:
        for line in snap.get("lines") or []:
            cargo = {}
            for k, c in (line.get("cargo") or {}).items():
                if _num(c.get("capacity")) > 0 or _num(c.get("used")) > 0:
                    cargo[str(int(k) - 1)] = c
            line["cargo"] = cargo
            for v in line.get("vehicles") or []:
                if isinstance(v.get("loaded"), list):
                    v["loaded"] = sum(_num(x) for x in v["loaded"])
        snap["schemaVersion"] = 2
    return snap


def load_snapshots(path: str) -> list[dict]:
    """All snapshots of a source (log, history.jsonl or single export), oldest first."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            return [upgrade_snapshot(json.loads(text))]
        except json.JSONDecodeError:  # history.jsonl: one snapshot per line
            return [upgrade_snapshot(json.loads(l)) for l in text.splitlines() if l.strip()]
    snaps = [upgrade_snapshot(x) for x in extract_all_from_log(text)]
    if not snaps:
        raise ValueError(f"No Line Doctor export found in {path}")
    return snaps


def same_game_history(snaps: list[dict]) -> list[dict]:
    """Recent snapshots of the same game as the last one (a log can hold several games)."""
    if not snaps:
        return []
    ids = {l.get("id") for l in snaps[-1].get("lines") or []}
    out = []
    for snap in reversed(snaps):
        other = {l.get("id") for l in snap.get("lines") or []}
        if ids and len(ids & other) < 0.5 * len(ids):
            break
        if snap.get("gameTime", 0) > snaps[-1].get("gameTime", 0):
            break  # an older save was loaded after this one
        out.append(snap)
        if len(out) >= QUEUE_HISTORY:
            break
    return list(reversed(out))


def load_snapshot(path: str) -> dict:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    stripped = text.lstrip()
    if stripped.startswith("{"):
        return upgrade_snapshot(json.loads(text))
    snap = extract_from_log(text)
    if snap is None:
        raise ValueError(f"No Line Doctor export found in {path}")
    return upgrade_snapshot(snap)


# --- metrics ----------------------------------------------------------------------------------

def _num(v, default=0.0) -> float:
    try:
        f = float(v)
        return default if math.isnan(f) else f
    except (TypeError, ValueError):
        return default


def _real_times(pairs) -> list[float]:
    """realSectionTimes entries are [time, samples]; keep sections that have samples."""
    out = []
    for p in pairs or []:
        if isinstance(p, list) and len(p) >= 2 and _num(p[1]) > 0:
            out.append(_num(p[0]))
        else:
            out.append(float("nan"))
    return out


def line_metrics(line: dict, passenger_id: str | None, year_ticks: float = 1461000) -> dict:
    fin = (line.get("finance") or {})
    last = fin.get("last12Months") or {}
    prev = fin.get("previous12Months") or {}
    vehicles = line.get("vehicles") or []
    cargo = line.get("cargo") or {}

    capacity = sum(_num(c.get("capacity")) for c in cargo.values())
    used = sum(_num(c.get("used")) for c in cargo.values())
    rate = _num(line.get("ratePerYear"))
    transported = _num(line.get("transportedPerYear"))

    parts = [p for v in vehicles for p in (v.get("parts") or [])]
    ages = [_num(p.get("ageYears")) for p in parts if p.get("ageYears") is not None]
    maint = [_num(p.get("maintenanceState")) for p in parts if p.get("maintenanceState") is not None]

    per_vehicle_capacity = 0.0
    if vehicles:
        per_vehicle_capacity = sum(
            sum(_num(x) for x in (v.get("capacities") or {}).values()) for v in vehicles
        ) / len(vehicles)

    waiting_per_stop = [0.0] * len(line.get("stops") or [])
    for c in cargo.values():
        for i, w in enumerate(c.get("waitingPerStop") or []):
            if i < len(waiting_per_stop):
                waiting_per_stop[i] += _num(w)

    # timings: compare real section time (incl. stop) with real/expected drive time
    real, drive_real, drive_expected = [], [], []
    for c in cargo.values():
        real = _real_times(c.get("realSectionTimes")) or real
        drive_real = _real_times(c.get("driveRealSectionTimes")) or drive_real
        drive_expected = [_num(x) for x in (c.get("driveSectionTimesSec") or [])] or drive_expected
        if real:
            break

    def _sum_valid(xs):
        vals = [x for x in xs if not math.isnan(x)]
        return sum(vals) if vals else None

    month_sec = year_ticks / 12 / TICKS_PER_SEC
    measured = any(isinstance(p, list) and len(p) >= 2 and _num(p[1]) > 0
                   for c in cargo.values() for p in (c.get("realSectionTimes") or []))
    real_total = _sum_valid(real)
    drive_real_total = _sum_valid(drive_real)
    drive_expected_total = sum(drive_expected) if drive_expected else None

    monthly = fin.get("monthlyNet") or []
    first_active = next((i for i, x in enumerate(monthly) if x), None)
    active_months = (len(monthly) - first_active) if first_active is not None else 0
    oldest_part = max(ages) if ages else None
    if oldest_part is None:
        age_months = active_months
    elif monthly and active_months == len(monthly):
        age_months = oldest_part * 12  # older than the 12-month window: trust the fleet age
    else:
        age_months = min(active_months, oldest_part * 12)

    is_passenger = passenger_id is not None and passenger_id in cargo
    income = last.get("income")
    net = last.get("net")
    costs = None
    if last.get("vehicleRunningCosts") is not None or last.get("vehicleMaintenance") is not None:
        costs = _num(last.get("vehicleRunningCosts")) + _num(last.get("vehicleMaintenance"))

    # queue: loaded vehicles standing still (not at a terminal) on their way to the same stop
    stops_list = line.get("stops") or []
    standing: dict[int, int] = {}
    for v in vehicles:
        moving_state = str(v.get("state")) in ("1", "EN_ROUTE")
        if moving_state and _num(v.get("speed"), 1.0) < QUEUE_SPEED and _num(v.get("loaded")) > 0 \
                and v.get("engineStopIndex") is not None:
            idx = int(v["engineStopIndex"])
            standing[idx] = standing.get(idx, 0) + 1
    queue = None
    if standing:
        idx, count = max(standing.items(), key=lambda kv: kv[1])
        at_terminal = sum(1 for v in vehicles
                          if v.get("engineStopIndex") == idx and _num(v.get("daysAtTerminal")) >= STUCK_DAYS)
        if count >= QUEUE_MIN_VEHICLES and count >= QUEUE_MIN_SHARE * len(vehicles):
            queue = {"stopIndex": idx, "vehicles": count, "longAtTerminal": at_terminal,
                     "stopName": stops_list[idx].get("stationName") if 0 <= idx < len(stops_list) else None}

    # direction asymmetry on two-stop lines (expected drive times, i.e. what the route itself costs)
    asymmetry = None
    legs = [x for x in drive_expected if x]
    if len(stops_list) == 2 and len(legs) == 2 and min(legs) > 0 and max(legs) / min(legs) >= ASYMMETRY_RATIO:
        slow = 0 if legs[0] > legs[1] else 1
        asymmetry = {"ratio": max(legs) / min(legs), "slowLeg": slow, "legsSec": legs,
                     "from": stops_list[slow].get("stationName"), "to": stops_list[1 - slow].get("stationName")}

    m = {
        "id": line.get("id"),
        "name": line.get("name"),
        "kind": "passengers" if is_passenger and len(cargo) == 1 else ("mixed" if is_passenger else "cargo"),
        "transportModes": line.get("transportModes"),
        "stops": len(line.get("stops") or []),
        "vehicles": len(vehicles),
        "net12m": net,
        "income12m": income,
        "costs12m": costs,
        "netPrev12m": prev.get("net"),
        "monthlyNet": monthly,
        "ageMonths": age_months,
        "young": None,  # set below, needs the cycle time
        "lastMonthsNet": monthly[-2:],
        "netPerVehicle": (net / len(vehicles)) if (net is not None and vehicles) else None,
        "costCoverage": (income / -costs) if (income is not None and costs) else None,
        "intervalSec": line.get("intervalSec"),
        "ratePerYear": rate or None,
        "transportedPerYear": transported,
        "utilisation": (transported / rate) if rate else None,
        "loadFactorNow": (used / capacity) if capacity else None,
        "capacityPerVehicle": per_vehicle_capacity or None,
        "waitingPerStop": waiting_per_stop,
        "waitingMax": max(waiting_per_stop) if waiting_per_stop else 0,
        "roundTripRealSec": real_total,
        "roundTripDriveRealSec": drive_real_total,
        "roundTripDriveExpectedSec": drive_expected_total,
        "dwellShare": ((real_total - drive_real_total) / real_total)
        if (real_total and drive_real_total is not None and real_total > 0) else None,
        "congestion": (drive_real_total / drive_expected_total)
        if (drive_real_total and drive_expected_total) else None,
        "avgSectionDriveSec": (drive_expected_total / len(drive_expected)) if drive_expected else None,
        "avgVehicleAgeYears": (sum(ages) / len(ages)) if ages else None,
        "minMaintenanceState": min(maint) if maint else None,
        "pendingIncome": sum(_num(v.get("pendingIncome")) for v in vehicles) if any(
            v.get("pendingIncome") is not None for v in vehicles) else None,
        "cargoTypes": sorted(cargo),
        "stoppedVehicles": sum(1 for v in vehicles if v.get("userStopped")),
        "sectionsMeasured": measured,
        "queue": queue,
        "asymmetry": asymmetry,
        "stuckAtTerminal": sum(1 for v in vehicles if _num(v.get("daysAtTerminal")) >= STUCK_DAYS),
        "noPathVehicles": sum(1 for v in vehicles if v.get("noPath")),
        "vehiclesInDepot": sum(1 for v in vehicles if str(v.get("state")) in ("IN_DEPOT", "GOING_TO_DEPOT")),
        "loadModes": sorted({str(s.get("loadMode")) for s in (line.get("stops") or []) if s.get("loadMode") is not None}),
        "issues": line.get("issues") or [],
        "stopProblems": line.get("stopProblems") or [],
        "stationGroups": [s.get("stationGroup") for s in (line.get("stops") or [])],
        "stationNames": [s.get("stationName") for s in (line.get("stops") or [])],
    }
    cycle_sec = real_total if measured and real_total else drive_expected_total
    m["cycleSec"] = cycle_sec
    m["cycleMonths"] = (cycle_sec / month_sec) if cycle_sec else None
    cycles_done = (age_months / m["cycleMonths"]) if m["cycleMonths"] else None
    m["cyclesSinceStart"] = cycles_done
    m["young"] = age_months < YOUNG_LINE_MONTHS or (cycles_done is not None and cycles_done < YOUNG_LINE_CYCLES)
    return m


# --- diagnostics ------------------------------------------------------------------------------

STRUCTURAL = {"OVERCAPACITY", "COST_STRUCTURE", "EMPTY_RETURN", "LOW_FREQUENCY"}


def diagnose(m: dict, shared: dict[int, list[str]], transfers: dict | None = None) -> list[Finding]:
    transfers = transfers or {}
    f: list[Finding] = []

    def add(finding: Finding):
        # on a young line demand is still building up: keep the hint, lower the alarm
        if m["young"] and finding.code in STRUCTURAL and finding.severity in ("critical", "major"):
            finding.severity = "minor"
            finding.message = "(ligne récente, à confirmer) " + finding.message
        f.append(finding)

    if m["vehicles"] == 0:
        add(Finding("NO_VEHICLES", "critical", "La ligne n'a aucun véhicule.",
                    "Affecter des véhicules ou supprimer la ligne (elle peut encore générer des coûts d'infrastructure)."))
    if m["issues"] or m["stopProblems"]:
        add(Finding("GAME_REPORTED_ISSUES", "critical", "Le jeu signale des problèmes de configuration.",
                    "Corriger d'abord ces problèmes (arrêts inutiles, chargement impossible, pas de chemin...).",
                    {"issues": m["issues"], "stopProblems": m["stopProblems"]}))
    if m["noPathVehicles"]:
        add(Finding("NO_PATH", "critical", f"{m['noPathVehicles']} véhicule(s) sans chemin.",
                    "Vérifier les connexions voie/route, la caténaire, les sens uniques et les quais compatibles."))
    if m["stoppedVehicles"] or m["vehiclesInDepot"]:
        add(Finding("IDLE_VEHICLES", "major",
                    f"{m['stoppedVehicles']} véhicule(s) arrêté(s), {m['vehiclesInDepot']} au dépôt.",
                    "Un véhicule immobilisé coûte sans rapporter : le relancer ou le vendre."))

    # a section can only be measured once a vehicle drove it; allow the first trip from the depot
    if m["vehicles"] and not m["sectionsMeasured"] and m["cycleMonths"]             and m["ageMonths"] > 1.5 * m["cycleMonths"] + 1:
        add(Finding("NO_COMPLETED_TRIP", "critical",
                    f"Aucune section parcourue en {m['ageMonths']:.0f} mois alors qu'un aller-retour "
                    f"prend ~{m['cycleMonths']:.1f} mois.",
                    "Les véhicules ne bouclent pas leur service : regarder dans le jeu où ils sont bloqués "
                    "(arrêt inaccessible ou du mauvais côté, demi-tour impossible, sens unique, quai saturé).",
                    {"stuckAtTerminal": m["stuckAtTerminal"]}))
    elif m["vehicles"] and not m["sectionsMeasured"]:
        add(Finding("FIRST_TRIP_PENDING", "info",
                    f"Aucun trajet terminé pour l'instant (aller-retour ≈ {m['cycleMonths'] or 0:.1f} mois de calendrier).",
                    "Normal au démarrage : attendre au moins un aller-retour avant de juger la ligne."))
    if m["stuckAtTerminal"]:
        add(Finding("STUCK_AT_TERMINAL", "major", f"{m['stuckAtTerminal']} véhicule(s) au quai depuis {STUCK_DAYS} jours ou plus (≈ {STUCK_DAYS * 4} s de jeu).",
                    "Vérifier les temps d'attente/chargement complet et l'accès à l'arrêt."))

    q = m.get("queue")
    seen = m.get("queueSeen")  # (exports with a queue, exports scanned, worst queue seen)
    seen_txt = f" File observée dans {seen[0]} des {seen[1]} derniers exports." if seen and seen[1] > 1 else ""
    recurring = seen and seen[1] > 1 and seen[0] >= 2 and seen[0] >= QUEUE_RECURRING_SHARE * seen[1]
    if not q and recurring:
        q = seen[2]
        q = dict(q, current=False)
    if q:
        now = q.get("current", True)
        add(Finding("QUEUE", "critical" if q["vehicles"] >= 0.6 * m["vehicles"] else "major",
                    (f"{q['vehicles']} véhicule(s) chargé(s) sur {m['vehicles']} à l'arrêt en route vers « {q['stopName']} »"
                     if now else
                     f"File récurrente vers « {q['stopName']} » (jusqu'à {q['vehicles']} véhicules chargés à l'arrêt)")
                    + (f", {q['longAtTerminal']} au quai depuis {STUCK_DAYS} jours ou plus" if now and q["longAtTerminal"] else "")
                    + "." + seen_txt,
                    "File d'attente : le déchargement à cet arrêt est le goulot (quais trop peu nombreux, chargement lent "
                    "ou destination qui n'accepte plus). Retirer des véhicules (ils attendent sans livrer plus) et/ou "
                    "ajouter des quais ; vérifier la file dans le jeu.",
                    q))
    a = m.get("asymmetry")
    if a:
        add(Finding("ASYMMETRIC_ROUTE", "major" if q else "minor",
                    f"Le trajet {a['from']} → {a['to']} dure {a['ratio']:.1f}× plus longtemps que le retour "
                    f"({a['legsSec'][a['slowLeg']]:.0f} s contre {a['legsSec'][1 - a['slowLeg']]:.0f} s).",
                    ("Probablement l'attente de la file ci-dessus comptée dans le trajet. " if q else
                     "Détour dans un sens : sens unique, carrefour ou bretelle manquante, voie unique, forte pente. ")
                    + "Suivre un véhicule dans le sens lent pour voir où il perd du temps.",
                    a))

    util = m["utilisation"]
    if util is not None and util < LOW_UTILISATION and m["vehicles"] > 1:
        target = max(1, math.ceil(m["vehicles"] * util / 0.75))
        add(Finding("OVERCAPACITY", "major",
                    f"Utilisation de {util:.0%} de la capacité annuelle théorique.",
                    f"Trop de capacité pour la demande : passer de {m['vehicles']} à environ {target} véhicule(s), "
                    "ou des véhicules plus petits/moins chers, ou capter plus de demande (arrêts mieux placés, correspondances).",
                    {"transportedPerYear": m["transportedPerYear"], "ratePerYear": m["ratePerYear"]}))

    cap = m["capacityPerVehicle"] or 0
    load = m["loadFactorNow"]
    if cap and m["waitingMax"] > CROWD_FACTOR * cap and (load is None or load > HIGH_LOAD):
        add(Finding("UNDERCAPACITY", "major",
                    f"Jusqu'à {m['waitingMax']:.0f} en attente à un arrêt pour {cap:.0f} places par véhicule.",
                    "Demande non servie : ajouter des véhicules ou augmenter leur capacité (attention à la saturation des quais).",
                    {"waitingPerStop": m["waitingPerStop"]}))

    if m["dwellShare"] is not None and m["dwellShare"] > DWELL_RATIO:
        full_load = [lm for lm in m["loadModes"] if "FULL" in lm.upper()]
        hint = ("Des arrêts sont en « chargement complet » : les véhicules attendent la cargaison. "
                if full_load else "")
        add(Finding("LONG_DWELL", "major" if full_load else "minor",
                    f"{m['dwellShare']:.0%} du temps de cycle passé à l'arrêt.",
                    hint + "Réduire les temps d'attente minimum, éviter le chargement complet si l'offre est faible, "
                    "vérifier la longueur des quais et les vitesses de chargement.",
                    {"loadModes": m["loadModes"]}))

    if m["congestion"] is not None and m["congestion"] > CONGESTION_RATIO:
        add(Finding("CONGESTION", "major",
                    f"Temps de roulage réel {m['congestion']:.1f}× supérieur au temps théorique.",
                    "Embouteillages ou blocages : voies/routes dédiées, signalisation, évitements, "
                    "ou réduire le nombre de véhicules sur le tronçon."))

    if m["avgVehicleAgeYears"] is not None and m["avgVehicleAgeYears"] > OLD_VEHICLE_YEARS:
        add(Finding("OLD_FLEET", "minor", f"Âge moyen de la flotte : {m['avgVehicleAgeYears']:.0f} ans.",
                    "Les véhicules anciens coûtent plus cher en entretien et sont plus lents : envisager le remplacement."))
    if m["minMaintenanceState"] is not None and m["minMaintenanceState"] < LOW_MAINTENANCE_STATE:
        state = m["minMaintenanceState"]
        add(Finding("POOR_MAINTENANCE", "major" if state < 0.25 else "minor",
                    f"État des véhicules : {state:.0%} (« {condition_label(state)} » en jeu).",
                    "Un véhicule mal entretenu coûte plus cher à faire rouler, roule moins vite et offre moins de confort : "
                    "rattacher la ligne à une station de maintenance (vérifier sa capacité)."))

    if m["kind"] != "cargo" and m["intervalSec"] and m["intervalSec"] > LONG_INTERVAL_SEC:
        add(Finding("LOW_FREQUENCY", "minor", f"Un passage toutes les {m['intervalSec'] / 60:.0f} min.",
                    "Une fréquence faible rend la ligne peu attractive : plus de véhicules plus petits plutôt que peu de gros."))

    if m["avgSectionDriveSec"] is not None and m["avgSectionDriveSec"] < SHORT_SECTION_SEC and m["stops"] > 2:
        add(Finding("SHORT_HOPS", "minor", f"Inter-arrêts très courts (~{m['avgSectionDriveSec']:.0f} s de roulage).",
                    "Le revenu dépend de la distance : supprimer des arrêts intermédiaires ou allonger la ligne."))

    if m["kind"] != "passengers" and load is not None and 0.35 < load < 0.6 and m["costCoverage"] is not None \
            and m["costCoverage"] < 1:
        add(Finding("EMPTY_RETURN", "minor", "Remplissage moyen proche de 50 % sur une ligne de fret.",
                    "Probable retour à vide : chercher une cargaison retour ou mutualiser avec une autre chaîne."))

    if m["costCoverage"] is not None and m["costCoverage"] < 1 and util is not None and util >= LOW_UTILISATION             and m["sectionsMeasured"]:
        add(Finding("COST_STRUCTURE", "major",
                    f"Les recettes couvrent {m['costCoverage']:.0%} des coûts malgré une utilisation correcte.",
                    "Le matériel est trop coûteux pour ce trafic, ou les trajets sont trop courts : "
                    "véhicules moins chers, ligne plus longue, ou autre mode de transport."))

    net, prev = m["net12m"], m["netPrev12m"]
    if net is not None and prev and m["income12m"]:
        if prev - net > DECLINE_RATIO * abs(m["income12m"]):
            add(Finding("DECLINING", "minor", f"Résultat en baisse : {prev:,.0f} → {net:,.0f} sur 12 mois.",
                        "Chercher ce qui a changé : nouvelle ligne concurrente, véhicules ajoutés, industrie fermée."))
    if m["cycleMonths"] and m["cycleMonths"] > SLOW_CYCLE_MONTHS:
        add(Finding("SLOW_CYCLE", "info",
                    f"Un aller-retour dure ~{m['cycleMonths']:.1f} mois de calendrier : les recettes arrivent par à-coups.",
                    "Juger la ligne sur plusieurs cycles ; des véhicules plus rapides augmentent les rotations "
                    "(et donc les recettes) à coût de fonctionnement annuel comparable."))
    if m["young"]:
        add(Finding("YOUNG_LINE", "info", f"Ligne récente : environ {m['ageMonths']:.0f} mois d'exploitation"
                    + (f", {m['cyclesSinceStart']:.1f} aller-retour" if m["cyclesSinceStart"] is not None else "") + ".",
                    "Attendre quelques mois avant de conclure, le démarrage inclut souvent des coûts sans recettes."))
    recent = [x for x in m["lastMonthsNet"] if x is not None]
    if net is not None and net < 0 and len(recent) == 2 and all(x > 0 for x in recent):
        add(Finding("RECOVERING", "info", f"Bénéficiaire sur les 2 derniers mois ({', '.join(_money(x) for x in recent)}).",
                    "Le déficit sur 12 mois vient surtout du démarrage : surveiller avant de modifier la ligne."))
    elif len(recent) == 2 and all(x < 0 for x in recent) and m["income12m"] == 0 and m["vehicles"]             and m["sectionsMeasured"]:
        add(Finding("NO_REVENUE", "major", "Aucune recette alors que les véhicules circulent.",
                    "Personne ne monte : vérifier que les arrêts couvrent des zones habitées/de destination, "
                    "qu'il existe une demande entre ces arrêts et que le trajet n'est pas plus lent qu'à pied."))

    tr = transfers.get(m["id"])
    if tr and _num(m["net12m"]) < 0:
        slow = [t for t in tr["lines"] if t["cycleMonths"] and t["cycleMonths"] > SLOW_CYCLE_MONTHS]
        msg = (f"En correspondance avec : {', '.join(t['name'] for t in tr['lines'])}. "
               f"Résultat cumulé du groupe : {_money(tr['groupNet'])}.")
        if slow:
            msg += " Correspondance lente : " + ", ".join(f"{t['name']} (~{t['cycleMonths']:.0f} mois)" for t in slow) + "."
        add(Finding("TRANSFER_DEPENDENT", "major" if tr["groupNet"] > 0 else "minor", msg,
                    "Dans TF3, chaque segment n'est payé qu'à la livraison au destinataire final : les recettes de "
                    "cette ligne dépendent des lignes suivantes et arrivent avec leur retard. Juger le groupe, pas la "
                    "ligne seule, avant de la réduire ou de la supprimer.",
                    {"groupNet": tr["groupNet"], "lines": [t["name"] for t in tr["lines"]]}))

    overlaps = shared.get(m["id"]) or []
    if overlaps:
        add(Finding("SHARED_STATIONS", "info",
                    f"Dessert en grande partie les mêmes arrêts que : {', '.join(overlaps)}.",
                    "Lignes parallèles : possible cannibalisation (à distinguer d'une simple correspondance)."))
    return f


def shared_station_map(metrics: list[dict]) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for a in metrics:
        sa = {s for s in a["stationGroups"] if s is not None}
        if len(sa) < 2:
            continue
        for b in metrics:
            if a is b:
                continue
            sb = {s for s in b["stationGroups"] if s is not None}
            if len(sa & sb) >= max(2, math.ceil(0.5 * len(sa))):
                out.setdefault(a["id"], []).append(str(b["name"]))
    return out


def transfer_map(metrics: list[dict]) -> dict:
    """Lines sharing a station and a cargo type can exchange passengers/cargo (correspondences).

    Groups are connected components; groupNet is the summed 12-month result of the component.
    """
    by_id = {m["id"]: m for m in metrics}
    links: dict = {m["id"]: set() for m in metrics}
    for a in metrics:
        for b in metrics:
            if a is b:
                continue
            if set(a["stationGroups"]) & set(b["stationGroups"]) and set(a["cargoTypes"]) & set(b["cargoTypes"]):
                links[a["id"]].add(b["id"])
    out = {}
    for m in metrics:
        if not links[m["id"]]:
            continue
        seen, todo = {m["id"]}, [m["id"]]
        while todo:
            for n in links[todo.pop()]:
                if n not in seen:
                    seen.add(n)
                    todo.append(n)
        out[m["id"]] = {
            "lines": [{"name": by_id[n]["name"], "cycleMonths": by_id[n]["cycleMonths"]}
                      for n in sorted(links[m["id"]], key=str)],
            "groupNet": sum(_num(by_id[n]["net12m"]) for n in seen),
        }
    return out


def common_deficit_stops(metrics: list[dict]) -> list[dict]:
    """Stops shared by several deficit lines and by no profitable line: a likely common cause."""
    deficit = [m for m in metrics if _num(m["net12m"]) < 0]
    profitable = {s for m in metrics if _num(m["net12m"]) >= 0 for s in m["stationGroups"]}
    out = []
    names = {}
    for m in deficit:
        for sg, name in zip(m["stationGroups"], m["stationNames"]):
            names[sg] = name
    for sg in names:
        if sg in profitable:
            continue
        users = [m for m in deficit if sg in m["stationGroups"]]
        # only lines that can actually exchange something there (same cargo type)
        for cargo in sorted({c for m in users for c in m["cargoTypes"]}):
            same = [m["name"] for m in users if cargo in m["cargoTypes"]]
            if len(same) >= 2:
                out.append({"stationGroup": sg, "station": names[sg], "deficitLines": same})
                break
    return out


def queue_history(history: list[dict], passenger_id, year_ticks) -> dict:
    """line id -> (exports with a queue, exports scanned, largest queue seen)."""
    out: dict = {}
    for snap in history:
        for line in snap.get("lines") or []:
            q = line_metrics(line, passenger_id, year_ticks)["queue"]
            k, n, worst = out.get(line.get("id"), (0, 0, None))
            if q and (worst is None or q["vehicles"] > worst["vehicles"]):
                worst = q
            out[line.get("id")] = (k + (1 if q else 0), n + 1, worst)
    return out


def analyze(snapshot: dict, history: list[dict] | None = None) -> dict:
    pid = snapshot.get("passengerCargoTypeId")
    passenger_id = str(pid) if pid is not None else None
    year_ticks = _num(snapshot.get("yearTicks"), 1461000) or 1461000
    metrics = [line_metrics(l, passenger_id, year_ticks) for l in snapshot.get("lines") or []]
    if history and len(history) > 1:
        seen = queue_history(history, passenger_id, year_ticks)
        for m in metrics:
            m["queueSeen"] = seen.get(m["id"])
    shared = shared_station_map(metrics)
    transfers = transfer_map(metrics)
    lines = []
    for m in metrics:
        findings = sorted(diagnose(m, shared, transfers), key=lambda x: SEVERITY_ORDER[x.severity])
        lines.append({"metrics": m, "findings": [f.__dict__ for f in findings]})
    lines.sort(key=lambda l: (l["metrics"]["net12m"] is None, l["metrics"]["net12m"] or 0))
    total_net = sum(_num(l["metrics"]["net12m"]) for l in lines)
    return {
        "date": snapshot.get("date"),
        "gameTime": snapshot.get("gameTime"),
        "company": snapshot.get("company"),
        "network": snapshot.get("network"),
        "cargoNames": snapshot.get("cargoNames"),
        "exportErrors": snapshot.get("errors") or [],
        "historyExports": len(history) if history else 1,
        "summary": {
            "lines": len(lines),
            "deficitLines": sum(1 for l in lines if _num(l["metrics"]["net12m"]) < 0),
            "totalNet12m": total_net,
        },
        "commonDeficitStops": common_deficit_stops(metrics),
        "finance": finance_summary(snapshot),
        "lines": lines,
    }


# --- company finances ----------------------------------------------------------------------------

FINANCE_LABELS = {
    "INCOME": "Recettes de transport",
    "SUBSIDY": "Subventions",
    ("MAINTENANCE", "VEHICLE"): "Fonctionnement des véhicules",
    ("MAINTENANCE", "VEHICLE_MAINTENANCE"): "Entretien des véhicules",
    ("MAINTENANCE", "INFRASTRUCTURE"): "Entretien de l'infrastructure",
    ("MAINTENANCE", "OTHER"): "Autre entretien",
    "ACQUISITION": "Achats de véhicules",
    "CONSTRUCTION": "Constructions",
    "INTEREST": "Intérêts",
    "LOAN": "Emprunt (capital)",
    "OTHER": "Autres",
}


# The engine's JournalEntry enums do not iterate in Lua, so the export holds their numeric values.
# Mapping checked on a real save (2026-10-06): carrier incomes match the summed incomes of the lines per
# vehicle family (road = trucks + buses, rail = trains, water = ships, air = plane + helicopters); types by
# sign and investment rows; maintenance kinds by size. Codes not seen or not identified stay as numbers.
ENUM_CODES = {
    "type": {"0": "LOAN", "1": "INTEREST", "2": "CONSTRUCTION", "3": "ACQUISITION", "4": "MAINTENANCE",
             "5": "INCOME", "6": "OTHER", "7": "SUBSIDY"},
    "maint": {"0": "VEHICLE", "1": "INFRASTRUCTURE", "2": "OTHER", "3": "VEHICLE_MAINTENANCE"},
    "carrier": {"0": "route", "1": "rail", "2": "tram", "3": "non identifié", "4": "air", "5": "eau"},
    "construction": {"0": "routes", "1": "voies ferrées", "6": "gares, dépôts et bâtiments", "7": "autre"},
}


def _decode(field: str, value):
    if value is None:
        return None
    return ENUM_CODES.get(field, {}).get(str(value), value)


def finance_summary(snapshot: dict) -> dict | None:
    """The game's finance table grouped as {label: [value per period]}, plus headers and totals."""
    ft = snapshot.get("financeTable")
    if not ft:
        return None
    n = len(ft.get("headers") or [])
    rows: dict[str, list[float]] = {}
    for raw in ft.get("entries") or []:
        e = {k: (_decode(k, v) if k in ENUM_CODES else v) for k, v in raw.items()}
        if e.get("type") not in ("CONSTRUCTION",):
            e["construction"] = None if e.get("type") != "MAINTENANCE" else e.get("construction")
        if e.get("type") != "MAINTENANCE":
            e["maint"] = None
        t = e.get("type")
        label = FINANCE_LABELS.get((t, e.get("maint"))) or FINANCE_LABELS.get(t) or str(t)
        if t == "CONSTRUCTION" and e.get("construction"):
            label = f"Constructions : {str(e['construction']).lower()}"
        if t == "MAINTENANCE" and e.get("maint") == "INFRASTRUCTURE" and e.get("construction") is not None:
            label = f"{label} – {str(e['construction']).lower()}"
        if e.get("carrier") is not None and t in ("INCOME", "MAINTENANCE"):
            label = f"{label} ({str(e['carrier']).lower()})"
        vals = rows.setdefault(label, [0.0] * n)
        for i, v in enumerate((e.get("values") or [])[:n]):
            vals[i] += _num(v)
    return {"headers": ft.get("headers") or [], "rows": rows, "total": ft.get("total") or []}


def finance_markdown(fs: dict) -> str:
    h = fs["headers"]
    out = ["## Finances de la compagnie (tableau du jeu)", "",
           "| Poste | " + " | ".join(str(x) for x in h) + " |", "|---|" + "---|" * len(h)]
    for label, vals in sorted(fs["rows"].items(), key=lambda kv: kv[1][-2] if len(kv[1]) > 1 else kv[1][-1]):
        if not any(vals):
            continue
        out.append(f"| {label} | " + " | ".join(_money(v) for v in vals) + " |")
    out.append("| **Total** | " + " | ".join(f"**{_money(_num(v))}**" for v in fs["total"]) + " |")
    return "\n".join(out)


# --- report -----------------------------------------------------------------------------------

def _money(v) -> str:
    return "n/a" if v is None else f"{v:,.0f}".replace(",", " ")


def _pct(v) -> str:
    return "n/a" if v is None else f"{v:.0%}"


def to_markdown(result: dict, only_deficit: bool = False) -> str:
    s = result["summary"]
    d = result.get("date") or {}
    out = [f"# Line Doctor — {d.get('day', '?')}/{d.get('month', '?')}/{d.get('year', '?')}", ""]
    out.append(f"- Lignes : {s['lines']} dont **{s['deficitLines']} déficitaires**")
    out.append(f"- Résultat cumulé des lignes (12 mois) : {_money(s['totalNet12m'])}")
    comp = result.get("company") or {}
    out.append(f"- Trésorerie : {_money(comp.get('balance'))} — Emprunt : {_money(comp.get('loan'))}")
    net = result.get("network") or {}
    out.append(f"- Trains bloqués : {net.get('blockedTrains', 'n/a')} — Véhicules sans chemin : {net.get('noPathVehicles', 'n/a')}")
    if result["exportErrors"]:
        out.append(f"- ⚠️ {len(result['exportErrors'])} erreur(s) API pendant l'export (voir exportErrors)")
    for c in result.get("commonDeficitStops") or []:
        out.append(f"- 🔎 Arrêt commun aux lignes déficitaires (et à aucune ligne rentable) : **{c['station']}** "
                   f"({', '.join(c['deficitLines'])}) — indice à vérifier, pas une preuve")
    out.append("")
    if result.get("finance"):
        out.append(finance_markdown(result["finance"]))
        out.append("")
    out.append("| Ligne | Type | Véh. | Résultat 12m | Recettes | Coûts | Couverture | Utilisation | Remplissage | Intervalle | Aller-retour |")
    out.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for l in result["lines"]:
        m = l["metrics"]
        if only_deficit and _num(m["net12m"]) >= 0:
            continue
        interval = f"{m['intervalSec'] / 60:.1f} min" if m["intervalSec"] else "n/a"
        cycle = f"{m['cycleMonths']:.1f} mois" if m["cycleMonths"] else "n/a"
        out.append(f"| {m['name']} | {m['kind']} | {m['vehicles']} | {_money(m['net12m'])} | {_money(m['income12m'])} | "
                   f"{_money(m['costs12m'])} | {_pct(m['costCoverage'])} | {_pct(m['utilisation'])} | "
                   f"{_pct(m['loadFactorNow'])} | {interval} | {cycle} |")
    out.append("")
    for l in result["lines"]:
        m = l["metrics"]
        if only_deficit and _num(m["net12m"]) >= 0:
            continue
        if not l["findings"]:
            continue
        out.append(f"## {m['name']} — {_money(m['net12m'])}")
        out.append(f"Arrêts : {' → '.join(str(n) for n in m['stationNames'])}")
        if m.get("pendingIncome"):
            out.append(f"Recettes en attente de livraison finale (à bord) : {_money(m['pendingIncome'])}")
        if any(m["waitingPerStop"]):
            out.append(f"En attente par arrêt : {[int(w) for w in m['waitingPerStop']]}")
        for fd in l["findings"]:
            out.append(f"- **[{fd['severity']}] {fd['code']}** — {fd['message']}  \n  → {fd['suggestion']}")
        out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?", help="export.json, history line or game log (stdout.txt)")
    ap.add_argument("--json", help="write the full diagnostics as JSON to this file")
    ap.add_argument("--deficit-only", action="store_true", help="only list lines with a negative 12-month result")
    args = ap.parse_args(argv)

    source = args.source
    if source is None:
        candidates = default_candidates()
        if not candidates:
            print("Aucun export trouvé. Lancer le jeu avec le mod actif, ou passer le chemin du fichier.", file=sys.stderr)
            return 2
        source = candidates[0]
    snapshots = load_snapshots(source)
    snapshot = snapshots[-1]
    result = analyze(snapshot, same_game_history(snapshots))
    result["source"] = os.path.abspath(source)

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"<!-- source: {result['source']} -->")
    print(to_markdown(result, args.deficit_only))
    return 0


if __name__ == "__main__":
    sys.exit(main())
