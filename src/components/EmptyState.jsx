import { Icon, Icons } from "../icons/Icons.jsx";

export default function EmptyState({ icon, title, sub }) {
  return (
    <div style={{padding:"48px 24px",textAlign:"center"}}>
      {icon && typeof icon === "object"
        ? <Icon d={icon} size={40} style={{color:"var(--text3)",marginBottom:12}} />
        : <Icon d={Icons.clipboardList} size={40} style={{color:"var(--text3)",marginBottom:12}} />
      }
      <div style={{fontSize:15,fontWeight:600,color:"var(--text1)",fontFamily:"var(--font-sans)",marginBottom:6}}>{title}</div>
      <div style={{fontSize:13,color:"var(--text3)"}}>{sub}</div>
    </div>
  );
}
