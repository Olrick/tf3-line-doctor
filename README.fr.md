# Line Doctor — diagnostic des lignes pour Transport Fever 3

*[English](README.md) · Français*

Line Doctor est en **deux parties** :

1. **Un mod Transport Fever 3** (`olrick_line_doctor_1`), utilisable seul : une boussole et un voyant de
   **santé** de la compagnie dans la barre du bas, une carte « Line Doctor » dans la fenêtre de chaque ligne,
   et un export mensuel de l'état détaillé de la partie.
2. **Une interface avec Claude** (ce dépôt, ouvert dans Claude Code) : des analyseurs Python et des instructions
   qui permettent à Claude de lire l'export et d'expliquer pourquoi une ligne, une chaîne de production ou la
   compagnie perd de l'argent, et quoi faire.

```
 Transport Fever 3 (le mod)                       Claude Code (ce dépôt)
┌───────────────────────────────────┐          ┌──────────────────────────────────────┐
│ barre du bas : Cap · Santé ● ● ● ●│          │ analyzer/*.py   → métriques, rapports │
│ fenêtre de ligne : carte          │  export  │ skill analyze-lines → explications    │
│ game script (lecture seule) ──────┼────────▶ │                       et remèdes      │
│   export au chargement + 1×/mois  │stdout.txt│ exports/<partie>/ → historique        │
└───────────────────────────────────┘          └──────────────────────────────────────┘
```

Le mod est **en lecture seule** : il n'envoie aucune commande au jeu, ne modifie aucun prix et n'écrit rien
dans la comptabilité. Il est marqué `cosmetic` pour ne pas bloquer les succès.

---

## Partie 1 — Ce qui est intégré à TF3

### La boussole

Dans la barre du bas, à gauche des gains : **`Cap NE 45°`**.

- Elle indique la direction vers laquelle regarde la caméra : N, NE, E, SE, S, SW, W, NW et le cap en degrés.
- TF3 n'a pas de nord officiel : ici, **le nord est l'axe +y de la carte**, le cap tourne dans le sens des
  aiguilles d'une montre. C'est la même convention que les rapports de Claude (« la gare est à 2 km au
  cap 330° »), ce qui permet de retrouver en jeu un bâtiment qu'il signale.
- Calibrée en jeu sur plusieurs repères.

### La santé de la compagnie

À côté de la boussole : **`Santé ● ● ● ●`**, un point par indicateur, en **vert**, **orange** ou **rouge**.
Survoler le mot affiche le détail (valeurs et seuils).

**Ce que mesure la santé.** Quatre ratios qui disent si la compagnie est structurellement rentable, au-delà
du résultat du mois :

| Point | Indicateur | Vert | Orange | Rouge | Ce qu'il surveille |
|---|---|---|---|---|---|
| 1 | Recettes ÷ fonctionnement des véhicules | ≥ 2,0 | 1,7 – 2,0 | < 1,7 | les véhicules rapportent-ils assez par rapport à ce qu'ils coûtent à faire rouler ? |
| 2 | Entretien des bâtiments ÷ recettes | ≤ 20 % | 20 – 28 % | > 28 % | trop de gares, dépôts, ports pour ce qu'ils rapportent |
| 3 | Entretien des véhicules ÷ recettes | ≤ 9 % | 9 – 11 % | > 11 % | flotte trop vieille ou mal entretenue |
| 4 | Résultat d'exploitation ÷ recettes | ≥ 10 % | 0 – 10 % | < 0 % | la compagnie gagne-t-elle de l'argent hors investissements ? |

Les seuils viennent d'une vraie partie, saine jusqu'en 1959 puis déficitaire : à l'entrée en déficit, les
ratios 1 à 3 étaient respectivement de 1,6, 34 % et 12 %.

**Calcul projeté au niveau max.** Avec l'inflation de TF3, le prix payé par passager ou par tonne baisse à
chaque promotion de la compagnie (jusqu'à ×0,75 au rang 15 en inflation Normale), alors que les coûts des
véhicules et des bâtiments ne baissent jamais. La santé est donc calculée **comme si la compagnie était
déjà au rang maximal** : recettes × (multiplicateur au rang 15 ÷ multiplicateur actuel), coûts inchangés.
Conséquences :

- un voyant vert aujourd'hui le restera après toutes les promotions ;
- une promotion ne change pas les voyants : seuls les recettes, les coûts et les bâtiments les font bouger ;
- sans inflation (option du jeu), la projection ne change rien.

Le calcul porte sur les **12 derniers mois terminés** du tableau des finances du jeu, **hors subventions**
(ponctuelles). Il est refait à chaque export mensuel ; avant le premier, le voyant affiche « Santé … ».

### La carte Line Doctor dans la fenêtre de ligne

Dans la fenêtre de chaque ligne, une carte « Line Doctor » indique le **profit en attente** : les recettes
déjà acquises par la ligne mais pas encore versées. Dans TF3, une marchandise n'est payée qu'à sa livraison au
client final, pour tous les segments du trajet : une ligne d'apport peut sembler déficitaire alors que ses
marchandises sont en route.

### L'export

À chaque chargement de partie puis chaque mois de jeu (le jeu ne doit pas être en pause), le mod écrit dans le
journal du jeu (`stdout.txt`) un instantané de la partie, entre des marqueurs `LINE_DOCTOR_BEGIN` /
`LINE_DOCTOR_END` : les scripts de jeu de TF3 n'ont pas le droit d'écrire de fichiers.

Contenu, par ligne : arrêts, finances sur 12 mois et série mensuelle, capacité et chargement, passagers ou
marchandises en attente par arrêt, temps de section réels et théoriques, véhicules (modèle, âge, état
d'entretien, coût annuel, recettes), problèmes signalés par le jeu. Pour la compagnie : trésorerie, emprunt,
rang et inflation, tableau des finances, entretien de chaque bâtiment, **stocks des entrepôts et des
usines** (quantités stockées, produites, livrées, expédiées par an, marchandises jetées), prix des billets
observés et profit en attente.

---

## Partie 2 — L'interface avec Claude

Ouvrir ce dépôt dans **Claude Code** et poser des questions en français, par exemple :

- « analyse mes lignes TF3 », « santé ? », « pourquoi ma trésorerie s'enfonce ? »
- « analyse la chaîne qui livre les vêtements à Vandœuvre », « montre l'infrastructure »
- « regarde l'entrepôt de Vitry », « est-ce que je peux rentabiliser un train rapide ? »

Claude suit les instructions de [.claude/skills/analyze-lines/SKILL.md](.claude/skills/analyze-lines/SKILL.md) :
il lit le dernier export, lance les analyseurs, recoupe les indices et répond avec les causes, les chiffres et
des actions concrètes à faire dans le jeu. Il ne joue pas à votre place : le mod ne peut rien modifier.

Les analyseurs peuvent aussi être lancés directement :

| Commande | Résultat |
|---|---|
| `python analyzer/analyze.py --deficit-only` | métriques et diagnostics par ligne (`OVERCAPACITY`, `QUEUE`, `TRANSFER_DEPENDENT`, `POOR_MAINTENANCE`…), tableau des finances |
| `python analyzer/health.py --html out/sante.html` | bilan de santé détaillé : ratios, rang et inflation, test de résistance au rang 15, lignes fragiles |
| `python analyzer/chains.py --html out/chaines.html` | chaînes de production de bout en bout, du client final aux matières premières, profit par segment |
| `python analyzer/infra.py --html out/infrastructure.html` | entretien bâtiment par bâtiment, gares sans ligne |
| `python analyzer/history.py --html out/historique.html` | historique de la compagnie année par année |

Chaque analyse archive les exports du journal dans `exports/<identifiant de partie>/`, car le journal du jeu
est effacé à chaque lancement : l'historique des parties est ainsi conservé.

---

## Installation

### Prérequis

- **Windows** et **Transport Fever 3** sur Steam.
- Pour la partie Claude : **[Claude Code](https://claude.com/claude-code)**, **Python 3.10+** et **git**.
- Pour les tests hors jeu (facultatif) : `pip install lupa`.

### 1. Récupérer le dépôt

```bash
git clone https://github.com/Olrick/tf3-line-doctor.git
```

```bash
cd tf3-line-doctor
```

### 2. Installer le mod dans TF3

```bash
powershell -ExecutionPolicy Bypass -File scripts/install.ps1
```

Le script trouve tout seul le dossier de données de TF3
(`C:\Program Files (x86)\Steam\userdata\<identifiant Steam>\3493540\local\`) et copie le mod dans son
sous-dossier `mods\`. Options :

- `-UserData "<chemin>\3493540\local"` si le dossier n'est pas trouvé ;
- `-Link` pour développer : crée un lien vers le dépôt au lieu d'une copie.

### 3. Activer le mod

1. Lancer TF3.
2. Créer une partie ou charger une sauvegarde : dans la liste des mods, cocher **Line Doctor**.
3. Laisser tourner le jeu quelques secondes, **pas en pause** : la boussole s'affiche tout de suite, la santé
   après le premier export.

### 4. Utiliser l'interface Claude

Ouvrir le dossier du dépôt dans Claude Code et demander « analyse mes lignes TF3 ». Claude lit le journal du
jeu (`…\3493540\local\crash_dump\stdout.txt`) ; si aucun export n'est trouvé, vérifier que le mod est coché
dans la partie et que le jeu n'est pas en pause.

### Mettre à jour

```bash
git pull
```

```bash
powershell -ExecutionPolicy Bypass -File scripts/install.ps1
```

- Si seuls des fichiers existants ont changé : **recharger la partie** suffit.
- Si un fichier a été **ajouté** au mod : **quitter complètement TF3 et le relancer** (le jeu n'indexe les
  nouveaux fichiers qu'au démarrage).

---

## Structure du dépôt

| Chemin | Rôle |
|---|---|
| `mod/olrick_line_doctor_1/` | le mod TF3 |
| `…/line_doctor/collector.lua` | collecte des données et calcul de la santé (toutes les API protégées par `pcall`) |
| `…/line_doctor/line_doctor.script.lua` | game script : déclenchement et écriture de l'export |
| `…/line_doctor/line_doctor_gui.script.lua` | interface en jeu : boussole, santé, carte de ligne |
| `…/line_doctor/pending.lua`, `ticket_probe.lua` | profit en attente, observation des prix des billets |
| `analyzer/` | analyseurs Python (voir la partie 2) |
| `.claude/skills/analyze-lines/` | instructions pour Claude |
| `tests/` | tests hors jeu : le vrai code Lua du mod contre une API de jeu simulée |
| `docs/TESTING.md` | procédure de test en jeu |

## Tests

```bash
pip install lupa
```

```bash
python -m unittest discover -s tests -v
```

Voir [docs/TESTING.md](docs/TESTING.md) pour la procédure en jeu.
