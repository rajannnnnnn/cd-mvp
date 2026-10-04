/** The concession schedule shown in the marketing demo. It mirrors backend/src/salesai/modules/pricing/engine.py
 *  (`concession_levels`): strictly decreasing levels from the list price down to the floor, evenly spaced, each rounded
 *  UP to the rounding rule so a level never undercuts the floor; the last level IS the floor.
 *  Integer arithmetic only, so it agrees exactly with the server's exact decimals (see ladder.test.ts and the golden
 *  file shared with the backend). */
export function ladderLevels(list: number, floor: number, steps: number, roundTo: number): number[] {
  const levels: number[] = []
  if (steps <= 0 || floor >= list) return levels
  let prev = list
  for (let k = 1; k <= steps; k++) {
    let lvl: number
    if (k === steps) lvl = floor
    else {
      // raw = list - (list - floor) * k / steps, rounded up to a multiple of roundTo, all in integers
      const num = list * steps - (list - floor) * k
      const den = steps * roundTo
      const rounded = Math.floor((num + den - 1) / den) * roundTo
      lvl = Math.max(floor, Math.min(prev, rounded))
    }
    if (lvl < prev) { levels.push(lvl); prev = lvl }
  }
  return levels
}

/** What the assistant offers a customer who asks for `ask` (below the list price): the lowest step that is still at or
 *  above the ask, never below the floor. If the ask is already above every step, it simply accepts the ask (the
 *  engine never offers less than a customer has offered). null when the price is fixed. */
export function stepFor(levels: number[], ask: number): number | null {
  if (!levels.length) return null
  return [...levels].reverse().find((p) => p >= ask) ?? ask
}
