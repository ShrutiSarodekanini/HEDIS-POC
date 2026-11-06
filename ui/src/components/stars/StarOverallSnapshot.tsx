import type { StarOverview } from "../../models/stars"
import { formatInteger } from "../../utils/format"
import { StarSection } from "./StarSection"

interface StarOverallSnapshotProps {
  overview: StarOverview | null
  bare?: boolean
}

export function StarOverallSnapshot({ overview, bare }: StarOverallSnapshotProps) {
  return (
    <StarSection
      title="Overall snapshot"
      bare={bare}
      aside={
        <span className="note">
          Measurement year {overview?.measurementYear ?? "-"}
          {overview?.cmsRatingYear ? ` | CMS rating year ${overview.cmsRatingYear}` : ""}
        </span>
      }
    >

      <div className="kpi-grid four">
        <article className="kpi-card">
          <span>Rated measures</span>
          <strong>
            {`${formatInteger(overview?.overall?.ratedMeasures ?? 0)}${overview?.overall?.totalMeasures != null
              ? ` / ${formatInteger(overview.overall.totalMeasures)}`
              : ""}`}
          </strong>
        </article>
        <article className="kpi-card">
          <span>Baseline overall star</span>
          <strong>{overview?.overall?.baselineStar?.toFixed(2) ?? "-"}</strong>
        </article>
        <article className="kpi-card">
          <span>Projected overall star</span>
          <strong>{overview?.overall?.projectedStar?.toFixed(2) ?? "-"}</strong>
        </article>
        <article className="kpi-card">
          <span>Expected change</span>
          <strong className="good-text">
            {overview?.overall?.delta == null ? "-" : `${overview.overall.delta >= 0 ? "+" : ""}${overview.overall.delta.toFixed(2)}`}
          </strong>
        </article>
      </div>

      <p className="note">
        {overview?.overall?.ratedMeasuresWithData != null && overview?.overall?.ratedMeasures != null
          ? `Overall stars use weighted CMS measure stars for ${formatInteger(overview.overall.ratedMeasuresWithData)} rated measures with data.`
          : ""}
        {overview?.overall?.ratedMeasuresWithData != null && overview?.overall?.ratedMeasures != null ? " " : ""}
        {overview?.catalogUrl ? (
          <>
            Measure set: <a className="text-link" href={overview.catalogUrl} target="_blank" rel="noreferrer">CMS reference</a>
          </>
        ) : (
          "Catalog URL is not available in this backend profile."
        )}
        {overview?.notesUrl ? (
          <>
            {" "}| Technical notes: <a className="text-link" href={overview.notesUrl} target="_blank" rel="noreferrer">details</a>
          </>
        ) : null}
      </p>
    </StarSection>
  )
}
