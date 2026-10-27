// Minimal Stripe client over fetch: the handful of calls PitWall makes,
// plus webhook signature checking. No SDK, so nothing to bundle and the
// API base can point at a local fake in tests (STRIPE_API_BASE).

const API_VERSION = "2023-10-16";   // what the Supabase functions pinned

export interface Subscription {
  id: string;
  status: string;
  created: number;
  current_period_end: number;
}

export class StripeError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

/** Stripe's form encoding: nested objects and arrays as a[b][0]=… keys. */
function form(params: Record<string, unknown>, prefix = ""): string[] {
  const out: string[] = [];
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null) continue;
    const key = prefix ? `${prefix}[${k}]` : k;
    if (typeof v === "object") {
      out.push(...form(v as Record<string, unknown>, key));
    } else {
      out.push(`${encodeURIComponent(key)}=${encodeURIComponent(String(v))}`);
    }
  }
  return out;
}

export class Stripe {
  constructor(private key: string, private base = "https://api.stripe.com") {}

  private async call<T>(method: string, path: string,
                        params: Record<string, unknown> = {},
                        idempotencyKey?: string): Promise<T> {
    const body = form(params).join("&");
    const headers: Record<string, string> = {
      Authorization: `Bearer ${this.key}`,
      "Stripe-Version": API_VERSION,
    };
    if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
    let url = `${this.base}${path}`;
    let init: RequestInit = { method, headers };
    if (method === "GET") {
      if (body) url += `?${body}`;
    } else {
      headers["Content-Type"] = "application/x-www-form-urlencoded";
      init = { ...init, body };
    }
    const res = await fetch(url, init);
    const data = await res.json() as { error?: { message?: string } };
    if (!res.ok) {
      throw new StripeError(res.status, data.error?.message ?? `Stripe HTTP ${res.status}`);
    }
    return data as T;
  }

  createCustomer(email: string, userId: string) {
    return this.call<{ id: string }>("POST", "/v1/customers",
      { email, metadata: { user_id: userId } }, `pitwall-customer-${userId}`);
  }

  retrieveCustomer(id: string) {
    return this.call<{ id: string; deleted?: boolean; metadata?: Record<string, string> }>(
      "GET", `/v1/customers/${encodeURIComponent(id)}`);
  }

  async listSubscriptions(customer: string): Promise<Subscription[]> {
    const r = await this.call<{ data: Subscription[] }>("GET", "/v1/subscriptions",
      { customer, status: "all", limit: 100 });
    return r.data;
  }

  async openCheckoutUrl(customer: string): Promise<string | null> {
    const r = await this.call<{ data: { url: string | null }[] }>(
      "GET", "/v1/checkout/sessions", { customer, status: "open", limit: 1 });
    return r.data[0]?.url ?? null;
  }

  createCheckout(params: Record<string, unknown>, idempotencyKey: string) {
    return this.call<{ url: string }>("POST", "/v1/checkout/sessions",
      params, idempotencyKey);
  }

  createPortal(customer: string, returnUrl: string) {
    return this.call<{ url: string }>("POST", "/v1/billing_portal/sessions",
      { customer, return_url: returnUrl });
  }
}

const hex = (buf: ArrayBuffer) =>
  [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");

/** Verify a Stripe-Signature header (t=…,v1=…) over the raw body.
 * Rejects anything older than `toleranceS`, so a captured request can't
 * be replayed later. */
export async function verifyWebhook(body: string, header: string | null,
                                    secret: string, nowS: number,
                                    toleranceS = 300): Promise<boolean> {
  if (!header || !secret) return false;
  let t = "";
  const sigs: string[] = [];
  for (const part of header.split(",")) {
    const [k, v] = part.split("=", 2);
    if (k === "t") t = v;
    else if (k === "v1" && v) sigs.push(v);
  }
  const ts = Number(t);
  if (!t || !Number.isFinite(ts) || Math.abs(nowS - ts) > toleranceS) return false;
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" },
    false, ["sign"]);
  const want = hex(await crypto.subtle.sign(
    "HMAC", key, new TextEncoder().encode(`${t}.${body}`)));
  return sigs.some((s) => timingSafeEqual(s, want));
}

export function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}
