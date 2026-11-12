import { useEffect, useId, useState } from 'react'
import { talentApi } from '../api/talentClient'
import { useDebounced } from '../components/ui'

// A type-to-search filter: free text, with the most common matching values offered as you type.
// The value is committed after a short pause, so the list isn't reloaded on every key press.
export default function TypeaheadInput({ field, value, onCommit, placeholder, label }) {
  const listId = useId()
  const [text, setText] = useState(value)
  const [options, setOptions] = useState([])
  const debounced = useDebounced(text, 350)

  useEffect(() => setText(value), [value])

  useEffect(() => {
    if (debounced !== value) onCommit(debounced)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced])

  useEffect(() => {
    // Skills can hold several comma-separated terms: suggest for the one being typed.
    const term = (field === 'skills' ? debounced.split(',').pop() : debounced).trim()
    if (!term) {
      setOptions([])
      return
    }
    let stale = false
    talentApi(`/talent/suggest?${new URLSearchParams({ field, q: term })}`)
      .then((data) => !stale && setOptions(data.items))
      .catch(() => !stale && setOptions([]))
    return () => {
      stale = true
    }
  }, [debounced, field])

  const prefix = field === 'skills' ? text.split(',').slice(0, -1).map((s) => s.trim()).filter(Boolean) : []

  return (
    <>
      <input
        className="talent-filter-input"
        list={listId}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={placeholder}
        aria-label={label}
      />
      <datalist id={listId}>
        {options.map((o) => (
          <option key={o.value} value={[...prefix, o.value].join(', ')}>
            {o.candidates} candidate{o.candidates === 1 ? '' : 's'}
          </option>
        ))}
      </datalist>
    </>
  )
}
