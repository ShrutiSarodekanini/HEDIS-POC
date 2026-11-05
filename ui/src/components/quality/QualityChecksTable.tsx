import type { QualityCheck } from "../../models/quality"
import { formatInteger } from "../../utils/format"
import { formatMetricValue, humanize, toRunStatusClass } from "./qualityDisplay"

const MAX_CHECK_ROWS = 200

export function QualityChecksTable({ checks }: { checks: QualityCheck[] }) {
  return (
    <>
      <div className="dq-scroll">
        <table className="compact">
          <thead>
            <tr>
              <th>Check</th>
              <th>Status</th>
              <th>Metric</th>
              <th className="r">Value</th>
              <th className="r">Threshold</th>
              <th>Resource type</th>
            </tr>
          </thead>
          <tbody>
            {checks.slice(0, MAX_CHECK_ROWS).map((item) => (
              <tr key={`${item.check_name}-${item.metric_name ?? "metric"}-${item.measure_id ?? "all"}`}>
                <td>{humanize(item.check_name)}</td>
                <td><span className={toRunStatusClass(item.status)}>{humanize(item.status)}</span></td>
                <td className="mono small">{item.metric_name ?? "-"}</td>
                <td className="r">{formatMetricValue(item.metric_value)}</td>
                <td className="r">{formatMetricValue(item.threshold_value)}</td>
                <td>{item.resource_type ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {checks.length > MAX_CHECK_ROWS ? (
        <p className="note small">
          and {formatInteger(checks.length - MAX_CHECK_ROWS)} more
        </p>
      ) : null}
    </>
  )
}
