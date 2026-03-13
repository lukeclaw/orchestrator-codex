import { describe, it, expect } from 'vitest'
import { isSupportedDropFile } from './fileDropUtils'

describe('isSupportedDropFile', () => {
  // Supported extensions
  it.each([
    'main.py', 'index.ts', 'data.json', 'config.yaml', 'style.css',
    'app.tsx', 'server.go', 'lib.rs', 'script.sh', 'query.sql',
  ])('accepts %s', (name) => {
    expect(isSupportedDropFile(name)).toBe(true)
  })

  // Case insensitive
  it.each(['DATA.JSON', 'Main.PY', 'INDEX.TS'])('accepts %s (case-insensitive)', (name) => {
    expect(isSupportedDropFile(name)).toBe(true)
  })

  // Known extensionless files
  it.each(['Dockerfile', 'Makefile', 'LICENSE', 'CODEOWNERS'])('accepts %s (extensionless)', (name) => {
    expect(isSupportedDropFile(name)).toBe(true)
  })

  // Known dotfiles
  it.each(['.gitignore', '.editorconfig', '.eslintrc', '.dockerignore', '.env'])(
    'accepts %s (dotfile)',
    (name) => {
      expect(isSupportedDropFile(name)).toBe(true)
    },
  )

  // Rejected file types
  it.each([
    'photo.png', 'image.jpg', 'video.mp4', 'app.exe', 'archive.zip',
    'binary.bin', 'font.woff2', 'model.pkl',
  ])('rejects %s', (name) => {
    expect(isSupportedDropFile(name)).toBe(false)
  })

  // Unknown extensionless files
  it.each(['mycommand', 'randomfile', 'something'])('rejects unknown extensionless %s', (name) => {
    expect(isSupportedDropFile(name)).toBe(false)
  })

  // Unknown dotfiles
  it.each(['.DS_Store', '.hidden', '.random'])('rejects unknown dotfile %s', (name) => {
    expect(isSupportedDropFile(name)).toBe(false)
  })

  // Edge case: file with multiple dots
  it('accepts test.d.ts', () => {
    expect(isSupportedDropFile('test.d.ts')).toBe(true)
  })

  // Edge case: compound extension uses last dot
  it('accepts config.test.json', () => {
    expect(isSupportedDropFile('config.test.json')).toBe(true)
  })
})
