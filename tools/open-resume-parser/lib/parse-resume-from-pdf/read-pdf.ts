/**
 * Node-adapted PDF reader based on Open Resume read-pdf.ts.
 * Uses pdfjs-dist legacy build + file path / Uint8Array input.
 * Forces hasEOL on the last text item of each page (page-break fix).
 */
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import type { TextItem, TextItems } from "lib/parse-resume-from-pdf/types";

const require = createRequire(import.meta.url);

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type PdfjsModule = any;

function loadPdfjs(): PdfjsModule {
  // Prefer legacy build for Node (CJS). Browser webpack entry is not used here.
  try {
    return require("pdfjs-dist/legacy/build/pdf.js");
  } catch {
    return require("pdfjs-dist");
  }
}

const pdfjs = loadPdfjs();

const workerPath = (() => {
  try {
    return require.resolve("pdfjs-dist/legacy/build/pdf.worker.js");
  } catch {
    try {
      return require.resolve("pdfjs-dist/build/pdf.worker.js");
    } catch {
      return "";
    }
  }
})();

if (workerPath) {
  // Node fake-worker uses require(workerSrc); must be a filesystem path, not file://
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
