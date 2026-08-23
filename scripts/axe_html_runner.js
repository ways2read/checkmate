#!/usr/bin/env node
/**
 * Run axe-core (and optionally collect images) on HTML pages via Puppeteer.
 *
 * Spawned with Ace's bundled Node. Resolves puppeteer / axe-core from ACE_ROOT
 * or NODE_PATH. Chrome comes from --chrome or PUPPETEER_CACHE_DIR.
 *
 * Usage:
 *   node axe_html_runner.js --pages-file pages.json --out out.json [--images-only]
 */
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const Module = require('module');

function argValue(flag) {
  const idx = process.argv.indexOf(flag);
  if (idx < 0 || idx + 1 >= process.argv.length) {
    return '';
  }
  return process.argv[idx + 1];
}

function hasFlag(flag) {
  return process.argv.includes(flag);
}

function requireFrom(name, roots) {
  const errors = [];
  const tryRoots = [];
  const seen = new Set();
  for (const root of roots) {
    if (!root) {
      continue;
    }
    for (const candidate of [root, path.join(root, 'node_modules')]) {
      const key = path.resolve(candidate);
      if (seen.has(key)) {
        continue;
      }
      seen.add(key);
      tryRoots.push(candidate);
    }
  }
  for (const root of tryRoots) {
    try {
      const resolved = Module._resolveFilename(name, {
        id: path.join(root, 'dummy.js'),
        filename: path.join(root, 'dummy.js'),
        paths: [root, ...Module._nodeModulePaths(root)],
      });
      return require(resolved);
    } catch (err) {
      errors.push(String(err && err.message ? err.message : err));
    }
  }
  const extra = errors.length ? `\n${errors.join('\n')}` : '';
  throw new Error(`Cannot find module ${name}${extra}`);
}

function fileExists(filePath) {
  try {
    return Boolean(filePath) && fs.existsSync(filePath) && fs.statSync(filePath).isFile();
  } catch (err) {
    return false;
  }
}

function systemChromePaths() {
  if (process.platform === 'win32') {
    const pf = process.env.ProgramFiles || 'C:\\Program Files';
    const pfx86 = process.env['ProgramFiles(x86)'] || '';
    const local = process.env.LOCALAPPDATA || '';
    const paths = [path.join(pf, 'Google', 'Chrome', 'Application', 'chrome.exe')];
    if (pfx86) {
      paths.push(path.join(pfx86, 'Google', 'Chrome', 'Application', 'chrome.exe'));
    }
    if (local) {
      paths.push(path.join(local, 'Google', 'Chrome', 'Application', 'chrome.exe'));
    }
    return paths;
  }
  if (process.platform === 'darwin') {
    return [
      '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
      '/Applications/Chromium.app/Contents/MacOS/Chromium',
    ];
  }
  return [
    '/usr/bin/google-chrome-stable',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium-browser',
    '/usr/bin/chromium',
  ];
}

function findChrome(cacheDir) {
  if (!cacheDir) {
    return '';
  }
  const root = path.resolve(cacheDir);
  if (!fs.existsSync(root)) {
    return '';
  }
  const wanted =
    process.platform === 'win32'
      ? new Set(['chrome.exe'])
      : new Set(['chrome', 'google-chrome', 'google-chrome-stable', 'Google Chrome for Testing', 'Chromium']);
  const stack = [root];
  while (stack.length) {
    const dir = stack.pop();
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch (err) {
      continue;
    }
    for (const entry of entries) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        stack.push(full);
        continue;
      }
      if (entry.isFile() && wanted.has(entry.name)) {
        return full;
      }
    }
  }
  return '';
}

function loadAxeSource(roots) {
  const names = ['@daisy/axe-core-for-ace', 'axe-core'];
  for (const name of names) {
    for (const root of roots) {
      try {
        const resolved = Module._resolveFilename(name, {
          id: path.join(root, 'dummy.js'),
          filename: path.join(root, 'dummy.js'),
          paths: Module._nodeModulePaths(root),
        });
        return fs.readFileSync(resolved, 'utf8');
      } catch (err) {
        // try next
      }
    }
  }
  return '';
}

function cssEscape(value) {
  if (typeof CSS !== 'undefined' && CSS.escape) {
    return CSS.escape(value);
  }
  return String(value).replace(/[^a-zA-Z0-9_-]/g, '\\$&');
}

const COLLECT_IMAGES_SOURCE = `(() => {
  const cssEscape = (value) => {
    if (typeof CSS !== 'undefined' && CSS.escape) return CSS.escape(value);
    return String(value).replace(/[^a-zA-Z0-9_-]/g, '\\\\$&');
  };
  const compact = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
  const firstSrcsetUrl = (raw) => {
    const first = String(raw || '').split(',')[0].trim();
    if (!first) return '';
    return first.split(/\\s+/)[0] || '';
  };
  const absoluteUrl = (raw) => {
    const text = String(raw || '').trim();
    if (!text || text.toLowerCase().startsWith('blob:')) return text;
    if (/^(data:|https?:|file:)/i.test(text)) return text;
    try {
      return new URL(text, location.href).href;
    } catch (err) {
      return text;
    }
  };
  const idsText = (attr) => {
    const bits = [];
    String(attr || '')
      .split(/\\s+/)
      .forEach((id) => {
        if (!id) return;
        const node = document.getElementById(id);
        if (node) bits.push(compact(node.innerText || node.textContent || ''));
      });
    return bits.filter(Boolean).join(' ');
  };
  const siblingText = (el, dir) => {
    const skip = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'IMG', 'SVG', 'PICTURE', 'VIDEO', 'CANVAS']);
    let cur = dir === 'prev' ? el.previousSibling : el.nextSibling;
    const parts = [];
    let hops = 0;
    while (cur && hops < 8 && parts.join(' ').length < 400) {
      if (cur.nodeType === 3) {
        const t = compact(cur.textContent);
        if (t) parts.push(t);
      } else if (cur.nodeType === 1 && !skip.has(cur.tagName)) {
        const t = compact(cur.innerText || cur.textContent || '');
        if (t) parts.push(t.slice(0, 240));
      }
      cur = dir === 'prev' ? cur.previousSibling : cur.nextSibling;
      hops += 1;
    }
    return dir === 'prev' ? parts.reverse().join(' ') : parts.join(' ');
  };
  const precedingHeading = (el) => {
    let node = el;
    while (node && node !== document.body) {
      let sib = node.previousElementSibling;
      while (sib) {
        if (/^H[1-6]$/.test(sib.tagName)) {
          return compact(sib.innerText || sib.textContent || '').slice(0, 160);
        }
        const inner = sib.querySelector && sib.querySelector('h1,h2,h3,h4,h5,h6');
        if (inner) {
          return compact(inner.innerText || inner.textContent || '').slice(0, 160);
        }
        sib = sib.previousElementSibling;
      }
      node = node.parentElement;
    }
    return '';
  };
  const selectorFor = (el) => {
    if (!el || el.nodeType !== 1) return '';
    if (el.id) return '#' + cssEscape(el.id);
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 6) {
      let part = node.tagName.toLowerCase();
      if (node.id) {
        parts.unshift('#' + cssEscape(node.id));
        break;
      }
      const parent = node.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter(
          (child) => child.tagName === node.tagName
        );
        if (same.length > 1) {
          part += ':nth-of-type(' + (same.indexOf(node) + 1) + ')';
        }
      }
      parts.unshift(part);
      node = node.parentElement;
    }
    return parts.join(' > ');
  };
  const figcaptionFor = (el) => {
    const figure = el.closest && el.closest('figure');
    if (!figure) return '';
    const cap = figure.querySelector('figcaption');
    return cap ? compact(cap.innerText || cap.textContent || '') : '';
  };
  const nearbyText = (el) => {
    const bits = [];
    const cap = figcaptionFor(el);
    if (cap) bits.push(cap);
    const described = idsText(el.getAttribute('aria-describedby'));
    if (described) bits.push(described);
    const labelled = idsText(el.getAttribute('aria-labelledby'));
    if (labelled) bits.push(labelled);
    const heading = precedingHeading(el);
    if (heading) bits.push(heading);
    const before = siblingText(el, 'prev');
    const after = siblingText(el, 'next');
    if (before) bits.push(before);
    if (after) bits.push(after);
    if (!before && !after && el.parentElement) {
      const parentBits = siblingText(el.parentElement, 'prev') + ' ' + siblingText(el.parentElement, 'next');
      const extra = compact(parentBits);
      if (extra) bits.push(extra.slice(0, 400));
    }
    const seen = new Set();
    const unique = [];
    bits.forEach((bit) => {
      const key = compact(bit);
      if (!key || seen.has(key)) return;
      seen.add(key);
      unique.push(key);
    });
    return unique.join(' ').slice(0, 800);
  };
  const isDecorative = (el, altPresent, alt) => {
    const role = (el.getAttribute('role') || '').toLowerCase();
    if (role === 'presentation' || role === 'none') return true;
    if (el.getAttribute('aria-hidden') === 'true') return true;
    if (altPresent && alt === '') return true;
    return false;
  };
  const srcFor = (el) => {
    const picture = el.closest && el.closest('picture');
    const pictureSrcset = picture
      ? firstSrcsetUrl(
          (picture.querySelector('source[srcset]') &&
            picture.querySelector('source[srcset]').getAttribute('srcset')) ||
            (picture.querySelector('source[src]') &&
              picture.querySelector('source[src]').getAttribute('src')) ||
            ''
        )
      : '';
    const raw =
      el.currentSrc ||
      el.getAttribute('src') ||
      el.getAttribute('data-src') ||
      el.getAttribute('data-lazy-src') ||
      el.getAttribute('data-original') ||
      firstSrcsetUrl(el.getAttribute('srcset') || el.getAttribute('data-srcset') || '') ||
      pictureSrcset ||
      el.getAttribute('href') ||
      el.getAttribute('xlink:href') ||
      '';
    return absoluteUrl(raw);
  };
  const record = (el, kind) => {
    const src = srcFor(el);
    if ((src || '').toLowerCase().startsWith('blob:')) return null;
    const altPresent = el.hasAttribute('alt');
    const alt = altPresent ? (el.getAttribute('alt') || '') : null;
    const role = el.getAttribute('role') || '';
    const ariaHidden = el.getAttribute('aria-hidden') === 'true';
    const ariaLabel = el.getAttribute('aria-label') || '';
    let width = 0;
    let height = 0;
    try {
      width = el.naturalWidth || (el.getBBox && el.getBBox().width) || el.clientWidth || 0;
      height = el.naturalHeight || (el.getBBox && el.getBBox().height) || el.clientHeight || 0;
    } catch (err) {
      width = el.clientWidth || 0;
      height = el.clientHeight || 0;
    }
    const rec = {
      kind,
      src,
      alt,
      altPresent,
      role,
      ariaHidden,
      ariaLabel,
      figcaption: figcaptionFor(el),
      nearbyText: nearbyText(el),
      selector: selectorFor(el),
      pageUrl: location.href,
      width: Math.round(width) || 0,
      height: Math.round(height) || 0,
      decorative: isDecorative(el, altPresent, alt || ''),
      svgMarkup: '',
    };
    if (kind === 'svg') {
      rec.svgMarkup = el.outerHTML || '';
    }
    return rec;
  };
  const items = [];
  const seen = new Set();
  const push = (rec) => {
    if (!rec) return;
    const key = [rec.kind, rec.src, rec.selector].join('|');
    if (seen.has(key)) return;
    seen.add(key);
    items.push(rec);
  };
  document.querySelectorAll('img').forEach((el) => push(record(el, 'img')));
  document.querySelectorAll('input[type="image"]').forEach((el) =>
    push(record(el, 'input'))
  );
  document.querySelectorAll('svg, [role="img"]').forEach((el) => {
    if (el.tagName && el.tagName.toLowerCase() === 'img') return;
    const kind = el.tagName && el.tagName.toLowerCase() === 'svg' ? 'svg' : 'role-img';
    push(record(el, kind));
  });
  return items;
})()`;

async function settleLazyImages(page) {
  await page.evaluate(async () => {
    const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    document.querySelectorAll('img[loading="lazy"]').forEach((el) => {
      try {
        el.loading = 'eager';
      } catch (err) {
        /* ignore */
      }
    });
    const started = Date.now();
    const maxSteps = 20;
    const maxMs = 3500;
    const step = Math.max(400, window.innerHeight || 800);
    let height = Math.max(
      (document.body && document.body.scrollHeight) || 0,
      (document.documentElement && document.documentElement.scrollHeight) || 0
    );
    let y = 0;
    let steps = 0;
    while (y < height + step && steps < maxSteps && Date.now() - started < maxMs) {
      window.scrollTo(0, y);
      await delay(80);
      y += step;
      steps += 1;
      height = Math.max(
        height,
        (document.body && document.body.scrollHeight) || 0,
        (document.documentElement && document.documentElement.scrollHeight) || 0
      );
    }
    window.scrollTo(0, 0);
    await delay(150);
  });
}

async function gotoPage(page, url) {
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
  return url;
}

function httpFallbackUrl(url) {
  if (!/^https:\/\//i.test(url)) {
    return '';
  }
  return 'http://' + url.slice('https://'.length);
}

function isTlsNavigationError(err) {
  const msg = String(err && err.message ? err.message : err);
  return /ERR_SSL|ERR_CERT|SSL_PROTOCOL|TLSV1|ERR_CONNECTION_CLOSED|ERR_CONNECTION_RESET/i.test(
    msg
  );
}

async function analyzePage(page, url, { axeSource, imagesOnly, loadDelayMs }) {
  const result = { url, error: null, axe: null, images: [] };
  try {
    let opened = url;
    try {
      opened = await gotoPage(page, url);
    } catch (err) {
      const httpUrl = httpFallbackUrl(url);
      if (!httpUrl || !isTlsNavigationError(err)) {
        throw err;
      }
      opened = await gotoPage(page, httpUrl);
      result.url = opened;
    }
    try {
      if (typeof page.waitForNetworkIdle === 'function') {
        await page.waitForNetworkIdle({ idleTime: 400, timeout: 3000 });
      }
    } catch (err) {
      /* analytics / chat widgets often never go idle */
    }
    if (loadDelayMs > 0) {
      await new Promise((resolve) => setTimeout(resolve, Math.min(loadDelayMs, 1500)));
    }
    try {
      await settleLazyImages(page);
    } catch (err) {
      /* still collect whatever is in the DOM */
    }
    result.images = await page.evaluate(COLLECT_IMAGES_SOURCE);
    if (!imagesOnly) {
      if (!axeSource) {
        result.error = 'axe-core source not found';
        return result;
      }
      await page.evaluate(axeSource);
      result.axe = await page.evaluate(async () => {
        if (typeof axe === 'undefined' || !axe.run) {
          return null;
        }
        return await axe.run(document, { resultTypes: ['violations', 'incomplete'] });
      });
    }
  } catch (err) {
    result.error = String(err && err.message ? err.message : err);
  }
  return result;
}

async function main() {
  const pagesFile = argValue('--pages-file');
  const outFile = argValue('--out');
  const chromeFlag = argValue('--chrome');
  const imagesOnly = hasFlag('--images-only');
  const loadDelayMs = Number(argValue('--load-delay-ms') || '1000') || 1000;
  const aceRoot = process.env.ACE_ROOT || '';
  const cacheDir = process.env.PUPPETEER_CACHE_DIR || '';

  if (!pagesFile || !outFile) {
    throw new Error('Usage: axe_html_runner.js --pages-file pages.json --out out.json');
  }
  const raw = JSON.parse(fs.readFileSync(pagesFile, 'utf8'));
  const urls = Array.isArray(raw) ? raw : raw.urls || raw.pages || [];
  if (!urls.length) {
    throw new Error('No page URLs in --pages-file');
  }

  const extraRoots = (process.env.ACE_REQUIRE_ROOTS || '')
    .split(path.delimiter)
    .map((item) => item.trim())
    .filter(Boolean);
  const nodePathRoots = (process.env.NODE_PATH || '')
    .split(path.delimiter)
    .map((item) => item.trim())
    .filter(Boolean);
  const roots = [
    aceRoot,
    ...extraRoots,
    ...nodePathRoots,
    process.cwd(),
    path.dirname(pagesFile),
  ].filter(Boolean);
  let puppeteer;
  try {
    puppeteer = requireFrom('puppeteer', roots);
  } catch (err) {
    puppeteer = requireFrom('puppeteer-core', roots);
  }
  let chrome = fileExists(chromeFlag) ? chromeFlag : '';
  if (!chrome) {
    chrome = findChrome(cacheDir);
  }
  if (!chrome && aceRoot) {
    chrome = findChrome(path.join(aceRoot, 'puppeteer'));
  }
  if (!chrome) {
    try {
      const computed =
        typeof puppeteer.executablePath === 'function' ? puppeteer.executablePath() : '';
      if (fileExists(computed)) {
        chrome = computed;
      }
    } catch (err) {
      chrome = '';
    }
  }
  if (!chrome) {
    chrome = systemChromePaths().find(fileExists) || '';
  }

  const axeSource = imagesOnly ? '' : loadAxeSource(roots);
  if (!imagesOnly && !axeSource) {
    throw new Error('axe-core not found under ACE_ROOT/node_modules');
  }

  process.stderr.write('PROGRESS 0 1 Opening Chrome…\n');
  let chromeProfile = '';
  try {
    chromeProfile = fs.mkdtempSync(path.join(os.tmpdir(), 'checkmate-chrome-'));
  } catch (err) {
    chromeProfile = '';
  }
  const launchOpts = {
    headless: true,
    ignoreHTTPSErrors: true,
    timeout: 60000,
    args: [
      '--no-sandbox',
      '--disable-gpu',
      '--disable-dev-shm-usage',
      '--disable-setuid-sandbox',
      '--no-first-run',
      '--no-default-browser-check',
      '--disable-extensions',
      // Fresh profiles still auto-upgrade http→https; that breaks HTTP-only hosts.
      '--disable-features=HttpsFirstBalancedMode,HttpsFirstBalancedModeAutoEnable,HttpsFirstModeV2,HttpsUpgrades,AutomaticHttpsUpgrades',
    ],
  };
  if (chromeProfile) {
    launchOpts.args.push(`--user-data-dir=${chromeProfile}`);
  }
  let browser;
  try {
    if (fileExists(chrome)) {
      browser = await puppeteer.launch({ ...launchOpts, executablePath: chrome });
    } else {
      try {
        browser = await puppeteer.launch(launchOpts);
      } catch (err) {
        try {
          browser = await puppeteer.launch({ ...launchOpts, channel: 'chrome' });
        } catch (err2) {
          const system = systemChromePaths().find(fileExists);
          if (!system) {
            throw err;
          }
          browser = await puppeteer.launch({ ...launchOpts, executablePath: system });
        }
      }
    }
    const pages = [];
    try {
      const page = await browser.newPage();
      await page.setViewport({ width: 1280, height: 800 });
      for (let i = 0; i < urls.length; i += 1) {
        const url = urls[i];
        process.stderr.write(`PROGRESS ${i + 1} ${urls.length} Loading page…\n`);
        pages.push(await analyzePage(page, url, { axeSource, imagesOnly, loadDelayMs }));
      }
    } finally {
      await browser.close();
    }

    const payload = { pages, chrome, engine: axeSource ? 'axe-core' : 'images-only' };
    fs.writeFileSync(outFile, JSON.stringify(payload), 'utf8');
  } finally {
    if (chromeProfile) {
      try {
        fs.rmSync(chromeProfile, { recursive: true, force: true });
      } catch (err) {
        /* ignore */
      }
    }
  }
}

main().catch((err) => {
  process.stderr.write(String(err && err.stack ? err.stack : err) + '\n');
  process.exit(1);
});
