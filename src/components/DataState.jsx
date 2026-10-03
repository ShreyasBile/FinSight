import { Icon, Icons } from "../icons/Icons.jsx";

/**
 * Renders the loading / error / empty states around API-backed content.
 *
 * The point of this existing at all: now that screens read from SQL Server, an
 * empty table can mean three very different things — still loading, the
 * backend is down, or there genuinely are no rows. Showing the same blank grid
 * for all three is how a broken connection gets mistaken for "no applications".
 *
 *   <DataState loading={loading} error={error} empty={!rows.length} onRetry={reload}>
 *     …content…
 *   </DataState>
 */
export default function DataState({
  loading, error, empty, onRetry, children,
  emptyIcon = Icons.clipboardList,
  emptyTitle = "Nothing here yet",
  emptySub = "",
  emptyAction = null,
  label = "data",
}) {
  if (loading) {
    return (
      <div className="card" style={{ textAlign: "center", padding: "48px 24px" }}>
        <div className="spinner" style={{ margin: "0 auto 14px", width: 22, height: 22,
                                          color: "var(--blue)" }} />
        <div style={{ fontSize: 13, color: "var(--text3)" }}>Loading {label}…</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="card" style={{ padding: "32px 24px", textAlign: "center" }}>
        <Icon d={Icons.alert} size={30} style={{ color: "var(--red)", marginBottom: 12 }} />
        <div style={{ fontSize: 14, fontWeight: 600, color: "var(--text1)", marginBottom: 6 }}>
          Couldn't load {label}
        </div>
        <div style={{ fontSize: 12, color: "var(--text3)", lineHeight: 1.6, maxWidth: 460,
                      margin: "0 auto 18px" }}>
          {error}
        </div>
        {onRetry && (
          <button className="btn btn-secondary" onClick={onRetry}>
            <Icon d={Icons.refresh} size={13} /> Retry
          </button>
        )}
      </div>
    );
  }

  if (empty) {
    return (
      <div className="card" style={{ textAlign: "center", padding: "48px 24px" }}>
        <Icon d={emptyIcon} size={38} style={{ color: "var(--text3)", marginBottom: 12 }} />
        <div style={{ fontSize: 15, fontWeight: 600, color: "var(--text1)", marginBottom: 6 }}>
          {emptyTitle}
        </div>
        {emptySub && (
          <div style={{ fontSize: 12, color: "var(--text3)", marginBottom: emptyAction ? 20 : 0 }}>
            {emptySub}
          </div>
        )}
        {emptyAction}
      </div>
    );
  }

  return children;
}
