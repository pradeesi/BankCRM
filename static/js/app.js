/* ============================================================================
 * Purpose: Client-side interactivity, Chart.js rendering, live underwriting
 *   calculator, and dynamic modal helpers for Aurelia Private Bank & Trust CRM.
 * Architecture/Context: Loaded at the end of `templates/base.html`; reads JSON
 *   data payloads embedded in `<script type="application/json">` DOM blocks.
 * Dependencies/Side Effects: Requires Bootstrap 5.3 JS bundle and Chart.js 4.x.
 * Author: Pradeep Singh
 * License: Apache-2.0
 * ============================================================================ */

/**
 * Initialize mobile sidebar navigation toggle behavior.
 *
 * Summary:
 *   Attaches click event listeners to `#sidebarToggleBtn` so responsive screens
 *   can slide the institutional navigation sidebar in and out.
 *
 * Parameters:
 *   None
 *
 * Returns:
 *   void
 */
function initSidebarToggle() {
  const toggleBtn = document.getElementById('sidebarToggleBtn');
  const sidebar = document.getElementById('crmSidebar');
  if (!toggleBtn || !sidebar) {
    return;
  }

  toggleBtn.addEventListener('click', () => {
    sidebar.classList.toggle('show');
  });
}

/**
 * Render Executive Dashboard Chart.js visualizations when present on the page.
 *
 * Summary:
 *   Reads `#dashboardChartData` JSON script block and renders the 6-Month AUM
 *   Trajectory line chart and Asset Allocation doughnut chart.
 *
 * Parameters:
 *   None
 *
 * Returns:
 *   void
 */
function initDashboardCharts() {
  const dataEl = document.getElementById('dashboardChartData');
  if (!dataEl || typeof Chart === 'undefined') {
    return;
  }

  let payload = null;
  try {
    payload = JSON.parse(dataEl.textContent);
  } catch (err) {
    console.error('Failed to parse dashboard chart payload:', err);
    return;
  }

  const aumCanvas = document.getElementById('aumTrendChart');
  if (aumCanvas && payload.aum_growth_labels) {
    new Chart(aumCanvas, {
      type: 'line',
      data: {
        labels: payload.aum_growth_labels,
        datasets: [
          {
            label: 'Total Institutional AUM ($M)',
            data: payload.aum_growth_values_m,
            borderColor: '#1d4ed8',
            backgroundColor: 'rgba(29, 78, 216, 0.10)',
            borderWidth: 2.5,
            pointBackgroundColor: '#1d4ed8',
            pointRadius: 4,
            fill: true,
            tension: 0.35,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (ctx) => ` $${ctx.parsed.y.toFixed(2)}M USD`,
            },
          },
        },
        scales: {
          y: {
            ticks: {
              callback: (val) => `$${val}M`,
              color: '#64748b',
            },
            grid: { color: '#f1f5f9' },
          },
          x: {
            ticks: { color: '#64748b' },
            grid: { display: false },
          },
        },
      },
    });
  }

  const allocCanvas = document.getElementById('assetAllocationChart');
  if (allocCanvas && payload.allocation_labels) {
    new Chart(allocCanvas, {
      type: 'doughnut',
      data: {
        labels: payload.allocation_labels,
        datasets: [
          {
            data: payload.allocation_values,
            backgroundColor: [
              '#0f172a',
              '#1d4ed8',
              '#047857',
              '#b45309',
              '#0284c7',
              '#475569',
              '#334155',
            ],
            borderWidth: 2,
            borderColor: '#ffffff',
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '66%',
        plugins: {
          legend: {
            position: 'bottom',
            labels: {
              boxWidth: 11,
              font: { size: 11 },
              color: '#334155',
            },
          },
        },
      },
    });
  }
}

/**
 * Render the Customer 360 Portfolio Asset Allocation doughnut chart.
 *
 * Summary:
 *   Reads `#clientPortfolioData` JSON block on the Customer 360 profile page
 *   and initializes a strategic asset mix chart (Equities, Fixed Income, PE, Cash).
 *
 * Parameters:
 *   None
 *
 * Returns:
 *   void
 */
function initClientPortfolioChart() {
  const dataEl = document.getElementById('clientPortfolioData');
  const canvas = document.getElementById('clientPortfolioChart');
  if (!dataEl || !canvas || typeof Chart === 'undefined') {
    return;
  }

  try {
    const mix = JSON.parse(dataEl.textContent);
    new Chart(canvas, {
      type: 'doughnut',
      data: {
        labels: ['Global Equities', 'Fixed Income & Treasuries', 'Private Equity / Direct', 'Liquidity & Cash'],
        datasets: [
          {
            data: [mix.equities, mix.fixed_income, mix.private_equity, mix.cash],
            backgroundColor: ['#1d4ed8', '#0f172a', '#047857', '#b45309'],
            borderWidth: 2,
            borderColor: '#ffffff',
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '68%',
        plugins: {
          legend: {
            position: 'bottom',
            labels: { boxWidth: 11, font: { size: 11 } },
          },
        },
      },
    });
  } catch (err) {
    console.error('Failed to render client portfolio chart:', err);
  }
}

/**
 * Configure dynamic form fields in the Ledger Wire & Transfer modal.
 *
 * Summary:
 *   Shows the destination account selector when 'Internal Transfer' is selected,
 *   and toggles the external counterparty input field accordingly.
 *
 * Parameters:
 *   None
 *
 * Returns:
 *   void
 */
function initLedgerTransactionModal() {
  const txnTypeSelect = document.getElementById('txnTypeSelect');
  const destGroup = document.getElementById('destAccountGroup');
  const counterpartyGroup = document.getElementById('counterpartyGroup');
  const counterpartyInput = document.getElementById('counterpartyInput');

  if (!txnTypeSelect || !destGroup || !counterpartyGroup) {
    return;
  }

  const syncVisibility = () => {
    const isInternal = txnTypeSelect.value === 'Internal Transfer';
    destGroup.classList.toggle('d-none', !isInternal);
    counterpartyGroup.classList.toggle('d-none', isInternal);
    if (counterpartyInput) {
      counterpartyInput.required = !isInternal;
    }
  };

  txnTypeSelect.addEventListener('change', syncVisibility);
  syncVisibility();
}

/**
 * Initialize the real-time Loan Underwriting LTV & Monthly Debt Service Calculator.
 *
 * Summary:
 *   Listens to changes on the Loan Origination modal inputs (`principal_usd`,
 *   `collateral_value_usd`, `interest_rate_pct`, `term_months`) and updates the
 *   live LTV ratio, estimated monthly payment, and indicative credit grade badge.
 *
 * Parameters:
 *   None
 *
 * Returns:
 *   void
 */
function initLoanUnderwritingCalculator() {
  const principalInput = document.getElementById('calcPrincipal');
  const collateralInput = document.getElementById('calcCollateral');
  const rateInput = document.getElementById('calcRate');
  const termInput = document.getElementById('calcTerm');

  const ltvOutput = document.getElementById('calcLtvOutput');
  const paymentOutput = document.getElementById('calcPaymentOutput');
  const gradeOutput = document.getElementById('calcGradeOutput');

  if (!principalInput || !collateralInput || !rateInput || !termInput) {
    return;
  }

  const recalculate = () => {
    const principal = parseFloat(principalInput.value) || 0;
    const collateral = parseFloat(collateralInput.value) || 0;
    const annualRate = parseFloat(rateInput.value) || 0;
    const termMonths = parseInt(termInput.value, 10) || 60;

    const ltv = collateral > 0 ? (principal / collateral) * 100 : 0;
    const monthlyRate = annualRate / 100 / 12;
    let monthlyPayment = 0;

    if (principal > 0 && termMonths > 0) {
      if (monthlyRate > 0) {
        monthlyPayment =
          principal * (monthlyRate / (1 - Math.pow(1 + monthlyRate, -termMonths)));
      } else {
        monthlyPayment = principal / termMonths;
      }
    }

    if (ltvOutput) {
      ltvOutput.textContent = `${ltv.toFixed(1)}%`;
    }
    if (paymentOutput) {
      paymentOutput.textContent = `$${monthlyPayment.toLocaleString('en-US', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })}/mo`;
    }
    if (gradeOutput) {
      if (ltv <= 55) {
        gradeOutput.textContent = 'AAA — Prime Collateral';
        gradeOutput.className = 'badge bg-success-subtle text-success border border-success-subtle';
      } else if (ltv <= 68) {
        gradeOutput.textContent = 'AA — Institutional';
        gradeOutput.className = 'badge bg-primary-subtle text-primary border border-primary-subtle';
      } else if (ltv <= 75) {
        gradeOutput.textContent = 'A+ — Core Credit';
        gradeOutput.className = 'badge bg-warning-subtle text-warning-emphasis border border-warning-subtle';
      } else {
        gradeOutput.textContent = 'BBB+ — Enhanced Review';
        gradeOutput.className = 'badge bg-danger-subtle text-danger border border-danger-subtle';
      }
    }
  };

  [principalInput, collateralInput, rateInput, termInput].forEach((el) => {
    el.addEventListener('input', recalculate);
  });
  recalculate();
}

document.addEventListener('DOMContentLoaded', () => {
  initSidebarToggle();
  initDashboardCharts();
  initClientPortfolioChart();
  initLedgerTransactionModal();
  initLoanUnderwritingCalculator();
});
