# Fortschritt

## Stand

Map Creator 1.8.2: echtes Gelände (USGS 3DEP in den USA, AWS Terrain Tiles sonst), Wald nach Spiel-Biomen mit
Baumbudget, echte Pisten und Lifte aus OpenStreetMap, Installation ins Spiel, Mod Library. Neu (noch nicht
veröffentlicht): in Europa optional Sonnys LiDAR-Höhenmodell „DTM Europe 1" v25" aus einem lokalen Ordner; Option
„Real forest“ (Bäume nur, wo Satellitenbilder Wald zeigen); 3D-Ansicht zeigt den Wald der Karte.

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
- Option „Real forest“ (Standard aus): echte Baumbedeckung aus den Meta/WRI-Kronenhöhen über die ganze Karte, auch
  ohne OSM (`resort.tree_cover(None, …)`, `builder._build`, `spec['real_forest']` in `map.json`; Schalter in
  `web/index.html`, `web/app.js`). Download fehlgeschlagen → generierter Wald + „Rebuild recommended“
  (`builder.recipe`, Stat `real_forest=False`). Tests in `tests/test_resort.py`; in der App vom Nutzer getestet:
  realistische Wälder (2026-11-16)
- 3D-Ansicht zeigt den Wald, den die Karte bekommt: bis 200k Baumkegel (InstancedMesh) über Waldboden-Textur, mit den
  Einstellungen des Erstellen-Dialogs inkl. OSM-Schneisen und Real forest. `api.view3d` ist jetzt ein Job (Fortschritt
  im 3D-Lade-Pill, Esc bricht ab). Gemeinsame Waldlogik für Vorschau und Build: `trees.growable`,
  `builder.forest_inputs` (`skieo_creator/view3d.py`, `web/viewer3d.js`, `web/app.js`); Test `tests/test_view3d.py`;
  in der App (Killington) geprüft, vom Nutzer abgenommen (2026-11-16)

## Offen

- `SONNY_SMOOTH_M` (8 m) an echten Karten im Spiel feinjustieren
- Version, CHANGELOG und Release, wenn veröffentlicht werden soll
- `tests/test_modlib.py` schlägt gelegentlich fehl (auch ohne diese Änderung): Ursache suchen
- Real forest: Offline-Fall (Netz aus, Cache leer → „Rebuild recommended“) noch nicht in der App geprüft
- 3D-Wald: noch nicht geprüft mit Real forest an, mit OSM-Pisten und auf 20-km-Karte (Bildrate)
- Sisters bis in den Sommer spielen, dann prüfen, ob die OSM-Gletscherflächen weiß bleiben (Beleg für permanenten
  Schnee; Auswerte-Skripte waren nur im Scratchpad)

## Entscheidungen

- _Bei Bedarf aus den Zips lesen statt entpacken_ — spart ~15 GB; das fertige Quadrat liegt ohnehin im `.npy`-Cache,
  Auspacken kostet nur beim ersten Laden ~1 s pro Kachel
- _Cache-Schlüssel enthält die vorhandenen Kacheln_ — eine später hinzugelegte Kachel ersetzt die AWS-Füllung
- _Alte Karten behalten beim Rebuild ihre Quelle_ — sonst verrutschen Pisten und Lifte; Sonny-Karte ohne Ordner fällt
  auf AWS zurück (Warnung im Log) statt abzubrechen
- _Sonny vor 3DEP in der Reihenfolge_ — überschneiden sich nicht (Europa / USA), daher harmlos
- _Sonny ist nicht feiner als AWS (1" ≈ 31 × 21 m)_ — Gewinn ist LiDAR-Qualität (Boden statt Baumkronen, schärfere Grate)
- _Echter Wald aus Satellit, nicht aus OSM-Waldpolygonen_ — OSM-Wald oft lückenhaft (fehlende Fläche = kahler Hang),
  Kronenhöhen weltweit und schon im Code; Standard aus, weil es das Aussehen jeder neuen Karte ändert
- _Real forest erhöht RECIPE nicht_ — alte Karten ändern sich nicht; nur ein fehlgeschlagener Download zählt eins
  weniger
- _3D-Vorschau und Build teilen `growable`/`forest_inputs`_ — sonst zeigt die Vorschau einen anderen Wald als das
  Spiel; Kegel ohne Schattenwurf und nur eine Form (200k Schattenkegel zu teuer; Arten trennen, wenn gewünscht)
- _Design-Hook: `broken-image` in `web/index.html` ignoriert_ (`.impeccable/config.json`) — `#diorama`/`#doneImg`
  bekommen ihr `src` zur Laufzeit von `app.js`
- _Kein Schnee aus OSM-Gletschern_ — Schnee ist eine Simulation im Spielstand (`SnowData`, int32-Länge + zlib,
  2× float16, Kanal 0 = Höhe in cm), startet bei 0; auf Sisters liegt der Tiefschnee schon ohne Zutun auf den
  OSM-Gletschern (91 % der Fläche > 1 m, Median 484 cm gegenüber 63 cm daneben)
- _Kein Fels aus OSM_ — `M_Terrain` hat keine Ebenen-Maske pro Karte; Fels nur über Hangneigung
  (`MPC_TerrainSurface`: `RockSlopeStartDeg`, `RockSlopeFullDeg`), ginge nur mit eigenem Material
