# Fortschritt

## Stand

Map Creator 1.10.1 (upstream oofbyte 1.10.0, eigene Commits darauf rebased am 2026-11-19): echtes Gelände (USGS 3DEP in
den USA, AWS Terrain Tiles sonst), Wald nach Spiel-Biomen mit Baumbudget, echte Pisten und Lifte aus OpenStreetMap,
Installation ins Spiel. Seit 1.10 baut jede Karte auf einer eigenen Vorlage (`data/map_template.zip`) statt auf einer
Kopie von Mad River Glen; die exe des Spiels wird nicht mehr gepatcht (über 1,5 Mio. Bäume braucht „Oof's Tree
Limit“). Die Mod Library ist upstream entfernt, Mods kommen über Z's Mod Loader
(`oldmods.py` räumt Altes einmal weg). Neu (noch nicht veröffentlicht): in Europa optional Sonnys LiDAR-Höhenmodell „DTM Europe 1" v25" aus einem lokalen Ordner; Option
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
- Update auf upstream 1.9.0: eigene Commits per Rebase auf `origin/main` gesetzt, Konflikte nur in `README.md`,
  `CHANGELOG.md` („Next version“ über v1.9.0), `docs/DEVELOPMENT.md`. Self-Checks alle grün
  (`py -3.12 tests/test_*.py`; Python 3.14 hat die Abhängigkeiten nicht), in der App vom Nutzer geprüft:
  Sonny-Ordner, Real forest, 3D-Wald gehen (2026-11-17)
- Update auf upstream 1.10.0 (2026-11-19): Rebase mit `-X ignore-cr-at-eol` (upstream wechselt Zeilenenden je Datei),
  danach jede Datei auf die Zeilenenden von `origin/main` gebracht. Echte Konflikte: `trees.plant` nimmt jetzt
  `stats()['density']` statt Spender-Records (Signatur `plant(H, sp, sc, rng, ok, …)`), `growable` bleibt davor;
  Startsave in `builder._build` ist upstream neu (`startsave.build`), bekommt `resort.Terrain(...)` direkt, weil
  `forest_inputs` kein `terrain` zurückgibt; „Real forest“-Schalter über der neuen expWarn-Meldung; CHANGELOG
  „Next version“ über v1.10.0. Alle 10 Self-Checks grün (`py -3.12 tests/test_*.py`); in der App und im Spiel vom
  Nutzer geprüft und abgenommen (2026-11-19)
- Deutsche Anleitung für Spieler „Sonny & Wald“ als Design-Canvas-Artifact
  (https://claude.ai/artifact/YRytbJztoc571VVbQP4wCy): Teil 1 Sonny-Höhendaten mit rot markiertem
  „Local elevation data…“, Teil 2 Baumfunktion mit 3D-Vergleich Real forest aus/an; im App-Look, vom Nutzer
  abgenommen (2026-11-19). Screenshots per `tests/guide_shots.py` (St. Anton 8 km, eigener App-Datenordner,
  Sonny- und Spielordner aus den echten Einstellungen); Zuschnitte und Markierungen von Hand, nicht im Skript
- 3D-Vorschau: Höhenregler startet auf 1.0× statt 1.5× (`web/viewer3d.js` Startwert `exag`, `web/index.html`)
  (2026-11-20)
- Test-exe für Freunde lokal gebaut mit `packaging\build.bat` (`dist\SkiEO-Map-Creator.exe`, 34 MB, nicht im Repo);
  startet, `verify_exe.py` meldet nur die gewollt fehlende Oodle-DLL (2026-11-20)
- Anleitung als PDF: `docs/Anleitung-Sonny-Wald.pdf` (2 Seiten, Text durchsuchbar), aus den Artifact-Seiten
  gedruckt (2026-11-20)
- Fels grau auf Map-Creator-Karten seit Spiel-Build 26803581 behoben (2026-11-20): `M_Terrain` holt Fels jetzt über
  `ML_Rock`, `RockTiling` ist eine Kachelzahl (Standard 32, vorher Weltgrösse in cm, 8192). Der Override 8192 × 3 =
  24576 kachelte 768× zu dicht → nur graue Mip-Stufe. `MATERIAL_OVERRIDES = {}`, neue Standards im Kommentar,
  `RECIPE` 13 / `RECIPE_OSM` 17 (`skieo_creator/builder.py`). Belegt an `MI_ebenalp_f40ef4_Terrain` aus dem Karten-Pak
  (`get_scalars` → 24576). Self-Checks grün, ebenalp neu gebaut und im Spiel vom Nutzer bestätigt
- Version 1.10.1 (`skieo_creator/__init__.py`, `packaging/version_info.txt`, CHANGELOG „v1.10.1“ statt „Next
  version“, enthält auch Sonny / Real forest / 3D-Wald)

## Offen

- Fels-Tiling: bei grossen Felswänden auf diagonale Streifen achten (früherer Grund für den Override); falls ja,
  `MATERIAL_OVERRIDES['RockTiling']` unter 32 im Spiel gegentesten (Richtung der neuen Kachelzahl nicht geprüft)
- Vor dem nächsten Override: Prüfung in `gamedata._validate`, dass der Spiel-Standard noch der ist, für den er gedacht
  war (bisher nicht nötig, es gibt keinen)

- Test-exe in `dist\` ist noch 1.10.0 (vor dem Fels-Fix gebaut) — vor dem Verschicken mit `packaging\build.bat`
  neu bauen
- Versionsnummer 1.10.1 kann mit einem künftigen upstream-1.10.1 kollidieren (Update-Prüfung hielte die exe dann
  für aktuell) — beim nächsten Rebase prüfen
- Anleitung: 3D-Screenshots zeigen noch Höhe 1.5× — bei der nächsten Änderung der Anleitung neu aufnehmen

- `SONNY_SMOOTH_M` (8 m) an echten Karten im Spiel feinjustieren
- Release, wenn veröffentlicht werden soll
- Real forest: Offline-Fall (Netz aus, Cache leer → „Rebuild recommended“) noch nicht in der App geprüft
- 3D-Wald: noch nicht geprüft mit Real forest an, mit OSM-Pisten und auf 20-km-Karte (Bildrate)

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
- _Kein Gletscher-Mod_ (2026-11-17 verworfen, zu komplex) — befahrbar macht eine Piste nur Schnee, keine Textur;
  Sommerschnee ginge nur über Temperatur per Lua-Mod. Im Winter liegt auf OSM-Gletschern ohnehin Tiefschnee
- _PDF mit Chromium headless shell (Playwright), je Seite einzeln + pypdf zusammengefügt_ — Edge druckt headless
  hier nichts, Zen/Firefox hat kein `--print-to-pdf`, Chromium wendet benannte `@page`-Grössen nicht an; jede Seite
  so hoch wie ihr Inhalt (Teil 1 braucht 1389 statt 1320 px). Bildschirmformat, nicht A4
- _Kein Fels aus OSM_ — `M_Terrain` hat keine Ebenen-Maske pro Karte; Fels nur über Hangneigung
  (`MPC_TerrainSurface`: `RockSlopeStartDeg`, `RockSlopeFullDeg`), ginge nur mit eigenem Material
