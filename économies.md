# Plan d'économies — « TF3 A »

**Objectif : trouver ≈ 13 M/an** (déficit de la dernière période complète : −12,9 M).
Données : exports Line Doctor du 6/10/2026 (simulation figée au 21/1/1983). Montants par année de simulation.

## Pourquoi on perd de l'argent (rappel)

| | Montant/an |
|---|---|
| Recettes de transport | +110,7 M |
| Véhicules (fonctionnement + entretien) | −83,1 M |
| **Marge des véhicules** | **+27,7 M** |
| **Entretien des bâtiments** (gares, dépôts, ports) | **−37,0 M** |
| Routes et voies | −3,4 M |
| **Résultat** | **−12,9 M** |

- **Route : −8,3 M** (36 gares routières = 15 M d'entretien). **Eau : −2,7 M**. **Rail : +3,7 M** (seul mode rentable).
- **Inflation** (réglage « Normale », rang ≈ 12/15) : les recettes sont réduites de **≈ 20 %** (≈ 27 M/an). Le rang dépend de la
  population de la carte et ne redescend jamais ; plafond −25 % au rang 15.

---

## Étape 0 — Avant de commencer (5 min)

- [ ] **Sauvegarder une copie** de « TF3 A » (ex. « TF3 A avant économies »).
- [ ] Recharger la partie, laisser tourner ~2 min, demander « montre l'historique » : vérifier ce qui a dérapé après 1960
      (confirme ou non le rôle du rang / de l'inflation).
- [ ] *(Option, à décider)* **Inflation** : dans l'écran de chargement → paramètres avancés de la sauvegarde → Économie →
      Inflation. « Aucune » ≈ **+27 M/an**, « Faible » ≈ **+16 M/an**. Vérifier avant si les succès Steam comptent pour toi.
      *Si on le fait, le reste du plan devient un bonus — mais il reste utile : le réseau est trop coûteux.*

## Étape 1 — Supprimer les lignes déficitaires sans dépendance (≈ +3,3 M) — confiance bonne

Aucune n'alimente une chaîne ou un groupe rentable (vérifié par l'analyse des correspondances et des arbres de production).

- [ ] Tissu Vandoeuvre (−727 k, 3 véh) — ⚠️ **vérifier d'abord** qu'elle ne livre pas l'usine textile ; sinon garder 1 camion au lieu de la supprimer
- [ ] Forage 3 St Leu (−308 k, 1 véh)
- [ ] Légumes Villeurbane (−285 k, 9 véh)
- [ ] Outil Port (−261 k, 3 véh) — ⚠️ maillon de la chaîne Outils (Plastique Outils → Outils Vandeuvre → Outil Port → …) : vérifier qu'un autre trajet livre les outils, sinon réduire à 2 véhicules
- [ ] Engrais 2 Vannes Train (−255 k, 1 véh)
- [ ] Outils Ouvriers Argile (−200 k, 2 véh)
- [ ] Blé St Leu Brasserie (−167 k, 4 véh) — ⚠️ alimente la brasserie : vérifier la production de bière avant
- [ ] Verre St Leu Brasserie (−138 k, 2 véh) — ⚠️ idem
- [ ] Plastique Fontenay (−98 k, 1 véh)
- [ ] Biere Vannes distrib (−96 k, 1 véh)
- [ ] Légumes St Leu (−82 k, 3 véh)
- [ ] Ouvriers Ploufragan Peche (−78 k, 2 véh)
- [ ] Ouvriers Bras (−66 k, 2 véh)

## Étape 2 — Fermer la filière poisson de Dombasle d'un bloc (≈ +1,5 à 1,8 M) — confiance bonne

Lignes : Poissons Dombasle (−288 k), Bus Dombasle (−264 k), Peche Dombasle (+47 k, mais elle n'est payée que si Poissons Dombasle livre).

- [ ] Supprimer **Peche Dombasle**, **Poissons Dombasle**, **Bus Dombasle**
- [ ] Démolir s'ils n'ont plus de ligne : **Port de Dombasle** (568 k), **Port de Dombasle-sur-Meurthe** (272 k),
      **Gare de Dombasle-sur-Meurthe** (168 k)
- [ ] Puis, si plus rien ne s'en sert : Dépôt naval de Dombasle-sur-Meurthe (≈ 400 k), Dépôt routier de Dombasle-sur-Meurthe (120 k),
      Bâtiment d'entretien de Dombasle-sur-Meurthe (40 k)
- [ ] ⚠️ Vérifier avant qu'**Engrais Dombasle** et les lignes d'ouvriers de Dombasle n'utilisent pas ces gares

## Étape 3 — Démolir les bâtiments sans ligne (≈ +0,5 M)

- [ ] **Gare de Soyaux Inférieur** (terminal routier modulaire, 168 k, aucune ligne depuis le début des exports) — la chercher
      dans Statistiques → Gares, ou à côté des autres gares de Soyaux
- [ ] **Gare de Villeurbanne** (328 k) — ne sert qu'à Légumes Villeurbane (étape 1)

## Étape 4 — Retirer les véhicules en trop (≈ +2,3 à 3,2 M) — confiance moyenne

Lignes utilisées à moins de 50 % de leur capacité ; cible = assez de véhicules pour ~75 % d'utilisation.
Le gain est le coût de fonctionnement des véhicules retirés ; un peu de recette peut partir avec (fréquence plus faible).

| Ligne | Véhicules | Utilisation | Résultat 12 mois | Gain/an |
|---|---|---|---|---|
| Poissons Ploufragan | 8 → 4 | 31 % | −82 784 | 582 532 |
| Forage St Leu | 3 → 1 | 18 % | −308 365 | 292 608 |
| Engrais Dombasle | 9 → 4 | 32 % | −33 045 | 255 965 |
| Ouvriers Conserverie | 6 → 2 | 21 % | −200 104 | 236 260 |
| Légumes Miramas | 6 → 4 | 38 % | −149 163 | 199 592 |
| Bus Aeroport | 3 → 1 | 1 % | −243 678 | 174 129 |
| Poissons Soyaux | 3 → 2 | 49 % | +469 931 | 162 429 |
| Argile 3 Bras | 3 → 2 | 45 % | −155 550 | 162 429 |
| Argile 2 Bras | 4 → 3 | 49 % | +113 902 | 162 429 |
| Ouvriers Soyaux Raffinerie | 5 → 3 | 33 % | −213 631 | 118 130 |
| Ouvriers Mantes Raffinerie | 3 → 1 | 2 % | −162 934 | 118 130 |
| Ouvriers Vetements | 2 → 1 | 11 % | −158 419 | 105 348 |
| Poissons Commercy | 2 → 1 | 25 % | −96 649 | 99 796 |
| Ouvriers St Leu | 2 → 1 | 11 % | −93 912 | 84 256 |
| Ouvriers 2 Bras | 3 → 2 | 32 % | +15 424 | 59 065 |
| Ouvrier Peche | 2 → 1 | 15 % | −69 738 | 59 065 |
| Engrais 1 Vannes | 3 → 2 | 49 % | −70 527 | 51 885 |
| Ouvriers Ferme | 2 → 1 | 21 % | −50 026 | 47 289 |
| Ouvriers Mantes Charbon | 3 → 2 | 29 % | −33 595 | 47 164 |
| Ligne 4 | 2 → 1 | 20 % | −28 886 | 47 164 |
| Ouvriers 2 St Lo | 2 → 1 | 20 % | −22 471 | 45 037 |
| Ouvriers Fontenay Ferme | 2 → 1 | 11 % | −22 093 | 18 818 |

*(Peche Dombasle exclue : traitée à l'étape 2.)*

## Étape 5 — Fermer les dépôts en doublon (≈ +2,6 M) — à vérifier en jeu

29 dépôts (6,3 M/an) + 28 bâtiments d'entretien (2,1 M/an). Garder **un dépôt par mode et par zone** ; ne pas toucher aux
bâtiments d'entretien sans vérifier qu'aucune ligne n'en perd un (les véhicules non entretenus se dégradent — cf. Bus Ballan).

- [ ] **Routiers (19 × 120 k)** — doublons évidents : Loos / Loos Sud / Loos n°1 ; Mantes-la-Ville / Mantes-la-Ville Sud /
      Mantes-la-Ville n°1 ; Commercy / Commercy Est ; Vannes / Vannes Sud. Objectif ≈ −8 dépôts (**≈ +1,0 M**)
- [ ] **Ferroviaires (5 × 400 k)** — Mantes-la-Ville et Mantes-la-Ville Sud : en garder un. Objectif −1 à −2 (**≈ +0,4 à 0,8 M**)
- [ ] **Navals (5 × 400 k)** — Bras-Panon, Commercy, Dombasle-sur-Meurthe (étape 2), Langres, Loos : objectif −2 (**≈ +0,8 M**)
- [ ] ⚠️ Avant chaque démolition : chaque ligne doit garder un dépôt accessible du bon type (pour racheter des véhicules)

## Étape 6 — Restructurer plutôt que supprimer (≈ +0,5 à 1,5 M)

Déficitaires mais elles **alimentent des chaînes ou des groupes rentables** — réduire les coûts, ne pas couper :
Engrais Vannes (−384 k), Forage St Leu (−308 k), Bus Aeroport (−244 k), Plastique Loos (−227 k), Ouvriers Soyaux Raffinerie (−214 k),
Ouvriers Conserverie (−200 k), Bus Aéroport (−174 k), Ouvriers Mantes Raffinerie (−163 k), Ouvriers Vetements (−158 k),
Argile 3 Bras (−156 k), Légumes Miramas (−149 k), Peche Vandoeuvre (−118 k), Outils Charbon (−109 k), Outils Vandeuvre (−103 k).

- [ ] Pour chacune : moins de véhicules (étape 4), véhicules moins chers, ou trajet plus direct (le prix ne dépend que de la
      distance à vol d'oiseau : chaque transbordement coûte sans rapporter)

---

## Bilan attendu

| Étape | Gain/an |
|---|---|
| 1. Lignes déficitaires sans dépendance | ≈ +3,3 M |
| 2. Filière poisson de Dombasle | ≈ +1,5 à 1,8 M |
| 3. Bâtiments sans ligne | ≈ +0,5 M |
| 4. Véhicules en trop | ≈ +2,3 à 3,2 M |
| 5. Dépôts en doublon | ≈ +2,2 à 2,6 M |
| 6. Restructurations | ≈ +0,5 à 1,5 M |
| **Total** | **≈ +10 à 13 M** |
| *(option) Inflation « Aucune »* | *≈ +27 M* |

Déjà acquis depuis la période de référence : train de colorants (−854 k → −30 k), aérien supprimé.

## Après les changements

- [ ] Laisser tourner **2 à 3 années de simulation** (les chaînes lentes paient par à-coups)
- [ ] Demander : « analyse mes finances », « montre l'infrastructure », « montre les chaînes déficitaires »
- [ ] Comparer le tableau des finances avant / après (les exports sont archivés dans `exports/`)

## Restent à éclaircir

- **Écart de ≈ 14 M** entre la somme des lignes (+42 M) et la marge véhicules du jeu (27,7 M) : véhicules sans ligne ?
  → ajouter à l'export les véhicules non affectés.
- **Poste d'infrastructure « non identifié » de 5,3 M/an** : non rattaché à un type de bâtiment.
- Nombre de véhicules rattachés à chaque bâtiment d'entretien (pour savoir lesquels fermer).
