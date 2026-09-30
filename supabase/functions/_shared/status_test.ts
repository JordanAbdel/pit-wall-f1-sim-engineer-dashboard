// deno test supabase/functions/_shared
import { assertEquals } from "jsr:@std/assert@1";
import { blocksNewCheckout, profileStatus, strongest } from "./status.ts";

const sub = (status: string, created: number) => ({ status, created });

Deno.test("an old canceled subscription never outranks a live one", () => {
  // Whatever order Stripe delivers events in, the webhook re-lists the
  // customer's subscriptions and this pick decides the profile.
  const live = sub("active", 100);
  assertEquals(strongest([sub("canceled", 200), live]), live);
  assertEquals(strongest([live, sub("canceled", 200)]), live);
});

Deno.test("a late 'incomplete' does not revoke an active subscription", () => {
  const live = sub("active", 100);
  assertEquals(strongest([live, sub("incomplete", 50)]), live);
});

Deno.test("newest wins between equally strong subscriptions", () => {
  const newer = sub("canceled", 300);
  assertEquals(strongest([sub("canceled", 100), newer]), newer);
});

Deno.test("no subscriptions -> inactive", () => {
  assertEquals(strongest([]), null);
  assertEquals(profileStatus(null), "inactive");
});

Deno.test("status mapping", () => {
  assertEquals(profileStatus("trialing"), "active");
  assertEquals(profileStatus("past_due"), "past_due");
  assertEquals(profileStatus("incomplete"), "inactive");
  assertEquals(profileStatus("unpaid"), "canceled");
  assertEquals(profileStatus("incomplete_expired"), "canceled");
});

Deno.test("existing subscriptions block a second checkout", () => {
  for (const s of ["active", "trialing", "past_due", "unpaid", "paused"]) {
    assertEquals(blocksNewCheckout(s), true, s);
  }
  for (const s of ["canceled", "incomplete", "incomplete_expired"]) {
    assertEquals(blocksNewCheckout(s), false, s);
  }
});
