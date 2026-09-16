import { ModifyMode, ImmutableFeatureCollection } from '@deck.gl-community/editable-layers';

// Editing a region's points.
//
// The library's ModifyMode deletes a point on ANY click on it, the left button
// included. Nobody could discover that, and a press meant to start a drag could
// delete a point instead. So here a click on a point does nothing at all, and
// deleting is a deliberate RIGHT-click, handled by deletePointAt() from the
// viewer's context-menu handler.
//
// The right-click is taken there, and only there: deck does not reliably pass
// the right button through to the mode as a click, and handling it in both
// places could delete two different points from one press (the second removal
// would land on shifted indices).
export class PointEditMode extends ModifyMode {
  handleClick(event, props) {
    if ((event.picks || []).some((p) => p && existingPointProps(p.object))) return;
    super.handleClick(event, props);   // a click on the outline between points adds one
  }
}

// The properties of an existing-point edit handle, or null. Mirrors the
// library's getPickedExistingEditHandle (edit-modes/utils.js), which the package
// does not export; accepts a deck.pickObject() result's object too.
export function existingPointProps(object) {
  const pr = object && object.properties;
  return pr && pr.guideType === 'editHandle' && pr.editHandleType === 'existing'
    && pr.featureIndex >= 0 && Array.isArray(pr.positionIndexes) ? pr : null;
}

// The ring a point belongs to: a Polygon's [ring, point] or a MultiPolygon's
// [polygon, ring, point] -- everything but the last index.
function ringOf(geometry, positionIndexes) {
  let node = geometry && geometry.coordinates;
  for (let i = 0; i < positionIndexes.length - 1; i += 1) node = node && node[positionIndexes[i]];
  return Array.isArray(node) ? node : null;
}

// Delete one point. Returns { updatedData, editContext }, or { refused: true }
// when the ring would drop below a triangle -- 4 positions, since a GeoJSON
// ring repeats its first point at the end.
export function deletePointAt(fc, pointProps) {
  const feature = fc && fc.features && fc.features[pointProps.featureIndex];
  const ring = feature && ringOf(feature.geometry, pointProps.positionIndexes);
  if (!ring || ring.length <= 4) return { refused: true };
  try {
    const updatedData = new ImmutableFeatureCollection(fc)
      .removePosition(pointProps.featureIndex, pointProps.positionIndexes)
      .getObject();
    return {
      updatedData,
      editContext: {
        featureIndexes: [pointProps.featureIndex],
        positionIndexes: pointProps.positionIndexes,
      },
    };
  } catch (e) {
    return { refused: true };
  }
}
