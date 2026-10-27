// PitWall licence API — Cloudflare Worker + D1.
//
// Replaces the Supabase project (auth + four edge functions) and answers
// on the same paths with the same JSON, so the app's cloud.py talks to it
// unchanged apart from the base URL:
//
//   POST /auth/v1/signup                         {email, password} -> session
//   POST /auth/v1/token?grant_type=password      {email, password} -> session
//   POST /auth/v1/token?grant_type=refresh_token {refresh_token}   -> session
//   GET  /auth/v1/user                           -> {id, email}
//   GET  /rest/v1/profiles?id=eq.<id>            -> [{subscription_status, email}]
//   POST /functions/v1/create-checkout           -> {url} (Stripe Checkout)
//   POST /functions/v1/create-portal-session     -> {url} (Customer Portal)
//   POST /functions/v1/issue-license             -> {token} (signed offline licence)
//   POST /functions/v1/stripe-webhook            Stripe events -> profiles
//
// A session is {access_token, refresh_token, token_type, expires_in, user}.
import {
  ACCESS_TTL_S, PBKDF2_ITERATIONS, REFRESH_TTL_S, b64, b64url, checkPassword,
  fromB64, hashPassword, normaliseEmail, randomToken, sha256Hex, signAccess,
  verifyAccess,
} from "./auth.ts";
import { blocksNewCheckout, profileStatus, rank, strongest } from "./status.ts";
import { Stripe, StripeError, verifyWebhook } from "./stripe.ts";

export interface Env {
  DB: D1Database;
  AUTH_SECRET: string;
  STRIPE_SECRET_KEY: string;
  STRIPE_WEBHOOK_SECRET: string;
  STRIPE_PRICE_ID: string;
  LICENSE_SIGNING_KEY: string;
  APP_URL?: string;
  TRIAL_DAYS?: string;
  STRIPE_API_BASE?: string;   // tests point this at a local fake
}

const GRACE_S = 7 * 86_400;          // must match cloud.LICENSE_GRACE_DAYS
const ENTITLED = new Set(["active", "past_due"]);
const MIN_PASSWORD = 8;
// Sign-in lockout: this many failures for one email inside the window.
const MAX_FAILURES = 10;
const FAILURE_WINDOW_S = 15 * 60;

const now = () => Math.floor(Date.now() / 1000);

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status, headers: { "Content-Type": "application/json" },
  });
}
const authError = (msg: string, status = 400) =>
  json({ error: "invalid_grant", error_description: msg }, status);

async function body(req: Request): Promise<Record<string, unknown>> {
  try {
    const b = await req.json();
    return b && typeof b === "object" ? b as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

interface Profile {
  id: string;
  email: string;
  subscription_status: string;
  stripe_customer_id: string | null;
}

// ---- auth ----------------------------------------------------------------

async function session(env: Env, userId: string, email: string) {
  const t = now();
  const refresh = randomToken();
  await env.DB.prepare(
    "insert into refresh_tokens (token_hash, user_id, expires_at) values (?, ?, ?)")
    .bind(await sha256Hex(refresh), userId, t + REFRESH_TTL_S).run();
  return json({
    access_token: await signAccess(env.AUTH_SECRET, userId, email, t),
    token_type: "bearer",
    expires_in: ACCESS_TTL_S,
    refresh_token: refresh,
    user: { id: userId, email },
  });
}

async function signup(env: Env, req: Request): Promise<Response> {
  const b = await body(req);
  const email = normaliseEmail(b.email);
  const password = typeof b.password === "string" ? b.password : "";
  if (!email) return authError("Enter a valid email address.");
  if (password.length < MIN_PASSWORD) {
    return authError(`Password should be at least ${MIN_PASSWORD} characters.`);
  }
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const id = crypto.randomUUID();
  try {
    await env.DB.batch([
      env.DB.prepare(
        "insert into users (id, email, password_hash, password_salt, password_iterations, created_at) values (?, ?, ?, ?, ?, ?)")
        .bind(id, email, await hashPassword(password, salt), b64(salt),
              PBKDF2_ITERATIONS, now()),
      env.DB.prepare("insert into profiles (id, email) values (?, ?)").bind(id, email),
    ]);
  } catch (e) {
    if (String(e).includes("UNIQUE")) return authError("User already registered");
    throw e;
  }
  return session(env, id, email);
}

async function passwordGrant(env: Env, req: Request): Promise<Response> {
  const b = await body(req);
  const email = normaliseEmail(b.email);
  const password = typeof b.password === "string" ? b.password : "";
  if (!email || !password) return authError("Invalid login credentials");
  const t = now();

  const fails = await env.DB.prepare(
    "select count, first_at from login_failures where email = ?")
    .bind(email).first<{ count: number; first_at: number }>();
  const recent = fails && t - fails.first_at < FAILURE_WINDOW_S;
  if (recent && fails.count >= MAX_FAILURES) {
    return authError("Too many sign-in attempts. Wait 15 minutes and try again.", 429);
  }

  const user = await env.DB.prepare(
    "select id, email, password_hash, password_salt, password_iterations from users where email = ?")
    .bind(email).first<{ id: string; email: string; password_hash: string;
                         password_salt: string; password_iterations: number }>();
  // Hash even for unknown emails, so timing doesn't reveal who has an account.
  const ok = user
    ? await checkPassword(password, user.password_hash, user.password_salt,
                          user.password_iterations)
    : (await hashPassword(password, new Uint8Array(16)), false);
  if (!ok || !user) {
    await env.DB.prepare(
      `insert into login_failures (email, count, first_at) values (?, 1, ?)
       on conflict(email) do update set
         count = case when ? - first_at < ? then count + 1 else 1 end,
         first_at = case when ? - first_at < ? then first_at else ? end`)
      .bind(email, t, t, FAILURE_WINDOW_S, t, FAILURE_WINDOW_S, t).run();
    return authError("Invalid login credentials");
  }
  await env.DB.prepare("delete from login_failures where email = ?").bind(email).run();
  return session(env, user.id, user.email);
}

async function refreshGrant(env: Env, req: Request): Promise<Response> {
  const b = await body(req);
  const token = typeof b.refresh_token === "string" ? b.refresh_token : "";
  if (!token) return authError("Invalid Refresh Token", 401);
  // Single use: delete and read back in one statement.
  const row = await env.DB.prepare(
    "delete from refresh_tokens where token_hash = ? returning user_id, expires_at")
    .bind(await sha256Hex(token)).first<{ user_id: string; expires_at: number }>();
  if (!row || row.expires_at <= now()) return authError("Invalid Refresh Token", 401);
  const user = await env.DB.prepare("select id, email from users where id = ?")
    .bind(row.user_id).first<{ id: string; email: string }>();
  if (!user) return authError("Invalid Refresh Token", 401);
  // Housekeeping: drop this user's expired tokens.
  await env.DB.prepare("delete from refresh_tokens where user_id = ? and expires_at <= ?")
    .bind(user.id, now()).run();
  return session(env, user.id, user.email);
}

async function caller(env: Env, req: Request) {
  const auth = req.headers.get("Authorization") ?? "";
  const token = auth.replace(/^Bearer\s+/i, "");
  return token ? verifyAccess(env.AUTH_SECRET, token, now()) : null;
}

async function loadProfile(env: Env, id: string): Promise<Profile | null> {
  return env.DB.prepare(
    "select id, email, subscription_status, stripe_customer_id from profiles where id = ?")
    .bind(id).first<Profile>();
}

// ---- billing -------------------------------------------------------------

const stripeFor = (env: Env) => new Stripe(env.STRIPE_SECRET_KEY, env.STRIPE_API_BASE);
const appUrl = (env: Env) => env.APP_URL || "http://127.0.0.1:8777";

/** The profile's Stripe customer, creating and linking one on first use. */
async function ensureCustomer(env: Env, p: Profile): Promise<string> {
  if (p.stripe_customer_id) return p.stripe_customer_id;
  const customer = await stripeFor(env).createCustomer(p.email, p.id);
  // Link only if nothing linked one first; if something did, adopt theirs.
  await env.DB.prepare(
    "update profiles set stripe_customer_id = ? where id = ? and stripe_customer_id is null")
    .bind(customer.id, p.id).run();
  const again = await loadProfile(env, p.id);
  if (!again?.stripe_customer_id) throw new Error("customer link vanished");
  return again.stripe_customer_id;
}

async function createCheckout(env: Env, userId: string): Promise<Response> {
  const p = await loadProfile(env, userId);
  if (!p) return json({ error: "not authenticated" }, 401);
  const stripe = stripeFor(env);
  const customer = await ensureCustomer(env, p);
  const subs = await stripe.listSubscriptions(customer);
  if (subs.some((s) => blocksNewCheckout(s.status))) {
    return json({
      error: "You already have a subscription. Use MANAGE SUBSCRIPTION "
        + "to update or cancel it.",
      manage: true,
    }, 409);
  }
  const open = await stripe.openCheckoutUrl(customer);
  if (open) return json({ url: open });
  const trialDays = Number(env.TRIAL_DAYS ?? 0);
  const session = await stripe.createCheckout({
    mode: "subscription",
    customer,
    client_reference_id: userId,
    line_items: [{ price: env.STRIPE_PRICE_ID, quantity: 1 }],
    subscription_data: {
      metadata: { user_id: userId },
      ...(trialDays > 0 ? { trial_period_days: trialDays } : {}),
    },
    success_url: `${appUrl(env)}/?billing=success`,
    cancel_url: `${appUrl(env)}/?billing=cancelled`,
  }, `pitwall-checkout-${customer}-${Math.floor(Date.now() / 60_000)}`);
  return json({ url: session.url });
}

async function createPortal(env: Env, userId: string): Promise<Response> {
  const p = await loadProfile(env, userId);
  if (!p?.stripe_customer_id) return json({ error: "No subscription to manage yet." }, 404);
  const s = await stripeFor(env).createPortal(p.stripe_customer_id,
    `${appUrl(env)}/?billing=portal`);
  return json({ url: s.url });
}

let signingKey: CryptoKey | null = null;

async function issueLicense(env: Env, userId: string): Promise<Response> {
  const p = await loadProfile(env, userId);
  if (!p || !ENTITLED.has(p.subscription_status)) {
    return json({ error: "no active subscription" }, 403);
  }
  signingKey ??= await crypto.subtle.importKey(
    "pkcs8", fromB64(env.LICENSE_SIGNING_KEY), { name: "Ed25519" }, false, ["sign"]);
  const t = now();
  const payload = b64url(new TextEncoder().encode(JSON.stringify({
    v: 1, sub: userId, email: p.email, status: p.subscription_status,
    iat: t, exp: t + GRACE_S,
  })));
  const sig = new Uint8Array(await crypto.subtle.sign(
    { name: "Ed25519" }, signingKey, new TextEncoder().encode(payload)));
  return json({ token: `${payload}.${b64url(sig)}` });
}

// Stripe neither orders nor de-duplicates deliveries, so an event is only a
// hint about which customer changed: re-read that customer's subscriptions
// and write the current state. Late, repeated or out-of-order events all
// converge on the truth; any failure returns 500 so Stripe retries.
const HANDLED = new Set([
  "checkout.session.completed",
  "customer.subscription.created",
  "customer.subscription.updated",
  "customer.subscription.deleted",
  "customer.subscription.paused",
  "customer.subscription.resumed",
  "invoice.paid",
  "invoice.payment_failed",
]);

interface StripeEvent {
  id: string;
  type: string;
  data: { object: { customer?: string | { id: string } | null; client_reference_id?: string | null } };
}

async function syncCustomer(env: Env, event: StripeEvent): Promise<string> {
  const c = event.data.object.customer;
  const customerId = !c ? null : typeof c === "string" ? c : c.id;
  if (!customerId) return "no customer on event";
  const stripe = stripeFor(env);

  let userId: string | null = null;
  if (event.type === "checkout.session.completed") {
    userId = event.data.object.client_reference_id ?? null;
  }
  if (!userId) {
    const cust = await stripe.retrieveCustomer(customerId);
    userId = (!cust.deleted && cust.metadata?.user_id) || null;
  }
  if (!userId) {
    const row = await env.DB.prepare("select id from profiles where stripe_customer_id = ?")
      .bind(customerId).first<{ id: string }>();
    userId = row?.id ?? null;
  }
  if (!userId) return `customer ${customerId} is not linked to a user`;

  const profile = await loadProfile(env, userId);
  if (!profile) return `user ${userId} has no profile (deleted?)`;

  const sub = strongest(await stripe.listSubscriptions(customerId));
  // A second, older customer may only take over when it is the stronger one,
  // so a stale cancel there can't revoke a live subscription here.
  const linked = profile.stripe_customer_id;
  if (linked && linked !== customerId) {
    const current = strongest(await stripe.listSubscriptions(linked));
    if (rank(current?.status) >= rank(sub?.status)) {
      return `ignored: ${customerId} is secondary to ${linked}`;
    }
  }
  const status = profileStatus(sub?.status ?? null);
  await env.DB.prepare(
    `update profiles set subscription_status = ?, stripe_customer_id = ?,
       stripe_subscription_id = ?, current_period_end = ?, billing_synced_at = ?
     where id = ?`)
    .bind(status, customerId, sub?.id ?? null, sub?.current_period_end ?? null,
          now(), userId).run();
  return `${userId} -> ${status}`;
}

async function webhook(env: Env, req: Request): Promise<Response> {
  const raw = await req.text();
  if (!await verifyWebhook(raw, req.headers.get("stripe-signature"),
                           env.STRIPE_WEBHOOK_SECRET, now())) {
    return new Response("bad signature", { status: 400 });
  }
  const event = JSON.parse(raw) as StripeEvent;
  if (!HANDLED.has(event.type)) return json({ received: true, ignored: true });
  try {
    console.log(`${event.id} ${event.type}: ${await syncCustomer(env, event)}`);
  } catch (e) {
    console.error(`${event.id} ${event.type} failed:`, e);
    return new Response("handler error", { status: 500 });
  }
  return json({ received: true });
}

// ---- routing -------------------------------------------------------------

async function route(req: Request, env: Env): Promise<Response> {
  const url = new URL(req.url);
  const path = url.pathname.replace(/\/+$/, "");
  const post = req.method === "POST";

  if (post && path === "/auth/v1/signup") return signup(env, req);
  if (post && path === "/auth/v1/token") {
    const grant = url.searchParams.get("grant_type");
    if (grant === "password") return passwordGrant(env, req);
    if (grant === "refresh_token") return refreshGrant(env, req);
    return authError("unsupported grant_type");
  }
  if (post && path === "/functions/v1/stripe-webhook") return webhook(env, req);

  const claims = await caller(env, req);
  const signedIn = path === "/auth/v1/user" || path === "/rest/v1/profiles"
    || path.startsWith("/functions/v1/");
  if (signedIn && !claims) return json({ error: "not authenticated" }, 401);

  if (req.method === "GET" && path === "/auth/v1/user") {
    return json({ id: claims!.sub, email: claims!.email });
  }
  if (req.method === "GET" && path === "/rest/v1/profiles") {
    // Only ever your own row, like the old row-level security policy.
    if (url.searchParams.get("id") !== `eq.${claims!.sub}`) return json([]);
    const p = await loadProfile(env, claims!.sub);
    return json(p ? [{ subscription_status: p.subscription_status, email: p.email }] : []);
  }
  if (post && path === "/functions/v1/create-checkout") return createCheckout(env, claims!.sub);
  if (post && path === "/functions/v1/create-portal-session") return createPortal(env, claims!.sub);
  if (post && path === "/functions/v1/issue-license") return issueLicense(env, claims!.sub);
  if (path === "" || path === "/health") return json({ ok: true, service: "pitwall-api" });
  return json({ error: "not found" }, 404);
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    try {
      return await route(req, env);
    } catch (e) {
      console.error(req.method, new URL(req.url).pathname, e);
      const msg = e instanceof StripeError
        ? "Billing is unavailable right now — try again shortly."
        : "Something went wrong — try again shortly.";
      return json({ error: msg }, 500);
    }
  },
};
