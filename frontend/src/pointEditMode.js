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
//
// Dragging works the same way. The library's handleStartDragging inserts a
// point whenever the press picked the "add a point here" handle on the outline
// -- even when it ALSO picked an existing point (a press picks everything
// within 10 px, and the add handle drawn at the last hover position, which
// looks exactly like a point, can be among them). It then dragged by stale
// indexes. So a press on a point drags that point and nothing else; and
// a drag that does insert a point is ONE undo step: the insert is marked
// dragStart, and App records the gesture when it is let go.
export class PointEditMode extends ModifyMode {
  handleClick(event, props) {
    if ((event.picks || []).some((p) => p && existingPointProps(p.object))) return;
    super.handleClick(event, props);   // a click on the outline between points adds one
  }

  handleStartDragging(event, props) {
    super.handleStartDragging({ ...event, picks: preferPoints(event.picks) }, {
      ...props,
      onEdit: (action) => props.onEdit(action && action.editType === 'addPosition'
        ? { ...action, editContext: { ...action.editContext, dragStart: true } }
        : action),
    });
  }

  handleDragging(event, props) {
    super.handleDragging({ ...event, pointerDownPicks: preferPoints(event.pointerDownPicks) }, props);
  }

  handleStopDragging(event, props) {
    super.handleStopDragging({ ...event, pointerDownPicks: preferPoints(event.pointerDownPicks) }, props);
  }
}

// The picks without the add-a-point handle, when an existing point is among them.
function preferPoints(picks) {
  if (!picks || !picks.some((p) => p && existingPointProps(p.object))) return picks;
  return picks.filter((p) => !(p && p.isGuide && p.object && p.object.properties
    && p.object.properties.editHandleType === 'intermediate'));
}

// The properties of an existing-point edit handle, or null. Mirrors the
// library's getPickedExistingEditHandle (edit-modes/utils.js), which the package
// does not export; accepts a deck.pickObject() result's object too.
export function existingPointProps(object) {
  const pr = object && object.properties;
  return pr && pr.guideType === 'editHandle' && pr.editHandleType === 'existing'
    && pr.featureIndex >= 0 && Array.isArray(pr.positionIndexes) ? pr : null;
}
