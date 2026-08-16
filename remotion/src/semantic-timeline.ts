import type {ProofTransition} from './types-semantic';

/** Return the latest transition that has started at ``frame``.
 *
 * Transition start frames are strictly ordered by the Python exporter.  A
 * binary search avoids allocating and reversing the complete transition list
 * for every rendered video frame, which becomes expensive for long proofs.
 */
export const transitionAtFrame = (
  transitions: ProofTransition[],
  frame: number,
): ProofTransition | null => {
  let low = 0;
  let high = transitions.length - 1;
  let found = -1;
  while (low <= high) {
    const middle = low + Math.floor((high - low) / 2);
    if (transitions[middle].startFrame <= frame) {
      found = middle;
      low = middle + 1;
    } else {
      high = middle - 1;
    }
  }
  return found >= 0 ? transitions[found] : null;
};
