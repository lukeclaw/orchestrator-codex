import { Component, type ReactNode } from 'react'

interface Props {
  children: ReactNode
  fallback?: ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
}

export default class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error('ErrorBoundary caught an error:', error, errorInfo)
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback
      }
      return (
        <div style={{
          padding: '40px',
          textAlign: 'center',
          color: 'var(--text-secondary)',
        }}>
          <h2 style={{ color: 'var(--red)', marginBottom: '16px' }}>Something went wrong</h2>
          <p style={{ marginBottom: '16px' }}>
            {this.state.error?.message || 'An unexpected error occurred'}
          </p>
          <button
            className="btn btn-primary"
            onClick={() => {
              this.setState({ hasError: false, error: null })
              window.location.reload()
            }}
          >
            Reload Page
          </button>
        </div>
      )
    }

    return this.props.children
  }
}
