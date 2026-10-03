import { useState, useEffect } from "react";
import { Icon, Icons } from "../../icons/Icons.jsx";
import { api } from "../../utils/api.js";
import { NAV } from "../../data/navConfig.js";
import logoMark from "../../assets/brand/mark-light.png";

export default function Sidebar({ role, page, setPage, user, session, onLogout, open, onClose }) {
  // Badge counts are read from the database and refreshed whenever the page changes
  // (they used to be constants: 3, 12, 5 ...).
  const [counts, setCounts] = useState({});
  useEffect(() => {
    if (!session?.user_id) { setCounts({}); return; }
    let stale = false;
    const uid = session.user_id;
    const load = role === "user"
      ? api.notifications(uid).then(n => ({ notifications: n.unread }))
      : role === "officer"
      ? Promise.all([api.applications({ officer_id: uid, include_closed: false }), api.fraudCases(uid), api.notifications(uid)])
          .then(([a, f, n]) => ({ applications: a.count, fraud: f.count, notifications: n.unread }))
      : Promise.all([api.overview(), api.notifications(uid)])
          .then(([o, n]) => ({ applications: o.totals.applications, fraud: o.fraud.cases, notifications: n.unread }));
    load.then(c => { if (!stale) setCounts(c); }).catch(() => { if (!stale) setCounts({}); });
    return () => { stale = true; };
  }, [role, session?.user_id, page]);
  const rc = user.color;
  const navItems = NAV[role];

  return (
    <>
      {open && <div className="nav-overlay" onClick={onClose} />}
      <div className={`sidebar${open ? " open" : ""}`}>
        <div className="sidebar-logo">
          <img className="logo-mark" src={logoMark} alt="" />
          <div>
            <div className="logo-text">FinSight AI</div>
            <div className="logo-sub">NBFC Platform</div>
          </div>
        </div>

        <div style={{margin:"8px 12px 0",padding:"8px 12px",background:`${rc}14`,borderRadius:8,border:`1px solid ${rc}33`}}>
          <div style={{fontSize:9,color:rc,fontFamily:"var(--font-mono)",letterSpacing:"1.5px",textTransform:"uppercase",marginBottom:1}}>{user.badge}</div>
          <div style={{fontSize:11,color:"var(--text3)"}}>{user.sub}</div>
        </div>

        {navItems.map(group=>(
          <div key={group.group} className="nav-group">
            <div className="nav-label">{group.group}</div>
            {group.items.map(item=>(
              <button key={item.id} className={`nav-item ${page===item.id?"active":""}`} onClick={()=>setPage(item.id)} aria-current={page===item.id?"page":undefined}>
                <Icon d={item.icon} size={14}/>
                <span>{item.label}</span>
                {counts[item.id]>0&&(
                  <span style={{marginLeft:"auto",fontSize:10,fontWeight:600,padding:"1px 6px",borderRadius:10,fontFamily:"var(--font-mono)",background:item.badgeColor==="red"?"var(--red)":item.badgeColor==="amber"?"var(--amber-tint-strong)":"var(--blue-tint-strong)",color:item.badgeColor==="red"?"var(--white)":item.badgeColor==="amber"?"var(--amber)":"var(--blue)"}}>
                    {counts[item.id]}
                  </span>
                )}
              </button>
            ))}
          </div>
        ))}

        <div className="sidebar-footer">
          <button className="user-chip" onClick={onLogout} title="Click to switch role" aria-label={`Switch role, currently ${user.name}`}>
            <div className="user-avatar" style={{background:`linear-gradient(135deg,${rc},${rc}88)`}}>{user.avatar}</div>
            <div>
              <div className="user-name">{user.name}</div>
              <div className="user-role">{user.badge}</div>
            </div>
            <Icon d={Icons.logout} size={13} style={{color:"rgba(255,255,255,0.4)",marginLeft:"auto"}} title="Switch Role"/>
          </button>
        </div>
      </div>
    </>
  );
}
