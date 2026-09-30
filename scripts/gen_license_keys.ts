// Generates the Ed25519 key pair for signed offline licences.
//   deno run scripts/gen_license_keys.ts
// Prints two lines:
//   LICENSE_SIGNING_KEY — secret; `supabase secrets set` it, never commit it
//   LICENSE_PUBLIC_KEY  — goes in the app's .env (baked into builds)
const pair = await crypto.subtle.generateKey(
  { name: "Ed25519" }, true, ["sign", "verify"]) as CryptoKeyPair;
const b64 = (buf: ArrayBuffer) => btoa(String.fromCharCode(...new Uint8Array(buf)));
console.log(`LICENSE_SIGNING_KEY=${b64(await crypto.subtle.exportKey("pkcs8", pair.privateKey))}`);
console.log(`LICENSE_PUBLIC_KEY=${b64(await crypto.subtle.exportKey("raw", pair.publicKey))}`);
