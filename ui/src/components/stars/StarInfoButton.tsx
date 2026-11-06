interface StarInfoButtonProps {
  label: string
  onClick: () => void
}

export function StarInfoButton({ label, onClick }: StarInfoButtonProps) {
  return (
    <button
      type="button"
      className="info-button"
      aria-label={label}
      title={label}
      onClick={(event) => {
        event.stopPropagation()
        onClick()
      }}
    >
      i
    </button>
  )
}
