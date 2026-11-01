import { acquireWithPlaywright, submitReceiptToHub } from "./worker.js";

const url = process.env.SOURCE_CANARY_URL ?? "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm";
const receipt = await acquireWithPlaywright(url, {
  sourceId: process.env.SOURCE_CANARY_SOURCE_ID ?? "fed-fomc-calendar-browser-canary",
  artifactDir: process.env.SOURCE_CANARY_DIR ?? "data/source-acquisition-canary",
  modes: ["dom", "network", "screenshot"],
});
console.log(JSON.stringify(receipt));
if (process.env.SOURCE_HUB_URL) {
  const result = await submitReceiptToHub(receipt, process.env.SOURCE_HUB_URL);
  console.log(JSON.stringify({ hub_receipt: result }));
}
if (receipt.status !== "succeeded") process.exitCode = 1;
