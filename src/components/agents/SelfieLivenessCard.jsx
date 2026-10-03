import { useEffect, useRef, useState } from "react";
import { Icon, Icons } from "../../icons/Icons.jsx";
import { checkLiveness } from "../../utils/ocr.js";

// Wizard selfie step — the Liveness agent checks that the selfie is a live
// person (not a printed photo or a screen) and the same person as the ID
// photo already uploaded to this application. Advisory: a failed check asks
// for a retake; it never blocks or rejects the application. The selfie is
// checked and discarded — it is not stored.

const VERDICT = {
  live_match: { cls: "alert-green", color: "var(--green)", icon: Icons.userCheck, text: "Live selfie — same person as the ID photo" },
  live_no_reference: { cls: "alert-green", color: "var(--green)", icon: Icons.userCheck, text: "Live selfie" },
  live_uncertain_match: { cls: "alert-amber", color: "var(--amber)", icon: Icons.userSearch, text: "Live selfie — match with the ID photo is unclear" },
  live_mismatch: { cls: "alert-red", color: "var(--red)", icon: Icons.alert, text: "Live selfie, but it does not look like the person on the ID" },
  spoof_suspected: { cls: "alert-amber", color: "var(--amber)", icon: Icons.alert, text: "This looks like a photo of a printout or a screen" },
  no_face: { cls: "alert-amber", color: "var(--amber)", icon: Icons.alert, text: "No face found" },
  multiple_faces: { cls: "alert-amber", color: "var(--amber)", icon: Icons.alert, text: "More than one face in the picture" },
  unreadable: { cls: "alert-red", color: "var(--red)", icon: Icons.alert, text: "The image could not be read" },
};

export default function SelfieLivenessCard({ isStaff, onResult }) {
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const fileRef = useRef(null);
  const [camera, setCamera] = useState(false);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState(null);
  const [error, setError] = useState(null);

  const stop = () => {
    streamRef.current?.getTracks().forEach(t => t.stop());
    streamRef.current = null;
    setCamera(false);
  };
  useEffect(() => stop, []);

  const start = async () => {
    setError(null);
    try {
      const s = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user", width: { ideal: 1280 } }, audio: false });
      streamRef.current = s;
      setCamera(true);
      requestAnimationFrame(() => { if (videoRef.current) { videoRef.current.srcObject = s; videoRef.current.play(); } });
    } catch {
      setError("Camera not available — you can upload a selfie photo instead.");
    }
  };

  const send = async (blob) => {
    setBusy(true); setError(null);
    try {
      const r = await checkLiveness(blob);
      setRes(r);
      onResult?.(r);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const capture = () => {
    const v = videoRef.current;
    if (!v || !v.videoWidth) return;
    const c = document.createElement("canvas");
    c.width = v.videoWidth; c.height = v.videoHeight;
    c.getContext("2d").drawImage(v, 0, 0);
    stop();
    c.toBlob(b => b && send(b), "image/jpeg", 0.92);
  };

  const v = res ? (VERDICT[res.verdict] || VERDICT.unreadable) : null;
  return (
    <div className="card">
      <div className="section-head">
        <Icon d={Icons.userCheck} size={14} /> Selfie check — liveness
      </div>
      <div style={{ fontSize: 12, color: "var(--text3)", marginBottom: 10, lineHeight: 1.6 }}>
        Take a quick selfie so we can confirm it is really you applying. Look at the camera in good light.
        The picture is checked and then discarded.
      </div>
      {camera && (
        <div style={{ marginBottom: 10 }}>
          <video ref={videoRef} playsInline muted
                 style={{ width: "100%", maxWidth: 360, borderRadius: 10, background: "#000", transform: "scaleX(-1)" }} />
        </div>
      )}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {!camera && (
          <button className="btn btn-primary btn-sm" disabled={busy} onClick={start}>
            <Icon d={Icons.userRound} size={12} /> {res ? "Retake with camera" : "Open camera"}
          </button>
        )}
        {camera && (
          <>
            <button className="btn btn-primary btn-sm" onClick={capture}><Icon d={Icons.check} size={12} /> Capture</button>
            <button className="btn btn-secondary btn-sm" onClick={stop}>Cancel</button>
          </>
        )}
        <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => fileRef.current?.click()}>
          <Icon d={Icons.upload} size={12} /> Upload a selfie
        </button>
        <input ref={fileRef} type="file" accept="image/*" capture="user" style={{ display: "none" }}
               onChange={e => { const f = e.target.files?.[0]; if (f) send(f); e.target.value = ""; }} />
      </div>
      {busy && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--text3)", marginTop: 10 }}>
          <span className="spinner" style={{ width: 13, height: 13 }} /> Checking the selfie…
        </div>
      )}
      {error && <div style={{ fontSize: 12, color: "var(--red)", marginTop: 10 }}>{error}</div>}
      {res && v && (
        <div style={{ marginTop: 10 }}>
          <div className={`alert-banner ${v.cls}`}>
            <Icon d={v.icon} size={15} style={{ color: v.color, flexShrink: 0, marginTop: 1 }} />
            <span><strong>{v.text}.</strong> {(res.notes || []).join(" ")}</span>
          </div>
          {isStaff && (
            <div style={{ fontSize: 11, color: "var(--text3)", fontFamily: "var(--font-mono)" }}>
              live score {res.live_score ?? "—"}
              {res.attack_scores ? ` · print ${res.attack_scores.print} · replay ${res.attack_scores.replay}` : ""}
              {res.match ? ` · ID similarity ${res.match.similarity} (${res.match.verdict})` : ""}
              {res.quality ? ` · sharpness ${res.quality.sharpness}` : ""}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
