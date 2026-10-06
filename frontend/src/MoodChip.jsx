// The Regime Radar's verdict as a small chip: today's market mood, how long it has lasted, and the last 90 days
// as a strip of colored ticks. Tap to open the agent.
const TONE = { bull: "var(--green)", bear: "var(--red)", sideways: "var(--faint)", wild: "var(--amber)" };

export default function MoodChip({ regime, onOpen, compact = false }) {
  if (!regime?.label) return null;
  const recent = regime.recent || [];
  return (
    <button className={`mood-chip mood-${regime.label} ${compact ? "compact" : ""}`} onClick={onOpen}
      title={`${regime.meaning} Bitcoin ${(regime.btc_vs_200 * 100).toFixed(1)}% vs its 200-day average, ${Math.round(regime.breadth * 100)}% of coins above their 50-day average.`}>
      <span className="mood-name">{regime.name}</span>
      <span className="mood-days">{regime.days}d</span>
      {!compact && (
        <span className="mood-strip" aria-hidden="true">
          {recent.map((l, i) => <i key={i} style={{ background: l ? TONE[l] : "transparent" }} />)}
        </span>
      )}
    </button>
  );
}
