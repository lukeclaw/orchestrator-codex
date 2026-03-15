interface StatusDotProps {
  status: string
  size?: 'sm' | 'md'
  className?: string
}

export default function StatusDot({ status, size = 'md', className = '' }: StatusDotProps) {
  return <span className={`status-dot ${status}${size === 'sm' ? ' sm' : ''}${className ? ` ${className}` : ''}`} />
}
