import { describe, it, expect } from 'vitest'
import { tokenize, renderTokens } from './Markdown'

describe('Markdown tokenizer', () => {
  describe('ordered lists', () => {
    it('handles tight ordered list (no blank lines)', () => {
      const input = `1. First item
2. Second item
3. Third item`
      const tokens = tokenize(input)
      
      expect(tokens).toHaveLength(1)
      expect(tokens[0].type).toBe('list')
      if (tokens[0].type === 'list') {
        expect(tokens[0].ordered).toBe(true)
        expect(tokens[0].items).toHaveLength(3)
        expect(tokens[0].items[0].content).toBe('First item')
        expect(tokens[0].items[1].content).toBe('Second item')
        expect(tokens[0].items[2].content).toBe('Third item')
      }
    })

    it('handles loose ordered list (blank lines between items)', () => {
      const input = `1. Programmatically create sub-tasks, each sub-task for auditing a single leaf folder that contains files to be audited

2. Go thought each sub-task, read every file and every line of code, use your best judgement to determine if any thing can break when member id < 0

3. Write a report for each sub-task audit result.

4. Aggregate all findings that needs work to the notes of the parent task.`
      const tokens = tokenize(input)
      
      expect(tokens).toHaveLength(1)
      expect(tokens[0].type).toBe('list')
      if (tokens[0].type === 'list') {
        expect(tokens[0].ordered).toBe(true)
        expect(tokens[0].items).toHaveLength(4)
        expect(tokens[0].items[0].content).toContain('Programmatically create sub-tasks')
        expect(tokens[0].items[1].content).toContain('Go thought each sub-task')
        expect(tokens[0].items[2].content).toContain('Write a report')
        expect(tokens[0].items[3].content).toContain('Aggregate all findings')
      }
    })

    it('handles loose ordered list with multiple blank lines', () => {
      const input = `1. First


2. Second


3. Third`
      const tokens = tokenize(input)
      
      expect(tokens).toHaveLength(1)
      expect(tokens[0].type).toBe('list')
      if (tokens[0].type === 'list') {
        expect(tokens[0].ordered).toBe(true)
        expect(tokens[0].items).toHaveLength(3)
      }
    })

    it('ends list when followed by non-list content', () => {
      const input = `1. First item
2. Second item

This is a paragraph.`
      const tokens = tokenize(input)
      
      expect(tokens).toHaveLength(2)
      expect(tokens[0].type).toBe('list')
      expect(tokens[1].type).toBe('paragraph')
    })
  })

  describe('unordered lists', () => {
    it('handles loose unordered list (blank lines between items)', () => {
      const input = `- First item

- Second item

- Third item`
      const tokens = tokenize(input)
      
      expect(tokens).toHaveLength(1)
      expect(tokens[0].type).toBe('list')
      if (tokens[0].type === 'list') {
        expect(tokens[0].ordered).toBe(false)
        expect(tokens[0].items).toHaveLength(3)
      }
    })
  })
})

describe('Markdown renderer', () => {
  it('renders ordered list with correct numbering', () => {
    const input = `1. First

2. Second

3. Third

4. Fourth`
    const tokens = tokenize(input)
    const html = renderTokens(tokens)
    
    // Should be a single <ol> with 4 <li> items
    expect(html).toContain('<ol>')
    expect(html).toContain('</ol>')
    expect((html.match(/<li>/g) || []).length).toBe(4)
    expect((html.match(/<ol>/g) || []).length).toBe(1)
  })

  it('preserves special characters in list items', () => {
    const input = `1. Check if member id < 0
2. Check if value > 100`
    const tokens = tokenize(input)
    const html = renderTokens(tokens)
    
    // < and > should be escaped
    expect(html).toContain('&lt;')
    expect(html).toContain('&gt;')
  })
})
