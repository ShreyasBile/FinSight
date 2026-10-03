import { useState } from "react";
import { Icon, Icons } from "../../icons/Icons.jsx";

export default function Topbar({ title, role, user, onLogout, onNotifClick, onMenuClick, onSearch }) {
  const rc = user.color;
  const placeholder = role==="officer"?"Search my applications...":"Search all applications...";
  const [query, setQuery] = useState("");

  const submit = () => {
    const q = query.trim();
    if (q && onSearch) onSearch(q);
  };

  return (
    <div className="topbar">
      <button className="icon-btn menu-btn" onClick={onMenuClick} title="Menu" aria-label="Open menu">
        <Icon d={Icons.menu} size={18}/>
      </button>
      <div style={{display:"flex",alignItems:"center",gap:10,minWidth:0}}>
        <div className="topbar-title">{title}</div>
      </div>
      <div className="search-bar">
        <Icon d={Icons.search} size={13} style={{color:"var(--text3)",flexShrink:0}}/>
        <input
          placeholder={placeholder}
          value={query}
          onChange={e=>setQuery(e.target.value)}
          onKeyDown={e=>{ if (e.key==="Enter") submit(); }}
          aria-label={placeholder}
        />
      </div>
      <button className="icon-btn" onClick={onNotifClick} aria-label="Notifications">
        <Icon d={Icons.bell} size={15}/>
        <div className="notif-dot"/>
      </button>
      <button
        style={{display:"flex",alignItems:"center",gap:8,padding:"5px 10px",background:`${rc}14`,borderRadius:8,border:`1px solid ${rc}33`,cursor:"pointer",flexShrink:0}}
        onClick={onLogout} title="Switch role" aria-label={`Switch role, currently ${user.name}`}
      >
        <div className="user-avatar" style={{width:26,height:26,fontSize:10,background:`linear-gradient(135deg,${rc},${rc}88)`}}>{user.avatar}</div>
        <div style={{fontSize:11,color:rc,fontFamily:"var(--font-mono)"}} className="topbar-user-name">{user.name}</div>
      </button>
    </div>
  );
}
