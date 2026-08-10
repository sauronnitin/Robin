import type { Line, Lines, TextItem } from "lib/parse-resume-from-pdf/types";

/**
 * List of bullet points
 * Reference: https://stackoverflow.com/questions/56540160/why-isnt-there-a-medium-small-black-circle-in-unicode
 * U+22C5   DOT OPERATOR (⋅)
 * U+2219   BULLET OPERATOR (∙)
 * U+1F784  BLACK SLIGHTLY SMALL CIRCLE (🞄)
 * U+2022   BULLET (•) -------- most common
 * U+2981   Z NOTATION SPOT (⦁)
 * U+26AB   MEDIUM BLACK CIRCLE (⚫︎)
 * U+25CF   BLACK CIRCLE (●)
 * U+2B24   BLACK LARGE CIRCLE (⬤)
 * U+26AC   MEDIUM SMALL WHITE CIRCLE ⚬
 * U+25CB   WHITE CIRCLE ○
 */
export const BULLET_POINTS = [
  "⋅",
  "∙",
  "🞄",
  "•",
  "⦁",
  "⚫︎",
  "●",
  "⬤",
  "⚬",
  "○",
];

/**
 * Convert bullet point lines into a string array aka descriptions.
 *
 * Process line-by-line so the next job's bold header / date line is not swallowed
 * into the previous bullet when subsections fail to split cleanly.
 */
export const getBulletPointsFromLines = (lines: Lines): string[] => {
  const firstBulletPointLineIndex = getFirstBulletPointLineIdx(lines);
  if (firstBulletPointLineIndex === undefined) {
    return lines
      .map((line) => line.map((item) => item.text).join(" ").trim())
      .filter((text) => !!text);
  }

  const descriptions: string[] = [];
  let current = "";

  const flush = () => {
    const t = current.replace(/\s+/g, " ").trim();
    if (t) descriptions.push(t);
    current = "";
  };

  for (let i = firstBulletPointLineIndex; i < lines.length; i++) {
    const line = lines[i];
    const lineText = line
      .map((item) => item.text)
      .join(" ")
      .replace(/\s+/g, " ")
      .trim();
    if (!lineText) continue;

    const startsWithBullet = BULLET_POINTS.some(
      (b) => lineText.startsWith(b) || (line[0]?.text || "").includes(b)
    );

    // Next role header leaked into descriptions: stop (do not append)
    if (!startsWithBullet && current && looksLikeJobHeaderLine(line, lineText)) {
      break;
    }

    if (startsWithBullet) {
      flush();
      current = stripLeadingBullet(lineText);
      continue;
    }

    // Soft-wrap continuation of the current bullet
    if (current) {
      if (!current.endsWith(" ") && !lineText.startsWith(" ")) current += " ";
      current += lineText;
    }
  }
  flush();
  return descriptions;
};

const stripLeadingBullet = (text: string): string => {
  let s = text.trim();
  for (const b of BULLET_POINTS) {
    if (s.startsWith(b)) {
      s = s.slice(b.length).trim();
      break;
    }
  }
  return s;
};

const DATE_LINE =
  /\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}\b|\b(?:19|20)\d{2}\s*[-–—]\s*(?:Present|(?:19|20)\d{2}|\w+\s+\d{4})\b/i;

const looksLikeJobHeaderLine = (line: Line, lineText: string): boolean => {
  // Short bold line without a bullet → likely company / title for the next role
  if (
    line[0] &&
    isBoldFont(line[0].fontName) &&
    lineText.split(/\s+/).length <= 8 &&
    !BULLET_POINTS.some((b) => lineText.includes(b))
  ) {
    return true;
  }
  // Date-range line that is mostly a tenure string
  if (DATE_LINE.test(lineText) && lineText.split(/\s+/).length <= 10) {
    return true;
  }
  return false;
};

const isBoldFont = (fontName: string) =>
  String(fontName || "").toLowerCase().includes("bold");

const getFirstBulletPointLineIdx = (lines: Lines): number | undefined => {
  for (let i = 0; i < lines.length; i++) {
    for (let item of lines[i]) {
      if (BULLET_POINTS.some((bullet) => item.text.includes(bullet))) {
        return i;
      }
    }
  }
  return undefined;
};

// Only consider words that don't contain numbers
const isWord = (str: string) => /^[^0-9]+$/.test(str);
const hasAtLeast8Words = (item: TextItem) =>
  item.text.split(/\s/).filter(isWord).length >= 8;

export const getDescriptionsLineIdx = (lines: Lines): number | undefined => {
  // The main heuristic to determine descriptions is to check if has bullet point
  let idx = getFirstBulletPointLineIdx(lines);

  // Fallback heuristic if the main heuristic doesn't apply (e.g. LinkedIn resume) to
  // check if the line has at least 8 words
  if (idx === undefined) {
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (line.length === 1 && hasAtLeast8Words(line[0])) {
        idx = i;
        break;
      }
    }
  }

  return idx;
};
