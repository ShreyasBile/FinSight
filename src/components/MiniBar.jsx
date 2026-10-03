export default function MiniBar({ values, color="var(--blue)" }) {
  const max = Math.max(...values,1);
  return <div style={{display:"flex",alignItems:"flex-end",gap:3,height:32}}>{values.map((v,i)=><div key={i} style={{flex:1,height:`${(v/max)*100}%`,background:i===values.length-1?color:"var(--surface2)",borderRadius:"2px 2px 0 0",minHeight:3}}/>)}</div>;
}
