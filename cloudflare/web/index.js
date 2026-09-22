import { DurableObject, WorkerEntrypoint } from 'cloudflare:workers';

const TYPES = {html:'text/html; charset=utf-8', xml:'application/rss+xml; charset=utf-8',
  css:'text/css; charset=utf-8', js:'text/javascript; charset=utf-8',
  png:'image/png', jpg:'image/jpeg', jpeg:'image/jpeg', woff2:'font/woff2',
  svg:'image/svg+xml', json:'application/json; charset=utf-8', txt:'text/plain; charset=utf-8'};

export class PublishedSite extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.sql = ctx.storage.sql;
    this.sql.exec('CREATE TABLE IF NOT EXISTS assets(path TEXT PRIMARY KEY, data BLOB NOT NULL, digest TEXT NOT NULL)');
    this.sql.exec('CREATE TABLE IF NOT EXISTS status(key TEXT PRIMARY KEY, value TEXT NOT NULL)');
  }

  publishSite(files, health) {
    // Validate the whole generation before beginning the atomic switch.
    const seen = new Set();
    for (const file of files) {
      if (!file.path || file.path.startsWith('/') || file.path.includes('\\') || file.path.split('/').includes('..')
          || seen.has(file.path) || file.data.byteLength > 1_900_000) {
        throw new Error('Invalid public asset');
      }
      seen.add(file.path);
    }
    if (!seen.has('index.html') || !seen.has('feed.xml')) throw new Error('Incomplete site generation');
    const existing = new Map([...this.sql.exec('SELECT path,digest FROM assets')].map(row => [row.path,row.digest]));
    this.ctx.storage.transactionSync(() => {
      for (const file of files) {
        if (existing.get(file.path) !== file.digest) {
          this.sql.exec('INSERT INTO assets VALUES (?,?,?) ON CONFLICT(path) DO UPDATE SET data=excluded.data,digest=excluded.digest',
            file.path, file.data, file.digest);
        }
        existing.delete(file.path);
      }
      for (const path of existing.keys()) this.sql.exec('DELETE FROM assets WHERE path=?', path);
      this.updateStatus(health);
    });
    return {published: files.length};
  }

  updateStatus(health) {
    this.sql.exec('INSERT INTO status VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
      'health', JSON.stringify(health));
  }

  async fetch(request) {
    if (!['GET','HEAD'].includes(request.method)) return new Response('Method not allowed', {status:405});
    let path;
    try { path = decodeURIComponent(new URL(request.url).pathname).replace(/^\/+/, ''); }
    catch { return new Response('Not found', {status:404}); }
    if (path.split('/').includes('..') || /[\\\x00]/.test(path)) return new Response('Not found', {status:404});
    if (path === 'healthz') {
      const row = [...this.sql.exec("SELECT value FROM status WHERE key='health'")][0];
      const health = row ? JSON.parse(row.value) : {ready:false, runtime:'cloudflare-workers'};
      return Response.json(health, {status:health.ready ? 200 : 503, headers:{'Cache-Control':'no-store'}});
    }
    if (!path || path.endsWith('/')) path += 'index.html';
    const row = [...this.sql.exec('SELECT data,digest FROM assets WHERE path=?', path)][0];
    if (!row) return new Response('Not found', {status:404});
    // Only shared, content-addressed assets are immutable. Reports and their
    // cards must revalidate so corrections and withdrawals remain effective.
    const cacheControl = path.startsWith('assets/') ? 'public, max-age=31536000, immutable' : 'no-cache';
    const headers = new Headers({'Content-Type':TYPES[path.split('.').pop()] || 'application/octet-stream',
      'Cache-Control':cacheControl, 'ETag':`"${row.digest}"`, 'X-Content-Type-Options':'nosniff'});
    if (request.headers.get('If-None-Match') === headers.get('ETag')) return new Response(null,{status:304,headers});
    return new Response(request.method === 'HEAD' ? null : row.data,{headers});
  }
}

export default class Frontend extends WorkerEntrypoint {
  fetch(request) {
    const path = new URL(request.url).pathname;
    if (path.startsWith('/auth/') || path === '/projects' || path.startsWith('/projects/')
        || path === '/submit' || path === '/write' || path.startsWith('/__admin/')) {
      return this.env.BACKEND.fetch(request);
    }
    return this.env.SITE.getByName('production').fetch(request);
  }

  // Service-binding RPC only. No HTTP endpoint exposes these mutations.
  publishSite(files, health) {
    return this.env.SITE.getByName('production').publishSite(files, health);
  }

  updateStatus(health) {
    return this.env.SITE.getByName('production').updateStatus(health);
  }
}
