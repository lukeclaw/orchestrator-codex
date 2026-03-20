const PREFIX = 'page-filters:'

export function savePageFilters(path: string, params: URLSearchParams) {
  try {
    const str = params.toString()
    if (str) sessionStorage.setItem(PREFIX + path, str)
    else sessionStorage.removeItem(PREFIX + path)
  } catch {
    // sessionStorage may be unavailable (e.g. private browsing quota)
  }
}

export function getPageFilters(path: string): string | null {
  try {
    return sessionStorage.getItem(PREFIX + path)
  } catch {
    return null
  }
}
