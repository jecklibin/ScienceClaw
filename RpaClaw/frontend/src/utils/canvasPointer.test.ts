import assert from 'node:assert/strict';

import { mapPointerToImageSpace } from './canvasPointer.js';

const centerPoint = mapPointerToImageSpace(
  { left: 0, top: 0, width: 1000, height: 1000 },
  { clientX: 500, clientY: 500 },
  { width: 1000, height: 500 },
);

assert.deepEqual(centerPoint, { x: 0.5, y: 0.5, withinImage: true });

const topLetterboxPoint = mapPointerToImageSpace(
  { left: 0, top: 0, width: 1000, height: 1000 },
  { clientX: 500, clientY: 100 },
  { width: 1000, height: 500 },
);

assert.equal(topLetterboxPoint.withinImage, false);
assert.equal(topLetterboxPoint.y, 0);

const visibleTopEdgePoint = mapPointerToImageSpace(
  { left: 0, top: 0, width: 1000, height: 1000 },
  { clientX: 500, clientY: 250 },
  { width: 1000, height: 500 },
);

assert.deepEqual(visibleTopEdgePoint, { x: 0.5, y: 0, withinImage: true });

console.log('canvas pointer mapping tests passed');
