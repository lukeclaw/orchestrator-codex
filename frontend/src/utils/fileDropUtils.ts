/** Utilities for drag-and-drop file support in terminals. */

/** File extensions accepted for drop-to-upload. */
export const SUPPORTED_EXTENSIONS = new Set([
  // Python / JS / TS
  '.py', '.pyi', '.pyw', '.ipynb',
  '.ts', '.tsx', '.js', '.jsx', '.cjs', '.mjs', '.d.ts',
  // Data / config
  '.json', '.jsonl', '.ndjson', '.yaml', '.yml',
  '.toml', '.cfg', '.ini', '.conf', '.env', '.properties',
  // Docs / text
  '.md', '.mdx', '.txt', '.rst', '.csv', '.tsv', '.log',
  '.tex', '.bib', '.org', '.adoc',
  // Web
  '.html', '.css', '.scss', '.less', '.styl', '.svg',
  '.vue', '.svelte', '.astro',
  '.ejs', '.hbs', '.erb', '.pug',
  '.j2', '.jinja', '.jinja2',
  // Shell
  '.sh', '.bash', '.zsh', '.fish', '.ps1', '.bat', '.cmd',
  // Systems / compiled
  '.rs', '.go', '.c', '.cpp', '.h', '.hpp', '.cs', '.fs',
  '.java', '.kt', '.scala', '.gradle', '.gradle.kts', '.sbt',
  '.swift', '.m', '.mm',
  // Functional / ML
  '.hs', '.ml', '.mli', '.clj', '.cljs', '.elm', '.ex', '.exs', '.erl',
  // Scripting
  '.rb', '.php', '.lua', '.r', '.jl', '.pl', '.pm', '.dart',
  // Modern / niche
  '.zig', '.nim', '.v', '.vhdl', '.sol', '.move',
  '.asm', '.s',
  // Query / schema
  '.sql', '.graphql', '.gql', '.proto',
  // Markup / config
  '.xml', '.plist', '.cmake',
  '.tf', '.hcl', '.nix', '.dhall',
  // VCS / build
  '.lock', '.sum',
  '.patch', '.diff',
  // Documents
  '.pdf',
])

/** Known filenames without extensions (case-sensitive). */
export const KNOWN_EXTENSIONLESS = new Set([
  'Dockerfile', 'Makefile', 'Rakefile', 'Gemfile', 'Procfile',
  'Vagrantfile', 'Justfile', 'Brewfile', 'Taskfile',
  'CMakeLists.txt',
  'LICENSE', 'CHANGELOG', 'README', 'AUTHORS', 'CONTRIBUTORS',
  'CODEOWNERS',
])

/** Known dotfile names (case-insensitive, stored lowercase). */
export const KNOWN_DOTFILES = new Set([
  '.gitignore', '.gitattributes', '.gitmodules',
  '.editorconfig', '.eslintrc', '.prettierrc',
  '.dockerignore', '.npmrc', '.nvmrc', '.env',
  '.babelrc', '.browserslistrc', '.stylelintrc',
  '.flake8', '.pylintrc', '.rubocop.yml',
])

/** Files larger than this trigger a confirmation modal (10 MB). */
export const LARGE_FILE_THRESHOLD = 10 * 1024 * 1024

/**
 * Check whether a filename is a supported type for drag-and-drop upload.
 * Images are handled separately via onImagePaste, so they're excluded here.
 */
export function isSupportedDropFile(filename: string): boolean {
  const lower = filename.toLowerCase()
  const dotIdx = lower.lastIndexOf('.')

  if (dotIdx > 0) {
    // Normal extension check
    const ext = lower.slice(dotIdx)
    return SUPPORTED_EXTENSIONS.has(ext)
  }

  if (dotIdx === 0) {
    // Dotfile — check against known dotfiles (case-insensitive)
    return KNOWN_DOTFILES.has(lower)
  }

  // No dot at all — check against known extensionless filenames (case-sensitive)
  return KNOWN_EXTENSIONLESS.has(filename)
}

/**
 * Convert a File to a base64 string (without the data URL prefix).
 * Extracted from duplicate code in SessionDetailPage and BrainPanel.
 */
export function fileToBase64(file: File): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve((reader.result as string).split(',')[1])
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}
