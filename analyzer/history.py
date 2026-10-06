"""Company history, year by year, from the game's own journal (financeHistory in the export).

    python analyzer/history.py                 # writes out/historique.html

Groups the finance table rows (income, vehicle running costs, vehicle maintenance, buildings, streets and
tracks, purchases, construction, loans) per simulated year, shows them as a chart and a table, and compares
the years before and after a pivot year (default: the year the balance peaked) to show what drifted.
"""

from __future__ import annotations

import argparse
import html
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import analyze  # noqa: E402

GROUPS = [
    ("Recettes", "#1f7a4d"),
    ("Fonctionnement des véhicules", "#c0392b"),
    ("Entretien des véhicules", "#e67e22"),
    ("Entretien des bâtiments", "#8e44ad"),
    ("Entretien routes et voies", "#7f8c8d"),
    ("Achats de véhicules", "#2c3e50"),
    ("Constructions", "#16a085"),
    ("Emprunts et intérêts", "#b7950b"),
    ("Autres", "#95a5a6"),
]


def group_of(label: str) -> str:
    if label.startswith("Recettes") or label.startswith("Subventions"):
        return "Recettes"
    if label.startswith("Fonctionnement"):
        return "Fonctionnement des véhicules"
    if label.startswith("Entretien des véhicules"):
        return "Entretien des véhicules"
    if label.startswith("Entretien de l'infrastructure"):
        return "Entretien routes et voies" if ("routes (" in label or "voies ferrées" in label) else "Entretien des bâtiments"
    if label.startswith("Achats"):
        return "Achats de véhicules"
    if label.startswith("Constructions"):
        return "Constructions"
    if label.startswith("Intérêts") or label.startswith("Emprunt"):
        return "Emprunts et intérêts"
    return "Autres"


def build(snapshot: dict) -> dict:
    ft = snapshot.get("financeHistory")
    if not ft:
        raise ValueError("pas d'historique dans l'export (mettre à jour le mod et recharger la partie)")
    fs = analyze.finance_summary({"financeTable": ft})
    headers = [str(h) for h in fs["headers"]]
    n = len(headers)
    groups = {g: [0.0] * n for g, _ in GROUPS}
    modes: dict[str, list[float]] = {}
    for label, vals in fs["rows"].items():
        g = group_of(label)
        for i, v in enumerate(vals):
            groups[g][i] += v
        if "(" in label and label.endswith(")"):
            mode = label.rsplit("(", 1)[1][:-1]
            m = modes.setdefault(mode, [0.0] * n)
            for i, v in enumerate(vals):
                m[i] += v
    for key in ("interest", "loanBorrowing", "loanRepayment"):
        for i, v in enumerate((ft.get(key) or [])[:n]):
            groups["Emprunts et intérêts"][i] += analyze._num(v)
    total = [analyze._num(v) for v in (ft.get("total") or [])][:n]
    balance = [analyze._num(v) for v in (ft.get("balance") or [])][:n]
    # drop the leading years before the company existed
    first = next((i for i in range(n) if any(groups[g][i] for g in groups) or (balance and balance[i])), 0)
    sl = slice(first, n)
    return {
        "headers": headers[sl], "groups": {g: v[sl] for g, v in groups.items()},
        "modes": {m: v[sl] for m, v in modes.items()}, "total": total[sl], "balance": balance[sl],
    }


def _m(v) -> str:
    return "–" if v is None else f"{v:,.0f}".replace(",", " ")


def _svg(headers, series, height=280, width=1100) -> str:
    """Simple multi-line chart (values in millions)."""
    vals = [v for _, vs, _ in series for v in vs]
    if not vals:
        return ""
    lo, hi = min(vals + [0]), max(vals + [0])
    span = (hi - lo) or 1
    n = len(headers)
    x = lambda i: 50 + (width - 70) * (i / max(n - 1, 1))
    y = lambda v: 10 + (height - 40) * (1 - (v - lo) / span)
    parts = [f'<line x1="50" x2="{width - 20}" y1="{y(0):.1f}" y2="{y(0):.1f}" stroke="currentColor" stroke-opacity=".3"/>']
    step = max(1, n // 12)
    for i in range(0, n, step):
        parts.append(f'<text x="{x(i):.1f}" y="{height - 8}" font-size="11" text-anchor="middle" fill="currentColor" opacity=".7">{html.escape(headers[i])}</text>')
    for frac in (0, .25, .5, .75, 1):
        v = lo + span * frac
        parts.append(f'<text x="44" y="{y(v) + 4:.1f}" font-size="11" text-anchor="end" fill="currentColor" opacity=".7">{v / 1e6:.0f} M</text>')
    for name, vs, color in series:
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vs))
        parts.append(f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{pts}"><title>{html.escape(name)}</title></polyline>')
    legend = "".join(f'<span class="lg"><i style="background:{c}"></i>{html.escape(nm)}</span>' for nm, _, c in series)
    return f'<svg viewBox="0 0 {width} {height}" width="100%" role="img">{"".join(parts)}</svg><div class="legend">{legend}</div>'


def compare(h: dict, pivot: int) -> list[tuple[str, float, float]]:
    """Average per year of each group, the 5 years before the pivot vs the 5 years after."""
    before = range(max(0, pivot - 5), pivot)
    after = range(pivot, min(len(h["headers"]), pivot + 5))
    out = []
    for g, _ in GROUPS:
        v = h["groups"][g]
        b = sum(v[i] for i in before) / max(len(before), 1)
        a = sum(v[i] for i in after) / max(len(after), 1)
        out.append((g, b, a))
    return out


def render(h: dict, pivot: int | None = None) -> str:
    headers = h["headers"]
    if pivot is None:
        pivot = max(range(len(h["balance"])), key=lambda i: h["balance"][i]) if h["balance"] else len(headers) // 2
    chart = _svg(headers, [(g, h["groups"][g], c) for g, c in GROUPS if any(h["groups"][g])] + [("Résultat", h["total"], "#000")])
    bal = _svg(headers, [("Trésorerie", h["balance"], "#1f4e79")], height=200)
    cmp_rows = "".join(
        f"<tr><td>{html.escape(g)}</td><td class='r'>{_m(b)}</td><td class='r'>{_m(a)}</td>"
        f"<td class='r {'neg' if a - b < 0 else 'pos'}'>{_m(a - b)}</td></tr>" for g, b, a in compare(h, pivot))
    table_rows = "".join(
        f"<tr><td>{html.escape(headers[i])}</td>" + "".join(f"<td class='r'>{_m(h['groups'][g][i])}</td>" for g, _ in GROUPS)
        + f"<td class='r {'neg' if h['total'][i] < 0 else 'pos'}'><b>{_m(h['total'][i])}</b></td>"
          f"<td class='r'>{_m(h['balance'][i] if i < len(h['balance']) else None)}</td></tr>"
        for i in range(len(headers)))
    modes = "".join(
        f"<tr><td>{html.escape(m)}</td>" + "".join(f"<td class='r'>{_m(v[i])}</td>" for i in range(max(0, pivot - 5), min(len(headers), pivot + 5))) + "</tr>"
        for m, v in sorted(h["modes"].items()))
    mode_head = "".join(f"<th class='r'>{html.escape(headers[i])}</th>" for i in range(max(0, pivot - 5), min(len(headers), pivot + 5)))
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Historique de la compagnie</title>
<style>
:root {{ --bg:#f6f5f2; --card:#fff; --ink:#1d1d1b; --muted:#6b6a66; --line:#e3e1dc; --pos:#1f7a4d; --neg:#b3261e; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#16171a; --card:#202226; --ink:#ecebe8; --muted:#a3a29d; --line:#33353a; --pos:#5cc493; --neg:#ff8a80; }} }}
body {{ margin:0; padding:24px 16px; background:var(--bg); color:var(--ink); font:14px/1.45 system-ui, sans-serif; }}
main {{ max-width:1200px; margin:auto; }} h1 {{ font-size:22px; margin:0 0 4px; }} h2 {{ font-size:17px; margin:0 0 10px; }}
.lead {{ color:var(--muted); }} .card {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; margin:0 0 16px; overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; }} th, td {{ padding:4px 8px; border-bottom:1px solid var(--line); }}
th {{ text-align:left; font-size:12px; color:var(--muted); }} .r {{ text-align:right; white-space:nowrap; }}
.pos {{ color:var(--pos); }} .neg {{ color:var(--neg); }}
.legend {{ display:flex; flex-wrap:wrap; gap:12px; font-size:12px; margin-top:6px; }} .lg i {{ display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:4px; }}
</style></head><body><main>
<h1>Historique de la compagnie</h1>
<p class="lead">Journal comptable du jeu, une colonne par année de simulation. Pivot : <b>{html.escape(headers[pivot])}</b>
(année du pic de trésorerie). Montants par an.</p>
<section class="card"><h2>Trésorerie</h2>{bal}</section>
<section class="card"><h2>Postes par an</h2>{chart}</section>
<section class="card"><h2>Ce qui a changé autour du pic (moyenne des 5 ans avant / après {html.escape(headers[pivot])})</h2>
<table><thead><tr><th>Poste</th><th class='r'>5 ans avant</th><th class='r'>5 ans après</th><th class='r'>Écart</th></tr></thead>
<tbody>{cmp_rows}</tbody></table></section>
<section class="card"><h2>Par mode de transport autour du pic</h2>
<table><thead><tr><th>Mode</th>{mode_head}</tr></thead><tbody>{modes}</tbody></table></section>
<section class="card"><h2>Détail année par année</h2>
<table><thead><tr><th>Année</th>{''.join(f"<th class='r'>{html.escape(g)}</th>" for g, _ in GROUPS)}<th class='r'>Résultat</th><th class='r'>Trésorerie</th></tr></thead>
<tbody>{table_rows}</tbody></table></section>
</main></body></html>"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?")
    ap.add_argument("--html", default="out/historique.html")
    ap.add_argument("--pivot", help="année pivot (texte de l'en-tête de colonne), par défaut le pic de trésorerie")
    args = ap.parse_args(argv)
    snaps = [s for s in analyze.load_history(args.source) if s.get("financeHistory")]
    if not snaps:
        print("Aucun export ne contient l'historique : recharger la partie avec la dernière version du mod.", file=sys.stderr)
        return 3
    h = build(snaps[-1])
    pivot = h["headers"].index(args.pivot) if args.pivot and args.pivot in h["headers"] else None
    out = Path(args.html)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(h, pivot), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"{len(h['headers'])} années -> {out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
