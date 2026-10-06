# Procédure de test — Line Doctor

Les tests sont organisés du moins coûteux au plus réaliste. Chaque niveau doit passer avant le suivant.
Cocher au fur et à mesure ; noter toute anomalie dans une issue GitHub (avec l'extrait de `stdout.txt`).

Chemins utiles (adapter l'identifiant Steam) :

| Quoi | Où |
|---|---|
| Données utilisateur TF3 | `C:\Program Files (x86)\Steam\userdata\<steamId>\3493540\local\` |
| Journal du jeu | `…\local\crash_dump\stdout.txt` |
| Export du mod | `…\local\line_doctor\export.json` (+ `history.jsonl`) |

---

## Niveau 0 — Tests hors jeu (automatisés, ~1 s)

Exécutent le **vrai code Lua du mod** contre une API de jeu simulée (`tests/mock_api.lua`), puis l'analyseur.

```bash
pip install lupa
python -m unittest discover -s tests -v
```

Attendu : 6 tests OK. Ils vérifient l'encodeur JSON, la collecte (y compris la capture d'une erreur d'API sans plantage),
l'export « au chargement puis mensuel », le repli vers le journal quand `io` est indisponible, et les diagnostics
attendus (`OVERCAPACITY`, `UNDERCAPACITY`, `IDLE_VEHICLES`…).

## Niveau 1 — Le jeu charge le mod (~5 min)

1. `powershell -ExecutionPolicy Bypass -File scripts\install.ps1`
2. Lancer TF3 → *Mods* : **Line Doctor** apparaît dans la liste.
   - S'il n'apparaît pas : réinstaller avec `-Target staging` et recommencer ; noter quel dossier fonctionne.
3. Démarrer une **nouvelle partie** (petite carte) avec le mod activé.
4. Laisser tourner le jeu **non en pause** ~10 s, puis chercher dans `stdout.txt` :
   ```bash
   grep -n "LineDoctor\|line_doctor" "<…>/crash_dump/stdout.txt"
   ```

| Résultat dans le journal | Signification | Suite |
|---|---|---|
| `[LineDoctor] exported 0 lines … to …export.json` | Tout fonctionne, export fichier OK | Niveau 2 |
| `file output unavailable, exported … to the game log` | `io` bloqué par le jeu : repli journal | Niveau 2 (l'analyseur lit `stdout.txt`) |
| `collect failed: …` | Erreur dans la collecte | Ouvrir une issue avec le message |
| Erreur Lua mentionnant `line_doctor` / rien du tout | Script non chargé | Vérifier `_content.json`, le nom du dossier, la casse |

## Niveau 2 — Données justes sur un cas contrôlé (~20 min)

But : vérifier que chaque chiffre exporté correspond à ce que le jeu affiche.

1. Dans la partie de test, créer :
   - **Ligne A** (bus) : 2 arrêts dans une ville, **4 bus** → doit devenir sur-capacitaire.
   - **Ligne B** (fret) : mine → usine, **1 camion** → doit accumuler de la cargaison en attente.
   - **Ligne C** : 1 arrêt seulement ou sans véhicule → doit remonter un problème.
   - Arrêter manuellement un bus de la ligne A.
2. Faire tourner ~2 mois en vitesse rapide, puis 10 s en vitesse normale.
3. `python analyzer/analyze.py` et comparer avec la fenêtre de chaque ligne dans le jeu :

| Contrôle | Où dans le jeu | Tolérance |
|---|---|---|
| Nom des lignes et des arrêts | Gestionnaire de lignes | exact |
| Nombre de véhicules | Fenêtre ligne | exact |
| Intervalle (« Fréquence ») | Fenêtre ligne | ± 1 s |
| Débit (« Rate ») | Fenêtre ligne | exact |
| Bilan annuel (« Balance ») | Fenêtre ligne | ≈ `Résultat 12m` (décalage possible de quelques jours) |
| Personnes/cargaison en attente | Fenêtre de la gare, par ligne | ordre de grandeur ; **vérifie que l'indice d'arrêt est bien 0-based** |
| Véhicule arrêté | Liste des véhicules | `IDLE_VEHICLES` présent |
| Ligne C | Icône d'avertissement de la ligne | `GAME_REPORTED_ISSUES` ou `NO_VEHICLES` |

4. Vérifier `exportErrors` dans `export.json` : idéalement vide. Chaque entrée indique une fonction d'API qui
   n'existe pas / a changé → à corriger dans `collector.lua`.

## Niveau 3 — Diagnostics sur une vraie partie (~30 min)

1. Charger une partie avancée (ex. « TF3 A ») avec le mod ajouté. Le jeu peut avertir que le mod est ajouté à une
   sauvegarde existante : sans risque, le mod ne fait que lire (aucune commande, aucune écriture monétaire).
   **Sauvegarder d'abord une copie** par précaution.
2. Laisser tourner 10 s, puis dans Claude Code : « analyse mes lignes TF3 » (skill `analyze-lines`).
3. Pour 3 lignes déficitaires, juger chaque diagnostic :
   - ✅ pertinent / ⚠️ discutable / ❌ faux → noter pourquoi (sert à ajuster les seuils en tête de `analyze.py`).
4. Appliquer une suggestion sur une ligne, laisser tourner 6–12 mois, ré-analyser : le résultat de la ligne
   doit s'améliorer (comparer avec `history.jsonl`).

## Niveau 4 — Non-régression et performance

- **Performance** : sur la plus grosse partie, aucune saccade visible au changement de mois (moment de l'export).
  Sinon noter le nombre de lignes / véhicules (`summary`).
- **Sauvegarde/chargement** : sauvegarder, recharger : un nouvel export « load » est produit, pas d'erreur.
- **Retrait du mod** d'une sauvegarde : la partie se charge sans le mod.
- **Succès Steam** : le mod est `cosmetic: true` ; vérifier que le jeu n'indique pas que les succès sont désactivés.

## Critères d'acceptation v0.1

- [ ] Niveau 0 vert
- [ ] Mod visible et chargé, export produit (fichier ou journal)
- [ ] Chiffres du niveau 2 conformes au jeu
- [ ] `exportErrors` vide ou documenté
- [ ] Au moins 2 diagnostics jugés pertinents sur 3 lignes réelles
- [ ] Pas d'impact perceptible sur les performances
