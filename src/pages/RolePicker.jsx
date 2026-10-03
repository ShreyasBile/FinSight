import { useState } from "react";
import { Icon, Icons } from "../icons/Icons.jsx";
import logoMark from "../assets/brand/mark.png";

export default function RolePicker({ onSelect }) {
  const [hovered, setHovered] = useState(null);

  const cards = [
    {
      key:"user", icon:"userRound", title:"Borrower", name:"Self-Service Portal", sub:"Apply & track your loans",
      color:"var(--blue)",
      access:["Select loan type from 12+ categories","Upload required documents","Submit loan application","Track application status","View application details"],
      restricted:["No access to other applicants' data","No underwriting or approval authority","No policy or system configuration"],
    },
    {
      key:"officer", icon:"clipboardList", title:"Underwriting Officer", name:"Priya Sharma", sub:"Credit Team",
      color:"var(--blue)",
      access:["Create & submit loan applications","Review AI risk scores & SHAP explanations","Validate/correct AI-extracted document fields","Flag incorrect model predictions for retraining feedback","View AI fraud detection on own applications","Policy queries (read-only)","Raise policy issue tickets","View own credit memos & reports"],
      restricted:["Approve loans above ₹5L","Modify risk model parameters","Access other officers' applications","View HQ portfolio analytics","Manage policy documents"],
    },
    {
      key:"head", icon:"globe", title:"Head Manager", name:"Anil Verma", sub:"Full Platform Access",
      color:"var(--green)",
      access:["Full platform access — all applications","Review model performance & retraining schedules","Configure risk thresholds & XAI explanation parameters","Monitor data drift & concept drift","Override AI recommendations with mandatory reason code","Manage underwriting rule engine (policy-as-code)","User & role management","All reports & full audit trail","Cross-application fraud pattern analysis","Escalation management"],
      restricted:[],
    },
  ];

  return (
    <div style={{minHeight:"100vh",background:"var(--bg)",display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",padding:"32px 20px",fontFamily:"var(--font-sans)"}}>
      <style>{`@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=DM+Mono:wght@400;500&display=swap');`}</style>

      <div style={{display:"flex",alignItems:"center",gap:12,marginBottom:12}}>
        <img src={logoMark} alt="" style={{height:50,width:"auto",display:"block"}}/>
        <div>
          <div style={{fontFamily:"var(--font-sans)",fontSize:22,fontWeight:700,color:"var(--text1)",letterSpacing:"-0.3px"}}>FinSight AI</div>
          <div style={{fontSize:10,color:"var(--text3)",fontFamily:"var(--font-mono)",letterSpacing:"1.5px",textTransform:"uppercase"}}>AI-Powered NBFC Platform</div>
        </div>
      </div>

      <div style={{textAlign:"center",marginBottom:32}}>
        <div style={{fontSize:24,fontWeight:700,color:"var(--text1)",fontFamily:"var(--font-sans)",marginBottom:6}}>Select Your Role to Continue</div>
        <div style={{fontSize:13,color:"var(--text2)",maxWidth:520}}>Each role has a tailored interface. Your access level determines which features, data, and actions are available to you.</div>
      </div>

      <div className="role-grid">
        {cards.map(r=>(
          <div key={r.key}
            onMouseEnter={()=>setHovered(r.key)}
            onMouseLeave={()=>setHovered(null)}
            onClick={()=>onSelect(r.key)}
            style={{
              background:"var(--surface)",
              border:`1px solid ${hovered===r.key?"var(--blue)":"var(--border)"}`,
              borderRadius:10,padding:"22px 20px",cursor:"pointer",
              transition:"all 0.2s",
            }}>
            <div style={{display:"flex",alignItems:"center",gap:10,marginBottom:16}}>
              <div style={{width:44,height:44,borderRadius:10,background:"var(--surface2)",display:"flex",alignItems:"center",justifyContent:"center",fontSize:20,flexShrink:0}}><Icon d={Icons[r.icon]} size={22} /></div>
              <div>
                <div style={{fontFamily:"var(--font-sans)",fontSize:14,fontWeight:600,color:"var(--text1)",marginBottom:1}}>{r.title}</div>
                <div style={{fontSize:11,color:r.color,fontFamily:"var(--font-mono)"}}>{r.name}</div>
                <div style={{fontSize:10,color:"var(--text3)",marginTop:1}}>{r.sub}</div>
              </div>
            </div>

            <div style={{marginBottom:r.restricted.length>0?12:0}}>
              <div style={{fontSize:9,color:"var(--text3)",fontFamily:"var(--font-mono)",letterSpacing:"1.5px",textTransform:"uppercase",marginBottom:8}}>ACCESS GRANTED</div>
              {r.access.map(p=>(
                <div key={p} style={{display:"flex",gap:7,alignItems:"flex-start",marginBottom:4}}>
                  <Icon d={Icons.check} size={12} style={{color:"var(--green)",flexShrink:0,marginTop:3}} />
                  <span style={{fontSize:11,color:"var(--text2)",lineHeight:1.5}}>{p}</span>
                </div>
              ))}
            </div>

            {r.restricted.length>0&&(
              <div style={{borderTop:"1px solid var(--border)",paddingTop:12,marginTop:4}}>
                <div style={{fontSize:9,color:"var(--text3)",fontFamily:"var(--font-mono)",letterSpacing:"1.5px",textTransform:"uppercase",marginBottom:8}}>RESTRICTED</div>
                {r.restricted.map(p=>(
                  <div key={p} style={{display:"flex",gap:7,alignItems:"flex-start",marginBottom:3}}>
                    <Icon d={Icons.x} size={10} style={{color:"var(--red)",flexShrink:0,marginTop:3}} />
                    <span style={{fontSize:11,color:"var(--text3)",lineHeight:1.5}}>{p}</span>
                  </div>
                ))}
              </div>
            )}

            <div style={{marginTop:16,padding:"8px 14px",background:hovered===r.key?r.color:"var(--surface2)",borderRadius:6,textAlign:"center",fontSize:12,fontWeight:500,color:hovered===r.key?"var(--white)":"var(--text3)",transition:"all 0.2s",border:`1px solid ${hovered===r.key?r.color:"var(--border)"}`}}>
              {hovered===r.key?`Enter as ${r.title} →`:`Continue as ${r.title}`}
            </div>
          </div>
        ))}
      </div>

      <div style={{marginTop:24,fontSize:11,color:"var(--text3)",fontFamily:"var(--font-mono)",letterSpacing:"0.5px"}}>DEMO MODE · ALL DATA IS PLACEHOLDER</div>
    </div>
  );
}
