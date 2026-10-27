// Pure subscription-status rules shared by the billing endpoints. No
// imports, so the unit tests run without secrets.

// How strongly a Stripe subscription status entitles the user. When a
// customer has several subscriptions, the strongest one wins.
const RANK: Record<string, number> = {
  active: 4, trialing: 4, past_due: 3, unpaid: 2, paused: 2, incomplete: 1,
};

export function rank(status: string | null | undefined): number {
  return status ? RANK[status] ?? 0 : 0;
}

/** Subscriptions that still need managing, not a second checkout. */
export function blocksNewCheckout(status: string): boolean {
  return rank(status) >= 2;
}

/** Stripe subscription status -> profiles.subscription_status. */
export function profileStatus(subStatus: string | null): string {
  if (!subStatus) return "inactive";
  if (subStatus === "active" || subStatus === "trialing") return "active";
  if (subStatus === "past_due") return "past_due";
  if (subStatus === "incomplete") return "inactive";  // checkout not finished
  return "canceled";  // canceled, unpaid, incomplete_expired, paused
}

/** The subscription that decides the customer's state: the strongest
 * status, newest on a tie. */
export function strongest<T extends { status: string; created: number }>(
  subs: T[],
): T | null {
  let best: T | null = null;
  for (const s of subs) {
    if (!best || rank(s.status) > rank(best.status)
        || (rank(s.status) === rank(best.status) && s.created > best.created)) {
      best = s;
    }
  }
  return best;
}
