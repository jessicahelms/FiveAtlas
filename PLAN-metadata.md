# Plan — damage annotation and metadata export

Revised 2026-08-06 from the user's description of how it actually works. Not
implemented (`app.py` was being edited by another session).

Templates:
```
S:\Phys\FIV925 XSection\Aldrin\RNAscope\metadata.yml            sample-level
S:\Phys\FIV925 XSection\Aldrin\RNAscope\annotation.notes.yaml   per-region
```

---

## The model, in one paragraph

Damage is **drawn**, not typed. The annotator creates a new region and names it
after a designation; multiple instances number themselves `voidsmall.1`,
`voidsmall.2`. On export, each damage shape is assigned to whichever anatomical
region contains it, and lands in that region's `voids` list in
`annotation.notes.yaml`, with the designation added to its `damage` list. Export
asks who you are and writes that into `annotator` for the regions you worked on and
into the `annotators` roster in `metadata.yml`.

So the YAML is **generated from geometry**, not hand-maintained. That is the whole
design, and it is why this is worth building: the file six people edit by hand
becomes a export artifact.

---

## The designations

Fourteen. The parenthesised token is the YAML tag and the shape-name stem.

| tag | name | drawn? |
|---|---|---|
| `missing` | Missing — region absent from the slice | no |
| `voidsmall` | Small void — small tears/scratches, no real tissue loss | optional |
| `separation` | Separation — large tear, tissue loss unlikely | **yes** |
| `voidlarge` | Large void — large tear with obvious missing tissue | **yes** |
| `bubble` | Bubble — ring with tissue pushed to the edges | **yes** |
| `foldsmall` | Small external fold — ≲10% of region covered | no |
| `foldlarge` | Large external fold — >10% covered | no |
| `foldinternal` | Internal fold — interior folded over itself | **yes** |
| `cutoff` | Cutoff — partially outside the imaged field | no |
| `overlap` | Overlap — overlapping an adjacent hemisection | no |
| `removal` | Removal — removed with the discarded hemisection | no |
| `distortion` | Distortion — pinched/warped, cells unlikely lost | no |
| `transcripts` | Low/no transcripts — intact tissue, few transcripts | no |
| `enclave` | Region fully encloses another annotated region | special, see below |

"Drawn" decides whether a shape is expected. This matters for export: in the sample
file `lft` has `damage: voidlarge,separation,bubble,transcripts` but
`voids: voidlarge.2,separation.7,bubble.1` — **`transcripts` appears in `damage`
with no instance in `voids`**, because it is not drawn. So:

- `damage` = the set of designations affecting the region (drawn or not)
- `voids` = the ids of the drawn instances only

**Therefore the panel must let a designation be recorded without geometry.** Half
of these can never be drawn; a UI that only produces tags from shapes cannot express
`missing`, `cutoff`, `transcripts` at all.

### Enclaves are a different thing

An enclave is "region A fully encloses region B, and A needs a hole where B is".
That is not damage, it is topology, and the app can detect it: `topology.containment()`
(added by the other session for the `hemi` guard) already reports one region sitting
inside another. On export, for each anatomical region A that fully contains another
annotated region B, write B into A's `enclaves`. Also worth surfacing live: if A
contains B and A's polygon has no hole there, that is a real annotation error and
the geometry check should say so.

---

## Naming: dropdown, not typing

Adding a region currently asks for a free-text name. Split it into two paths:

- **Anatomical region** — free text, as now.
- **Damage** — a dropdown of the fourteen designations. Choosing one names the shape
  `<tag>.<n>` with `n` the next free number, and the name is not typed at all.

Numbering is **global per designation across the section**, not per region — the
sample data proves it: `separation.4/.5/.6` are in `HY` and `separation.7` is in
`lft`. So the next number comes from scanning every existing damage shape.

Still parse names defensively for files annotated before this existed, or by hand:
normalise by lowercasing and stripping spaces, hyphens and underscores, then match
against both the tag and the display name, so `Small Void`, `small_void`,
`voidsmall` and `VoidSmall` all resolve to `voidsmall`. Anything unrecognised stays
an ordinary region — never guess.

---

## Assignment: containment does the work

On export, each damage shape is assigned to the anatomical region that contains it.

- Fully inside exactly one region → that region.
- Straddling several → assign to the one with the **largest overlap**, and flag it.
- Inside none (outside the tissue, or the regions do not cover it) → flag as
  unassigned rather than dropping it silently.

**The user must be able to override**, both when it is wrong and when the damage
genuinely spans regions. So the export step is not silent: it shows the assignment
table — shape, designation, assigned region, % overlap — with an editable region
cell and multi-select for a shape that belongs to more than one. Anything flagged
sorts to the top.

*Open question:* when one shape spans two regions, does its id go in **both**
regions' `voids`, or only the dominant one? The sample file has no duplicated id, so
the current data cannot answer it. Defaulting to both, since dropping it from a
region that is genuinely affected loses information — but confirm.

---

## Damage shapes must not behave like regions

This is the trap. Damage shapes deliberately sit *inside* anatomical regions, so
every existing assumption that the file is a clean partition is violated:

- **Share borders / partition / move-border** must exclude them, and
  `containment()` would refuse them anyway (a shape 100% inside a region is exactly
  what that guard catches).
- **Geometry validation** must not report a damage shape overlapping its host as an
  error — that overlap is the point.
- **The region list** should show them in a separate "Damage" group, with the
  designation and host region, not mixed in with anatomy.
- **Export of regions** should be able to include or omit them — downstream tools
  expecting an anatomical partition will choke on them.

Identify them by the parsed name, and keep the parse in one place
(`backend/designations.py`) so every consumer agrees.

---

## Export: the name prompt

On export, ask "Who are you?" — a dropdown of `metadata.yml: annotators` plus free
text for a new person. Then:

- `annotator` is set for **the regions this person actually worked on**, which the
  `_provenance` trail already knows (each entry carries `regions` and `who`). This
  is where that trail earns its keep — it is the only record of who touched what.
- A name not already in `metadata.yml: annotators` is appended to the roster.
- Regions nobody touched keep whatever `annotator` they had.

Remember the name for the session so it is asked once, not per export.

---

## Writing the YAML

`ruamel.yaml`, **not PyYAML** — round-tripping must preserve comments, key order and
the `---` / `...` markers. This file is hand-edited by six people; silently
reformatting it is its own kind of damage. New dependency: add to the venv and to
`hiddenimports` in `FiveAtlas.spec` (another session owns that file — coordinate).

Parse defensively: `hemi` in the sample omits `damage`/`voids`/`enclaves` entirely,
and `placeholder` is a common real value that must render as "not set", never as
data. Preserve any key the app does not understand rather than dropping it.

Write to a working copy, with an explicit "Save notes to the dataset folder" that
names the exact path and asks once — these are shared files, and the rest of the app
already treats the dataset folder as read-only.

---

## Order of work

1. `backend/designations.py` — the fourteen, aliases, name parse/format, next-number.
2. Damage-aware region list and the add-region dropdown (frontend).
3. Exclude damage shapes from partition / validation / share-borders.
4. `backend/notes.py` — ruamel round-trip read/write of both YAMLs.
5. Containment-based assignment + the reviewable assignment table at export.
6. Enclave detection, and the geometry check for a missing hole.
7. Annotator prompt at export, fed by the provenance trail.
8. Per-region annotation panel for the designations that are never drawn, plus
   `notes` free text.

Steps 1–3 are worth doing first and alone: until damage shapes stop being treated as
anatomy, drawing one will corrupt a share-borders run.

---

## Still to confirm

1. A shape spanning two regions — both regions' `voids`, or just the dominant one?
2. Is `voidsmall` numbered even when not drawn, or does an undrawn designation never
   get an instance id? (Sample suggests the latter — `transcripts` has none.)
3. `enclaves` in the sample holds `placeholder1,placeholder2` — is the intended
   content the enclosed **region name** (e.g. `VL`) or an instance id?
4. Do damage shapes belong in the exported regions `.geojson` that downstream tools
   read, or only in the YAML?
