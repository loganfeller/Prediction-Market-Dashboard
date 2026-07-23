const INDICATORS = [
  { key: "cpi", file: "data/cpi.json", title: "CPI, Year-over-Year", unit: "%", tickerLabel: "CPI YoY" },
  { key: "fed_rate", file: "data/fed_rate.json", title: "Fed Funds Rate (Upper Bound)", unit: "%", tickerLabel: "FED FUNDS" },
];

const CHART_COLORS = {
  official: "#4f9da6",
  market: "#c89b3c",
  grid: "#2a3240",
  text: "#8b93a1",
};

function fmtPct(v) {
  return `${v.toFixed(2)}%`;
}

function fmtDate(d) {
  return new Date(d).toLocaleDateString("en-US", { month: "short", year: "numeric" });
}

function expectedValue(marketRows) {
  const totalProb = marketRows.reduce((sum, r) => sum + r.implied_probability, 0) || 1;
  const weighted = marketRows.reduce((sum, r) => sum + r.strike * r.implied_probability, 0);
  return weighted / totalProb;
}

async function loadIndicator(spec) {
  const res = await fetch(spec.file);
  if (!res.ok) throw new Error(`Failed to load ${spec.file}`);
  return res.json();
}

function buildTicker(dataByKey) {
  const inner = document.getElementById("tickerInner");
  const parts = [];
  for (const spec of INDICATORS) {
    const d = dataByKey[spec.key];
    const last = d.official[d.official.length - 1];
    const ev = expectedValue(d.market);
    const dir = ev > last.value ? "tk-up" : ev < last.value ? "tk-down" : "";
    parts.push(
      `<span>${spec.tickerLabel} ${fmtPct(last.value)}</span>` +
      `<span class="${dir}">MKT IMPLIED ${fmtPct(ev)}</span>`
    );
  }
  // duplicate content so the marquee loop has no visible seam
  inner.innerHTML = parts.concat(parts).join("");
}

function renderCard(spec, data, container) {
  const template = document.getElementById("card-template");
  const node = template.content.cloneNode(true);

  const last = data.official[data.official.length - 1];
  const ev = expectedValue(data.market);
  const delta = ev - last.value;

  node.querySelector(".card-title").textContent = spec.title;
  node.querySelector(".official-value").textContent = fmtPct(last.value);
  node.querySelector(".official-date").textContent = fmtDate(last.date);

  node.querySelector(".expected").textContent = fmtPct(ev);
  node.querySelector(".last-official").textContent = fmtPct(last.value);
  const deltaEl = node.querySelector(".delta");
  deltaEl.textContent = `${delta >= 0 ? "+" : ""}${delta.toFixed(2)} pts`;
  deltaEl.style.color = Math.abs(delta) < 0.05 ? "var(--accent-good)" : "var(--accent-warn)";

  container.appendChild(node);

  const officialCanvas = container.querySelector(".card:last-child .official-chart");
  const marketCanvas = container.querySelector(".card:last-child .market-chart");

  new Chart(officialCanvas, {
    type: "line",
    data: {
      labels: data.official.map((r) => fmtDate(r.date)),
      datasets: [{
        data: data.official.map((r) => r.value),
        borderColor: CHART_COLORS.official,
        backgroundColor: "transparent",
        tension: 0.25,
        pointRadius: 2,
      }],
    },
    options: {
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: CHART_COLORS.text, font: { size: 10 } }, grid: { color: CHART_COLORS.grid } },
        y: { ticks: { color: CHART_COLORS.text, font: { size: 10 } }, grid: { color: CHART_COLORS.grid } },
      },
    },
  });

  new Chart(marketCanvas, {
    type: "bar",
    data: {
      labels: data.market.map((r) => r.title),
      datasets: [{
        data: data.market.map((r) => Math.round(r.implied_probability * 100)),
        backgroundColor: CHART_COLORS.market,
        borderRadius: 3,
      }],
    },
    options: {
      indexAxis: "y",
      plugins: { legend: { display: false } },
      scales: {
        x: {
          ticks: { color: CHART_COLORS.text, font: { size: 10 }, callback: (v) => `${v}%` },
          grid: { color: CHART_COLORS.grid },
        },
        y: { ticks: { color: CHART_COLORS.text, font: { size: 10 } }, grid: { display: false } },
      },
    },
  });
}

async function init() {
  const cardsEl = document.getElementById("cards");
  const dataByKey = {};

  let latestUpdate = null;
  for (const spec of INDICATORS) {
    const data = await loadIndicator(spec);
    dataByKey[spec.key] = data;
    if (!latestUpdate || new Date(data.last_updated) > new Date(latestUpdate)) {
      latestUpdate = data.last_updated;
    }
    renderCard(spec, data, cardsEl);
  }

  buildTicker(dataByKey);

  document.getElementById("lastUpdated").textContent =
    `Data last updated ${new Date(latestUpdate).toLocaleString("en-US", {
      dateStyle: "medium",
      timeStyle: "short",
    })} UTC`;
}

init().catch((err) => {
  console.error(err);
  document.getElementById("cards").innerHTML =
    `<p style="color:var(--accent-warn)">Couldn't load indicator data: ${err.message}</p>`;
});
