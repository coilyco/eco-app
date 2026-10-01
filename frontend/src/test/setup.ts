import "@testing-library/jest-dom/vitest"

function createMemoryStorage(): Storage {
  const values = new Map<string, string>()

  return {
    get length() {
      return values.size
    },
    clear() {
      values.clear()
    },
    getItem(key) {
      return values.get(key) ?? null
    },
    key(index) {
      return Array.from(values.keys())[index] ?? null
    },
    removeItem(key) {
      values.delete(key)
    },
    setItem(key, value) {
      values.set(key, String(value))
    },
  }
}

// A test that opts into the node environment (scripts/og-cards.test.mjs) has no window.
let hasLocalStorage = typeof window === "undefined"
try {
  hasLocalStorage ||= typeof window.localStorage?.getItem === "function"
} catch {
  hasLocalStorage = false
}

if (!hasLocalStorage) {
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: createMemoryStorage(),
  })
}
