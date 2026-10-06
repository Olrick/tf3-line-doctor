# Line Doctor — diagnostic des lignes pour Transport Fever 3

Un mod TF3 qui exporte l'état détaillé de toutes tes lignes, et un analyseur que **Claude** utilise pour expliquer
pourquoi certaines lignes perdent de l'argent et quoi faire pour y remédier.

```
 Transport Fever 3                         Claude Code (ce dépôt)
┌──────────────────────────┐   export   ┌────────────────────────────────────┐
│ mod olrick_line_doctor_1 │──────────▶ │ analyzer/analyze.py  → métriques   │
│ game script (lecture     │ export.json│                      → diagnostics │
│ seule, 1×/mois + au      │  ou        │ skill analyze-lines  → explication │
│ chargement)              │ stdout.txt │                        + remèdes   │
└──────────────────────────┘            └────────────────────────────────────┘
```

Le jeu n'a pas d'accès réseau depuis Lua : le mod écrit un fichier, Claude le lit. Le mod est **en lecture seule**
(aucune commande envoyée, aucune écriture comptable) et marqué `cosmetic` pour ne pas bloquer les succès.

## Données exportées (par ligne)

- arrêts (gare, quai, mode de chargement, temps d'attente min/max)
- finances 12 derniers mois et 12 mois précédents : résultat, recettes, coûts de fonctionnement et d'entretien, série mensuelle
- débit théorique, transportés/an, intervalle, capacité utilisée, personnes/cargaison en attente par arrêt
- temps de section réels vs théoriques (roulage et arrêts)
- véhicules : état, arrêté/au dépôt/sans chemin, capacité, chargement, coût annuel, âge et état d'entretien par élément
- problèmes signalés par le jeu (arrêts inutiles, rien à charger/décharger, pas de chemin…)

## Diagnostics produits

`NO_VEHICLES`, `GAME_REPORTED_ISSUES`, `NO_PATH`, `IDLE_VEHICLES`, `OVERCAPACITY`, `UNDERCAPACITY`, `LONG_DWELL`,
`CONGESTION`, `OLD_FLEET`, `POOR_MAINTENANCE`, `LOW_FREQUENCY`, `SHORT_HOPS`, `EMPTY_RETURN`, `COST_STRUCTURE`,
`DECLINING`, `YOUNG_LINE`, `SHARED_STATIONS`, `QUEUE` (file d'attente, y compris récurrente sur les
~24 derniers exports), `ASYMMETRIC_ROUTE`, `TRANSFER_DEPENDENT`, `SLOW_CYCLE`, `NO_COMPLETED_TRIP`. Les seuils sont en tête de [analyzer/analyze.py](analyzer/analyze.py).

## Installation

```bash
powershell -ExecutionPolicy Bypass -File scripts/install.ps1
```

Activer **Line Doctor** dans la liste des mods de la partie. Option `-Link` pour développer (jonction vers le dépôt).

## Utilisation

1. Jouer : le mod exporte au chargement puis chaque mois de jeu (le jeu ne doit pas être en pause).
2. Dans Claude Code, depuis ce dossier : *« analyse mes lignes TF3 »* (skill `analyze-lines`), ou directement :

```bash
python analyzer/analyze.py --deficit-only
```

## Structure

| Chemin | Rôle |
|---|---|
| `mod/olrick_line_doctor_1/` | le mod (à copier dans les mods TF3) |
| `…/content/line_doctor/collector.lua` | collecte des données (toutes les API protégées par `pcall`) |
| `…/content/line_doctor/line_doctor.script.lua` | game script : déclenchement et écriture de l'export |
| `analyzer/analyze.py` | métriques, diagnostics, rapport Markdown/JSON |
| `analyzer/infra.py` | entretien de l'infrastructure bâtiment par bâtiment (gares sans ligne, plus coûteuses), page HTML |
| `analyzer/history.py` | historique de la compagnie année par année (journal du jeu), page HTML |
| `analyzer/archive.py` | archive les exports du journal du jeu dans `exports/<partie>/` (lancé automatiquement) |
| `analyzer/chains.py` | chaînes de marchandises de bout en bout (profit en attente par étape), page HTML |
| `.claude/skills/analyze-lines/` | instructions pour Claude |
| `tests/` | tests hors jeu (Lua réel + API simulée) |
| `docs/TESTING.md` | **procédure de test complète** |

## Tests

```bash
pip install lupa
python -m unittest discover -s tests -v
```

Voir [docs/TESTING.md](docs/TESTING.md) pour la procédure en jeu.
