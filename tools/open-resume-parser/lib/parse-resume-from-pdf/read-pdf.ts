/**
 * Node-adapted PDF reader based on Open Resume read-pdf.ts.
 * Uses pdfjs-dist legacy build (ESM, pdfjs-dist >=5) + file path / Uint8Array
 * input. Forces hasEOL on the last text item of each page (page-break fix).
 */
import fs from "node:fs";
import path from "node:path";
import type { TextItem, TextItems } from "lib/parse-resume-from-pdf/types";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type PdfjsModule = any;

async function loadPdfjs(): Promise<PdfjsModule> {
  // pdfjs-dist >=5 ships ESM-only builds (.mjs); the old CJS pdf.js/pdf.worker.js
  // legacy files are gone. Legacy build is still the right choice for Node --
  // it's the one built to run outside a browser DOM.
  try {
    return await import("pdfjs-dist/legacy/build/pdf.mjs");
  } catch {
    return await import("pdfjs-dist");
  }
}

// Top-level await: this module only ever runs under tsx/ESM (see parse.mjs),
// which supports it natively.
const pdfjs: PdfjsModule = await loadPdfjs();

const workerPath = (() => {
  try {
    return import.meta.resolve("pdfjs-dist/legacy/build/pdf.worker.mjs");
  } catch {
    try {
      return import.meta.resolve("pdfjs-dist/build/pdf.worker.mjs");
    } catch {
      return "";
    }
  }
})();

if (workerPath) {
  pdfjs.GlobalWorkerOptions.workerSrc = workerPath;
}

function resolvePdfInput(filePathOrUrl: string): { url?: string; data?: Uint8Array } {
  if (/^https?:\/\//i.test(filePathOrUrl) || /^file:/i.test(filePathOrUrl)) {
    return { url: filePathOrUrl };
  }
  const abs = path.resolve(filePathOrUrl);
  if (!fs.existsSync(abs)) {
    throw new Error(`PDF not found: ${abs}`);
  }
  // Prefer bytes so Node does not need fetch for file://
  const data = new Uint8Array(fs.readFileSync(abs));
  return { data };
}

/**
 * Step 1: Read pdf and output textItems by concatenating results from each page.
 *
 * @param filePathOrUrl Absolute/relative filesystem path, or file:// / http(s) URL.
 */
export const readPdf = async (filePathOrUrl: string): Promise<TextItems> => {
  const input = resolvePdfInput(filePathOrUrl);
  const pdfFile = await pdfjs.getDocument({
    ...input,
    useSystemFonts: true,
    isEvalSupported: false,
  }).promise;

  let textItems: TextItems = [];

  for (let i = 1; i <= pdfFile.numPages; i++) {
    const page = await pdfFile.getPage(i);
    const textContent = await page.getTextContent();

    // Wait for font data to be loaded
    await page.getOperatorList();
    const commonObjs = page.commonObjs;

    const pageTextItems = textContent.items
      .filter((item: { str?: string }) => item && typeof (item as { str?: string }).str === "string")
      .map((item: {
        str: string;
        dir?: string;
        transform: number[];
        fontName: string;
        width: number;
        height: number;
        hasEOL?: boolean;
      }) => {
        const {
          str: text,
          // eslint-disable-next-line @typescript-eslint/no-unused-vars
          dir: _dir,
          transform,
          fontName: pdfFontName,
          ...otherProps
        } = item;

        const x = transform[4];
        const y = transform[5];

        let fontName = pdfFontName;
        try {
          const fontObj = commonObjs.get(pdfFontName);
          if (fontObj && fontObj.name) {
            fontName = fontObj.name;
          }
        } catch {
          // keep pdfFontName
        }

        // Soft-hyphen / pdfjs dash noise (Open Resume original intent)
        const newText = text.replace(/\u00AD/g, "");

        return {
          ...otherProps,
          fontName,
          text: newText,
          x,
          y,
          hasEOL: Boolean(item.hasEOL),
        } as TextItem;
      });

    // Page-break hasEOL fix: last item on a page usually lacks hasEOL, so the
    // trailing line of page N merges with the first line of page N+1.
    if (pageTextItems.length > 0) {
      pageTextItems[pageTextItems.length - 1].hasEOL = true;
    }

    textItems.push(...pageTextItems);
  }

  const isEmptySpace = (textItem: TextItem) =>
    !textItem.hasEOL && textItem.text.trim() === "";
  textItems = textItems.filter((textItem) => !isEmptySpace(textItem));

  return textItems;
};
