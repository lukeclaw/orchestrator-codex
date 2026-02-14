import './RdevTable.css'

interface Rdev {
  name: string
  state: string
  cluster: string
  created: string
  last_accessed: string
  in_use: boolean
  worker_name?: string
  worker_status?: string
}

interface Props {
  rdevs: Rdev[]
  onDelete: (name: string) => void
  onRestart: (name: string) => void
  onStop: (name: string) => void
  actionLoading: string | null
}

export default function RdevTable({ rdevs, onDelete, onRestart, onStop, actionLoading }: Props) {
  if (!rdevs.length) {
    return <p className="empty-state">No rdevs found</p>
  }

  return (
    <div className="rdev-table-wrapper">
      <table className="rdev-table">
        <thead>
          <tr>
            <th>State</th>
            <th>Name</th>
            <th>Worker</th>
            <th>Cluster</th>
            <th>Last Accessed</th>
            <th>Created</th>
            <th className="th-actions">Actions</th>
          </tr>
        </thead>
        <tbody>
          {rdevs.map(rdev => {
            const isRunning = rdev.state === 'RUNNING'
            const isStopped = rdev.state === 'STOPPED'
            const isLoading = actionLoading === rdev.name

            return (
              <tr key={rdev.name} className="rt-row">
                <td>
                  <span className={`state-badge state-${rdev.state.toLowerCase()}`}>
                    {rdev.state}
                  </span>
                </td>
                <td className="rt-name">{rdev.name}</td>
                <td>
                  {rdev.in_use && rdev.worker_name ? (
                    <div className="worker-info">
                      <span className="worker-name">{rdev.worker_name}</span>
                      {rdev.worker_status && (
                        <span className={`worker-status-badge status-${rdev.worker_status}`}>
                          {rdev.worker_status}
                        </span>
                      )}
                    </div>
                  ) : (
                    <span className="rt-empty">—</span>
                  )}
                </td>
                <td className="rt-cluster">{rdev.cluster || '—'}</td>
                <td className="rt-time">{rdev.last_accessed || '—'}</td>
                <td className="rt-time">{rdev.created || '—'}</td>
                <td className="rt-actions">
                  {isRunning && (
                    <button
                      className="btn-action btn-stop"
                      onClick={() => onStop(rdev.name)}
                      disabled={isLoading}
                      title="Stop rdev"
                    >
                      {isLoading ? '...' : 'Stop'}
                    </button>
                  )}
                  {isStopped && (
                    <button
                      className="btn-action btn-restart"
                      onClick={() => onRestart(rdev.name)}
                      disabled={isLoading}
                      title="Restart rdev"
                    >
                      {isLoading ? '...' : 'Restart'}
                    </button>
                  )}
                  <button
                    className="btn-action btn-delete"
                    onClick={() => onDelete(rdev.name)}
                    disabled={isLoading || rdev.in_use}
                    title={rdev.in_use ? 'Remove worker first' : 'Delete rdev'}
                  >
                    Delete
                  </button>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
