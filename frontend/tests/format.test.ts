import { describe, expect, it } from "vitest";
import { describeToolArgs, splitBold, splitCitations, toBlocks } from "@/lib/format";

describe("splitCitations", () => {
  it("extracts single and grouped markers in order", () => {
    expect(splitCitations("Revenue rose [sql1]. Risks [c2, C3] exist.")).toEqual([
      { kind: "text", text: "Revenue rose " },
      { kind: "cite", id: "sql1" },
      { kind: "text", text: ". Risks " },
      { kind: "cite", id: "c2" },
      { kind: "cite", id: "c3" },
      { kind: "text", text: " exist." },
    ]);
  });
  it("leaves other brackets alone", () => {
    expect(splitCitations("see [note 4] and [calculator]")).toEqual([
      { kind: "text", text: "see [note 4] and [calculator]" },
    ]);
  });
});

describe("toBlocks / splitBold", () => {
  it("builds paragraphs and merges consecutive bullets", () => {
    expect(toBlocks("Intro\n\n* a\n- b\nOutro")).toEqual([
      { kind: "p", text: "Intro" },
      { kind: "ul", items: ["a", "b"] },
      { kind: "p", text: "Outro" },
    ]);
  });
  it("finds bold spans without touching lone asterisks", () => {
    expect(splitBold("a **b** c * d")).toEqual([
      { bold: false, text: "a " },
      { bold: true, text: "b" },
      { bold: false, text: " c * d" },
    ]);
  });
});

describe("describeToolArgs", () => {
  it("summarises each tool", () => {
    expect(describeToolArgs("filing_search", { query: "risks", ticker: "AAPL", fiscal_year: 2024, section: "Item 1A" })).toBe(
      "risks (AAPL FY2024 Item 1A)",
    );
    expect(describeToolArgs("calculator", { expression: "1+1" })).toBe("1+1");
    expect(describeToolArgs("unknown", {})).toBe("");
  });
});
