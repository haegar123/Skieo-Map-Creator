# Fortschritt

## Stand

Map Creator 1.8.2: echtes Gelände (USGS 3DEP in den USA, AWS Terrain Tiles sonst), Wald nach Spiel-Biomen mit
Baumbudget, echte Pisten und Lifte aus OpenStreetMap, Installation ins Spiel, Mod Library. Neu (noch nicht
veröffentlicht): in Europa optional Sonnys LiDAR-Höhenmodell „DTM Europe 1" v25" aus einem lokalen Ordner.

## Erledigt

- Lokale Höhendaten von Sonny (CC BY 4.0) haben Vorrang, AWS Terrain Tiles füllen fehlende Kacheln und NoData-Löcher
  mit weichem Übergang (`skieo_creator/dem.py`: `sonny_index`, `_fetch_sonny`, `load(folder=…)`)
- Kacheln werden direkt aus den Zips gelesen: lose `.hgt`, Kachel-Zip, Sammel-Zip mit Kachel-Zips; kaputte Zips und
  gleichnamige 3"-Kacheln werden übersprungen (Größe aus dem lokalen Zip-Header)
- Datenordner einstellbar: Fußzeile „Local elevation data…“ / ×, Einstellung `dem_folder`
  (`skieo_creator/api.py`: `pick_dem_folder`, `clear_dem_folder`; `skieo_creator/paths.py`; `web/app.js`, `web/index.html`)
- Quellenangabe: Terrain-Chip, Fußzeile, `map.json` und `.skimap`-Manifest (`elevation`), README, DEVELOPMENT,
  `packaging/THIRD-PARTY.txt`
- Tests auf erfundenen Kacheln: Ablageformen, 3"-Ablehnung, schräge Ebene über vier Kacheln, Loch + fehlende
  Kachel, Cache nach neuer Kachel, ohne Ordner wie bisher (`tests/test_dem.py`)
- Mit echten Daten geprüft (578 Kacheln, Index 0,1 s): St. Anton / 47° N-Grenze / Lücke bei 14° O,
  Abweichung zu AWS im Median < 1 m, keine Naht
- Manueller Test in der App nach Testanleitung (Ordner wählen, Laden, Vergleich, Kachelgrenze, Lücke, Karte bauen)
  vom Nutzer als erfolgreich bestätigt (2026-11-16)

## Offen

- `SONNY_SMOOTH_M` (8 m) an echten Karten im Spiel feinjustieren
- Version, CHANGELOG und Release, wenn veröffentlicht werden soll
- `tests/test_modlib.py` schlägt gelegentlich fehl (auch ohne diese Änderung): Ursache suchen

## Entscheidungen

- _Bei Bedarf aus den Zips lesen statt entpacken_ — spart ~15 GB; das fertige Quadrat liegt ohnehin im `.npy`-Cache,
  Auspacken kostet nur beim ersten Laden ~1 s pro Kachel
- _Cache-Schlüssel enthält die vorhandenen Kacheln_ — eine später hinzugelegte Kachel ersetzt die AWS-Füllung
- _Alte Karten behalten beim Rebuild ihre Quelle_ — sonst verrutschen Pisten und Lifte; Sonny-Karte ohne Ordner fällt
  auf AWS zurück (Warnung im Log) statt abzubrechen
- _Sonny vor 3DEP in der Reihenfolge_ — überschneiden sich nicht (Europa / USA), daher harmlos
- _Sonny ist nicht feiner als AWS (1" ≈ 31 × 21 m)_ — Gewinn ist LiDAR-Qualität (Boden statt Baumkronen, schärfere Grate)
