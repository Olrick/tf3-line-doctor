"""End-to-end view of every cargo chain: each step (line), its pending income, then the chain totals.

    python analyzer/chains.py                       # latest export (game log or export file), writes out/chaines.html
    python analyzer/chains.py --html report.html --cargo Bois

Data comes from the Line Doctor export (snapshot["pendingIncome"]):
  chains   : complete chains that really delivered, key "cargo|lineA>lineB", with deliveries and summed price
  prefixes : cargo items still in the network, grouped by the lines already used (partial chain), with the
             estimated pending share of each completed step ("done") and of the step being driven ("inProgress")

A partial chain is attached to the complete chains it is the beginning of (weighted by their deliveries).
Partial chains that match no delivered chain are shown as "jamais livrée" (new or broken chains).
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import analyze  # noqa: E402


@dataclass
class Step:
    line: int
    name: str
    stops: list[str]
    vehicles: int
    net12m: float | None
    shared: int = 1          # number of chains using this line
    share: float = 1.0       # part of the line's result attributed to this chain
    allocated: float = 0.0   # share x the line's 12-month result
    done: float = 0.0        # pending income of completed segments on this step
    in_progress: float = 0.0  # pending income of the segment being driven on this step
    waiting_after: float = 0.0  # cargo units waiting at the end of this step for the next one
    on_board: float = 0.0       # cargo units in this step's vehicles


@dataclass
class Chain:
    key: str
    cargo: str
    lines: list[int]
    deliveries: int = 0
    price_sum: float = 0.0
    first: float | None = None
    last: float | None = None
    delivered: bool = True
    steps: list[Step] = field(default_factory=list)

    @property
    def pending(self) -> float:
        return sum(s.done for s in self.steps)

    @property
    def in_progress(self) -> float:
        return sum(s.in_progress for s in self.steps)

    @property
    def units(self) -> float:
        return sum(s.waiting_after + s.on_board for s in self.steps)

    @property
    def net12m(self) -> float:
        return sum(s.net12m or 0 for s in self.steps)

    @property
    def allocated(self) -> float:
        """12-month result of the chain: each line's result split among the chains using it."""
        return sum(s.allocated for s in self.steps)

    @property
    def potential(self) -> float:
        """Allocated result + income already earned but not booked yet."""
        return self.allocated + self.pending + self.in_progress


def _key(cargo, lines) -> str:
    return f"{cargo}|" + ">".join(str(l) for l in lines)


def build(snapshot: dict) -> tuple[list[Chain], dict]:
    pi = snapshot.get("pendingIncome") or {}
    if "error" in pi:
        raise ValueError(f"pendingIncome: {pi['error']}")
    names = snapshot.get("cargoNames") or {}
    k = pi.get("k") or 0.804
    lines = {l["id"]: l for l in snapshot.get("lines") or []}

    def step_for(line_id) -> Step:
        l = lines.get(line_id) or {}
        fin = (l.get("finance") or {}).get("last12Months") or {}
        return Step(line=line_id, name=l.get("name") or f"ligne {line_id} (supprimée ?)",
                    stops=[s.get("stationName") or "?" for s in l.get("stops") or []],
                    vehicles=len(l.get("vehicles") or []), net12m=fin.get("net"))

    chains: dict[str, Chain] = {}
    for key, c in (pi.get("chains") or {}).items():
        ch = Chain(key=key, cargo=names.get(str(c["cargoType"]), str(c["cargoType"])), lines=list(c["lines"]),
                   deliveries=c.get("n", 0), price_sum=c.get("price", 0), first=c.get("first"), last=c.get("last"))
        ch.steps = [step_for(l) for l in ch.lines]
        chains[key] = ch

    # attach every partial chain to the delivered chains it starts, weighted by their deliveries
    for p in pi.get("prefixes") or []:
        cargo, seq = p["cargoType"], list(p["lines"])
        cargo_name = names.get(str(cargo), str(cargo))
        targets = [c for c in chains.values()
                   if c.delivered and c.cargo == cargo_name and c.lines[:len(seq)] == seq]
        if not targets:
            key = _key(cargo, seq) + " (jamais livrée)"
            if key not in chains:
                ch = Chain(key=key, cargo=cargo_name, lines=seq, delivered=False)
                ch.steps = [step_for(l) for l in seq]
                chains[key] = ch
            targets = [chains[key]]
        weights = [max(c.deliveries, 1) for c in targets]
        total_w = sum(weights)
        for c, w in zip(targets, weights):
            share = w / total_w
            for j, value in enumerate(p.get("done") or []):
                c.steps[j].done += value * share
            last = c.steps[len(seq) - 1]
            last.in_progress += (p.get("inProgress") or 0) * share
            if p.get("inVehicle"):
                last.on_board += p["units"] * share
            else:
                last.waiting_after += p["units"] * share

    uses: dict[int, int] = {}
    for c in chains.values():
        for l in set(c.lines):
            uses[l] = uses.get(l, 0) + 1
    # split each line's result among its chains, by delivered units (new chains weigh their units in transit)
    weights: dict[int, dict[str, float]] = {}
    for c in chains.values():
        w = c.deliveries if c.deliveries else max(c.units, 1) * 0.1
        for l in c.lines:
            weights.setdefault(l, {})
            weights[l][c.key] = weights[l].get(c.key, 0) + w
    for c in chains.values():
        for s in c.steps:
            s.shared = uses.get(s.line, 1)
            total = sum(weights[s.line].values())
            s.share = weights[s.line][c.key] / total if total else 1.0
            s.allocated = (s.net12m or 0) * s.share

    meta = {"date": snapshot.get("date"), "k": k, "items": pi.get("items"), "skipped": pi.get("skipped"),
            "yearTicks": snapshot.get("yearTicks") or 1461000, "gameTime": snapshot.get("gameTime")}
    ordered = sorted(chains.values(), key=lambda c: -(c.pending + c.in_progress))
    return ordered, meta


# --- HTML ------------------------------------------------------------------------------------------

def _m(v) -> str:
    if v is None:
        return "–"
    return f"{v:,.0f}".replace(",", " ")


def _cls(v) -> str:
    return "neg" if (v or 0) < 0 else "pos"


def render(chains: list[Chain], meta: dict, title="Chaînes de transport") -> str:
    d = meta.get("date") or {}
    year = meta["yearTicks"]
    cards = []
    for c in chains:
        rows = []
        for i, s in enumerate(c.steps, 1):
            shared = f' <span class="tag">partagée ×{s.shared}</span>' if s.shared > 1 else ""
            part = f"{s.share:.0%} → {_m(s.allocated)}" if s.shared > 1 else _m(s.allocated)
            rows.append(
                f"<tr><td class='n'>{i}</td><td><b>{html.escape(s.name)}</b>{shared}"
                f"<div class='stops'>{html.escape(' → '.join(s.stops))}</div></td>"
                f"<td class='r'>{s.vehicles}</td>"
                f"<td class='r {_cls(s.net12m)}'>{_m(s.net12m)}</td>"
                f"<td class='r {_cls(s.allocated)}'>{part}</td>"
                f"<td class='r pos'>{_m(s.done)}</td><td class='r'>{_m(s.in_progress)}</td>"
                f"<td class='r'>{s.on_board:.0f} / {s.waiting_after:.0f}</td></tr>")
        per = c.price_sum / c.deliveries * meta["k"] if c.deliveries else None
        rate = ""
        if c.deliveries and c.first is not None and c.last and (c.last - c.first) >= year / 6:
            # only extrapolate over at least two months of observation
            rate = f" · ≈ {c.deliveries / ((c.last - c.first) / year):.0f} unités/an"
        status = (f"{c.deliveries} unités livrées observées{rate} · ≈ {_m(per)} par unité livrée"
                  if c.delivered else "<b class='neg'>jamais livrée depuis l'installation du mod</b> (chaîne neuve ou interrompue)")
        cards.append(f"""
<section class="card">
  <h2>{html.escape(c.cargo)} <span class="sub">{len(c.steps)} étape(s) · résultat de la chaîne
    <b class="{_cls(c.allocated)}">{_m(c.allocated)}</b> · potentiel avec l'attente
    <b class="{_cls(c.potential)}">{_m(c.potential)}</b></span></h2>
  <p class="status">{status}</p>
  <table>
    <thead><tr><th>#</th><th>Ligne et arrêts</th><th class='r'>Véh.</th><th class='r'>Résultat 12 mois de la ligne</th>
    <th class='r'>Part pour cette chaîne</th>
    <th class='r'>En attente</th><th class='r'>En cours</th><th class='r'>Unités à bord / en attente</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
    <tfoot><tr><td></td><td><b>Total chaîne</b></td><td></td><td></td>
      <td class='r {_cls(c.allocated)}'><b>{_m(c.allocated)}</b></td>
      <td class='r pos'><b>{_m(c.pending)}</b></td><td class='r'><b>{_m(c.in_progress)}</b></td>
      <td class='r'><b>{c.units:.0f}</b></td></tr></tfoot>
  </table>
</section>""")
    total_pending = sum(c.pending for c in chains)
    total_progress = sum(c.in_progress for c in chains)
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root {{ --bg:#f6f5f2; --card:#fff; --ink:#1d1d1b; --muted:#6b6a66; --line:#e3e1dc; --pos:#1f7a4d; --neg:#b3261e; --tag:#eef2f7; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#16171a; --card:#202226; --ink:#ecebe8; --muted:#a3a29d; --line:#33353a; --pos:#5cc493; --neg:#ff8a80; --tag:#2b3340; }} }}
body {{ margin:0; padding:24px 16px; background:var(--bg); color:var(--ink); font:14px/1.45 system-ui, sans-serif; }}
main {{ max-width:1100px; margin:auto; }}
h1 {{ font-size:22px; margin:0 0 4px; }} .lead {{ color:var(--muted); margin:0 0 20px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; margin:0 0 16px; overflow-x:auto; }}
h2 {{ font-size:17px; margin:0; }} .sub, .status, .stops {{ color:var(--muted); font-weight:normal; font-size:13px; }}
.status {{ margin:4px 0 10px; }}
table {{ width:100%; border-collapse:collapse; }} th, td {{ padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ text-align:left; font-size:12px; color:var(--muted); font-weight:600; }} tfoot td {{ border-bottom:none; }}
.r {{ text-align:right; white-space:nowrap; }} .n {{ color:var(--muted); width:1%; }}
.pos {{ color:var(--pos); }} .neg {{ color:var(--neg); }}
.tag {{ background:var(--tag); border-radius:4px; padding:1px 5px; font-size:11px; font-weight:normal; }}
.kpi {{ display:flex; gap:24px; flex-wrap:wrap; margin:0 0 20px; }} .kpi div b {{ display:block; font-size:20px; }}
</style></head><body><main>
<h1>{html.escape(title)}</h1>
<p class="lead">État au {d.get('day','?')}/{d.get('month','?')}/{d.get('year','?')} · {meta.get('items') or 0} marchandises en route analysées
({meta.get('skipped') or 0} ignorées faute de position). Montants estimés : prix final ≈ facteur appris × distance directe,
part de chaque étape ∝ longueur de son segment. Passagers exclus (payés à chaque trajet).</p>
<div class="kpi"><div>En attente (étapes terminées)<b class="pos">{_m(total_pending)}</b></div>
<div>En cours (segments en route)<b>{_m(total_progress)}</b></div><div>Chaînes<b>{len(chains)}</b></div></div>
{''.join(cards)}
<p class="lead">« Part pour cette chaîne » : le résultat 12 mois d'une ligne partagée est réparti entre ses chaînes au prorata
des unités livrées. « Résultat de la chaîne » = somme de ces parts ; « potentiel » = ce résultat + l'attente + l'en cours
(recettes déjà gagnées, versées à la livraison finale).</p>
</main></body></html>"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?", help="export.json or game log (default: latest found)")
    ap.add_argument("--html", default="out/chaines.html", help="output HTML file")
    ap.add_argument("--cargo", help="only chains whose cargo name contains this text")
    ap.add_argument("--deficit", action="store_true", help="only chains whose result is negative, worst first")
    args = ap.parse_args(argv)
    source = args.source or (analyze.default_candidates() or [None])[0]
    if not source:
        print("Aucun export trouvé.", file=sys.stderr)
        return 2
    snaps = [s for s in analyze.load_snapshots(source) if (s.get("pendingIncome") or {}).get("prefixes") is not None]
    if not snaps:
        print("Aucun export ne contient encore les chaînes : relancer le jeu avec la dernière version du mod.", file=sys.stderr)
        return 3
    chains, meta = build(snaps[-1])
    if args.cargo:
        chains = [c for c in chains if args.cargo.lower() in c.cargo.lower()]
    title = "Chaînes de transport"
    if args.deficit:
        chains = sorted((c for c in chains if c.allocated < 0), key=lambda c: c.allocated)
        title = "Chaînes déficitaires"
    out = Path(args.html)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(chains, meta, title), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"{len(chains)} chaînes -> {out.resolve()}")
    for c in chains[:10]:
        print(f"  {c.cargo:12} {' > '.join(s.name for s in c.steps)} | résultat {_m(c.allocated)} | en attente {_m(c.pending)} | en cours {_m(c.in_progress)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
