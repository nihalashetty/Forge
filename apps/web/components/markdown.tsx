"use client";
/* Renders an assistant reply as GitHub-Flavored Markdown (Feature 1 — structured responses).
   Safe by default: react-markdown does NOT render raw HTML (no rehype-raw), so agent output
   cannot inject markup. Visual styling lives in the `.md` block in app/globals.css and uses
   the app's design tokens, so it adapts to light/dark automatically. Memoized so finalized
   messages don't re-parse while a newer message is still streaming. */
import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export const Markdown = memo(function Markdown({ children }: { children: string }) {
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ node, ...props }: any) => (
            <a {...props} target="_blank" rel="noopener noreferrer" />
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
});
