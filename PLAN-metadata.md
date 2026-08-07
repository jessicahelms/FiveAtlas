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

## File layout: damage is its own file

From the SOP (LabArchives, *Damage Tracking*): damage annotations are exported as a
**separate `damage.geojson` per sample**, shared between annotators. The workflow is:
import the existing `damage.geojson` if there is one, add any damage in your region
that nobody has annotated yet, re-export the whole file, then update
`annotation.notes.yaml`.

So FiveAtlas loads three things per dataset:

```
<regions>.geojson        anatomy, one per annotator's working copy
damage.geojson           damage shapes, SHARED across annotators for the sample
annotation.notes.yaml    per-region record, generated from the two above
```

Damage is a **separate layer**, drawn and edited alongside the regions but exported
to its own file. It does not belong in the region export — the SOP has annotators
strip non-damage annotations before exporting `damage.geojson`, and vice versa.

Because the file is shared and accumulates, two practical consequences:

- **Numbering must not collide.** `<tag>.<n>` is allocated across the whole
  `damage.geojson`, so two annotators working on the same sample at once can both
  mint `separation.8`. Renumber on import, or scope the check to the loaded file and
  detect duplicates on merge.
- **Import must merge, not replace.** Loading a `damage.geojson` that a colleague has
  since extended should keep their shapes and yours. Merge by name, and report
  same-name-different-geometry rather than silently picking one.

## Assignment: overlap, not containment

The SOP is explicit: fill *"the void field with the names of all selections in the
damage GeoJSON file that **overlap** your region"*. So a shape straddling two regions
appears in **both** regions' `voids` — which is what falls out naturally when each
annotator independently records their own region.

- Overlaps exactly one region → that region.
- Overlaps several → all of them, per the SOP.
- Overlaps none → flag as unassigned rather than dropping it silently.

Needs a minimum-overlap threshold so a hairline touch along a shared border does not
list a shape in a neighbour it barely grazes. Suggest ignoring below ~1% of the shape
or a few hundred px², shown in the table so it is never invisible.

**The user must be able to override.** Export shows the assignment table — shape,
designation, regions, % overlap each — with the region cells editable and anything
unassigned or marginal sorted to the top.

> **Conflict to resolve:** the user asked for *dominant region by default, with a
> prompt offering both*. The written SOP says *all overlapping regions*. These
> produce different YAML. Recommendation: follow the SOP (all overlapping, each
> deselectable in the table), because that is what the manual process has been
> producing and what downstream readers of these files expect.

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

## Answered by the SOP

- **Damage lives in its own `damage.geojson`**, shared per sample, not in the region
  export.
- **Overlap, not containment**, decides which regions list a shape.
- **`enclaves` holds the enclosed region's name** — "the enclosed region should be
  noted in the metadata yaml".
- **Undrawn designations get no instance id.** They appear in `damage` only, which
  is why the per-region panel must be able to add a code without geometry.

## Useful details from the SOP worth building in

- **Damage is found using the stains.** The ATP1A1/CD45/E-Cadherin boundary stain
  (magenta) shows voids as black and folds as increased signal; alphaSMA/Vimentin
  marks the pial surface, so alphaSMA interior to the boundary stain means an
  external fold. Entering damage-annotation mode should offer to switch to that
  channel pair — it is the actual working view for this task.
- **`notes` carries the judgement calls**, and the SOP asks for them explicitly:
  an area dense with small voids that were not individually annotated, or a line of
  separations where only the large ones were drawn. The panel should prompt for a
  note when a designation is recorded with no shape.
- **"Not Done"** is a real state — a region too damaged to annotate is marked Not
  Done rather than annotated badly. Worth representing.
- **Tissue that is not part of the section** (stray fragments, severed optic nerve)
  must be excluded from annotation. A stray-fragment check could flag small
  disconnected pieces far from the main body.

## Still to confirm

1. **Dominant-vs-all overlap** — see the conflict flagged above.
2. Is `damage.geojson` ever per-annotator rather than per-sample? If two people can
   hold different copies, merge-on-import needs to be the default, not a special case.
3. Should FiveAtlas write `annotation.notes.yaml` for **all** regions, or only the
   ones this annotator worked on? The SOP is per-annotator ("update the damage field"
   for your region), which suggests a partial update that preserves everyone else's
   entries untouched.
## SmartSheet

The SOP has annotators tick damage types in a SmartSheet dropdown by hand. Two ways
to help, and the cheap one may be the better one.

**Do the 30-second check first.** Log in → Account → Personal Settings → API Access.
If "Generate new access token" is there, that account has API access. Don't take my
word on which plans include it — Smartsheet has moved API access between tiers, and
the panel is definitive for *this* account.

### Option A — no API, no cost: "Copy for SmartSheet"

A button producing a TSV block — one row per region: region, damage codes, voids,
annotator, notes. Multi-column TSV pastes straight into a sheet. No token, no IT
approval, nothing to pay for, and it works today.

This gets most of the value. The manual step becomes one paste instead of ticking
boxes per region, and there is no credential to manage or leak.

### Option B — API write-back, if available

`POST /2.0/sheets/{id}/rows`, token in the header. Only worth it if updates should
land without anyone opening the sheet.

Three cautions if this route is taken:

- **A token is a secret.** Never in the repo, never in the shipped exe, never in an
  exported file. Per-user, stored in the workdir alongside `identity.json`, and
  excluded from every export path.
- **One shared token destroys the attribution.** SmartSheet records the token
  owner as the editor, so a single lab token makes every update look like one
  person's — which defeats the point of tracking who annotated what. Each annotator
  needs their own token, which is also a per-seat licence question.
- **Writing to a shared tracking sheet is outward-facing.** It must be explicit and
  confirmed, never a silent side effect of Export.

Recommendation: build Option A regardless — it is small, useful immediately, and
needs no permission from anyone. Treat Option B as optional, config-gated on a
`smartsheet.json` in the workdir, feature hidden when absent.
