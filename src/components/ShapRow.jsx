export default function ShapRow({ label, value, color="var(--blue)" }) {
  return (
    <div style={{display:"flex",alignItems:"center",gap:10,marginBottom:10}}>
      <span style={{fontSize:12,color:"var(--text2)",width:160,flexShrink:0}}>{label}</span>
      <div style={{flex:1,height:7,background:"var(--surface2)",borderRadius:4,overflow:"hidden"}}>
        <div style={{height:"100%",borderRadius:4,background:color,width:`${value}%`}}/>
      </div>
      <span style={{fontSize:11,fontFamily:"var(--font-mono)",color:"var(--text3)",width:40,textAlign:"right"}}>{value}%</span>
    </div>
  );
}
