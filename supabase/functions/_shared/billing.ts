// Shared by the billing edge functions (Supabase bundles `_shared/`
// alongside each function that imports it; it is never deployed alone).
import Stripe from "npm:stripe@14";
import { createClient, type User } from "npm:@supabase/supabase-js@2";
import { profileStatus, strongest } from "./status.ts";

export { blocksNewCheckout, rank } from "./status.ts";

export const stripe = new Stripe(Deno.env.get("STRIPE_SECRET_KEY")!, {
  apiVersion: "2023-10-16",
});

// Service role: bypasses RLS, and is the only role allowed to write the
// subscription columns of `profiles` (see the column-level grant).
export const admin = createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
);

export const APP_URL = Deno.env.get("APP_URL") ?? "http://127.0.0.1:8777";

// Kept for older app builds, whose dashboard called create-checkout from the
// browser. Current builds call from the app process, which ignores CORS.
const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type",
};

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status, headers: { ...cors, "Content-Type": "application/json" },
  });
}

export function preflight(req: Request): Response | null {
  return req.method === "OPTIONS" ? new Response("ok", { headers: cors }) : null;
}

/** The caller's Supabase user, from the JWT in Authorization — or null. */
export async function callerUser(req: Request): Promise<User | null> {
  const auth = req.headers.get("Authorization");
  if (!auth) return null;
  const { data: { user }, error } = await admin.auth.getUser(
    auth.replace(/^Bearer\s+/i, ""));
  return error ? null : user;
}

export interface Profile {
  id: string;
  email: string;
  subscription_status: string;
  stripe_customer_id: string | null;
}

export async function loadProfile(userId: string): Promise<Profile> {
  const { data, error } = await admin.from("profiles")
    .select("id, email, subscription_status, stripe_customer_id")
    .eq("id", userId).single();
  if (error) throw new Error(`profile lookup failed: ${error.message}`);
  return data as Profile;
}

// ---- subscription state, derived from Stripe (the source of truth) ----

export interface CustomerState {
  subscription: Stripe.Subscription | null;  // the strongest one, if any
  status: string;                            // profiles.subscription_status
}

/** The customer's current billing state, read live from Stripe. Reading
 * it fresh (rather than trusting the event payload) is what makes the
 * webhook order-independent and safe to replay. */
export async function customerState(customerId: string): Promise<CustomerState> {
  const subs = await stripe.subscriptions.list({
    customer: customerId, status: "all", limit: 100,
  });
  const best = strongest(subs.data);
  return { subscription: best, status: profileStatus(best?.status ?? null) };
}

/** The profile's Stripe customer, creating and linking one on first use.
 * One customer per user: repeat checkouts reuse it instead of minting a
 * new customer each time. */
export async function ensureCustomer(profile: Profile): Promise<string> {
  if (profile.stripe_customer_id) return profile.stripe_customer_id;
  const customer = await stripe.customers.create(
    { email: profile.email, metadata: { user_id: profile.id } },
    // Two racing requests get the same customer back from Stripe.
    { idempotencyKey: `pitwall-customer-${profile.id}` },
  );
  // Link it only if nothing else linked one first; if something did,
  // adopt theirs.
  const { data, error } = await admin.from("profiles")
    .update({ stripe_customer_id: customer.id })
    .eq("id", profile.id).is("stripe_customer_id", null)
    .select("stripe_customer_id");
  if (error) throw new Error(`could not link customer: ${error.message}`);
  if (data && data.length) return customer.id;
  const again = await loadProfile(profile.id);
  if (!again.stripe_customer_id) throw new Error("customer link vanished");
  return again.stripe_customer_id;
}
