import { toCsv } from "../lib/csv.ts";
const out = toCsv([{ a: '=HYPERLINK("x")', b: "plain, comma", c: "+1", d: 'q"uote', e: null }]);
console.log(JSON.stringify(out));
if (!out.includes("'=HYPERLINK") || !out.includes('"plain, comma"') || !out.includes("'+1") || !out.includes('"q""uote"')) { console.log("FAIL"); process.exit(1); }
console.log("csv ok");
