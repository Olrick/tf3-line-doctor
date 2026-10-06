"""Infrastructure upkeep, building by building: what each station / depot / port / airport costs per year,
which lines use it and how much traffic they carry. Finds buildings to close, merge or downsize.

    python analyzer/infra.py                    # latest export, writes out/infrastructure.html

Data: snapshot["infrastructure"] (maintenance cost of every owned object). The engine's unit for
`maintenanceCost` is not documented, so costs are scaled to match the infrastructure upkeep of the game's
finance table (average of its complete periods) when it is available.
"""

from __future__ import annotations

import argparse
import html
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import analyze  # noqa: E402


def infra_upkeep_from_finance(snapshot: dict) -> float | None:
    """Yearly infrastructure upkeep from the finance table (average of the complete periods)."""
    fs = analyze.finance_summary(snapshot)
    if not fs:
        return None
    # buildings only: streets and tracks are separate rows of the finance table
    rows = [v for k, v in fs["rows"].items() if k.startswith("Entretien de l'infrastructure")
            and "routes (" not in k and "voies ferrées" not in k]
    if not rows:
        return None
    n = len(rows[0])
    complete = range(max(n - 1, 1))  # the last column is the period in progress
    per = [sum(r[i] for r in rows) for i in complete]
    return -sum(per) / len(per)


def _finance_row(snapshot: dict, needle: str) -> float:
    fs = analyze.finance_summary(snapshot)
    if not fs:
        return 0.0
    rows = [v for k, v in fs["rows"].items() if k.startswith("Entretien de l'infrastructure") and needle in k]
    if not rows:
        return 0.0
    n = len(rows[0])
    per = [sum(r[i] for r in rows) for i in range(max(n - 1, 1))]
    return -sum(per) / len(per)


def build(snapshot: dict) -> dict:
    infra = snapshot.get("infrastructure")
    if not infra:
        raise ValueError("pas de données d'infrastructure dans l'export (mettre à jour le mod et relancer le jeu)")
    raw_total = infra.get("total") or 0
    finance = infra_upkeep_from_finance(snapshot)
    scale = (finance / raw_total) if finance and raw_total else 1.0
    lines = {l["id"]: l for l in snapshot.get("lines") or []}

    buildings = []
    for b in infra.get("buildings") or []:
        served = [lines[l] for l in b.get("lines") or [] if l in lines]
        fin = [((l.get("finance") or {}).get("last12Months") or {}) for l in served]
        buildings.append({
            "name": b.get("name") or f"#{b['id']}", "kind": b.get("kind"), "file": b.get("file"),
            "cost": b["cost"] * scale,
            "lines": [l.get("name") for l in served],
            "linesNet": sum(f.get("net") or 0 for f in fin),
            "linesIncome": sum(f.get("income") or 0 for f in fin),
            "transported": sum(analyze._num(l.get("transportedPerYear")) for l in served),
            "depots": b.get("depots") or 0,
        })

    kinds: dict[str, dict] = {}
    for b in buildings:
        k = kinds.setdefault(b["kind"], {"count": 0, "cost": 0.0, "unused": 0, "unusedCost": 0.0})
        k["count"] += 1
        k["cost"] += b["cost"]
        if not b["lines"] and not b["depots"]:
            k["unused"] += 1
            k["unusedCost"] += b["cost"]
    return {
        "date": snapshot.get("date"), "scale": scale, "financeUpkeep": finance, "rawTotal": raw_total,
        "buildings": buildings, "kinds": kinds,
        "street": _finance_row(snapshot, "routes ("), "track": _finance_row(snapshot, "voies ferrées"),
        "other": (infra.get("other") or {}).get("cost", 0) * scale,
        "otherSamples": (infra.get("other") or {}).get("samples") or [],
    }


def _m(v) -> str:
    return "–" if v is None else f"{v:,.0f}".replace(",", " ")


def render(r: dict) -> str:
    d = r.get("date") or {}
    unused = [b for b in r["buildings"] if not b["lines"] and not b["depots"]]
    used = sorted((b for b in r["buildings"] if b["lines"]), key=lambda b: -b["cost"])
    kinds = sorted(r["kinds"].items(), key=lambda kv: -kv[1]["cost"])
    total = sum(b["cost"] for b in r["buildings"]) + r["street"] + r["track"] + r["other"]

    def row_kind(name, k):
        return (f"<tr><td>{html.escape(name)}</td><td class='r'>{k['count']}</td><td class='r neg'>{_m(k['cost'])}</td>"
                f"<td class='r'>{k['unused']}</td><td class='r neg'>{_m(k['unusedCost'])}</td></tr>")

    def row_building(b):
        ratio = (b["cost"] / b["linesIncome"]) if b["linesIncome"] else None
        flag = ""
        if b["lines"] and b["linesNet"] < 0:
            flag = "<span class='tag neg'>lignes déficitaires</span>"
        if ratio is not None and ratio > 0.5:
            flag += " <span class='tag neg'>coûte &gt; 50 % des recettes de ses lignes</span>"
        return (f"<tr><td><b>{html.escape(b['name'])}</b><div class='muted'>{html.escape(b['kind'])}</div></td>"
                f"<td class='r neg'>{_m(b['cost'])}</td>"
                f"<td>{html.escape(', '.join(b['lines']) or '—')} {flag}</td>"
                f"<td class='r'>{_m(b['transported'])}</td><td class='r'>{_m(b['linesIncome'])}</td>"
                f"<td class='r {'neg' if b['linesNet'] < 0 else 'pos'}'>{_m(b['linesNet'])}</td></tr>")

    scale_note = (f"Coûts recalés sur le tableau des finances du jeu (entretien de l'infrastructure ≈ {_m(r['financeUpkeep'])}/an ; "
                  f"facteur {r['scale']:.3g} appliqué au coût brut du moteur)." if r["financeUpkeep"]
                  else "Coûts bruts du moteur (unité non confirmée : pas de tableau des finances dans l'export).")
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Entretien de l'infrastructure</title>
<style>
:root {{ --bg:#f6f5f2; --card:#fff; --ink:#1d1d1b; --muted:#6b6a66; --line:#e3e1dc; --pos:#1f7a4d; --neg:#b3261e; --tag:#f6e7e5; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#16171a; --card:#202226; --ink:#ecebe8; --muted:#a3a29d; --line:#33353a; --pos:#5cc493; --neg:#ff8a80; --tag:#3a2523; }} }}
body {{ margin:0; padding:24px 16px; background:var(--bg); color:var(--ink); font:14px/1.45 system-ui, sans-serif; }}
main {{ max-width:1200px; margin:auto; }} h1 {{ font-size:22px; margin:0 0 4px; }} h2 {{ font-size:17px; margin:0 0 10px; }}
.lead, .muted {{ color:var(--muted); }} .muted {{ font-size:12px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; margin:0 0 16px; overflow-x:auto; }}
table {{ width:100%; border-collapse:collapse; }} th, td {{ padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ text-align:left; font-size:12px; color:var(--muted); }} .r {{ text-align:right; white-space:nowrap; }}
.pos {{ color:var(--pos); }} .neg {{ color:var(--neg); }}
.tag {{ display:inline-block; background:var(--tag); border-radius:4px; padding:1px 5px; font-size:11px; }}
.kpi {{ display:flex; gap:24px; flex-wrap:wrap; margin:0 0 16px; }} .kpi b {{ display:block; font-size:20px; }}
</style></head><body><main>
<h1>Entretien de l'infrastructure</h1>
<p class="lead">Simulation au {d.get('day','?')}/{d.get('month','?')}/{d.get('year','?')}. {scale_note}</p>
<div class="kpi"><div>Total par an<b class="neg">{_m(total)}</b></div>
<div>Bâtiments<b class="neg">{_m(sum(b['cost'] for b in r['buildings']))}</b></div>
<div>Routes<b class="neg">{_m(r['street'])}</b></div><div>Voies<b class="neg">{_m(r['track'])}</b></div>
<div>Autres objets<b class="neg">{_m(r['other'])}</b></div>
<div>Sans aucune ligne<b class="neg">{_m(sum(b['cost'] for b in unused))}</b></div></div>

<section class="card"><h2>Par type de bâtiment</h2><table>
<thead><tr><th>Type</th><th class='r'>Nombre</th><th class='r'>Coût / an</th><th class='r'>Sans ligne</th><th class='r'>Coût sans ligne</th></tr></thead>
<tbody>{''.join(row_kind(n, k) for n, k in kinds)}</tbody></table></section>

<section class="card"><h2>Bâtiments sans aucune ligne ({len(unused)}) — à démolir ou à réutiliser</h2><table>
<thead><tr><th>Bâtiment</th><th class='r'>Coût / an</th><th>Lignes</th><th class='r'>Transportés/an</th><th class='r'>Recettes des lignes</th><th class='r'>Résultat des lignes</th></tr></thead>
<tbody>{''.join(row_building(b) for b in sorted(unused, key=lambda b: -b['cost'])) or "<tr><td colspan=6>Aucun</td></tr>"}</tbody></table></section>

<section class="card"><h2>Bâtiments desservis, du plus coûteux au moins coûteux</h2><table>
<thead><tr><th>Bâtiment</th><th class='r'>Coût / an</th><th>Lignes qui le desservent</th><th class='r'>Transportés/an (lignes)</th><th class='r'>Recettes 12 mois (lignes)</th><th class='r'>Résultat 12 mois (lignes)</th></tr></thead>
<tbody>{''.join(row_building(b) for b in used)}</tbody></table>
<p class="muted">Recettes et résultat sont ceux des lignes entières (une ligne compte dans chaque gare qu'elle dessert) : ce sont des
ordres de grandeur pour comparer les bâtiments entre eux, pas un compte de résultat de la gare.</p></section>
</main></body></html>"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?")
    ap.add_argument("--html", default="out/infrastructure.html")
    args = ap.parse_args(argv)
    source = args.source or (analyze.default_candidates() or [None])[0]
    snaps = [s for s in analyze.load_snapshots(source) if s.get("infrastructure")] if source else []
    if not snaps:
        print("Aucun export ne contient l'infrastructure : recharger la partie avec la dernière version du mod.", file=sys.stderr)
        return 3
    r = build(snaps[-1])
    out = Path(args.html)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(r), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    unused = [b for b in r["buildings"] if not b["lines"] and not b["depots"]]
    print(f"{len(r['buildings'])} bâtiments, {len(unused)} sans ligne ({_m(sum(b['cost'] for b in unused))}/an) -> {out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
