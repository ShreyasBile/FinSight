export default function PageHeader({ title, sub, children }) {
  return (
    <div className="page-header">
      <div><div className="page-title">{title}</div>{sub&&<div className="page-sub">{sub}</div>}</div>
      <div style={{display:"flex",gap:8,flexWrap:"wrap",alignItems:"center"}}>{children}</div>
    </div>
  );
}
