"""Export regions x genes as an AnnData .h5ad -- without shipping anndata.

anndata pulls pandas and scipy: ~200 MB of packaged weight to write one file.
The on-disk format is just HDF5 with a documented schema (anndata 0.8+,
encoding-version 0.1.0), so this writes it directly with h5py and the result
opens in scanpy/anndata unchanged. The test suite round-trips it through the
real `anndata` package to keep this honest.

The matrix: X[region, gene] = the sum of the transcript-density grid inside
the region's polygon(s) -- the same /density/gene 10 um grid the viewer draws,
so what you see is literally what you export.
"""
from __future__ import annotations

import numpy as np


def _str_ds(grp, name, values):
    import h5py
    ds = grp.create_dataset(name, data=np.array([str(v) for v in values],
                                                dtype=h5py.string_dtype()))
    ds.attrs["encoding-type"] = "string-array"
    ds.attrs["encoding-version"] = "0.2.0"
    return ds


def _num_ds(grp, name, values, dtype=None):
    ds = grp.create_dataset(name, data=np.asarray(values, dtype=dtype))
    ds.attrs["encoding-type"] = "array"
    ds.attrs["encoding-version"] = "0.2.0"
    return ds


def _dataframe(parent, name, index_name, index, columns):
    """columns: list of (colname, values, kind) with kind 'str'|'num'."""
    import h5py
    g = parent.create_group(name)
    g.attrs["encoding-type"] = "dataframe"
    g.attrs["encoding-version"] = "0.2.0"
    g.attrs["_index"] = index_name
    # explicit string dtype: an EMPTY object array (a frame with no columns,
    # like var) has no native HDF5 type and h5py refuses it
    g.attrs.create("column-order", np.array([c[0] for c in columns],
                                            dtype=h5py.string_dtype()))
    _str_ds(g, index_name, index)
    for col, values, kind in columns:
        (_str_ds if kind == "str" else _num_ds)(g, col, values)
    return g


def write_h5ad(path, X, obs_index, obs_cols, var_index, obsm=None, uns=None):
    """Write a minimal, valid .h5ad. X: (n_obs, n_var) array-like."""
    import h5py

    X = np.asarray(X, dtype=np.float32)
    with h5py.File(path, "w") as f:
        f.attrs["encoding-type"] = "anndata"
        f.attrs["encoding-version"] = "0.1.0"

        _num_ds(f, "X", X)
        _dataframe(f, "obs", "_index", obs_index, obs_cols)
        _dataframe(f, "var", "_index", var_index, [])

        gm = f.create_group("obsm")
        gm.attrs["encoding-type"] = "dict"
        gm.attrs["encoding-version"] = "0.1.0"
        for k, v in (obsm or {}).items():
            _num_ds(gm, k, np.asarray(v, dtype=np.float64))

        gu = f.create_group("uns")
        gu.attrs["encoding-type"] = "dict"
        gu.attrs["encoding-version"] = "0.1.0"
        for k, v in (uns or {}).items():
            if isinstance(v, str):
                ds = gu.create_dataset(k, data=v)
                ds.attrs["encoding-type"] = "string"
            else:
                ds = gu.create_dataset(k, data=float(v))
                ds.attrs["encoding-type"] = "numeric-scalar"
            ds.attrs["encoding-version"] = "0.2.0"

        # empty-but-present optional slots keep older readers from guessing
        for slot in ("layers", "obsp", "varp", "varm"):
            g = f.create_group(slot)
            g.attrs["encoding-type"] = "dict"
            g.attrs["encoding-version"] = "0.1.0"


def region_gene_counts(gd, features, id_prop):
    """(names, counts[n_regions, n_genes], areas_px2, centroids) from the
    GeneDensity CSR against the region polygons.

    One labels raster on the density grid (regions drawn in file order, later
    regions painting over earlier at the rare overlapping cell), then a single
    pass over the CSR: no per-gene dense grids, no 400-grid cache blow-up.
    """
    from PIL import Image, ImageDraw
    from shapely.geometry import shape

    names, geoms = [], []
    for f in features or []:
        nm = (f.get("properties") or {}).get(id_prop)
        try:
            g = shape(f["geometry"])
        except Exception:
            continue
        if nm is None or g.is_empty:
            continue
        names.append(str(nm))
        geoms.append(g if g.is_valid else g.buffer(0))
    if not names:
        raise ValueError("no regions to export")

    # px -> density-grid cells: cell = px * pixel_size / grid_step
    sx = gd.pixel_size / gd.grid_x
    sy = gd.pixel_size / gd.grid_y
    lab = Image.new("I", (gd.cols, gd.rows), -1)
    drw = ImageDraw.Draw(lab)
    for i, g in enumerate(geoms):
        polys = [g] if g.geom_type == "Polygon" else list(g.geoms)
        for p in polys:
            ext = [(x * sx, y * sy) for x, y in p.exterior.coords]
            if len(ext) >= 3:
                drw.polygon(ext, fill=i)
            for hole in p.interiors:
                h = [(x * sx, y * sy) for x, y in hole.coords]
                if len(h) >= 3:
                    drw.polygon(h, fill=-1)
    labels = np.asarray(lab, dtype=np.int64)          # (rows, cols)

    genes = gd.gene_names
    G, R = len(genes), len(names)
    ip = gd._indptr
    nnz_rows = np.diff(ip)
    row_ids = np.repeat(np.arange(len(nnz_rows), dtype=np.int64), nnz_rows)
    gene_of = row_ids // gd.rows
    lr_of = row_ids % gd.rows
    region_of = labels[lr_of, gd._indices]
    ok = region_of >= 0
    flat = region_of[ok] * G + gene_of[ok]
    counts = np.bincount(flat, weights=gd._data[ok].astype(np.float64),
                         minlength=R * G).reshape(R, G)

    keep = [i for i, g in enumerate(genes) if g in set(gd.gene_list())]
    counts = counts[:, keep]
    kept_genes = [genes[i] for i in keep]

    areas = [float(g.area) for g in geoms]
    cents = [[g.centroid.x, g.centroid.y] for g in geoms]
    return names, counts, kept_genes, areas, cents
