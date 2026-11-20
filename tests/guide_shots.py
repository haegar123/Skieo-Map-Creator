"""Screenshots for the user guide on Sonny's elevation data and the forest (dev tool, like ui_drive.py).

usage: python tests/guide_shots.py <out dir>
  Runs the creator on a scratch app data folder next to <out dir> (the real settings stay as they are), with the
  Sonny folder and game folder of the real settings: the LiDAR button before and after picking the folder, St. Anton am Arlberg
  (8 km), the Terrain chip, the forest settings, Real forest on, the 3D view with Real forest off and on.
"""
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else 'guide_shots')
REAL = json.loads((Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
                   / 'SkiEO Map Creator' / 'settings.json').read_text())
SONNY = REAL.get('dem_folder')
if not SONNY:
    sys.exit('Pick the Sonny folder in the Map Creator first (Use LiDAR elevation tiles...).')
SCR = OUT.resolve().parent / (OUT.name + '_appdata')
os.environ['LOCALAPPDATA'] = str(SCR)              # before any import that reads paths.APP_DIR
(SCR / 'SkiEO Map Creator').mkdir(parents=True, exist_ok=True)
(SCR / 'SkiEO Map Creator' / 'settings.json').write_text(json.dumps({
    'game_folder': REAL.get('game_folder'), 'units': 'm', 'layer': 'topo',
    'view': {'lat': 47.13, 'lon': 10.25, 'km': 8, 'zoom': 12}, 'dem_folder': None,
    'old_mods_removed': REAL.get('old_mods_removed', [])}))
sys.argv = ['ui_drive', str(OUT), 'guide']
sys.path.insert(0, str(REPO / 'tests'))
sys.path.insert(0, str(REPO))
import ui_drive as U  # noqa: E402
from skieo_creator import api, paths, server  # noqa: E402
from skieo_creator.main import release_webview_profile, style_titlebar  # noqa: E402

js, shot, wait = U.js, U.shot, U.wait_js
B = None


def into(win, el_id, block='start'):
    js(win, "document.getElementById('%s').scrollIntoView({block: '%s'}); 0" % (el_id, block))
    time.sleep(0.8)


def open3d(win, name):
    js(win, "document.getElementById('photo').click()")
    wait(win, "!document.getElementById('v3d').classList.contains('hidden') && "
              "document.getElementById('v3dLoading').classList.contains('hidden')", 600)
    time.sleep(5)
    shot(name)
    js(win, "document.getElementById('v3dClose').click()")
    time.sleep(1.5)


def drive(win):
    wait(win, "document.getElementById('ver').textContent.length > 0", 60)
    time.sleep(3)
    shot('01_start')                                            # section 1: "Use LiDAR elevation tiles..."
    B._settings.set(dem_folder=SONNY)                           # what picking the folder does (no file dialog)
    js(win, "renderDem(%s); toast('%d elevation tiles found. Load the terrain again to use them.'); 0"
       % (json.dumps(B._dem_status()), B._dem_status()['tiles']))
    time.sleep(1.2)
    shot('02_folder_set')
    js(win, "document.getElementById('q').value = 'St. Anton am Arlberg'; doSearch();")
    wait(win, "document.querySelectorAll('.row[data-i]').length > 0", 30)
    time.sleep(0.6)
    shot('03_search')
    js(win, "pickResult(0); moveSquare(47.135, 10.255); fitSquare(); 0")   # the valley with forest, not the rocks south
    time.sleep(4)
    shot('03b_square')
    js(win, "document.getElementById('loadBtn').click()")
    wait(win, "!!S.preview", 400)
    time.sleep(3)
    into(win, 'srcTag', 'center')
    shot('04_terrain')
    print('source', js(win, "document.getElementById('srcTag').textContent"), flush=True)
    into(win, 'biome')
    shot('05_forest_settings')
    into(win, 'trees')
    shot('06_trees')
    js(win, "document.getElementById('realForest').click()")
    time.sleep(0.6)
    into(win, 'realForest', 'center')
    shot('07_real_forest_on')
    js(win, "document.getElementById('realForest').click()")    # off again: the generated forest first
    time.sleep(0.4)
    open3d(win, '08_3d_generated')
    js(win, "document.getElementById('realForest').click()")
    time.sleep(0.4)
    open3d(win, '09_3d_real')
    print('errors', js(win, "JSON.stringify(window.__errs || [])"), flush=True)
    time.sleep(0.5)
    win.destroy()


def main():
    global B
    import webview
    paths.setup_logging()
    release_webview_profile(paths.WEBVIEW)
    url, _ = server.start()
    B = api.Api()
    win = webview.create_window(U.TITLE, url, js_api=B, width=1440, height=900, min_size=(1100, 680),
                                background_color='#0b1118')
    B._attach(win)
    win.events.shown += style_titlebar
    webview.start(drive, (win,), gui='edgechromium', private_mode=False, storage_path=str(paths.WEBVIEW))


if __name__ == '__main__':
    main()
