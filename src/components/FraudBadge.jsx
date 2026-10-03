export default function FraudBadge({ level }) {
  const map = { None:"badge-green", Low:"badge-blue", Medium:"badge-amber", High:"badge-red" };
  // "None" = assessed and nothing found; "Not assessed" = the fraud agents have not run yet
  return <span className={`badge ${map[level]||"badge-gray"}`}>{level==="None"?"✓ Clean":level}</span>;
}
