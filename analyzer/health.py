"""Health check of the company, built for games with TF3 "inflation".

    python analyzer/health.py                 # writes out/sante.html and prints the summary

1. Ratios of the last complete period of the game's finance table, with thresholds learned on the first save
   ("TF3 A": healthy until 1959, deficit at the end):
     income / vehicle running costs   healthy ≥ 2.0   (2.2 in 1959, 1.6 in deficit)
     buildings upkeep / income        healthy ≤ 20 %  (19 % in 1959, 34 % in deficit)
     vehicle maintenance / income     healthy ≤ 9 %   (8 % in 1959, 12 % in deficit)
2. Rank and inflation: current price multiplier and the one at rank 15.
3. Stress test: the company and every line as if the company were already at rank 15 (income scaled by
   multiplier at rank 15 / current multiplier, costs unchanged).
4. Quick checks: buildings without line, lines using less than half of their capacity.
"""

from __future__ import annotations

import argparse
import html
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import analyze  # noqa: E402
import infra  # noqa: E402

# (name, value function, good threshold, bad threshold, higher is better, format)
RATIOS = [
    ("Recettes ÷ fonctionnement des véhicules", "running", 2.0, 1.7, True, "{:.2f}"),
    ("Entretien des bâtiments ÷ recettes", "buildings", 0.20, 0.28, False, "{:.0%}"),
    ("Entretien des véhicules ÷ recettes", "vmaint", 0.09, 0.11, False, "{:.0%}"),
]


def period_totals(snapshot: dict) -> dict | None:
    """Totals of the last complete period of the finance table, grouped."""
    fs = analyze.finance_summary(snapshot)
    if not fs:
        return None
    n = len(fs["headers"])
    col = max(n - 2, 0)  # the last column is the period in progress
    t = {"income": 0.0, "running": 0.0, "vmaint": 0.0, "buildings": 0.0, "roads": 0.0, "invest": 0.0}
    for label, vals in fs["rows"].items():
        v = vals[col]
        if label.startswith("Recettes") or label.startswith("Subventions"):
            t["income"] += v
        elif label.startswith("Fonctionnement"):
            t["running"] += v
        elif label.startswith("Entretien des véhicules"):
            t["vmaint"] += v
        elif label.startswith("Entretien de l'infrastructure"):
            t["roads" if ("routes (" in label or "voies ferrées" in label) else "buildings"] += v
        elif label.startswith("Achats") or label.startswith("Constructions"):
            t["invest"] += v
    t["total"] = analyze._num((snapshot.get("financeTable") or {}).get("total", [0] * n)[col])
    t["period"] = fs["headers"][col]
    return t


def ratio_values(t: dict) -> dict:
    inc = t["income"] or 1
    return {
        "running": (t["income"] / -t["running"]) if t["running"] else None,
        "buildings": -t["buildings"] / inc,
        "vmaint": -t["vmaint"] / inc,
    }


def level(value, good, bad, higher_better) -> str:
    if value is None:
        return "gris"
    if higher_better:
        return "vert" if value >= good else "orange" if value >= bad else "rouge"
    return "vert" if value <= good else "orange" if value <= bad else "rouge"


def build(snapshot: dict, history: list[dict]) -> dict:
    company = snapshot.get("company") or {}
    current = analyze._num(company.get("priceMultiplier"), 0) or None
    floor = analyze._num(company.get("priceMultiplierAtMaxRank"), 0) or None
    if current is None:  # older export: use the probe's measurement on passenger lines
        tp = snapshot.get("ticketProbe") or {}
        lines_tp = (tp.get("lines") or {}).values()
        pj = sum(x.get("journalIncome") or 0 for x in lines_tp if x.get("passenger") and not x.get("final"))
        pb = sum(x.get("basePriceSum") or 0 for x in lines_tp if x.get("passenger") and not x.get("final"))
        current = (pj / pb) if pb else 1.0
    scale = (floor / current) if (floor and current) else None  # income factor if already at rank 15

    t = period_totals(snapshot)
    ratios = []
    if t:
        vals = ratio_values(t)
        for name, key, good, bad, hb, fmt in RATIOS:
            v = vals[key]
            ratios.append({"name": name, "value": v, "text": fmt.format(v) if v is not None else "n/a",
                           "level": level(v, good, bad, hb), "good": fmt.format(good), "bad": fmt.format(bad)})
    stressed_total = None
    if t and scale is not None:
        stressed_total = t["total"] - t["income"] * (1 - scale)

    res = analyze.analyze(snapshot, analyze.same_game_history(history) if history else None)
    lines = []
    for L in res["lines"]:
        m = L["metrics"]
        net, inc = m["net12m"], m["income12m"]
        if net is None or inc is None:
            continue
        at_max = net - inc * (1 - scale) if scale is not None else None
        lines.append({"name": m["name"], "kind": m["kind"], "vehicles": m["vehicles"], "net": net, "income": inc,
                      "atMax": at_max, "utilisation": m["utilisation"], "young": m["young"]})
    fragile = sorted((l for l in lines if l["net"] >= 0 and l["atMax"] is not None and l["atMax"] < 0),
                     key=lambda l: l["atMax"])
    losing = sorted((l for l in lines if l["net"] < 0), key=lambda l: l["net"])
    oversized = sorted((l for l in lines if l["utilisation"] is not None and l["utilisation"] < 0.5
                        and l["vehicles"] >= 2 and not l["young"]), key=lambda l: l["utilisation"])

    unused = []
    if snapshot.get("infrastructure"):
        r = infra.build(snapshot)
        unused = [b for b in r["buildings"] if not b["lines"] and not b["depots"]
                  and "dépôt" not in b["kind"] and "entretien" not in b["name"].lower()]
    return {
        "date": snapshot.get("date"), "company": company, "current": current, "floor": floor, "scale": scale,
        "totals": t, "ratios": ratios, "stressedTotal": stressed_total,
        "fragile": fragile, "losing": losing, "oversized": oversized, "unused": unused,
    }


def _m(v) -> str:
    return "–" if v is None else f"{v:,.0f}".replace(",", " ")


COLORS = {"vert": "#1f7a4d", "orange": "#b7791f", "rouge": "#b3261e", "gris": "#888"}


def summary_text(h: dict) -> str:
    c = h["company"]
    out = []
    rank = c.get("rank")
    out.append(f"Rang {rank if rank is not None else '?'}/15 · multiplicateur de prix {h['current']:.3f}"
               + (f" · au rang 15 : {h['floor']:.2f}" if h["floor"] else " · réglage d'inflation inconnu"))
    for r in h["ratios"]:
        out.append(f"[{r['level']}] {r['name']} : {r['text']} (vert si {'≥' if r['name'].startswith('Recettes') else '≤'} {r['good']})")
    if h["totals"]:
        out.append(f"Résultat de la période {h['totals']['period']} : {_m(h['totals']['total'])}"
                   + (f" ; au rang 15 : {_m(h['stressedTotal'])}" if h["stressedTotal"] is not None else ""))
    out.append(f"{len(h['fragile'])} ligne(s) rentables aujourd'hui mais déficitaires au rang 15 ; "
               f"{len(h['losing'])} déjà déficitaires ; {len(h['oversized'])} utilisées à moins de 50 % ; "
               f"{len(h['unused'])} bâtiment(s) sans ligne")
    return "\n".join(out)


def render(h: dict) -> str:
    d = h.get("date") or {}
    cards = "".join(
        f"<div class='kpi' style='border-color:{COLORS[r['level']]}'><div class='muted'>{html.escape(r['name'])}</div>"
        f"<b style='color:{COLORS[r['level']]}'>{r['text']}</b><div class='muted'>vert {r['good']} · rouge {r['bad']}</div></div>"
        for r in h["ratios"])

    def rows(items, extra):
        return "".join(f"<tr><td>{html.escape(l['name'])}</td><td>{l['kind']}</td><td class='r'>{l['vehicles']}</td>"
                       f"<td class='r {'neg' if l['net'] < 0 else 'pos'}'>{_m(l['net'])}</td>{extra(l)}</tr>" for l in items) \
            or "<tr><td colspan=6>Aucune</td></tr>"

    at_max = lambda l: f"<td class='r {'neg' if (l['atMax'] or 0) < 0 else 'pos'}'>{_m(l['atMax'])}</td>"
    util = lambda l: f"<td class='r'>{l['utilisation']:.0%}</td>"
    c = h["company"]
    stress = ""
    if h["totals"]:
        stress = (f"<p>Résultat de la dernière période complète : <b class='{'neg' if h['totals']['total'] < 0 else 'pos'}'>"
                  f"{_m(h['totals']['total'])}</b>"
                  + (f" — <b>au rang 15 : <span class='{'neg' if h['stressedTotal'] < 0 else 'pos'}'>{_m(h['stressedTotal'])}</span></b>"
                     if h["stressedTotal"] is not None else "") + "</p>")
    unused = "".join(f"<li>{html.escape(b['name'])} ({html.escape(b['kind'])}, {_m(b['cost'])}/an)</li>" for b in h["unused"]) or "<li>Aucun</li>"
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bilan de santé</title>
<style>
:root {{ --bg:#f6f5f2; --card:#fff; --ink:#1d1d1b; --muted:#6b6a66; --line:#e3e1dc; --pos:#1f7a4d; --neg:#b3261e; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#16171a; --card:#202226; --ink:#ecebe8; --muted:#a3a29d; --line:#33353a; --pos:#5cc493; --neg:#ff8a80; }} }}
body {{ margin:0; padding:24px 16px; background:var(--bg); color:var(--ink); font:14px/1.45 system-ui, sans-serif; }}
main {{ max-width:1100px; margin:auto; }} h1 {{ font-size:22px; margin:0 0 4px; }} h2 {{ font-size:17px; margin:0 0 10px; }}
.muted {{ color:var(--muted); font-size:12px; }} .card {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; margin:0 0 16px; overflow-x:auto; }}
.kpis {{ display:flex; gap:12px; flex-wrap:wrap; }} .kpi {{ border:2px solid; border-radius:8px; padding:10px 14px; min-width:220px; }} .kpi b {{ font-size:22px; }}
table {{ width:100%; border-collapse:collapse; }} th, td {{ padding:5px 8px; border-bottom:1px solid var(--line); }}
th {{ text-align:left; font-size:12px; color:var(--muted); }} .r {{ text-align:right; white-space:nowrap; }}
.pos {{ color:var(--pos); }} .neg {{ color:var(--neg); }}
</style></head><body><main>
<h1>Bilan de santé</h1>
<p class="muted">Simulation au {d.get('day','?')}/{d.get('month','?')}/{d.get('year','?')} · seuils tirés de « TF3 A » (sain jusqu'en 1959, déficitaire ensuite).</p>
<section class="card"><h2>Rang et inflation</h2>
<p>Rang <b>{c.get('rank', '?')}</b> / 15 · multiplicateur de prix actuel <b>{h['current']:.3f}</b>
{f"· au rang 15 : <b>{h['floor']:.2f}</b> (recettes ×{h['scale']:.2f} par rapport à aujourd'hui)" if h['scale'] is not None else "· réglage d'inflation inconnu"}</p>
{stress}</section>
<section class="card"><h2>Ratios (dernière période complète)</h2><div class="kpis">{cards}</div></section>
<section class="card"><h2>Lignes rentables aujourd'hui, déficitaires au rang 15 ({len(h['fragile'])})</h2>
<table><thead><tr><th>Ligne</th><th>Type</th><th class='r'>Véh.</th><th class='r'>Résultat 12 mois</th><th class='r'>Au rang 15</th></tr></thead>
<tbody>{rows(h['fragile'], at_max)}</tbody></table></section>
<section class="card"><h2>Lignes déjà déficitaires ({len(h['losing'])})</h2>
<table><thead><tr><th>Ligne</th><th>Type</th><th class='r'>Véh.</th><th class='r'>Résultat 12 mois</th><th class='r'>Au rang 15</th></tr></thead>
<tbody>{rows(h['losing'], at_max)}</tbody></table></section>
<section class="card"><h2>Lignes utilisées à moins de 50 % ({len(h['oversized'])})</h2>
<table><thead><tr><th>Ligne</th><th>Type</th><th class='r'>Véh.</th><th class='r'>Résultat 12 mois</th><th class='r'>Utilisation</th></tr></thead>
<tbody>{rows(h['oversized'], util)}</tbody></table></section>
<section class="card"><h2>Bâtiments sans ligne ({len(h['unused'])})</h2><ul>{unused}</ul></section>
</main></body></html>"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?")
    ap.add_argument("--html", default="out/sante.html")
    args = ap.parse_args(argv)
    snaps = analyze.load_history(args.source)
    if not snaps:
        print("Aucun export trouvé.", file=sys.stderr)
        return 2
    h = build(snaps[-1], snaps)
    out = Path(args.html)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(h), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(summary_text(h))
    print(f"-> {out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
