import type { LocatorCandidate, LocatorDescriptor } from '../action-model.js';

export function buildSelectorRecord(
  locator: LocatorDescriptor,
  locatorAlternatives: LocatorCandidate[] = [],
): {
  locator: LocatorDescriptor;
  locatorAlternatives: LocatorCandidate[];
} {
  return {
    locator,
    locatorAlternatives: [...locatorAlternatives],
  };
}
