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
    id: 'r4-rule',
    q: 'Round 4: replace the $5-per-event rule with an earnings-per-hour test that entering and pulling both use?',
    options: [
      'Yes: rest a bid only while it earns at least 5c an hour; pull it below 3c an hour; skip a market that can’t reach $1.50 before it steps aside. A pulled market comes back once it clears 5c again (recommended)',
      'Keep the $5 event total, but add a gap: enter at $6 or more, pull under $4',
      'Drop the threshold: rest anything that qualifies',
    ],
    why: 'Fills cost about 3c per market-hour on mention events. Events that paid $5+ earned 5.5c an hour and netted +$31.25; events that paid $1–5 earned 3.3c and netted −$8.24. A $ total to step-aside shrinks as the clock runs, so the same bid passes on Monday and fails on Wednesday; a rate does not. The 2c gap stops a bid on the line flipping every hour. FREE earns about 1c an hour, so it stays out.',
  },
  {
    id: 'r4-calibrate',
    q: 'The new rule leans on the bot’s own reward estimate, which has never been checked against what Kalshi actually paid. Check it first?',
    options: [
      'Yes: a read-only check of the estimate against the 17 paid events, then build with the bars adjusted if it runs high or low (recommended)',
      'No: build now and judge it on the first two weeks',
    ],
    why: 'This study used real payouts. The bot decides with an estimate. If the estimate runs 1.5× high, a 5c bar quietly lets in 3c markets, which is break-even at best.',
  },
  {
    id: 'r4-mamdani',
    q: 'Until the new rule is live, what should happen to the Mamdani FREE bid?',
    options: [
      'Pull it and keep the Mamdani Nov 18 event out until the new rule ships (recommended)',
      'Leave the place-and-pull loop running',
    ],
    why: 'It earns about 1c an hour against about 3c an hour of fill risk, and the event is already −$16.25 from the BILL fill. Keeping it out lowers exposure; it is still a small change to the live bot, so it waits on this answer.',
  },
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
  study: { when: 'Nov 7, 7:55 PM CT', q: 'I think we need to look at how we’re approaching this. Maybe we need to modify that $5 threshold… there should be some logic in there that it should be allowed to pull and re-enter if it is back above the threshold. I’m curious if we would even earn anything with all these enters and exits.' },
  built: { when: 'Nov 6, 7:45 AM CT', q: 'Yes change the bot to audit existing resting bids and pull them if they are earning below the 5 dollar threshold.' },
};

// Round 1's answers read back (answers-2026-11-06.json) and what each one changed.
export const BUILT = [
  { id: 'r1-windows', title: 'Window markets: live trial', state: 'live', what: 'Live since Nov 6, 6:59 AM CT. First three bids on the weekly set (closes Nov 9), all cheap YES, $10 each: REFI 5c × 200, AUTO 4c × 250, QUAN 5c × 200. That fills this week’s 3-market cap; the words mmsell holds are skipped. They step aside 48 h before close.' },
  { id: 'r1-stepaside', title: '24-hour step-aside', state: 'live', what: 'Mention-event bids now leave 24 h before the event instead of 48 h. Window markets keep 48 h. After the next two mention events I compare reward gained with fills taken.' },
  { id: 'r1-small', title: 'Skip events under $5', state: 'live', what: 'A mention event is entered only if the book’s projected reward there (held bids plus the best new ones, to their step-aside) is $5 or more.' },
  { id: 'r1-409', title: '409 fix', state: 'live', what: 'A refused (409) bid or sell is checked on the exchange at once and marked not placed, instead of after 15 minutes. This also stops maintenance 409s on resting sells being booked as “filled”.' },
  { id: 'r2-window-event-block', title: 'Window markets: block per word', state: 'live', what: 'A window market is now refused only when another book rests on or holds that same word, not when it holds any word of the week. Dated mention events keep the whole-event block. The bot also never bids a word the account already holds under any tag, so two books never net each other.' },
  { id: 'r3-audit', title: 'Pull resting bids under $5', state: 'live', what: 'Live since Nov 6, 9:45 AM CT. First audit at 9:47 AM pulled all four resting bids: Mamdani FREE (event $3.68 to step-aside), Refinery ($2.06), and AUTO and QUAN ($0.00 each: both words firmed, AUTO’s best YES bid went from 5c to 15c, leaving our 4–5c bids too far behind to score). The book now holds no resting bids; the YouTube leftovers are unchanged.' },
  { id: 'r1-grocsafe', title: 'Crowding filter', state: 'kept', what: 'No change: GROC and SAFE stay turned away while their books are too crowded to pay.' },
  { id: 'r1-youtube', title: 'YouTube leftovers', state: 'kept', what: 'No change: held, sells stay up at 2c.' },
  { id: 'r1-tz', title: 'Central time', state: 'kept', what: 'Confirmed. Reports and check-ins stay in CT.' },
];

// Round 4 (2026-11-08): the threshold study. Figures are copied from
// study/study-2026-11-08.json (study/threshold_study.py over two ops reads), not estimated here.
// c/h = cents per market-hour of resting bid.
export const STUDY = {
  asOf: 'Nov 7, 7:30 PM CT',
  bars: [
    { k: 'Mention events that paid $5+', n: '6 events · 1,094 market-hours', rew: 5.5, fill: 2.6, net: 31.25 },
    { k: 'Mention events that paid $1–5', n: '5 events · 408 market-hours', rew: 3.3, fill: 5.3, net: -8.24 },
    { k: 'YouTube views (closed)', n: '9 events · 535 market-hours', rew: 4.3, fill: 2.5, net: 9.93 },
    { k: 'Moving numbers (Sept, dropped)', n: '71 events · 419 market-hours', rew: 0.8, fill: 6.2, net: -22.80 },
  ],
  free: { rew: 1.0, note: 'Mamdani FREE now: the audit projects $2.42 over ~240 hours to step-aside' },
  findings: [
    { h: 'Break-even is about 3c an hour', d: 'Across all 17 finished mention events, bids earned 4.6c per market-hour and fills cost 3.1c: +$23.78 net over 1,590 hours. A bid earning under ~3c an hour loses money on average.' },
    { h: 'The $5 line split the events, but only after the fact', d: 'Events that ended up paying $5+ netted +$31.25; those paying $1–5 netted −$8.24. The bot can’t know the final payout when it enters, so it needs a test it can apply up front: earnings per hour.' },
    { h: 'Kalshi’s $1 minimum bites', d: 'Four mention events rested 85 market-hours and paid $0: each market stayed under Kalshi’s $1 minimum. A market that can’t reach $1 before it steps aside earns nothing at any rate.' },
    { h: 'The FREE loop earns about nothing', d: 'FREE has been placed 7 times since Nov 6 and rested 48 hours in all. At ~1c an hour that’s roughly 50c, and the payout may not clear $1. The Mamdani event as a whole has rested 508 market-hours and lost $16.25 on one fill (BILL); its payout comes after Nov 18.' },
    { h: 'Losses are lumpy', d: 'Most of the fill cost comes from a few single fills of $5–16. Seventeen events is a small sample, so 5c / 3c is a starting point to review after two weeks, not a final answer.' },
  ],
};
