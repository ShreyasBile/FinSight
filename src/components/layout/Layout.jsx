import { useState, useEffect } from "react";
import Sidebar from "./Sidebar.jsx";
import Topbar from "./Topbar.jsx";

export default function Layout({ role, page, setPage, user, session, title, onLogout, onSearch, children }) {
  const [navOpen, setNavOpen] = useState(false);

  // Close the mobile drawer whenever the page changes.
  useEffect(() => { setNavOpen(false); }, [page]);

  return (
    <div className="app">
      <Sidebar
        role={role} page={page} setPage={setPage} user={user} session={session} onLogout={onLogout}
        open={navOpen} onClose={() => setNavOpen(false)}
      />
      <div className="main">
        <Topbar
          title={title} role={role} user={user} onLogout={onLogout}
          onNotifClick={() => setPage("notifications")}
          onMenuClick={() => setNavOpen(true)}
          onSearch={onSearch}
        />
        <div className="content">{children}</div>
      </div>
    </div>
  );
}
