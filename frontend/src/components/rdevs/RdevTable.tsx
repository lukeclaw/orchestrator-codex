import { Link } from 'react-router-dom'
import { IconStop, IconTrash, IconPlay } from '../common/Icons'
import ConfirmPopover from '../common/ConfirmPopover'
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
  worker_id?: string
}

export type RdevSortKey = 'state' | 'name' | 'worker' | 'cluster' | 'last_accessed' | 'created'
export type SortDir = 'asc' | 'desc'

interface Props {
  rdevs: Rdev[]
  onDelete: (name: string) => void
  onRestart: (name: string) => void
  onStop: (name: string) => void
  actionLoading: string | null
  sortKey: RdevSortKey
  sortDir: SortDir
  onSort: (key: RdevSortKey) => void
}

export default function RdevTable({ rdevs, onDelete, onRestart, onStop, actionLoading, sortKey, sortDir, onSort }: Props) {
  if (!rdevs.length) {
    return <p className="empty-state">No rdevs found</p>
  }

  function SortHeader({ k, children }: { k: RdevSortKey; children: React.ReactNode }) {
    const active = sortKey === k
    return (
      <th className={`rt-th sortable ${active ? 'active' : ''}`} onClick={() => onSort(k)}>
        {children}
        {active && <span className="sort-arrow">{sortDir === 'asc' ? '↑' : '↓'}</span>}
      </th>
    )
  }

  return (
    <div className="rdev-table-wrapper">
      <table className="rdev-table">
        <thead>
          <tr>
            <SortHeader k="state">State</SortHeader>
            <SortHeader k="name">Name</SortHeader>
            <SortHeader k="worker">Worker</SortHeader>
            <SortHeader k="cluster">Cluster</SortHeader>
            <SortHeader k="last_accessed">Last Accessed</SortHeader>
            <SortHeader k="created">Created</SortHeader>
            <th className="th-actions">Actions</th>
          </tr>
        </thead>
        <tbody>
          {rdevs.map(rdev => {
            const isRunning = rdev.state === 'RUNNING'
            const isStopped = rdev.state === 'STOPPED'
            const isLoading = actionLoading === rdev.name

            return (
              <tr key={rdev.name} className={`rt-row rt-state-${rdev.state.toLowerCase()}`}>
                <td>
                  <span className={`state-badge state-${rdev.state.toLowerCase()}`}>
                    {rdev.state}
                  </span>
                </td>
                <td className="rt-name">{rdev.name}</td>
                <td>
                  {rdev.in_use && rdev.worker_name && rdev.worker_id ? (
                    <Link to={`/workers/${rdev.worker_id}`} className="worker-tag-link">
                      <span className={`pt-worker-tag ${rdev.worker_status || 'idle'}`} title={`${rdev.worker_name} (${rdev.worker_status || 'idle'})`}>
                        {rdev.worker_name}
                      </span>
                    </Link>
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
                      className="rt-action-btn stop"
                      onClick={() => onStop(rdev.name)}
                      disabled={isLoading}
                      title="Stop rdev"
                    >
                      <IconStop size={14} />
                    </button>
                  )}
                  {isStopped && (
                    <button
                      className="rt-action-btn restart"
                      onClick={() => onRestart(rdev.name)}
                      disabled={isLoading}
                      title="Start rdev"
                    >
                      <IconPlay size={14} />
                    </button>
                  )}
                  <ConfirmPopover
                    message={`Delete rdev "${rdev.name}"? This cannot be undone.`}
                    confirmLabel="Delete"
                    onConfirm={() => onDelete(rdev.name)}
                    variant="danger"
                  >
                    {({ onClick }) => (
                      <button
                        className="rt-action-btn delete"
                        onClick={onClick}
                        disabled={isLoading || rdev.in_use}
                        title={rdev.in_use ? 'Remove worker first' : 'Delete rdev'}
                      >
                        <IconTrash size={14} />
                      </button>
                    )}
                  </ConfirmPopover>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
