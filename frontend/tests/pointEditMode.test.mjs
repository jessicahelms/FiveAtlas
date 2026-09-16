// node frontend/tests/pointEditMode.test.mjs
//
// The point-deletion rules, checked without a browser: right-click deletes
// exactly one point, a plain click on a point deletes nothing, and a region
// never drops below a triangle.
import { PointEditMode, existingPointProps, deletePointAt } from '../src/pointEditMode.js';

let fails = 0;
const check = (label, cond, detail = '') => {
  console.log(`  ${cond ? 'ok  ' : 'FAIL'}  ${label}${detail ? '  ' + detail : ''}`);
  if (!cond) fails += 1;
};

const square = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]];
const triangle = [[0, 0], [10, 0], [5, 8], [0, 0]];
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

console.log('\nright-click deletes one point');
const res = deletePointAt(fc([square]), handle(0, [0, 2]).properties);
const ring = res.updatedData && res.updatedData.features[0].geometry.coordinates[0];
check('a square loses exactly one corner', ring && ring.length === 4, ring && JSON.stringify(ring));
check('the deleted corner is the one clicked', ring && !ring.some(([x, y]) => x === 10 && y === 10));
check('the ring is still closed', ring && JSON.stringify(ring[0]) === JSON.stringify(ring[ring.length - 1]));
check('the edit says which feature changed', res.editContext && res.editContext.featureIndexes[0] === 0);

console.log('\ndeleting the FIRST point keeps the ring closed');
const first = deletePointAt(fc([square]), handle(0, [0, 0]).properties);
const r0 = first.updatedData && first.updatedData.features[0].geometry.coordinates[0];
check('first corner removed and ring re-closed',
  r0 && r0.length === 4 && JSON.stringify(r0[0]) === JSON.stringify(r0[r0.length - 1]), r0 && JSON.stringify(r0));

console.log('\na triangle cannot lose a point');
check('refused', deletePointAt(fc([triangle]), handle(0, [0, 1]).properties).refused === true);

console.log('\nthe other regions are untouched');
const two = deletePointAt(fc([square, square]), handle(1, [0, 1]).properties);
check('only region 2 changed',
  two.updatedData.features[0].geometry.coordinates[0].length === 5
  && two.updatedData.features[1].geometry.coordinates[0].length === 4);

console.log('\na PLAIN click on a point deletes nothing');
const mode = new PointEditMode();
let edits = 0;
const props = { data: fc([square]), selectedIndexes: [0], modeConfig: {}, onEdit: () => { edits += 1; } };
mode.handleClick({ picks: [{ isGuide: true, object: handle(0, [0, 2]) }] }, props);
check('no edit fired', edits === 0, `${edits} edit(s)`);

console.log('\n' + (fails ? `${fails} FAILED` : 'all checks passed'));
process.exit(fails ? 1 : 0);
