import type { ReactNode } from "react"

interface StarSectionProps {
  title: string
  aside: ReactNode
  bare?: boolean
  children: ReactNode
}

// Bare sections sit inside another card, so they drop the card chrome and use a smaller heading.
export function StarSection({ title, aside, bare = false, children }: StarSectionProps) {
  if (bare) {
    return (
      <section className="star-subsection">
        <div className="card-head row-split">
          <h3>{title}</h3>
          {aside}
        </div>
        {children}
      </section>
    )
  }

  return (
    <article className="card">
      <div className="card-head row-split">
        <h2>{title}</h2>
        {aside}
      </div>
      {children}
    </article>
  )
}
