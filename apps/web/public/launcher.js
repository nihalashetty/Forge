/* Forge chat launcher - a self-contained, dependency-free floating chat bubble that embeds the
 * Forge widget (/embed) in an iframe. Drop one <script> tag on any allowed site:
 *
 *   <script src="https://YOUR-FORGE/launcher.js"
 *           data-forge-key="pk_…"
 *           data-forge-origin="https://YOUR-FORGE"
 *           data-forge-title="Chat with us"
 *           data-forge-color="#4f46e5"
 *           data-forge-token="OPTIONAL_SERVER_MINTED_SESSION_TOKEN"
 *           defer></script>
 *
 * The host site must be in the project's allowed_origins (the /embed page sets a
 * frame-ancestors CSP) - the launcher cannot bypass that. The publishable key is safe to ship;
 * the optional session_token must be minted server-side and injected per request.
 */
(function () {
  "use strict";
  var me = document.currentScript || document.querySelector("script[data-forge-key]");
  if (!me) return;
  var ds = me.dataset;
  var KEY = ds.forgeKey;
  var TOKEN = ds.forgeToken || "";
  var TITLE = ds.forgeTitle || "Chat";
  var COLOR = ds.forgeColor || "#4f46e5";
  var ORIGIN = ds.forgeOrigin || new URL(me.src, location.href).origin;
  if (!KEY) { console.warn("[forge] launcher: missing data-forge-key"); return; }
  if (window.__forgeLauncher) return; // idempotent
  window.__forgeLauncher = true;

  // Pass our (the host page's) origin so the widget can validate the postMessage channel
  // strictly, without depending on the referrer (which strict Referrer-Policy can strip).
  var src = ORIGIN + "/embed?key=" + encodeURIComponent(KEY) + "&host=" + encodeURIComponent(location.origin);
  if (TOKEN) src += "&session_token=" + encodeURIComponent(TOKEN);

  var REDUCED = (window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);

  var css =
    ".forge-fab{position:fixed;bottom:20px;right:20px;width:56px;height:56px;border-radius:50%;" +
    "border:0;cursor:pointer;z-index:2147483000;background:" + COLOR + ";color:#fff;" +
    "box-shadow:0 4px 14px rgba(0,0,0,.25);display:flex;align-items:center;justify-content:center;" +
    "padding:0;transition:" + (REDUCED ? "none" : "transform .15s ease") + "}" +
    ".forge-fab:hover{transform:" + (REDUCED ? "none" : "scale(1.05)") + "}" +
    ".forge-fab:focus-visible{outline:3px solid rgba(0,0,0,.35);outline-offset:2px}" +
    ".forge-fab svg{width:26px;height:26px;display:block}" +
    ".forge-panel{position:fixed;bottom:88px;right:20px;width:400px;height:600px;" +
    "max-height:calc(100vh - 108px);border:0;border-radius:16px;overflow:hidden;z-index:2147483000;" +
    "background:#fff;box-shadow:0 12px 40px rgba(0,0,0,.28);display:none}" +
    ".forge-panel.forge-open{display:block}" +
    ".forge-panel iframe{width:100%;height:100%;border:0;display:block}" +
    "@media (max-width:480px){.forge-panel{bottom:0;right:0;left:0;top:0;width:100%;height:100%;" +
    "max-height:100%;border-radius:0}.forge-fab.forge-hidden{display:none}}";
  var style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);

  var openIcon = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 3C6.5 3 2 6.7 2 11.2c0 2.5 1.4 4.7 3.5 6.2-.1.9-.5 2.1-1.3 3.1 1.6-.2 3-.8 4.1-1.6 1.1.3 2.3.5 3.7.5 5.5 0 10-3.7 10-8.2S17.5 3 12 3z"/></svg>';
  var closeIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';

  var fab = document.createElement("button");
  fab.type = "button";
  fab.className = "forge-fab";
  fab.setAttribute("aria-haspopup", "dialog");
  fab.setAttribute("aria-expanded", "false");
  fab.setAttribute("aria-label", "Open " + TITLE);
  fab.innerHTML = openIcon;

  var panel = document.createElement("div");
  panel.className = "forge-panel";
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", TITLE);
  var iframe = document.createElement("iframe");
  iframe.title = TITLE;
  iframe.setAttribute("allow", "clipboard-write");
  panel.appendChild(iframe);

  var open = false, loaded = false;
  function setOpen(next) {
    open = next;
    if (open && !loaded) { iframe.src = src; loaded = true; } // lazy-load on first open
    panel.classList.toggle("forge-open", open);
    fab.classList.toggle("forge-hidden", open);
    fab.setAttribute("aria-expanded", String(open));
    fab.setAttribute("aria-label", (open ? "Close " : "Open ") + TITLE);
    fab.innerHTML = open ? closeIcon : openIcon;
    if (open) { try { iframe.focus(); } catch (e) {} }
    else { try { fab.focus(); } catch (e) {} }
  }
  fab.addEventListener("click", function () { setOpen(!open); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && open) setOpen(false); });

  document.body.appendChild(panel);
  document.body.appendChild(fab);

  // postMessage: accept ONLY forge:-namespaced messages from OUR iframe at the Forge ORIGIN.
  window.addEventListener("message", function (e) {
    if (e.origin !== ORIGIN) return;
    if (e.source !== iframe.contentWindow) return;
    var m = e.data;
    if (!m || typeof m !== "object" || typeof m.type !== "string" || m.type.indexOf("forge:") !== 0) return;
    if (m.type === "forge:ready") {
      iframe.contentWindow.postMessage({ type: "forge:host", host: location.origin, title: TITLE, color: COLOR }, ORIGIN);
    } else if (m.type === "forge:close") {
      setOpen(false);
    } else if (m.type === "forge:open") {
      setOpen(true);
    }
  });
})();
