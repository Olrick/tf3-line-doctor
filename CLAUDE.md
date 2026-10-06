# Line Doctor (mod Transport Fever 3)

- Mod TF3 en Lua (lecture seule) : `mod/olrick_line_doctor_1/`. Analyseur Python : `analyzer/analyze.py`.
- Pour analyser les lignes du joueur : suivre `.claude/skills/analyze-lines/SKILL.md`. Répondre en français.
- Référence API du jeu (définitions Teal) : `<TF3>/api/tealdef/api/` (engine.d.tl, engine/system.d.tl,
  engine/util.d.tl, type.d.tl). TF3 installé dans `D:\SteamLibrary\steamapps\common\Transport Fever 3`.
- Toute nouvelle API utilisée dans `collector.lua` passe par `try(...)` et doit être ajoutée à `tests/mock_api.lua`.
- Toute modification de fichier du mod : mettre à jour `_content.json` si un fichier est ajouté/renommé.
- Tests : `python -m unittest discover -s tests -v` (nécessite `pip install lupa`).
