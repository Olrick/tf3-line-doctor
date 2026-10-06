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
    price_share: float | None = None  # part of the chain's price earned by this step (segment length)
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
    destination: str = "?"
    source: str = ""                  # producing industry (links chains into production trees)
    unit_value: float | None = None   # booked income of one delivered unit (whole chain)

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
        if c.get("nShare"):
            for st, sh in zip(ch.steps, c.get("stepShare") or []):
                st.price_share = sh / c["nShare"]
        if ch.deliveries:
            ch.unit_value = ch.price_sum / ch.deliveries * k
        targets = c.get("targets") or {}
        if targets:
            best = max(targets.items(), key=lambda kv: kv[1])[0]
            ch.destination = (pi.get("targetNames") or {}).get(best) or ""
        sources = c.get("sources") or {}
        if sources:
            best = max(sources.items(), key=lambda kv: kv[1])[0]
            ch.source = (pi.get("targetNames") or {}).get(best) or ""
        if not ch.destination or ch.destination == "?":
            last_stops = ch.steps[-1].stops if ch.steps else []
            ch.destination = f"livré via {last_stops[-1]}" if last_stops else "?"
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
                ch.destination = "pas encore livrée"
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
        base = c.deliveries if c.deliveries else max(c.units, 1) * 0.1
        for st in c.steps:
            # revenue this chain brings to the line: deliveries x unit price x share of that step
            w = base * (c.price_sum / c.deliveries if c.deliveries else 1) * (st.price_share or 1 / len(c.steps))
            weights.setdefault(st.line, {})
            weights[st.line][c.key] = weights[st.line].get(c.key, 0) + w
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


# --- production trees -----------------------------------------------------------------------------

@dataclass
class Tree:
    chain: Chain
    inputs: list["Tree"] = field(default_factory=list)

    def all_chains(self) -> list[Chain]:
        out = [self.chain]
        for t in self.inputs:
            out += t.all_chains()
        return out

    @property
    def allocated(self) -> float:
        return sum(c.allocated for c in self.all_chains())

    @property
    def potential(self) -> float:
        return sum(c.potential for c in self.all_chains())


def build_trees(chains: list[Chain]) -> list[Tree]:
    """Roots are chains whose customer produces nothing that is transported further; the inputs of a
    chain are the chains delivering to the industry where its cargo comes from (matched by name)."""
    by_destination: dict[str, list[Chain]] = {}
    for c in chains:
        if c.delivered and c.destination:
            by_destination.setdefault(c.destination, []).append(c)
    sources = {c.source for c in chains if c.source}

    def grow(chain: Chain, seen: set[str]) -> Tree:
        tree = Tree(chain)
        if chain.source and chain.source not in seen:
            for feeder in sorted(by_destination.get(chain.source, []), key=lambda c: c.allocated):
                if feeder is not chain:
                    tree.inputs.append(grow(feeder, seen | {chain.source}))
        return tree

    roots = [c for c in chains if not (c.delivered and c.destination in sources)]
    return sorted((grow(c, set()) for c in roots), key=lambda t: t.allocated)


# --- HTML ------------------------------------------------------------------------------------------

def _m(v) -> str:
    if v is None:
        return "–"
    return f"{v:,.0f}".replace(",", " ")


def _cls(v) -> str:
    return "neg" if (v or 0) < 0 else "pos"


def _step_box(i: int, s: Step, unit_value: float | None) -> str:
    shared = f'<span class="tag">ligne partagée ×{s.shared} · {s.share:.0%} ici</span>' if s.shared > 1 else ""
    share = ""
    if s.price_share is not None:
        per = f" ≈ {_m(unit_value * s.price_share)}/unité" if unit_value else ""
        share = f'<div class="kv"><span>Part du prix</span><b>{s.price_share:.0%}{per}</b></div>'
    return f"""<div class="step">
  <div class="num">Segment {i}</div>
  <div class="name">{html.escape(s.name)}</div>
  <div class="stops">{html.escape(' → '.join(s.stops))}</div>
  {shared}
  <div class="big {_cls(s.allocated)}">{_m(s.allocated)}</div>
  <div class="cap">résultat du segment sur 12 mois</div>
  {share}
  <div class="kv"><span>En attente</span><b class="pos">{_m(s.done)}</b></div>
  <div class="kv"><span>En cours</span><b>{_m(s.in_progress)}</b></div>
  <div class="kv"><span>Véhicules · à bord/attente</span><b>{s.vehicles} · {s.on_board:.0f}/{s.waiting_after:.0f}</b></div>
</div>"""


def _chain_row(c: Chain, depth: int) -> str:
    boxes = "<div class='arrow'>→</div>".join(_step_box(i, st, c.unit_value) for i, st in enumerate(c.steps, 1))
    status = (f"{c.deliveries} unités livrées · ≈ {_m(c.unit_value)} par unité"
              if c.delivered else "<b class='neg'>pas encore livrée</b> depuis l'installation")
    label = (f'<div class="label">{"└ " if depth else ""}<b>{html.escape(c.cargo)}</b> → {html.escape(c.destination)}'
             + (f' <span class="muted">(produit par {html.escape(c.source)})</span>' if c.source else "") + "</div>")
    who = "Client final" if depth == 0 else "Livré à"
    return f"""<div class="chain" style="margin-left:{depth * 28}px">{label}
  <div class="flow">{boxes}<div class='arrow'>→</div>
    <div class="step client"><div class="num">{who}</div><div class="name">{html.escape(c.destination)}</div>
      <div class="cap">{status}</div>
      <div class="big {_cls(c.allocated)}">{_m(c.allocated)}</div><div class="cap">résultat de la chaîne (12 mois)</div>
      <div class="kv"><span>+ en attente + en cours</span><b class="{_cls(c.potential)}">{_m(c.potential)}</b></div>
    </div>
  </div>
</div>"""


def _tree_rows(t: "Tree", depth: int = 0) -> str:
    return _chain_row(t.chain, depth) + "".join(_tree_rows(i, depth + 1) for i in t.inputs)


def render(trees: list["Tree"], meta: dict, title="Chaînes de transport") -> str:
    d = meta.get("date") or {}
    chains = [c for t in trees for c in t.all_chains()]
    sections = []
    for t in trees:
        root = t.chain
        n = len(t.all_chains())
        sections.append(f"""<section class="card">
  <h2>{html.escape(root.cargo)} <span class="sub">→ {html.escape(root.destination)}
    {f"· arbre de {n} chaînes" if n > 1 else ""}</span>
    <span class="right">résultat <b class="{_cls(t.allocated)}">{_m(t.allocated)}</b>
    · avec l'attente <b class="{_cls(t.potential)}">{_m(t.potential)}</b></span></h2>
  {_tree_rows(t)}
</section>""")
    total_pending = sum(c.pending for c in chains)
    total_progress = sum(c.in_progress for c in chains)
    total_result = sum(c.allocated for c in chains)
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root {{ --bg:#f6f5f2; --card:#fff; --ink:#1d1d1b; --muted:#6b6a66; --line:#e3e1dc; --pos:#1f7a4d; --neg:#b3261e; --tag:#eef2f7; --step:#fbfaf8; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#16171a; --card:#202226; --ink:#ecebe8; --muted:#a3a29d; --line:#33353a; --pos:#5cc493; --neg:#ff8a80; --tag:#2b3340; --step:#25282d; }} }}
body {{ margin:0; padding:24px 16px; background:var(--bg); color:var(--ink); font:14px/1.45 system-ui, sans-serif; }}
main {{ max-width:1400px; margin:auto; }}
h1 {{ font-size:22px; margin:0 0 4px; }} .lead {{ color:var(--muted); margin:0 0 20px; max-width:900px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; margin:0 0 16px; }}
h2 {{ font-size:17px; margin:0 0 12px; display:flex; gap:8px; align-items:baseline; flex-wrap:wrap; }}
h2 .sub {{ color:var(--muted); font-weight:normal; }} h2 .right {{ margin-left:auto; }}
.chain {{ overflow-x:auto; padding-bottom:6px; margin-bottom:10px; border-bottom:1px dashed var(--line); }}
.chain:last-child {{ border-bottom:none; margin-bottom:0; }}
.label {{ margin:0 0 6px; }} .muted {{ color:var(--muted); font-weight:normal; }}
.flow {{ display:flex; align-items:stretch; gap:6px; min-width:min-content; }}
.step {{ background:var(--step); border:1px solid var(--line); border-radius:8px; padding:10px; width:210px; flex:none; }}
.step.client {{ border-width:2px; }}
.arrow {{ align-self:center; color:var(--muted); font-size:20px; flex:none; }}
.num {{ font-size:11px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }}
.name {{ font-weight:600; margin:2px 0; }} .stops, .cap {{ color:var(--muted); font-size:12px; }}
.big {{ font-size:20px; font-weight:700; margin-top:8px; }}
.kv {{ display:flex; justify-content:space-between; gap:8px; font-size:12px; margin-top:4px; }} .kv span {{ color:var(--muted); }}
.pos {{ color:var(--pos); }} .neg {{ color:var(--neg); }}
.tag {{ display:inline-block; background:var(--tag); border-radius:4px; padding:1px 5px; font-size:11px; margin-top:4px; }}
.kpi {{ display:flex; gap:24px; flex-wrap:wrap; margin:0 0 20px; }} .kpi div b {{ display:block; font-size:20px; }}
</style></head><body><main>
<h1>{html.escape(title)}</h1>
<p class="lead">Simulation au {d.get('day','?')}/{d.get('month','?')}/{d.get('year','?')} · {meta.get('items') or 0} marchandises en route analysées
({meta.get('skipped') or 0} ignorées faute de position). Chaque arbre part du produit livré au client final ; en dessous, en retrait, les chaînes qui alimentent
l'industrie qui le fabrique (puis leurs propres intrants). Chaque chaîne se lit de gauche à droite.
<b>Résultat du segment</b> : résultat 12 mois de la ligne ; une ligne partagée est répartie entre ses chaînes selon la recette
que chacune lui apporte. <b>Part du prix</b> : part de la recette de la chaîne versée à ce segment (longueur à vol d'oiseau).
<b>En attente / en cours</b> : recettes déjà gagnées, versées à la livraison au client (estimation). Passagers exclus.</p>
<div class="kpi"><div>Résultat des chaînes<b class="{_cls(total_result)}">{_m(total_result)}</b></div>
<div>En attente<b class="pos">{_m(total_pending)}</b></div><div>En cours<b>{_m(total_progress)}</b></div>
<div>Chaînes<b>{len(chains)}</b></div></div>
{''.join(sections)}
</main></body></html>"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?", help="export.json or game log (default: latest found)")
    ap.add_argument("--html", default="out/chaines.html", help="output HTML file")
    ap.add_argument("--cargo", help="only chains whose cargo name contains this text")
    ap.add_argument("--deficit", action="store_true", help="only chains whose result is negative, worst first")
    ap.add_argument("--line", help="only chains with a line whose name contains this text")
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
    trees = build_trees(chains)
    if args.cargo:
        trees = [t for t in trees if any(args.cargo.lower() in c.cargo.lower() for c in t.all_chains())]
    if args.line:
        trees = [t for t in trees if any(args.line.lower() in s.name.lower() for c in t.all_chains() for s in c.steps)]
    title = "Chaînes de transport"
    if args.deficit:
        trees = [t for t in trees if t.allocated < 0 or any(c.allocated < 0 for c in t.all_chains())]
        title = "Chaînes déficitaires (et arbres qui en contiennent)"
    out = Path(args.html)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(trees, meta, title), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"{len(trees)} arbres -> {out.resolve()}")
    for t in trees[:10]:
        c = t.chain
        print(f"  {c.cargo:12} -> {c.destination} | {len(t.all_chains())} chaîne(s) | résultat {_m(t.allocated)} | avec attente {_m(t.potential)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
