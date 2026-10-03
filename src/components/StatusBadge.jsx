export default function StatusBadge({ status }) {
  const map = {
    "Approved":         "badge-green",
    "Rejected":         "badge-red",
    "Under Review":     "badge-amber",
    "Pending Docs":     "badge-blue",
    "Legal Review":     "badge-purple",
    "Pending Approval": "badge-blue",
    "Disbursed":        "badge-green",
    "Closed":           "badge-gray",
  };
  return <span className={`badge ${map[status]||"badge-gray"}`}>{status}</span>;
}
