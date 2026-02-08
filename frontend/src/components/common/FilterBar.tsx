import './FilterBar.css'

interface FilterOption {
  value: string
  label: string
}

interface FilterDef {
  key: string
  label: string
  options: FilterOption[]
  value: string
}

interface Props {
  filters: FilterDef[]
  onChange: (key: string, value: string) => void
}

export default function FilterBar({ filters, onChange }: Props) {
  return (
    <div className="filter-bar">
      {filters.map(f => (
        <div key={f.key} className="filter-group">
          <label className="filter-label">{f.label}</label>
          <select
            className="filter-select"
            value={f.value}
            onChange={e => onChange(f.key, e.target.value)}
          >
            {f.options.map(o => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </div>
      ))}
    </div>
  )
}
