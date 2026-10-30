/**
 * house-alert: "Dagelijkse mail instellen" from the overview page.
 *
 * GitHub Pages is static, so the page cannot write to the repository itself.
 * This Cloudflare Worker (free tier, one file, no build step) is the hand
 * that does: it receives the name, the sealed address and the filters, turns
 * them into config/subscriptions/<slug>.yaml and commits that file with a
 * fine-grained token that can touch nothing but this repo's contents.
 *
 * The address arrives already encrypted (see src/house_alert/sealed.py): the
 * Worker never sees it in clear, and neither does anyone reading the repo.
 *
 * Variables (Worker → Settings → Variables and Secrets):
 *   GITHUB_TOKEN       secret; fine-grained PAT, this repo only, Contents: read and write
 *   GITHUB_REPO        "tvt93/house-alert"
 *   ALLOWED_ORIGIN     "https://tvt93.github.io"
 *   MAX_SUBSCRIPTIONS  optional, default 20: refuse new files beyond this many
 */

const TYPES = ["house", "apartment", "land", "commercial", "parking"];
const RANGES = ["price", "bedrooms", "living_area_m2", "plot_area_m2"];
const DIR = "config/subscriptions";

export default {
  async fetch(request, env) {
    const cors = {
      "Access-Control-Allow-Origin": env.ALLOWED_ORIGIN || "",
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
      "Access-Control-Max-Age": "86400",
      "Vary": "Origin",
    };
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    if (request.method !== "POST") return reply({ error: "POST only" }, 405, cors);
    if ((request.headers.get("Origin") || "") !== env.ALLOWED_ORIGIN) {
      return reply({ error: "origin not allowed" }, 403, cors);
    }
    if (!env.GITHUB_TOKEN || !env.GITHUB_REPO) {
      return reply({ error: "worker not configured (GITHUB_TOKEN / GITHUB_REPO)" }, 500, cors);
    }

    const text = await request.text();
    if (text.length > 20000) return reply({ error: "request too large" }, 413, cors);
    let body;
    try { body = JSON.parse(text); } catch { return reply({ error: "invalid JSON" }, 400, cors); }

    const parsed = validate(body);
    if (parsed.error) return reply({ error: parsed.error }, 400, cors);

    const gh = github(env);
    const existing = await gh.list(DIR);
    if (existing === null) return reply({ error: "GitHub did not answer; try again later" }, 502, cors);
    const limit = Number(env.MAX_SUBSCRIPTIONS) || 20;
    if (existing.length >= limit) {
      return reply({ error: "het maximum aantal mails is bereikt; vraag de beheerder er een te verwijderen" }, 429, cors);
    }

    // A second subscription with the same name gets -2, -3, ... rather than
    // overwriting someone else's mail.
    const taken = new Set(existing.map((f) => f.name.toLowerCase()));
    let file = parsed.slug + ".yaml", n = 2;
    while (taken.has(file)) file = parsed.slug + "-" + (n++) + ".yaml";

    const ok = await gh.put(DIR + "/" + file, toYaml(parsed), "Dagelijkse mail: " + parsed.name);
    if (!ok) return reply({ error: "GitHub weigerde het bestand; probeer later opnieuw" }, 502, cors);
    return reply({ ok: true, file, name: parsed.name }, 201, cors);
  },
};

function reply(obj, status, headers) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { ...headers, "Content-Type": "application/json; charset=utf-8" },
  });
}

// ------------------------------------------------------------------ input

function validate(b) {
  if (!b || typeof b !== "object") return { error: "expected an object" };

  const name = String(b.name || "").replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim().slice(0, 60);
  if (!name) return { error: "naam ontbreekt" };
  const slug = name.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "filter";

  const to = String(b.to || "").trim();
  // enc: + base64 of (32 + 24 + 16 + address) bytes: 73 bytes of overhead
  // and an address of at most ~254 characters.
  if (!/^enc:[A-Za-z0-9+/]{96,480}={0,2}$/.test(to)) return { error: "adres niet versleuteld" };

  let types = Array.isArray(b.property_types) ? b.property_types : [];
  types = [...new Set(types.map(String).map((t) => t.toLowerCase()))].filter((t) => TYPES.includes(t));
  if (Array.isArray(b.property_types) && b.property_types.length && !types.length) {
    return { error: "onbekend woningtype" };
  }

  const ranges = {};
  for (const key of RANGES) {
    const r = b[key] && typeof b[key] === "object" ? b[key] : {};
    const min = numberOrNull(r.min), max = numberOrNull(r.max);
    if (min === false || max === false) return { error: key + ": geen geldig getal" };
    if (min !== null && max !== null && min > max) return { error: key + ": min is groter dan max" };
    ranges[key] = { min, max, include_unknown: r.include_unknown !== false };
  }

  let postcodes = Array.isArray(b.postcodes) ? b.postcodes.map((p) => String(p).trim()) : [];
  if (postcodes.length > 50 || postcodes.some((p) => !/^\d{1,4}$/.test(p))) {
    return { error: "postcodes: 1 tot 4 cijfers, hoogstens 50" };
  }
  postcodes = [...new Set(postcodes)];

  return { name, slug, to, types, ranges, postcodes };
}

function numberOrNull(v) {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(String(v).replace(",", "."));
  if (!Number.isFinite(n) || n < 0 || n > 1e9) return false;
  return n;
}

// ------------------------------------------------------------------- yaml

function q(s) { return "'" + String(s).replace(/'/g, "''") + "'"; }

function range(key, r) {
  return key + ":\n  min: " + (r.min === null ? "" : r.min) + "\n  max: " + (r.max === null ? "" : r.max) +
    "\n  include_unknown: " + (r.include_unknown ? "true" : "false") + "\n";
}

function list(items, quote) {
  return items.length ? "\n" + items.map((x) => "  - " + (quote ? q(x) : x)).join("\n") : " []";
}

function toYaml(p) {
  return "# Dagelijkse mail, ingesteld vanaf de overzichtspagina op " + new Date().toISOString().slice(0, 10) + ".\n" +
    "# Het adres is versleuteld met de sleutel van de site; alleen de mailrun (MAIL_KEY) kan het lezen.\n" +
    "# Verwijder dit bestand om de mail te stoppen.\n\n" +
    "name: " + q(p.name) + "\n" +
    "to:\n  - " + q(p.to) + "\n" +
    "enabled: true\n\n" +
    "property_types:" + list(p.types, false) + "\n\n" +
    RANGES.map((k) => range(k, p.ranges[k])).join("\n") + "\n" +
    "postcodes:" + list(p.postcodes, true) + "\n\n" +
    "municipalities:\n  include: []\n  exclude: []\n\n" +
    "exclude_keywords:\n  - garagebox\n  - staanplaats\n  - parkeerplaats\n  - opbrengsteigendom\n  - nieuwbouwproject\n  - handelspand\n\n" +
    "highlight_keywords:\n  - tuin\n  - zuidgericht\n  - te renoveren\n";
}

// ----------------------------------------------------------------- github

function github(env) {
  const base = "https://api.github.com/repos/" + env.GITHUB_REPO + "/contents/";
  const headers = {
    "Authorization": "Bearer " + env.GITHUB_TOKEN,
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "house-alert-subscribe-worker",
  };
  return {
    // The files in a directory, [] when it does not exist yet, null on failure.
    async list(path) {
      const res = await fetch(base + path, { headers });
      if (res.status === 404) return [];
      if (!res.ok) return null;
      const data = await res.json();
      return Array.isArray(data) ? data.filter((f) => f.type === "file" && /\.ya?ml$/i.test(f.name)) : [];
    },
    async put(path, content, message) {
      const res = await fetch(base + path, {
        method: "PUT",
        headers: { ...headers, "Content-Type": "application/json" },
        body: JSON.stringify({ message, content: b64utf8(content), committer: { name: "house-alert[bot]", email: "house-alert@users.noreply.github.com" } }),
      });
      return res.ok;
    },
  };
}

function b64utf8(s) {
  const bytes = new TextEncoder().encode(s);
  let bin = "";
  for (const byte of bytes) bin += String.fromCharCode(byte);
  return btoa(bin);
}
