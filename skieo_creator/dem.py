"""Elevation for a square terrain window, sampled onto the game's 2017 x 2017 landscape grid (row 0 = north).

Sources:
  * Sonny's DTM Europe 1" (LiDAR terrain models, CC BY 4.0) from a folder on this PC, when the player has set one
    and it has tiles under the window (the gaps filled from Terrain Tiles)
  * USGS 3DEP 1/3 arc-second (~10 m) inside the United States, via the public 3DEPElevation ImageServer
  * AWS Terrain Tiles (terrarium PNG, open data, global) everywhere else, and as the fallback
All are resampled with cubic convolution onto a local transverse Mercator grid centred on the window (see geo.py).
"""
import hashlib
import io
import logging
import math
import re
import struct
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

from . import geo, net, paths

log = logging.getLogger(__name__)
G = 2017
CACHE_VERSION = 2
TDEP_URL = ('https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage?'
            'bbox={w:.7f},{s:.7f},{e:.7f},{n:.7f}&bboxSR=4326&imageSR=4326&size={nx},{ny}&format=tiff&pixelType=F32'
            '&noData=-9999&interpolation=RSP_BilinearInterpolation&f=image')
TERRARIUM_URL = 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'
# '3dep' (until 1.7.0) read the server's image as if it covered exactly the requested box, but exportImage keeps its
# pixels square in degrees and widens the latitude span instead (by 1/cos(lat), ~38% at 44N): the terrain came out
# squeezed north-south towards the map centre. '3dep2' asks for the exact box and maps the image by its own
# georeferencing. New maps use '3dep2'; maps made with '3dep' keep it on Rebuild, so the terrain under their resorts
# stays where it was (2026-11-14, found from real lifts landing off their ridges at Killington).
SOURCES = {
    '3dep2': dict(name='USGS 3DEP (10 m)', credit='Elevation: USGS 3D Elevation Program (public domain)'),
    '3dep': dict(name='USGS 3DEP (10 m)', credit='Elevation: USGS 3D Elevation Program (public domain)'),
    'terrarium': dict(name='Terrain Tiles (~30 m)', credit='Elevation: Terrain Tiles on AWS (Mapzen; SRTM, GMTED, ETOPO1, 3DEP and others)'),
}
SONNY_CREDIT = 'Elevation: DTM Europe 1" v25 by Sonny (sonny.4lima.de), CC BY 4.0, resampled'
SOURCES['sonny'] = dict(name='Sonny LiDAR DTM (1")', credit=SONNY_CREDIT)
SOURCES['sonny+terrarium'] = dict(name='Sonny LiDAR DTM + Terrain Tiles',
                                  credit=SONNY_CREDIT + '; gaps: ' + SOURCES['terrarium']['credit'][len('Elevation: '):])
# rough boxes of 3DEP coverage (lon_min, lat_min, lon_max, lat_max): CONUS, Alaska, Hawaii, Puerto Rico
US_BOXES = ((-125.0, 24.4, -66.8, 49.5), (-170.0, 51.0, -129.5, 71.6), (-160.5, 18.8, -154.6, 22.4), (-67.4, 17.8, -65.2, 18.6))


class DemError(Exception):
    pass


def in_us(lat, lon):
    return any(a <= lon <= c and b <= lat <= d for a, b, c, d in US_BOXES)


def grid_latlon(lat, lon, size_m):
    g = geo.LocalGrid(lat, lon)
    s = np.arange(G) * (size_m / (G - 1)) - size_m / 2
    X, Y = np.meshgrid(s, -s)
    return g.to_latlon(X, Y)


# ---------------------------------------------------------------- resampling / smoothing without scipy

def _keys(t):
    """cubic convolution weights (a = -0.5) for the four taps at offsets -1, 0, 1, 2"""
    a = -0.5
    t2, t3 = t * t, t * t * t
    return ((a * (t3 - 2 * t2 + t)), ((a + 2) * t3 - (a + 3) * t2 + 1), (-(a + 2) * t3 + (2 * a + 3) * t2 - a * t),
            (a * (t2 - t3)))


def sample_cubic(img, rows, cols):
    """img[rows, cols] by cubic convolution at fractional pixel-centre coordinates (edge clamped)"""
    h, w = img.shape
    out = np.empty(rows.shape, np.float64)
    step = 256
    for a in range(0, rows.shape[0], step):
        r, c = rows[a:a + step], cols[a:a + step]
        r0, c0 = np.floor(r).astype(np.int64), np.floor(c).astype(np.int64)
        wr, wc = _keys(r - r0), _keys(c - c0)
        acc = np.zeros(r.shape, np.float64)
        for i in range(4):
            ri = np.clip(r0 + i - 1, 0, h - 1)
            row_acc = np.zeros(r.shape, np.float64)
            for j in range(4):
                row_acc += wc[j] * img[ri, np.clip(c0 + j - 1, 0, w - 1)]
            acc += wr[i] * row_acc
        out[a:a + step] = acc
    return out


def gaussian(a, sigma):
    if sigma <= 0:
        return a
    rad = int(math.ceil(4 * sigma))
    k = np.exp(-0.5 * (np.arange(-rad, rad + 1) / sigma) ** 2)
    k /= k.sum()
    out = a.astype(np.float64)
    for axis in (0, 1):
        p = np.pad(out, [(rad, rad) if ax == axis else (0, 0) for ax in (0, 1)], mode='edge')
        acc = np.zeros_like(out)
        n = out.shape[axis]
        for i, kv in enumerate(k):
            acc += kv * (p[i:i + n, :] if axis == 0 else p[:, i:i + n])
        out = acc
    return out


# ---------------------------------------------------------------- sources

def _georef(data):
    """(west, north, degrees per pixel x, degrees per pixel y) from a GeoTIFF's ModelTiepoint / ModelPixelScale, or
    None when the tags are missing"""
    import tifffile
    with tifffile.TiffFile(io.BytesIO(data)) as t:
        tags = t.pages[0].tags
        sc, tp = tags.get('ModelPixelScaleTag'), tags.get('ModelTiepointTag')
        if not (sc and tp):
            return None
        i, j, x, y = tp.value[0], tp.value[1], tp.value[3], tp.value[4]      # pixel (i, j) corner at (x, y)
        return x - i * sc.value[0], y + j * sc.value[1], sc.value[0], sc.value[1]


def _fetch_3dep(lat, lon, size_m, cancel, progress, exact=True):
    """exact: the image's real extent (see SOURCES); False reproduces the old, squeezed terrain of '3dep' maps"""
    import tifffile
    LA, LO = grid_latlon(lat, lon, size_m)
    pad_lat = 3 * size_m / (G - 1) / 111320.0
    pad_lon = pad_lat / max(math.cos(math.radians(lat)), 0.05)
    s, n = LA.min() - pad_lat, LA.max() + pad_lat
    w, e = LO.min() - pad_lon, LO.max() + pad_lon
    spacing = size_m / (G - 1)
    ny = int(min(4000, max(256, math.ceil((n - s) * 111320.0 / spacing * 1.1))))
    nx = int(min(4000, max(256, math.ceil((e - w) * 111320.0 * math.cos(math.radians(lat)) / spacing * 1.1))))
    progress(0.1, 'Downloading USGS elevation data...')
    url = TDEP_URL.format(w=w, s=s, e=e, n=n, nx=nx, ny=ny) + ('&adjustAspectRatio=false' if exact else '')
    data = net.get(url, timeout=300, retries=3, cancel=cancel)
    if data[:2] not in (b'II', b'MM'):
        raise DemError('USGS returned no elevation image (%r)' % data[:80])
    img = tifffile.imread(io.BytesIO(data)).astype(np.float64)
    if img.ndim != 2:
        img = img[..., 0]
    if (img < -1000).any() or not np.isfinite(img).all():
        raise DemError('USGS has no elevation for part of this area')
    progress(0.7, 'Resampling elevation...')
    ph, pw = img.shape
    ref = _georef(data) if exact else None
    if ref:                                          # where the image really is (pixel centres at +0.5)
        west, north, dx, dy = ref
        rows = (north - LA) / dy - 0.5
        cols = (LO - west) / dx - 0.5
    else:
        rows = (n - LA) / (n - s) * ph - 0.5
        cols = (LO - w) / (e - w) * pw - 0.5
    return sample_cubic(img, rows, cols)


def _lonlat_to_px(lon, lat, z):
    n = 256 * 2 ** z
    x = (lon + 180.0) / 360.0 * n
    s = np.sin(np.radians(np.clip(lat, -85.05, 85.05)))
    y = (0.5 - np.log((1 + s) / (1 - s)) / (4 * np.pi)) * n
    return x, y


def _terrarium_tile(z, x, y, cancel):
    x %= 2 ** z
    f = paths.DEMS / 'terrarium' / str(z) / str(x) / ('%d.png' % y)
    data = None
    if f.is_file():
        data = f.read_bytes()
    if not data:
        data = net.get(TERRARIUM_URL.format(z=z, x=x, y=y), timeout=60, retries=4, cancel=cancel, per_host=6)
        paths.atomic_write(f, data)
    a = np.asarray(Image.open(io.BytesIO(data)).convert('RGB')).astype(np.float64)
    return a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256 - 32768


def _fetch_terrarium(lat, lon, size_m, cancel, progress):
    LA, LO = grid_latlon(lat, lon, size_m)
    if abs(lat) > 84:
        raise DemError('Elevation tiles do not cover the poles')
    z = 13
    px, py = _lonlat_to_px(LO, LA, z)
    x0, x1 = int(px.min()) // 256 - 1, int(px.max()) // 256 + 1
    y0, y1 = int(py.min()) // 256 - 1, int(py.max()) // 256 + 1
    tiles = [(tx, ty) for ty in range(y0, y1 + 1) for tx in range(x0, x1 + 1)]
    mosaic = np.zeros(((y1 - y0 + 1) * 256, (x1 - x0 + 1) * 256), np.float64)
    done = [0]

    def one(t):
        tx, ty = t
        tile = _terrarium_tile(z, tx, ty, cancel)
        mosaic[(ty - y0) * 256:(ty - y0 + 1) * 256, (tx - x0) * 256:(tx - x0 + 1) * 256] = tile
        done[0] += 1
        progress(0.05 + 0.6 * done[0] / len(tiles), 'Downloading elevation tiles (%d of %d)...' % (done[0], len(tiles)))

    with ThreadPoolExecutor(6) as ex:
        list(ex.map(one, tiles))
    progress(0.7, 'Resampling elevation...')
    dem = sample_cubic(mosaic, py - y0 * 256 - 0.5, px - x0 * 256 - 0.5)
    # coarse source (~30 m outside the US): smooth the stair steps of the 16-bit tiles; stronger on small windows
    sigma = float(np.clip(3.0 * math.sqrt(8000.0 / size_m), 2.0, 5.0))
    return gaussian(dem, sigma)


# ---------------------------------------------------------------- Sonny's DTM Europe 1" (local .hgt tiles)
# SRTM .hgt: 1 x 1 degree named by its south-west corner, 3601 x 3601 big-endian int16 metres, row 0 = north edge,
# a sample on every whole arc second (neighbouring tiles share their edge row / column), -32768 = no data.

HGT_N = 3601
HGT_BYTES = HGT_N * HGT_N * 2                      # a 3" tile of the same name (1201 x 1201) is not one of these
HGT_NODATA = -32768
TILE_NAME = re.compile(r'([NS])(\d\d)([EW])(\d{3})\.(hgt|zip)$', re.I)
SONNY_SMOOTH_M = 8.0     # ponytail: light smoothing of the whole-metre steps; tune on real maps
SONNY_FEATHER = 3.0      # arc seconds (~90 m): the blend into Terrain Tiles round a gap


def tile_name(lat0, lon0):
    return '%s%02d%s%03d' % ('N' if lat0 >= 0 else 'S', abs(lat0), 'E' if lon0 >= 0 else 'W', abs(lon0))


def _tile_of(path):
    m = TILE_NAME.search(path.replace('\\', '/').rsplit('/', 1)[-1])
    return m and (m.group(1) + m.group(2) + m.group(3) + m.group(4)).upper()


def _inner_is_1s(z, info):
    """a tile's zip inside a bigger zip: from its first local header (a few bytes to inflate, not the tile), is it a
    1" tile? A header without the size (data descriptor) is checked on reading."""
    with z.open(info) as s:
        h = s.read(30)
    if len(h) < 30 or h[:4] != b'PK\x03\x04':
        return False
    flags, size = struct.unpack_from('<H', h, 6)[0], struct.unpack_from('<I', h, 22)[0]
    return bool(flags & 8) or size == HGT_BYTES


def sonny_index(folder):
    """{'N47E009': [(path, member), ...]} of the 1" tiles in this folder and its subfolders, best first: a loose .hgt
    (member None), a tile's own zip (member None), then a tile (.hgt or its zip) inside a bigger zip. Broken files and
    tiles that are not 1" (Sonny's 3" tiles have the same names) are left out."""
    idx = {}
    root = Path(folder) if folder else None
    if not root or not root.is_dir():
        return idx
    for f in sorted(root.rglob('*')):
        suffix = f.suffix.lower()
        if suffix not in ('.hgt', '.zip') or not f.is_file():
            continue
        name = _tile_of(f.name)
        if suffix == '.hgt':
            if name and f.stat().st_size == HGT_BYTES:
                idx.setdefault(name, []).append((0, str(f), None))
            continue
        try:
            with zipfile.ZipFile(f) as z:
                if name:
                    if [i.file_size for i in z.infolist() if i.filename.lower().endswith('.hgt')] == [HGT_BYTES]:
                        idx.setdefault(name, []).append((1, str(f), None))
                    continue
                for i in z.infolist():
                    t = _tile_of(i.filename)
                    if t and (i.file_size == HGT_BYTES if i.filename.lower().endswith('.hgt') else _inner_is_1s(z, i)):
                        idx.setdefault(t, []).append((2, str(f), i.filename))
        except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as e:
            log.warning('elevation folder: skipping %s (%s)', f.name, e)
    return {k: [(p, m) for _, p, m in sorted(v)] for k, v in idx.items()}


def _hgt_from_zip(z):
    hgts = [i for i in z.infolist() if i.filename.lower().endswith('.hgt')]
    if len(hgts) != 1 or hgts[0].file_size != HGT_BYTES:
        raise ValueError('no 1" .hgt inside')
    return z.read(hgts[0])


def _read_tile(path, member):
    if member is None and path.lower().endswith('.hgt'):
        data = Path(path).read_bytes()
    else:
        with zipfile.ZipFile(path) as z:
            if member is None:
                data = _hgt_from_zip(z)
            elif member.lower().endswith('.hgt'):
                data = z.read(member)
            else:
                with zipfile.ZipFile(io.BytesIO(z.read(member))) as inner:
                    data = _hgt_from_zip(inner)
    if len(data) != HGT_BYTES:
        raise ValueError('not a 1" tile')
    return np.frombuffer(data, '>i2').reshape(HGT_N, HGT_N)


def _sonny_tile(where):
    """the tile from the first of its copies that reads, or None"""
    for path, member in where:
        try:
            return _read_tile(path, member)
        except (OSError, ValueError, zipfile.BadZipFile) as e:
            log.warning('elevation tile %s%s unreadable: %s', path, ' > ' + member if member else '', e)
    return None


def _window_box(lat, lon, size_m):
    """the grid's lat / lon and the whole arc seconds round it (north, south, west, east; 3 more each side)"""
    LA, LO = grid_latlon(lat, lon, size_m)
    N, S = math.ceil(LA.max() * 3600) + 3, math.floor(LA.min() * 3600) - 3
    W, E = math.floor(LO.min() * 3600) - 3, math.ceil(LO.max() * 3600) + 3
    return LA, LO, (N, S, W, E)


def _box_tiles(box):
    N, S, W, E = box
    return [(la, lo) for la in range(S // 3600, (N - 1) // 3600 + 1) for lo in range(W // 3600, (E - 1) // 3600 + 1)]


def sonny_tiles(lat, lon, size_m, index):
    """names of the window's tiles that the folder has (1, 2 or 4 tiles touch a window up to 20 km)"""
    return [tile_name(la, lo) for la, lo in _box_tiles(_window_box(lat, lon, size_m)[2]) if tile_name(la, lo) in index]


def _fetch_sonny(lat, lon, size_m, index, cancel, progress):
    """-> (dem, source id): the tiles' heights, with Terrain Tiles where a tile is missing or has no data"""
    LA, LO, box = _window_box(lat, lon, size_m)
    N, S, W, E = box
    mosaic = np.full((N - S + 1, E - W + 1), np.nan)        # mosaic[i, j] at (N - i) / 3600 N, (W + j) / 3600 E
    tiles = _box_tiles(box)
    for k, (la, lo) in enumerate(tiles):
        name = tile_name(la, lo)
        progress(0.05 + 0.5 * k / len(tiles), 'Reading local elevation tile %s...' % name)
        cancel.check()
        tile = _sonny_tile(index[name]) if name in index else None
        if tile is None:
            continue
        top, left = (la + 1) * 3600, lo * 3600                # arc seconds of the tile's row 0 / column 0
        hi, lo_ = min(N, top), max(S, la * 3600)
        l_, r_ = max(W, left), min(E, left + 3600)
        part = tile[top - hi:top - lo_ + 1, l_ - left:r_ - left + 1].astype(np.float64)
        part[part == HGT_NODATA] = np.nan
        mosaic[N - hi:N - lo_ + 1, l_ - W:r_ - W + 1] = part
    valid_m = np.isfinite(mosaic)
    if not valid_m.any():
        raise DemError('The local elevation tiles for this area could not be read')
    progress(0.6, 'Resampling elevation...')
    rows, cols = N - LA * 3600, LO * 3600 - W                 # samples sit on whole arc seconds: no half-pixel shift
    dem = sample_cubic(mosaic, rows, cols)                   # NaN wherever a tap had no data
    sp = size_m / (G - 1)
    src = 'sonny'
    bad = ~np.isfinite(dem)
    if bad.any():
        log.info('local elevation: %.1f%% of the window has no tile data, filling from Terrain Tiles', 100 * bad.mean())
        fill = _fetch_terrarium(lat, lon, size_m, cancel, lambda f, t: progress(0.6 + 0.3 * f, t))
        w_m = np.clip(2 * gaussian(valid_m.astype(np.float64), SONNY_FEATHER) - 1, 0, 1) * valid_m
        w = np.where(bad, 0.0, np.clip(sample_cubic(w_m, rows, cols), 0, 1))
        dem = w * np.where(bad, 0.0, dem) + (1 - w) * fill
        src = 'sonny+terrarium'
    return gaussian(dem, float(np.clip(SONNY_SMOOTH_M / sp, 1.0, 5.0))), src


# ---------------------------------------------------------------- entry point

def cache_key(lat, lon, size_m, source):
    s = '%d|%.6f|%.6f|%.1f|%s' % (CACHE_VERSION, lat, lon, size_m, source)
    return hashlib.sha1(s.encode()).hexdigest()[:20]


def load(lat, lon, size_m, cancel=net.NEVER, progress=lambda f, t: None, prefer=None, folder=None):
    """-> (dem metres float32 (G, G), source id). Cached per window. folder: Sonny's tiles, used first where it has
    them (also when rebuilding a map made from them; without them it falls back to Terrain Tiles)."""
    index = sonny_index(folder)
    local = sonny_tiles(lat, lon, size_m, index) if index else []
    if prefer in ('sonny', 'sonny+terrarium'):
        if not local:
            log.warning('this map was made from local elevation tiles that are not there now: using Terrain Tiles')
        order = ['sonny', 'terrarium'] if local else ['terrarium']
    elif prefer:
        order = [prefer]
    else:
        order = (['sonny'] if local else []) + (['3dep2', 'terrarium'] if in_us(lat, lon) else ['terrarium'])
    errors = []
    for src in order:
        # Sonny's windows are cached by the tiles they had, so adding the missing one makes them again
        keys = {s: cache_key(lat, lon, size_m, s + ':' + ','.join(local)) for s in ('sonny', 'sonny+terrarium')} \
            if src == 'sonny' else {src: cache_key(lat, lon, size_m, src)}
        for s, k in keys.items():
            f = paths.DEMS / ('%s.npy' % k)
            if f.is_file():
                try:
                    dem = np.load(f)
                    if dem.shape == (G, G):
                        f.touch()
                        progress(1.0, 'Elevation loaded from cache')
                        return dem, s
                except (OSError, ValueError):
                    pass
        t = time.time()
        try:
            if src in ('3dep', '3dep2'):
                dem = _fetch_3dep(lat, lon, size_m, cancel, progress, exact=src == '3dep2')
            elif src == 'sonny':
                dem, src = _fetch_sonny(lat, lon, size_m, index, cancel, progress)
            else:
                dem = _fetch_terrarium(lat, lon, size_m, cancel, progress)
        except (DemError, net.NetError) as e:
            log.warning('%s failed: %s', src, e)
            errors.append(str(e))
            continue
        dem = np.maximum(dem, 0.0).astype(np.float32)          # ocean floor (terrarium bathymetry) -> sea level
        buf = io.BytesIO()
        np.save(buf, dem)
        f = paths.DEMS / ('%s.npy' % keys[src])
        paths.atomic_write(f, buf.getvalue())
        _trim_cache()
        log.info('elevation %s for %.5f,%.5f %.0f m in %.1f s: %.0f..%.0f m', src, lat, lon, size_m, time.time() - t,
                 dem.min(), dem.max())
        progress(1.0, 'Elevation ready')
        return dem, src
    raise DemError(errors[-1] if errors else 'No elevation source covers this area')


def _trim_cache(keep=24):
    files = sorted(paths.DEMS.glob('*.npy'), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files[keep:]:
        try:
            f.unlink()
        except OSError:
            pass
