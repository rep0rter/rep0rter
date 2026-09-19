// Serves the generated site from Workers Static Assets and forwards the owner
// account paths to the Flask app, reproducing what Caddyfile does today.
//
// Asset responses get their headers from _headers. Proxied responses do not:
// _headers never applies to Worker-generated responses, and the Flask app
// already sets its own cache and security headers in rep0rter/web.py.

interface Env {
  /** Static Assets binding holding the built site. */
  ASSETS: Fetcher;
  /** Where /auth/*, /submit and /projects* live while accounts runs on Singa. */
  APP_ORIGIN: string;
}

// Mirrors the Caddyfile matcher: path /auth/* /submit /projects /projects/*
function isAppPath(pathname: string): boolean {
  return pathname === "/submit" || pathname === "/projects" ||
    pathname.startsWith("/auth/") || pathname.startsWith("/projects/");
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (isAppPath(url.pathname)) {
      const target = new URL(url.pathname + url.search, env.APP_ORIGIN);
      return fetch(new Request(target, request));
    }

    // html_handling is "none", so directory requests are not resolved for us.
    // Caddy's file_server serves index.html for them; keep that behaviour.
    if (url.pathname.endsWith("/")) {
      const index = new URL(url);
      index.pathname += "index.html";
      return env.ASSETS.fetch(new Request(index, request));
    }
    return env.ASSETS.fetch(request);
  },
} satisfies ExportedHandler<Env>;
