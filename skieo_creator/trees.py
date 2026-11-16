"""Forests for generated maps, in the game's baked-obstacle format (DA_BakedObstacles_<map>, 'SKOB' v2).

A SKOB v2 blob holds a species table -- (species data asset, size class 0-3 = 5/10/15/20 m, state 0 healthy /
1 dying / 2 dead / 3 krummholz, trunk mesh) -- and 20-byte tree records: x, y, z (int32 mm in the landscape frame),
yaw / width / height bytes and a u16 index into the table. Every entry of the game's own tables is exactly one
(size, state, trunk mesh) variant of the species' DA_TreeSpecies_* asset, so tables for any biome are built from those
assets (species_variants) and stay consistent with what the game would bake itself.
"""
import json
import logging
import struct

import numpy as np

from . import biomes, ecology, paths
from .engine import legacy_header as lh

log = logging.getLogger(__name__)
G = 2017
STRIDE = 20
BANDS = 10
# The tree slider starts at, and goes up to, the game's own forest density over the map's area (its maps average
# ~18,800 trees per km2: Mad River Glen 19,007, Sugar Bowl 18,682) or TREE_BUDGET (below), whichever is less; in
# experimental mode up to the density, 7.5M at 20 km. Its count is the most tree instances a map keeps (tree_budget).
# Trees load in square by square after the map opens, the game slow until done (the UI warns). The game drops all
# trees past 1.5M unless the Oof's Tree Limit mod raises its limit in memory (install.GAME_TREE_LIMIT, tree_mod_on).
TREES_PER_KM2 = 18750
DEFAULT_TREES = 250000         # about what the game's own maps have (Mad River Glen 195k, Sugar Bowl 256k)
MIN_TREES = 25000
# Full forest up to the map's tree slope ("Trees up to" slider), none at or above it. Measured on the slope's steepest
# value around each vertex, so trees don't stand right at a rock face either. The default keeps big cliffs bare and
# is about where the game's own forests end: their trees thin out from ~20 deg to ~5% density at 42 deg, and 99%
# stand on ground up to 41 deg (Mad River Glen) / 38.5 deg (Sugar Bowl) in this measure (measured 2026-11-01 from
# their baked obstacles and height textures).
TREE_SLOPE = 42.0
MIN_TREE_SLOPE, MAX_TREE_SLOPE = 20.0, 55.0
TREELINE_FADE, MAX_TREELINE_FADE = 0.0, 1000.0     # m below the treeline over which forest thins out (0 = sharp)
# Forests grow in stands: patches about STAND_M across, each with the species of the game's own maps of the biome at
# its height and ground (ecology), each species healthy and in one size per stand. The game draws trees with one
# instanced component per 164 m square per kind of tree (species, size, state, trunk; two for broadleaf: trunk and
# canopy) and, while trees are hidden (the right-hand toggle), walks every component every frame. With every kind in
# every square a 9.7 km map had 117k components: 87 -> 37 fps with trees hidden, and gigabytes of memory. The game's
# own maps have ~22 per square (Mad River Glen 9k in all); stands have about 9.
STAND_M = 400.0
PRESENT = 0.03                 # a species grows in a stand where its odds there are at least this
STAND_EDGE = 0.1               # neighbouring stands mingle over this share of STAND_M at their borders
STAND_FIELD = ((250.0, 2.0), (40.0, 1.5))   # species clumps: layers of (size m, strength), like the game's
# StandFieldStrength; as clumped as its maps (Mad River Glen: one species is 61% of a 164 m square, 77% of 40 m)
STAND_WARP = ((2.0, 0.6), (0.8, 0.35), (0.3, 0.15), (0.12, 0.07))   # outline noise: (feature size, shift) x STAND_M
# Near the top of the forest only conifers (and the broadleaf trees that make the tree line where they grow):
# broadleaf thin out from SUMMIT_FROM to SUMMIT_TO of the way from a mountain's valleys (the lowest ground within
# VALLEY_R) to the top of its forest (its summit, the highest ground within SUMMIT_R, or the treeline if lower), stand by
# stand, then tree by tree over SUMMIT_FADE more. The treeline itself is unchanged: no trees above it.
SUMMIT_FROM, SUMMIT_TO = 0.5, 0.7
SUMMIT_FADE = 0.1             # above SUMMIT_TO the last broadleaf trees go over this much more of the way, tree by tree
SUMMIT_R, VALLEY_R = 1500.0, 3000.0
SUMMIT_BROADLEAF = ('MountainBirch', 'SnowGum', 'SouthernBeech')
# Dead trees and krummholz stand one by one among the healthy trees, never as whole stands: each tree is as likely to be
# dead as on the game's maps, and krummholz (near the tops of their forests) KRUMMHOLZ_SHARE as likely, always a living
# one (the game's dead krummholz reads as dead trees), so the tops stay wooded. Over each block of LONE_BLOCK x
# LONE_BLOCK of the game's squares, the trees in each such state take the species, size and mesh of one of them: one
# more kind of tree per state in a square, not one per species or stand. No dying trees: the game's maps have few
# (1-2% in most places), and one by one they would cost every square one more kind.
DYING, DEAD, KRUMMHOLZ = 1, 2, 3                      # tree states (0 healthy)
KRUMMHOLZ_SHARE = 0.5
LONE = ((DEAD, 1.0), (KRUMMHOLZ, KRUMMHOLZ_SHARE))   # (state, share of the game's odds)
SQUARE_M = 163.84             # the game's squares of trees (one instanced component per kind of tree in each)
LONE_BLOCK = 6
# What a forest costs the game each frame goes with the tree instances it draws: every tree, and the canopy of each
# living broadleaf tree or larch (a second mesh), hardly with their triangles (its trees are Nanite, by the help of
# its SkiResortSimulator.Obstacle.CullDistanceScale: "0 = never cull, leaving the transition to Nanite LOD", which its
# top Draw Distance sets). Sun Peaks (14 km, 3.6M trees): 47 fps on an RTX 5070 Ti, 133 without trees, ~4.5 ms a frame
# per million trees; firs drawn with the spruce meshes (a third of the triangles) changed nothing. The game itself
# stops at 1.5M trees and its own maps have up to 1.42M (Whistler), so a forest of more instances than its budget (the
# tree slider's count, at most TREE_BUDGET without experimental mode; data/tree_meshes.json: the meshes of each kind
# of tree) thins out, round the resort (within NEAR_M of the lifts and trails) up to 1 / FAR_SHARE times as dense as
# NEAR_FADE_M further out; evenly on a map without the real resort.
# Fewer trees, bigger ones: where a stand keeps few of its trees, its living ones grow a size (5 / 10 / 15 / 20 m, up
# to the species' largest) with the odds that bring back its ground cover (a crown's area taken as its height squared).
TREE_BUDGET = 1_500_000
NEAR_M, NEAR_FADE_M = 1000.0, 1000.0
FAR_SHARE = 0.5
_meshes = None
_stats = None
CONIFERS =('Fir', 'Spruce', 'Pine', 'WhitebarkPine', 'Hemlock', 'Larch', 'Sclerophyll')
BROADLEAF = ('Aspen', 'Birch', 'Maple', 'Oak', 'MountainBirch', 'SouthernBeech', 'SnowGum')


# ---------------------------------------------------------------- formats

def read_skob(uexp):
    raw_len = struct.unpack_from('<i', uexp, 2)[0]
    raw = uexp[6:6 + raw_len]
    if raw[:4] != b'SKOB':
        raise ValueError('not a baked obstacle asset')
    ver, count, stride = struct.unpack_from('<iii', raw, 4)
    if (ver, stride) != (2, STRIDE):
        raise ValueError('baked obstacle format %d/%d is not supported' % (ver, stride))
    recs = raw[len(raw) - count * stride:]
    return dict(head2=uexp[:2], hdr=raw[16:48], table=raw[48:len(raw) - count * stride], records=recs, ver=ver,
                stride=stride, tail=uexp[6 + raw_len + 8:], count=count)


def table_entries(table):
    n = struct.unpack_from('<H', table, 0)[0]
    o, out = 2, []
    for _ in range(n):
        ln = struct.unpack_from('<H', table, o)[0]
        sp = table[o + 2:o + 2 + ln].decode()
        o += 2 + ln
        a, b = table[o], table[o + 1]
        o += 2
        ln = struct.unpack_from('<H', table, o)[0]
        mesh = table[o + 2:o + 2 + ln].decode()
        o += 2 + ln
        out.append((sp, a, b, mesh))
    if o != len(table):
        raise ValueError('tree table has trailing bytes')
    return out


def encode_table(ents):
    out = struct.pack('<H', len(ents))
    for sp, a, b, mesh in ents:
        out += struct.pack('<H', len(sp)) + sp.encode() + bytes([a, b]) + struct.pack('<H', len(mesh)) + mesh.encode()
    return out


def species_variants(ua, ue):
    """[(size, state, trunk mesh)] of a DA_TreeSpecies_* asset. Its variant array is unversioned structs: fragment
    0x1180 (8 fields, zero mask follows), mask byte, then Size (u8) and State (u8) unless zero, TrunkMesh (import
    index), CanopyMesh (import index) unless zero, then fields this tool does not need."""
    f, names, _, _ = lh.parse(ua)
    n, off = f['imports_count'][1], f['imports_offset'][1]
    imps = [names[struct.unpack_from('<8i', ua, off + 32 * i)[5]] for i in range(n)]
    out = []
    p = 0
    while True:
        p = ue.find(b'\x80\x11', p)
        if p < 0:
            break
        m = ue[p + 2]
        q = p + 3
        size = 0 if m & 1 else ue[q]
        q += 0 if m & 1 else 1
        state = 0 if m & 2 else ue[q]
        q += 0 if m & 2 else 1
        trunk = struct.unpack_from('<i', ue, q)[0]
        if not (-len(imps) <= trunk < 0) or size > 3 or state > 3:
            raise ValueError('unexpected tree species layout')
        out.append((size, state, imps[-trunk - 1]))
        p += 2
    if not out:
        raise ValueError('no tree variants found')
    return out


# ---------------------------------------------------------------- planting

def _slope(H, sp, sc):
    gy, gx = np.gradient(H.astype(np.float64) * sc, sp)
    return np.degrees(np.arctan(np.hypot(gx, gy)))


def _local_max(a, r):
    out = a.copy()
    p = np.pad(a, r, mode='edge')
    n0, n1 = a.shape
    for dy in range(2 * r + 1):
        for dx in range(2 * r + 1):
            np.maximum(out, p[dy:dy + n0, dx:dx + n1], out=out)
    return out


def open_ground(H, hmin, sp, sc, treeline, valley):
    """treeless cells: above the treeline, flat valley floor below `valley` (m), and lakes / sea (dead flat)"""
    elev = (H.astype(np.float64) + 32768) * sc + hmin
    slope = _slope(H, sp, sc)
    mask = np.zeros(H.shape, bool)
    if treeline > 0:
        mask |= elev > treeline
    if valley > 0:
        mask |= (elev < valley) & (slope < 10.0)
    r = max(2, int(round(12.0 / sp)))                       # water: flat to within 0.6 deg over ~25 m
    mask |= _local_max(slope, r) < 0.6
    return mask


def max_trees(size_km, experimental=False):
    """the top of the tree slider for a map size: the game's own forest density over its area, at most TREE_BUDGET
    without experimental mode"""
    full = int(round(TREES_PER_KM2 * size_km * size_km, -3))
    return full if experimental else min(full, TREE_BUDGET)


def tree_budget(size_km, count, experimental=False):
    """the most tree instances a map keeps (forest's budget) for the slider's count: the count, but the whole forest at
    the slider's top; never over TREE_BUDGET without experimental mode (None: no limit)"""
    if count >= max_trees(size_km, True):
        return None if experimental else TREE_BUDGET
    return count if experimental else min(count, TREE_BUDGET)


def stats():
    """data/forest_stats.json (packaging/make_forest_stats.py): what the game's own forests are like -- trees per m2,
    and per look ('mrg' / 'sb', biomes.BIOMES) the trees' scale range and their scales by height band and size"""
    global _stats
    if _stats is None:
        _stats = json.loads(paths.resource('data', 'forest_stats.json').read_text(encoding='utf-8'))
    return _stats


def header_tail(look):
    """the forest header's last 16 bytes: a look's scale range (4 floats)"""
    return struct.pack('<4f', *stats()['looks'][look or 'mrg']['header'])


def growable(H, hmin, sp, sc, treeline, valley, tree_slope=TREE_SLOPE, treeline_fade=0.0, cover=None):
    """the share (0..1 per vertex, row = y) of the full forest density that grows there: none on open ground or
    steeper than tree_slope (deg), thinning out over the last treeline_fade m below the treeline, times cover (the
    real tree cover, optional). The map build and the 3D view both plant by this."""
    ok = ((_local_max(_slope(H, sp, sc), 1) < tree_slope) & ~open_ground(H, hmin, sp, sc, treeline, valley)).astype(np.float64)
    if treeline > 0 and treeline_fade > 0:
        ok *= np.clip((treeline - ((H.astype(np.float64) + 32768) * sc + hmin)) / treeline_fade, 0.0, 1.0)
    if cover is not None:
        ok *= cover
    return ok


def plant(H, sp, sc, rng, ok, max_trees=DEFAULT_TREES, clear=None):
    """tree positions (x, y in metres from the landscape corner, z in metres above its bottom) at the stock density
    times ok (growable), thinned evenly when that would give more than max_trees; clear (optional (bool raster, metres
    per cell), row = y) marks ground kept free of trees (trails, lifts)"""
    dens = stats()['density']
    ext = (G - 1) * sp
    area_frac = max(float(ok.mean()), 0.01)                 # the share of the map trees can grow on
    n = min(int(ext * ext * dens), int(max_trees / area_frac))
    x = rng.uniform(0.5, ext - 0.5, n)
    y = rng.uniform(0.5, ext - 0.5, n)
    vx, vy = x / sp, y / sp
    x0 = np.clip(vx.astype(int), 0, G - 2)
    y0 = np.clip(vy.astype(int), 0, G - 2)
    fx, fy = vx - x0, vy - y0
    Hf = H.astype(np.float64)
    h = (Hf[y0, x0] * (1 - fx) * (1 - fy) + Hf[y0, x0 + 1] * fx * (1 - fy) + Hf[y0 + 1, x0] * (1 - fx) * fy
         + Hf[y0 + 1, x0 + 1] * fx * fy)
    iy, ix = np.clip(np.rint(vy).astype(int), 0, G - 1), np.clip(np.rint(vx).astype(int), 0, G - 1)
    keep = rng.random(n) < ok[iy, ix]
    if clear is not None:
        cm, res = clear
        keep &= ~cm[np.clip((y / res).astype(int), 0, cm.shape[0] - 1), np.clip((x / res).astype(int), 0, cm.shape[1] - 1)]
    keep &= np.cumsum(keep) <= max_trees      # the thinning is random; never past max_trees (the game drops ALL past its limit)
    return x[keep], y[keep], (h[keep] + 32768) * sc


def _noise(x, y, scale, rng):
    """smooth value noise in -1..1 with features about `scale` m across, its lattice turned at random (no seams along
    the map's axes)"""
    a = rng.uniform(0, 2 * np.pi)
    fx, fy = (x * np.cos(a) - y * np.sin(a)) / scale, (x * np.sin(a) + y * np.cos(a)) / scale
    fx, fy = fx - fx.min(), fy - fy.min()
    n = int(max(float(fx.max()), float(fy.max()))) + 3
    g = rng.uniform(-1.0, 1.0, (n, n)).astype(np.float32)
    i, j = np.minimum(fx.astype(np.int64), n - 2), np.minimum(fy.astype(np.int64), n - 2)
    u, v = fx - i, fy - j
    u, v = u * u * (3 - 2 * u), v * v * (3 - 2 * v)
    return (g[i, j] * (1 - u) + g[i + 1, j] * u) * (1 - v) + (g[i, j + 1] * (1 - u) + g[i + 1, j + 1] * u) * v


def _cells(wx, wy, size, rng):
    """the nearest and second nearest centre of a jittered grid of cells `size` m across: (id, d2, id, d2)"""
    o = 2 * size - min(float(wx.min()), float(wy.min()), 0.0)            # keep the grid's indices positive
    n = int((max(float(wx.max()), float(wy.max())) + o) // size) + 3
    cx = (np.arange(n)[:, None] + rng.uniform(0.2, 0.8, (n, n))) * size - o
    cy = (np.arange(n)[None, :] + rng.uniform(0.2, 0.8, (n, n))) * size - o
    i0, j0 = ((wx + o) // size).astype(np.int64), ((wy + o) // size).astype(np.int64)
    d1 = np.full(len(wx), np.inf, np.float32)
    d2 = d1.copy()
    id1, id2 = np.zeros(len(wx), np.int64), np.zeros(len(wx), np.int64)
    for di in (-1, 0, 1):                                  # jitter within the cell: the nearest centres are neighbours
        for dj in (-1, 0, 1):
            i, j = np.clip(i0 + di, 0, n - 1), np.clip(j0 + dj, 0, n - 1)
            d = ((cx[i, j] - wx) ** 2 + (cy[i, j] - wy) ** 2).astype(np.float32)
            k = i * n + j
            first, second = d < d1, (d >= d1) & (d < d2) & (k != id1)
            d2, id2 = np.where(first, d1, np.where(second, d, d2)), np.where(first, id1, np.where(second, k, id2))
            d1, id1 = np.where(first, d, d1), np.where(first, k, id1)
    return id1, d1, id2, d2


def _patches(x, y, rng, size):
    """patch id per tree (x, y in m): the nearest centre of a jittered grid of cells `size` m across, after moving the
    points by layers of smooth noise so outlines wind at every scale; near a border a tree may belong to the
    neighbouring patch (they mingle there)"""
    if not len(x):
        return np.zeros(0, np.int64)
    x, y = x.astype(np.float32), y.astype(np.float32)
    wx, wy = x.copy(), y.copy()
    for f, a in STAND_WARP:
        wx += a * size * _noise(x, y, f * size, rng)
        wy += a * size * _noise(x, y, f * size, rng)
    pid, d1, other, d2 = _cells(wx, wy, size, rng)
    edge = np.clip(1.0 - (np.sqrt(d2) - np.sqrt(d1)) / (STAND_EDGE * size), 0.0, 1.0)
    mingle = rng.random(len(x)) < 0.5 * edge
    pid[mingle] = other[mingle]
    return pid


def _per(ids, values):
    """the mean of values over each id, per tree"""
    return _group_mean(ids, values, int(ids.max()) + 1)[ids]


def _group_mean(ids, values, n):
    """the mean of values over each id 0..n-1 (0 where none)"""
    return np.bincount(ids, weights=values, minlength=n) / np.maximum(np.bincount(ids, minlength=n), 1)


def _draw(w, rng):
    """one column per row of w (rows of non-negative weights), drawn in proportion"""
    c = np.cumsum(w, axis=1)
    u = rng.random(len(w)) * c[:, -1]
    return np.minimum((u[:, None] > c).sum(axis=1), w.shape[1] - 1)


def _one_of(flag, ids, n, rng):
    """per id 0..n-1, the index of one of its flagged items at random (-1 where none)"""
    idx = np.nonzero(flag)[0]
    idx = idx[rng.permutation(len(idx))]
    u, first = np.unique(ids[idx], return_index=True)
    out = np.full(n, -1, np.int64)
    out[u] = idx[first]
    return out


def _relief(H, sp, sc, hmin, x, y, elev, treeline):
    """where each tree stands between its valley (0) and the top of its forest (1): the lowest ground within VALLEY_R,
    and the highest ground within SUMMIT_R or the treeline (m, 0 = none) if that is lower; on ~50 m cells, 0 where the
    two differ by under 150 m"""
    f = max(1, int(round(50.0 / sp)))
    n = (H.shape[0] - 1) // f
    E = ((H[:n * f, :n * f].astype(np.float32) + 32768) * sc + hmin).reshape(n, f, n, f)

    def spread(a, cells, op):                              # op over the square `cells` cells round each cell
        for _ in range(cells):
            p = np.pad(a, 1, mode='edge')
            a = op(op(p[1:-1, 1:-1], p[:-2, 1:-1]), p[2:, 1:-1])
            p = np.pad(a, 1, mode='edge')
            a = op(op(a, p[1:-1, :-2]), p[1:-1, 2:])
        return a
    cell = f * sp
    peak = spread(E.max(axis=(1, 3)), int(SUMMIT_R / cell), np.maximum)
    valley = spread(E.min(axis=(1, 3)), int(VALLEY_R / cell), np.minimum)
    r = np.clip((y / cell).astype(np.int64), 0, n - 1)                  # H rows run along y
    c = np.clip((x / cell).astype(np.int64), 0, n - 1)
    pk, vl = peak[r, c], valley[r, c]
    if treeline > 0:
        pk = np.minimum(pk, treeline)
    return np.where(pk - vl > 150.0, (elev - vl) / np.maximum(pk - vl, 1.0), 0.0)


def _pick_variant(variants, size, state, pick):
    """indices into `variants` for trees that should look like (size, state); pick: an int per tree (its stand's),
    so a stand keeps to one of the matching variants"""
    def cands(pred):
        return [i for i, v in enumerate(variants) if pred(v)]
    c = cands(lambda v: v[0] == size and v[1] == state)
    if state == KRUMMHOLZ:                                 # a living one: the dead krummholz meshes read as dead trees
        c = [i for i in c if 'dead' not in variants[i][2].lower()] or c
    if not c and state == 3:
        c = cands(lambda v: v[0] == 0 and v[1] == 0)
    if not c and state == 1:
        c = cands(lambda v: v[0] == size and v[1] == 0)
    if not c:
        same = [i for i, v in enumerate(variants) if v[1] == state] or [i for i, v in enumerate(variants) if v[1] == 0]
        best = min(abs(variants[i][0] - size) for i in same)
        c = [i for i in same if abs(variants[i][0] - size) == best]
    return np.asarray(c)[pick % len(c)]


def tree_meshes(table):
    """(meshes the game draws for one tree, their triangles) of each table entry, as rows (data/tree_meshes.json; a
    variant it doesn't know, say after a game update: as one of its species in that state, else one mesh)"""
    global _meshes
    if _meshes is None:
        _meshes = json.loads(paths.resource('data', 'tree_meshes.json').read_text(encoding='utf-8'))
        _meshes['by_state'] = {'%s|%s' % tuple(k.split('|')[0:3:2]): r for k, r in _meshes['variants'].items()}
    v, by_state, d = _meshes['variants'], _meshes['by_state'], _meshes['default']
    rows = []
    for e in table:
        sp = e[0].replace('DA_TreeSpecies_', '')
        rows.append(v.get('%s|%d|%d|%s' % (sp, e[1], e[2], e[3])) or by_state.get('%s|%d' % (sp, e[2]), d))
    return np.array(rows, np.float64).reshape(-1, 2)


def _distance(clear, x, y, reach, cell=50.0):
    """distance (m, up to reach) from each tree (x, y in m) to the nearest cleared ground (clear: (bool raster, m per
    cell), row = y) on ~cell m squares"""
    cm, res = clear
    f = max(1, int(round(cell / res)))
    n0, n1 = cm.shape[0] // f, cm.shape[1] // f
    cur = cm[:n0 * f, :n1 * f].reshape(n0, f, n1, f).any(axis=(1, 3))
    step = res * f
    d = np.where(cur, 0.0, reach)
    for i in range(1, int(reach / step) + 1):              # one square further each step
        p = np.pad(cur, 1)
        grown = (p[1:-1, 1:-1] | p[:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, :-2] | p[1:-1, 2:]
                 | p[:-2, :-2] | p[:-2, 2:] | p[2:, :-2] | p[2:, 2:])
        d[grown & ~cur] = min(i * step, reach)
        cur = grown
    return d[np.clip((y / step).astype(np.int64), 0, n0 - 1), np.clip((x / step).astype(np.int64), 0, n1 - 1)]


def _thin_odds(x, y, cost, budget, clear):
    """odds of keeping each tree so the forest's instances (cost per tree) come to `budget`: within NEAR_M of cleared
    ground (lifts, trails) up to 1 / FAR_SHARE times the odds NEAR_FADE_M further out (and never more than 1); the
    same everywhere without the resort"""
    w = np.ones(len(x))
    if clear is not None and clear[0].any():
        w -= (1.0 - FAR_SHARE) * np.clip((_distance(clear, x, y, NEAR_M + NEAR_FADE_M) - NEAR_M) / NEAR_FADE_M, 0, 1)
    lo, hi = 0.0, 1.0 / w.min()
    for _ in range(30):                                    # the scale k of the odds min(1, k w) that fits the budget
        k = (lo + hi) / 2
        if float((np.minimum(1.0, k * w) * cost).sum()) > budget:
            hi = k
        else:
            lo = k
    return np.minimum(1.0, lo * w)


def forest(H, hmin, sp, sc, biome, treeline, valley, species, biome_species, seed, max_trees=DEFAULT_TREES,
           tree_slope=TREE_SLOPE, treeline_fade=0.0, clear=None, cover=None, lat=45.0, budget=TREE_BUDGET):
    """-> (record bytes, table bytes, header tail, {species: count})
    species: {short name: [(size, state, trunk)]}; biome_species: short names;
    max_trees: plant at most this many, evenly; cover: optional share (0..1 per vertex) of the forest density to keep
    (the real tree cover round a resort); lat: the map's latitude (where the sun stands); budget: the most tree
    instances (trees and canopies) to keep, thinning out round the resort (tree_budget; None: all)"""
    rng = np.random.default_rng(seed)
    spec = biomes.BIOMES[biome]
    hdr_tail = header_tail(spec['look'])
    if not spec['look'] or not biome_species:
        return b'', encode_table([]), hdr_tail, {}
    ok = growable(H, hmin, sp, sc, treeline, valley, tree_slope, treeline_fade, cover)
    x, y, z = plant(H, sp, sc, rng, ok, max_trees=max_trees, clear=clear)
    m = len(x)

    avail = [s for s in biome_species if s in species]
    if not avail:
        return b'', encode_table([]), hdr_tail, {}

    # height bands of this forest: each tree's yaw and scale like the game's trees of the same band and size (below)
    zl, zh = np.percentile(z, [1, 99]) if m else (0.0, 1.0)
    band = np.clip(((z - zl) / max(zh - zl, 1.0) * BANDS).astype(int), 0, BANDS - 1)
    stand = _patches(x, y, rng, STAND_M)
    n_st = int(stand.max()) + 1 if m else 1
    stand_pick = rng.integers(0, 1 << 30, n_st)
    if m:                                                  # a stand's mix follows its own height, not the contours
        band = np.rint(_per(stand, band)).astype(int)
        t = _relief(H, sp, sc, hmin, x, y, z + hmin, treeline)
        summit_st = rng.random(n_st)[stand] < np.clip((_per(stand, t) - SUMMIT_FROM) / (SUMMIT_TO - SUMMIT_FROM), 0.0, 1.0)
        summit = summit_st | (rng.random(m) < np.clip((t - SUMMIT_TO) / SUMMIT_FADE, 0.0, 1.0))   # and above it, all
    else:
        summit_st = summit = np.zeros(0, bool)
    rec = np.zeros((m, STRIDE), np.uint8)
    rec[:, 0:4] = np.rint(x * 1000).astype('<i4').view(np.uint8).reshape(-1, 4)
    rec[:, 4:8] = np.rint(y * 1000).astype('<i4').view(np.uint8).reshape(-1, 4)
    rec[:, 8:12] = np.rint(z * 1000).astype('<i4').view(np.uint8).reshape(-1, 4)
    # Species as the game's own maps of the biome have them (ecology, learned from them): each tree's odds of each
    # species by its stand's height in the forest and its spot's slope, sun, wetness and ridge. A stand has the species
    # whose odds in it reach PRESENT (summit stands: conifers only), so like the game's maps the common species are
    # nearly everywhere and the rare ones in patches; each healthy and in one size per stand, drawn as the game's maps
    # have that species at that height (dead trees and krummholz: LONE). Each tree takes one of its stand's
    # species by its odds (scaled so the biome's mix holds) times a smooth field per species, so they gather in clumps
    # like the game's.
    model = ecology.patterns()['biomes'].get(biome)
    names = [s for s in (model['species'] if model else avail) if s in avail] or avail
    S = len(names)
    E, cell = ecology.grid(H, sp, sc, hmin)
    nf = E.shape[0]
    ci = np.clip((y / cell).astype(np.int64), 0, nf - 1) * nf + np.clip((x / cell).astype(np.int64), 0, nf - 1)
    used, ci = np.unique(ci, return_inverse=True)                         # only the cells with trees
    ci = ci.ravel()
    if model:
        cols = [model['species'].index(s) for s in names]
        coef = np.asarray(model['coef'], np.float32)[:, cols]
        Fe = {k: v.ravel()[used] for k, v in ecology.features(E, cell, lat).items()}
        terr = ecology.design(np.zeros(len(used), np.int64), Fe['slope'], Fe['heat'], Fe['wet'], Fe['ridge'])
        cell_logit = terr[:, ecology.BANDS:] @ coef[ecology.BANDS:] + np.asarray(model['bias'], np.float32)[cols]
        band_logit = coef[:ecology.BANDS]
        size_cond = np.asarray(model['size_cond'], np.float64)[cols]       # (species, band, size * 4 + condition)
    else:                                                  # (every biome with trees has a model; just in case)
        cell_logit, band_logit = np.zeros((len(used), S), np.float32), np.zeros((BANDS, S), np.float32)
        size_cond = np.zeros((S, BANDS, 16))
        size_cond[:, :, 0::4] = 0.25                                       # healthy, every size alike
    prob = cell_logit[ci] + band_logit[np.clip(band, 0, BANDS - 1)]
    if m:
        prob -= prob.max(axis=1, keepdims=True)
    np.exp(prob, out=prob)
    prob /= np.maximum(prob.sum(axis=1, keepdims=True), 1e-30)
    high_ok = np.array([s in CONIFERS or s in SUMMIT_BROADLEAF for s in names])
    if not high_ok.any():
        high_ok[:] = True
    w_st = np.stack([_group_mean(stand, prob[:, k], n_st) for k in range(S)], 1)
    first = np.unique(stand, return_index=True)[1] if m else np.zeros(0, np.int64)
    st_high = np.zeros(n_st, bool)
    st_high[stand[first]] = summit_st[first]
    st_band = np.clip(np.rint(_group_mean(stand, band.astype(np.float64), n_st)).astype(int), 0, BANDS - 1)
    w_ok = w_st * np.where(st_high[:, None], high_ok[None, :], True)      # summit stands: conifers only
    present = w_ok >= PRESENT
    present[np.arange(n_st), np.argmax(w_ok, axis=1)] = True               # at least its likeliest species
    allowed = present[stand] & np.where(summit[:, None], high_ok[None, :], True)
    lost = ~allowed.any(axis=1)                                            # a summit tree in a stand with no conifer:
    allowed[lost] = high_ok[None, :]                                       # any conifer
    q = prob * allowed
    for k in range(S):                                                     # smooth clumps per species
        if m:
            q[:, k] *= np.exp(sum(2.0 * a * _noise(x, y, f, rng) for f, a in STAND_FIELD))
    # each species' odds scaled so the trees that may be any species (below the summits) keep the biome's mix
    boost = np.ones(S, np.float32)
    free = np.nonzero(~summit)[0]
    if len(free):
        free = free[rng.random(len(free)) < 200000.0 / len(free)]           # a sample is plenty
        q_free, target = q[free], prob[free].mean(axis=0)
        for _ in range(15):
            r = q_free * boost
            got = (r / np.maximum(r.sum(axis=1, keepdims=True), 1e-30)).mean(axis=0)
            boost *= np.where(got > 0, np.clip(target / np.maximum(got, 1e-9), 0.5, 2.0), 1.0).astype(np.float32)
    q *= boost
    pick = _draw(q, rng) if m else np.zeros(0, np.int64)
    del q
    sc4 = size_cond[np.arange(S)[None, :], st_band[:, None]].reshape(n_st, S, 4, 4)     # stand, species, size, state
    look = sc4.copy()
    look[..., 1:] = 0.0                                                    # a species' look in a stand: healthy
    sc_pick = _draw(look.reshape(-1, 16), rng).reshape(n_st, S)
    size, state = (sc_pick // 4)[stand, pick], (sc_pick % 4)[stand, pick]
    odds = sc4.sum(axis=2) / np.maximum(sc4.sum(axis=(2, 3)), 1e-12)[..., None]          # stand, species, state
    u = rng.random(m)
    lone = []
    for st, share in LONE:                                                 # each tree in one of them at most
        p = share * odds[stand, pick, st]
        lone.append((u >= 0) & (u < p))
        u -= p
    del u, p
    vpick = stand_pick[stand]                                              # which of a look's meshes
    if m:
        block = LONE_BLOCK * SQUARE_M
        nb = int((G - 1) * sp // block) + 1
        grp = ((x // block).astype(np.int64) * nb + (y // block).astype(np.int64)) * 2 + summit   # (summit: conifers)
        for (st, _), flag in zip(LONE, lone):                              # single trees, one kind per block
            rep = _one_of(flag, grp, 2 * nb * nb, rng)
            rep_size = _draw(sc4[stand[rep], pick[rep], :, st], rng)         # (rows of blocks without any: unused)
            r = rep[grp[flag]]
            pick[flag], size[flag], state[flag], vpick[flag] = pick[r], rep_size[grp[flag]], st, vpick[r]
    kind = lambda n, t: tuple(species[n][_pick_variant(species[n], 0, t, np.zeros(1, np.int64))[0]])  # as drawn below
    per_kind = tree_meshes([('DA_TreeSpecies_' + n,) + kind(n, t) for n in names for t in range(4)])[:, 0]
    cost = per_kind.reshape(S, 4)[pick, state]             # instances the game draws per tree (alike at every size)
    if budget is not None and float(cost.sum()) > budget:
        odds = _thin_odds(x, y, cost, budget, clear)
        keep = rng.random(m) < odds
        kept = np.nonzero(keep)[0]                         # never over it (the game drops every tree past 1.5M):
        keep[kept[np.cumsum(cost[kept]) > budget]] = False  # the last few chosen go (trees are in random order)
        top = np.array([max([v[0] for v in species[n] if v[1] == 0] or [0]) for n in names])[pick]
        area = ((size + 2.0) / (size + 1.0)) ** 2         # a crown one size up over this one
        grow = np.clip((1.0 / np.maximum(_per(stand, odds), 1e-6) - 1.0) / (area - 1.0), 0.0, 1.0)
        grow = (state == 0) & (size < top) & (rng.random((n_st, S))[stand, pick] < grow)    # per stand and species
        log.info('forest: %d tree instances over the budget of %d: %d of %d trees kept, %d of them a size larger',
                 cost.sum(), budget, int(keep.sum()), m, int((grow & keep).sum()))
        size = size + grow
        x, y, rec, pick, size, state, vpick, band = (a[keep] for a in (x, y, rec, pick, size, state, vpick, band))
        m = len(x)
    tgt_sp = np.asarray(names, object)[pick]
    # yaw (any way round, as on the game's maps) and scale (width = height) like the game's trees of that height band
    # and size (forest_stats.json: a histogram of their scales)
    hist = np.asarray(stats()['looks'][spec['look']]['scale'], np.float64)        # band, size, bin
    bins = hist.shape[2]
    rec[:, 12:14] = rng.integers(0, 65536, m, dtype=np.uint16).astype('<u2').view(np.uint8).reshape(-1, 2)
    for b in range(BANDS):
        in_band = band == b
        for s in range(4):
            sel = np.nonzero(in_band & (size == s))[0]
            if not len(sel):
                continue
            p = hist[b, s] if hist[b, s].sum() else hist[:, s].sum(axis=0)
            p = p if p.sum() else np.ones(bins)
            u = (rng.choice(bins, len(sel), p=p / p.sum()) + rng.random(len(sel))) * (65536.0 / bins)
            wh = np.minimum(u, 65535).astype('<u2').view(np.uint8).reshape(-1, 2)
            rec[sel, 14:16] = wh
            rec[sel, 16:18] = wh

    table, index = [], {}
    var = np.zeros(m, np.uint16)
    for sp_name in avail:
        vs = species[sp_name]
        mine = tgt_sp == sp_name
        if not mine.any():
            continue
        for s in range(4):
            for t in range(4):
                sel = np.nonzero(mine & (size == s) & (state == t))[0]
                if not len(sel):
                    continue
                picks = _pick_variant(vs, s, t, vpick[sel])
                for vi in np.unique(picks):
                    key = (sp_name,) + tuple(vs[vi])
                    j = index.get(key)
                    if j is None:
                        j = index[key] = len(table)
                        table.append(('DA_TreeSpecies_' + sp_name,) + key[1:])
                    var[sel[picks == vi]] = j
    rec[:, 18:20] = var.astype('<u2').view(np.uint8).reshape(-1, 2)
    counts = {}
    for e, n in zip(table, np.bincount(var, minlength=len(table))):
        if n:
            counts[e[0].replace('DA_TreeSpecies_', '')] = counts.get(e[0].replace('DA_TreeSpecies_', ''), 0) + int(n)
    log.info('forest: %d trees, %s', len(var),
             ', '.join('%s %d' % kv for kv in sorted(counts.items(), key=lambda kv: -kv[1])))
    return rec.tobytes(), encode_table(table), hdr_tail, counts
