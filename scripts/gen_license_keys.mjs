// Generates the Ed25519 key pair for signed offline licences.
//   node scripts/gen_license_keys.mjs
// Prints two lines:
//   LICENSE_SIGNING_KEY — secret; `npx wrangler secret put` it, never commit it
//   LICENSE_PUBLIC_KEY  — goes in the app's .env (baked into builds)
const pair = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
const b64 = (buf) => Buffer.from(buf).toString("base64");
console.log(`LICENSE_SIGNING_KEY=${b64(await crypto.subtle.exportKey("pkcs8", pair.privateKey))}`);
console.log(`LICENSE_PUBLIC_KEY=${b64(await crypto.subtle.exportKey("raw", pair.publicKey))}`);
