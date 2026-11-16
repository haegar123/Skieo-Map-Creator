"""Offline checks of the USGS elevation georeferencing (dem._georef) and of the local tiles of Sonny's DTM Europe
(finding them in folders and zips, joining them across tile edges, filling gaps): usage: python tests/test_dem.py"""
import io
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from skieo_creator import dem  # noqa: E402


def geotiff(west, north, dx, dy, shape=(4, 6), tie=(0, 0)):
    buf = io.BytesIO()
    i, j = tie
    tifffile.imwrite(buf, np.zeros(shape, np.float32), extratags=[
        (33550, 'd', 3, (dx, dy, 0.0), False),                                   # ModelPixelScale
        (33922, 'd', 6, (i, j, 0.0, west + i * dx, north - j * dy, 0.0), False)])  # ModelTiepoint
    return buf.getvalue()


# the box the server really returned, not the one asked for (it widens the latitude span by default)
w, n, dx, dy = dem._georef(geotiff(-72.84, 43.625, 0.0002, 0.0002))
assert (round(w, 6), round(n, 6), dx, dy) == (-72.84, 43.625, 0.0002, 0.0002), (w, n, dx, dy)
# a tie point that is not the top-left pixel still gives the top-left corner
w, n, dx, dy = dem._georef(geotiff(-72.84, 43.625, 0.0002, 0.0002, tie=(3, 2)))
assert (round(w, 6), round(n, 6)) == (-72.84, 43.625), (w, n)
# no georeferencing: None (the caller falls back to the requested box)
buf = io.BytesIO()
tifffile.imwrite(buf, np.zeros((2, 2), np.float32))
assert dem._georef(buf.getvalue()) is None
assert dem.SOURCES['3dep2']['name'] == dem.SOURCES['3dep']['name']


# ---------------------------------------------------------------- Sonny's tiles, made up
# heights on a tilted plane over 46..48 N, 9..11 E: 1 m per arc second northwards, 2 m eastwards. Cubic sampling keeps
# a plane as it is, so wherever a window lands its heights must be the plane: edges, offsets and directions all at once.

def plane(lat, lon):
    return 1000.0 + (lat - 46) * 3600 + 2 * (lon - 9) * 3600


def hgt(la, lo, n=dem.HGT_N, hole=None):
    i, j = np.mgrid[0:n, 0:n]
    a = ((la + 1 - 46) * 3600 - i) + 2 * ((lo - 9) * 3600 + j) + 1000
    if hole:
        a[hole] = dem.HGT_NODATA
    return a.astype('>i2').tobytes()


def zipped(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        for name, data in entries:
            z.writestr(name, data)
    return buf.getvalue()


def fake_terrarium(lat, lon, size_m, cancel, progress):
    LA, LO = dem.grid_latlon(lat, lon, size_m)
    return plane(LA, LO) + 100.0                       # 100 m off, to see where it was used


tmp = Path(tempfile.mkdtemp(prefix='skieo_test_dem_'))
dem.paths.DEMS = tmp / 'cache'
dem._fetch_terrarium = fake_terrarium
full, part = tmp / 'full', tmp / 'part'
(full / 'loose').mkdir(parents=True)
part.mkdir()
(full / 'loose' / 'N46E009.hgt').write_bytes(hgt(46, 9))                                      # a loose tile
(full / 'N47E009.zip').write_bytes(zipped([('N47E009.hgt', hgt(47, 9))]))                     # a tile's own zip
(full / 'aaa 3asec.zip').write_bytes(zipped([('N47E010.zip', zipped([('N47E010.hgt', b'\0' * 2884802)]))]))  # 3"
(full / 'big.zip').write_bytes(zipped([('DTM/N46E010.zip', zipped([('N46E010.hgt', hgt(46, 10))])),    # zips in
                                       ('DTM/N47E010.zip', zipped([('N47E010.hgt', hgt(47, 10))])),    # a zip
                                       ('DTM/_Readme.txt', b'hi')]))
(full / 'empty.zip').write_bytes(b'')                                                          # broken: left out
(full / 'junk.zip').write_bytes(b'not a zip')
(full / 'big.zip.part').write_bytes(b'PK')

idx = dem.sonny_index(full)
assert sorted(idx) == ['N46E009', 'N46E010', 'N47E009', 'N47E010'], sorted(idx)
assert idx['N46E009'] == [(str(full / 'loose' / 'N46E009.hgt'), None)]
assert idx['N47E010'] == [(str(full / 'big.zip'), 'DTM/N47E010.zip')]               # not the 3" tile of that name
three = (str(full / 'aaa 3asec.zip'), 'N47E010.zip')       # and one that slips through is passed over on reading
assert dem._sonny_tile([three] + idx['N47E010'])[0, 0] == plane(48, 10)
assert dem.sonny_index(None) == {} and dem.sonny_index(tmp / 'nothing') == {}
assert dem.tile_name(-1, -1) == 'S01W001' and dem.tile_name(47, 9) == 'N47E009'

# a window on the corner of all four tiles: Sonny's heights only, the plane everywhere (inside the smoothing's edge)
LA, LO = dem.grid_latlon(47.0, 10.0, 5000.0)
inner = (slice(40, -40), slice(40, -40))
d, src = dem.load(47.0, 10.0, 5000.0, folder=full)
assert src == 'sonny', src
assert np.abs(d - plane(LA, LO))[inner].max() < 0.05, np.abs(d - plane(LA, LO))[inner].max()
assert dem.load(47.0, 10.0, 5000.0, folder=full)[1] == 'sonny'                      # from the cache

# only the south-west tile, with a hole: the rest and the hole from Terrain Tiles, Sonny's heights round them
row, col = np.unravel_index(np.argmin(np.abs(LA - 46.99) + np.abs(LO - 9.99)), LA.shape)    # hole round 46.99 N 9.99 E
(part / 'N46E009.hgt').write_bytes(hgt(46, 9, hole=(slice(30, 42), slice(3558, 3570))))
d, src = dem.load(47.0, 10.0, 5000.0, folder=part)
assert src == 'sonny+terrarium', src
off = d - plane(LA, LO)
assert abs(off[row, col] - 100) < 0.5, off[row, col]                                # the hole: Terrain Tiles
assert abs(off[400, 400] - 100) < 0.5 and abs(off[1600, 1600] - 100) < 0.5          # the missing tiles: same
assert abs(off[1600, 400]) < 0.05 and abs(off[1900, 200]) < 0.05, (off[1600, 400], off[1900, 200])  # Sonny's tile
assert ((off > -0.5) & (off < 100.5))[inner].all()                                  # the blend stays between them
# the tile added later: made again from the tiles, not from the cache of the window without them
(part / 'N47E010.hgt').write_bytes(hgt(47, 10))
assert dem.sonny_tiles(47.0, 10.0, 5000.0, dem.sonny_index(part)) == ['N46E009', 'N47E010']
d, src = dem.load(47.0, 10.0, 5000.0, folder=part)
assert src == 'sonny+terrarium' and abs(d[400, 1600] - plane(LA, LO)[400, 1600]) < 0.05

# no folder, or a map made from tiles that are gone: Terrain Tiles as before
assert dem.load(47.0, 10.0, 5000.0)[1] == 'terrarium'
assert dem.load(47.0, 10.0, 5000.0, prefer='sonny', folder=tmp / 'nothing')[1] == 'terrarium'
assert dem.load(47.0, 10.0, 5000.0, prefer='terrarium', folder=full)[1] == 'terrarium'   # old maps keep their source
shutil.rmtree(tmp, ignore_errors=True)
print('dem: all checks passed')
