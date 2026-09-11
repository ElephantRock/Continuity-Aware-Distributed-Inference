// RT-01 review acceptance sample — intentionally flawed for review exercise.
const cache = new Map();

export async function getCachedValue(key, loader) {
  if (cache.has(key)) {
    return cache.get(key);
  }
  const value = await loader(key);
  cache.set(key, value);
  return value;
}

export function clearExpired(maxAgeMs) {
  const now = new Date().getTime();
  for (const key of cache.keys()) {
    if (now - cache.get(key).fetchedAt > maxAgeMs) {
      cache.delete(key);
    }
  }
}
