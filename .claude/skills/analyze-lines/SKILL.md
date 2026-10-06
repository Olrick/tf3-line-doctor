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

Pour une ligne précise, lire aussi ses données brutes dans l'export (`lines[]` où `name` correspond) :
temps de section réels vs théoriques, attente par arrêt, capacités et âge de chaque véhicule, modes de chargement.

## 2. Raisonner

**Échelle de temps (vérifiée en jeu) :** une année de calendrier = 1 461 s de jeu, donc **1 mois ≈ 122 s**.
Les temps de section (`sectionTimesSec`, `realSectionTimes`) sont en secondes de jeu : une section de 550 s
dure ~4,5 mois de calendrier. Sur une ligne lente, les recettes arrivent par à-coups à chaque fin de trajet et
le résultat d'un mois isolé ne veut rien dire : raisonner en allers-retours (colonne « Aller-retour »).
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
