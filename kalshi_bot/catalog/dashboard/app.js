'use strict';
const $ = id => document.getElementById(id);
let token = '', generation = 0, busy = false;
const number = value => Number(value || 0).toLocaleString();
const date = value => value ? new Date(value).toLocaleString() : 'Never recorded';
const gb = value => (value / 1e9).toFixed(1) + ' GB';
function node(tag, text, className) { const el = document.createElement(tag); el.textContent = text; if(className) el.className = className; return el; }
function pill(text, tone = 'neutral') { return node('span', text, 'pill ' + tone); }
function row(parent, values) { const tr = document.createElement('tr'); for(const value of values) { const td = document.createElement('td'); td.append(value instanceof Node ? value : node('span', value)); tr.append(td); } parent.append(tr); }
function stage(title, state, detail, tone) { const el = node('article', '', 'stage'); el.append(node('h3', title), pill(state, tone), node('p', detail)); $('pipeline').append(el); }
function notice(text) { $('message').textContent = text; $('message').hidden = !text; }
async function api(path, currentToken) {
  const response = await fetch(path, {headers:{Authorization:'Bearer ' + currentToken},cache:'no-store',signal:AbortSignal.timeout(45000)});
  if(response.status === 401) throw new Error('Token not accepted. Check CATALOG_API_TOKEN in Railway and reconnect.');
  if(!response.ok) throw new Error('Catalog request failed (' + response.status + '). Check the service and try again.');
  return response.json();
}
function renderStatus(s) {
  const jobs = s.jobs || {}, reviews = s.series_reviews || {}, reviewed = reviews.reviewed || 0;
  $('series').textContent = number(s.objects?.series); $('markets').textContent = number(s.objects?.market);
  $('reviews').textContent = number(reviewed); $('review-detail').textContent = number(Math.max(0,(s.objects?.series || 0)-reviewed)) + ' series awaiting a current structured review';
  $('contexts').textContent = number(s.assessment_contexts); $('pipeline').replaceChildren();
  const discovery = ['series','events'].every(k => jobs['discovery:' + k]?.complete);
  const discoveryErrors = Object.entries(jobs).some(([k,v])=>k.startsWith('job:discovery:') && v?.error);
  stage('1. Discovery', discoveryErrors ? 'Needs attention' : discovery ? 'Pass complete' : 'Collecting', 'Public series, events, and market updates. Historical listings included.', discoveryErrors ? 'warn' : 'good');
  const migration = jobs.registry_seed || {}, economics = jobs.live_economics_summary || {};
  const archive=s.contract_archive || {}, targets=archive.targets_by_status || {}, failures=targets.refresh_failed || 0;
  stage('2. Contract documents', failures ? 'Needs attention' : archive.blobs ? 'Capturing' : 'Awaiting capture', number(archive.blobs)+' distinct documents saved; '+number(targets.not_captured)+' links awaiting capture; '+number(failures)+' failed refreshes. Saved documents do not prove which terms governed an older trade.', failures?'warn':'neutral');
  stage('3. Reviews', reviewed ? 'In progress' : 'Needs structured review', number(migration.series) + ' legacy classifications migrated; ' + number(migration.historical_signatures) + ' historical signatures. Missing semantic fields remain visible.', 'neutral');
  const imported = ['paper','live'].every(k=>s.backfill?.[k]?.initial_complete);
  const permissions = jobs.source_permissions;
  stage('4. Evidence', imported ? 'Import verified' : permissions?.accepted === false ? 'Connection blocked' : 'Import incomplete', 'Paper and live history remain separate. Completion requires verified source-ID coverage.', imported ? 'good' : 'warn');
  stage('5. Live economics', number(economics.attributed) + ' attributable', number(economics.blocked) + ' markets blocked. Results require exclusive ownership, complete source fills, actual fees, and final settlement.', economics.blocked ? 'warn' : 'neutral');
  stage('6. Scoring', s.confidence_calibrated ? 'Calibrated' : 'Calibration pending', 'Descriptive assessments exist. Unknown confidence is not a zero score.', s.confidence_calibrated ? 'good' : 'neutral');
  stage('7. Strategy connection', s.consumer_cutover ? 'Connected' : 'Not connected', 'Catalog selections are advisory. This page does not authorize or activate trades.', 'neutral');
  $('attention').replaceChildren();
  const add = text => $('attention').append(node('li',text));
  if(failures) add(number(failures)+' contract document refreshes failed. Previous captures remain available; inspect the review packets before using the terms.');
  if(permissions?.accepted === false) add('Historical import blocked: configure CATALOG_SOURCE_DATABASE_URL with the genuine bot_readonly account and its own password, then redeploy. An administrator URL is refused.');
  else if(!imported) add('Finish and verify the historical paper and live imports. Missing coverage means incomplete, even if a seed is present.');
  if(!reviewed) add('Complete the semantic fields for migrated legacy reviews. A historical signature alone does not approve the current rules.');
  else if(reviewed < (s.objects?.series || 0)) add('Continue structured reviews for the intended strategy universe; unreviewed series stay unqualified.');
  if(economics.blocked) add(number(economics.blocked) + ' live markets have incomplete or ambiguous economics. See the authenticated live-economics API for specific reasons.');
  if(!s.confidence_calibrated) add('Validate attributable live economics, independent outcomes, and forward evidence before publishing confidence scores.');
  if(!s.consumer_cutover) add('Compare selections in advisory mode before a controlled, versioned strategy cutover.');
  const storage = jobs['storage:metrics'];
  if(storage?.volume_total_bytes > 0) {
    const used = storage.volume_total_bytes - storage.volume_free_bytes;
    $('storage').textContent = gb(storage.volume_free_bytes) + ' free';
    $('storage-detail').textContent = gb(used) + ' used of ' + gb(storage.volume_total_bytes) + '. Database: ' + gb(storage.file_bytes?.database || 0) + '. Volume usage includes backups and other files.';
    $('storage-meter').value = Math.max(0,Math.min(100,used / storage.volume_total_bytes * 100));
    $('storage-time').textContent = 'Measured ' + date(storage.captured_at);
    if(storage.volume_free_bytes / storage.volume_total_bytes < .2) add('Volume has less than 20% free space. Review growth and capacity before it fills.');
  } else { $('storage').textContent='Not measured yet'; $('storage-detail').textContent='Storage measurement is not available.'; $('storage-time').textContent=''; $('storage-meter').value=0; }
  $('imports').replaceChildren();
  for(const source of ['paper','live']) { const b=s.backfill?.[source] || {}; row($('imports'),[source,number(b.local_records),pill(b.initial_complete ? 'Verified' : 'Incomplete',b.initial_complete?'good':'warn'),pill(b.reconciliation_complete?'Verified':'Incomplete',b.reconciliation_complete?'good':'neutral'),date(b.coverage?.checked_at)]); }
  $('jobs').replaceChildren();
  for(const [key,value] of Object.entries(jobs).filter(([k])=>k.startsWith('job:')).sort()) {
    const v=value || {}, deferred=v.error==='DiscoveryDeferred', stale=v.last_success_at && Date.now()-Date.parse(v.last_success_at)>15*60*1000;
    const state=deferred?'Waiting to retry':v.error?'Error':!v.last_success_at?'Not run':stale?'Stale':'Last run OK';
    const detail=v.error?(deferred?'Retry after '+date(v.retry_at_unix*1000):v.error+(v.http_status?' · HTTP '+v.http_status:'')):'Records in last run: '+number(v.records);
    row($('jobs'),[key.slice(4),pill(state,v.error||stale?'warn':'neutral'),date(v.last_success_at),detail]);
  }
  $('connection').textContent='Snapshot received ' + date(s.captured_at) + ' · refreshes every 60 seconds';
}
function renderAssessments(data) {
  $('assessments').replaceChildren();
  for(const a of data.items || []) {
    const identity=node('div',a.series_ticker); identity.append(node('small',a.strategy_id+' · '+a.strategy_version));
    const qualification=node('div',''); qualification.append(pill(a.qualified?'Qualified':'Unqualified',a.qualified?'good':'neutral'));
    qualification.append(node('small',(a.qualification_reasons || []).join(', ').replaceAll('_',' ')));
    row($('assessments'),[identity,a.evidence_source+' / '+a.window,number(a.executions),Number(a.observation_span_days || 0).toFixed(1)+' days',a.edge_cents_per_contract == null?'Unknown':Number(a.edge_cents_per_contract).toFixed(2),a.confidence_score == null?'Unknown':String(a.confidence_score),qualification]);
  }
  if(!data.items?.length) row($('assessments'),['No assessments for this selection.','—','—','—','—','—','—']);
  $('assessment-detail').textContent='Showing '+number(data.items?.length)+' of '+number(data.total)+' contexts. Limited to 100 per view; these are not unique market counts.';
  for(const a of data.items || []) if(!Array.from($('strategy').options).some(o=>o.value===a.strategy_id)) { const option=node('option',a.strategy_id); option.value=a.strategy_id; $('strategy').append(option); }
}
function renderReadiness(data) {
  $('review-packets').replaceChildren();
  for(const packet of data.items || []) {
    const facts=packet.series_review, details=node('details','');
    details.append(node('summary','Open packet'));
    const content=node('pre',JSON.stringify(packet,null,2));
    content.className='review-packet';
    details.append(content);
    row($('review-packets'),[packet.series_ticker,number(packet.live_markets),number(packet.attributed_markets)+' / '+number(packet.blocked_markets),facts.review_status.replaceAll('_',' '),facts.missing_fields.join(', ').replaceAll('_',' '),details]);
  }
  if(!data.items?.length) row($('review-packets'),['No series in this review queue.','—','—','—','—','—']);
  $('readiness-detail').textContent='Showing '+number(data.items?.length)+' of '+number(data.total)+' series. Field completion, approved review, and strategy confidence are separate. These packets are current views, not frozen calibration datasets.';
}
async function refresh() {
  if(!token || busy) return;
  busy=true; $('refresh').disabled=true; $('connect').querySelector('button').disabled=true;
  const g=generation, currentToken=token;
  try {
    const [status,assessments,readiness]=await Promise.all([api('/v1/status',currentToken),api('/v1/assessments?limit=100&strategy='+encodeURIComponent($('strategy').value),currentToken),api('/v1/review-packets?strategy=mmsell&limit=10',currentToken)]);
    if(g!==generation) return;
    renderStatus(status); renderAssessments(assessments); renderReadiness(readiness); notice('');
    $('content').hidden=false; $('login').hidden=true; $('refresh').hidden=false; $('disconnect').hidden=false;
  } catch(error) { if(g===generation) { notice(error.message); $('connection').textContent='Refresh failed · displayed data may be stale'; } }
  finally { busy=false; $('refresh').disabled=false; $('connect').querySelector('button').disabled=false; }
}
$('connect').addEventListener('submit',e=>{e.preventDefault(); token=$('token').value.trim(); $('token').value=''; generation++; refresh();});
$('refresh').addEventListener('click',refresh);
$('strategy').addEventListener('change',refresh);
$('disconnect').addEventListener('click',()=>{token=''; generation++; $('content').hidden=true; $('login').hidden=false; $('refresh').hidden=true; $('disconnect').hidden=true; $('connection').textContent='Not connected'; notice(''); $('assessments').replaceChildren();});
setInterval(refresh,60000);
