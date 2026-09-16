import { ModifyMode } from '@deck.gl-community/editable-layers';

// Editing a region's points.
//
// The library's ModifyMode deletes a point on ANY click on it, the left button
// included. Nobody could discover that, and a press meant to start a drag could
// delete a point instead. So here a click on a point does nothing at all, and
// deleting is a deliberate RIGHT-click, handled by the viewer's context-menu
// handler -- which asks the server, so a point on a shared border goes from both
// regions and the rest of that stretch re-spaces.
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
