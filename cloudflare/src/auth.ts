// Email/password accounts: PBKDF2 password hashes, short-lived signed
// access tokens, and long-lived refresh tokens that are stored hashed and
// rotated on every use (a stolen, already-used token is worthless).
import { timingSafeEqual } from "./stripe.ts";

// Workers cap PBKDF2 at 100k iterations; the count is stored per user so
// it can be raised later without breaking existing passwords.
export const PBKDF2_ITERATIONS = 100_000;
export const ACCESS_TTL_S = 3600;
export const REFRESH_TTL_S = 60 * 86_400;

const enc = new TextEncoder();

export function b64(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s);
}

export function b64url(bytes: Uint8Array): string {
  return b64(bytes).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function fromB64(s: string): Uint8Array {
  const norm = s.replace(/-/g, "+").replace(/_/g, "/");
  const bin = atob(norm + "=".repeat((4 - (norm.length % 4)) % 4));
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

export function randomToken(bytes = 32): string {
  return b64url(crypto.getRandomValues(new Uint8Array(bytes)));
}

export async function sha256Hex(s: string): Promise<string> {
  const d = await crypto.subtle.digest("SHA-256", enc.encode(s));
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function hashPassword(password: string, salt: Uint8Array,
                                   iterations = PBKDF2_ITERATIONS): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw", enc.encode(password), "PBKDF2", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", hash: "SHA-256", salt, iterations }, key, 256);
  return b64(new Uint8Array(bits));
}

export async function checkPassword(password: string, hash: string,
                                    salt: string, iterations: number) {
  return timingSafeEqual(await hashPassword(password, fromB64(salt), iterations), hash);
}

// ---- access tokens: HS256 JWTs signed with AUTH_SECRET ----

async function hmacKey(secret: string) {
  return crypto.subtle.importKey("raw", enc.encode(secret),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
}

export interface Claims { sub: string; email: string; iat: number; exp: number }

export async function signAccess(secret: string, sub: string, email: string,
                                 now: number): Promise<string> {
  const head = b64url(enc.encode(JSON.stringify({ alg: "HS256", typ: "JWT" })));
  const body = b64url(enc.encode(JSON.stringify(
    { sub, email, iat: now, exp: now + ACCESS_TTL_S })));
  const sig = await crypto.subtle.sign("HMAC", await hmacKey(secret),
    enc.encode(`${head}.${body}`));
  return `${head}.${body}.${b64url(new Uint8Array(sig))}`;
}

export async function verifyAccess(secret: string, token: string,
                                   now: number): Promise<Claims | null> {
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  try {
    const ok = await crypto.subtle.verify("HMAC", await hmacKey(secret),
      fromB64(parts[2]), enc.encode(`${parts[0]}.${parts[1]}`));
    if (!ok) return null;
    const claims = JSON.parse(new TextDecoder().decode(fromB64(parts[1]))) as Claims;
    if (typeof claims.exp !== "number" || claims.exp <= now) return null;
    return claims;
  } catch {
    return null;
  }
}

export function normaliseEmail(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const email = raw.trim().toLowerCase();
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) && email.length <= 254 ? email : null;
}
