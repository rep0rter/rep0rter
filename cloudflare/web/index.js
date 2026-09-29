import { DurableObject, WorkerEntrypoint } from 'cloudflare:workers';

const TYPES = {html:'text/html; charset=utf-8', xml:'application/rss+xml; charset=utf-8',
  css:'text/css; charset=utf-8', js:'text/javascript; charset=utf-8',
  png:'image/png', jpg:'image/jpeg', jpeg:'image/jpeg', woff2:'font/woff2',
  svg:'image/svg+xml', json:'application/json; charset=utf-8', txt:'text/plain; charset=utf-8'};

const MAX_FILE = 1_900_000;
const MAX_BATCH = 2 * 1024 * 1024;
const MAX_SITE = 64 * 1024 * 1024;
function validPath(path) {
  return typeof path === 'string' && path && !path.startsWith('/') && !/[\\\x00]/.test(path)
    && !path.split('/').some(part => part === '..' || part === '.' || !part);
}

export class PublishedSite extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.sql = ctx.storage.sql;
    this.sql.exec('CREATE TABLE IF NOT EXISTS assets(path TEXT PRIMARY KEY, data BLOB NOT NULL, digest TEXT NOT NULL)');
    this.sql.exec('CREATE TABLE IF NOT EXISTS status(key TEXT PRIMARY KEY, value TEXT NOT NULL)');
    this.sql.exec('CREATE TABLE IF NOT EXISTS pending_assets(path TEXT PRIMARY KEY, digest TEXT NOT NULL, size INTEGER NOT NULL, data BLOB)');
  }

  beginPublication(id, manifest) {
    if (!/^[a-f0-9]{32}$/.test(id) || !Array.isArray(manifest) || manifest.length > 10000)
      throw new Error('Invalid publication');
    const seen = new Set();
    let total = 0;
    for (const file of manifest) {
      if (!validPath(file.path) || seen.has(file.path) || !/^[a-f0-9]{64}$/.test(file.digest)
          || !Number.isSafeInteger(file.size) || file.size < 0 || file.size > MAX_FILE)
        throw new Error('Invalid public asset manifest');
      seen.add(file.path);
      total += file.size;
    }
    if (!seen.has('index.html') || !seen.has('feed.xml') || total > MAX_SITE)
      throw new Error('Incomplete or oversized site generation');
    this.ctx.storage.transactionSync(() => {
      this.sql.exec('DELETE FROM pending_assets');
      for (const file of manifest) {
        // Reuse unchanged bytes inside SQLite, without transferring or loading them.
        this.sql.exec('INSERT INTO pending_assets VALUES (?,?,?,(SELECT data FROM assets WHERE path=? AND digest=? AND length(data)=?))',
          file.path, file.digest, file.size, file.path, file.digest, file.size);
      }
      this.sql.exec('INSERT INTO status VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', 'publication', id);
    });
    return [...this.sql.exec('SELECT path FROM pending_assets WHERE data IS NULL')].map(row => row.path);
  }

  requirePublication(id) {
    const row = [...this.sql.exec("SELECT value FROM status WHERE key='publication'")][0];
    if (!row || row.value !== id) throw new Error('Publication expired or replaced');
  }

  stagePublication(id, files) {
    this.requirePublication(id);
    if (!Array.isArray(files)) throw new Error('Invalid publication batch');
    let total = 0;
    for (const file of files) {
      const size = file.data?.byteLength;
      const expected = [...this.sql.exec('SELECT digest,size FROM pending_assets WHERE path=?', file.path)][0];
      if (!expected || expected.digest !== file.digest || !Number.isSafeInteger(size) || size !== expected.size)
        throw new Error('Public asset does not match manifest');
      total += size;
    }
    if (total > MAX_BATCH) throw new Error('Publication batch too large');
    this.ctx.storage.transactionSync(() => {
      for (const file of files) this.sql.exec('UPDATE pending_assets SET data=? WHERE path=?', file.data, file.path);
    });
  }

  commitPublication(id, health) {
    this.requirePublication(id);
    if ([...this.sql.exec('SELECT path FROM pending_assets WHERE data IS NULL LIMIT 1')].length)
      throw new Error('Incomplete site generation');
    const count = [...this.sql.exec('SELECT count(*) AS count FROM pending_assets')][0].count;
    this.ctx.storage.transactionSync(() => {
      this.sql.exec('DELETE FROM assets WHERE path NOT IN (SELECT path FROM pending_assets)');
      this.sql.exec('INSERT OR REPLACE INTO assets SELECT path,data,digest FROM pending_assets WHERE NOT EXISTS (SELECT 1 FROM assets WHERE assets.path=pending_assets.path AND assets.digest=pending_assets.digest)');
      this.sql.exec('DELETE FROM pending_assets');
      this.sql.exec("DELETE FROM status WHERE key='publication'");
      this.updateStatus(health);
    });
    return {published: count};
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
      this.sql.exec('DELETE FROM pending_assets');
      this.sql.exec("DELETE FROM status WHERE key='publication'");
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
    if (path === 'ppt' || path === 'ppt/') path = 'ppt.html';
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
  beginPublication(id, manifest) {
    return this.env.SITE.getByName('production').beginPublication(id, manifest);
  }

  stagePublication(id, files) {
    return this.env.SITE.getByName('production').stagePublication(id, files);
  }

  commitPublication(id, health) {
    return this.env.SITE.getByName('production').commitPublication(id, health);
  }

  publishSite(files, health) {
    return this.env.SITE.getByName('production').publishSite(files, health);
  }

  updateStatus(health) {
    return this.env.SITE.getByName('production').updateStatus(health);
  }
}
