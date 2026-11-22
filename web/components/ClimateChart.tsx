import { climateSummary, rainAxis, scale, temperatureAxis, twelveMonths } from "@/lib/data/chart";
import { MONTHS, formatShare } from "@/lib/data/helpers";
import type { ClimateMonth } from "@/lib/data/types";

// Hand-built SVG chart (no chart library, no client JavaScript): average daily maximum and
// minimum temperature per month as lines (left axis, degrees C) over average rainy days as bars
// (right axis), with the best months highlighted. The same numbers are in a table below.
// Colours come from CSS classes (globals.css), so they follow the light and dark themes.
//
// Responsive without JavaScript: the chart is drawn twice from the same data, in a wide layout
// and a compact one (narrower coordinate system, so text stays readable on a phone; one-letter
// month names). CSS shows exactly one; the other is display:none, so assistive technology meets
// a single chart.

type Layout = {
  name: "wide" | "compact";
  w: number;
  h: number;
  margin: { top: number; right: number; bottom: number; left: number };
  monthLabel: (month: number) => string;
  marker: number; // marker radius
};

const LAYOUTS: Layout[] = [
  {
    name: "wide",
    w: 640,
    h: 310,
    margin: { top: 30, right: 44, bottom: 34, left: 44 },
    monthLabel: (m) => MONTHS[m - 1].slice(0, 3),
    marker: 4.5,
  },
  {
    name: "compact",
    w: 360,
    h: 250,
    margin: { top: 28, right: 30, bottom: 28, left: 32 },
    monthLabel: (m) => MONTHS[m - 1].slice(0, 1),
    marker: 3.5,
  },
];

const fmt1 = (v: number | null) => (v === null ? "-" : v.toFixed(1));

type ChartProps = {
  layout: Layout;
  cityId: string;
  cityName: string;
  months: ClimateMonth[];
  bestMonths: string[];
  summary: string;
};

function ChartSvg({ layout, cityId, cityName, months, bestMonths, summary }: ChartProps) {
  const { w, h, margin: m } = layout;
  const innerW = w - m.left - m.right;
  const innerH = h - m.top - m.bottom;
  const band = innerW / 12;
  const tAxis = temperatureAxis(months);
  const rAxis = rainAxis(months);
  const yT = scale(tAxis, m.top + innerH, m.top);
  const yR = scale(rAxis, m.top + innerH, m.top);
  const x = (month: number) => m.left + band * (month - 0.5);
  const isBest = (month: number) => bestMonths.includes(MONTHS[month - 1]);
  const titleId = `climate-title-${cityId}-${layout.name}`;
  const descId = `climate-desc-${cityId}-${layout.name}`;

  const line = (key: "avg_temp_max_c" | "avg_temp_min_c") => {
    // Separate segments around months without data, so a gap is not drawn as a line.
    const segments: string[] = [];
    let current: string[] = [];
    for (const row of months) {
      const v = row[key];
      if (v === null) {
        if (current.length) segments.push(current.join(" "));
        current = [];
      } else {
        current.push(`${current.length ? "L" : "M"}${x(row.month).toFixed(1)},${yT(v).toFixed(1)}`);
      }
    }
    if (current.length) segments.push(current.join(" "));
    return segments.join(" ");
  };
  const r = layout.marker;

  return (
    <svg
      className={`climate-chart climate-chart-${layout.name}`}
      viewBox={`0 0 ${w} ${h}`}
      role="img"
      aria-labelledby={`${titleId} ${descId}`}
      data-testid={layout.name === "wide" ? "climate-chart" : "climate-chart-compact"}
    >
      <title id={titleId}>Climate by month in {cityName}</title>
      <desc id={descId}>{summary}</desc>

      {/* Best months: background bands */}
      {months
        .filter((row) => isBest(row.month))
        .map((row) => (
          <rect
            key={`best-${row.month}`}
            className="chart-best"
            x={m.left + band * (row.month - 1)}
            y={m.top}
            width={band}
            height={innerH}
          />
        ))}

      {/* Grid with the temperature axis (left) and the rainy-day axis (right) */}
      {tAxis.ticks.map((t) => (
        <g key={`t-${t}`}>
          <line className="chart-grid" x1={m.left} x2={w - m.right} y1={yT(t)} y2={yT(t)} />
          <text className="chart-tick" x={m.left - 6} y={yT(t)} textAnchor="end" dominantBaseline="middle">
            {t}°
          </text>
        </g>
      ))}
      {rAxis.ticks.map((d) => (
        <text key={`r-${d}`} className="chart-tick" x={w - m.right + 6} y={yR(d)} dominantBaseline="middle">
          {d}
        </text>
      ))}
      {/* Axis titles above the plot, clear of the top tick labels */}
      <text className="chart-axis-label" x={m.left - 6} y={m.top - 16} textAnchor="end">
        °C
      </text>
      <text className="chart-axis-label" x={w - m.right + 6} y={m.top - 16}>
        days
      </text>

      {/* Rainy days: bars */}
      {months.map((row) =>
        row.avg_rainy_days === null ? null : (
          <rect
            key={`rain-${row.month}`}
            className="chart-rain"
            x={x(row.month) - band * 0.3}
            y={yR(row.avg_rainy_days)}
            width={band * 0.6}
            height={Math.max(0, yR(rAxis.min) - yR(row.avg_rainy_days))}
            rx={2}
          />
        ),
      )}

      {/* Temperatures: lines with markers (circles for max, squares for min), with halos */}
      <path className="chart-halo" d={line("avg_temp_max_c")} />
      <path className="chart-halo" d={line("avg_temp_min_c")} />
      <path className="chart-line chart-max" d={line("avg_temp_max_c")} />
      <path className="chart-line chart-min" d={line("avg_temp_min_c")} />
      {months.map((row) =>
        row.avg_temp_max_c === null ? null : (
          <circle
            key={`max-${row.month}`}
            className="chart-max-point"
            cx={x(row.month)}
            cy={yT(row.avg_temp_max_c)}
            r={r}
          />
        ),
      )}
      {months.map((row) =>
        row.avg_temp_min_c === null ? null : (
          <rect
            key={`min-${row.month}`}
            className="chart-min-point"
            x={x(row.month) - r}
            y={yT(row.avg_temp_min_c) - r}
            width={r * 2}
            height={r * 2}
          />
        ),
      )}

      {/* Month labels; best months in bold */}
      {months.map((row) => (
        <text
          key={`label-${row.month}`}
          className={`chart-month${isBest(row.month) ? " chart-month-best" : ""}`}
          x={x(row.month)}
          y={h - m.bottom + 18}
          textAnchor="middle"
        >
          {layout.monthLabel(row.month)}
        </text>
      ))}
    </svg>
  );
}

export function ClimateChart({
  cityId,
  cityName,
  climate,
  bestMonths,
}: {
  cityId: string;
  cityName: string;
  climate: ClimateMonth[];
  bestMonths: string[];
}) {
  const months = twelveMonths(climate, cityId);
  if (!months.some((row) => row.avg_temp_max_c !== null)) return <p>No climate data yet.</p>;
  const summary = climateSummary(cityName, months, bestMonths);
  const isBest = (month: number) => bestMonths.includes(MONTHS[month - 1]);
  const years = Math.max(0, ...months.map((row) => row.years_covered ?? 0));

  return (
    <figure className="climate">
      {LAYOUTS.map((layout) => (
        <ChartSvg
          key={layout.name}
          layout={layout}
          cityId={cityId}
          cityName={cityName}
          months={months}
          bestMonths={bestMonths}
          summary={summary}
        />
      ))}

      <figcaption>
        <ul className="chart-legend">
          <li>
            <span className="legend-swatch legend-max" aria-hidden="true" /> Average high (°C)
          </li>
          <li>
            <span className="legend-swatch legend-min" aria-hidden="true" /> Average low (°C)
          </li>
          <li>
            <span className="legend-swatch legend-rain" aria-hidden="true" /> Rainy days (1 mm or more)
          </li>
          {bestMonths.length > 0 && (
            <li>
              <span className="legend-swatch legend-best" aria-hidden="true" /> Best months
            </li>
          )}
        </ul>
        <p className="small muted">
          Averages over {years} years of daily weather history (Open-Meteo).
        </p>
      </figcaption>

      <details className="chart-table">
        <summary>Show the data as a table</summary>
        <div className="table-wrap">
          <table>
            <caption className="visually-hidden">Monthly climate in {cityName}</caption>
            <thead>
              <tr>
                <th scope="col">Month</th>
                <th scope="col">Avg high (°C)</th>
                <th scope="col">Avg low (°C)</th>
                <th scope="col">Rainy days</th>
                <th scope="col">Pleasant days</th>
              </tr>
            </thead>
            <tbody>
              {months.map((row) => (
                <tr key={row.month} className={isBest(row.month) ? "row-best" : undefined}>
                  <th scope="row">
                    {MONTHS[row.month - 1]}
                    {isBest(row.month) && <span className="visually-hidden"> (best month)</span>}
                  </th>
                  <td>{fmt1(row.avg_temp_max_c)}</td>
                  <td>{fmt1(row.avg_temp_min_c)}</td>
                  <td>{fmt1(row.avg_rainy_days)}</td>
                  <td>{row.pleasant_day_share === null ? "-" : formatShare(row.pleasant_day_share)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}
