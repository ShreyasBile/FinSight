import { Component } from "react";
import { Icon, Icons } from "../icons/Icons.jsx";

// Top-level safety net. Without this, any uncaught render error anywhere in
// the tree white-screens the whole app with no way back except a hard
// browser refresh (which also loses in-progress application state — see
// UserNewApplication.jsx, which has none of its own persistence either).
// "Try again" re-renders the tree from scratch; "Start over" clears state so
// a broken page doesn't just immediately re-throw the same error.
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error("Unhandled error in FinSight UI:", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div style={{
        minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center",
        padding: 24, fontFamily: "var(--font-sans)", background: "var(--surface1, #f7f7f9)",
      }}>
        <div className="card" style={{ maxWidth: 440, textAlign: "center", padding: 32 }}>
          <Icon d={Icons.alert} size={32} style={{ color: "var(--red)", marginBottom: 14 }} />
          <div style={{ fontSize: 16, fontWeight: 600, color: "var(--text1)", marginBottom: 8 }}>
            Something went wrong
          </div>
          <div style={{ fontSize: 13, color: "var(--text3)", marginBottom: 20, lineHeight: 1.6 }}>
            This screen hit an unexpected error. Your in-progress application data may not have been saved.
          </div>
          <div style={{ display: "flex", gap: 10, justifyContent: "center" }}>
            <button className="btn btn-ghost" onClick={() => this.setState({ error: null })}>
              <Icon d={Icons.refresh} size={14} /> Try again
            </button>
            <button className="btn btn-primary" onClick={() => window.location.reload()}>
              Start over
            </button>
          </div>
        </div>
      </div>
    );
  }
}
