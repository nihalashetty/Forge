import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/* Restrict which sites may embed the /embed widget via the response's `frame-ancestors` CSP,
   derived from the project's configured allowed_origins (Phase 3b). Default 'self' blocks
   external embedding until the project explicitly allow-lists an origin — a secure default. */
export const config = { matcher: ["/embed"] };

export async function middleware(req: NextRequest) {
  const res = NextResponse.next();
  const key = req.nextUrl.searchParams.get("key");
  let origins: string[] = [];
  if (key) {
    try {
      const r = await fetch(`${req.nextUrl.origin}/api/forge/v1/embed/${encodeURIComponent(key)}/config`, { cache: "no-store" });
      if (r.ok) origins = (await r.json())?.allowed_origins || [];
    } catch {
      /* fall back to the secure default below */
    }
  }
  const ancestors = ["'self'", ...origins].join(" ");
  res.headers.set("Content-Security-Policy", `frame-ancestors ${ancestors}`);
  return res;
}
