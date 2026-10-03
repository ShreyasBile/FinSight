export default function ProgressBar({ value, max=100, color="var(--blue)" }) {
  return <div className="progress-bar"><div className="progress-fill" style={{width:`${Math.min((value/max)*100,100)}%`,background:color}}/></div>;
}
