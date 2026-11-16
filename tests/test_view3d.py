"""Self-check of the 3D view's data (skieo_creator/view3d.py): trees only where the forest grows, on a made-up hill.
usage: python tests/test_view3d.py"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from skieo_creator import builder, trees, view3d  # noqa: E402

G, SIZE = trees.G, 10000.0
yy, xx = np.mgrid[0:G, 0:G] * (SIZE / (G - 1))
dem = 800 + 600 * np.exp(-((xx - 5000) ** 2 + (yy - 5000) ** 2) / 2500.0 ** 2)

# forest in the east half only: every tree east of the centre, none on bare ground
dens = np.zeros((G, G))
dens[:, G // 2 + 1:] = 1.0
heights, meta, albedo, pts = view3d.build(dem, SIZE, 1200.0, dens)
p = np.frombuffer(pts, np.float32).reshape(-1, 4)
assert meta['trees'] == len(p) == view3d.PREVIEW_TREES, meta['trees']
assert (p[:, 0] > 0).all() and (np.abs(p[:, :2]) <= SIZE / 2).all()
assert len(heights) == 2 * meta['n'] ** 2 and albedo[:2] == b'\xff\xd8'
assert view3d.tree_points(np.zeros((G, G)), SIZE) == b''

# growable: no trees above the treeline, the same forest as the build plants by
H, fr = builder.heights(dem, SIZE)
ok = trees.growable(H, fr.hmin, fr.sp, fr.sc, 1200.0, 0.0)
assert ok[dem > 1201].max() == 0 and ok[(dem > 900) & (dem < 1100)].min() == 1   # (the flat foot reads as a lake)
p = np.frombuffer(view3d.tree_points(ok, SIZE), np.float32).reshape(-1, 4)
r, c = np.rint((p[:, 1] + SIZE / 2) / fr.sp).astype(int), np.rint((p[:, 0] + SIZE / 2) / fr.sp).astype(int)
assert dem[r, c].max() < 1200 + 10                                   # jitter: at most half a cell past the edge
print('view3d ok: %d trees' % len(p))
