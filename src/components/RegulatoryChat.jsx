import { useState, useRef, useEffect } from "react";
import { Icon, Icons } from "../icons/Icons.jsx";
import { ragQuery } from "../utils/compliance.js";

// Chat over the RBI compliance knowledge base (BM25 retrieval + citations).
// Embedded in the Policy Engine pages for both Underwriting Officers and Head
// Managers — this replaced the old mock "AI Policy Query" box and the
// short-lived separate "RBI Assistant" page, which were the same feature.

const SUGGESTIONS = [
  "What is the cooling-off period for digital loans?",
  "Can we charge penal interest?",
  "What must the Key Facts Statement include?",
  "What is the household income cap for microfinance loans?",
  "Which documents count as OVDs for KYC?",
];

export default function RegulatoryChat({ height = 520 }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef(null);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, busy]);

  const ask = async (q) => {
    const query = (q ?? input).trim();
    if (!query || busy) return;
    setInput("");
    setMessages(m => [...m, { who: "user", text: query }]);
    setBusy(true);
    try {
      const res = await ragQuery(query, 4, true);
      setMessages(m => [...m, {
        who: "bot", text: res.answer, confidence: res.confidence, citations: res.citations,
        synthesized: res.synthesized, grounded: res.grounded,
      }]);
    } catch (e) {
      setMessages(m => [...m, {
        who: "bot", error: true,
        text: `Couldn't reach the compliance service: ${e.message}`,
      }]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{display:"flex",flexDirection:"column",height}}>
      <div style={{flex:1,overflowY:"auto",display:"flex",flexDirection:"column",gap:12,paddingRight:4}}>
        {messages.length === 0 && (
          <div style={{margin:"auto",textAlign:"center",maxWidth:440}}>
            <Icon d={Icons.policy} size={30} style={{color:"var(--text3)",marginBottom:10}}/>
            <div style={{fontSize:13,fontWeight:600,color:"var(--text1)",marginBottom:4}}>Ask about any RBI lending regulation</div>
            <div style={{fontSize:11,color:"var(--text3)",marginBottom:14}}>
              Answers come from the regulatory knowledge base with citations — not legal advice.
            </div>
            <div style={{display:"flex",flexWrap:"wrap",gap:6,justifyContent:"center"}}>
              {SUGGESTIONS.map(s => (
                <button key={s} className="btn btn-ghost" style={{fontSize:11,padding:"4px 10px"}} onClick={()=>ask(s)}>{s}</button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) => m.who === "user" ? (
          <div key={i} style={{alignSelf:"flex-end",maxWidth:"75%",background:"var(--blue)",color:"#fff",borderRadius:"12px 12px 2px 12px",padding:"8px 12px",fontSize:12.5,lineHeight:1.5}}>
            {m.text}
          </div>
        ) : (
          <div key={i} style={{alignSelf:"flex-start",maxWidth:"92%"}}>
            <div style={{background:m.error?"var(--red-tint)":"var(--surface2)",border:`1px solid ${m.error?"var(--red)":"var(--border)"}`,borderRadius:"12px 12px 12px 2px",padding:"9px 13px",fontSize:12.5,lineHeight:1.6,color:m.error?"var(--red)":"var(--text1)",whiteSpace:"pre-line"}}>
              {m.text}
              {m.confidence && m.confidence !== "None" && (
                <div style={{marginTop:5,display:"flex",gap:5}}>
                  <span className={`badge ${m.confidence==="High"?"badge-green":m.confidence==="Medium"?"badge-amber":"badge-gray"}`} style={{fontSize:9}}>
                    confidence: {m.confidence}
                  </span>
                  <span className="badge badge-gray" style={{fontSize:9}}>
                    {m.synthesized ? "answer written from the cited passages" : "verbatim passage"}
                  </span>
                </div>
              )}
            </div>
            {m.citations?.length > 0 && (
              <details style={{marginTop:5,marginLeft:4}}>
                <summary style={{fontSize:10.5,color:"var(--text3)",cursor:"pointer"}}>
                  {m.citations.length} cited passage{m.citations.length>1?"s":""}
                </summary>
                <div style={{display:"flex",flexDirection:"column",gap:5,marginTop:5}}>
                  {m.citations.map((c, j) => (
                    <div key={j} style={{background:"var(--surface2)",border:"1px solid var(--border)",borderRadius:8,padding:"7px 10px"}}>
                      <div style={{fontSize:10,fontWeight:600,color:"var(--blue)",marginBottom:2}}>
                        📘 {c.document_title}{c.section_title ? ` · ${c.section_title}` : ""}
                        <span style={{color:"var(--text3)",fontWeight:400,marginLeft:6}}>score {c.score}</span>
                      </div>
                      <div style={{fontSize:11,color:"var(--text2)",lineHeight:1.55}}>{c.text}</div>
                    </div>
                  ))}
                </div>
              </details>
            )}
          </div>
        ))}

        {busy && (
          <div style={{alignSelf:"flex-start",display:"flex",alignItems:"center",gap:8,fontSize:11.5,color:"var(--text3)",padding:"6px 10px"}}>
            <span className="spinner" style={{width:12,height:12}}/> Searching the regulations and drafting a cited answer…
          </div>
        )}
        <div ref={endRef}/>
      </div>

      <div style={{display:"flex",gap:8,paddingTop:10,borderTop:"1px solid var(--border)",marginTop:10}}>
        <input
          className="form-input" style={{flex:1}}
          placeholder="Ask about RBI lending regulations…"
          value={input}
          onChange={e=>setInput(e.target.value)}
          onKeyDown={e=>{ if (e.key === "Enter") ask(); }}
          aria-label="Ask about RBI lending regulations"
        />
        <button className="btn btn-primary" onClick={()=>ask()} disabled={busy || !input.trim()}>Ask</button>
      </div>
    </div>
  );
}
