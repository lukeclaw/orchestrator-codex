/**
 * File/folder icon mapping for the file explorer.
 * Icons are loaded from Material Icon Theme via jsDelivr CDN — zero bundle impact.
 */

const CDN_BASE =
  'https://cdn.jsdelivr.net/npm/material-icon-theme@5.31.0/icons'

// ── Exact filename → icon name ──────────────────────────────────────────────

const FILE_NAMES: Record<string, string> = {
  // Docker
  dockerfile: 'docker',
  'docker-compose.yml': 'docker',
  'docker-compose.yaml': 'docker',
  '.dockerignore': 'docker',
  // Git
  '.gitignore': 'git',
  '.gitattributes': 'git',
  '.gitmodules': 'git',
  '.gitkeep': 'git',
  // CI/CD
  '.gitlab-ci.yml': 'gitlab',
  '.travis.yml': 'travis',
  'jenkinsfile': 'jenkins',
  // JS/TS config
  'package.json': 'nodejs',
  'package-lock.json': 'nodejs',
  'tsconfig.json': 'tsconfig',
  'jsconfig.json': 'jsconfig',
  '.eslintrc': 'eslint',
  '.eslintrc.js': 'eslint',
  '.eslintrc.cjs': 'eslint',
  '.eslintrc.json': 'eslint',
  'eslint.config.js': 'eslint',
  'eslint.config.mjs': 'eslint',
  'eslint.config.ts': 'eslint',
  '.prettierrc': 'prettier',
  '.prettierrc.json': 'prettier',
  'prettier.config.js': 'prettier',
  '.prettierignore': 'prettier',
  'babel.config.js': 'babel',
  '.babelrc': 'babel',
  'webpack.config.js': 'webpack',
  'webpack.config.ts': 'webpack',
  'vite.config.ts': 'vite',
  'vite.config.js': 'vite',
  'rollup.config.js': 'rollup',
  'rollup.config.ts': 'rollup',
  'tailwind.config.js': 'tailwindcss',
  'tailwind.config.ts': 'tailwindcss',
  'postcss.config.js': 'postcss',
  'next.config.js': 'next',
  'next.config.mjs': 'next',
  'nuxt.config.ts': 'nuxt',
  'svelte.config.js': 'svelte',
  'astro.config.mjs': 'astro',
  // Python
  'requirements.txt': 'python-misc',
  'setup.py': 'python-misc',
  'setup.cfg': 'python-misc',
  'pyproject.toml': 'python-misc',
  'pipfile': 'python-misc',
  'pipfile.lock': 'python-misc',
  '.python-version': 'python-misc',
  'manage.py': 'django',
  // Ruby
  gemfile: 'gemfile',
  'gemfile.lock': 'gemfile',
  rakefile: 'ruby',
  // Rust
  'cargo.toml': 'cargo',
  'cargo.lock': 'cargo',
  // Go
  'go.mod': 'go-mod',
  'go.sum': 'go-mod',
  // Build
  makefile: 'makefile',
  cmakelists: 'cmake',
  'cmakelists.txt': 'cmake',
  // Config
  '.editorconfig': 'editorconfig',
  '.env': 'tune',
  '.env.local': 'tune',
  '.env.development': 'tune',
  '.env.production': 'tune',
  '.env.example': 'tune',
  '.npmrc': 'npm',
  '.npmignore': 'npm',
  '.nvmrc': 'nodejs',
  '.node-version': 'nodejs',
  'yarn.lock': 'yarn',
  '.yarnrc': 'yarn',
  '.yarnrc.yml': 'yarn',
  'pnpm-lock.yaml': 'pnpm',
  'pnpm-workspace.yaml': 'pnpm',
  'bun.lockb': 'bun',
  'deno.json': 'deno',
  'deno.lock': 'deno',
  // Misc
  license: 'certificate',
  'license.md': 'certificate',
  'license.txt': 'certificate',
  changelog: 'changelog',
  'changelog.md': 'changelog',
  'readme.md': 'readme',
  'readme.txt': 'readme',
  readme: 'readme',
  'todo.md': 'todo',
  'authors.md': 'authors',
  '.browserslistrc': 'browserlist',
  'biome.json': 'biome',
}

// ── File extension → icon name ──────────────────────────────────────────────

const FILE_EXTENSIONS: Record<string, string> = {
  // JavaScript / TypeScript
  js: 'javascript',
  mjs: 'javascript',
  cjs: 'javascript',
  jsx: 'react',
  ts: 'typescript',
  mts: 'typescript',
  cts: 'typescript',
  tsx: 'react_ts',
  // Web
  html: 'html',
  htm: 'html',
  css: 'css',
  scss: 'sass',
  sass: 'sass',
  less: 'less',
  styl: 'stylus',
  svg: 'svg',
  // Data / Config
  json: 'json',
  jsonc: 'json',
  json5: 'json',
  yaml: 'yaml',
  yml: 'yaml',
  toml: 'toml',
  xml: 'xml',
  csv: 'csv',
  ini: 'settings',
  cfg: 'settings',
  conf: 'settings',
  properties: 'settings',
  // Markdown / Docs
  md: 'markdown',
  mdx: 'mdx',
  rst: 'readme',
  txt: 'document',
  pdf: 'pdf',
  // Python
  py: 'python',
  pyi: 'python',
  pyx: 'python',
  pyw: 'python',
  ipynb: 'jupyter',
  // Rust
  rs: 'rust',
  // Go
  go: 'go',
  // Java / JVM
  java: 'java',
  kt: 'kotlin',
  kts: 'kotlin',
  scala: 'scala',
  groovy: 'groovy',
  gradle: 'gradle',
  // C / C++
  c: 'c',
  h: 'c',
  cpp: 'cpp',
  cxx: 'cpp',
  cc: 'cpp',
  hpp: 'cpp',
  hxx: 'cpp',
  // C#
  cs: 'csharp',
  // Ruby
  rb: 'ruby',
  erb: 'erb',
  // PHP
  php: 'php',
  // Shell
  sh: 'console',
  bash: 'console',
  zsh: 'console',
  fish: 'console',
  ps1: 'powershell',
  bat: 'console',
  cmd: 'console',
  // Swift / Objective-C
  swift: 'swift',
  m: 'objective-c',
  mm: 'objective-cpp',
  // Dart / Flutter
  dart: 'dart',
  // Lua
  lua: 'lua',
  // R
  r: 'r',
  rmd: 'r',
  // SQL
  sql: 'database',
  // GraphQL
  graphql: 'graphql',
  gql: 'graphql',
  // Protobuf
  proto: 'proto',
  // Docker
  dockerignore: 'docker',
  // Terraform
  tf: 'terraform',
  tfvars: 'terraform',
  // Nix
  nix: 'nix',
  // Zig
  zig: 'zig',
  // Elixir
  ex: 'elixir',
  exs: 'elixir',
  // Haskell
  hs: 'haskell',
  lhs: 'haskell',
  // Clojure
  clj: 'clojure',
  cljs: 'clojure',
  cljc: 'clojure',
  // OCaml
  ml: 'ocaml',
  mli: 'ocaml',
  // Erlang
  erl: 'erlang',
  // Images
  png: 'image',
  jpg: 'image',
  jpeg: 'image',
  gif: 'image',
  bmp: 'image',
  ico: 'image',
  webp: 'image',
  avif: 'image',
  // Video
  mp4: 'video',
  avi: 'video',
  mov: 'video',
  mkv: 'video',
  webm: 'video',
  // Audio
  mp3: 'audio',
  wav: 'audio',
  ogg: 'audio',
  flac: 'audio',
  // Archives
  zip: 'zip',
  tar: 'zip',
  gz: 'zip',
  bz2: 'zip',
  xz: 'zip',
  '7z': 'zip',
  rar: 'zip',
  // Fonts
  woff: 'font',
  woff2: 'font',
  ttf: 'font',
  otf: 'font',
  eot: 'font',
  // Lock files
  lock: 'lock',
  // Logs
  log: 'log',
  // Env
  env: 'tune',
}

// ── Folder name → icon name (without folder- prefix) ───────────────────────

const FOLDER_NAMES: Record<string, string> = {
  src: 'src',
  source: 'src',
  lib: 'lib',
  libs: 'lib',
  dist: 'dist',
  build: 'dist',
  out: 'dist',
  output: 'dist',
  bin: 'dist',
  test: 'test',
  tests: 'test',
  __tests__: 'test',
  spec: 'test',
  specs: 'test',
  e2e: 'test',
  node_modules: 'node',
  '.git': 'git',
  '.github': 'github',
  '.gitlab': 'gitlab',
  '.vscode': 'vscode',
  '.idea': 'intellij',
  config: 'config',
  configs: 'config',
  configuration: 'config',
  '.config': 'config',
  public: 'public',
  static: 'public',
  assets: 'images',
  images: 'images',
  img: 'images',
  icons: 'images',
  docs: 'docs',
  doc: 'docs',
  documentation: 'docs',
  api: 'api',
  apis: 'api',
  components: 'components',
  component: 'components',
  hooks: 'hook',
  utils: 'utils',
  util: 'utils',
  utilities: 'utils',
  helpers: 'helper',
  helper: 'helper',
  services: 'services',
  service: 'services',
  middleware: 'middleware',
  middlewares: 'middleware',
  routes: 'routes',
  router: 'routes',
  models: 'database',
  model: 'database',
  schemas: 'database',
  migrations: 'database',
  types: 'typescript',
  typings: 'typescript',
  '@types': 'typescript',
  interfaces: 'typescript',
  scripts: 'scripts',
  tools: 'tools',
  templates: 'template',
  views: 'views',
  pages: 'views',
  layouts: 'layout',
  styles: 'css',
  css: 'css',
  sass: 'sass',
  fonts: 'font',
  i18n: 'i18n',
  locale: 'i18n',
  locales: 'i18n',
  plugins: 'plugin',
  vendor: 'dist',
  tmp: 'temp',
  temp: 'temp',
  '.tmp': 'temp',
  cache: 'temp',
  '.cache': 'temp',
  __pycache__: 'python',
  '.venv': 'python',
  venv: 'python',
  env: 'environment',
  '.env': 'environment',
  logs: 'log',
  coverage: 'coverage',
  '.nyc_output': 'coverage',
  __snapshots__: 'test',
  fixtures: 'test',
  mocks: 'test',
  __mocks__: 'test',
  storybook: 'storybook',
  '.storybook': 'storybook',
  docker: 'docker',
  nginx: 'nginx',
  kubernetes: 'kubernetes',
  k8s: 'kubernetes',
  ci: 'ci',
  '.ci': 'ci',
  github: 'github',
  workflows: 'github',
  actions: 'github',
  android: 'android',
  ios: 'apple',
  linux: 'linux',
  windows: 'windows',
  shared: 'shared',
  common: 'shared',
  core: 'core',
  app: 'app',
  server: 'server',
  client: 'client',
  frontend: 'client',
  backend: 'server',
  auth: 'secure',
  security: 'secure',
  tasks: 'tasks',
  context: 'context',
  '.claude': 'robot',
}

// ── Public API ──────────────────────────────────────────────────────────────

/** Get CDN URL for a file icon based on its name. */
export function getFileIconUrl(filename: string): string {
  const lower = filename.toLowerCase()

  // 1. Exact filename match
  const byName = FILE_NAMES[lower]
  if (byName) return `${CDN_BASE}/${byName}.svg`

  // 2. Extension match (try longest first: e.g. "test.d.ts" → "d.ts" before "ts")
  const dotIdx = lower.indexOf('.')
  if (dotIdx >= 0) {
    const parts = lower.slice(dotIdx + 1).split('.')
    for (let i = 0; i < parts.length; i++) {
      const ext = parts.slice(i).join('.')
      const byExt = FILE_EXTENSIONS[ext]
      if (byExt) return `${CDN_BASE}/${byExt}.svg`
    }
  }

  // 3. Default file icon
  return `${CDN_BASE}/file.svg`
}

/** Get CDN URL for a folder icon based on its name and open/closed state. */
export function getFolderIconUrl(folderName: string, isOpen: boolean): string {
  const lower = folderName.toLowerCase()
  const mapped = FOLDER_NAMES[lower]
  if (mapped) {
    return isOpen
      ? `${CDN_BASE}/folder-${mapped}-open.svg`
      : `${CDN_BASE}/folder-${mapped}.svg`
  }
  return isOpen ? `${CDN_BASE}/folder-open.svg` : `${CDN_BASE}/folder.svg`
}
