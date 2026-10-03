import { useState } from "react";
import { Icon, Icons } from "../../icons/Icons.jsx";
import PageHeader from "../../components/PageHeader.jsx";
import LlmStatusCard from "../../components/agents/LlmStatusCard.jsx";

export default function OfficerSettings() {
  const [toggles, setToggles] = useState({ email:true, sms:false, fraud:true, policyUpdates:true });

  const toggle = (key) => setToggles({ ...toggles, [key]:!toggles[key] });

  const ToggleSwitch = ({ on, onClick }) => (
    <div onClick={onClick} style={{width:36,height:20,borderRadius:10,background:on?"var(--green)":"var(--surface2)",cursor:"pointer",display:"flex",alignItems:"center",padding:"0 3px",justifyContent:on?"flex-end":"flex-start"}}>
      <div style={{width:14,height:14,borderRadius:"50%",background:"white"}}/>
    </div>
  );

  return (
    <div>
      <PageHeader title="My Settings" sub="Profile and personal preferences" />
      <div className="grid2">
        <div>
          <div className="card" style={{marginBottom:16}}>
            <div className="section-head">Profile</div>
            <div className="form-group"><label className="form-label">Full Name</label><input className="form-input" defaultValue="Priya Sharma"/></div>
            <div className="form-group"><label className="form-label">Employee ID</label><input className="form-input" defaultValue="EMP-0042" readOnly style={{opacity:0.5}}/></div>
            <div className="form-group"><label className="form-label">Branch</label><input className="form-input" defaultValue="Jaipur" readOnly style={{opacity:0.5}}/></div>
            <div className="form-group"><label className="form-label">Role</label><input className="form-input" defaultValue="Underwriting Officer" readOnly style={{opacity:0.5}}/></div>
            <div className="form-group"><label className="form-label">Email</label><input className="form-input" defaultValue="priya.sharma@sunrisenbc.com"/></div>
            <div className="form-group"><label className="form-label">Mobile</label><input className="form-input" defaultValue="+91 98765 43210"/></div>
            <div style={{marginTop:8}}><button className="btn btn-primary"><Icon d={Icons.check} size={13}/> Save Changes</button></div>
          </div>
          <div className="card" style={{marginBottom:16}}>
            <div className="section-head">Change Password</div>
            <div className="form-group"><label className="form-label">Current Password</label><input className="form-input" type="password" placeholder="••••••••"/></div>
            <div className="form-row">
              <div className="form-group"><label className="form-label">New Password</label><input className="form-input" type="password" placeholder="••••••••"/></div>
              <div className="form-group"><label className="form-label">Confirm Password</label><input className="form-input" type="password" placeholder="••••••••"/></div>
            </div>
            <div style={{marginTop:8}}><button className="btn btn-primary"><Icon d={Icons.check} size={13}/> Update Password</button></div>
          </div>
        </div>
        <div>
          <LlmStatusCard />
          <div className="card" style={{marginBottom:16}}>
            <div className="section-head">Notification Preferences</div>
            <div style={{padding:"9px 0",borderBottom:"1px solid var(--border)",display:"flex",justifyContent:"space-between",alignItems:"center"}}>
              <span style={{fontSize:13,color:"var(--text2)"}}>Email alerts</span>
              <ToggleSwitch on={toggles.email} onClick={()=>toggle("email")} />
            </div>
            <div style={{padding:"9px 0",borderBottom:"1px solid var(--border)",display:"flex",justifyContent:"space-between",alignItems:"center"}}>
              <span style={{fontSize:13,color:"var(--text2)"}}>SMS alerts</span>
              <ToggleSwitch on={toggles.sms} onClick={()=>toggle("sms")} />
            </div>
            <div style={{padding:"9px 0",borderBottom:"1px solid var(--border)",display:"flex",justifyContent:"space-between",alignItems:"center"}}>
              <span style={{fontSize:13,color:"var(--text2)"}}>Fraud alerts</span>
              <ToggleSwitch on={toggles.fraud} onClick={()=>toggle("fraud")} />
            </div>
            <div style={{padding:"9px 0",display:"flex",justifyContent:"space-between",alignItems:"center"}}>
              <span style={{fontSize:13,color:"var(--text2)"}}>Policy updates</span>
              <ToggleSwitch on={toggles.policyUpdates} onClick={()=>toggle("policyUpdates")} />
            </div>
            <div style={{marginTop:8}}><button className="btn btn-primary"><Icon d={Icons.check} size={13}/> Save Preferences</button></div>
          </div>
          <div className="card" style={{marginBottom:16}}>
            <div className="section-head">Assigned Applications Summary</div>
            <div className="grid3" style={{gap:10,marginBottom:12}}>
              <div style={{textAlign:"center",padding:"12px 8px",background:"var(--blue-tint)",borderRadius:8}}>
                <div style={{fontSize:22,fontWeight:700,color:"var(--blue)",fontFamily:"var(--font-sans)"}}>12</div>
                <div style={{fontSize:10,color:"var(--text3)"}}>Total Assigned</div>
              </div>
              <div style={{textAlign:"center",padding:"12px 8px",background:"var(--amber-tint)",borderRadius:8}}>
                <div style={{fontSize:22,fontWeight:700,color:"var(--amber)",fontFamily:"var(--font-sans)"}}>5</div>
                <div style={{fontSize:10,color:"var(--text3)"}}>Pending</div>
              </div>
              <div style={{textAlign:"center",padding:"12px 8px",background:"var(--green-tint)",borderRadius:8}}>
                <div style={{fontSize:22,fontWeight:700,color:"var(--green)",fontFamily:"var(--font-sans)"}}>4</div>
                <div style={{fontSize:10,color:"var(--text3)"}}>Approved</div>
              </div>
              <div style={{textAlign:"center",padding:"12px 8px",background:"var(--red-tint)",borderRadius:8}}>
                <div style={{fontSize:22,fontWeight:700,color:"var(--red)",fontFamily:"var(--font-sans)"}}>2</div>
                <div style={{fontSize:10,color:"var(--text3)"}}>Rejected</div>
              </div>
              <div style={{textAlign:"center",padding:"12px 8px",background:"var(--purple-tint)",borderRadius:8}}>
                <div style={{fontSize:22,fontWeight:700,color:"var(--purple)",fontFamily:"var(--font-sans)"}}>2.4</div>
                <div style={{fontSize:10,color:"var(--text3)"}}>Avg Days</div>
              </div>
            </div>
            <button className="btn btn-ghost" style={{fontSize:12}}>View Full Report →</button>
          </div>
          <div className="card" style={{background:"var(--blue-tint)",borderColor:"var(--blue-tint-strong)"}}>
            <div style={{fontSize:12,color:"var(--blue)",fontWeight:500,marginBottom:8}}>ℹ Access Restrictions</div>
            <div style={{fontSize:12,color:"var(--text3)",lineHeight:1.7}}>System settings, AI model configuration, policy document management, and branch-level analytics are restricted to Branch Manager and Head Manager roles. Contact your Branch Manager for role changes or access requests.</div>
          </div>
        </div>
      </div>
    </div>
  );
}
