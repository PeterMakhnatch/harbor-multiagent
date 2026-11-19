import { readFileSync } from "node:fs";

const view = JSON.parse(readFileSync(0, "utf8"));
let action = 0;
if (process.argv[2] === "high") {
  const legal = view.legal_actions;
  if (!Array.isArray(legal) || legal.length === 0) {
    process.exit(2);
  }
  action = legal[legal.length - 1];
} else {
  const obs = view.observation;
  action = Number.isInteger(obs) && obs >= 0 && obs <= 2 ? obs : 0;
}
process.stdout.write(JSON.stringify({ action }) + "\n");
