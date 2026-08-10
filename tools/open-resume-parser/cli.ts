import path from "node:path";
import { parseResumeFromPdf } from "lib/parse-resume-from-pdf";

async function main(): Promise<void> {
  const pdfArg = process.argv[2];
  if (!pdfArg) {
    process.stdout.write(
      JSON.stringify({
        ok: false,
        error: "Usage: node parse.mjs <path-to-resume.pdf>",
      }) + "\n",
    );
    process.exit(1);
  }

  try {
    const abs = path.resolve(pdfArg);
    const resume = await parseResumeFromPdf(abs);
    process.stdout.write(JSON.stringify({ ok: true, resume }) + "\n");
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    process.stdout.write(JSON.stringify({ ok: false, error: message }) + "\n");
    process.exit(1);
  }
}

main();
