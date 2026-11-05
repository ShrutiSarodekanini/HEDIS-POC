import { StarRatingSections } from "../components/stars/StarRatingSections"
import { useStarsWorkflow } from "../hooks/useStarsWorkflow"

export function StarRatingPage() {
  const workflow = useStarsWorkflow()
  const { loadingSetup, setupError, retrySetup } = workflow

  if (loadingSetup) {
    return (
      <section id="view-stars" className="view">
        <article className="card state-card">
          <h2>Star Rating</h2>
          <p className="card-message">Loading star rating capabilities from backend...</p>
        </article>
      </section>
    )
  }

  return (
    <section id="view-stars" className="view">
      <div className="page-head">
        <div>
          <h1>Star Rating</h1>
          <p>Evaluate current stars, simulate gap closures, and compare before versus after outcomes.</p>
        </div>
      </div>

      {setupError ? (
        <article className="card danger-box">
          <p>
            <strong>Star rating API unavailable:</strong> {setupError}
          </p>
          <div className="actions">
            <button
              type="button"
              className="btn outline"
              onClick={() => {
                void retrySetup()
              }}
            >
              Re-check star endpoints
            </button>
          </div>
        </article>
      ) : null}

      <StarRatingSections workflow={workflow} />
    </section>
  )
}
