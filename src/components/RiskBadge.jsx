export default function RiskBadge({ score }) {
  if (score === null || score === undefined) return <span className="badge badge-gray">Not scored</span>;
  const cls = score<40?"badge-green":score<65?"badge-amber":score<80?"badge-red":"badge-red";
  const label = score<40?"Low":score<65?"Medium":score<80?"High":"Very High";
  return <span className={`badge ${cls}`}>{label} {score}</span>;
}
