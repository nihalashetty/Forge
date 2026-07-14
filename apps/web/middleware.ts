import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/* Security headers for every app route. The /embed widget is intentionally framable by the
   project's configured allowed_origins (Phase 3b); every OTHER route - the authenticated operator
   dashboard - is never framable, to stop clickjacking of one-click destructive actions (audit M7).
   Static assets are excluded from the matcher. */
export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };

export async function middleware(req: NextRequest) {
  const res = NextResponse.next();
  res.headers.set("X-Content-Type-Options", "nosniff");
  res.headers.set("Referrer-Policy", "strict-origin-when-cross-origin");

  if (req.nextUrl.pathname === "/embed") {
    // Widget: restrict which sites may embed it via `frame-ancestors`, derived from the project's
    // configured allowed_origins. Default 'self' blocks external embedding until a project
    // explicitly allow-lists an origin - a secure default.
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
  } else {
    // Operator dashboard + everything else: never framable (clickjacking defense, audit M7).
    res.headers.set("Content-Security-Policy", "frame-ancestors 'none'");
    res.headers.set("X-Frame-Options", "DENY");
  }
  return res;
}
