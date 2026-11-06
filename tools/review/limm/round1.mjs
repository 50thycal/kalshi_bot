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
    id: 'r2-window-event-block',
    q: 'Round 2: the window trial is live but blocked. The bot never enters an event where another live book holds a position, and the mmsell book holds positions in this week’s “Trump says X” set. Should window markets be blocked only on the same word instead of the whole week?',
    options: [
      'Yes: for KXTRUMPSAY, block only a word another book holds or rests on, not the whole weekly set (recommended)',
      'No: keep the whole-week block; the trial runs only in weeks mmsell sits out',
      'Stop the window trial',
    ],
    why: 'Different words in one week resolve separately, unlike the words of one speech. mmsell is short YES on its words and we would be long YES on others, so a burst of words being said moves the two books in opposite directions rather than stacking risk. The same-word guard, which stops two books trading one market, stays. This loosens a live safeguard, so it waits on your answer.',
  },
  {
    id: 'r1-windows',
    q: 'The window-markets study says PROMISING. Start a small live trial?',
    options: [
      'Yes: cheap YES bids only, $10 each, one tick behind the best price, out 48 h before the window closes, inside the existing $100 / 10-market cap (recommended)',
      'Not yet: re-run the study next week and decide then',
      'Leave window markets out',
    ],
    why: 'Today 16 markets qualify at about $1.04 a day each. In 238 past entries, 69% filled but cost only $0.31 each on average (a 2–3c YES bid loses little), and 11 filled bids won. The worst single fill is the $10 bid. This changes what the live bot trades, so it waits on this answer.',
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

// The operator's own question at the top of each section (operator, 2026-11-06: "if I asked a
// question, that question should be at the top of that section or decision to remind me").
export const ASKED = {
  windows: { when: 'Nov 5, 9:10 PM CT', q: 'The “Trump says X before Dec 1” style markets are open-ended windows… Let’s explore those again.' },
  groc: { when: 'Nov 5, 9:10 PM CT', q: 'We tried GROC and SAFE yesterday around 10:00 UTC, but Kalshi rejected both. Why didn’t we try placing a resting order again? What happened?' },
  book: { when: 'Nov 5', q: 'What is my expected rewards? I only see one market we have a resting order on, is there more?' },
  decide: { when: 'Nov 5, 9:10 PM CT', q: 'Also can you change your settings to be in my time zone?' },
  built: { when: 'Nov 6, 5:40 AM CT', q: 'Yeah let’s set that up so we can still open up those resting orders for the incentive program.' },
};

// Round 1's answers read back (answers-2026-11-06.json) and what each one changed.
export const BUILT = [
  { id: 'r1-windows', title: 'Window markets: live trial', state: 'built', what: 'Live since Nov 5, 11:37 PM CT. It was blocked by mmsell’s positions in the weekly set; your round-2 answer unblocks it (per-word blocking, waiting on merge). KXTRUMPSAY (the weekly series you marked Go further) joins the book: cheap YES bids only, $10, one tick behind the best price, out 48 h before the window closes. But the bot never enters an event where another live book holds a position, and the mmsell book holds positions in this week’s set (Nov 9), as it does most weeks. All 32 markets are turned away for that reason. See the new question on Decide.' },
  { id: 'r1-stepaside', title: '24-hour step-aside', state: 'live', what: 'Mention-event bids now leave 24 h before the event instead of 48 h. Window markets keep 48 h. After the next two mention events I compare reward gained with fills taken.' },
  { id: 'r1-small', title: 'Skip events under $5', state: 'live', what: 'A mention event is entered only if the book’s projected reward there (held bids plus the best new ones, to their step-aside) is $5 or more.' },
  { id: 'r1-409', title: '409 fix', state: 'live', what: 'A refused (409) bid or sell is checked on the exchange at once and marked not placed, instead of after 15 minutes. This also stops maintenance 409s on resting sells being booked as “filled”.' },
  { id: 'r2-window-event-block', title: 'Window markets: block per word', state: 'built', what: 'A window market is now refused only when another book rests on or holds that same word, not when it holds any word of the week. Dated mention events keep the whole-event block. The bot also never bids a word the account already holds under any tag, so two books never net each other.' },
  { id: 'r1-grocsafe', title: 'Crowding filter', state: 'kept', what: 'No change: GROC and SAFE stay turned away while their books are too crowded to pay.' },
  { id: 'r1-youtube', title: 'YouTube leftovers', state: 'kept', what: 'No change: held, sells stay up at 2c.' },
  { id: 'r1-tz', title: 'Central time', state: 'kept', what: 'Confirmed. Reports and check-ins stay in CT.' },
];
