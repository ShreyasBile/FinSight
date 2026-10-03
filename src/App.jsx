import { useEffect, useState } from "react";
import "./styles/index.css";
import { api } from "./utils/api.js";
import { ROLES } from "./data/roles.js";
import { DEFAULT_PAGE, TITLES } from "./data/navConfig.js";
import Layout from "./components/layout/Layout.jsx";
import RolePicker from "./pages/RolePicker.jsx";
import UserHome from "./pages/user/UserHome.jsx";
import UserNewApplication from "./pages/user/UserNewApplication.jsx";
import UserApplications from "./pages/user/UserApplications.jsx";
import UserAppDetail from "./pages/user/UserAppDetail.jsx";
import PageAppDetail from "./pages/PageAppDetail.jsx";
import PageCustomer360 from "./pages/PageCustomer360.jsx";
import PageRiskDashboard from "./pages/PageRiskDashboard.jsx";
import PageFraud from "./pages/PageFraud.jsx";
import PagePolicy from "./pages/PagePolicy.jsx";
import PageKnowledgeGraph from "./pages/PageKnowledgeGraph.jsx";
import PageReports from "./pages/PageReports.jsx";
import PageSettings from "./pages/PageSettings.jsx";
import OfficerApplications from "./pages/officer/OfficerApplications.jsx";
import OfficerFraud from "./pages/officer/OfficerFraud.jsx";
import OfficerPolicy from "./pages/officer/OfficerPolicy.jsx";
import OfficerReports from "./pages/officer/OfficerReports.jsx";
import OfficerNotifications from "./pages/officer/OfficerNotifications.jsx";
import OfficerSettings from "./pages/officer/OfficerSettings.jsx";
import HeadDashboard from "./pages/head/HeadDashboard.jsx";
import HeadApplications from "./pages/head/HeadApplications.jsx";
import HeadCompliance from "./pages/head/HeadCompliance.jsx";
import HeadAgentActivity from "./pages/head/HeadAgentActivity.jsx";

export default function App() {
  const [role, setRole] = useState(null);
  const [page, setPage] = useState("dashboard");
  const [globalSearch, setGlobalSearch] = useState("");
  // Who is signed in, resolved from the database. There is no login yet, so
  // picking a role asks the backend which person holds it; every screen then
  // queries with that real user_id / customer_id instead of the frontend
  // inventing an identity.
  const [session, setSession] = useState(null);
  const [sessionError, setSessionError] = useState(null);
  // Which application the detail pages should load. Set by whichever list the
  // user clicked; previously the detail pages always rendered one hardcoded
  // applicant regardless of the row selected.
  const [openApp, setOpenApp] = useState(null);

  useEffect(() => {
    if (!role) { setSession(null); setSessionError(null); return; }
    let stale = false;
    api.session(role)
      .then(s => { if (!stale) { setSession(s); setSessionError(null); } })
      .catch(e => { if (!stale) { setSession(null); setSessionError(e.message); } });
    return () => { stale = true; };
  }, [role]);

  const handleSelectRole = r => { setRole(r); setPage(DEFAULT_PAGE[r]); };
  const handleLogout = () => { setRole(null); };
  // Topbar search is global across pages; the only real search targets today
  // are the applications lists, so it navigates there and seeds their query.
  const handleSearch = q => { setGlobalSearch(q); setPage(role === "user" ? "my-applications" : "applications"); };

  if (!role) return <RolePicker onSelect={handleSelectRole}/>;

  // The signed-in person's real name comes from ops.users; ROLES only supplies
  // the role label and avatar styling.
  // name and initials of the person the API signed in (the role table only holds defaults)
  const initials = (n) => String(n || "").split(/\s+/).filter(Boolean).map(w => w[0]).join("").slice(0, 2).toUpperCase();
  const user = { ...ROLES[role], ...(session ? { name: session.full_name, avatar: initials(session.full_name) || ROLES[role]?.avatar } : {}) };
  const titles = TITLES[role];

  if (sessionError) {
    return (
      <div style={{padding:40,maxWidth:640,margin:"60px auto",textAlign:"center"}}>
        <div style={{fontSize:16,fontWeight:600,color:"var(--text1)",marginBottom:8}}>
          Can't reach the lending database
        </div>
        <div style={{fontSize:13,color:"var(--text3)",lineHeight:1.6,marginBottom:20}}>
          {sessionError}
        </div>
        <div style={{fontSize:12,color:"var(--text3)",lineHeight:1.7,textAlign:"left",
                     background:"var(--surface2)",borderRadius:8,padding:"14px 16px"}}>
          Every screen reads from SQL Server. To bring it back:
          <div style={{marginTop:8}}>
            1. Close the two FinSight windows in the taskbar (or run <span style={{fontFamily:"var(--font-mono)",fontSize:11}}>launcher\stop-finsight.bat</span>).<br/>
            2. Double-click the <b>FinSight AI</b> shortcut on the Desktop — it starts and checks the database.<br/>
            3. Still failing? Windows may be out of memory: close a few programs and try again.
          </div>
        </div>
        <button className="btn btn-ghost" style={{marginTop:20}} onClick={handleLogout}>
          Back to role selection
        </button>
      </div>
    );
  }

  if (!session) {
    return (
      <div style={{padding:60,textAlign:"center",color:"var(--text3)",fontSize:13}}>
        Signing in…
      </div>
    );
  }

  const userPages = {
    "new-application": <UserHome setPage={setPage} session={session} openApp={setOpenApp}/>,
    "new-app":         <UserNewApplication setPage={setPage} session={session}/>,
    "my-applications": <UserApplications setPage={setPage} session={session} openApp={setOpenApp}/>,
    "app-detail":      <UserAppDetail setPage={setPage} session={session} applicationNo={openApp}/>,
    "notifications":   <OfficerNotifications session={session} setPage={setPage} openApp={setOpenApp}/>,
  };

  const officerPages = {
    "new-app":       <UserNewApplication role={role} setPage={setPage} session={session}/>,
    "app-detail":    <PageAppDetail role={role} setPage={setPage} applicationNo={openApp} session={session}/>,
    "customer360":   <PageCustomer360 role={role} setPage={setPage} applicationNo={openApp} session={session}/>,
    "applications":  <OfficerApplications setPage={setPage} session={session}
                                          initialSearch={globalSearch} openApp={setOpenApp}/>,
    "fraud":         <OfficerFraud setPage={setPage} session={session} openApp={setOpenApp}/>,
    "policy":        <OfficerPolicy setPage={setPage} session={session}/>,
    "reports":       <OfficerReports setPage={setPage} session={session} openApp={setOpenApp}/>,
    "notifications": <OfficerNotifications session={session} setPage={setPage} openApp={setOpenApp}/>,
    "settings":      <OfficerSettings session={session}/>,
  };

  const headPages = {
    "new-app":       <UserNewApplication role={role} setPage={setPage} session={session}/>,
    "app-detail":    <PageAppDetail role={role} setPage={setPage} applicationNo={openApp} session={session}/>,
    "customer360":   <PageCustomer360 role={role} setPage={setPage} applicationNo={openApp} session={session}/>,
    "risk":          <PageRiskDashboard/>,
    "graph":         <PageKnowledgeGraph setPage={setPage} openApp={setOpenApp}/>,
    "dashboard":     <HeadDashboard setPage={setPage} session={session} openApp={setOpenApp}/>,
    "applications":  <HeadApplications setPage={setPage} initialSearch={globalSearch} openApp={setOpenApp}/>,
    "fraud":         <PageFraud role={role} setPage={setPage} openApp={setOpenApp}/>,
    "policy":        <PagePolicy role={role} setPage={setPage}/>,
    "compliance":    <HeadCompliance/>,
    "agents":        <HeadAgentActivity setPage={setPage} openApp={setOpenApp}/>,
    "reports":       <PageReports setPage={setPage} openApp={setOpenApp}/>,
    "notifications": <OfficerNotifications session={session} setPage={setPage} openApp={setOpenApp}/>,
    "settings":      <PageSettings role={role}/>,
  };

  const pageMap = role==="user" ? userPages : role==="officer" ? officerPages : headPages;
  const currentPage = pageMap[page] || (
    <div style={{padding:40,textAlign:"center"}}>
      <div style={{fontSize:40,marginBottom:16}}>🔒</div>
      <div style={{fontSize:16,color:"var(--text1)",fontFamily:"var(--font-sans)",marginBottom:8}}>Access Restricted</div>
      <div style={{fontSize:13,color:"var(--text3)"}}>This page is not available for your role.</div>
    </div>
  );

  return (
    <Layout role={role} page={page} setPage={setPage} user={user} session={session} title={titles[page]||page} onLogout={handleLogout} onSearch={handleSearch}>
      {currentPage}
    </Layout>
  );
}
