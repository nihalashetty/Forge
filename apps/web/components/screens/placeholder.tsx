"use client";
import { Tile } from "../primitives";

export function Placeholder({ title, icon, note }: { title: string; icon: string; note?: string }) {
  return (
    <div className="col center fade-up" style={{ flex: 1, padding: 40, gap: 14 }}>
      <Tile icon={icon} color="var(--accent)" size={56} glow />
      <div className="t-display">{title}</div>
      <div className="fg-1" style={{ maxWidth: 460, textAlign: "center" }}>
        {note ||
          "This surface is on the roadmap. The backend engine, schema validation, and live run streaming are already wired - this screen is being built next."}
      </div>
      <span className="chip chip-mono">see docs/ROADMAP.md</span>
    </div>
  );
}
