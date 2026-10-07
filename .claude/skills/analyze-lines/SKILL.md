---
name: analyze-lines
description: Analyse les lignes Transport Fever 3 exportées par le mod Line Doctor, explique pourquoi certaines sont déficitaires et propose des remèdes concrets. À utiliser quand l'utilisateur demande d'analyser ses lignes, son réseau TF3 ou une ligne précise.
---

# Analyse des lignes TF3 (Line Doctor)

## 1. Obtenir les données

Lancer l'analyseur (il trouve tout seul l'export le plus récent) :

```bash
python analyzer/analyze.py --json out/diagnostics.json
```

- Si l'utilisateur fournit un fichier, le passer en argument (export.json, ligne de history.jsonl ou stdout.txt du jeu).
- Si aucun export n'est trouvé : rappeler que le jeu doit tourner (non en pause) avec le mod actif au moins quelques secondes, et vérifier `stdout.txt` (`grep LineDoctor`).
- Noter la date de l'export (en tête du rapport) et la signaler si elle semble ancienne.

Pour une chaîne de marchandises de bout en bout (étapes, profit en attente par étape, totaux) :

```bash
python analyzer/chains.py --html out/chaines.html            # toutes les chaînes
python analyzer/chains.py --html out/chaines.html --cargo Bois
```

puis ouvrir `out/chaines.html` dans le navigateur intégré.

Pour l'économie de la compagnie : le rapport `analyze.py` affiche le tableau des finances du jeu (recettes,
fonctionnement, entretien des véhicules et de l'infrastructure par mode, achats, constructions). Pour l'entretien
bâtiment par bâtiment (gares sans ligne, bâtiments coûteux) :

```bash
python analyzer/infra.py --html out/infrastructure.html
```

Bilan de santé (ratios avec seuils, rang et inflation, test de résistance au rang 15, lignes fragiles) :

```bash
python analyzer/health.py --html out/sante.html
```

Seuils (tirés de « TF3 A », sain jusqu'en 1959) : recettes ÷ fonctionnement des véhicules ≥ 2,0 ; entretien des
bâtiments ≤ 20 % des recettes ; entretien des véhicules ≤ 9 %. Avec l'inflation, une ligne doit rester rentable
au rang 15 (recettes × multiplicateur au rang 15 ÷ multiplicateur actuel).

Historique de la compagnie année par année (journal du jeu, depuis le début de la partie) :

```bash
python analyzer/history.py --html out/historique.html     # pivot = pic de trésorerie, ou --pivot 1960
```

Les exports sont archivés automatiquement par partie dans `exports/<gameId>/` à chaque analyse
(le journal du jeu est effacé à chaque lancement) ; `python analyzer/archive.py` le fait à la main.

Pour une ligne précise, lire aussi ses données brutes dans l'export (`lines[]` où `name` correspond) :
temps de section réels vs théoriques, attente par arrêt, capacités et âge de chaque véhicule, modes de chargement.

## 2. Raisonner

**Échelle de temps (vérifiée en jeu) :** une année de calendrier = 1 461 s de jeu, donc **1 mois ≈ 122 s**.
Les temps de section (`sectionTimesSec`, `realSectionTimes`) sont en secondes de jeu : une section de 550 s
dure ~4,5 mois de calendrier. Sur une ligne lente, les recettes arrivent par à-coups à chaque fin de trajet et
le résultat d'un mois isolé ne veut rien dire : raisonner en allers-retours (colonne « Aller-retour »).
**Règle de paiement TF3 :** la rémunération de **tous** les segments d'un trajet (passagers ou marchandises) n'est
versée qu'à la livraison au destinataire final (usine, consommateur, destination du passager). Conséquences :
- une ligne d'apport (correspondance) peut paraître déficitaire alors que le groupe de lignes est rentable : regarder
  `TRANSFER_DEPENDENT` et le résultat cumulé du groupe avant de conseiller de réduire ou supprimer une ligne ;
- ses recettes arrivent avec le retard du trajet le plus lent de la chaîne ;
- une chaîne interrompue (marchandise jamais livrée au client final) ne rapporte rien à aucun segment ;
- `pendingIncome` = recettes à bord pas encore versées.

**État d'entretien (vérifié en jeu) :** `maintenanceState` = état du véhicule affiché en jeu (0–100 %). Sans station
de maintenance il descend jusqu'à 0 ; un mauvais état augmente les coûts de fonctionnement et réduit vitesse et confort.

**Files d'attente :** `QUEUE` s'appuie sur l'historique des exports du journal (récurrence sur ~2 ans de jeu). Un
voyage aller 3× plus long que le retour (`ASYMMETRIC_ROUTE`) avec une file = attente au déchargement, pas un détour.

`lastLineStopDeparture` peut rester à 0 même quand la ligne fonctionne : ne pas s'en servir pour conclure à un blocage.

Les diagnostics de l'analyseur sont des **indices heuristiques**, pas des verdicts. Pour chaque ligne déficitaire :

1. Recouper les indicateurs : une ligne peut être en sur-capacité (utilisation faible) *et* mal placée (peu de demande).
2. Distinguer la cause racine des symptômes. Ordre habituel de vérification :
   problème de configuration signalé par le jeu → véhicules immobilisés/sans chemin → dimensionnement de la flotte
   (utilisation, attente) → temps perdus (arrêts, chargement complet, congestion) → structure de coût (matériel trop cher,
   trajets trop courts, retours à vide) → concurrence entre lignes.
3. Tenir compte du contexte : ligne récente (`YOUNG_LINE`), ligne d'apport (une ligne déficitaire qui alimente une ligne
   très rentable peut valoir la peine), année/époque du jeu.
4. Quantifier quand c'est possible (nombre de véhicules cible, économie annuelle estimée = coûts par véhicule × véhicules retirés).

## 3. Répondre (en français)

- Résumé en 3–5 lignes : nombre de lignes, déficitaires, perte totale, priorité n°1.
- Puis, ligne par ligne, des plus déficitaires aux moins : **Cause probable**, **Indices** (chiffres), **Actions** (concrètes,
  dans l'ordre, faisables dans le jeu), **Gain attendu**.
- Signaler les incertitudes (données manquantes, `exportErrors`, échantillons de temps réels absents).
- Ne pas inventer de mécaniques du jeu : si un point dépend d'une règle inconnue, le dire.
