import type { PrPreviewData } from '../../api/types'

export const GH_PR_RE = /github\.com\/[^/]+\/([^/]+)\/pull\/(\d+)/

export function isPrUrl(url: string): boolean {
  return GH_PR_RE.test(url)
}

export function prLinkLabel(url: string): string {
  const m = url.match(GH_PR_RE)
  return m ? `${m[1]} #${m[2]}` : url
}

export const APPROVAL_GATE_RE = /approval/i

export function getPrStatusChips(data: PrPreviewData): Array<{ label: string; color: string }> {
  const chips: Array<{ label: string; color: string }> = []

  if (data.state === 'merged') {
    chips.push({ label: 'Merged', color: 'purple' })
  } else if (data.state === 'closed') {
    chips.push({ label: 'Closed', color: 'red' })
  } else {
    if (data.draft) {
      chips.push({ label: 'Draft', color: 'gray' })
    } else {
      chips.push({ label: 'Open', color: 'green' })
    }

    // Check for approval gate in CI checks — when present, it's the
    // authoritative signal and we suppress review-based approval chips
    // to avoid contradictions (e.g. reviewer approved an old commit but
    // the gate reset to pending after new commits were pushed).
    const approvalGate = data.checks?.find(c => APPROVAL_GATE_RE.test(c.name))

    if (approvalGate) {
      if (approvalGate.conclusion === 'success') {
        chips.push({ label: 'Owner approved', color: 'green' })
      } else if (approvalGate.status === 'in_progress' || approvalGate.status === 'queued' || !approvalGate.conclusion) {
        chips.push({ label: 'Owner approval pending', color: 'yellow' })
      }
    } else if (data.reviews && data.reviews.length > 0) {
      // No approval gate — fall back to review-based approval status
      const hasApproval = data.reviews.some(r => r.state === 'approved')
      const hasChangesRequested = data.reviews.some(r => r.state === 'changes_requested')
      if (hasChangesRequested) {
        chips.push({ label: 'Changes requested', color: 'red' })
      } else if (hasApproval) {
        chips.push({ label: 'Approved', color: 'green' })
      }
    }

    if (data.checks && data.checks.length > 0) {

      // Filter out approval gates and skipped/cancelled/neutral checks (same as PrPreviewCard)
      const skippedConclusions = new Set(['cancelled', 'skipped', 'neutral'])
      const ciChecks = data.checks.filter(c => !APPROVAL_GATE_RE.test(c.name))
      const relevantChecks = ciChecks.filter(c => !skippedConclusions.has(c.conclusion ?? ''))

      if (relevantChecks.length > 0) {
        const anyFailed = relevantChecks.some(c => c.conclusion === 'failure' || c.conclusion === 'timed_out')
        const anyRunning = relevantChecks.some(c => c.status === 'in_progress')

        if (anyFailed) {
          chips.push({ label: 'CI failing', color: 'red' })
        } else if (anyRunning) {
          chips.push({ label: 'CI running', color: 'yellow' })
        }
      }
    }
  }

  return chips
}
