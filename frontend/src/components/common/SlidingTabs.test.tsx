import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import SlidingTabs from './SlidingTabs'

describe('SlidingTabs', () => {
  it('renders tab buttons as non-submit buttons', () => {
    const html = renderToStaticMarkup(
      <form>
        <SlidingTabs
          tabs={[
            { value: 'claude', label: 'Claude' },
            { value: 'codex', label: 'Codex' },
          ]}
          value="claude"
          onChange={() => {}}
        />
      </form>,
    )

    expect(html).toContain('type="button"')
  })
})
