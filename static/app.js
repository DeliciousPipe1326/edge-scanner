    const appConfig = JSON.parse(document.getElementById('scanner-config').textContent || '{}');
    const hasEnvKey = appConfig.hasEnvKey === true || appConfig.hasEnvKey === 'true';
    const form = document.getElementById('scan-form');
    const apiKeyInput = document.getElementById('api-key');
    const stakeInput = document.getElementById('stake-input');
    const scanBtn = document.getElementById('scan-btn');
    const statusEl = document.getElementById('status');
    const summaryEl = document.getElementById('summary-info');
    const resultsBody = document.querySelector('#results-table tbody');
    const emptyState = document.getElementById('table-empty');
    const allSportsToggle = document.getElementById('all-sports');
    const summaryROI = document.getElementById('roi-bars');
    const summarySport = document.getElementById('sport-bars');
    const tableCount = document.getElementById('table-count');
    const statOpps = document.querySelector('#stat-opps .stat-value');
    const statEvents = document.querySelector('#stat-events .stat-value');
    const statSports = document.querySelector('#stat-sports .stat-value');
    const statCalls = document.querySelector('#stat-calls .stat-value');
    const themeToggle = document.getElementById('theme-toggle');
    const toggleKeyBtn = document.getElementById('toggle-key');
    const currencyFormatter = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });
    const partialWarning = document.getElementById('partial-warning');
    const commissionInput = document.getElementById('commission-input');
    const tabs = document.querySelectorAll('.tab-btn');
    const arbitragePanel = document.getElementById('arbitrage-panel');
    const middlesPanel = document.getElementById('middles-panel');
    const plusEvPanel = document.getElementById('plus-ev-panel');
    const middlesConfig = document.getElementById('middles-config');
    const plusEvConfig = document.getElementById('plus-ev-config');
    const minGapInput = document.getElementById('min-gap-input');
    const positiveEvToggle = document.getElementById('no-loss-toggle');
    const middleSortSelect = document.getElementById('middle-sort');
    const middleStatCount = document.getElementById('middle-stat-count');
    const middleStatPositive = document.getElementById('middle-stat-positive');
    const middleStatAvg = document.getElementById('middle-stat-avg');
    const middleStatBest = document.getElementById('middle-stat-best');
    const middleSummaryInfo = document.getElementById('middles-summary-info');
    const middleSportBars = document.getElementById('middles-sport-bars');
    const middlesTableBody = document.querySelector('#middles-table tbody');
    const middlesTableCount = document.getElementById('middles-table-count');
    const middlesEmpty = document.getElementById('middles-empty');
    const sharpSelect = document.getElementById('sharp-book-select');
    const minEdgeInput = document.getElementById('min-edge-input');
    const bankrollInput = document.getElementById('bankroll-input');
    const kellySelect = document.getElementById('kelly-select');
    const plusEvSortSelect = document.getElementById('plus-ev-sort');
    const plusEvCount = document.getElementById('plus-ev-count');
    const plusEvAvg = document.getElementById('plus-ev-avg');
    const plusEvBest = document.getElementById('plus-ev-best');
    const plusEvTotal = document.getElementById('plus-ev-total');
    const plusEvSummaryInfo = document.getElementById('plus-ev-summary-info');
    const plusEvSportBars = document.getElementById('plus-ev-sport-bars');
    const plusEvEdgeBars = document.getElementById('plus-ev-edge-bars');
    const plusEvTableBody = document.querySelector('#plus-ev-table tbody');
    const plusEvTableCount = document.getElementById('plus-ev-table-count');
    const plusEvEmpty = document.getElementById('plus-ev-empty');
    const oddsToggle = document.getElementById('odds-toggle');
    const defaultCommissionPercent = parseFloat(commissionInput.value) || 5;
    let lastCommissionRate = defaultCommissionPercent / 100;
    let lastScanData = null;
    let scanBusy = false;
    let lastExpiredCount = 0;
    let activeTab = 'arbitrage';
    let oddsFormat = readPreference('edge-odds-format') || 'decimal';

    document.addEventListener('DOMContentLoaded', () => {
      const savedTheme = readPreference('arb-theme');
      if (savedTheme) {
        document.documentElement.dataset.theme = savedTheme;
        themeToggle.textContent = savedTheme === 'dark' ? '☀️' : '🌙';
      }
      updateOddsToggle();
      setInterval(refreshExpired, 1000);
    });

    if (toggleKeyBtn && apiKeyInput) {
      toggleKeyBtn.addEventListener('click', () => {
        if (apiKeyInput.type === 'password') {
          apiKeyInput.type = 'text';
          toggleKeyBtn.textContent = '🙈';
        } else {
          apiKeyInput.type = 'password';
          toggleKeyBtn.textContent = '👁';
        }
      });
    }

    themeToggle.addEventListener('click', () => {
      const current = document.documentElement.dataset.theme || 'light';
      const next = current === 'light' ? 'dark' : 'light';
      document.documentElement.dataset.theme = next;
      savePreference('arb-theme', next);
      themeToggle.textContent = next === 'dark' ? '☀️' : '🌙';
    });

    if (oddsToggle) {
      oddsToggle.addEventListener('click', () => {
        oddsFormat = oddsFormat === 'decimal' ? 'american' : 'decimal';
        savePreference('edge-odds-format', oddsFormat);
        updateOddsToggle();
        renderArbitrage();
        renderMiddles();
        renderPlusEv();
      });
    }

    tabs.forEach((tab) => {
      tab.addEventListener('click', () => {
        const target = tab.dataset.tab;
        if (target && target !== activeTab) {
          setActiveTab(target);
        }
      });
    });

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (!hasEnvKey && !apiKeyInput?.value.trim()) {
        showStatus('Please enter your API key.', 'error');
        return;
      }
      const sports = Array.from(form.querySelectorAll('input[name="sports"]:checked')).map((el) => el.value);
      const regions = Array.from(form.querySelectorAll('input[name="regions"]:checked')).map((el) => el.value);
      if (!sports.length && !allSportsToggle.checked) {
        showStatus('Select at least one sport.', 'error');
        return;
      }
      if (!regions.length) {
        showStatus('Select at least one region.', 'error');
        return;
      }
      const stakeValue = parseFloat(stakeInput.value) || 100;
      const commissionValue = Number.isFinite(parseFloat(commissionInput.value))
        ? parseFloat(commissionInput.value)
        : defaultCommissionPercent;
      await reviewScan({
        apiKey: apiKeyInput ? apiKeyInput.value.trim() : '',
        sports,
        regions,
        windowHours: document.getElementById('window-select').value === 'all' ? null : Number(document.getElementById('window-select').value),
        maxQuoteAge: numberOrDefault(document.getElementById('max-age-input').value, 120),
        bookmakers: document.getElementById('bookmakers-input').value.split(',').map(s => s.trim()).filter(Boolean),
        commission: commissionValue,
        allSports: allSportsToggle.checked,
        stake: stakeValue,
        sharpBook: sharpSelect?.value || appConfig.defaultSharpBook,
        minEdgePercent: numberOrDefault(minEdgeInput?.value, appConfig.defaultMinEdgePercent ?? 1),
        bankroll: numberOrDefault(bankrollInput?.value, appConfig.defaultBankroll ?? 1000),
        kellyFraction: numberOrDefault(kellySelect?.value, appConfig.defaultKellyFraction ?? 0.25),
      });
    });

    stakeInput.addEventListener('input', () => {
      if (lastScanData) {
        renderArbitrage();
        renderMiddles();
      }
    });

    document.getElementById('min-roi-input').addEventListener('input', () => renderArbitrage());
    minGapInput.addEventListener('input', () => renderMiddles());
    positiveEvToggle.addEventListener('change', () => renderMiddles());
    middleSortSelect.addEventListener('change', () => renderMiddles());
    minEdgeInput.addEventListener('input', () => renderPlusEv());
    bankrollInput.addEventListener('input', () => renderPlusEv());
    kellySelect.addEventListener('change', () => renderPlusEv());
    plusEvSortSelect.addEventListener('change', () => renderPlusEv());

    let plannedPayload = null;
    const planDialog = document.getElementById('plan-dialog');
    document.getElementById('cancel-plan').addEventListener('click', () => {
      plannedPayload = null; planDialog.close();
    });
    planDialog.addEventListener('cancel', () => { plannedPayload = null; });
    document.getElementById('confirm-scan').addEventListener('click', async () => {
      const payload = plannedPayload;
      plannedPayload = null; planDialog.close();
      if (payload) await runScan(payload);
    });

    async function postJson(url, payload) {
      const response = await fetch(url, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      let data;
      try { data = await response.json(); }
      catch { throw new Error('The server returned an unreadable response. Please try again.'); }
      if (response.ok === false || !data.success) {
        const error = new Error(data.error || 'Request failed');
        error.data = data;
        throw error;
      }
      return data;
    }

    async function reviewScan(payload) {
      if (scanBusy) return;
      setScanning(true);
      try {
        const data = await postJson('/plan', payload);
        const plan = data.plan;
        const remaining = data.quota?.remaining;
        const summary = `${windowLabel(plan.window_hours)} • ${plan.odds_requests} sports, regions ${plan.regions.join(', ').toUpperCase()}. Up to ${plan.max_credits} credits. Remaining: ${remaining ?? 'unknown'}.`;
        document.getElementById('plan-summary').textContent = summary;
        document.getElementById('plan-sports').textContent = plan.sports.map(s => `${s.name}: ${s.markets.join(', ')} (up to ${s.max_credits} credits)`).join(' • ');
        const allowed = plan.odds_requests > 0 && (remaining == null || remaining >= plan.max_credits);
        document.getElementById('confirm-scan').disabled = !allowed;
        plannedPayload = allowed ? { ...payload, maxCredits: plan.max_credits } : null;
        planDialog.showModal();
        showStatus(allowed ? 'Review the scan cost before continuing.' : 'No active sports or insufficient credits. Change your selection.', allowed ? '' : 'error');
      } catch (err) {
        showStatus(err.message, 'error');
      } finally { setScanning(false); }
    }

    async function runScan(payload) {
      if (scanBusy) return;
      setScanning(true);
      renderScanData(null);
      try {
        const data = await postJson('/scan', payload);
        renderScanData(data);
        const count = ['arbitrage', 'middles', 'plus_ev'].reduce((sum, key) => sum + (data[key]?.opportunities_count || 0), 0);
        showStatus(`${data.partial ? 'Partial scan' : 'Scan complete'} at ${new Date(data.scan_time).toLocaleTimeString()} • ${count} candidates before display filters`, data.partial ? 'warning' : 'success');
      } catch (err) {
        showStatus(err.message || 'Scan failed', 'error');
        renderScanData(null);
        if (err.data) renderScanMeta(err.data);
      } finally { setScanning(false); }
    }

    function setScanning(isScanning) {
      scanBusy = isScanning;
      scanBtn.disabled = isScanning;
      scanBtn.textContent = isScanning ? 'Scanning…' : 'Scan Now';
      if (isScanning) showStatus('Scanning, please wait…', '');
    }

    function numberOrDefault(value, fallback) {
      const number = parseFloat(value);
      return Number.isFinite(number) ? number : fallback;
    }

    function escapeHtml(value) {
      return String(value ?? '').replace(/[&<>"']/g, (char) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
      }[char]));
    }

    function showStatus(message, type) {
      statusEl.textContent = message;
      statusEl.className = `status ${type}`;
    }

    function renderScanData(payload) {
      lastScanData = payload;
      document.getElementById('download-results').disabled = !payload;
      lastExpiredCount = countExpired();
      if (payload && typeof payload.commission_rate === 'number') {
        lastCommissionRate = payload.commission_rate;
      }
      renderArbitrage();
      renderMiddles();
      renderPlusEv();
      renderPartialWarning(payload);
      renderScanMeta(payload);
    }

    function renderArbitrage() {
      const arbData = lastScanData?.arbitrage;
      const stakeValue = parseFloat(stakeInput.value) || 0;
      const opportunities = (arbData?.opportunities || []).filter(isCurrent).filter(opp => {
        const calc = calculateStakes(opp, stakeValue, true);
        return calc && calc.roi_percent > 0 && calc.roi_percent >= numberOrDefault(document.getElementById('min-roi-input').value, 2) && withinReportedLimits(opp.best_odds || [], calc.breakdown.map(b => b.stake));
      });
      resultsBody.innerHTML = '';
      tableCount.textContent = `${opportunities.length} results`;
      if (!opportunities.length) {
        emptyState.textContent = lastScanData ? 'No current arbitrage candidates meet the selected event window, minimum net ROI, stake, and reported limits.' : 'Run a scan to check for paper arbitrage candidates.';
        emptyState.classList.remove('hidden');
      } else {
        emptyState.classList.add('hidden');
        let runningProfit = 0;
        opportunities.forEach((opp) => {
          const row = document.createElement('tr');
          const calcNet = calculateStakes(opp, stakeValue, true);
          const netRoi = calcNet?.roi_percent ?? opp.roi_percent;
          row.classList.add(roiBandClass(netRoi));
          const commence = opp.commence_time ? new Date(opp.commence_time).toLocaleString() : 'TBD';
          const calcGross = opp.has_exchange ? calculateStakes(opp, stakeValue, false) : null;
          const grossRoi = opp.has_exchange ? calcGross?.roi_percent ?? opp.gross_roi_percent : null;
          const netNegative = typeof netRoi === 'number' && netRoi < 0;
          if (netNegative) row.classList.add('roi-negative');
          if (opp.has_exchange) row.classList.add('has-exchange');
          const breakdown = calcNet?.breakdown || [];
          const profitValue = calcNet?.guaranteed_profit ?? 0;
          runningProfit += profitValue || 0;
          const profitClass = profitValue > 0 ? 'gain' : profitValue < 0 ? 'loss' : '';
          const grossCell = opp.has_exchange ? formatRoiBadge(grossRoi) : '';
          row.innerHTML = `
            <td><span class="roi-badge">${formatPercent(netRoi)}</span>${netNegative ? '<span class="roi-warning" title="Net ROI is negative after commission.">⚠</span>' : ''}</td>
            <td>${grossCell}</td>
            <td class="profit-cell ${profitClass}">${calcNet ? formatProfit(profitValue) : '-'}</td>
            <td>${formatExchangeCell(opp)}</td>
            <td>${escapeHtml(opp.sport_display || opp.sport || '')}</td>
            <td>${escapeHtml(opp.event || '')}</td>
            <td>${commence}</td>
            <td>${formatMarket(opp)}</td>
            <td>${formatOutcome(breakdown[0])}</td>
            <td>${formatOutcome(breakdown[1])}</td>
            <td>${formatExecution(opp.best_odds || [], breakdown.map(b => b.stake))}</td>`;
          resultsBody.appendChild(row);
        });
        tableCount.textContent = `${opportunities.length} results • hypothetical sum ${formatCurrency(runningProfit)}`;
      }
      const summary = arbData?.summary;
      if (summary) {
        statOpps.textContent = opportunities.length;
        statEvents.textContent = summary.events_scanned;
        statSports.textContent = summary.sports_scanned;
        statCalls.textContent = summary.api_calls_used;
        const totalProfit = opportunities.reduce((sum, opp) => {
          const calc = calculateStakes(opp, parseFloat(stakeInput.value) || stakeValue, true);
          return sum + (calc?.guaranteed_profit || 0);
        }, 0);
        const sportCounts = Object.create(null);
        const bands = {'0-1%': 0, '1-2%': 0, '2%+': 0};
        opportunities.forEach(opp => {
          const roi = calculateStakes(opp, stakeValue, true)?.roi_percent ?? 0;
          bands[roi >= 2 ? '2%+' : roi >= 1 ? '1-2%' : '0-1%']++;
          const sport = opp.sport_display || opp.sport || 'Other';
          sportCounts[sport] = (sportCounts[sport] || 0) + 1;
        });
        renderBars(summaryROI, bands, 'roi');
        renderBars(summarySport, sportCounts, 'sport');
        const totalStaked = stakeValue > 0 ? stakeValue * opportunities.length : 0;
        const commissionPercent = formatCommissionPercent(lastCommissionRate);
        const regionsLabel = Array.isArray(lastScanData?.regions) && lastScanData.regions.length
          ? lastScanData.regions.join(', ').toUpperCase()
          : '';
        summaryEl.classList.remove('hidden');
        summaryEl.innerHTML = `
          <h3>Summary</h3>
          <p>Found <strong>${opportunities.length}</strong> opportunities across <strong>${summary.events_scanned}</strong> events in <strong>${summary.sports_scanned}</strong> sports.</p>
          <p>Hypothetical sum of returns (overlapping bets are not additive): <strong>${formatCurrency(totalProfit)}</strong>${totalStaked ? ` on ${formatCurrency(totalStaked)} staked` : ''}</p>
          <p>Odds requests made: ${summary.api_calls_used}${regionsLabel ? ` • Regions: ${regionsLabel}` : ''}</p>
          <p>Exchange commission applied: ${commissionPercent}.</p>
          <p class="helper">ROI = return on total stake. 2% ROI means $2 profit on a $100 stake.</p>
        `;
      } else {
        statOpps.textContent = statEvents.textContent = statSports.textContent = statCalls.textContent = '0';
        summaryROI.innerHTML = '';
        summarySport.innerHTML = '';
        summaryEl.classList.add('hidden');
        summaryEl.innerHTML = '';
      }
    }

    function renderMiddles() {
      const opportunities = (lastScanData?.middles?.opportunities || []).filter(isCurrent);
      const total = numberOrDefault(stakeInput.value, 100);
      const minGap = numberOrDefault(minGapInput.value, 0);
      const filtered = opportunities.map(opp => ({ ...opp, calc: calculateMiddle(opp, total) }))
        .filter(opp => opp.calc && withinReportedLimits([opp.side_a, opp.side_b], [opp.calc.stakeA, opp.calc.stakeB]) && (opp.gap?.points ?? 0) >= minGap && (!positiveEvToggle.checked || opp.calc.worst >= 0));
      filtered.sort((a, b) => {
        if (middleSortSelect.value === 'gap') return (b.gap?.points || 0) - (a.gap?.points || 0);
        if (middleSortSelect.value === 'time') return new Date(a.commence_time) - new Date(b.commence_time);
        return (a.calc.threshold ?? 100) - (b.calc.threshold ?? 100);
      });
      const thresholds = filtered.map(o => o.calc.threshold).filter(v => v != null);
      middleStatCount.textContent = filtered.length;
      middleStatPositive.textContent = filtered.filter(o => o.calc.worst >= 0).length;
      middleStatAvg.textContent = formatPercent(thresholds.length ? thresholds.reduce((a,b) => a+b, 0) / thresholds.length : null);
      middleStatBest.textContent = formatPercent(thresholds.length ? Math.min(...thresholds) : null);
      middleSummaryInfo.classList.toggle('hidden', !filtered.length);
      middleSummaryInfo.textContent = filtered.length ? `${filtered.length} middles. Break-even uses the worst non-middle payoff; it is a required probability, not a prediction.` : '';
      const bySport = Object.create(null);
      filtered.forEach(o => { const key = o.sport_display || o.sport || 'Other'; bySport[key] = (bySport[key] || 0) + 1; });
      renderBars(middleSportBars, bySport, 'sport');
      middlesTableBody.innerHTML = '';
      middlesTableCount.textContent = `${filtered.length} results`;
      middlesEmpty.classList.toggle('hidden', filtered.length > 0);
      filtered.forEach(opp => {
        const c = opp.calc;
        const row = document.createElement('tr');
        const scenarios = c.scenarios.map(s => `<div>${s.score}: ${s.side_a}/${s.side_b} ${formatProfit(s.profit)}</div>`).join('');
        row.innerHTML = `
          <td>${formatPercent(c.threshold)}</td>
          <td>${formatProfit(c.worst)}</td>
          <td>${formatGapCell(opp)}</td>
          <td>${escapeHtml(opp.sport_display || opp.sport || '')}</td>
          <td>${escapeHtml(opp.event || '')}</td>
          <td>${opp.commence_time ? new Date(opp.commence_time).toLocaleString() : 'TBD'}</td>
          <td>${escapeHtml(opp.market || '')}</td>
          <td>${formatMiddleSide(opp.side_a, c.stakeA)}</td>
          <td>${formatMiddleSide(opp.side_b, c.stakeB)}</td>
          <td>${escapeHtml(opp.middle_zone || '')}</td>
          <td>${formatProfit(c.both)}<details><summary>All payoffs</summary>${scenarios}<p>First/last rows represent the outside tails.</p></details></td>
          <td>${formatExecution([opp.side_a, opp.side_b], [c.stakeA, c.stakeB])}</td>`;
        middlesTableBody.appendChild(row);
      });
    }

    function calculateMiddle(opp, total) {
      const a = Number(opp.side_a?.effective_price ?? opp.side_a?.price);
      const b = Number(opp.side_b?.effective_price ?? opp.side_b?.price);
      if (!(total > 0 && a > 1 && b > 1) || !opp.profile?.scenarios?.length) return null;
      const stakeA = roundMoney(total * b / (a + b));
      const stakeB = roundMoney(total - stakeA);
      if (stakeA <= 0 || stakeB <= 0) return null;
      const payout = (state, stake, odds) => roundMoney(stake * (state === 'win' ? odds : state === 'push' ? 1 : 0));
      const scenarios = opp.profile.scenarios.map(s => ({ ...s, profit: roundMoney(payout(s.side_a, stakeA, a) + payout(s.side_b, stakeB, b) - total) }));
      const both = Math.min(...scenarios.filter(s => s.side_a === 'win' && s.side_b === 'win').map(s => s.profit));
      const miss = Math.min(...scenarios.filter(s => !(s.side_a === 'win' && s.side_b === 'win')).map(s => s.profit));
      const threshold = both > miss ? Math.max(0, -miss / (both - miss)) * 100 : null;
      return { stakeA, stakeB, scenarios, both, worst: Math.min(...scenarios.map(s => s.profit)), threshold };
    }

    function roundMoney(number) { return Math.round((number + Number.EPSILON) * 100) / 100; }

    function renderPlusEv() {
      const plusData = lastScanData?.plus_ev;
      const opportunities = (plusData?.opportunities || []).filter(isCurrent);
      const minEdge = numberOrDefault(minEdgeInput.value, appConfig.defaultMinEdgePercent ?? 1);
      const bankroll = numberOrDefault(bankrollInput.value, appConfig.defaultBankroll ?? 1000);
      const kellyFraction = numberOrDefault(kellySelect.value, appConfig.defaultKellyFraction ?? 0.25);
      const sortKey = plusEvSortSelect.value || 'edge';
      const filtered = opportunities.filter((opp) => (opp.edge_percent || 0) >= minEdge && withinReportedLimits([opp.bet], [calculateKellyStake(oppTrueProb(opp), oppEffectiveOdds(opp), bankroll, kellyFraction).recommended]));
      filtered.sort((a, b) => {
        switch (sortKey) {
          case 'ev':
            return (b.ev_per_100 || 0) - (a.ev_per_100 || 0);
          case 'kelly': {
            const bkA = calculateKellyStake(oppTrueProb(a), oppEffectiveOdds(a), bankroll, kellyFraction);
            const bkB = calculateKellyStake(oppTrueProb(b), oppEffectiveOdds(b), bankroll, kellyFraction);
            return bkB.recommended - bkA.recommended;
          }
          case 'time': {
            const timeA = a.commence_time ? new Date(a.commence_time).getTime() : 0;
            const timeB = b.commence_time ? new Date(b.commence_time).getTime() : 0;
            return timeA - timeB;
          }
          case 'edge':
          default:
            return (b.edge_percent || 0) - (a.edge_percent || 0);
        }
      });
      const stats = buildPlusEvStats(filtered);
      plusEvCount.textContent = stats.count;
      plusEvAvg.textContent = formatPercent(stats.average_edge_percent);
      plusEvBest.textContent = formatPercent(stats.best_edge_percent);
      plusEvTotal.textContent = formatCurrency(stats.total_ev_per_100);
      if (stats.count > 0) {
        plusEvSummaryInfo.classList.remove('hidden');
        const bestText = stats.best_event ? `Best: ${escapeHtml(stats.best_event)}` : '';
        plusEvSummaryInfo.innerHTML = `
          <h3>Summary</h3>
          <p>+EV bets: <strong>${stats.count}</strong> • Average edge <strong>${formatPercent(stats.average_edge_percent)}</strong> • ${bestText}</p>
        `;
        renderBars(plusEvSportBars, stats.by_sport, 'sport');
        renderBars(plusEvEdgeBars, stats.by_edge_band, 'roi');
      } else {
        plusEvSummaryInfo.classList.add('hidden');
        plusEvSummaryInfo.innerHTML = '';
        plusEvSportBars.innerHTML = '<p class="muted">No data</p>';
        plusEvEdgeBars.innerHTML = '<p class="muted">No data</p>';
      }

      plusEvTableBody.innerHTML = '';
      plusEvTableCount.textContent = `${filtered.length} results`;
      if (!filtered.length) {
        plusEvEmpty.textContent = lastScanData?.quality?.events_without_reference ? 'No matching +EV candidates. Some events have no sufficiently fresh, qualified sharp reference; check scan diagnostics.' : 'No current +EV candidates meet the selected window, edge, bankroll, and limit filters.';
        plusEvEmpty.classList.remove('hidden');
        return;
      }
      plusEvEmpty.classList.add('hidden');
      filtered.forEach((opp) => {
        const row = document.createElement('tr');
        if (opp.has_exchange) row.classList.add('has-exchange');
        const commence = opp.commence_time ? new Date(opp.commence_time).toLocaleString() : 'TBD';
        const kellyData = calculateKellyStake(oppTrueProb(opp), oppEffectiveOdds(opp), bankroll, kellyFraction);
        row.innerHTML = `
          <td>${formatEdgeCell(opp)}</td>
          <td class="profit-cell">${formatProfit(opp.ev_per_100 || 0)}</td>
          <td>${formatKellyCell(kellyData)}</td>
          <td>${escapeHtml(opp.sport_display || opp.sport || '')}</td>
          <td>${escapeHtml(opp.event || '')}</td>
          <td>${commence}</td>
          <td>${formatPlusMarket(opp)}</td>
          <td>${formatPlusBet(opp)}</td>
          <td>${formatOddsDisplay(opp.bet?.soft_odds)}</td>
          <td>${formatOddsDisplay(opp.sharp?.fair_odds)}<small>${escapeHtml(opp.sharp?.book || "")}</small></td>
          <td>${formatPercent(opp.sharp?.true_probability_percent)}</td>
          <td>${formatExecution([opp.bet], [kellyData.recommended])}</td>
        `;
        plusEvTableBody.appendChild(row);
      });
    }

    function oppTrueProb(opp) {
      return opp?.sharp?.true_probability ?? (opp?.sharp?.true_probability_percent || 0) / 100;
    }

    function oppEffectiveOdds(opp) {
      return opp?.bet?.effective_odds || opp?.bet?.soft_odds || 0;
    }

    function calculateKellyStake(prob, odds, bankroll, fraction) {
      if (!prob || !odds || bankroll <= 0) return { fullPercent: 0, fractionPercent: 0, recommended: 0 };
      const p = Math.max(0, Math.min(prob, 1));
      const q = 1 - p;
      const b = odds - 1;
      if (b <= 0) return { fullPercent: 0, fractionPercent: 0, recommended: 0 };
      let full = (b * p - q) / b;
      if (full <= 0) return { fullPercent: 0, fractionPercent: 0, recommended: 0 };
      const frac = Math.max(0, Math.min(fraction, 1));
      const adjusted = full * frac;
      const recommended = Math.max(0, bankroll * adjusted);
      return {
        fullPercent: full * 100,
        fractionPercent: adjusted * 100,
        recommended: Number(recommended.toFixed(2)),
      };
    }

    function formatEdgeCell(opp) {
      const net = formatPercent(opp.edge_percent);
      if (opp.has_exchange && typeof opp.gross_edge_percent === 'number' && opp.gross_edge_percent !== opp.edge_percent) {
        return `${net}<span class="ev-gross">Gross ${formatPercent(opp.gross_edge_percent)}</span>`;
      }
      return net;
    }

    function formatKellyCell(kellyData) {
      if (!kellyData) return '';
      const stake = kellyData.recommended || 0;
      const frac = kellyData.fractionPercent || 0;
      return `<div class="kelly-cell">${formatCurrency(stake)}<span class="mono">${frac.toFixed(2)}% roll</span></div>`;
    }

    function formatPlusMarket(opp) {
      if (!opp) return '';
      if ((opp.market === 'spreads' || opp.market === 'totals') && opp.market_point !== undefined && opp.market_point !== null) {
        return `${escapeHtml(opp.market)} (${formatLine(opp.market_point)})`;
      }
      return escapeHtml(opp.market || '');
    }

    function formatPlusBet(opp) {
      if (!opp?.bet) return '';
      const exchangeBadge = opp.bet.is_exchange ? '<span class="exchange-badge">Exchange</span>' : '';
      const book = opp.bet.soft_book ? ` @ ${escapeHtml(opp.bet.soft_book)}` : '';
      const point = opp.bet.point !== undefined && opp.bet.point !== null ? ` (${formatLine(opp.bet.point)})` : '';
      return `<span class="outcome">${exchangeBadge}<strong>${escapeHtml(opp.bet.outcome || '')}${point}</strong><span>${book}</span></span>`;
    }

    function updateOddsToggle() {
      if (!oddsToggle) return;
      oddsToggle.querySelectorAll('span').forEach((span) => {
        const format = span.dataset.format;
        span.classList.toggle('active', format === oddsFormat);
      });
    }

    function formatOddsDisplay(value) {
      if (!value && value !== 0) return '-';
      if (oddsFormat === 'american') {
        const american = decimalToAmerican(value);
        return american > 0 ? `+${american}` : `${american}`;
      }
      return Number(value).toFixed(2);
    }

    function decimalToAmerican(decimal) {
      if (!decimal || decimal <= 1) return 0;
      if (decimal >= 2) {
        return Math.round((decimal - 1) * 100);
      }
      return Math.round(-100 / (decimal - 1));
    }

    function formatOutcome(item) {
      if (!item) return '';
      const point = item.point !== undefined && item.point !== null ? ` (${escapeHtml(item.point)})` : '';
      const stake = item.stake !== undefined ? formatCurrency(item.stake) : '';
      const book = item.bookmaker ? ` @ ${escapeHtml(item.bookmaker)}` : '';
      const exchangeBadge = item.is_exchange ? '<span class="exchange-badge">Exchange</span>' : '';
      const effectiveNote =
        item.is_exchange && item.effective_price && item.effective_price !== item.price
          ? `<span class="mono net-note">Net @ ${formatOddsDisplay(item.effective_price)}</span>`
          : '';
      return `<span class="outcome">${exchangeBadge}<strong>${escapeHtml(item.outcome || '')}${point}</strong><span>${stake}${book}</span><span class="mono">@ ${formatOddsDisplay(item.price)}</span>${effectiveNote}</span>`;
    }

    function formatMarket(opp) {
      if (opp.market === 'spreads' || opp.market === 'totals') {
        const point = opp.best_odds?.[0]?.point ?? opp.point;
        if (point !== undefined && point !== null) {
          return `${escapeHtml(opp.market)} (${escapeHtml(point)})`;
        }
      }
      return escapeHtml(opp.market || '');
    }

    function formatPrice(value) {
      if (!value && value !== 0) return '';
      return Number(value).toFixed(2);
    }

    function calculateStakes(opp, totalStake, useEffectivePrices = true) {
      const odds = (opp.best_odds || []).slice(0, 2);
      if (!totalStake || totalStake <= 0 || odds.length < 2) return null;
      const inverses = odds.map((o) => {
        if (!o) return 0;
        const price = useEffectivePrices
          ? Number(o.effective_price ?? o.price)
          : Number(o.price);
        return price > 0 ? 1 / price : 0;
      });
      const sumInv = inverses.reduce((a, b) => a + b, 0);
      if (sumInv <= 0) return null;
      let allocated = 0;
      const breakdown = odds.map((o, idx) => {
        if (!o || !o.price) return null;
        const fraction = inverses[idx] / sumInv;
        const stake = idx === odds.length - 1
          ? roundMoney(totalStake - allocated)
          : roundMoney(totalStake * fraction);
        allocated += stake;
        const displayPrice = Number(o.price);
        const effectivePrice = Number(o.effective_price ?? o.price);
        const priceUsed = useEffectivePrices ? effectivePrice : displayPrice;
        if (!priceUsed || priceUsed <= 0) return null;
        const payout = roundMoney(stake * priceUsed);
        return {
          outcome: o.outcome,
          bookmaker: o.bookmaker,
          price: displayPrice,
          effective_price: effectivePrice,
          point: o.point,
          stake,
          payout,
          fraction,
          is_exchange: Boolean(o.is_exchange),
          bet_limit: o.bet_limit,
          last_update: o.last_update,
        };
      });
      if (breakdown.includes(null)) return null;
      const minPayout = Math.min(...breakdown.map((b) => b.payout));
      const guaranteed_profit = Number((minPayout - totalStake).toFixed(2));
      const roi_percent = totalStake ? (guaranteed_profit / totalStake) * 100 : 0;
      return { breakdown, guaranteed_profit, roi_percent };
    }

    function formatCurrency(value) {
      return currencyFormatter.format(value || 0);
    }

    function formatProfit(value) {
      const formatted = formatCurrency(Math.abs(value || 0));
      return `${value >= 0 ? '+' : '-'}${formatted}`;
    }

    function renderPartialWarning(data) {
      if (data && data.partial && Array.isArray(data.sport_errors) && data.sport_errors.length) {
        const details = data.sport_errors
          .map((err) => `${err.sport || err.sport_key || 'Unknown'} (${err.error || 'Unknown error'})`)
          .join(', ');
        partialWarning.innerHTML = `<strong>Partial results:</strong> Failed to fetch odds for ${escapeHtml(details)}.`;
        partialWarning.classList.remove('hidden');
      } else {
        partialWarning.classList.add('hidden');
        partialWarning.textContent = '';
      }
    }

    function formatCommissionPercent(rate) {
      if (typeof rate !== 'number') return `${defaultCommissionPercent.toFixed(1)}%`;
      const percent = (rate * 100).toFixed(2);
      return `${percent.replace(/\.00$/, '').replace(/(\.\d)0$/, '$1')}%`;
    }

    function formatExchangeCell(opp) {
      if (!opp || !opp.has_exchange) return '<span class="exchange-flag none">No</span>';
      const names = Array.isArray(opp.exchange_books) && opp.exchange_books.length
        ? opp.exchange_books.join(', ')
        : 'Exchange';
      const commissionText = formatCommissionPercent(lastCommissionRate);
      return `<span class="exchange-flag">${escapeHtml(names)}<span class="exchange-note">${commissionText}</span></span>`;
    }

    function setActiveTab(tab) {
      activeTab = tab;
      tabs.forEach((btn) => btn.classList.toggle('active', btn.dataset.tab === tab));
      arbitragePanel.classList.toggle('hidden', tab !== 'arbitrage');
      middlesPanel.classList.toggle('hidden', tab !== 'middles');
      plusEvPanel.classList.toggle('hidden', tab !== 'plus-ev');
      middlesConfig.classList.toggle('hidden', tab !== 'middles');
      plusEvConfig.classList.toggle('hidden', tab !== 'plus-ev');
      if (tab === 'arbitrage') {
        renderArbitrage();
      } else if (tab === 'middles') {
        renderMiddles();
      } else {
        renderPlusEv();
      }
    }

    function formatGapCell(opp) {
      const integers = opp.gap?.middle_integers || [];
      const keyFlag = opp.gap?.includes_key_number ? ' 🏈' : '';
      const points = typeof opp.gap?.points === 'number' ? `${opp.gap.points.toFixed(1)} pts` : '';
      let range = '';
      if (integers.length) {
        range = integers.length === 1 ? `${integers[0]}` : `${integers[0]}-${integers[integers.length - 1]}`;
      }
      const pieces = [range, points].filter(Boolean);
      return `${escapeHtml(pieces.join(' · '))}${keyFlag}`;
    }

    function formatMiddleSide(side, stakeValue) {
      if (!side) return '';
      const line = side.line !== undefined && side.line !== null ? formatLine(side.line) : '';
      const book = side.bookmaker ? ` @ ${escapeHtml(side.bookmaker)}` : '';
      const odds = side.effective_price ? formatOddsDisplay(side.effective_price) : formatOddsDisplay(side.price);
      const stake = stakeValue ? `Stake: ${formatCurrency(stakeValue)}` : '';
      const exchangeBadge = side.is_exchange ? '<span class="exchange-badge">Exchange</span>' : '';
      return `<span class="outcome">${exchangeBadge}<strong>${escapeHtml(side.team || '')} ${line}</strong><span>Odds: ${odds}${book}</span><span>${stake}</span></span>`;
    }

    function formatLine(value) {
      if (value === null || value === undefined || Number.isNaN(value)) return '';
      const num = Number(value);
      const sign = num > 0 ? '+' : '';
      return `${sign}${num.toFixed(1)}`;
    }

    function roiBandClass(roi) {
      if (roi >= 2) return 'band-hot';
      if (roi >= 1) return 'band-warm';
      return 'band-cool';
    }

    function renderBars(container, data, type) {
      if (!data || Object.keys(data).length === 0) {
        container.innerHTML = '<p class="muted">No data</p>';
        return;
      }
      const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
      const max = entries[0][1] || 1;
      container.innerHTML = entries
        .map(([label, value]) => {
          const width = Math.max((value / max) * 100, 8);
          const bandClass = type === 'roi' ? roiBandLabelClass(label) : '';
          return `
            <div class="bar-row">
              <span>${escapeHtml(label)}</span>
              <div class="bar-track">
                <div class="bar-fill ${bandClass}" style="width: ${width}%"></div>
              </div>
              <span class="bar-value">${escapeHtml(value)}</span>
            </div>`;
        })
        .join('');
    }

    function roiBandLabelClass(label) {
      if (label.includes('10')) return 'band-hot';
      if (label.includes('5')) return 'band-warm';
      return 'band-cool';
    }

    function formatPercent(value) {
      if (value === null || value === undefined || Number.isNaN(value)) return '-';
      return `${Number(value).toFixed(2)}%`;
    }

    function formatRoiBadge(value) {
      if (value === null || value === undefined || Number.isNaN(value)) return '';
      return `<span class="roi-badge secondary">${formatPercent(value)}</span>`;
    }

    function evBandClass(evPercent) {
      if (evPercent >= 10) return 'ev-hot';
      if (evPercent >= 5) return 'ev-warm';
      if (evPercent >= 1) return 'ev-cool';
      if (evPercent < 0) return 'ev-negative';
      return '';
    }

    function buildPlusEvStats(list) {
      const count = list.length;
      if (!count) {
        return {
          count: 0,
          average_edge_percent: 0,
          best_edge_percent: 0,
          best_event: '',
          total_ev_per_100: 0,
          by_sport: {},
          by_edge_band: {},
        };
      }
      let edgeSum = 0;
      let best = list[0];
      let totalEv = 0;
      const bySport = Object.create(null);
      const byEdge = Object.create(null);
      list.forEach((opp) => {
        const edge = Number(opp.edge_percent) || 0;
        edgeSum += edge;
        totalEv += Number(opp.ev_per_100) || 0;
        if (!best || edge > (best.edge_percent || 0)) best = opp;
        const sport = opp.sport_display || opp.sport || 'Other';
        bySport[sport] = (bySport[sport] || 0) + 1;
        const bandLabel = edge >= 10 ? '10%+' : edge >= 5 ? '5-10%' : edge >= 3 ? '3-5%' : '1-3%';
        byEdge[bandLabel] = (byEdge[bandLabel] || 0) + 1;
      });
      return {
        count,
        average_edge_percent: edgeSum / count,
        best_edge_percent: best?.edge_percent || 0,
        best_event: best?.event || '',
        total_ev_per_100: totalEv,
        by_sport: bySport,
        by_edge_band: byEdge,
      };
    }

    function isCurrent(opp) {
      return Boolean(opp.valid_until) && new Date(opp.valid_until).getTime() > Date.now();
    }

    function renderScanMeta(data) {
      const notice = document.getElementById('scan-meta');
      if (!data) { notice.textContent = ''; notice.classList.add('hidden'); return; }
      const quota = data.quota || {};
      const quality = data.quality || {};
      const all = ['arbitrage', 'middles', 'plus_ev'].flatMap(key => data[key]?.opportunities || []);
      const expired = all.filter(opp => !isCurrent(opp)).length;
      const skipped = (quality.stale_markets || 0) + (quality.missing_or_invalid_timestamps || 0) + (quality.invalid_markets || 0);
      notice.textContent = `${windowLabel(data.plan?.window_hours)} • Credits used: ${quota.credits_used ?? 'unknown'}${quota.complete === false ? ` (at least ${quota.known_credits_used || 0} reported)` : ''}. Remaining: ${quota.remaining ?? 'unknown'}. ${quality.fresh_markets || 0} fresh markets; ${quality.outside_window_events || 0} events outside the selected window; ${skipped} stale/invalid markets excluded. ${expired} results expired. ${quality.rejected_reference_lines || 0} unreliable reference lines excluded; ${quality.events_without_reference || 0} events without a usable reference. ` +
        `Quote age limit: ${data.max_quote_age ?? 120}s. Candidates exceeding a reported limit are hidden. Unknown limits remain unverified. Recheck prices, currency, and settlement rules before placing any bets.`;
      notice.classList.remove('hidden');
      const options = document.getElementById('bookmaker-options');
      options.innerHTML = (data.bookmakers || []).map(book => `<option value="${escapeHtml(book.key)}">${escapeHtml(book.name)}</option>`).join('');
    }

    function formatExecution(legs, stakes) {
      return legs.filter(Boolean).map((leg, i) => {
        const limit = leg.bet_limit;
        const known = typeof limit === 'number' && Number.isFinite(limit);
        const over = known && stakes[i] > limit;
        const updated = leg.last_update ? new Date(leg.last_update).toLocaleTimeString() : 'unknown';
        return `<div class="execution ${over ? 'loss' : ''}">${over ? 'Exceeds provider limit' : known ? 'Within reported limit' : 'Limit unverified'}<small>Limit: ${known ? Number(limit).toFixed(2) : 'unknown'} · updated ${updated}</small></div>`;
      }).join('');
    }

    function readPreference(key) { try { return localStorage.getItem(key); } catch { return null; } }
    function savePreference(key, value) { try { localStorage.setItem(key, value); } catch { /* Preferences are optional. */ } }

    function countExpired() {
      return ['arbitrage', 'middles', 'plus_ev'].flatMap(key => lastScanData?.[key]?.opportunities || []).filter(opp => !isCurrent(opp)).length;
    }
    function refreshExpired() {
      if (!lastScanData || scanBusy || countExpired() === lastExpiredCount) return;
      lastExpiredCount = countExpired();
      renderArbitrage(); renderMiddles(); renderPlusEv(); renderScanMeta(lastScanData);
    }

    function withinReportedLimits(legs, stakes) {
      return legs.every((leg, i) => typeof leg?.bet_limit !== 'number' || !Number.isFinite(leg.bet_limit) || stakes[i] <= leg.bet_limit);
    }

    document.getElementById('download-results').addEventListener('click', () => {
      if (!lastScanData) return;
      const blob = new Blob([JSON.stringify(lastScanData, null, 2)], {type: 'application/json'});
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `edge-scanner-${String(lastScanData.scan_time || 'snapshot').replace(/[^0-9A-Za-z-]/g, '-')}.json`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });

    function windowLabel(hours) { return hours == null ? 'Infinite window' : hours === 168 ? '7-day window' : `${hours}-hour window`; }
