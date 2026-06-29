/**
 * Returns a debounced copy of `value` that only updates after `delayMs`
 * of stillness. Used by SearchPage so rapid filter edits collapse into
 * one network request.
 *
 * Implementation note: we compare with `JSON.stringify` rather than `===`
 * because the filters object is reconstructed on every render of
 * SearchPage (it's derived from URL search params). Reference equality
 * would never hold, so the timer would never fire.
 */

import { useEffect, useState } from "react";

export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState<T>(value);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
    // Stringify lets us compare by structural equality. Filters are
    // small (a handful of arrays and strings) so the cost is trivial
    // and the win — no extra request for an unchanged identity — is
    // worth it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(value), delayMs]);

  return debounced;
}
