// node frontend/tests/pointEditMode.test.mjs
//
// Clicking points, checked without a browser: a point handle is told apart from
// everything else, and a PLAIN click on a point deletes nothing. (Deleting --
// right-click -- is done by the server, see atlas_editor_sandbox/test_delete_point.py.)
import { PointEditMode, existingPointProps } from '../src/pointEditMode.js';

let fails = 0;
const check = (label, cond, detail = '') => {
  console.log(`  ${cond ? 'ok  ' : 'FAIL'}  ${label}${detail ? '  ' + detail : ''}`);
  if (!cond) fails += 1;
};

const square = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]];
const fc = (rings) => ({
  type: 'FeatureCollection',
  features: rings.map((r, i) => ({
    type: 'Feature', properties: { name: `R${i}` },
    geometry: { type: 'Polygon', coordinates: [r] },
  })),
});
const handle = (featureIndex, positionIndexes, editHandleType = 'existing') => ({
  properties: { guideType: 'editHandle', editHandleType, featureIndex, positionIndexes },
});

console.log('\nrecognising a point');
check('an existing point handle is recognised', !!existingPointProps(handle(0, [0, 1])));
check('an in-between (intermediate) handle is not a point', !existingPointProps(handle(0, [0, 1], 'intermediate')));
check('a region body is not a point', !existingPointProps({ properties: { name: 'R0' } }));

console.log('\na PLAIN click on a point deletes nothing');
const mode = new PointEditMode();
let edits = 0;
const props = { data: fc([square]), selectedIndexes: [0], modeConfig: {}, onEdit: () => { edits += 1; } };
mode.handleClick({ picks: [{ isGuide: true, object: handle(0, [0, 2]) }] }, props);
check('no edit fired', edits === 0, `${edits} edit(s)`);

console.log('\n' + (fails ? `${fails} FAILED` : 'all checks passed'));
process.exit(fails ? 1 : 0);
