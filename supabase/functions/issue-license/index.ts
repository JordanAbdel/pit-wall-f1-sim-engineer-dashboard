// issue-license — returns an Ed25519-signed offline licence for an
// entitled user. The app caches it in license.json and, when the licence
// server is unreachable, trusts it only if the signature checks out
// against the public key baked into the build. That makes the cache
// unforgeable by editing the file.
// Called by the PitWall app with the user's Supabase JWT in Authorization.
// Secrets:
//   LICENSE_SIGNING_KEY — base64 PKCS#8 DER Ed25519 private key
//                         (scripts/gen_license_keys.ts prints one)
//
// Token: base64url(JSON payload) + "." + base64url(signature over the
// first part's ASCII bytes). Payload: {v, sub, email, status, iat, exp}.
import { callerUser, json, loadProfile, preflight } from "../_shared/billing.ts";

const GRACE_S = 7 * 86_400;          // must match cloud.LICENSE_GRACE_DAYS
const ENTITLED = new Set(["active", "past_due"]);

const b64url = (bytes: Uint8Array) =>
  btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

let signingKey: CryptoKey | null = null;
async function key(): Promise<CryptoKey> {
  if (!signingKey) {
    const der = Uint8Array.from(
      atob(Deno.env.get("LICENSE_SIGNING_KEY")!), (c) => c.charCodeAt(0));
    signingKey = await crypto.subtle.importKey(
      "pkcs8", der, { name: "Ed25519" }, false, ["sign"]);
  }
  return signingKey;
}

Deno.serve(async (req) => {
  const pre = preflight(req);
  if (pre) return pre;
  try {
    const user = await callerUser(req);
    if (!user) return json({ error: "not authenticated" }, 401);

    const profile = await loadProfile(user.id);
    if (!ENTITLED.has(profile.subscription_status)) {
      return json({ error: "no active subscription" }, 403);
    }
    const now = Math.floor(Date.now() / 1000);
    const payload = b64url(new TextEncoder().encode(JSON.stringify({
      v: 1,
      sub: user.id,
      email: profile.email,
      status: profile.subscription_status,
      iat: now,
      exp: now + GRACE_S,
    })));
    const sig = new Uint8Array(await crypto.subtle.sign(
      { name: "Ed25519" }, await key(), new TextEncoder().encode(payload)));
    return json({ token: `${payload}.${b64url(sig)}` });
  } catch (e) {
    console.error("issue-license:", e);
    return json({ error: "could not issue licence" }, 500);
  }
});
