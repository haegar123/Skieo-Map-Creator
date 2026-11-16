# Echter Wald über die ganze Karte (Satellit)

## Context

Der generierte Wald füllt heute alles unterhalb der Baumgrenze (außer Steilhängen). Nur bei Karten mit echten Pisten/Liften
folgt er im Umkreis von 300 m (`COVER_BUF_M`) der echten Baumbedeckung aus den Meta/WRI-Kronenhöhen (`resort.tree_cover`).
Wunsch: Bäume nur dort, wo in echt Wald steht. Entscheidung des Users: Quelle **Satellit (vorhandene Kronenhöhen), ganze
Karte**; **Checkbox im Erstellen-Dialog, Standard aus**. OSM-Waldpolygone: nicht verwendet. Schnee/Fels: nicht Teil (siehe
Untersuchung: Schnee macht das Spiel selbst, Fels nur über Hangneigung).

Ergebnis: Option "Real forest" an → Dichte des generierten Waldes wird über die ganze Karte mit der echten Bedeckung
multipliziert. Arten, Stände, Baumgrenze, Steilhang-Grenze, Budget bleiben wie gehabt (Maske kann nur wegnehmen, nie
hinzufügen). Wo keine Satellitendaten (Meer/fehlende Datei, `COVER_NODATA`): generierter Wald bleibt.

## Änderungen

### 1. `skieo_creator/resort.py` — `tree_cover` für die ganze Karte
- `tree_cover(pl, terrain, canopy)`: `pl is None` = ganze Karte → `near = np.ones((n, n))`, Zeichnen der Linien
  überspringen. Sonst unverändert (300 m + Übergang).
- Docstring ergänzen. Rest (Sampling in `COVER_ROWS`-Blöcken, `COVER_STEP_M`, `COVER_WIN_M`, `COVER_FULL`, `NODATA → 1`)
  bleibt und wird wiederverwendet.
- Aufwand bei 20 km: 2017² Zellen, k=2 → ~16 M Abfragen, ~530 Kronenhöhen-Blöcke (Cache ~20 KB/Block gemessen, also
  grob 10–50 MB einmalig; `canopy.CACHE_MB` = 400 reicht). Kein neuer Level-Parameter nötig.

### 2. `skieo_creator/builder.py` — `_build`
- Nach dem OSM-Block: Cover-Berechnung zusammenführen:
  ```python
  whole = bool(spec.get('real_forest'))
  if whole or (pl and pl['tree_check']):
      terrain = terrain or resort.Terrain(H, fr, spec['lat'], spec['lon'], spec['size_m'])
      try:
          cover = resort.tree_cover(None if whole else pl, terrain, lambda la, lo: canopy.heights(
              la, lo, cancel, lambda f, t: progress(0.015, t), nodata=resort.COVER_NODATA))
      except (net.NetError, canopy.CanopyError, OSError) as ex:
          log.warning('real tree cover failed: %s', ex)
          if pl: pl['tree_check'] = False
          forest_ok = False
  ```
  (`terrain` vorher mit `None` initialisieren; bei OSM existiert es schon.)
- Stats: `real_forest=True/False` (nur wenn angefordert, sonst `None`).
- `recipe(spec, stats)`: RECIPE **nicht** erhöhen (alte Karten ändern sich nicht). Nur: angefordert aber fehlgeschlagen
  (`stats['real_forest'] is False`) → eins weniger → "Rebuild recommended", wie schon bei `tree_check`.

### 3. `skieo_creator/api.py`
- `_validate`: `real_forest=bool(params.get('real_forest'))`.
- `_do_create`: `'real_forest'` in die Liste der Spec-Schlüssel → steht in `map.json`, Rebuild (`rebuild_map`) übernimmt es
  automatisch.

### 4. `web/index.html` + `web/app.js`
- Im Baum-Abschnitt (neben "Experimental mode", gleiches `row-label`/`switch`-Muster): Checkbox `realForest`,
  Label "Real forest", Meta-Text: "Trees only where satellite images show forest (Meta / WRI tree heights). Downloads
  about 10–50 MB the first time; without internet the generated forest is used."
- `create`-Aufruf (`app.js` ~651): `real_forest: $('realForest').checked`.
- Attribution "Tree heights Meta, WRI" steht schon im Footer (`index.html:163`).

### 5. Tests — `tests/test_resort.py` (nach Zeile 113, gleiche Hilfen)
- `tree_cover(None, terrain, bowl_open)`: Punkt weit weg von Lift/Piste (300, 300) = 1.0 (Bäume 20 m), offene Fläche
  < 0.05 — auch außerhalb der 300 m.
- Kanopie überall 0 → ganze Karte 0 (keine Bäume); überall `COVER_NODATA` → überall 1.0.

### 6. Doku
- `docs/DEVELOPMENT.md` Abschnitt Forest/Real trails: Option real forest, `tree_cover(None, ...)`.
- `CHANGELOG.md`: Eintrag unter nächster Version.

## Verifikation
1. `py -3.12 tests/test_resort.py` und `py -3.12 tests/test_trees.py` (bestehende Tests grün, neue Asserts).
2. App starten (`/run`-Skill bzw. `.venv`/`py -3.12 run.py`), Sisters-Gebiet (44.128, -121.732, 12.5 km) laden, Karte mit
   "Real forest" an erstellen; Log: Download-Fortschritt "satellite tree heights", Baumzahl deutlich unter der ohne Option.
3. Vergleich im Spiel bzw. Vorschau: Lavafelder/Kahlflächen an den Sisters und Wiesen bleiben baumlos, Wald endet an
   echter Waldgrenze.
4. Offline-Test (Netz trennen, Cache leeren): Build läuft durch, generierter Wald, Karte zeigt "Rebuild recommended".
5. Rebuild einer Karte mit Option: `real_forest` bleibt in `map.json`.
