import './ProviderBadge.css'

const PROVIDER_LABELS: Record<string, string> = {
  claude: 'Claude',
  codex: 'Codex',
}

export function normalizeProviderId(provider: string | null | undefined): string {
  return (provider || '').trim().toLowerCase()
}

export function getProviderLabel(provider: string | null | undefined): string {
  const normalized = normalizeProviderId(provider)
  if (!normalized) return 'Unknown'
  return PROVIDER_LABELS[normalized] || normalized.charAt(0).toUpperCase() + normalized.slice(1)
}

interface ProviderBadgeProps {
  provider: string | null | undefined
  compact?: boolean
  className?: string
  title?: string
}

export default function ProviderBadge({
  provider,
  compact = false,
  className = '',
  title,
}: ProviderBadgeProps) {
  const normalized = normalizeProviderId(provider)
  if (!normalized) return null

  const label = getProviderLabel(normalized)
  const classes = [
    'provider-badge',
    `provider-badge--${normalized}`,
    compact ? 'provider-badge--compact' : '',
    className,
  ].filter(Boolean).join(' ')

  return (
    <span
      className={classes}
      data-provider={normalized}
      title={title || `Provider: ${label}`}
    >
      {label}
    </span>
  )
}
