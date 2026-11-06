// Round 1 of the Alimm1 review page (2026-11-06): the operator's questions on
// the "Trump says X before <date>" window markets, why GROC and SAFE were not
// retried, and the decisions still open from the 72h reviews. The page
// (page.mjs) is generated from this module, so the page and the docs can't
// drift. Ids are never reused across rounds: a new round adds new ids.

export const ROUND = 'r1';
export const ROUND_DATE = '2026-11-06';
export const TZ_NOTE = 'Times are US Central (CT, UTC−6).';

// The book as of the last check-in (Nov 5, 7:15 PM CT). Figures are copied from
// the ops reads quoted in the check-in reports, not estimated here.
export const BOOK = {
  asOf: 'Nov 5, 7:15 PM CT',
  netSoFar: -42.6, // settled fills + confirmed rewards, lifetime
  netIfLeftoversZero: -57.71,
  rewardsConfirmed: 103.95,
  resting: [{ what: 'Mamdani “FREE” (Nov 18 mention)', side: 'YES', price: 9, qty: 111, usd: 10 }],
  held: [
    { what: 'YouTube FUE', side: 'NO', price: 1, qty: 500, exit: 'sell up at 2c' },
    { what: 'YouTube FUT', side: 'NO', price: 1, qty: 500, exit: 'sell up at 2c' },
    { what: 'YouTube WEE', side: 'NO', price: 1, qty: 500, exit: 'sell up at 2c' },
    { what: 'YouTube TAT', side: 'NO', price: 1, qty: 11, exit: 'sell up at 2c' },
  ],
  expected: [
    { when: '~Nov 9', what: 'YouTube views, December months', usd: '~$9.50' },
    { when: '~Nov 12', what: 'Ski markets', usd: '$0–8.80, unsure' },
    { when: '~Nov 19–20', what: 'Mamdani mention event', usd: '~$6–9' },
  ],
};

// The three window series the probe scores. `id` is the review id.
export const SERIES = [
  { id: 'win-say', ticker: 'KXTRUMPSAY', name: 'Trump says X this week', pitch: 'A weekly set of 40–80 words; each resolves YES the moment he says it.' },
  { id: 'win-month', ticker: 'KXTRUMPSAYMONTH', name: 'Trump says X this month', pitch: 'The same words over a month-long window, so a cheap YES rests longer.' },
  { id: 'win-company', ticker: 'KXTRUMPSAYCOMPANY', name: 'Trump names a company', pitch: 'Company names over a window; fewer words, thinner books.' },
];

// What we already knew before the probe, with its source.
export const PRIOR = [
  { k: 'Reward pools', v: '$50–100 per word, 40–80 words a week', src: 'Kalshi incentive programmes, Nov 5 scan' },
  { k: 'Our one past try', v: '$1.99 earned, no fills (Oct 12)', src: 'reward_ledger.py' },
  { k: 'Cheap YES 5–7c came true', v: '2 of 58', src: 'mmsell research, same series' },
  { k: 'Why dropped in October', v: 'No set time to step aside before (§9.55)', src: 'LIQUIDITY_INCENTIVE_THESIS.md' },
];

// GROC / SAFE: the sequence, in CT.
export const GROC_STEPS = [
  { t: 'Thu 4:00 AM', h: 'Bids sent', d: 'GROC NO 10c and SAFE NO 5c, $10 each, on the Mamdani Nov 18 event.' },
  { t: '4:00 AM', h: 'Kalshi: “conflict” (409)', d: 'The last minutes of the weekly Thursday maintenance. FREE, on the same event, had gone in fine six hours before.' },
  { t: '4:15 AM', h: 'Marked “not placed”', d: 'After 15 minutes the bot checked the exchange, found no order, and freed the market for a new try. No money moved.' },
  { t: 'Every hour since', h: 'Turned down: too crowded', d: 'Other traders’ size on GROC and SAFE is over 10× what the programme rewards, so our share rounds to almost nothing. The word is then skipped for an hour.' },
];

export const QUESTIONS = [
  {
    id: 'r1-windows',
    q: 'Window markets (“Trump says X before a date”): what should happen after the study?',
    options: [
      'Go by the study’s verdict: a small live trial if it says PROMISING, drop them if AVOID, keep watching if NOT-YET (recommended)',
      'Start a small live trial now, whatever the study says',
      'Leave window markets out',
    ],
    why: 'A trial would be cheap YES bids only, $10 each, behind the best price, out 48 hours before the window closes. Any live change still needs your go.',
  },
  {
    id: 'r1-grocsafe',
    q: 'GROC and SAFE keep being turned away as too crowded (other traders’ size over 10× the reward size). Keep that filter?',
    options: ['Keep it as is (recommended)', 'Loosen it for mention markets (say how in the note)'],
    why: 'In a crowded book our share of the pool is tiny, but a fill costs the same. The filter is why the book is quiet, not a fault.',
  },
  {
    id: 'r1-409',
    q: 'When Kalshi answers “conflict” (409), the bot waits 15 minutes before it decides the order never landed. Change that?',
    options: [
      'Fix it: check the exchange right away and settle the order’s status at once (recommended)',
      'Leave it: 15 minutes of delay is harmless',
    ],
    why: 'It changes bookkeeping only, not what is bid. It mostly shows up in the Thursday maintenance window.',
  },
  {
    id: 'r1-stepaside',
    q: 'Bids on mention events step aside 48 hours before the event. Try 24 hours?',
    options: [
      'Try 24 hours on the next two mention events, then compare (recommended)',
      'Keep 48 hours',
    ],
    why: 'The 48-hour rule cut reward more than it cut fill losses, and the bad fills landed days earlier anyway. The partial-fill cancel and $10 cap now limit those fills. The Oct 27-A rally paid $6.85, above the $5 bar set for keeping this idea.',
  },
  {
    id: 'r1-small',
    q: 'Skip mention events expected to pay under $5?',
    options: ['Yes, skip them (recommended)', 'No, bid on everything that qualifies'],
    why: 'Oct 13 ($3.31), Oct 17 ($1.70) and Oct 24 ($1.82) each paid less than one typical loss. With only one bid resting, skipping will make the book quieter still.',
  },
  {
    id: 'r1-youtube',
    q: 'The four YouTube leftovers (1,511 NO contracts bought at 1c, $15.11) have sells up at 2c. What should happen to them?',
    options: [
      'Hold them, sells stay up at 2c (recommended)',
      'Sell now at whatever the best bid is',
    ],
    why: 'They were bought at 1c, so selling now locks in close to the whole $15.11. Holding risks the same $15.11 with a chance the sells fill.',
  },
  {
    id: 'r1-tz',
    q: 'Reports and the check-ins now use US Central time (CT). Is that right?',
    options: ['Yes, Central', 'No: another zone (say which in the note)'],
    why: 'Set from your screenshot: 7:17 PM when it was 01:17 UTC.',
  },
];
