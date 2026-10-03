import { useState } from "react";
import { Icon, Icons } from "../icons/Icons.jsx";
import LlmSettingsCard from "../components/agents/LlmSettingsCard.jsx";
import { UsersTab, AuditTab, StatusTab } from "./settingsTabs.jsx";

export default function PageSettings({ role }) {
  const [activeTab, setActiveTab] = useState("General");
  const tabs = ["General","AI Models","Risk Thresholds","Users & Roles","Integrations","Audit Log","System Status"];

  if (role!=="head") {
    return <div className="card" style={{textAlign:"center",padding:48}}><Icon d={Icons.settings} size={40} style={{color:"var(--text3)",marginBottom:12}}/><div style={{fontSize:16,color:"var(--text2)",marginBottom:4}}>Access Restricted</div><div style={{fontSize:13,color:"var(--text3)"}}>Settings are only accessible to Head Manager role.</div></div>;
  }

  return (
    <div>
      <div className="page-header">
        <div><div className="page-title">Settings</div><div className="page-sub">Platform configuration and AI model management</div></div>
        <button className="btn btn-primary"><Icon d={Icons.check} size={13}/>Save Changes</button>
      </div>
      <div className="tabs">{tabs.map(t=><div key={t} className={`tab ${activeTab===t?"active":""}`} onClick={()=>setActiveTab(t)}>{t}</div>)}</div>
      {activeTab==="General"&&(
        <div className="grid2">
          <div className="card">
            <div className="section-head">NBFC Configuration</div>
            <div className="form-group"><label className="form-label">Platform Name</label><input className="form-input" defaultValue="Finsight AI" /></div>
            <div className="form-group"><label className="form-label">NBFC Registration Number</label><input className="form-input" defaultValue="N-13.01793" style={{fontFamily:"var(--font-mono)"}} /></div>
            <div className="form-group"><label className="form-label">Headquarters</label><input className="form-input" defaultValue="Jaipur, Rajasthan" /></div>
            <div className="form-group"><label className="form-label">Default Loan Currency</label>
              <select className="form-input form-select"><option>INR (₹)</option></select>
            </div>
          </div>
          <div className="card">
            <div className="section-head">Branch Settings</div>
            {[
              {branch:"Jaipur HQ",code:"JPR",active:true},
              {branch:"Lucknow Branch",code:"LKO",active:true},
              {branch:"Patna Branch",code:"PAT",active:true},
              {branch:"Indore Branch",code:"IDR",active:true},
            ].map(b=>(
              <div key={b.branch} style={{display:"flex",justifyContent:"space-between",alignItems:"center",padding:"8px 0",borderBottom:"1px solid var(--border)"}}>
                <div>
                  <div style={{fontSize:12,color:"var(--text1)",fontWeight:500}}>{b.branch}</div>
                  <div style={{fontSize:10,color:"var(--text3)",fontFamily:"var(--font-mono)"}}>{b.code}</div>
                </div>
                <span className={`badge ${b.active?"badge-green":"badge-gray"}`}>{b.active?"Active":"Inactive"}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      {activeTab==="AI Models"&&<LlmSettingsCard role={role} />}
      {activeTab==="Risk Thresholds"&&(
        <div className="card">
          <div className="section-head">Segment-wise Risk Thresholds</div>
          {[
            {seg:"Commercial Vehicle",npa:8,dti:50,ltv:85,minCibil:650,highRisk:70},
            {seg:"MSME Loans",npa:10,dti:55,ltv:80,minCibil:600,highRisk:75},
            {seg:"Home Loans",npa:5,dti:50,ltv:90,minCibil:700,highRisk:65},
            {seg:"Gold Loan",npa:3,dti:60,ltv:85,minCibil:550,highRisk:80},
            {seg:"Microfinance",npa:12,dti:45,ltv:0,minCibil:500,highRisk:75},
            {seg:"Tractor Loan",npa:7,dti:50,ltv:80,minCibil:600,highRisk:70},
          ].map(s=>(
            <div key={s.seg} style={{padding:"12px 0",borderBottom:"1px solid var(--border)"}}>
              <div style={{fontSize:12,fontWeight:600,color:"var(--text1)",marginBottom:8}}>{s.seg}</div>
              <div className="form-row3">
                <div className="form-group"><label className="form-label">NPA Threshold (%)</label><input className="form-input" type="number" min={0} max={100} defaultValue={s.npa} style={{padding:"6px 10px",fontSize:12}}/></div>
                <div className="form-group"><label className="form-label">DTI Cap (%)</label><input className="form-input" type="number" min={0} max={100} defaultValue={s.dti} style={{padding:"6px 10px",fontSize:12}}/></div>
                <div className="form-group"><label className="form-label">LTV Cap (%)</label><input className="form-input" type="number" min={0} max={100} defaultValue={s.ltv} style={{padding:"6px 10px",fontSize:12}}/></div>
                <div className="form-group"><label className="form-label">Min CIBIL</label><input className="form-input" type="number" min={300} max={900} defaultValue={s.minCibil} style={{padding:"6px 10px",fontSize:12}}/></div>
                <div className="form-group"><label className="form-label">High Risk Cutoff</label><input className="form-input" type="number" min={0} max={100} defaultValue={s.highRisk} style={{padding:"6px 10px",fontSize:12}}/></div>
              </div>
            </div>
          ))}
        </div>
      )}
      {activeTab==="Users & Roles"&&<UsersTab/>}
      {activeTab==="Integrations"&&(
        <div className="grid2">
          <div className="card">
            <div className="section-head">API & Provider Config</div>
            <div className="form-group"><label className="form-label">Document extraction</label>
              <div style={{fontSize:12,color:"var(--text2)",lineHeight:1.6}}>Handled by the Extraction agent — choose its model on the <b>AI Models</b> tab.</div>
            </div>
            <div className="form-group"><label className="form-label">Anthropic API key</label>
              <div style={{fontSize:12,color:"var(--text2)",lineHeight:1.6}}>Stored only on the server (<span style={{fontFamily:"var(--font-mono)"}}>api/.env</span>); it is never entered, shown, or sent to the browser.</div>
            </div>
            <div className="form-group"><label className="form-label">Webhook URL</label>
              <input className="form-input" defaultValue="https://hooks.finsight.ai/events" style={{fontFamily:"var(--font-mono)"}} />
            </div>
          </div>
          <div className="card">
            <div className="section-head">Communication & Storage</div>
            <div className="form-group"><label className="form-label">SMS Provider</label>
              <select className="form-input"><option>Twilio</option><option>MSG91</option><option>Amazon SNS</option></select>
            </div>
            <div className="form-group"><label className="form-label">Email Provider</label>
              <select className="form-input"><option>Amazon SES</option><option>SendGrid</option><option>SMTP (Custom)</option></select>
            </div>
            <div className="form-group"><label className="form-label">Document Storage</label>
              <select className="form-input"><option>AWS S3</option><option>Azure Blob</option><option>Google Cloud Storage</option></select>
            </div>
          </div>
        </div>
      )}
      {activeTab==="Audit Log"&&<AuditTab/>}
      {activeTab==="System Status"&&<StatusTab/>}
    </div>
  );
}
